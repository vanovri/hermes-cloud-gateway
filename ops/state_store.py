"""Version encrypted state independently of code; fail closed on forks or rollback."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import time
import zipfile

from state_bundle import create_bundle

BUNDLE = 'hermes-bundle.enc'
META = 'hermes-bundle.meta.json'
MARKER = '.hermes/state/bundle_generation.json'
REFS = ['refs/heads/state/live', 'refs/heads/state/bak-1', 'refs/heads/state/bak-2']
MAX_BYTES = 99_614_720
ARTIFACT = re.compile(r'^hermes-recovery-bundle-(\d+)-(\d+)-(\d+)$')
LEGACY_ARTIFACT = re.compile(r'^hermes-encrypted-recovery-(\d+)$')
HEX = re.compile(r'^[a-f0-9]{64}$')


def run(args, *, cwd=None, data=None, env=None):
    result = subprocess.run(args, cwd=cwd, input=data, env=env, capture_output=True, timeout=180)
    if result.returncode:
        raise RuntimeError(f'{args[0]} {args[1]} failed, exit {result.returncode}')
    return result.stdout


def git(repo, *args, **kwargs):
    return run(['git', *args], cwd=repo, **kwargs)


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(obj, sort_keys=True) + '\n')
    temp.chmod(0o600)
    temp.replace(path)


def validate_meta(meta):
    if meta.get('format') != 1 or type(meta.get('state_seq')) is not int or meta['state_seq'] < 1:
        raise ValueError('Invalid state sequence')
    for key in ('bundle_sha256', 'parent_sha256', 'lineage_id', 'legacy_base_sha256'):
        if not isinstance(meta.get(key), str) or not HEX.fullmatch(meta[key]):
            raise ValueError('Invalid state hash')
    if not re.fullmatch(r'[0-9]+', str(meta.get('source_run_id', ''))):
        raise ValueError('Invalid state producer')
    if type(meta.get('source_run_attempt')) is not int or meta['source_run_attempt'] < 1:
        raise ValueError('Invalid state producer attempt')
    stamp = datetime.fromisoformat(meta['created_at_utc'])
    if stamp.utcoffset() is None:
        raise ValueError('State timestamp lacks a timezone')
    return meta


def marker(meta):
    return {key: value for key, value in meta.items() if key != 'bundle_sha256'}


def verify_archive(path, meta):
    with tarfile.open(path) as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            raise ValueError('Duplicate archive member')
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not name.parts:
                raise ValueError('Unsafe archive member')
            if name.parts[0] not in {'.hermes', '.9router', '.cua', 'hermes-backup-manifest.json'}:
                raise ValueError('Unexpected archive root')
            if not (member.isfile() or member.isdir() or member.issym()):
                raise ValueError('Unsupported archive member type')
            # Apply the same confinement checks as the eventual extraction, before healing refs.
            tarfile.data_filter(member, '/hermes-restore-root')
        for required in ('.hermes/.env', '.hermes/config.yaml', '.hermes/state.db'):
            entry = archive.getmember(required)
            if not entry.isfile() or entry.size == 0:
                raise ValueError('Required archive member missing')
        if meta['state_seq'] == 0:
            return  # Legacy archives acquire a verified inner generation on their next snapshot.
        validate_meta(meta)
        inner = json.load(archive.extractfile(MARKER))
        if inner != marker(meta):
            raise ValueError('Inner and outer generation disagree')
        manifest = json.load(archive.extractfile('hermes-backup-manifest.json'))
        if set(names) != set(manifest['files']) | {'hermes-backup-manifest.json'}:
            raise ValueError('Archive inventory disagrees with manifest')
        for name, expected in manifest['files'].items():
            entry = archive.getmember(name)
            if 'symlink' in expected:
                if not entry.issym() or entry.linkname != expected['symlink']:
                    raise ValueError('Archive symlink disagrees with manifest')
            else:
                handle = archive.extractfile(entry)
                if not entry.isfile() or entry.size != expected['bytes'] or handle is None:
                    raise ValueError('Archive content disagrees with manifest')
                if hashlib.file_digest(handle, 'sha256').hexdigest() != expected['sha256']:
                    raise ValueError('Archive content hash mismatch')


def decrypt(bundle, meta, target):
    if not 0 < bundle.stat().st_size < MAX_BYTES or digest(bundle) != meta['bundle_sha256']:
        raise ValueError('Encrypted bundle checksum mismatch')
    run(['openssl', 'enc', '-d', '-aes-256-cbc', '-pbkdf2', '-in', str(bundle),
         '-out', str(target), '-pass', 'env:HERMES_BUNDLE_KEY'])
    target.chmod(0o600)
    verify_archive(target, meta)


def remote_refs(repo):
    raw = git(repo, 'ls-remote', '--heads', 'origin', *REFS).decode()
    found = dict(line.split()[::-1] for line in raw.splitlines())
    return {ref: found.get(ref, '') for ref in REFS}


def read_commit(repo, oid, directory):
    git(repo, 'fetch', '--no-tags', '--depth=1', 'origin', oid)
    if any(line.startswith('parent ') for line in git(repo, 'cat-file', '-p', oid).decode().splitlines()):
        raise ValueError('State commit must have no parents')
    if git(repo, 'ls-tree', '--name-only', oid).decode().splitlines() != [BUNDLE, META]:
        raise ValueError('State commit contains unexpected files')
    directory.mkdir(parents=True, exist_ok=True)
    bundle = directory / BUNDLE
    bundle.write_bytes(git(repo, 'show', f'{oid}:{BUNDLE}'))
    meta = validate_meta(json.loads(git(repo, 'show', f'{oid}:{META}')))
    if digest(bundle) != meta['bundle_sha256']:
        raise ValueError('State ref checksum mismatch')
    return bundle, meta


def commit_bundle(repo, bundle, meta):
    validate_meta(meta)
    if digest(bundle) != meta['bundle_sha256']:
        raise ValueError('Ref publication checksum mismatch')
    blob = git(repo, 'hash-object', '-w', str(bundle.resolve())).decode().strip()
    metadata = git(repo, 'hash-object', '-w', '--stdin', data=json.dumps(meta, sort_keys=True).encode()).decode().strip()
    tree = git(repo, 'mktree', data=f'100644 blob {blob}\t{BUNDLE}\n100644 blob {metadata}\t{META}\n'.encode()).decode().strip()
    env = {**os.environ, 'GIT_AUTHOR_NAME': 'hermes-bot', 'GIT_COMMITTER_NAME': 'hermes-bot',
           'GIT_AUTHOR_EMAIL': 'hermes-bot@users.noreply.github.com',
           'GIT_COMMITTER_EMAIL': 'hermes-bot@users.noreply.github.com',
           'GIT_AUTHOR_DATE': meta['created_at_utc'], 'GIT_COMMITTER_DATE': meta['created_at_utc']}
    return git(repo, 'commit-tree', tree, '-m', f"state: seq={meta['state_seq']} run={meta['source_run_id']}", env=env).decode().strip()


def publish(repo, bundle, meta, expected, *, attempts=3):
    oid = commit_bundle(repo, bundle, meta)
    desired = {REFS[0]: oid, REFS[1]: expected[REFS[0]], REFS[2]: expected[REFS[1]]}
    for attempt in range(attempts):
        current = remote_refs(repo)
        if current == desired:
            return desired  # A timed-out push may already have committed its atomic update.
        if current != expected:
            raise RuntimeError('State refs changed; refusing to overwrite another writer')
        args = ['push', '--atomic']
        for ref in REFS:
            args += [f'--force-with-lease={ref}:{expected[ref]}']
        args += ['origin']
        args += [f'{desired[ref]}:{ref}' for ref in REFS if desired[ref] or current[ref]]
        try:
            git(repo, *args)
        except (RuntimeError, subprocess.TimeoutExpired):
            if attempt + 1 < attempts:
                time.sleep(2 * (attempt + 1))
            continue
        if remote_refs(repo) == desired:
            return desired
    raise RuntimeError('State publication failed; encrypted recovery artifact remains authoritative')


def select_candidate(base, candidates):
    selected = base
    for candidate in sorted(candidates, key=lambda item: item[1]['state_seq']):
        meta, previous = candidate[1], selected[1]
        validate_meta(meta)
        if meta['state_seq'] < previous['state_seq']:
            continue
        if meta['lineage_id'] != previous['lineage_id'] or meta['legacy_base_sha256'] != previous['legacy_base_sha256']:
            raise ValueError('Recovery state belongs to a different lineage')
        if meta['state_seq'] == previous['state_seq']:
            if meta != previous:
                raise ValueError('Conflicting state at the same sequence')
            continue
        if meta['state_seq'] != previous['state_seq'] + 1 or meta['parent_sha256'] != previous['bundle_sha256']:
            raise ValueError('Missing or conflicting recovery predecessor')
        selected = candidate
    return selected


class GitHubArtifacts:
    def __init__(self, workflow='hermes.yml'):
        self.repo = os.environ['GITHUB_REPOSITORY']
        self.repo_id = int(os.environ['GITHUB_REPOSITORY_ID'])
        self.workflow_id = self.api(f'repos/{self.repo}/actions/workflows/{workflow}')['id']

    def api(self, path, *, paginate=False):
        for attempt in range(3):
            try:
                return json.loads(run(['gh', 'api', *(['--paginate', '--slurp'] if paginate else []), path]))
            except (RuntimeError, subprocess.TimeoutExpired):
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))

    def legacy_timestamp(self):
        commits = self.api(f'repos/{self.repo}/commits?path={BUNDLE}&per_page=1')
        return datetime.fromisoformat(commits[0]['commit']['committer']['date'].replace('Z', '+00:00')).timestamp()

    def candidates(self, minimum):
        pages = self.api(f'repos/{self.repo}/actions/artifacts?per_page=100', paginate=True)
        for page in pages:
            for artifact in page['artifacts']:
                match = ARTIFACT.fullmatch(artifact['name'])
                legacy = LEGACY_ARTIFACT.fullmatch(artifact['name'])
                if artifact['expired'] or not (match or legacy):
                    continue
                if match and int(match[1]) < minimum:
                    continue
                identity = artifact.get('workflow_run') or {}
                if (identity.get('repository_id') != self.repo_id or identity.get('head_repository_id') != self.repo_id
                        or identity.get('head_branch') != 'main'):
                    continue
                run_id = int(match[2] if match else legacy[1])
                endpoint = f"repos/{self.repo}/actions/runs/{identity['id']}"
                if match:
                    endpoint += f'/attempts/{int(match[3])}'
                producer = self.api(endpoint)
                if (producer['workflow_id'] != self.workflow_id or producer['status'] != 'completed'
                        or producer['event'] not in {'workflow_dispatch', 'schedule'}):
                    continue
                if identity.get('id') != run_id:
                    raise ValueError('Recovery artifact name and producer disagree')
                yield artifact, match

    def download(self, artifact, directory, match):
        if not 0 < artifact['size_in_bytes'] < MAX_BYTES:
            raise ValueError('Recovery artifact exceeds size limit')
        raw = run(['gh', 'api', f"repos/{self.repo}/actions/artifacts/{artifact['id']}/zip"])
        checksum = artifact.get('digest')
        if checksum and checksum != 'sha256:' + hashlib.sha256(raw).hexdigest():
            raise ValueError('Recovery artifact ZIP digest mismatch')
        directory.mkdir(parents=True, exist_ok=True)
        import io
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            expected = {BUNDLE, META} if match else {'hermes-recovery.enc'}
            if set(archive.namelist()) != expected or len(archive.infolist()) != len(expected):
                raise ValueError('Recovery artifact contains unexpected files')
            if sum(item.file_size for item in archive.infolist()) > MAX_BYTES:
                raise ValueError('Recovery artifact expands beyond size limit')
            bundle = directory / BUNDLE
            bundle.write_bytes(archive.read(BUNDLE if match else 'hermes-recovery.enc'))
            if not match:
                return bundle, None
            meta = validate_meta(json.loads(archive.read(META)))
            if (meta['state_seq'] != int(match[1]) or str(meta['source_run_id']) != match[2]
                    or meta['source_run_attempt'] != int(match[3])):
                raise ValueError('Recovery artifact generation disagrees with its name')
            if digest(bundle) != meta['bundle_sha256']:
                raise ValueError('Recovery artifact ciphertext checksum mismatch')
            return bundle, meta


def reconcile(repo, work, *, artifacts=None, heal=True):
    work.mkdir(parents=True, exist_ok=True)
    refs = remote_refs(repo)  # Network errors must never masquerade as an absent state branch.
    if not refs[REFS[0]] and any(refs.values()):
        raise ValueError('Live state ref is missing while backup refs exist')
    legacy_bundle = repo / BUNDLE
    legacy_hash = digest(legacy_bundle)
    legacy = {'state_seq': 0, 'bundle_sha256': legacy_hash, 'lineage_id': legacy_hash,
              'legacy_base_sha256': legacy_hash}
    base = read_commit(repo, refs[REFS[0]], work / 'git') if refs[REFS[0]] else (legacy_bundle, legacy)
    if base[1]['legacy_base_sha256'] != legacy_hash:
        raise ValueError('Legacy main bundle changed after migration; manual reconciliation required')
    service = artifacts or GitHubArtifacts()
    candidates, legacy_candidates = [], []
    for artifact, match in service.candidates(base[1]['state_seq']):
        if match:
            candidates.append(service.download(artifact, work / str(artifact['id']), match))
        elif datetime.fromisoformat(artifact['created_at'].replace('Z', '+00:00')).timestamp() > service.legacy_timestamp():
            if base[1]['state_seq']:
                raise ValueError('New legacy recovery appeared after state migration')
            legacy_candidates.append((artifact, match))
    if legacy_candidates:
        artifact, match = max(legacy_candidates, key=lambda item: item[0]['created_at'])
        bundle, _ = service.download(artifact, work / 'legacy-recovery', match)
        recovered_hash = digest(bundle)
        base = (bundle, {**legacy, 'bundle_sha256': recovered_hash, 'lineage_id': recovered_hash})
    selected = select_candidate(base, candidates)
    plain = work / 'selected.tar.gz'
    decrypt(*selected, plain)
    if selected != base and heal:
        refs = publish(repo, *selected, refs)
    elif selected != base and not heal:
        pass  # Read-only restore drills validate recovery without changing production refs.
    context = {'base': selected[1], 'refs': refs, 'recovered': selected != base or bool(legacy_candidates),
               'archive': str(plain.resolve())}
    save(work / 'context.json', context)
    return context


def seal(home, context, directory):
    directory.mkdir(parents=True, exist_ok=True)
    previous = context['base']
    inner = {'format': 1, 'state_seq': previous['state_seq'] + 1,
             'parent_sha256': previous['bundle_sha256'], 'lineage_id': previous['lineage_id'],
             'legacy_base_sha256': previous['legacy_base_sha256'],
             'source_run_id': os.environ['GITHUB_RUN_ID'], 'source_run_attempt': int(os.environ['GITHUB_RUN_ATTEMPT']),
             'created_at_utc': datetime.now(timezone.utc).isoformat()}
    save(home / MARKER, inner)
    plain = directory / 'state.tar.gz'
    snapshot = create_bundle(home, plain)
    save(directory.parent / 'snapshot-proof.json', snapshot)
    bundle = directory / BUNDLE
    run(['openssl', 'enc', '-aes-256-cbc', '-pbkdf2', '-salt', '-in', str(plain),
         '-out', str(bundle), '-pass', 'env:HERMES_BUNDLE_KEY'])
    bundle.chmod(0o600)
    meta = {**inner, 'bundle_sha256': digest(bundle)}
    try:
        decrypt(bundle, meta, directory / 'verify.tar.gz')
        save(directory / META, meta)
    finally:
        plain.unlink(missing_ok=True)
        (directory / 'verify.tar.gz').unlink(missing_ok=True)
    return meta


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['restore', 'seal', 'publish'])
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--home', type=Path)
    parser.add_argument('--read-only', action='store_true')
    args = parser.parse_args()
    if args.action == 'restore':
        context = reconcile(args.repo, args.work, heal=not args.read_only)
        if args.home:
            args.home.mkdir(parents=True, exist_ok=True)
            try:
                with tarfile.open(context['archive']) as archive:
                    archive.extractall(args.home, filter='data')
            finally:
                Path(context['archive']).unlink(missing_ok=True)
        print(json.dumps({'state_seq': context['base']['state_seq'], 'recovered': context['recovered']}))
    elif args.action == 'seal':
        context = json.loads((args.work / 'context.json').read_text())
        meta = seal(args.home, context, args.work / 'publish')
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write(f"state_seq={meta['state_seq']}\n")
        print(json.dumps({'sealed_state_seq': meta['state_seq'],
                          **json.loads((args.work / 'snapshot-proof.json').read_text())}))
    else:
        context = json.loads((args.work / 'context.json').read_text())
        folder = args.work / 'publish'
        meta = validate_meta(json.loads((folder / META).read_text()))
        if meta['state_seq'] != context['base']['state_seq'] + 1 or meta['parent_sha256'] != context['base']['bundle_sha256']:
            raise ValueError('Publication does not extend the restored generation')
        publish(args.repo, folder / BUNDLE, meta, context['refs'])
        print(json.dumps({'published_state_seq': meta['state_seq']}))


if __name__ == '__main__':
    main()

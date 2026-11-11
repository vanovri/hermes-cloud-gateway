import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import io

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
import state_store as store
from state_bundle import create_bundle


class FakeArtifacts:
    def __init__(self, pairs=()):
        self.pairs = list(pairs)

    def candidates(self, minimum):
        for index, (_, meta) in enumerate(self.pairs):
            yield {'id': index}, True

    def download(self, artifact, directory, match):
        return self.pairs[artifact['id']]


class StateStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'code'
        self.repo.mkdir()
        self.remote = self.root / 'remote.git'
        store.run(['git', 'init', '--bare', str(self.remote)])
        store.git(self.repo, 'init', '-b', 'main')
        store.git(self.repo, 'config', 'user.name', 'synthetic-test')
        store.git(self.repo, 'config', 'user.email', 'test@example.invalid')
        store.git(self.repo, 'remote', 'add', 'origin', str(self.remote))
        self.env = patch.dict(os.environ, {'HERMES_BUNDLE_KEY': 'synthetic-test-passphrase',
                                         'GITHUB_RUN_ID': '100', 'GITHUB_RUN_ATTEMPT': '1'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.home = self.root / 'home'
        hermes = self.home / '.hermes'
        (hermes / 'cron').mkdir(parents=True)
        (hermes / '.env').write_text('SYNTHETIC=true\n')
        (hermes / 'config.yaml').write_text('gateway: {}\n')
        (hermes / 'cron/jobs.json').write_text('{"jobs": []}')
        db = sqlite3.connect(hermes / 'state.db')
        db.execute('CREATE TABLE example (content TEXT)')
        db.execute("INSERT INTO example VALUES ('preserved')")
        db.commit()
        db.close()
        plain = self.root / 'legacy.tar.gz'
        create_bundle(self.home, plain)
        store.run(['openssl', 'enc', '-aes-256-cbc', '-pbkdf2', '-salt', '-in', str(plain),
                   '-out', str(self.repo / store.BUNDLE), '-pass', 'env:HERMES_BUNDLE_KEY'])
        store.git(self.repo, 'add', store.BUNDLE)
        store.git(self.repo, 'commit', '-m', 'synthetic legacy state')
        store.git(self.repo, 'push', 'origin', 'main')
        self.initial_main = store.git(self.repo, 'rev-parse', 'HEAD')

    def boot(self, name='boot', artifacts=None, heal=True):
        return store.reconcile(self.repo, self.root / name, artifacts=artifacts or FakeArtifacts(), heal=heal)

    def sealed(self, context, name='sealed'):
        folder = self.root / name
        meta = store.seal(self.home, context, folder)
        return folder / store.BUNDLE, meta

    def test_legacy_migration_marker_roundtrip_and_no_plaintext_commit(self):
        context = self.boot()
        self.assertEqual(context['base']['state_seq'], 0)
        bundle, meta = self.sealed(context)
        refs = store.publish(self.repo, bundle, meta, context['refs'])
        oid = refs[store.REFS[0]]
        self.assertEqual(store.git(self.repo, 'ls-tree', '--name-only', oid).decode().splitlines(), [store.BUNDLE, store.META])
        self.assertNotIn(b'\nparent ', store.git(self.repo, 'cat-file', '-p', oid))
        booted = self.boot('second')
        self.assertEqual(booted['base'], meta)
        with tarfile.open(booted['archive']) as archive:
            self.assertEqual(json.load(archive.extractfile(store.MARKER)), store.marker(meta))
        self.assertEqual(store.git(self.repo, 'rev-parse', 'HEAD'), self.initial_main)

    def test_push_rejected_then_recovery_heals_and_preserves_latest_database(self):
        context = self.boot()
        bundle, meta = self.sealed(context)
        hook = self.remote / 'hooks/pre-receive'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o700)
        with self.assertRaises(RuntimeError):
            store.publish(self.repo, bundle, meta, context['refs'], attempts=1)
        self.assertFalse(any(store.remote_refs(self.repo).values()))
        hook.unlink()
        restored = self.boot('recovered', FakeArtifacts([(bundle, meta)]))
        self.assertTrue(restored['recovered'])
        self.assertEqual(restored['base']['state_seq'], 1)
        self.assertTrue(store.remote_refs(self.repo)[store.REFS[0]])
        target = self.root / 'restored'
        with tarfile.open(restored['archive']) as archive:
            archive.extractall(target, filter='data')
        with sqlite3.connect(target / '.hermes/state.db') as db:
            self.assertEqual(db.execute('SELECT content FROM example').fetchone()[0], 'preserved')

    def test_rotation_is_atomic_parentless_and_retry_is_idempotent(self):
        contexts, commits = [], []
        for index in range(4):
            context = self.boot(f'boot-{index}')
            bundle, meta = self.sealed(context, f'sealed-{index}')
            refs = store.publish(self.repo, bundle, meta, context['refs'])
            self.assertEqual(store.publish(self.repo, bundle, meta, context['refs']), refs)
            commits.append(refs[store.REFS[0]])
            contexts.append(context)
        self.assertEqual(list(store.remote_refs(self.repo).values()), list(reversed(commits[-3:])))
        for oid in store.remote_refs(self.repo).values():
            self.assertEqual(store.git(self.repo, 'rev-list', '--count', oid).strip(), b'1')
        context = self.boot('rejected-backup')
        bundle, meta = self.sealed(context, 'rejected-seal')
        before = store.remote_refs(self.repo)
        hook = self.remote / 'hooks/update'
        hook.write_text('#!/bin/sh\n[ "$1" != "refs/heads/state/bak-1" ]\n')
        hook.chmod(0o700)
        with self.assertRaises(RuntimeError):
            store.publish(self.repo, bundle, meta, before, attempts=1)
        self.assertEqual(store.remote_refs(self.repo), before)

    def test_concurrent_writer_cannot_overwrite_live_or_backup_refs(self):
        context = self.boot()
        first, meta = self.sealed(context)
        committed = store.publish(self.repo, first, meta, context['refs'])
        other = {**meta, 'source_run_id': '101'}
        with self.assertRaises(RuntimeError):
            store.publish(self.repo, first, other, context['refs'], attempts=1)
        self.assertEqual(store.remote_refs(self.repo), committed)

    def test_push_timeout_after_server_acceptance_does_not_rotate_twice(self):
        context = self.boot()
        bundle, meta = self.sealed(context)
        original = store.git
        pushes = []
        def lost_ack(repo, *args, **kwargs):
            result = original(repo, *args, **kwargs)
            if args[0] == 'push':
                pushes.append(args)
                raise subprocess.TimeoutExpired('synthetic lost acknowledgement', 1)
            return result
        with patch.object(store, 'git', side_effect=lost_ack), patch.object(store.time, 'sleep'):
            result = store.publish(self.repo, bundle, meta, context['refs'])
        self.assertEqual(len(pushes), 1)
        self.assertEqual(store.remote_refs(self.repo), result)

    def test_corrupt_newer_recovery_never_falls_back_and_marker_is_checked(self):
        context = self.boot()
        bundle, meta = self.sealed(context)
        with self.assertRaises(ValueError):
            self.boot('corrupt', FakeArtifacts([(bundle, {**meta, 'bundle_sha256': '0' * 64})]))
        self.assertFalse(any(store.remote_refs(self.repo).values()))
        # A valid ciphertext hash alone cannot authenticate forged outer generation metadata.
        with self.assertRaises(ValueError):
            store.decrypt(bundle, {**meta, 'source_run_id': '999'}, self.root / 'mismatch.tar.gz')

    def test_same_sequence_fork_gap_and_lineage_changes_are_rejected(self):
        context = self.boot()
        bundle, meta = self.sealed(context)
        base = (bundle, meta)
        for changed in ({**meta, 'bundle_sha256': '1' * 64},
                        {**meta, 'state_seq': 3},
                        {**meta, 'lineage_id': '2' * 64}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                store.select_candidate(base, [(bundle, changed)])
        for invalid in (-1, 1.5, True, '2'):
            with self.assertRaises(ValueError):
                store.validate_meta({**meta, 'state_seq': invalid})

    def test_api_failure_and_missing_live_never_fall_back_to_legacy(self):
        with patch.object(store, 'remote_refs', side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError):
                self.boot()
        with patch.object(store, 'remote_refs', return_value={store.REFS[0]: '', store.REFS[1]: 'abc', store.REFS[2]: ''}):
            with self.assertRaises(ValueError):
                self.boot()

    def test_archive_traversal_and_legacy_writer_after_migration_are_rejected(self):
        bad = self.root / 'unsafe.tar.gz'
        with tarfile.open(bad, 'w:gz') as archive:
            archive.addfile(tarfile.TarInfo('../escape'))
        with self.assertRaises(ValueError):
            store.verify_archive(bad, {'state_seq': 0})
        context = self.boot()
        bundle, meta = self.sealed(context)
        store.publish(self.repo, bundle, meta, context['refs'])
        (self.repo / store.BUNDLE).write_bytes(b'legacy writer changed main')
        with self.assertRaises(ValueError):
            self.boot('old-writer')

    def test_artifact_zip_cannot_write_paths_and_all_generations_are_considered(self):
        client = object.__new__(store.GitHubArtifacts)
        client.repo = 'synthetic/repo'
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, 'w') as archive:
            archive.writestr('../escape', 'bad')
        with patch.object(store, 'run', return_value=raw.getvalue()):
            with self.assertRaises(ValueError):
                client.download({'id': 1, 'size_in_bytes': len(raw.getvalue())}, self.root / 'zip', True)
        context = self.boot()
        one, meta_one = self.sealed(context, 'one')
        two, meta_two = self.sealed({'base': meta_one}, 'two')
        selected = store.select_candidate((self.repo / store.BUNDLE, context['base']), [(two, meta_two), (one, meta_one)])
        self.assertEqual(selected[1]['state_seq'], 2)

    def test_artifact_catalog_paginates_and_ignores_untrusted_workflows(self):
        client = object.__new__(store.GitHubArtifacts)
        client.repo, client.repo_id, client.workflow_id = 'synthetic/repo', 1, 7
        def entry(seq, producer, repository=1):
            return {'name': f'hermes-recovery-bundle-{seq}-{producer}-1', 'expired': False,
                    'workflow_run': {'id': producer, 'repository_id': repository,
                                     'head_repository_id': repository, 'head_branch': 'main'}}
        pages = [{'artifacts': [entry(1, 100), entry(99, 999, 2)]},
                 {'artifacts': [entry(3, 300), entry(2, 200)]}]
        calls = []
        def api(path, **kwargs):
            calls.append((path, kwargs))
            if 'artifacts?' in path:
                return pages
            producer = int(path.split('/runs/')[1].split('/')[0])
            return {'workflow_id': 8 if producer == 300 else 7, 'status': 'completed', 'event': 'workflow_dispatch'}
        client.api = api
        candidates = list(client.candidates(2))
        self.assertEqual([match[1] for _, match in candidates], ['2'])
        self.assertTrue(calls[0][1]['paginate'])
        self.assertFalse(any('/999/' in path for path, _ in calls))


if __name__ == '__main__':
    unittest.main()

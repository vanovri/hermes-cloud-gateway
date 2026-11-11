"""Exercise the real Actions artifact API using synthetic, non-production state."""
import argparse
import json
import os
from pathlib import Path
import sqlite3

from state_store import GitHubArtifacts, decrypt, seal


def create(folder):
    home = folder / 'user'
    hermes = home / '.hermes'
    (hermes / 'cron').mkdir(parents=True)
    (hermes / '.env').write_text('SYNTHETIC=true\n')
    (hermes / 'config.yaml').write_text('gateway: {}\n')
    (hermes / 'cron/jobs.json').write_text('{"jobs": []}')
    db = sqlite3.connect(hermes / 'state.db')
    try:
        db.execute('CREATE TABLE synthetic (content TEXT)')
        db.execute("INSERT INTO synthetic VALUES ('artifact roundtrip')")
        db.commit()
    finally:
        db.close()
    base = {'state_seq': 0, 'bundle_sha256': '0' * 64, 'lineage_id': '0' * 64, 'legacy_base_sha256': '0' * 64}
    seal(home, {'base': base}, folder / 'publish')


def verify(folder):
    client = GitHubArtifacts('verify.yml', events=frozenset({'push', 'workflow_dispatch'}))
    candidates = [(artifact, match) for artifact, match in client.candidates(1)
                  if artifact['workflow_run']['head_sha'] == os.environ['GITHUB_SHA']]
    if not candidates:
        raise RuntimeError('Synthetic recovery artifact for this code commit is missing')
    artifact, match = max(candidates, key=lambda item: item[0]['id'])
    bundle, meta = client.download(artifact, folder / 'download', match)
    if meta['lineage_id'] != '0' * 64 or meta['state_seq'] != 1:
        raise ValueError('Unexpected synthetic recovery generation')
    plain = folder / 'verified.tar.gz'
    decrypt(bundle, meta, plain)
    plain.unlink()
    print(json.dumps({'artifact_api_roundtrip': 'passed', 'artifact_id': artifact['id'],
                      'inner_generation_verified': True, 'production_refs_changed': False,
                      'gateway_started': False, 'model_called': False, 'message_sent': False}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['create', 'verify'])
    parser.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    args.folder.mkdir(parents=True, exist_ok=True)
    (create if args.action == 'create' else verify)(args.folder)

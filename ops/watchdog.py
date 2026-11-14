"""Restart a completed gateway independently of its persistence or exit outcome."""
import json
import os
import subprocess

ACTIVE = {'queued', 'in_progress', 'waiting', 'pending', 'requested'}


def ensure_running(repo):
    result = subprocess.run(['gh', 'api', f'repos/{repo}/actions/workflows/hermes.yml/runs?per_page=100'],
                            capture_output=True, text=True, check=True, timeout=45)
    active = [run for run in json.loads(result.stdout)['workflow_runs']
              if run['status'] in ACTIVE and run['head_branch'] == 'main']
    if active:
        return {'action': 'already_active', 'run_ids': [run['id'] for run in active]}
    subprocess.run(['gh', 'workflow', 'run', 'hermes.yml', '--repo', repo, '--ref', 'main'],
                   capture_output=True, text=True, check=True, timeout=45)
    return {'action': 'dispatched'}


if __name__ == '__main__':
    print(json.dumps(ensure_running(os.environ['GITHUB_REPOSITORY'])))

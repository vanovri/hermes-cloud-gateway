"""Opt-in recovery probe: check live readiness, SQLite, one model answer and owner delivery."""
import json
import os
from pathlib import Path
import sys
import subprocess
import time
import urllib.request

from live_control import readiness
from io_utils import readonly_database, response_json, run_process

MARKER = 'HERMES_RECOVERY_OK'


def probe(home):
    for _ in range(60):
        if readiness(home)['ready']:
            break
        time.sleep(2)
    else:
        raise RuntimeError('Live gateway did not become ready')
    counts = {}
    with readonly_database(home / "state.db") as db:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('Restored SQLite integrity check failed')
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('sessions', 'messages', 'delivery_obligations'):
            if table in tables:
                counts[table] = db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    result = run_process(['hermes', 'chat', '-q',
        f'Recovery connectivity test. Reply with exactly {MARKER}. Do not use tools or change any files.',
        '--quiet', '--max-turns', '1', '--run-budget', '90', '--toolsets', 'no_mcp'],
        text=True, timeout=150, check=True)
    if MARKER not in result.stdout:
        raise RuntimeError('Model did not return the recovery marker')
    env = {}
    for line in (home / '.env').read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            env[key.strip()] = value.strip().strip("\"'")
    token, target = env.get('TELEGRAM_BOT_TOKEN'), env.get('TELEGRAM_HOME_CHANNEL')
    if not token or not target:
        raise RuntimeError('Owner Telegram delivery configuration is missing')
    payload = json.dumps({'chat_id': target, 'text':
        'Hermes aktif lagi. State terbaru sudah dipulihkan dan database lolos pemeriksaan integritas. '
        'Uji model berhasil. Pesan ini menguji pengiriman Telegram.'}).encode()
    request = urllib.request.Request('https://api.telegram.org/bot' + token + '/sendMessage',
                                     data=payload, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        receipt = response_json(response, timeout=20)
    if receipt.get('ok') is not True or not receipt.get('result', {}).get('message_id'):
        raise RuntimeError('Telegram did not acknowledge the test message')
    return {'ready': True, 'sqlite_integrity': 'ok', 'restored_counts': counts,
            'model_inference_tested': True, 'model_marker_verified': True,
            'telegram_delivery_acknowledged': True, 'message_sent': True}


if __name__ == '__main__':
    try:
        evidence = probe(Path.home() / '.hermes')
        conclusion = 'success'
    except Exception as exc:
        evidence = {'functional_smoke': 'failed', 'error_type': type(exc).__name__}
        conclusion = 'failure'
    print(json.dumps(evidence), flush=True)
    payload = {'name': 'Hermes recovery functional smoke', 'head_sha': os.environ['GITHUB_SHA'],
               'status': 'completed', 'conclusion': conclusion, 'external_id': os.environ['GITHUB_RUN_ID'],
               'output': {'title': 'Recovery model and Telegram test: ' + conclusion,
                          'summary': json.dumps(evidence, sort_keys=True)}}
    subprocess.run(['gh', 'api', '--method', 'POST',
                    'repos/' + os.environ['GITHUB_REPOSITORY'] + '/check-runs', '--input', '-'],
                   input=json.dumps(payload), text=True, capture_output=True, check=True, timeout=45)
    sys.exit(0 if conclusion == 'success' else 1)

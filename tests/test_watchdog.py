import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
from watchdog import ensure_running


class WatchdogTests(unittest.TestCase):
    def test_failure_and_success_both_restart_when_no_cycle_is_active(self):
        for conclusion in ('success', 'failure', 'cancelled', 'timed_out'):
            with self.subTest(conclusion=conclusion), patch('watchdog.subprocess.run') as call:
                call.return_value.stdout = json.dumps({'workflow_runs': [
                    {'id': 1, 'status': 'completed', 'conclusion': conclusion, 'head_branch': 'main'}]})
                self.assertEqual(ensure_running('example/repo')['action'], 'dispatched')
                self.assertEqual(call.call_count, 2)

    def test_active_or_queued_gateway_never_gets_a_duplicate(self):
        for status in ('queued', 'in_progress', 'waiting', 'pending', 'requested'):
            with self.subTest(status=status), patch('watchdog.subprocess.run') as call:
                call.return_value.stdout = json.dumps({'workflow_runs': [
                    {'id': 2, 'status': status, 'head_branch': 'main'}]})
                self.assertEqual(ensure_running('example/repo'), {'action': 'already_active', 'run_ids': [2]})
                self.assertEqual(call.call_count, 1)

    def test_api_failure_never_masquerades_as_no_active_gateway(self):
        with patch('watchdog.subprocess.run', side_effect=subprocess.CalledProcessError(1, ['gh'])) as call:
            with self.assertRaises(subprocess.CalledProcessError):
                ensure_running('example/repo')
            self.assertEqual(call.call_count, 1)

import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
from functional_smoke import probe


class FunctionalSmokeTests(unittest.TestCase):
    def test_model_and_telegram_need_real_acknowledgments(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / '.env').write_text('TELEGRAM_BOT_TOKEN=synthetic\nTELEGRAM_HOME_CHANNEL=123\n')
            with sqlite3.connect(home / 'state.db') as db:
                db.execute('CREATE TABLE messages (text TEXT)')
                db.execute("INSERT INTO messages VALUES ('preserved')")
            with patch('functional_smoke.readiness', return_value={'ready': True}), \
                 patch('functional_smoke.run_process') as model, \
                 patch('functional_smoke.urllib.request.urlopen') as telegram:
                model.return_value.stdout = 'HERMES_RECOVERY_OK'
                telegram.return_value.__enter__.return_value = io.BytesIO(json.dumps(
                    {'ok': True, 'result': {'message_id': 1}}).encode())
                evidence = probe(home)
                self.assertTrue(evidence['model_marker_verified'])
                self.assertTrue(evidence['telegram_delivery_acknowledged'])
                self.assertEqual(evidence['restored_counts']['messages'], 1)
                model.return_value.stdout = 'Provider error'
                telegram.reset_mock()
                with self.assertRaisesRegex(RuntimeError, 'Model did not return'):
                    probe(home)
                telegram.assert_not_called()

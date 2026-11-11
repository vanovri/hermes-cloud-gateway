import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
from live_control import query, readiness
from publish_readiness import publish


class LiveControlTests(unittest.TestCase):
    def test_private_socket_uses_live_response(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            path = home / "gateway.sock"
            server = socket.socket(socket.AF_UNIX)
            server.bind(str(path))
            path.chmod(0o600)
            server.listen(1)
            received = []
            def answer():
                connection, _ = server.accept()
                with connection:
                    received.append(json.loads(connection.recv(4096)))
                    connection.sendall(b'{"ok":true,"result":{"state":"ready","runtime_protocol":1}}\n')
            thread = threading.Thread(target=answer)
            thread.start()
            self.assertEqual(query(home, "identify")["state"], "ready")
            thread.join(timeout=3)
            server.close()
            self.assertEqual(received[0]["verb"], "identify")
            self.assertEqual(received[0]["protocol"], 1)

    def test_connected_adapter_does_not_make_a_starting_runtime_ready(self):
        with patch("live_control.query", side_effect=[{"state":"starting","runtime_protocol":1}, {"platforms":{"telegram":{"state":"connected"}}}]):
            self.assertFalse(readiness(Path("/synthetic"))["ready"])

    def test_receipt_is_created_once_then_updated_on_degradation(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"GITHUB_RUN_ID":"synthetic-run","GITHUB_REPOSITORY":"example/repo"}):
            ready = {"ready":True,"control_state":"ready","telegram_adapter_state":"connected"}
            with patch("publish_readiness.readiness", return_value=ready.copy()), patch("publish_readiness.subprocess.run") as request:
                request.return_value.stdout = '{"id": 123}'
                publish(Path(td), "a" * 40)
                publish(Path(td), "a" * 40)
                self.assertEqual(request.call_count, 1)
                self.assertIn("POST", request.call_args.args[0])
            degraded = {**ready,"ready":False,"telegram_adapter_state":"retrying"}
            with patch("publish_readiness.readiness", return_value=degraded), patch("publish_readiness.subprocess.run") as request:
                request.return_value.stdout = '{"id": 123}'
                publish(Path(td), "a" * 40)
                self.assertIn("PATCH", request.call_args.args[0])
                self.assertEqual(json.loads(request.call_args.kwargs["input"])["conclusion"], "failure")


if __name__ == "__main__":
    unittest.main()

import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
from io_utils import atomic_json, readonly_database, response_json, retry_delay, run_process
from live_control import query
from publish_readiness import publish
from state_bundle import sqlite_backup


class IOResilienceTests(unittest.TestCase):
    def test_busy_backup_aborts_and_removes_partial_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            source, target = Path(td) / "source.db", Path(td) / "snapshot.db"
            writer = sqlite3.connect(source)
            self.addCleanup(writer.close)
            writer.execute("CREATE TABLE state (value TEXT)")
            writer.commit()
            writer.execute("BEGIN EXCLUSIVE")
            start = time.monotonic()
            try:
                with self.assertRaises(TimeoutError):
                    sqlite_backup(source, target, timeout=0.05)
                self.assertLess(time.monotonic() - start, 1)
                self.assertFalse(target.exists())
            finally:
                writer.rollback()
                writer.close()

    def test_query_budget_interrupts_long_sql_and_closes_connection(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.db"
            sqlite3.connect(path).close()
            with self.assertRaises(sqlite3.OperationalError), readonly_database(path, timeout=0.01) as db:
                db.execute("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<100000000) SELECT sum(x) FROM n").fetchone()
            with self.assertRaises(sqlite3.ProgrammingError):
                db.execute("SELECT 1")

    def test_failed_atomic_replace_preserves_previous_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "receipt.json"
            atomic_json(path, {"generation": 1})
            with patch("io_utils.os.replace", side_effect=OSError("synthetic disk failure")):
                with self.assertRaises(OSError):
                    atomic_json(path, {"generation": 2})
            self.assertEqual(json.loads(path.read_text()), {"generation": 1})
            self.assertEqual(list(Path(td).glob("*.tmp")), [])
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_corrupt_receipt_can_be_replaced_and_api_call_has_deadline(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"GITHUB_RUN_ID": "1", "GITHUB_REPOSITORY": "synthetic/repo"}):
            path = Path(td) / "state/runtime_readiness.json"
            path.parent.mkdir()
            path.write_text("{")
            with patch("publish_readiness.readiness", return_value={"ready": True}), patch("publish_readiness.probe", return_value={"ok": True}), patch("publish_readiness.subprocess.run") as api:
                api.return_value.stdout = '{"id": 1}'
                publish(Path(td), "a" * 40)
                self.assertEqual(api.call_args.kwargs["timeout"], 30)
                self.assertEqual(json.loads(path.read_text())["check_run_id"], 1)

    def test_health_command_returns_nonzero_without_live_control(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / ".env").write_text("")
            sqlite3.connect(home / "state.db").close()
            script = Path(__file__).resolve().parents[1] / "ops/health_report.py"
            result = subprocess.run([sys.executable, str(script), "--home", str(home)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(json.loads(result.stdout)["live_control"]["ready"])

    def test_http_response_size_and_absolute_read_budget(self):
        with self.assertRaises(ValueError):
            response_json(io.BytesIO(b"x" * 20), max_bytes=10)
        with patch("io_utils.time.monotonic", side_effect=[0, 0, 2]):
            with self.assertRaises(TimeoutError):
                response_json(io.BytesIO(b'{}'), timeout=1)
        self.assertEqual(response_json(io.BytesIO(b'{"ok":true}')), {"ok": True})

    def test_jitter_is_exponential_and_capped(self):
        with patch("io_utils.random.uniform", side_effect=lambda low, high: high):
            self.assertEqual([retry_delay(n) for n in (0, 1, 3, 10)], [1, 2, 8, 15])

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_timeout_kills_child_process_group(self):
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "orphan"
            child = "import time,pathlib; time.sleep(0.4); pathlib.Path(" + repr(str(marker)) + ").touch()"
            parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); time.sleep(30)"
            with self.assertRaises(subprocess.TimeoutExpired):
                run_process([sys.executable, "-c", parent], timeout=0.1)
            time.sleep(0.5)
            self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == "posix", "Private Unix socket ownership")
    def test_slow_control_stream_cannot_renew_deadline(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            server = socket.socket(socket.AF_UNIX)
            server.bind(str(home / "gateway.sock"))
            (home / "gateway.sock").chmod(0o600)
            server.listen(1)
            def trickle():
                connection, _ = server.accept()
                with connection:
                    connection.recv(4096)
                    try:
                        for _ in range(20):
                            connection.sendall(b" ")
                            time.sleep(0.02)
                    except BrokenPipeError:
                        pass
            thread = threading.Thread(target=trickle, daemon=True)
            thread.start()
            start = time.monotonic()
            try:
                self.assertEqual(query(home, "identify", timeout=0.05), {})
                self.assertLess(time.monotonic() - start, 0.2)
            finally:
                thread.join(timeout=1)
                server.close()


if __name__ == "__main__":
    unittest.main()

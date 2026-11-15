import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import json
import logging
import os
from logging.handlers import QueueListener
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
import runtime_guard
from runtime_guard import BoundedLogQueue, install_exception_guard, monitor_required_workers, request_fatal_shutdown
from runtime_probe import probe


class RuntimeGuardTests(unittest.TestCase):
    def test_watchdog_diagnostics_rotate_and_keep_only_five_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "watchdog.log"
            for index in range(8):
                target.write_text(str(index) * 20)
                runtime_guard.rotate_diagnostic_log(target, max_bytes=10)
            self.assertFalse(target.exists())
            self.assertEqual(target.with_name("watchdog.log.1").read_text(), "7" * 20)
            self.assertEqual(target.with_name("watchdog.log.5").read_text(), "3" * 20)
            self.assertEqual(len(list(Path(directory).iterdir())), 5)

    def test_log_backpressure_is_bounded_and_retains_shutdown_sentinel(self):
        records = BoundedLogQueue(2)
        records.put("oldest")
        records.put("newer")
        records.put("newest")
        self.assertEqual(records.dropped_records, 1)
        records.put(None)
        records.put("late diagnostic")
        self.assertEqual(records.qsize(), 2)
        self.assertEqual(records.get_nowait(), "newest")
        records.task_done()
        self.assertIsNone(records.get_nowait())
        records.task_done()
        self.assertEqual(records.unfinished_tasks, 0)
        self.assertEqual(records.dropped_records, 3)

    def test_queue_listener_stops_after_full_queue_during_slow_disk(self):
        entered, release = threading.Event(), threading.Event()

        class SlowHandler(logging.Handler):
            def emit(self, record):
                entered.set()
                release.wait(2)

        records = BoundedLogQueue(2)
        listener = QueueListener(records, SlowHandler())
        listener.start()
        records.put(logging.makeLogRecord({"msg": "first"}))
        self.assertTrue(entered.wait(1))
        for i in range(100):
            records.put(logging.makeLogRecord({"msg": str(i)}))
        stop = threading.Thread(target=listener.stop, daemon=True)
        stop.start()
        release.set()
        stop.join(2)
        self.assertFalse(stop.is_alive(), "full log queue stranded the stop sentinel")
        self.assertGreater(records.dropped_records, 0)

    def test_fatal_failure_requests_durable_drain_once_and_bounds_it(self):
        runner = SimpleNamespace(_shutdown_event=threading.Event())
        arm = Mock()
        self.assertTrue(request_fatal_shutdown(runner, "required_worker_stopped", arm=arm))
        self.assertTrue(runner._shutdown_event.is_set())
        self.assertEqual(runner._exit_code, 1)
        self.assertTrue(runner._exit_with_failure)
        arm.assert_called_once_with(120.0, exit_code=1, name="cloud-fatal-shutdown")
        self.assertFalse(request_fatal_shutdown(runner, "second_fault", arm=arm))

    def test_exception_guard_keeps_recoverable_error_and_escalates_corruption(self):
        loop, runner = Mock(), object()
        previous = Mock()
        loop.get_exception_handler.return_value = previous
        install_exception_guard(loop, runner)
        handler = loop.set_exception_handler.call_args.args[0]
        with patch.object(runtime_guard, "request_fatal_shutdown") as fatal:
            handler(loop, {"exception": ValueError("ordinary task failure")})
            fatal.assert_not_called()
            handler(loop, {"exception": sqlite3.DatabaseError("database disk image is malformed")})
            fatal.assert_called_once_with(runner, "unrecoverable_DatabaseError")
        self.assertEqual(previous.call_count, 2)

    def test_dead_required_worker_escalates_even_with_responsive_event_loop(self):
        runner = SimpleNamespace(_running=True, _loop_heartbeat_task=Mock())
        runner._loop_heartbeat_task.done.return_value = False
        worker = Mock()
        worker.is_alive.return_value = False
        with patch.object(runtime_guard, "request_fatal_shutdown") as fatal:
            asyncio.run(monitor_required_workers(runner, threading.Event(), worker, interval=0.001))
            fatal.assert_called_once_with(runner, "housekeeping_thread_stopped")

    def test_retry_delay_is_capped_and_jittered(self):
        with patch.object(runtime_guard.random, "uniform", return_value=0.5):
            self.assertEqual(runtime_guard.retry_delay(0), 0.5)
            self.assertEqual(runtime_guard.retry_delay(100000), 30)
        with patch.object(runtime_guard.random, "uniform", return_value=1.0):
            self.assertEqual(runtime_guard.retry_delay(100000), 60)


class RuntimeProbeTests(unittest.TestCase):
    @contextmanager
    def runtime(self, *, reply=b"1", blocked=False, worker_age=0, unix=False):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "state").mkdir()
            listener = socket.socket(socket.AF_UNIX if unix else socket.AF_INET)
            listener.bind(str(home / "state/gateway.loop-tick.123.sock") if unix else ("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(1)
            release = threading.Event()

            def serve():
                try:
                    connection, _ = listener.accept()
                    with connection:
                        if blocked:
                            release.wait(1)
                        else:
                            connection.sendall(reply)
                except OSError:
                    pass

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            (home / "state/gateway.heartbeat").write_text(json.dumps({
                "pid": 123, "monotonic": time.monotonic(), "loop_tick_socket": True,
                "loop_tick_tcp_port": None if unix else listener.getsockname()[1]}))
            (home / "gateway_state.json").write_text(json.dumps({
                "pid": 123, "updated_at": (datetime.now(timezone.utc) - timedelta(seconds=worker_age)).isoformat()}))
            try:
                yield home
            finally:
                release.set()
                listener.close()
                thread.join(2)

    def test_live_event_loop_and_worker_progress_pass(self):
        with self.runtime() as home:
            result = probe(home, expected_pid=123)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["loop_responsive"])

    @unittest.skipUnless(os.name == "posix", "production Unix socket witness")
    def test_native_unix_socket_witness(self):
        with self.runtime(unix=True) as home:
            result = probe(home, expected_pid=123)
        self.assertTrue(result["ok"], result)

    def test_cron_heartbeat_without_success_does_not_count_as_progress(self):
        with self.runtime() as home:
            (home / "cron").mkdir()
            (home / "cron/ticker_heartbeat").write_text(f"{time.time()} 123")
            (home / "cron/ticker_last_success").write_text(str(time.time() - 601))
            result = probe(home, require_cron=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "cron_progress_stalled")

    def test_successful_cron_iteration_passes(self):
        with self.runtime() as home:
            (home / "cron").mkdir()
            (home / "cron/ticker_heartbeat").write_text(f"{time.time()} 123")
            (home / "cron/ticker_last_success").write_text(str(time.time()))
            result = probe(home, require_cron=True)
        self.assertTrue(result["ok"], result)

    def test_fresh_file_cannot_mask_frozen_loop(self):
        with self.runtime(blocked=True) as home:
            start = time.monotonic()
            result = probe(home, timeout=0.05)
            self.assertLess(time.monotonic() - start, 0.5)
        self.assertFalse(result["ok"])
        self.assertFalse(result["loop_responsive"])

    def test_responsive_loop_cannot_mask_stalled_housekeeping(self):
        with self.runtime(worker_age=301) as home:
            result = probe(home)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "housekeeping_stale")

    def test_pid_mismatch_and_invalid_witness_fail_closed(self):
        with self.runtime() as home:
            self.assertEqual(probe(home, expected_pid=456)["reason"], "heartbeat_pid_mismatch")
        with self.runtime(reply=b"0") as home:
            self.assertEqual(probe(home)["reason"], "invalid_loop_witness_response")


if __name__ == "__main__":
    unittest.main()

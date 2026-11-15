"""Small production backstops installed into the pinned Hermes runtime."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import queue
import random
import sqlite3
import threading

logger = logging.getLogger("gateway.cloud_resilience")
_diagnostic_rotation_lock = threading.Lock()


def retry_delay(attempt: int) -> float:
    return random.uniform(0.5, 1.0) * min(60.0, 2.0 ** min(max(attempt, 0), 6))


def rotate_diagnostic_log(path: Path, max_bytes: int = 20 * 1024 * 1024, backups: int = 5):
    """Rotate before appending a crash dump; keep the most recent five incidents files."""
    with _diagnostic_rotation_lock:
        if not path.exists() or path.stat().st_size < max_bytes:
            return
        for index in range(backups - 1, 0, -1):
            older = path.with_name(f"{path.name}.{index}")
            if older.exists():
                older.replace(path.with_name(f"{path.name}.{index + 1}"))
        path.replace(path.with_name(f"{path.name}.1"))


class BoundedLogQueue(queue.Queue):
    """Drop oldest diagnostics under disk backpressure, never a stop sentinel.

    Producers never wait for disk or queue capacity. QueueListener's None sentinel
    must remain deliverable even when all record slots are occupied.
    """

    def __init__(self, maxsize: int = 2048):
        if maxsize <= 0:
            raise ValueError("log queue capacity must be positive")
        super().__init__(maxsize)
        self.dropped_records = 0
        self._sentinel_queued = False

    def _get(self):
        item = super()._get()
        if item is None:
            self._sentinel_queued = False
        return item

    def put(self, item, block=True, timeout=None):
        with self.not_full:
            # A listener is stopping. New records must not displace its sentinel.
            if self._sentinel_queued:
                if item is not None:
                    self.dropped_records += 1
                return
            if self._qsize() >= self.maxsize:
                self._get()
                self.unfinished_tasks -= 1
                self.dropped_records += 1
            self._put(item)
            if item is None:
                self._sentinel_queued = True
            self.unfinished_tasks += 1
            self.not_empty.notify()


def request_fatal_shutdown(runner, reason: str, *, arm=None) -> bool:
    """Request normal durable drain, with the existing thread watchdog as backstop."""
    if getattr(runner, "_cloud_fatal_shutdown", False):
        return False
    runner._cloud_fatal_shutdown = True
    runner._exit_with_failure = True
    runner._exit_code = 1
    runner._exit_reason = reason
    logger.critical(json.dumps({"event": "fatal_runtime_failure", "reason": reason,
                                "exit_code": 1}, sort_keys=True))
    if arm is None:
        from gateway.shutdown_watchdog import arm_shutdown_watchdog
        arm = arm_shutdown_watchdog
    # This watchdog stays armed through asyncio's final executor drain as well.
    arm(120.0, exit_code=1, name="cloud-fatal-shutdown")
    runner._shutdown_event.set()
    return True


def install_exception_guard(loop, runner):
    """Preserve transient handling. Escalate explicit process/state corruption only."""
    previous = loop.get_exception_handler()

    def handler(active_loop, context):
        if previous is not None:
            previous(active_loop, context)
        else:
            active_loop.default_exception_handler(context)
        exc = context.get("exception")
        corrupt_db = isinstance(exc, sqlite3.DatabaseError) and any(
            token in str(exc).lower() for token in ("malformed", "not a database", "corrupt"))
        if isinstance(exc, (MemoryError, SystemError)) or corrupt_db:
            request_fatal_shutdown(runner, "unrecoverable_" + type(exc).__name__)

    loop.set_exception_handler(handler)


async def monitor_required_workers(runner, stop_event: threading.Event, housekeeping_thread,
                                   *, interval: float = 10.0):
    """Detect a dead housekeeping owner or heartbeat task while the loop still runs."""
    while not stop_event.is_set() and getattr(runner, "_running", False):
        heartbeat = getattr(runner, "_loop_heartbeat_task", None)
        if not housekeeping_thread.is_alive():
            request_fatal_shutdown(runner, "housekeeping_thread_stopped")
            return
        if heartbeat is None or heartbeat.done():
            request_fatal_shutdown(runner, "loop_heartbeat_task_stopped")
            return
        await asyncio.sleep(interval)

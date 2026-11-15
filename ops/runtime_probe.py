"""Probe the native Hermes event-loop witness, not merely a process or status file."""
from __future__ import annotations

import argparse
import json
import math
import socket
import time
from datetime import datetime, timezone
from pathlib import Path


def _read_record(path: Path) -> dict:
    with path.open("rb") as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("oversized runtime record")
    record = json.loads(data)
    if not isinstance(record, dict):
        raise ValueError("invalid runtime record")
    return record


def probe(home: Path, timeout: float = 2.0, expected_pid: int | None = None,
          require_cron: bool = False) -> dict:
    """Bound socket connect/read by one deadline; caller bounds filesystem access too.

    A fresh on-disk heartbeat alone is insufficient because its write is off-loop.
    The one-byte socket reply is dispatched by the actual gateway asyncio loop.
    Cron markers track dispatch ticks, not job completion. The pinned built-in
    scheduler uses sync=False even with one worker, so long jobs do not age them.
    """
    result = {"ok": False, "pid": None, "loop_responsive": False}
    deadline = time.monotonic() + timeout
    try:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("invalid probe timeout")
        heartbeat = _read_record(Path(home) / "state/gateway.heartbeat")
        pid = int(heartbeat["pid"])
        result["pid"] = pid
        if pid <= 0 or (expected_pid is not None and pid != expected_pid):
            raise ValueError("heartbeat_pid_mismatch")
        age = time.monotonic() - float(heartbeat["monotonic"])
        result["heartbeat_age_seconds"] = round(age, 3)
        if not math.isfinite(age) or not 0 <= age <= 120:
            raise ValueError("loop_heartbeat_stale")
        if heartbeat.get("loop_tick_socket") is not True:
            raise ValueError("loop_witness_unavailable")
        port = heartbeat.get("loop_tick_tcp_port")
        if port is not None:
            if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
                raise ValueError("invalid_witness_port")
            family, address = socket.AF_INET, ("127.0.0.1", port)
        else:
            family = socket.AF_UNIX
            address = str(Path(home) / "state" / f"gateway.loop-tick.{pid}.sock")
        with socket.socket(family, socket.SOCK_STREAM) as connection:
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            connection.connect(address)
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            if connection.recv(1) != b"1":
                raise ValueError("invalid_loop_witness_response")
        result["loop_responsive"] = True
        status = _read_record(Path(home) / "gateway_state.json")
        if int(status.get("pid", 0)) != pid:
            raise ValueError("status_pid_mismatch")
        updated = datetime.fromisoformat(status["updated_at"].replace("Z", "+00:00"))
        if updated.tzinfo is None:
            raise ValueError("invalid_status_timestamp")
        worker_age = (datetime.now(timezone.utc) - updated).total_seconds()
        result["housekeeping_age_seconds"] = round(worker_age, 3)
        if not math.isfinite(worker_age) or not -5 <= worker_age <= 300:
            raise ValueError("housekeeping_stale")
        cron_heartbeat = Path(home) / "cron/ticker_heartbeat"
        if require_cron or cron_heartbeat.exists():
            with cron_heartbeat.open("r", encoding="utf-8-sig") as stream:
                fields = stream.read(257).split()
            if len(fields) != 2 or int(fields[1]) != pid:
                raise ValueError("cron_pid_mismatch")
            cron_age = time.time() - float(fields[0])
            result["cron_heartbeat_age_seconds"] = round(cron_age, 3)
            if not math.isfinite(cron_age) or not -5 <= cron_age <= 300:
                raise ValueError("cron_heartbeat_stale")
            with (Path(home) / "cron/ticker_last_success").open("r", encoding="utf-8-sig") as stream:
                success_age = time.time() - float(stream.read(257))
            result["cron_success_age_seconds"] = round(success_age, 3)
            if not math.isfinite(success_age) or not -5 <= success_age <= 600:
                raise ValueError("cron_progress_stalled")
        result.update(ok=True, reason="loop_and_housekeeping_responding")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        known_reasons = {
            "heartbeat_pid_mismatch", "loop_heartbeat_stale", "loop_witness_unavailable",
            "invalid_witness_port", "invalid_loop_witness_response", "status_pid_mismatch",
            "invalid_status_timestamp", "housekeeping_stale", "cron_pid_mismatch",
            "cron_heartbeat_stale", "cron_progress_stalled"}
        result["reason"] = str(exc) if str(exc) in known_reasons else type(exc).__name__
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--expected-pid", type=int)
    parser.add_argument("--require-cron", action="store_true")
    args = parser.parse_args()
    result = probe(args.home, args.timeout, args.expected_pid, args.require_cron)
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["ok"] else 1)

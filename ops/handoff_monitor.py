"""Bounded handoff witness. Never cancels runs, starts consumers, or sends messages."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import time

from io_utils import atomic_json, run_process

VARIABLE = "HERMES_VERIFY_DELIVERY_SHA"
READINESS = "Hermes live runtime readiness"
SMOKE = "Hermes recovery functional smoke"
PENDING = {"queued", "waiting", "pending", "requested"}


class GitHub:
    def __init__(self, executable, *, deadline, clock=time.monotonic):
        self.executable, self.deadline, self.clock = executable, deadline, clock

    def request(self, method, endpoint):
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise TimeoutError("Monitor lifetime exhausted")
        result = run_process([self.executable, "api", "--method", method, endpoint],
                             timeout=min(45, remaining), text=True, check=True)
        return json.loads(result.stdout) if result.stdout.strip() else {}


def summary(check):
    try:
        value = json.loads(check.get("output", {}).get("summary", ""))
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def evaluate(run, checks, now):
    """Only checks attached to this exact run are evidence for the handoff."""
    matched = {name: next((item for item in checks
                          if item.get("name") == name and str(item.get("external_id")) == str(run["id"])), None)
               for name in (READINESS, SMOKE)}
    evidence = {"run_id": run["id"], "run_status": run["status"],
                "readiness": None, "functional_smoke": None}
    for name, key in ((READINESS, "readiness"), (SMOKE, "functional_smoke")):
        check = matched[name]
        if check:
            evidence[key] = {"check_run_id": check["id"], "conclusion": check.get("conclusion"),
                             "evidence": summary(check)}
    if any(item and item.get("conclusion") in {"failure", "cancelled", "timed_out", "action_required"}
           for item in matched.values()):
        return "failure", evidence
    ready, smoke = matched[READINESS], matched[SMOKE]
    if ready and smoke and ready.get("conclusion") == smoke.get("conclusion") == "success":
        state, delivery = summary(ready), summary(smoke)
        try:
            observed = datetime.fromisoformat(state["observed_at"].replace("Z", "+00:00"))
            fresh = observed.tzinfo is not None and 0 <= (now - observed).total_seconds() <= 300
        except (ValueError, TypeError, KeyError):
            fresh = False
        progress = state.get("runtime_progress")
        if (fresh and state.get("ready") is True and isinstance(progress, dict) and progress.get("ok") is True
                and delivery.get("model_marker_verified") is True
                and delivery.get("model_inference_tested") is True
                and delivery.get("telegram_delivery_acknowledged") is True
                and delivery.get("message_sent") is True
                and delivery.get("sqlite_integrity") == "ok"):
            evidence["marker"] = "HERMES_RECOVERY_OK"
            return "success", evidence
    if run["status"] == "completed" and run.get("conclusion") not in {"success", "cancelled"}:
        return "failure", evidence
    return "waiting", evidence


def snapshot(client, repository, sha, now):
    runs = client.request("GET", f"repos/{repository}/actions/workflows/hermes.yml/runs?per_page=100")["workflow_runs"]
    candidates = [run for run in runs if run.get("head_sha") == sha and run.get("head_branch") == "main"]
    active = [run for run in candidates if run["status"] == "in_progress"]
    pending = [run for run in candidates if run["status"] in PENDING]
    selected = max(active or pending or candidates, key=lambda item: item["id"], default=None)
    if selected is None:
        return "waiting", {"run_id": None, "reason": "target_cycle_not_visible"}
    checks = client.request("GET", f"repos/{repository}/commits/{sha}/check-runs?per_page=100")["check_runs"]
    return evaluate(selected, checks, now)


def clear_request(client, repository, sha):
    endpoint = f"repos/{repository}/actions/variables/{VARIABLE}"
    record = client.request("GET", endpoint)
    if record.get("value") == sha:
        client.request("DELETE", endpoint)
        return "cleared_matching_sha"
    return "preserved_other_value"


def monitor(client, repository, sha, output, *, deadline, interval=180,
            clock=time.monotonic, sleep=time.sleep, utcnow=lambda: datetime.now(timezone.utc)):
    # Reserve time for the final guarded variable read/delete within the lifetime.
    polling_deadline = deadline - min(90, max(0, (deadline - clock()) / 4))
    status, evidence = "waiting", {}
    result = {"repository": repository, "head_sha": sha, "monitor_pid": os.getpid()}
    try:
        while clock() < polling_deadline:
            try:
                status, evidence = snapshot(client, repository, sha, utcnow())
                result.pop("last_error_type", None)
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, TimeoutError) as exc:
                result["last_error_type"] = type(exc).__name__
                status = "waiting"
            result.update(status=status, observed_at=utcnow().isoformat(), **evidence)
            atomic_json(output, result)
            if status in {"success", "failure"}:
                break
            sleep(max(0, min(interval, polling_deadline - clock())))
        else:
            status = "timeout"
    finally:
        result.update(status=status if status in {"success", "failure", "timeout"} else "failure",
                      finished_at=utcnow().isoformat())
        try:
            result["smoke_request_cleanup"] = clear_request(client, repository, sha)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, TimeoutError) as exc:
            result["smoke_request_cleanup"] = "failed_" + type(exc).__name__
        atomic_json(output, result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gh", default="gh")
    parser.add_argument("--max-seconds", type=float, default=12600)
    parser.add_argument("--interval", type=float, default=180)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        parser.error("Invalid repository")
    if not re.fullmatch(r"[a-f0-9]{40}", args.sha):
        parser.error("Expected a 40-character commit SHA")
    if not 0 < args.max_seconds <= 12600 or not 0 < args.interval <= 180:
        parser.error("Lifetime must be <=12600 seconds and interval <=180 seconds")
    # flock is advisory and the kernel releases it even after a forced exit.
    import fcntl
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        deadline = time.monotonic() + args.max_seconds
        result = monitor(GitHub(args.gh, deadline=deadline), args.repo, args.sha, args.output,
                         deadline=deadline, interval=args.interval)
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())

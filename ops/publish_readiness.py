"""Expose a safe live readiness receipt through a repository check run."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

from live_control import readiness
from io_utils import atomic_json
from runtime_probe import probe


def publish(home: Path, sha: str) -> dict:
    snapshot = readiness(home)
    snapshot["runtime_progress"] = probe(home, require_cron=True)
    snapshot["ready"] = snapshot["ready"] and snapshot["runtime_progress"]["ok"]
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    snapshot.update({"observed_at": datetime.now(timezone.utc).isoformat(), "run_id": run_id,
                     "inference_tested": False, "message_sent": False})
    record_path = home / "state/runtime_readiness.json"
    try:
        previous = json.loads(record_path.read_text())
        if not isinstance(previous, dict):
            previous = {}
        stamp = datetime.fromisoformat(previous.get("observed_at", ""))
        if stamp.tzinfo is None or not isinstance(previous.get("check_run_id"), int):
            previous = {}
    except (OSError, ValueError, TypeError):
        previous = {}
    same_run = previous.get("run_id") == run_id
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(previous["observed_at"])).total_seconds() if same_run else 0
    if (not run_id or (not snapshot["ready"] and not same_run)
            or (same_run and previous.get("ready") == snapshot["ready"] and age < 180)):
        return snapshot
    payload = {"name": "Hermes live runtime readiness", "status": "completed",
               "conclusion": "success" if snapshot["ready"] else "failure",
               "output": {"title": "Gateway READY with loop and housekeeping progress" if snapshot["ready"] else "Live gateway readiness is degraded",
                          "summary": json.dumps(snapshot, sort_keys=True)}}
    endpoint = "repos/" + os.environ["GITHUB_REPOSITORY"] + "/check-runs"
    if same_run:
        endpoint += "/" + str(previous["check_run_id"])
    else:
        payload.update(head_sha=sha, external_id=run_id)
    result = subprocess.run(["gh", "api", "--method", "PATCH" if same_run else "POST", endpoint, "--input", "-"],
                            input=json.dumps(payload), text=True, capture_output=True, check=True, timeout=30)
    snapshot["check_run_id"] = json.loads(result.stdout)["id"]
    atomic_json(record_path, snapshot)
    return snapshot


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    print(json.dumps(publish(args.home, args.sha), sort_keys=True))

"""Expose a safe live readiness receipt through a repository check run."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

from live_control import readiness


def publish(home: Path, sha: str) -> dict:
    snapshot = readiness(home)
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    snapshot.update({"observed_at": datetime.now(timezone.utc).isoformat(), "run_id": run_id,
                     "inference_tested": False, "message_sent": False})
    record_path = home / "state/runtime_readiness.json"
    previous = json.loads(record_path.read_text()) if record_path.exists() else {}
    same_run = previous.get("run_id") == run_id
    if (not run_id or (not snapshot["ready"] and not same_run)
            or (same_run and previous.get("ready") == snapshot["ready"])):
        return snapshot
    payload = {"name": "Hermes live runtime readiness", "status": "completed",
               "conclusion": "success" if snapshot["ready"] else "failure",
               "output": {"title": "Gateway READY and Telegram adapter connected" if snapshot["ready"] else "Live gateway readiness is degraded",
                          "summary": json.dumps(snapshot, sort_keys=True)}}
    endpoint = "repos/" + os.environ["GITHUB_REPOSITORY"] + "/check-runs"
    if same_run:
        endpoint += "/" + str(previous["check_run_id"])
    else:
        payload.update(head_sha=sha, external_id=run_id)
    result = subprocess.run(["gh", "api", "--method", "PATCH" if same_run else "POST", endpoint, "--input", "-"],
                            input=json.dumps(payload), text=True, capture_output=True, check=True)
    snapshot["check_run_id"] = json.loads(result.stdout)["id"]
    record_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = record_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(snapshot, sort_keys=True))
    temporary.chmod(0o600)
    temporary.replace(record_path)
    return snapshot


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    print(json.dumps(publish(args.home, args.sha), sort_keys=True))

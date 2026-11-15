import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
from handoff_monitor import GitHub, READINESS, SMOKE, clear_request, evaluate, monitor, snapshot

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
SHA = "a" * 40


def checks(*, age=0, run_id="9", marker=True, ack=True):
    return [
        {"id": 10, "name": READINESS, "external_id": run_id, "conclusion": "success", "output": {"summary": json.dumps({
            "observed_at": (NOW - timedelta(seconds=age)).isoformat(), "ready": True, "runtime_progress": {"ok": True}})}},
        {"id": 11, "name": SMOKE, "external_id": run_id, "conclusion": "success", "output": {"summary": json.dumps({
            "model_marker_verified": marker, "model_inference_tested": True,
            "telegram_delivery_acknowledged": ack, "message_sent": True, "sqlite_integrity": "ok"})}},
    ]


class FakeGitHub:
    def __init__(self, *, value=SHA, records=None):
        self.value, self.records, self.calls = value, records or checks(), []

    def request(self, method, path):
        self.calls.append((method, path))
        if "/variables/" in path:
            return {"value": self.value}
        if "/check-runs" in path:
            return {"check_runs": self.records}
        return {"workflow_runs": [
            {"id": 9, "status": "in_progress", "head_sha": SHA, "head_branch": "main"},
            {"id": 12, "status": "pending", "head_sha": SHA, "head_branch": "main"}]}


class HandoffMonitorTests(unittest.TestCase):
    def test_api_call_is_bounded_by_remaining_lifetime(self):
        with patch("handoff_monitor.run_process") as run:
            run.return_value.stdout = "{}"
            client = GitHub("gh", deadline=100, clock=lambda: 92)
            client.request("GET", "repos/owner/repo")
            self.assertEqual(run.call_args.kwargs["timeout"], 8)
            client = GitHub("gh", deadline=100, clock=lambda: 101)
            with self.assertRaises(TimeoutError):
                client.request("GET", "repos/owner/repo")
            self.assertEqual(run.call_count, 1)

    def test_requires_fresh_same_run_model_and_delivery_evidence(self):
        run = {"id": 9, "status": "in_progress"}
        self.assertEqual(evaluate(run, checks(), NOW)[0], "success")
        for evidence in (checks(age=301), checks(run_id="8"), checks(marker=False), checks(ack=False)):
            self.assertEqual(evaluate(run, evidence, NOW)[0], "waiting")

    def test_matching_failed_check_is_terminal(self):
        evidence = checks()
        evidence[1]["conclusion"] = "failure"
        self.assertEqual(evaluate({"id": 9, "status": "in_progress"}, evidence, NOW)[0], "failure")

    def test_malformed_progress_or_missing_delivery_proof_cannot_pass(self):
        for field in ("message_sent", "model_inference_tested"):
            evidence = checks()
            record = json.loads(evidence[1]["output"]["summary"])
            record[field] = False
            evidence[1]["output"]["summary"] = json.dumps(record)
            self.assertEqual(evaluate({"id": 9, "status": "in_progress"}, evidence, NOW)[0], "waiting")
        evidence = checks()
        record = json.loads(evidence[0]["output"]["summary"])
        record["runtime_progress"] = []
        evidence[0]["output"]["summary"] = json.dumps(record)
        self.assertEqual(evaluate({"id": 9, "status": "in_progress"}, evidence, NOW)[0], "waiting")

    def test_active_same_sha_cycle_takes_precedence_over_pending_replacement(self):
        result, evidence = snapshot(FakeGitHub(), "owner/repo", SHA, NOW)
        self.assertEqual((result, evidence["run_id"]), ("success", 9))

    def test_clears_only_matching_variable_and_never_mutates_runs(self):
        for value, deletes in ((SHA, 1), ("b" * 40, 0)):
            client = FakeGitHub(value=value)
            clear_request(client, "owner/repo", SHA)
            self.assertEqual(sum(method == "DELETE" for method, _ in client.calls), deletes)
            self.assertTrue(all("/variables/" in path for method, path in client.calls))

    def test_success_writes_atomic_final_verdict(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "result.json"
            client = FakeGitHub()
            result = monitor(client, "owner/repo", SHA, path, deadline=100, clock=lambda: 0, utcnow=lambda: NOW)
            self.assertEqual(result["status"], "success")
            self.assertEqual(json.loads(path.read_text())["marker"], "HERMES_RECOVERY_OK")
            self.assertEqual(client.calls[-1][0], "DELETE")

    def test_timeout_is_bounded_and_writes_final_result(self):
        with tempfile.TemporaryDirectory() as folder:
            value = [0]
            client = FakeGitHub(records=checks(age=301))
            result = monitor(client, "owner/repo", SHA, Path(folder) / "result.json", deadline=20, interval=8,
                             clock=lambda: value[0], sleep=lambda seconds: value.__setitem__(0, value[0] + seconds),
                             utcnow=lambda: NOW)
            self.assertEqual(result["status"], "timeout")
            self.assertLessEqual(value[0], 20)
            self.assertEqual(client.calls[-1][0], "DELETE")


if __name__ == "__main__":
    unittest.main()

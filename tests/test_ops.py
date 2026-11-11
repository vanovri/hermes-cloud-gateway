import importlib.util
import json
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))
sys.path.insert(0, str(ROOT / "overlay/.hermes/ops"))
from runtime_config import configure
from state_bundle import create_bundle
from startup_evidence import checked_url, commit, database, prepare, validate_items
import startup_evidence


class OpsTests(unittest.TestCase):
    def test_cloud_without_a_cron_store_migrates_existing_active_jobs(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / "ops").mkdir()
            (home / "ops/startup_prompt.txt").write_text("structured evidence prompt")
            (home / "config.yaml").write_text("model: {provider: custom, default: existing}\n")
            result = configure(home)
            jobs = json.loads((home / "cron/jobs.json").read_text())["jobs"]
            self.assertIn("2a8e765b6611", result["migrated_missing_jobs"])
            self.assertTrue(all(job["deliver"] == "telegram" and job["origin"] is None for job in jobs))
            self.assertTrue(all(job["next_run_at"] > job["created_at"] for job in jobs))
            self.assertFalse(configure(home)["migrated_missing_jobs"])

    def test_sqlite_wal_and_operational_state_survive_restore(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "user"
            hermes = home / ".hermes"
            (hermes / "cron").mkdir(parents=True)
            (hermes / ".env").write_text("DUMMY_SECRET=not-a-real-secret\n")
            (hermes / "config.yaml").write_text("gateway: {}\n")
            (hermes / "cron/jobs.json").write_text('{"jobs": []}')
            (hermes / "scripts").mkdir()
            (hermes / "scripts/example.py").write_text("print('synthetic')\n")
            original = sqlite3.connect(hermes / "state.db")
            original.execute("PRAGMA journal_mode=WAL")
            original.execute("CREATE TABLE delivery_obligations (content TEXT)")
            original.execute("INSERT INTO delivery_obligations VALUES ('pending synthetic')")
            original.commit()
            ledger = sqlite3.connect(hermes / "cron/deliveries.db")
            ledger.execute("CREATE TABLE deliveries (content TEXT)")
            ledger.execute("INSERT INTO deliveries VALUES ('queued synthetic')")
            ledger.commit()
            archive = Path(td) / "result.tar.gz"
            result = create_bundle(home, archive)
            self.assertEqual(result["sqlite_snapshots"], 2)
            restored = Path(td) / "restored"
            with tarfile.open(archive) as bundle:
                bundle.extractall(restored, filter="data")
            with sqlite3.connect(restored / ".hermes/state.db") as db:
                self.assertEqual(db.execute("SELECT content FROM delivery_obligations").fetchone()[0], "pending synthetic")
            with sqlite3.connect(restored / ".hermes/cron/deliveries.db") as db:
                self.assertEqual(db.execute("SELECT content FROM deliveries").fetchone()[0], "queued synthetic")
            self.assertTrue((restored / ".hermes/scripts/example.py").exists())
            original.close()
            ledger.close()

    def test_missing_required_state_never_replaces_previous_archive(self):
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / "bundle.tar.gz"
            archive.write_bytes(b"previous good archive")
            with self.assertRaises(RuntimeError):
                create_bundle(Path(td) / "missing", archive)
            self.assertEqual(archive.read_bytes(), b"previous good archive")

    def test_bootstrap_preserves_model_choices_pauses_only_expired_event(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / "cron").mkdir()
            (home / "ops").mkdir()
            (home / "ops/startup_prompt.txt").write_text("structured evidence prompt")
            (home / "config.yaml").write_text("model: {provider: custom, default: chosen}\nfallback_providers: [{provider: independent}]\n")
            jobs = [{"id": "2a8e765b6611", "enabled": True, "context_from": ["self"]},
                    {"id": "c3277fb270f1", "enabled": True}, {"id": "unrelated", "enabled": False}]
            (home / "cron/jobs.json").write_text(json.dumps({"jobs": jobs}))
            configure(home)
            import yaml
            cfg = yaml.safe_load((home / "config.yaml").read_text())
            self.assertEqual(cfg["model"]["default"], "chosen")
            self.assertEqual(cfg["fallback_providers"], [{"provider": "independent"}])
            changed = json.loads((home / "cron/jobs.json").read_text())["jobs"]
            self.assertIsNone(changed[0]["context_from"])
            self.assertFalse(changed[1]["enabled"])
            self.assertFalse(changed[2]["enabled"])

    def test_fabricated_quote_and_deadline_mismatch_are_rejected(self):
        hosts = {"example.org": "Synthetic organizer"}
        candidate = {"program_title": "Synthetic Climate Call", "official_url": "https://example.org/call",
                     "category": "open_call", "deadline_status": "EXACT_DATE", "deadline_iso": "2026-12-20",
                     "verbatim_deadline_quote": "Applications close on 20 December 2026.",
                     "verbatim_benefit_quote": "Technical support is available."}
        page = "Synthetic Climate Call. Applications close on 20 December 2026. Technical support is available."
        fetcher = lambda *args: page
        accepted, rejected = validate_items([candidate], hosts, fetcher=fetcher, today=date(2026,11,11))
        self.assertEqual(len(accepted), 1)
        mismatched = {**candidate, "deadline_iso": "2099-12-31"}
        forged = {**candidate, "verbatim_benefit_quote": "A grant of USD 150000 is guaranteed."}
        self.assertFalse(validate_items([mismatched], hosts, fetcher=fetcher, today=date(2026,11,11))[0])
        self.assertFalse(validate_items([forged], hosts, fetcher=fetcher, today=date(2026,11,11))[0])
        with self.assertRaises(ValueError):
            checked_url("https://user:password@example.org/call", hosts)
        with self.assertRaises(ValueError):
            checked_url("https://example.org.evil.test/call", hosts)

    def test_failed_delivery_does_not_mark_opportunities_seen(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / "ops").mkdir()
            (home / "ops/startup_sources.json").write_text(json.dumps({"hosts": {"example.org": "Synthetic organizer"}}))
            candidate = {"program_title": "Synthetic Climate Call", "official_url": "https://example.org/call",
                         "category": "investor_reference", "deadline_status": "NOT_STATED", "deadline_iso": None,
                         "verbatim_benefit_quote": "Technical support is available."}
            page = "Synthetic Climate Call. Technical support is available."
            original = startup_evidence.validate_items
            def local_validate(items, hosts, **kwargs):
                return original(items, hosts, fetcher=lambda *a: page, **kwargs)
            with patch.object(startup_evidence, "validate_items", side_effect=local_validate):
                text = prepare(home, json.dumps({"opportunities": [candidate]}))
                with database(home) as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM delivered").fetchone()[0], 0)
                # The exact rendered payload is the commit identity, not a model-supplied flag.
                with self.assertRaises(ValueError):
                    commit(home, text + " altered")
                commit(home, text)
                with database(home) as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM delivered").fetchone()[0], 1)
                again = prepare(home, json.dumps({"opportunities": [candidate]}))
                self.assertNotIn("1. Synthetic Climate Call", again)


if __name__ == "__main__":
    unittest.main()

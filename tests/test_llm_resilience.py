import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
from llm_resilience import bounded_outbound, compact_skill_index, fresh_payload_rejection, skip_fallback, RequestBudgetExceeded, unhealthy_legacy_override
from runtime_config import configure_llm


class LLMResilienceTests(unittest.TestCase):
    def test_legacy_override_cannot_reintroduce_failed_cloud_route(self):
        self.assertTrue(unhealthy_legacy_override({"provider": "custom", "model": "grok-auto"}))
        self.assertTrue(unhealthy_legacy_override({"provider": "openrouter", "model": "particle-ai/particle-1:free"}))
        self.assertFalse(unhealthy_legacy_override({"provider": "custom", "model": "gemini-3.5-flash-lite"}))
    def test_large_catalog_is_lazy_and_bounded_without_removing_skills(self):
        catalog = {"engineering": [(f"skill-{index}", "specialized instructions " * 30) for index in range(1134)]}
        result = compact_skill_index(catalog, {"skills_list", "skill_view"})
        self.assertLess(len(result.encode()), 2000)
        self.assertIn("1134", result)
        self.assertIn("skills_list", result)
        self.assertEqual(len(catalog["engineering"]), 1134)
        self.assertIsNone(compact_skill_index({"small": [("one", "description")]}, {"skills_list"}))

    def test_vision_fallback_cannot_silently_discard_image(self):
        agent = SimpleNamespace(platform="telegram", _cloud_request_has_images=True)
        self.assertTrue(skip_fallback(agent, {"provider": "groq", "model": "openai/gpt-oss-120b"}))
        self.assertTrue(skip_fallback(agent, {"provider": "openrouter", "model": "text-only"}))
        self.assertFalse(skip_fallback(agent, {"provider": "openrouter", "model": "verified", "supports_vision": True}))

    def test_result_bound_changes_only_outbound_copy(self):
        messages = [{"role": "tool", "content": "a" * 300000}]
        result = bounded_outbound(SimpleNamespace(), messages)
        self.assertLess(len(result[0]["content"]), 10000)
        self.assertEqual(len(messages[0]["content"]), 300000)

    def test_attachment_budget_fails_before_network_and_preserves_input(self):
        messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 1600000}}]}]
        with self.assertRaises(RequestBudgetExceeded) as error:
            bounded_outbound(SimpleNamespace(), messages)
        self.assertEqual(error.exception.status_code, 413)
        self.assertEqual(len(messages[0]["content"]), 1)
        self.assertTrue(fresh_payload_rejection(messages))

    def test_verified_routes_avoid_constrained_provider_and_timeouts_are_short(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / ".env").write_text("OPENROUTER_API_KEY=synthetic\nGOOGLE_API_KEY=synthetic\nCOPILOT_GITHUB_TOKEN=synthetic\n")
            config = {"model": {"provider": "9router", "default": "grok-auto"}, "fallback_providers": [{"provider": "groq"}]}
            configure_llm(config, home)
            self.assertEqual(config["model"]["default"], "gemini-3.5-flash-lite")
            self.assertEqual(config["model"]["provider"], "custom")
            self.assertEqual(config["model"]["key_env"], "GOOGLE_API_KEY")
            self.assertEqual(config["fallback_providers"][0]["model"], "gemini-3.5-flash")
            self.assertEqual(config["fallback_providers"][0]["key_env"], "GOOGLE_API_KEY")
            self.assertEqual(
                [(route["provider"], route["model"]) for route in config["fallback_providers"]],
                [("custom", "gemini-3.5-flash"), ("copilot", "gpt-4.1"), ("openrouter", "openrouter/free")],
            )
            self.assertNotIn("synthetic", json.dumps(config))
            self.assertEqual(config["auxiliary"]["compression"]["model"], "gemini-3.5-flash-lite")
            self.assertTrue(all(entry["supports_vision"] and entry["provider"] != "groq" for entry in config["fallback_providers"]))
            self.assertEqual(config["agent"]["api_max_retries"], 1)
            self.assertEqual(config["agent"]["local_stream_stale_timeout"], 20)
            self.assertTrue(all(provider["request_timeout_seconds"] == provider["stale_timeout_seconds"] == 20 for provider in config["providers"].values()))

    def test_google_does_not_depend_on_openrouter_and_legacy_chain_is_removed(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {}, clear=True):
            home = Path(folder)
            (home / '.env').write_text('GOOGLE_API_KEY=synthetic\n')
            config = {'fallback_model': {'provider': 'openrouter', 'model': 'extra:free'}}
            configure_llm(config, home)
            self.assertEqual(len(config['fallback_providers']), 1)
            self.assertNotIn('fallback_model', config)
            self.assertNotIn('synthetic', json.dumps(config))

    def test_missing_google_key_cannot_silently_boot_into_daily_cap(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {}, clear=True):
            home = Path(folder)
            (home / '.env').write_text('OPENROUTER_API_KEY=synthetic\n')
            with self.assertRaisesRegex(ValueError, 'GOOGLE_API_KEY'):
                configure_llm({}, home)


if __name__ == "__main__":
    unittest.main()

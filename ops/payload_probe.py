"""Run through hermes --run-module cloud_payload_probe in an isolated restore."""
import json
import os
from pathlib import Path


def measure():
    import yaml
    from run_agent import AIAgent
    from agent.system_prompt import build_system_prompt_parts, _skills_prompt
    home = Path(os.environ["HERMES_HOME"])
    config = yaml.safe_load((home / "config.yaml").read_text()) or {}
    model = config.get("model", {})
    agent = AIAgent(model=model.get("default", "diagnostic"), provider="custom",
                    base_url="http://127.0.0.1:20129/v1", api_key="diagnostic-no-inference",
                    platform="telegram", enabled_toolsets=config.get("platform_toolsets", {}).get("telegram", ["hermes-telegram"]),
                    quiet_mode=True, load_soul_identity=True, skip_background_review=True)
    try:
        parts = build_system_prompt_parts(agent)
        system = "\n\n".join(value for value in parts.values() if value)
        request = {"model": model.get("default"), "messages": [{"role": "system", "content": system},
                    {"role": "user", "content": "https://www.instagram.com/p/test/"}], "tools": agent.tools}
        wire = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
        skills = _skills_prompt(agent)
        result = {"request_bytes": len(wire.encode()), "system_bytes": len(system.encode()),
                  "skill_catalog_bytes": len(skills.encode()), "tool_schema_bytes": len(json.dumps(agent.tools).encode()),
                  "tool_count": len(agent.tools), "system_tiers_bytes": {k: len(v.encode()) for k, v in parts.items()}}
        try:
            import tiktoken
            encoder = tiktoken.get_encoding("cl100k_base")
            result.update(reference_tokens=len(encoder.encode(wire, disallowed_special=())), token_method="cl100k_base_reference_not_provider_usage")
        except (ImportError, OSError):
            result.update(reference_tokens=(len(wire) + 3) // 4, token_method="characters_divided_by_4_estimate")
        Path(os.environ["PAYLOAD_PROBE_OUTPUT"]).write_text(json.dumps(result))
    finally:
        agent.close()


if __name__ == "__main__":
    measure()

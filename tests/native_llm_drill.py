"""Exercise the patched native 413 and gateway verdict functions without a provider."""
import ast
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


def load_function(path, name, namespace):
    tree = ast.parse(path.read_text())
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[name]


def main(core):
    spec = importlib.util.spec_from_file_location("agent.cloud_llm_resilience", core / "agent/cloud_llm_resilience.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    metadata = ModuleType("agent.model_metadata")
    metadata.estimate_messages_tokens_rough = lambda messages: 100
    namespace = {"FailoverReason": SimpleNamespace(payload_too_large="payload_too_large")}
    recover = load_function(core / "agent/turn_overflow.py", "_recover_payload_too_large", namespace)
    agent = SimpleNamespace(_try_activate_fallback=lambda **kwargs: False)
    class Recovery:
        messages = [{"role": "system", "content": "large skill catalog"}, {"role": "user", "content": "one URL"}]
        def __init__(self):
            self.agent = agent
        def fail_turn(self, text, **flags):
            return {"failed": True, "error": text, "failure_reason": flags.pop("reason"), **flags}
        def count_attempt(self, **kwargs):
            raise AssertionError("A fresh request must not compress history it does not have")
    with patch.dict(sys.modules, {"agent.cloud_llm_resilience": helper, "agent.model_metadata": metadata}):
        result = recover(Recovery(), SimpleNamespace())
    assert result["failure_reason"] == "provider_request_limit" and result["compression_exhausted"] is False
    verdict = load_function(core / "gateway/run_turn.py", "is_context_overflow_failure_result", {})
    assert verdict(result, 0) is False
    assert verdict({"failed": True, "compression_exhausted": True}, 100) is True
    print("PASS native fresh-413 preserves session; genuine history overflow still detected")


if __name__ == "__main__":
    main(Path(sys.argv[1]))

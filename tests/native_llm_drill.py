"""Exercise pinned native recovery and route binding without network or live state."""
import ast
import importlib.util
import inspect
import logging
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


def load_function(path, name, namespace):
    tree = ast.parse(path.read_text())
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[name]


def rate_limit_drill(core):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
    from runtime_config import configure_llm
    with tempfile.TemporaryDirectory() as folder:
        home = Path(folder)
        (home / '.env').write_text('GOOGLE_API_KEY=synthetic-google\nOPENROUTER_API_KEY=synthetic-router\n'
                                   'COPILOT_GITHUB_TOKEN=synthetic-copilot\n')
        plugin = home / 'plugins/github-copilot/plugin.yaml'
        plugin.parent.mkdir(parents=True)
        plugin.touch()
        config = {}
        configure_llm(config, home)
    custom_ns = {'_clean': lambda value: str(value or '').strip(),
                 'get_secret_str': lambda name, default: 'synthetic-google' if name == 'GOOGLE_API_KEY' else default}
    load_function(core / 'hermes_cli/runtime_provider_custom.py', '_key_env_secret', custom_ns)
    primary_key = load_function(core / 'hermes_cli/runtime_provider_custom.py', '_model_cfg_key_env_for', custom_ns)
    assert primary_key(config['model'], config['model']['base_url']) == 'synthetic-google'
    assert primary_key(config['model'], 'https://unrelated.invalid/v1') == ''

    def module(name, **values):
        result = ModuleType(name)
        result.__dict__.update(values)
        return result

    noop = lambda *args, **kwargs: None
    chain_spec = importlib.util.spec_from_file_location('hermes_cli.fallback_config', core / 'hermes_cli/fallback_config.py')
    schema = importlib.util.module_from_spec(chain_spec)
    chain_spec.loader.exec_module(schema)
    reason = SimpleNamespace(rate_limit='rate_limit')
    calls = []
    reply = 'COPILOT_NATIVE_OK'
    reset_at = 2_000_000_000
    cooldown_hints = []

    class HTTP429(RuntimeError):
        status_code = 429

    def rate_limited(model):
        calls.append(model)
        raise HTTP429('Rate limit exceeded')

    def copilot_call():
        calls.append('gpt-4.1')
        return reply

    def resolve_client(provider, *, model, explicit_base_url, explicit_api_key, **kwargs):
        if provider == 'custom':
            assert model == 'gemini-3.5-flash' and explicit_api_key == 'synthetic-google'
            return SimpleNamespace(base_url=explicit_base_url, create=lambda: rate_limited(model)), model
        assert provider == 'copilot' and model == 'gpt-4.1'
        assert not explicit_api_key and not explicit_base_url
        return SimpleNamespace(base_url='https://api.githubcopilot.com', create=copilot_call), model

    def swap_client(agent, client, *args):
        agent.client = client

    def arm_restart(agent, messages, prompt, retry):
        retry.restart_with_rebuilt_messages = True
        return prompt

    modules = {
        'hermes_cli.fallback_config': schema,
        'hermes_cli.providers': module('hermes_cli.providers', normalize_provider=lambda value: value.lower(), is_actual_route=lambda *args: True),
        'hermes_cli.model_normalize': module('hermes_cli.model_normalize', normalize_model_for_provider=lambda model, provider: model),
        'agent.secret_scope': module('agent.secret_scope', get_secret=lambda name: 'synthetic-google' if name == 'GOOGLE_API_KEY' else None),
        'agent.auxiliary_client': module('agent.auxiliary_client', resolve_provider_client=resolve_client),
        'agent.fallback_cooldown': module('agent.fallback_cooldown', switch_deferred_by_reset=lambda *args: False,
                                         _arm_rate_limit_cooldown=lambda a, r, reset_at: cooldown_hints.append(reset_at)),
        'agent.turn_recovery_codex': module('agent.turn_recovery_codex', reset_codex_reasoning_replay=noop),
        'agent.client_lifecycle': module('agent.client_lifecycle', _swap_fallback_clients=swap_client),
        'agent.agent_runtime_helpers': module('agent.agent_runtime_helpers', sync_credential_pool_entry_id=noop),
        'agent.native_compaction': module('agent.native_compaction', resolve_native_compaction_capabilities=lambda **kwargs: {}),
        'hermes_cli.observability.shared_metrics_events': module('hermes_cli.observability.shared_metrics_events', record_fallback=noop),
        'agent.conversation_loop': module('agent.conversation_loop', _arm_fallback_restart=arm_restart),
    }
    helper_values = {name: noop for name in ('_rebind_fallback_credential_pool', '_reresolve_fallback_reasoning_config', '_rescope_fallback_extra_body', '_update_fallback_context_compressor')}
    helper_values.update(_fallback_api_mode_hint=lambda *args: (True, 'chat_completions'), _fallback_api_mode_resolved=lambda *args: 'chat_completions')
    modules['agent.chat_completion_helpers'] = module('agent.chat_completion_helpers', **helper_values)
    logger = logging.getLogger('native-429-drill')
    bind = load_function(core / 'agent/route_binding.py', 'bind_route_entry', {'logger': logger, 'base_url_host_matches': lambda *args: False})
    modules['agent.route_binding'] = module('agent.route_binding', bind_route_entry=bind)
    fallback_ns = {'logger': logger, 'rewrite_prompt_model_identity': noop, '_reset_stale_streak': noop,
                   '_fallback_entry_key': lambda fb: (fb['provider'], fb['model'], fb.get('base_url', '')),
                   '_should_skip_fallback_candidate': lambda *args: False,
                   '_fallback_chain_exhausted': lambda *args: False, '_fallback_reason_text': str,
                   '_buffer_fallback_notice': noop, '_log_fallback_activated': noop}
    activate = load_function(core / 'agent/chat_completion_helpers.py', 'try_activate_fallback', fallback_ns)
    handle = load_function(core / 'agent/turn_api_error.py', 'handle_api_error', {'ApiErrorVerdict': SimpleNamespace, 'FailoverReason': reason})
    with patch.dict(sys.modules, modules):
        chain = schema.get_fallback_chain(config)
        assert chain[0]['key_env'] == 'GOOGLE_API_KEY' and chain[0]['supports_vision'] is True
        agent = SimpleNamespace(model=config['model']['default'], provider='custom', base_url=config['model']['base_url'],
                                client=SimpleNamespace(create=lambda: rate_limited(config['model']['default'])), thinking_callback=None,
                                _fallback_chain=chain, _fallback_index=0,
                                _extract_api_error_context=lambda error: {'reset_at': reset_at},
                                _anthropic_prompt_cache_policy=lambda **kwargs: (False, False), _ensure_lmstudio_runtime_loaded=noop)
        agent._try_activate_fallback = lambda **kwargs: activate(agent, **kwargs)
        retry = SimpleNamespace(restart_with_rebuilt_messages=False)
        kwargs = {name: None for name in inspect.signature(handle).parameters if name != 'agent'}
        kwargs.update(_retry=retry, messages=[{'role': 'user', 'content': 'synthetic turn'}], api_messages=[],
                      retry_count=1, max_retries=1, compression_attempts=0)
        for index, model in enumerate(('gemini-3.5-flash', 'gpt-4.1'), start=1):
            retry.restart_with_rebuilt_messages = False
            try:
                agent.client.create()
            except HTTP429 as error:
                verdict = handle(agent, **{**kwargs, 'api_error': error})
            else:
                raise AssertionError('Google route must return mocked HTTP 429')
            assert verdict.action == 'break' and verdict.retry_count == 0
            assert retry.restart_with_rebuilt_messages is True
            assert agent.model == model and agent._fallback_index == index
        assert agent.client.create() == reply
        assert calls == ['gemini-3.5-flash-lite', 'gemini-3.5-flash', 'gpt-4.1'], calls
        assert cooldown_hints == [reset_at, reset_at], cooldown_hints
    print('PASS native HTTP 429 on both Google routes -> Copilot response; provider reset hint preserved')


def main(core):
    rate_limit_drill(core)
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

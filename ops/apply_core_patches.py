"""Narrow, version-checked patches. Never replace the existing delivery ledger."""
from pathlib import Path
import argparse
import hashlib
import json


def replace_once(path: Path, original: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    if replacement in text:
        return
    if text.count(original) != 1:
        raise RuntimeError(f"Patch context changed: {path.name}; refusing a blind replacement")
    path.write_text(text.replace(original, replacement, 1), encoding="utf-8")


def apply(core: Path, source: Path) -> dict:
    resilience_paths = apply_resilience(core, source)
    delivery_path = core / "cron/scheduler_delivery.py"
    text = delivery_path.read_text(encoding="utf-8")
    text = text.replace('except Exception as exc:\n            logger.error("Job output validator rejected output (%s)", type(exc).__name__)',
                        'except Exception:\n            logger.exception("Job output validator rejected output")')
    text = text.replace('except Exception as exc:\n            # The send succeeded; a dedup bookkeeping failure must not trigger another send.\n            logger.error("Delivered output dedup commit failed (%s)", type(exc).__name__)',
                        'except Exception:\n            # The send succeeded; a dedup bookkeeping failure must not trigger another send.\n            logger.exception("Delivered output dedup commit failed")')
    delivery_path.write_text(text, encoding="utf-8")
    initial_patch = (
        "        # The ticker starts before READY is published; keep due instants intact during boot.\n"
        "        if not headless and loop is not None:\n"
        "            from cron.scheduler_gateway_gate import _gateway_unavailable\n"
        "            hint = _gateway_unavailable(_sched._get_hermes_home())\n"
        "            if hint is not None:\n"
        "                _sched.logger.info(\"Cron startup deferred: %s\", hint)\n"
        "                return 0\n\n"
        "        _pre_dispatch_housekeeping(_sched, headless)\n")
    tick = core / "cron/scheduler_tick.py"
    text = tick.read_text(encoding="utf-8")
    if initial_patch in text:
        tick.write_text(text.replace(initial_patch, "        _pre_dispatch_housekeeping(_sched, headless)\n"), encoding="utf-8")
    replace_once(tick,
        '        if can_dispatch is not None and not can_dispatch():\n'
        '            _sched.logger.debug("Cron dispatch paused while gateway drains existing work")\n'
        '            return 0\n',
        '        from cron.scheduler_gateway_gate import hold_inprocess_tick\n'
        '        if hold_inprocess_tick(_sched._get_hermes_home(), headless=headless,\n'
        '                               loop=loop, can_dispatch=can_dispatch):\n'
        '            return 0\n')
    gate = core / "cron/scheduler_gateway_gate.py"
    helper = '''

def hold_inprocess_tick(home, *, headless, loop, can_dispatch) -> bool:
    """Do not consume a due occurrence before startup or while draining."""
    if can_dispatch is not None and not can_dispatch():
        logger.debug("Cron dispatch paused while gateway drains existing work")
        return True
    if headless or loop is None:
        return False
    hint = _gateway_unavailable(home)
    if hint is not None:
        logger.info("Cron startup deferred: %s", hint)
        return True
    return False
'''
    text = gate.read_text(encoding="utf-8")
    if helper not in text:
        gate.write_text(text + helper, encoding="utf-8")
    replace_once(core / "cron/scheduler_delivery.py",
        '    job.pop("_bot_chat_delivery_receipts", None)\n',
        '    if not for_failure and job.get("output_validator"):\n'
        '        from cron.output_validation import prepare_output\n'
        '        try:\n'
        '            content = prepare_output(job, content)\n'
        '        except Exception:\n'
        '            logger.exception("Job output validator rejected output")\n'
        '            return "output validator rejected the generated result"\n'
        '    job.pop("_bot_chat_delivery_receipts", None)\n')
    replace_once(core / "cron/scheduler_delivery.py",
        '    return "; ".join(delivery_errors) if delivery_errors else None\n',
        '    if (not delivery_errors and not unverified_targets and not suppressed_targets\n'
        '            and not for_failure and job.get("output_validator")):\n'
        '        from cron.output_validation import commit_output\n'
        '        try:\n'
        '            commit_output(job, content)\n'
        '        except Exception:\n'
        '            # The send succeeded; a dedup bookkeeping failure must not trigger another send.\n'
        '            logger.exception("Delivered output dedup commit failed")\n'
        '    return "; ".join(delivery_errors) if delivery_errors else None\n')
    target = core / "cron/output_validation.py"
    target.write_text((source / "output_validation.py").read_text(encoding="utf-8"), encoding="utf-8")
    return {str(p.relative_to(core)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [tick, gate, core / "cron/scheduler_delivery.py", target, *resilience_paths]}


def apply_resilience(core: Path, source: Path) -> list[Path]:
    """Retain the pinned runtime's reconnect/watchdog logic; close identified gaps."""
    guard = core / "gateway/cloud_resilience.py"
    guard.write_text((source / "runtime_guard.py").read_text(encoding="utf-8"), encoding="utf-8")
    log = core / "hermes_logging.py"
    replace_once(log, "            _log_queue = queue.SimpleQueue()\n",
                 "            from gateway.cloud_resilience import BoundedLogQueue\n"
                 "            _log_queue = BoundedLogQueue()\n")
    bootstrap = core / "gateway/run_bootstrap.py"
    replace_once(bootstrap,
                 "        loop.set_exception_handler(_gateway_loop_exception_handler)\n",
                 "        loop.set_exception_handler(_gateway_loop_exception_handler)\n"
                 "        from gateway.cloud_resilience import install_exception_guard\n"
                 "        install_exception_guard(loop, runner)\n")
    replace_once(bootstrap,
                 "            publish_gateway_runtime_ready(runner)\n",
                 "            from gateway.cloud_resilience import monitor_required_workers\n"
                 "            _worker_guard = asyncio.create_task(\n"
                 "                monitor_required_workers(runner, cron_stop, housekeeping_thread))\n"
                 "            _worker_guard._hermes_supervised_watcher = True\n"
                 "            runner._retain_background_task(_worker_guard)\n"
                 "            publish_gateway_runtime_ready(runner)\n")
    adapters = core / "gateway/run_adapters.py"
    replace_once(adapters,
                 "        return min(60, 2 ** min(attempt, 6))\n",
                 "        from gateway.cloud_resilience import retry_delay\n"
                 "        return retry_delay(attempt)\n")
    replace_once(adapters,
                 '                        logger.debug("on_give_up callback for %s raised", name, exc_info=True)\n'
                 "                return\n",
                 '                        logger.debug("on_give_up callback for %s raised", name, exc_info=True)\n'
                 "                        from gateway.cloud_resilience import request_fatal_shutdown\n"
                 '                        request_fatal_shutdown(self, "watcher_recovery_failed:" + str(name))\n'
                 "                else:\n"
                 "                    from gateway.cloud_resilience import request_fatal_shutdown\n"
                 '                    request_fatal_shutdown(self, "watcher_retry_exhausted:" + str(name))\n'
                 "                return\n")
    watchdog = core / "gateway/shutdown_watchdog.py"
    replace_once(watchdog,
                 "    exit_code: int = GATEWAY_SERVICE_RESTART_EXIT_CODE,\n",
                 "    exit_code: int = 1,\n")
    replace_once(watchdog,
                 '        writer.write(b"1")\n        await writer.drain()\n',
                 '        writer.write(b"1")\n        await asyncio.wait_for(writer.drain(), timeout=2.0)\n')
    replace_once(watchdog,
                 '    with contextlib.suppress(Exception), open(dump_path, "a", encoding="utf-8") as fh:\n',
                 "    with contextlib.suppress(OSError):\n"
                 "        from gateway.cloud_resilience import rotate_diagnostic_log\n"
                 "        rotate_diagnostic_log(dump_path)\n"
                 '    with contextlib.suppress(Exception), open(dump_path, "a", encoding="utf-8") as fh:\n')
    return [guard, log, bootstrap, adapters, watchdog, *apply_llm_resilience(core, source)]


def apply_llm_resilience(core: Path, source: Path) -> list[Path]:
    helper = core / "agent/cloud_llm_resilience.py"
    helper.write_text((source / "llm_resilience.py").read_text(encoding="utf-8"), encoding="utf-8")
    prompt = core / "agent/prompt_builder.py"
    replace_once(prompt, '    if not skills_by_category:\n        return ""\n',
                 '    if not skills_by_category:\n        return ""\n'
                 '    from agent.cloud_llm_resilience import compact_skill_index\n'
                 '    compact = compact_skill_index(skills_by_category, available_tools)\n'
                 '    if compact is not None:\n        return compact\n')
    request = core / "agent/turn_api_request.py"
    replace_once(request, '    agent._reset_stream_delivery_tracking()\n',
                 '    from agent.cloud_llm_resilience import bounded_outbound\n'
                 '    api_messages = bounded_outbound(agent, api_messages)\n'
                 '    agent._reset_stream_delivery_tracking()\n')
    fallback = core / "agent/chat_completion_helpers.py"
    replace_once(fallback, '    if fb_key in unavailable:\n',
                 '    from agent.cloud_llm_resilience import skip_fallback\n'
                 '    if skip_fallback(agent, fb):\n        return True\n'
                 '    if fb_key in unavailable:\n')
    recovery = core / "agent/turn_recovery.py"
    replace_once(recovery, 'or (_is_transport_failure and retry_count >= 2)\n',
                 'or (_is_transport_failure and (getattr(agent, "platform", None) == "telegram" or retry_count >= 2))\n')
    # Do not sleep for minutes on an interactive route. The blocked route is left
    # in cooldown; the next healthy route receives the request instead.
    replace_once(recovery, '    _reset = reset_hint(api_error) if _adaptive else ""\n',
                 '    if getattr(agent, "platform", None) == "telegram":\n'
                 '        wait_time = min(wait_time, 2.0)\n'
                 '    _reset = reset_hint(api_error) if _adaptive else ""\n')
    overflow = core / "agent/turn_overflow.py"
    replace_once(overflow, '    exhausted = st.count_attempt(payload_too_large=True)\n',
                 '    from agent.cloud_llm_resilience import fresh_payload_rejection, PROVIDER_LIMIT_COPY\n'
                 '    if fresh_payload_rejection(st.messages):\n'
                 '        if agent._try_activate_fallback(reason=FailoverReason.payload_too_large):\n'
                 '            from agent.conversation_loop import _arm_fallback_restart\n'
                 '            st.active_system_prompt = _arm_fallback_restart(agent, st.api_messages, st.active_system_prompt, _retry)\n'
                 '            return st.done("break")\n'
                 '        return st.fail_turn(PROVIDER_LIMIT_COPY, compression_exhausted=False,\n'
                 '                            reason="provider_request_limit", retryable=True)\n'
                 '    exhausted = st.count_attempt(payload_too_large=True)\n')
    gateway = core / "gateway/run_turn.py"
    replace_once(gateway, '    if agent_result.get("compression_exhausted"):\n',
                 '    if agent_result.get("failure_reason") == "provider_request_limit":\n'
                 '        return False\n'
                 '    if agent_result.get("compression_exhausted"):\n')
    init = core / "agent/agent_init.py"
    replace_once(init, '    agent._client_kwargs = client_kwargs  # stored for rebuilding after interrupt\n',
                 '    if getattr(agent, "platform", None) == "telegram":\n'
                 '        import httpx\n'
                 '        client_kwargs.update(timeout=httpx.Timeout(20.0, connect=5.0), max_retries=0)\n'
                 '    agent._client_kwargs = client_kwargs  # stored for rebuilding after interrupt\n')
    cache = core / "gateway/run_agent_cache.py"
    replace_once(cache, '        if not persisted:\n            return\n',
                 '        if not persisted:\n            return\n'
                 '        from agent.cloud_llm_resilience import unhealthy_legacy_override\n'
                 '        if unhealthy_legacy_override(persisted):\n'
                 '            logger.warning("Ignoring unavailable legacy model override; using configured route")\n'
                 '            return\n')
    return [helper, prompt, request, fallback, recovery, overflow, gateway, init, cache]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--core", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(apply(args.core, Path(__file__).parent)))

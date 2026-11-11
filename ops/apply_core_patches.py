"""Narrow, version-checked patches. Never replace the existing delivery ledger."""
from pathlib import Path
import argparse
import hashlib
import json


def replace_once(path: Path, original: str, replacement: str) -> None:
    text = path.read_text()
    if replacement in text:
        return
    if text.count(original) != 1:
        raise RuntimeError(f"Patch context changed: {path.name}; refusing a blind replacement")
    path.write_text(text.replace(original, replacement, 1))


def apply(core: Path, source: Path) -> dict:
    delivery_path = core / "cron/scheduler_delivery.py"
    text = delivery_path.read_text()
    text = text.replace('except Exception as exc:\n            logger.error("Job output validator rejected output (%s)", type(exc).__name__)',
                        'except Exception:\n            logger.exception("Job output validator rejected output")')
    text = text.replace('except Exception as exc:\n            # The send succeeded; a dedup bookkeeping failure must not trigger another send.\n            logger.error("Delivered output dedup commit failed (%s)", type(exc).__name__)',
                        'except Exception:\n            # The send succeeded; a dedup bookkeeping failure must not trigger another send.\n            logger.exception("Delivered output dedup commit failed")')
    delivery_path.write_text(text)
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
    text = tick.read_text()
    if initial_patch in text:
        tick.write_text(text.replace(initial_patch, "        _pre_dispatch_housekeeping(_sched, headless)\n"))
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
    text = gate.read_text()
    if helper not in text:
        gate.write_text(text + helper)
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
    target.write_text((source / "output_validation.py").read_text())
    return {str(p.relative_to(core)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [tick, gate, core / "cron/scheduler_delivery.py", target]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--core", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(apply(args.core, Path(__file__).parent)))

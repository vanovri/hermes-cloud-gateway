"""Exercise pinned ticker/dispatch code while a real worker future stays blocked.

Only external admission/store/agent dependencies are stubbed. The ticker methods,
tick dispatch and marker writer come from the supplied core, never a live home.
The clock advances by 721 seconds without waiting for a real research job.
"""
from __future__ import annotations

import ast
import concurrent.futures
import contextlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


def module(name, **values):
    result = ModuleType(name)
    result.__dict__.update(values)
    return result


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def function(path, name, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


def check(core: Path, *, multiplex: bool, no_agent: bool):
    with tempfile.TemporaryDirectory(prefix="hermes-cron-drill-") as directory:
        home = Path(directory)
        cron = home / "cron"
        cron.mkdir()
        started, release = threading.Event(), threading.Event()
        errors, futures, marker_records = [], [], []
        clock = SimpleNamespace(now=1_800_000_000.0)
        timer = SimpleNamespace(time=lambda: clock.now, monotonic=lambda: clock.now)
        job = {"id": "research-evidence-fixture", "name": "Long-running research fixture",
               "no_agent": no_agent, "execution_id": "synthetic-execution"}

        def write_marker(name, text, _prefix):
            (cron / name).write_text(text)
            marker_records.append((name, clock.now))

        heartbeat = function(core / "cron/jobs.py", "record_ticker_heartbeat",
                             {"time": timer, "os": os, "_write_marker": write_marker})

        def run_one_job(claimed, **_kwargs):
            assert claimed["no_agent"] == no_agent
            started.set()
            if not release.wait(10):
                raise TimeoutError("test worker was not released")
            return True

        process = function(core / "cron/scheduler.py", "_process_due_job", {
            "claim_job_for_fire": lambda *_args, **_kwargs: dict(job),
            "run_one_job": run_one_job,
        })
        stop = SimpleNamespace(cycles=0)
        stop.is_set = lambda: stop.cycles >= 2

        def wait(_timeout):
            assert started.wait(2), "job was not submitted to a worker"
            assert not futures[0].done(), "ticker waited for the job to complete"
            stop.cycles += 1
            clock.now += 721

        stop.wait = wait
        logger = logging.getLogger("cron-drill")
        health = SimpleNamespace(dispatch_blocked=lambda _jobs: False)
        schedule = module("cron.scheduler", CronTickYielded=type("CronTickYielded", (Exception,), {}))
        schedule._is_fd_exhaustion = lambda _exc: False
        schedule._should_yield_tick_to_fresh_gateway = lambda: None
        schedule._get_lock_paths = lambda: (cron, cron / ".tick.lock")
        schedule._ensure_cron_dir = lambda _path: None
        schedule._acquire_tick_lock = lambda _path: object()
        schedule._release_tick_lock = lambda _fd: None
        schedule.get_due_jobs = lambda: [job]
        schedule._sweep_stale_inflight_for_tick = lambda _jobs: None
        schedule.advance_next_runs = lambda _ids: None
        schedule._resolve_max_parallel_workers = lambda: 1
        schedule._sweep_mcp_orphans = lambda: None
        schedule._maybe_run_worktree_maintenance = lambda: None
        schedule._sweep_mcp_orphans_when_all_done = lambda _futures: None
        schedule._process_due_job = process
        schedule.logger = logger
        schedule._get_hermes_home = lambda: home
        cron_package = module("cron", scheduler=schedule, store_health=health)
        modules = {
            "cron": cron_package,
            "cron.store_health": module("cron.store_health", dispatch_blocked=health.dispatch_blocked),
            "cron.constants": module("cron.constants", is_recurring=lambda _job: True),
            "cron.scheduler": schedule,
            "cron.jobs": module("cron.jobs", record_ticker_heartbeat=heartbeat,
                                record_ticker_error=lambda error: errors.append(error), clear_ticker_error=lambda: None),
            "cron.scheduler_ownership": module("cron.scheduler_ownership", register_ticked_homes=lambda _homes: None),
            "cron.dispatch_hold": module("cron.dispatch_hold", note_dispatching=lambda: None,
                                         note_held=lambda: None, note_held_profiles=lambda _homes: None),
            "cron.scheduler_preflight": module("cron.scheduler_preflight", SharedRouteAdapters=None,
                                               _primary_profile_routes_for_current_home=lambda: {},
                                               has_credential_platform=lambda *_args: True),
            "cron.scheduler_gateway_gate": module("cron.scheduler_gateway_gate", hold_inprocess_tick=lambda *_a, **_k: False),
            "hermes_constants": module("hermes_constants", get_process_hermes_home=lambda: home),
            "hermes_cli.backend_retirement": module("hermes_cli.backend_retirement",
                                                    retirement=SimpleNamespace(work=lambda: contextlib.nullcontext(True))),
        }
        # _tick_admitted also runs housekeeping before dispatch. Leave that outside
        # this job-duration drill; a blocked admission/store remains a real stall.
        with patch.dict(sys.modules, modules), concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            schedule._get_parallel_pool = lambda _workers: pool

            def submit_once(claimed, worker_pool, callback):
                if futures:
                    return None
                future = worker_pool.submit(callback, claimed)
                futures.append(future)
                return future

            schedule._submit_with_guard = submit_once
            tick_module = load("isolated_scheduler_tick", core / "cron/scheduler_tick.py")
            tick_module._pre_dispatch_housekeeping = lambda *_args: None
            schedule.tick = tick_module.tick
            provider = load("isolated_scheduler_provider", core / "cron/scheduler_provider.py")
            provider.time = timer
            provider._profile_cron_scope = lambda _home: contextlib.nullcontext()
            scheduler = provider.InProcessCronScheduler()
            scheduler.recover_interrupted = lambda: 0
            try:
                scheduler.start(stop, loop=object(), profile_homes=[home] if multiplex else None)
                assert not errors, errors
                assert len(futures) == 1 and not futures[0].done(), "long job did not remain in flight"
                successful = [stamp for name, stamp in marker_records if name == "ticker_last_success"]
                assert len(successful) == 2, marker_records
                assert successful[1] - successful[0] == 721, successful
                assert float((cron / "ticker_last_success").read_text()) == successful[-1]
                stamp, pid = (cron / "ticker_heartbeat").read_text().split()
                assert int(pid) == os.getpid() and float(stamp) == successful[-1]
            finally:
                release.set()
                for future in futures:
                    future.result(timeout=3)
        return {"multiplex": multiplex, "no_agent": no_agent, "simulated_job_seconds": 721,
                "successful_dispatch_ticks": 2, "job_blocked_during_ticks": True}


if __name__ == "__main__":
    core = Path(sys.argv[1]).resolve()
    cases = [check(core, multiplex=False, no_agent=False), check(core, multiplex=True, no_agent=True)]
    print(json.dumps({"cron_progress_drills": cases}, sort_keys=True))

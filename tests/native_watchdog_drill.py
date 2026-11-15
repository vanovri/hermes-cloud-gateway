"""Exercise the patched pinned watchdog in isolated subprocesses, without gateway startup."""
import subprocess
import sys
from pathlib import Path

CHILD = r'''
import asyncio, importlib.util, json, sys, tempfile, time, types
from pathlib import Path
core, mode = Path(sys.argv[1]), sys.argv[2]
sys.path.insert(0, str(core))
temporary = tempfile.TemporaryDirectory()
home = Path(temporary.name)
def stub(name, **values):
    module = types.ModuleType(name)
    module.__dict__.update(values)
    sys.modules[name] = module
stub("gateway.restart", GATEWAY_SERVICE_RESTART_EXIT_CODE=75)
stub("hermes_constants", get_hermes_home=lambda:home, get_process_hermes_home=lambda:home)
stub("utils", atomic_json_write=lambda *a,**kw:None)
stub("gateway.lifecycle_ledger", mark_exited=lambda *a,**kw:None)
stub("gateway.status", write_runtime_status=lambda *a,**kw:None)
stub("hermes_cli.observability.shared_metrics_process", stamp_exit=lambda *a:None)
stub("tools.environments.base", kill_live_foreground_processes=lambda **kw:None)
spec=importlib.util.spec_from_file_location("gateway.shutdown_watchdog", core/"gateway/shutdown_watchdog.py")
watchdog=importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)
if mode=="frozen":
    class FrozenLoop:
        def call_soon_threadsafe(self, callback): pass
    watchdog.start_loop_liveness_watchdog(FrozenLoop(),probe_interval=.01,probe_timeout=.01,max_strikes=2)
    time.sleep(2)
    sys.exit(99)
elif mode=="healthy":
    async def run():
        handle=watchdog.start_loop_liveness_watchdog(asyncio.get_running_loop(),probe_interval=.01,probe_timeout=.05,max_strikes=2)
        await asyncio.sleep(.12)
        handle.stop()
        handle.join(.2)
        assert not handle.is_alive()
    asyncio.run(run())
elif mode=="shutdown_stall":
    watchdog.arm_shutdown_watchdog(.03)
    time.sleep(2)
    sys.exit(99)
elif mode=="shutdown_cancel":
    done=watchdog.arm_shutdown_watchdog(.03)
    done.set()
    time.sleep(.08)
temporary.cleanup()
'''

core = Path(sys.argv[1])
for mode, expected in [("frozen", 1), ("healthy", 0), ("shutdown_stall", 1), ("shutdown_cancel", 0)]:
    process = subprocess.run([sys.executable, "-c", CHILD, str(core), mode],
                             capture_output=True, text=True, timeout=5)
    if process.returncode != expected:
        raise AssertionError((mode, process.returncode, expected, process.stderr[-2000:]))
    print(f"PASS {mode}: exit={process.returncode}")

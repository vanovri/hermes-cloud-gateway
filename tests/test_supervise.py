"""Linux fault drills exercise real process termination, not production state."""
import json
import os
from pathlib import Path
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

OPS = Path(__file__).resolve().parents[1] / "ops"
sys.path.insert(0, str(OPS))
if sys.platform == "linux":
    import supervise


@unittest.skipUnless(sys.platform == "linux", "Production supervisor uses Linux process groups")
class SupervisorTests(unittest.TestCase):
    def run_drill(self, mode, *, terminate=False):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            # Each child creates a detached grandchild; the wrapper must reap it.
            child = home / "child.py"
            child.write_text('''import os, pathlib, signal, subprocess, sys, time
name = sys.argv[1]
pathlib.Path(os.environ['HERMES_HOME'], name + '.main').write_text(str(os.getpid()))
if name == 'gateway' and sys.argv[2] == 'crash':
    sys.exit(7)
p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], start_new_session=True)
pathlib.Path(os.environ['HERMES_HOME'], name + '.pid').write_text(str(p.pid))
def stop(*_):
    if name == 'gateway':
        root = pathlib.Path(os.environ['HERMES_HOME'])
        try:
            os.kill(int((root/'router.main').read_text()), 0)
            (root/'drained').write_text('router_alive')
        except ProcessLookupError:
            (root/'drained').write_text('router_dead')
        time.sleep(0.1)
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
print('X' * 50000, flush=True)
while True: time.sleep(0.05)
''')
            harness = home / "drill.py"
            harness.write_text('''import pathlib, sys
sys.path.insert(0, sys.argv[1])
import supervise
home, mode = pathlib.Path(sys.argv[2]), sys.argv[3]
supervise.router_command = lambda home: ([], {})
class Drill(supervise.Supervisor):
    calls = 0
    def start(self, name, command, **kwargs):
        if name in ('router', 'gateway'):
            command = [sys.executable, str(home/'child.py'), name, mode]
        elif name == 'probe':
            self.calls += 1
            code = 'import time; time.sleep(120)' if mode == 'freeze' and self.calls > 2 else 'pass'
            command = [sys.executable, '-c', code]
        return super().start(name, command, **kwargs)
s = Drill(home, max_runtime=1.5 if mode == 'cycle' else 0, startup_timeout=2,
          probe_interval=0.1, probe_timeout=0.15, failure_limit=2, shutdown_timeout=0.2)
try:
    result = s.run()
finally:
    s.cleanup()
sys.exit(result)
''')
            env = os.environ.copy()
            for name in ("GITHUB_SHA", "VERIFY_DELIVERY", "NOTIFY_SOCKET"):
                env.pop(name, None)
            process = subprocess.Popen([sys.executable, str(harness), str(OPS), folder, mode],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            try:
                if terminate:
                    deadline = time.monotonic() + 5
                    while not (home / "gateway.pid").exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue((home / "gateway.pid").exists())
                    process.send_signal(signal.SIGTERM)
                output, error = process.communicate(timeout=8)
                for path in home.glob("*.pid"):
                    pid = int(path.read_text())
                    deadline = time.monotonic() + 2
                    while Path(f"/proc/{pid}").exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertFalse(Path(f"/proc/{pid}").exists(), f"orphan PID {pid}")
                self.assertEqual(error, "")
                if terminate:
                    self.assertEqual((home / "drained").read_text(), "router_alive")
                return process.returncode, [json.loads(line)["event"] for line in output.splitlines()]
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)

    def test_frozen_probe_forces_nonzero_exit_and_cleans_detached_children(self):
        code, events = self.run_drill("freeze")
        self.assertEqual(code, 1)
        self.assertIn("fatal_health_stall", events)

    def test_unexpected_gateway_exit_fails_cycle(self):
        code, events = self.run_drill("crash")
        self.assertEqual(code, 1)
        self.assertIn("critical_process_exit", events)

    def test_sigterm_drains_and_reaps_tree(self):
        code, events = self.run_drill("healthy", terminate=True)
        self.assertEqual(code, 0)
        self.assertIn("shutdown_requested", events)

    def test_planned_cycle_exit_is_success(self):
        code, events = self.run_drill("cycle")
        self.assertEqual(code, 0)
        self.assertIn("cycle_complete", events)

    def test_log_rotation_stays_bounded_without_newlines(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(supervise, "LOG_BYTES", 16384):
            home = Path(folder)
            (home / "logs").mkdir()
            supervisor = supervise.Supervisor(home, shutdown_timeout=0.1)
            try:
                process = supervisor.start("noise", [sys.executable, "-c", "import sys; sys.stdout.write('x' * 300000)"])
                deadline = time.monotonic() + 5
                while supervisor.selector.get_map() and time.monotonic() < deadline:
                    supervisor.drain()
                self.assertEqual(process.wait(timeout=2), 0)
                logs = list((home / "logs").glob("supervisor-noise.log*"))
                self.assertLessEqual(len(logs), 6)
                self.assertLessEqual(sum(p.stat().st_size for p in logs), 6 * (16384 + 8192))
            finally:
                supervisor.cleanup()

    def test_systemd_receives_watchdog_during_drain(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "notify.sock")
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as listener:
                listener.bind(path)
                listener.settimeout(1)
                with patch.dict(os.environ, {"NOTIFY_SOCKET": path}):
                    supervisor = supervise.Supervisor(Path(folder))
                    try:
                        supervisor.drain(timeout=0)
                        self.assertEqual(listener.recv(512), b"WATCHDOG=1")
                    finally:
                        supervisor.cleanup()

    def test_router_is_foreground_and_memory_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder)
            (package / "app").mkdir()
            (package / "app/custom-server.js").touch()
            (package / "cli.js").touch()
            with patch("supervise.shutil.which", return_value=str(package / "cli.js")), patch.dict(os.environ):
                command, environment = supervise.router_command(package / ".hermes")
                self.assertEqual(command[:2], ["node", "--max-old-space-size=1536"])
                self.assertEqual(environment["ENABLE_REQUEST_LOGS"], "false")
                self.assertEqual(environment["HOSTNAME"], "127.0.0.1")

    @unittest.skipUnless(shutil.which("node"), "Node runtime unavailable")
    def test_router_exits_despite_log_only_exception_handler(self):
        for failure in ("throw new Error('secret-must-not-appear')",
                        "Promise.reject(new Error('secret-must-not-appear'))"):
            result = subprocess.run(["node", "--unhandled-rejections=strict", "--require",
                                     str(OPS / "router_guard.cjs"), "-e",
                                     "process.on('uncaughtException', () => {}); " + failure],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn("secret-must-not-appear", result.stderr)
            self.assertEqual(json.loads(result.stderr)["event"], "router_fatal")


if __name__ == "__main__":
    unittest.main()

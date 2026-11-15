"""Own the gateway process tree and fail closed on loss of real loop progress.

Linux only. Both Actions and systemd use this same lifecycle. Probes run in
separate process groups so even a wedged DNS resolver cannot stop supervision.
"""
from __future__ import annotations

import argparse
import ctypes
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import selectors
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
LOG_BYTES = 20 * 1024 * 1024


def event(name, **fields):
    print(json.dumps({"event": name, "time": time.time(), **fields}, sort_keys=True), flush=True)


def notify(message):
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.2)
            sock.sendto(message.encode(), address)
    except OSError:
        pass


def router_ready():
    # Ignore ambient proxy settings for the private loopback sidecar.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:20128/v1/models", timeout=2) as response:
            return response.status == 200
    except urllib.error.HTTPError as exc:
        exc.close()
        return exc.code == 401
    except (OSError, ValueError):
        return False


def check(home, expected_pid=None):
    from live_control import readiness
    from runtime_probe import probe
    witness = probe(home, timeout=2, expected_pid=expected_pid, require_cron=True)
    identity_matches = expected_pid is None or witness.get("pid") == expected_pid
    control = readiness(home)
    router = router_ready()
    return {"ready": bool(witness.get("ok") and identity_matches and control["ready"] and router),
            "progress": witness, "control_state": control.get("control_state"),
            "telegram_adapter_state": control.get("telegram_adapter_state"), "router_ready": router}


def signal_group(process, signum):
    # The group can still contain descendants after its leader has exited.
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass


def descendants():
    """Include detached sessions adopted by this Linux subreaper."""
    records = {}
    for item in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = item.read_text().rpartition(")")[2].split()
            records[int(item.parent.name)] = (int(fields[1]), fields[19])
        except (OSError, ValueError, IndexError):
            continue
    owned = {os.getpid()}
    while True:
        added = {pid for pid, (parent, _) in records.items() if parent in owned} - owned
        if not added:
            break
        owned.update(added)
    return {pid: records[pid][1] for pid in owned if pid != os.getpid()}


def signal_descendants(records, signum):
    for pid, birth in records.items():
        try:
            current = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[19]
            if birth == current:  # Do not signal an unrelated recycled PID.
                os.kill(pid, signum)
        except (OSError, IndexError):
            pass


def router_command(home):
    executable = shutil.which("9router")
    if not executable:
        raise RuntimeError("9router is not installed")
    package = Path(executable).resolve().parent
    entrypoint = package / "app/custom-server.js"
    if not entrypoint.is_file():
        raise RuntimeError("Pinned 9router foreground entrypoint is missing")
    # The CLI detaches its server. Run the same custom server in our process
    # group, preserving its peer-IP handling and automatic token refresh.
    node_path = [str(home.parent / ".9router/runtime/node_modules"), str(package / "app/node_modules")]
    if os.environ.get("NODE_PATH"):
        node_path.append(os.environ["NODE_PATH"])
    environment = dict(HOSTNAME="127.0.0.1", PORT="20128", DATA_DIR=str(home.parent / ".9router"),
                       NODE_ENV="production", ENABLE_REQUEST_LOGS="false", LOG_LEVEL="INFO",
                       NODE_PATH=os.pathsep.join(node_path))
    return ["node", "--max-old-space-size=1536", "--dns-result-order=ipv4first", "--unhandled-rejections=strict",
            "--require", str(HERE / "router_guard.cjs"), str(entrypoint)], environment


class Supervisor:
    def __init__(self, home, *, max_runtime=0, startup_timeout=180, probe_interval=15,
                 probe_timeout=10, failure_limit=5, shutdown_timeout=60):
        self.home = home
        self.max_runtime = max_runtime
        self.startup_timeout = startup_timeout
        self.probe_interval = probe_interval
        self.probe_timeout = probe_timeout
        self.failure_limit = failure_limit
        self.shutdown_timeout = shutdown_timeout
        self.selector = selectors.DefaultSelector()
        self.children = {}
        self.handlers = {}
        self.stop_requested = False
        self.lock = None
        self.next_watchdog = 0
        self.probe_output = bytearray()

    def start(self, name, command, *, capture=True, environment=None):
        # Start a session to kill the entire tree, including subprocesses that
        # inherit pipes after a parent crash. Never interpolate a shell command.
        env = os.environ.copy()
        env.pop("NOTIFY_SOCKET", None)
        env["HERMES_HOME"] = str(self.home)
        env.update(environment or {})
        process = subprocess.Popen(command, stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                   stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                   start_new_session=True, env=env)
        self.children[name] = process
        if capture:
            if name == "probe":
                self.probe_output = bytearray()
            else:
                handler = RotatingFileHandler(self.home / "logs" / f"supervisor-{name}.log",
                                              maxBytes=LOG_BYTES, backupCount=5, encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(message)s"))
                self.handlers[name] = handler
            os.set_blocking(process.stdout.fileno(), False)
            self.selector.register(process.stdout, selectors.EVENT_READ, name)
        return process

    def drain(self, timeout=0.2):
        if time.monotonic() >= self.next_watchdog:
            notify("WATCHDOG=1")
            self.next_watchdog = time.monotonic() + 10
        for key, _ in self.selector.select(timeout):
            block = os.read(key.fileobj.fileno(), 8192)
            if not block:
                self.selector.unregister(key.fileobj)
                key.fileobj.close()
                continue
            # Fixed-size reads avoid accumulating an unbounded partial log line.
            if key.data == "probe":
                self.probe_output.extend(block[:max(0, 8192 - len(self.probe_output))])
                continue
            self.handlers[key.data].emit(logging.LogRecord(key.data, logging.INFO, "", 0,
                                                          block.decode("utf-8", "replace").rstrip(), (), None))

    def stop_child(self, name):
        process = self.children.pop(name, None)
        if process:
            signal_group(process, signal.SIGKILL)
            process.wait(timeout=5)
            if process.stdout and not process.stdout.closed:
                self.drain(timeout=0)
                if not process.stdout.closed:
                    self.selector.unregister(process.stdout)
                    process.stdout.close()

    def cleanup(self):
        notify("STOPPING=1")
        processes = list(self.children.values())
        detached = descendants()
        router = self.children.get("router")
        gateway = self.children.get("gateway")
        for process in processes:
            if process is not router:
                signal_group(process, signal.SIGTERM)
        # Let the gateway drain before terminating any of its detached tools.
        deadline = time.monotonic() + self.shutdown_timeout
        while gateway and gateway.poll() is None and time.monotonic() < deadline:
            self.drain()
        if router:
            signal_group(router, signal.SIGTERM)
        router_deadline = time.monotonic() + 5
        while router and router.poll() is None and time.monotonic() < router_deadline:
            self.drain()
        for process in processes:
            signal_group(process, signal.SIGKILL)
        detached.update(descendants())
        signal_descendants(detached, signal.SIGKILL)
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                event("child_reap_timeout", pid=process.pid)
        for key in list(self.selector.get_map().values()):
            self.selector.unregister(key.fileobj)
            key.fileobj.close()
        self.selector.close()
        for handler in self.handlers.values():
            handler.close()
        # Reap orphan descendants adopted through PR_SET_CHILD_SUBREAPER.
        try:
            while os.waitpid(-1, os.WNOHANG)[0]:
                pass
        except ChildProcessError:
            pass
        if self.lock:
            self.lock.close()

    def run(self):
        os.umask(0o077)
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "logs").mkdir(exist_ok=True)
        self.lock = (self.home / "supervisor.lock").open("a")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if sys.platform == "linux":
            ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0)
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: setattr(self, "stop_requested", True))
        started = time.monotonic()
        stage_started = started
        stage = "router"
        next_probe = 0
        probe_started = 0
        failures = 0
        ready = False
        next_receipt = started
        receipt_started = 0
        smoke_started = 0
        smoke_ran = False
        last_health = {}
        command, environment = router_command(self.home)
        self.start("router", command, environment=environment)
        while not self.stop_requested:
            self.drain()
            now = time.monotonic()
            if self.max_runtime and now - started >= self.max_runtime:
                event("cycle_complete")
                return 0
            for name in ("router", "gateway"):
                child = self.children.get(name)
                if child and child.poll() is not None:
                    event("critical_process_exit", process=name, exit_code=child.returncode)
                    return 1
            if not ready and now - stage_started >= self.startup_timeout:
                event("startup_timeout", stage=stage)
                return 1
            active_probe = self.children.get("probe")
            if active_probe and (active_probe.poll() is not None or now - probe_started >= self.probe_timeout):
                healthy = active_probe.poll() == 0
                self.stop_child("probe")
                try:
                    last_health = json.loads(self.probe_output)
                except (ValueError, UnicodeError):
                    last_health = {"reason": "probe_failed_or_timed_out"}
                if healthy and stage == "router":
                    self.start("gateway", ["hermes", "gateway", "run"])
                    stage = "gateway"
                    stage_started = now
                    next_probe = now
                elif stage == "gateway":
                    failures = 0 if healthy else failures + 1
                    if healthy and not ready:
                        ready = True
                        notify("READY=1")
                        event("gateway_ready")
                    if ready and failures >= self.failure_limit:
                        event("fatal_health_stall", consecutive_failures=failures, health=last_health)
                        return 1
            if "probe" not in self.children and now >= next_probe:
                command = [sys.executable, str(HERE / "supervise.py"), "--home", str(self.home)]
                command += ["--router-check"] if stage == "router" else ["--check", "--expected-pid", str(self.children["gateway"].pid)]
                self.start("probe", command)
                probe_started = now
                next_probe = now + self.probe_interval
            receipt = self.children.get("receipt")
            if receipt and (receipt.poll() is not None or now - receipt_started >= 40):
                if receipt.poll() != 0:
                    event("readiness_receipt_failed")
                self.stop_child("receipt")
            if ready and now >= next_receipt:
                event("supervisor_heartbeat", healthy=failures == 0, failures=failures)
                next_receipt = now + 180
                if os.environ.get("GITHUB_SHA") and "receipt" not in self.children:
                    self.start("receipt", [sys.executable, str(HERE / "publish_readiness.py"), "--home",
                                            str(self.home), "--sha", os.environ["GITHUB_SHA"]], capture=False)
                    receipt_started = now
            smoke = self.children.get("smoke")
            if smoke and (smoke.poll() is not None or now - smoke_started >= 180):
                event("functional_smoke_complete", exit_code=smoke.poll())
                self.stop_child("smoke")
            if ready and not smoke_ran and os.environ.get("VERIFY_DELIVERY") == "true":
                self.start("smoke", [sys.executable, str(HERE / "functional_smoke.py")])
                smoke_started = now
                smoke_ran = True
        event("shutdown_requested")
        return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=Path.home() / ".hermes")
    parser.add_argument("--max-runtime", type=int, default=0, help="Seconds, 0 means persistent host")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--router-check", action="store_true")
    parser.add_argument("--expected-pid", type=int)
    args = parser.parse_args()
    if args.router_check:
        return 0 if router_ready() else 1
    if args.check:
        result = check(args.home, args.expected_pid)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["ready"] else 1
    if args.max_runtime < 0:
        parser.error("--max-runtime cannot be negative")
    supervisor = Supervisor(args.home, max_runtime=args.max_runtime)
    try:
        return supervisor.run()
    except Exception as exc:
        frames = [{"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                  for frame in traceback.extract_tb(exc.__traceback__)[-6:]]
        event("supervisor_fatal", error_type=type(exc).__name__, frames=frames)
        return 1
    finally:
        supervisor.cleanup()


if __name__ == "__main__":
    sys.exit(main())

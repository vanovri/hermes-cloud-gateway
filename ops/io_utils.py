"""Bounded reads and durable local writes used by operational probes."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import random
import signal
import sqlite3
import subprocess
import tempfile
import time


def run_process(args, *, timeout, input=None, text=False, check=False, cwd=None, env=None):
    """A timed-out CLI must not leave its own subprocesses holding pipe handles."""
    with subprocess.Popen(args, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env,
                          text=text, start_new_session=os.name == "posix") as process:
        try:
            stdout, stderr = process.communicate(input, timeout=timeout)
        except BaseException:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait(timeout=5)
            raise
        result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result


def retry_delay(attempt, *, base=1.0, cap=15.0):
    return random.uniform(0, min(cap, base * 2 ** attempt))


def atomic_text(path: Path, value: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path: Path, value):
    atomic_text(path, json.dumps(value, sort_keys=True) + "\n")


@contextmanager
def readonly_database(path: Path, *, timeout=10.0):
    deadline = time.monotonic() + timeout
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=min(timeout, 5))
    connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    try:
        yield connection
    finally:
        connection.close()


def response_json(response, *, timeout=10.0, max_bytes=1_048_576):
    """Use with an HTTP response opened with a finite socket timeout.

    read1 returns after one socket read, so a drip-fed body cannot renew the
    whole-response budget. A read already in flight may take one socket timeout.
    """
    deadline = time.monotonic() + timeout
    read = getattr(response, "read1", response.read)
    chunks, length = [], 0
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError("HTTP response deadline exceeded")
        chunk = read(min(65536, max_bytes + 1 - length))
        if not chunk:
            return json.loads(b"".join(chunks))
        length += len(chunk)
        if length > max_bytes:
            raise ValueError("HTTP response exceeds size limit")
        chunks.append(chunk)

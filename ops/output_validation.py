"""Optional profile-local cron result validation, before any delivery side effect."""
from __future__ import annotations

import hashlib
import subprocess
import sys

from hermes_constants import get_hermes_home


def _run(job: dict, content: str, phase: str) -> str:
    from tools.environments.local import build_subprocess_env

    home = get_hermes_home()
    root = (home / "scripts").resolve()
    script = (root / str(job["output_validator"])).resolve()
    if not script.is_relative_to(root) or script.suffix != ".py" or not script.is_file():
        raise ValueError("Output validator must be a Python file inside profile scripts")
    environment = build_subprocess_env(strip_launch_profile=True)
    environment["HERMES_HOME"] = str(home)
    process = subprocess.run([sys.executable, str(script), phase], input=content,
                             text=True, capture_output=True, timeout=75, check=False,
                             env=environment)
    if process.returncode or not process.stdout.strip():
        raise ValueError("Output validator failed closed")
    return process.stdout.rstrip()


def prepare_output(job: dict, content: str) -> str:
    digest = hashlib.sha256(content.encode()).hexdigest()
    if job.get("_validated_output_sha256") == digest:
        return content
    output = _run(job, content, "prepare")
    job["_validated_output_sha256"] = hashlib.sha256(output.encode()).hexdigest()
    return output


def commit_output(job: dict, content: str) -> None:
    _run(job, content, "commit")

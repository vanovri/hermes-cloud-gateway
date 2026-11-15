"""Check patch compatibility against the installed pinned core without starting Hermes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import py_compile
import subprocess
import sys

from apply_core_patches import apply

PINNED_CORE_REVISION = "2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19"


def verify(core: Path) -> dict:
    source = Path(__file__).resolve().parent
    if (core / ".git").exists():
        revision = subprocess.run(["git", "-C", str(core), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        if revision != PINNED_CORE_REVISION:
            raise RuntimeError("Installed core revision does not match the tested deployment pin")
    first = apply(core, source)
    second = apply(core, source)
    if first != second:
        raise RuntimeError("Core patches are not idempotent")
    for relative in first:
        py_compile.compile(str(core / relative), doraise=True)
    subprocess.run([sys.executable, str(source.parent / "tests/native_watchdog_drill.py"), str(core)],
                   check=True, timeout=30)
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests",
                    "-p", "test_core_resilience.py", "-v"],
                   cwd=source.parent, check=True, timeout=30)
    return {"patch_idempotence": True, "compiled_modules": len(first), "watchdog_drills": 4,
            "core_revision": PINNED_CORE_REVISION if (core / ".git").exists() else "archive_context_checked"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--core", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.core.resolve()), sort_keys=True))

"""Snapshot persistent Hermes state using SQLite's online backup API."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tarfile
import tempfile
from pathlib import Path

HERMES_PATHS = (".env", ".copilot_jwt.json", "auth.json", "config.yaml", "SOUL.md", "bin",
                "cron", "memories", "skills", "scripts", "plugins", "plugin-data", "profiles",
                "state", "state.db", "kanban.db", "shared-state.db", "projects.db",
                "channel_directory.json", "pending_messages", "platforms", "pairing")
ROUTER_PATHS = ("db/data.sqlite", "jwt-secret", "machine-id", "auth", "model-catalog.json", "model-catalog-raw.json")
CUA_PATHS = ("credentials.json", "device-key", "session.json")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "cache", "logs",
             "admissions", "external-workers", "audio_cache", "image_cache", "runtime"}


def persistent_file(path: Path) -> bool:
    return not (path.name.endswith(("-wal", "-shm", ".lock", ".pid", ".sock", ".tmp"))
                or path.name.startswith(".tick") or path.name == "catch_up_occurrences")


def sqlite_backup(source: Path, target: Path) -> None:
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=15) as original:
        with sqlite3.connect(target) as snapshot:
            original.backup(snapshot, pages=256, sleep=0.1)
            result = snapshot.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError(f"SQLite integrity check failed: {source.name}")
    target.chmod(0o600)


def create_bundle(home: Path, destination: Path) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    for required in (".hermes/.env", ".hermes/state.db", ".hermes/config.yaml", ".hermes/cron/jobs.json"):
        if not (home / required).is_file() or not (home / required).stat().st_size:
            raise RuntimeError(f"Required state missing: {required}")
    roots = [(home / ".hermes" / p, Path(".hermes") / p) for p in HERMES_PATHS]
    roots += [(home / ".9router" / p, Path(".9router") / p) for p in ROUTER_PATHS]
    roots += [(home / ".cua" / p, Path(".cua") / p) for p in CUA_PATHS]
    # Native polling receipt filenames include the bot's identifier; preserve them without logging names.
    roots += [(p, Path(".hermes") / p.name) for p in (home / ".hermes").glob("telegram*receipts*.json")]
    entries = {}
    for root, arcroot in roots:
        if not root.exists():
            continue
        if root.is_symlink():
            entries[str(arcroot)] = root
        elif root.is_dir():
            for current, directories, names in os.walk(root, followlinks=False):
                for name in list(directories):
                    p = Path(current) / name
                    if name in SKIP_DIRS:
                        directories.remove(name)
                    elif p.is_symlink():
                        entries[str(arcroot / p.relative_to(root))] = p
                        directories.remove(name)
                for name in names:
                    p = Path(current) / name
                    if persistent_file(p) and p.is_file():
                        entries[str(arcroot / p.relative_to(root))] = p
        elif persistent_file(root):
            entries[str(arcroot)] = root
    manifest = {"format": 1, "files": {}, "sqlite_count": 0}
    with tempfile.TemporaryDirectory(prefix="hermes-state-", dir=destination.parent) as td:
        temporary = Path(td) / "bundle.tar.gz"
        with tarfile.open(temporary, "w:gz", dereference=False) as archive:
            for arcname, source in sorted(entries.items()):
                if source.is_symlink():
                    link = os.readlink(source)
                    resolved = source.resolve()
                    if not resolved.is_relative_to(home.resolve()):
                        raise RuntimeError("Backup symlink leaves the user home")
                    info = archive.gettarinfo(source, arcname=arcname)
                    info.linkname = os.path.relpath(resolved, (home / arcname).parent)
                    archive.addfile(info)
                    manifest["files"][arcname] = {"symlink": info.linkname}
                    continue
                actual = source
                with source.open("rb") as handle:
                    is_sqlite = handle.read(16) == b"SQLite format 3\0"
                if is_sqlite:
                    actual = Path(td) / (str(manifest["sqlite_count"]) + ".sqlite")
                    sqlite_backup(source, actual)
                    manifest["sqlite_count"] += 1
                archive.add(actual, arcname=arcname, recursive=False)
                manifest["files"][arcname] = {"sha256": hashlib.sha256(actual.read_bytes()).hexdigest(), "bytes": actual.stat().st_size}
            raw = json.dumps(manifest, sort_keys=True).encode()
            import io
            info = tarfile.TarInfo("hermes-backup-manifest.json")
            info.size = len(raw)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(raw))
        with tarfile.open(temporary) as check:
            if len(check.getnames()) != len(entries) + 1:
                raise RuntimeError("Bundle member validation failed")
            for arcname, metadata in manifest["files"].items():
                if "sha256" in metadata:
                    handle = check.extractfile(arcname)
                    if handle is None or hashlib.sha256(handle.read()).hexdigest() != metadata["sha256"]:
                        raise RuntimeError("Bundle content hash validation failed")
        temporary.chmod(0o600)
        os.replace(temporary, destination)
    return {"persistent_entries": len(entries), "sqlite_snapshots": manifest["sqlite_count"], "archive_bytes": destination.stat().st_size}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(create_bundle(args.home, args.output)))

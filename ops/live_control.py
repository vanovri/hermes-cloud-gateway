"""Read the documented local JSON control socket without invoking inference."""
import json
import os
import socket
import stat
from pathlib import Path


def query(home: Path, verb: str) -> dict:
    path = home / "gateway.sock"
    pointer = home / "gateway.sock.path"
    try:
        if pointer.exists():
            info = pointer.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                return {}
            path = Path(pointer.read_text().strip())
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            return {}
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(2)
            connection.connect(str(path))
            connection.sendall(json.dumps({"verb": verb, "id": 1, "protocol": 1}).encode() + b"\n")
            raw = bytearray()
            while b"\n" not in raw and len(raw) <= 524288:
                block = connection.recv(65536)
                if not block:
                    break
                raw.extend(block)
        if len(raw) > 524288:
            return {}
        response = json.loads(raw)
        if not isinstance(response, dict) or response.get("ok") is not True:
            return {}
        result = response.get("result")
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def readiness(home: Path) -> dict:
    identity = query(home, "identify")
    status = query(home, "status")
    platforms = status.get("platforms") or {}
    telegram = platforms.get("telegram") or platforms.get("default:telegram") or {}
    state = identity.get("state", "unavailable")
    adapter = telegram.get("state", "unavailable")
    return {"control_state": state, "telegram_adapter_state": adapter,
            "runtime_protocol": identity.get("runtime_protocol"), "code_sha": identity.get("code_sha"),
            "ready": state == "ready" and adapter == "connected" and identity.get("runtime_protocol") == 1}

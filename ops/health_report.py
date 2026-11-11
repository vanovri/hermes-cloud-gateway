"""Report process, adapter and ledger health without sending messages or calling a model."""
import argparse
import hashlib
import json
import sqlite3
import urllib.request
from pathlib import Path


def report(home: Path) -> dict:
    data = {"model_inference_tested": False, "message_sent": False}
    state_path = home / "gateway_state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
        data["runtime"] = {k: state[k] for k in ("state", "status", "code_sha", "code_version", "started_at") if k in state}
    path = home / "state.db"
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "delivery_obligations" in tables:
            data["delivery_obligations"] = dict(connection.execute("SELECT state, COUNT(*) FROM delivery_obligations GROUP BY state"))
    # The fingerprint names a bot identity, never the token or target chat.
    env = {}
    for line in (home / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip("\"'")
    token = env.get("TELEGRAM_BOT_TOKEN")
    if token:
        try:
            with urllib.request.urlopen("https://api.telegram.org/bot" + token + "/getMe", timeout=8) as response:
                identity = json.load(response)
            data["telegram_identity"] = {"ok": identity.get("ok") is True,
                "fingerprint": hashlib.sha256(str(identity.get("result", {}).get("id", "")).encode()).hexdigest()[:12]}
        except Exception as exc:
            data["telegram_identity"] = {"ok": False, "error_type": type(exc).__name__}
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(report(args.home), sort_keys=True))

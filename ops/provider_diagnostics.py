"""Opt-in live inference on an isolated restore. Never starts Telegram polling."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import struct
import subprocess
import time
import urllib.error
import urllib.request
import zlib

from io_utils import atomic_json, run_process
from supervise import router_command


def credentials(home):
    result = {}
    for line in (home / ".hermes/.env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip("\"'")
    return result


def request(url, headers, payload=None, secrets=()):
    started = time.monotonic()
    try:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers})
        with urllib.request.urlopen(req, timeout=15) as response:
            body = json.loads(response.read(2_000_000))
        return {"http": 200, "seconds": round(time.monotonic() - started, 3), "body": body}
    except urllib.error.HTTPError as exc:
        with exc:
            text = exc.read(2048).decode("utf-8", "replace")
        for secret in secrets:
            if secret:
                text = text.replace(secret, "[redacted]")
        return {"http": exc.code, "seconds": round(time.monotonic() - started, 3), "error": text[:500]}
    except Exception as exc:
        return {"http": None, "seconds": round(time.monotonic() - started, 3), "error_type": type(exc).__name__}


def red_image():
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xffffffff)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress((b"\0" + b"\xff\0\0" * 16) * 16)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


def inference(route, env, vision):
    provider, model, url, key = route
    content = ([{"type": "text", "text": "Name the single color in this image. One word."},
                {"type": "image_url", "image_url": {"url": red_image()}}] if vision else "Reply with exactly HERMES_TEXT_OK")
    result = request(url + "/chat/completions", {"Authorization": "Bearer " + key},
                     {"model": model, "messages": [{"role": "user", "content": content}], "max_tokens": 512,
                      "stream": False}, env.values())
    body = result.pop("body", {})
    text = str((body.get("choices") or [{}])[0].get("message", {}).get("content") or "")
    result.update(provider=provider, model=model, vision=vision, content=text[:80],
                  success=("red" in text.lower() if vision else "HERMES_TEXT_OK" in text))
    return result


def measure(home, core, output):
    shutil.copyfile(Path(__file__).with_name("payload_probe.py"), core / "cloud_payload_probe.py")
    env = {**os.environ, "HERMES_HOME": str(home / ".hermes"), "PAYLOAD_PROBE_OUTPUT": str(output)}
    try:
        run_process(["hermes", "--run-module", "cloud_payload_probe"], env=env, timeout=90, check=True)
        return json.loads(output.read_text())
    except Exception as exc:
        return {"error_type": type(exc).__name__}


def diagnose(home, core, output):
    import yaml
    env = credentials(home)
    config = yaml.safe_load((home / ".hermes/config.yaml").read_text()) or {}
    result = {"observed_at": datetime.now(timezone.utc).isoformat(), "model": config.get("model"),
              "fallbacks": [{k: v for k, v in entry.items() if k in {"model", "provider"}}
                            for entry in config.get("fallback_providers", [])],
              "credential_names": [name for name in env if name.endswith(("_KEY", "_TOKEN"))],
              "skills_count": len(list((home / ".hermes/skills").rglob("SKILL.md")))}
    # Only publish routing choices, never key-bearing extra model settings.
    result["model"] = {k: v for k, v in (result["model"] or {}).items() if k in {"default", "provider", "context_length", "max_tokens"}}
    result["router_accounts"] = []
    db = home / ".9router/db/data.sqlite"
    if db.exists():
        from contextlib import closing
        with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=2)) as connection:
            for provider, active, data in connection.execute("SELECT provider,isActive,data FROM providerConnections"):
                data = json.loads(data)
                result["router_accounts"].append({"provider": provider, "active": bool(active),
                    "expires_at": data.get("expiresAt"), "refresh_present": bool(data.get("refreshToken")),
                    "error_code": data.get("errorCode"), "backoff_level": data.get("backoffLevel")})
    result["payload_before"] = measure(home, core, output.with_suffix(".payload-before"))
    atomic_json(output, result)
    command, extra = router_command(home / ".hermes")
    extra["PORT"] = "20129"
    process = subprocess.Popen(command, env={**os.environ, **extra}, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        for _ in range(60):
            if request("http://127.0.0.1:20129/v1/models", {}).get("http") in {200, 401}:
                break
            if process.poll() is not None:
                break
            time.sleep(1)
        routes = [("custom", model, "http://127.0.0.1:20129/v1", env.get("NINEROUTER_API_KEY", ""))
                  for model in ("grok-auto", "ag/gemini-3.8-flash", "ag/gemini-3-flash")]
        if env.get("GOOGLE_API_KEY"):
            routes += [("google", model, "https://generativelanguage.googleapis.com/v1beta/openai", env["GOOGLE_API_KEY"])
                       for model in ("gemini-3.8-flash", "gemini-3.5-flash-lite")]
        if env.get("OPENROUTER_API_KEY"):
            routes += [("openrouter", model, "https://openrouter.ai/api/v1", env["OPENROUTER_API_KEY"])
                       for model in ("particle-ai/particle-1:free", "inclusionai/ling-3.2-flash:free", "nvidia/nemotron-3.5-super-vl:free")]
        if env.get("GROQ_API_KEY"):
            routes.append(("groq", "openai/gpt-oss-120b", "https://api.groq.com/openai/v1", env["GROQ_API_KEY"]))
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(inference, route, env, vision) for route in routes for vision in (False, True)]
            result["probes"] = [future.result() for future in futures]
        atomic_json(output, result)
    finally:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    diagnose(args.home, args.core, args.output)

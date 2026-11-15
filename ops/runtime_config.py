"""Apply host-owned operational settings without changing credentials or memories."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

import yaml
from io_utils import atomic_text

RESEARCH_JOBS = {"ee5cdd1455a1", "decb1f7e8447", "357fd2ff763c", "6f17c39ca284", "2a8e765b6611"}
STARTUP_JOB = "2a8e765b6611"
JOB_TEMPLATES = {
    "ee5cdd1455a1": ("ENVIID weekly brief", "0 9 * * 1", "Rangkum perkembangan mingguan biochar, carbon removal, peluang dan langkah praktis yang relevan untuk ENVIID tongkol jagung Lampung. Gunakan sumber resmi dan tanggal akses. Jangan mengarang revenue, pelanggan, produksi, sertifikasi, pendanaan, atau hasil aplikasi ENVIID."),
    "decb1f7e8447": ("Draft LinkedIn mingguan", "17 19 * * 0", "Siapkan satu draf LinkedIn berbahasa Indonesia yang ringkas tentang pembelajaran engineering atau climate-tech. Pisahkan draf dari publikasi. Jangan mengarang pengalaman, sertifikat, metrik dampak, atau hasil kerja pribadi Ardhya. Jika fakta pribadi belum terbukti, gunakan pembahasan edukasi umum dengan sumber resmi."),
    "357fd2ff763c": ("Carbon morning brief", "45 6 * * 1-5", "Rangkum tiga perkembangan terbaru biochar dan carbon removal yang relevan untuk Indonesia. Verifikasi sumber resmi, sebutkan tanggal, dan pisahkan fakta dari analisis. Tetap laporkan singkat jika sumber baru terbatas."),
    "6f17c39ca284": ("Promo AI harian", "57 7 * * *", "Cari promo AI baru yang relevan. Verifikasi halaman resmi, batas waktu, eligibility dan biaya atau syarat. Jangan menebak benefit. Jika file scripts/promo-ai-seen.md tersedia, baca untuk menghindari pengulangan. Jangan melakukan pembelian atau pendaftaran."),
    STARTUP_JOB: ("Peluang Startup & Investor Harian", "30 8 * * *", "Structured startup evidence digest"),
}


def next_template_run(expression: str, now: datetime) -> str:
    minute, hour, _, _, days = expression.split()
    for offset in range(8):
        candidate = (now + timedelta(days=offset)).replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
        cron_day = (candidate.weekday() + 1) % 7
        allowed = days == "*" or (days == "1-5" and 1 <= cron_day <= 5) or days == str(cron_day)
        if allowed and candidate > now:
            return candidate.isoformat()
    raise ValueError("No next occurrence in the operational job template")


def ensure_migrated_jobs(jobs: list, now: datetime) -> list[str]:
    added = []
    for job_id, (name, expression, prompt) in JOB_TEMPLATES.items():
        if any(job.get("id") == job_id for job in jobs):
            continue
        if any(job.get("name") == name for job in jobs):
            raise ValueError("A job name already exists with another ID; manual reconciliation required")
        jobs.append({"id": job_id, "name": name, "prompt": prompt, "skills": [], "skill": None,
                     "provider": "openrouter", "model": "openrouter/free", "no_agent": False,
                     "schedule": {"kind": "cron", "expr": expression, "display": expression},
                     "schedule_display": expression, "repeat": {"times": None, "completed": 0},
                     "enabled": True, "state": "scheduled", "created_at": now.isoformat(),
                     "next_run_at": next_template_run(expression, now), "last_run_at": None,
                     "last_status": None, "last_error": None, "failure_streak": 0,
                     "deliver": "telegram", "origin": None, "context_from": None})
        added.append(job_id)
    return added


def atomic_write(path: Path, text: str) -> None:
    atomic_text(path, text)


def configure_llm(config: dict, home: Path) -> None:
    env = {}
    path = home / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip("\"'")
    if not env.get("OPENROUTER_API_KEY"):
        return
    # These routes passed isolated cloud text AND image probes on 2026-11-15.
    config["model"] = {"provider": "openrouter", "default": "particle-ai/particle-1:free",
                       "context_length": 131072, "max_tokens": 4096}
    fallbacks = []
    if env.get("GOOGLE_API_KEY"):
        fallbacks.append({"provider": "custom", "model": "gemini-3.5-flash-lite",
                          "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                          "api_key_env": "GOOGLE_API_KEY", "supports_vision": True})
    fallbacks.extend({"provider": "openrouter", "model": model, "supports_vision": True}
                     for model in ("nvidia/nemotron-3.5-super-vl:free", "inclusionai/ling-3.2-flash:free"))
    config["fallback_providers"] = fallbacks
    config.setdefault("platform_toolsets", {})["telegram"] = ["terminal", "file", "web", "skills", "vision", "image_gen", "no_mcp"]
    config.setdefault("agent", {}).update(api_max_retries=1, local_stream_stale_timeout=20, reasoning_effort="low")
    for provider in ("openrouter", "custom", "gemini", "groq", "9router"):
        config.setdefault("providers", {}).setdefault(provider, {}).update(request_timeout_seconds=20, stale_timeout_seconds=20)
    config.setdefault("web", {})["extract_char_limit"] = 6000
    config.setdefault("vision", {}).update(embed_target_bytes=131072, max_calls_per_image=2)
    config.setdefault("compression", {}).update(threshold=0.4, threshold_tokens=24000, protect_last_n=4)
    config.setdefault("auxiliary", {})["compression"] = {"provider": "openrouter", "model": "particle-ai/particle-1:free", "timeout": 20}


def configure(home: Path) -> dict:
    config_path = home / "config.yaml"
    config = yaml.safe_load(config_path.read_text()) or {}
    config["terminal"] = {**config.get("terminal", {}), "backend": "local"}
    config.setdefault("gateway", {})["delivery_ledger"] = True
    config.setdefault("cron", {})["max_parallel_jobs"] = 1
    config.setdefault("platforms", {}).setdefault("telegram", {}).setdefault("extra", {})["drop_pending_on_cold_boot"] = False
    configure_llm(config, home)
    config.setdefault("auxiliary", {}).setdefault("compression", {}).setdefault("provider", "auto")
    if "notion" in config.get("mcp_servers", {}):
        config["mcp_servers"]["notion"]["enabled"] = False
    brain = config.get("mcp_servers", {}).get("gbrain", {})
    command = str(brain.get("command", ""))
    if command.startswith("/") and not Path(command).exists():
        brain["enabled"] = False
    plugins = config.get("plugins", {})
    if isinstance(plugins.get("enabled"), list):
        plugins["enabled"] = [name for name in plugins["enabled"] if name not in {"markifact", "td"}]
        plugins["disabled"] = sorted(set(plugins.get("disabled", [])) | {"markifact", "td"})
    atomic_write(config_path, yaml.safe_dump(config, sort_keys=False, allow_unicode=True))

    path = home / "cron/jobs.json"
    data = json.loads(path.read_text()) if path.exists() else {"jobs": []}
    added = ensure_migrated_jobs(data.setdefault("jobs", []), datetime.now(timezone(timedelta(hours=7))))
    changed = []
    for job in data.get("jobs", []):
        if job.get("id") in RESEARCH_JOBS:
            job["provider"] = "openrouter"
            job["model"] = "particle-ai/particle-1:free"
            job["enabled_toolsets"] = ["web", "file", "no_mcp"] if job.get("id") == "6f17c39ca284" else ["web", "no_mcp"]
            changed.append(job["id"])
        if job.get("id") == "c3277fb270f1":
            job["enabled"] = False
            job["state"] = "paused"
            job["paused_reason"] = "October 2026 event expired; annual recurrence disabled."
        if job.get("id") == STARTUP_JOB:
            job["context_from"] = None
            job["script"] = "startup_digest_context.py"
            job["output_validator"] = "validate_startup_digest.py"
            job["prompt"] = (home / "ops/startup_prompt.txt").read_text()
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return {"research_jobs_pinned": changed, "migrated_missing_jobs": added,
            "enabled_research_jobs": [j["id"] for j in data["jobs"] if j.get("enabled") and j.get("id") in RESEARCH_JOBS],
            "expired_event_paused": True,
            "startup_validator": "validate_startup_digest.py", "max_parallel_jobs": 1}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(configure(args.home)))

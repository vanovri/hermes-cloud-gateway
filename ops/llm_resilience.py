"""Small prompt and route guards for the owner-managed Telegram deployment."""
import json

PROVIDER_LIMIT_COPY = (
    "Provider menolak ukuran permintaan atau kuota akun untuk giliran ini. "
    "Riwayat Anda tetap tersimpan. Coba lagi setelah kuota pulih atau gunakan provider lain."
)


def compact_skill_index(categories, available_tools):
    full_size = sum(len(name) + len(description) for entries in categories.values() for name, description in entries)
    if full_size <= 12000 or not available_tools or "skills_list" not in available_tools:
        return None
    count = sum(len(entries) for entries in categories.values())
    return (f"## Skills\n{count} installed skills are available on demand. "
            "Call skills_list to discover relevant skills, then skill_view(name) to load their instructions. "
            "Do not assume a skill's contents from its name.\nCategories: "
            + ", ".join(sorted(categories))[:1500])


def contains_image(value):
    if isinstance(value, dict):
        return value.get("type") in {"image_url", "input_image", "image"} or any(contains_image(v) for v in value.values())
    if isinstance(value, list):
        return any(contains_image(v) for v in value)
    return False


def skip_fallback(agent, entry):
    # Groq's account TPM cap can be far below the advertised model window.
    if getattr(agent, "platform", None) == "telegram" and entry.get("provider") == "groq":
        return True
    try:
        from tools.vision_tools_history_budget import _native_turn_images
        vision = bool(_native_turn_images.get())
    except ImportError:
        vision = False
    vision = vision or getattr(agent, "_cloud_request_has_images", False)
    if not vision:
        return False
    return entry.get("supports_vision") is not True


class RequestBudgetExceeded(ValueError):
    status_code = 413


def bounded_outbound(agent, messages):
    agent._cloud_request_has_images = contains_image(messages)
    bounded = []
    for message in messages:
        if message.get("role") == "tool" and isinstance(message.get("content"), str) and len(message["content"]) > 10000:
            message = {**message, "content": message["content"][:7000] + "\n[Result excerpt. Use a targeted tool query for omitted content.]\n" + message["content"][-2000:]}
        bounded.append(message)
    if len(json.dumps(bounded, ensure_ascii=False).encode()) > 1_500_000:
        raise RequestBudgetExceeded("Request attachment budget exceeded. Use fewer or smaller attachments.")
    return bounded


def fresh_payload_rejection(messages):
    return sum(message.get("role") == "user" for message in messages) <= 3


def unhealthy_legacy_override(entry):
    return entry.get("provider") in {"custom", "9router", "groq"} and entry.get("model") in {
        "grok-auto", "ag/gemini-3.8-flash", "ag/gemini-3-flash", "openai/gpt-oss-120b"}

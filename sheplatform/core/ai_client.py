"""AI client (guide 23) - multi-provider, grounded.

Supported providers (switch via AI_PROVIDER env):
  kimi      -> Hetzner Inference API (Kimi-K2.7-Code, free while experimental)
  deepseek  -> api.deepseek.com (deepseek-v4-flash / deepseek-v4-pro)
  gemini    -> Google AI Studio OpenAI-compatible endpoint
  anthropic -> Anthropic SDK
  openai-compatible -> generic AI_BASE_URL/AI_API_KEY/AI_MODEL

Grounding rule (guide 23 failure point): AI answers ONLY from data provided
in the prompt. All callers MUST query the DB first and pass results as
context. If the active provider is unconfigured, calls return a graceful
"not configured" message instead of crashing.
"""
from __future__ import annotations

import logging

from sheplatform.config import settings

logger = logging.getLogger("sheplatform.ai")

SYSTEM_GROUNDING = (
    "You are a SHE compliance assistant for a Safety, Health and Environment "
    "management platform. Answer ONLY based on the data provided in the prompt. "
    "If the data does not contain the answer, say so explicitly. Never invent "
    "incident numbers, dates, names, or statistics. Be concise and professional."
)

PROVIDERS = ("kimi", "deepseek", "gemini", "anthropic", "openai-compatible")


def provider_info() -> dict:
    """Resolve the active provider configuration (no secrets in output)."""
    p = settings.AI_PROVIDER
    info = {"provider": p, "model": "", "configured": False}
    if p == "kimi":
        info["model"] = settings.KIMI_MODEL
        info["configured"] = bool(settings.KIMI_API_KEY and settings.KIMI_BASE_URL)
        info["label"] = "Kimi (Hetzner)"
    elif p == "deepseek":
        info["model"] = settings.DEEPSEEK_MODEL
        info["configured"] = bool(settings.DEEPSEEK_API_KEY and settings.DEEPSEEK_BASE_URL)
        info["label"] = "DeepSeek"
    elif p == "gemini":
        info["model"] = settings.GEMINI_MODEL
        info["configured"] = bool(settings.GEMINI_API_KEY and settings.GEMINI_BASE_URL)
        info["label"] = "Gemini (Google)"
    elif p == "anthropic":
        info["model"] = settings.AI_MODEL or "claude-sonnet-4-20250514"
        info["configured"] = bool(settings.ANTHROPIC_API_KEY)
        info["label"] = "Anthropic Claude"
    else:  # openai-compatible generic
        info["model"] = settings.AI_MODEL
        info["configured"] = bool(settings.AI_API_KEY and settings.AI_BASE_URL)
        info["label"] = "OpenAI-compatible"
    return info


def ai_configured() -> bool:
    return provider_info()["configured"]


def _endpoint_and_key() -> tuple[str, str, str]:
    """Return (base_url, api_key, model) for the active provider."""
    p = settings.AI_PROVIDER
    if p == "kimi":
        return settings.KIMI_BASE_URL, settings.KIMI_API_KEY, settings.KIMI_MODEL
    if p == "deepseek":
        return settings.DEEPSEEK_BASE_URL, settings.DEEPSEEK_API_KEY, settings.DEEPSEEK_MODEL
    if p == "gemini":
        return settings.GEMINI_BASE_URL, settings.GEMINI_API_KEY, settings.GEMINI_MODEL
    if p == "anthropic":
        return "", settings.ANTHROPIC_API_KEY, (settings.AI_MODEL or "claude-sonnet-4-20250514")
    return settings.AI_BASE_URL, settings.AI_API_KEY, settings.AI_MODEL


async def ask_ai(prompt: str, system: str | None = None, max_tokens: int = 2000) -> str:
    """Send a grounded prompt to the configured provider. Never raises."""
    info = provider_info()
    if not info["configured"]:
        return (f"AI assistant not configured. Provider '{info['provider']}' needs a key. "
                "Set it in the platform .env (see KIMI_API_KEY / DEEPSEEK_API_KEY / "
                "GEMINI_API_KEY) and restart.")
    system_prompt = system or SYSTEM_GROUNDING
    try:
        if settings.AI_PROVIDER == "anthropic":
            return await _ask_anthropic(prompt, system_prompt, max_tokens)
        return await _ask_openai_compatible(prompt, system_prompt, max_tokens)
    except Exception as e:
        logger.exception("AI request failed")
        return f"AI request failed: {e}"


async def _ask_openai_compatible(prompt: str, system: str, max_tokens: int) -> str:
    import httpx

    base_url, api_key, model = _endpoint_and_key()
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.2,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


async def _ask_anthropic(prompt: str, system: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    msg = await client.messages.create(
        model=settings.AI_MODEL or "claude-sonnet-4-20250514",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in msg.content if block.type == "text").strip()


async def ask_ai_vision(prompt, image_bytes, mime_type="image/jpeg",
                        system=None, max_tokens=1500) -> str:
    """Send an image + prompt to a vision-capable provider. Never raises."""
    import base64

    info = provider_info()
    if not info["configured"]:
        return "AI vision not configured."
    b64 = base64.b64encode(image_bytes).decode()
    system_prompt = system or SYSTEM_GROUNDING
    try:
        if settings.AI_PROVIDER == "anthropic":
            return await _ask_anthropic_vision(prompt, b64, mime_type, system_prompt, max_tokens)
        return await _ask_openai_vision(prompt, b64, mime_type, system_prompt, max_tokens)
    except Exception as e:
        logger.exception("AI vision failed")
        return f"AI vision failed: {e}"


async def _ask_openai_vision(prompt: str, b64: str, mime_type: str,
                             system: str, max_tokens: int) -> str:
    import httpx

    base_url, api_key, model = _endpoint_and_key()
    data_uri = f"data:{mime_type};base64,{b64}"
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ]},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.2,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


async def _ask_anthropic_vision(prompt: str, b64: str, mime_type: str,
                                system: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    msg = await client.messages.create(
        model=settings.AI_MODEL or "claude-sonnet-4-20250514",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image", "source": {
                "type": "base64", "media_type": mime_type, "data": b64}},
        ]}],
    )
    return "".join(block.text for block in msg.content if block.type == "text").strip()

"""Resolve which LLM provider to talk to and how, from env and saved settings."""

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.config import settings

PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"
PROVIDER_MUAPI = "muapi"
SUPPORTED_PROVIDERS = (PROVIDER_OPENAI_COMPATIBLE, PROVIDER_MUAPI)

DEFAULT_BASE_URLS = {
    PROVIDER_OPENAI_COMPATIBLE: "https://openrouter.ai/api/v1",
    PROVIDER_MUAPI: "https://api.muapi.ai/api/v1",
}

DEFAULT_MODELS = {
    PROVIDER_OPENAI_COMPATIBLE: "x-ai/grok-4.5",
    PROVIDER_MUAPI: "grok-4-5",
}

# "" means "do not send the parameter". Anything else is forwarded verbatim as
# `reasoning_effort` so servers that understand it (OpenRouter, llama.cpp,
# halogen-flash-server, OpenAI) can act on it and servers that do not will
# say so. The tuple lists the values the UI offers; other well-formed tokens
# are passed through too because servers keep adding levels.
REASONING_EFFORT_CHOICES = ("", "none", "minimal", "low", "medium", "high", "xhigh", "max")
_REASONING_EFFORT_TOKEN = re.compile(r"^[a-z0-9_-]{1,32}$")


def normalize_provider(value: Optional[str]) -> str:
    candidate = (value or "").strip().lower().replace("-", "_")
    if candidate in SUPPORTED_PROVIDERS:
        return candidate
    return PROVIDER_OPENAI_COMPATIBLE


def normalize_reasoning_effort(value: Optional[str]) -> str:
    candidate = (value or "").strip().lower()
    if not candidate:
        return ""
    if _REASONING_EFFORT_TOKEN.match(candidate):
        return candidate
    return ""


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    api_key: str
    base_url: str
    reasoning_effort: str
    default_model: str

    @property
    def is_openrouter(self) -> bool:
        return "openrouter.ai" in self.base_url.lower()


def resolve_llm_config(app_settings: Dict[str, Any]) -> LLMConfig:
    """Merge saved settings over environment defaults into one provider config."""
    provider = normalize_provider(app_settings.get("llm_provider") or settings.LLM_PROVIDER)
    api_key = str(app_settings.get("llm_api_key") or settings.LLM_API_KEY or "").strip()
    base_url = str(app_settings.get("llm_base_url") or settings.LLM_BASE_URL or "").strip()
    if not base_url:
        base_url = DEFAULT_BASE_URLS[provider]
    reasoning_effort = normalize_reasoning_effort(
        app_settings.get("llm_reasoning_effort")
        if app_settings.get("llm_reasoning_effort") is not None
        else settings.LLM_REASONING_EFFORT
    )
    default_model = str(app_settings.get("default_model") or settings.DEFAULT_MODEL or "").strip()
    if not default_model:
        default_model = DEFAULT_MODELS[provider]
    return LLMConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        reasoning_effort=reasoning_effort,
        default_model=default_model,
    )

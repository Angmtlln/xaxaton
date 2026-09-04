"""Small provider factory for the LangChain Master model only."""
from __future__ import annotations

from typing import Optional

from langchain_core.language_models import BaseChatModel
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

from app.config import Settings


def build_master_model(settings: Settings) -> Optional[BaseChatModel]:
    """Return the configured standard LangChain adapter, or offline fallback."""
    if settings.llm_mock:
        return None

    model_name = settings.master_model_name()
    common = {
        "model": model_name,
        "temperature": 0.0,
        "max_tokens": settings.agent_router_max_tokens,
        "timeout": settings.agent_model_timeout_s,
        "max_retries": 0,
        "model_kwargs": {"parallel_tool_calls": False},
    }
    if settings.master_provider == "polza":
        if not settings.polza_api_key:
            return None
        return ChatOpenAI(
            api_key=settings.polza_api_key,
            base_url=settings.polza_base_url.rstrip("/"),
            **common,
        )

    if not settings.groq_api_key:
        return None
    reasoning = {}
    if "gpt-oss" in model_name:
        reasoning["reasoning_format"] = "hidden"
        if settings.groq_reasoning_effort:
            reasoning["reasoning_effort"] = settings.groq_reasoning_effort
    return ChatGroq(
        api_key=settings.groq_api_key,
        base_url=_groq_sdk_base_url(settings.groq_base_url),
        **common,
        **reasoning,
    )


def _groq_sdk_base_url(value: str) -> str:
    """Groq SDK appends /openai/v1 to its root base URL."""
    normalized = value.rstrip("/")
    suffix = "/openai/v1"
    return normalized[:-len(suffix)] if normalized.endswith(suffix) else normalized

"""Standard OpenAI-compatible ChatOpenAI adapter for the Master Agent."""
from __future__ import annotations

from typing import Optional

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from app.config import Settings


def build_master_model(settings: Settings) -> Optional[BaseChatModel]:
    """Return the OpenRouter-backed Master model, or the offline fallback."""
    if settings.llm_mock or not settings.openrouter_api_key:
        return None

    return ChatOpenAI(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url.rstrip("/"),
        model=settings.master_model,
        temperature=0.0,
        max_tokens=settings.agent_router_max_tokens,
        timeout=settings.agent_model_timeout_s,
        max_retries=0,
        model_kwargs={"parallel_tool_calls": False},
    )

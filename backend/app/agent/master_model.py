"""Standard OpenAI-compatible ChatOpenAI adapter for the Master Agent."""
from __future__ import annotations

import logging
from typing import Optional

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from app.config import Settings


log = logging.getLogger(__name__)


def build_master_model(settings: Settings) -> Optional[BaseChatModel]:
    """Return the OpenRouter-backed Master model, or the offline fallback."""
    if settings.llm_mock or not settings.openrouter_api_key:
        return None

    extra_body = {}
    if settings.openrouter_reasoning_effort:
        extra_body["reasoning"] = {"effort": settings.openrouter_reasoning_effort}
    provider = {}
    if settings.openrouter_provider_sort:
        provider["sort"] = settings.openrouter_provider_sort
    if settings.openrouter_preferred_max_latency is not None:
        provider["preferred_max_latency"] = settings.openrouter_preferred_max_latency
    if provider:
        extra_body["provider"] = provider

    return ChatOpenAI(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url.rstrip("/"),
        model=settings.master_model,
        temperature=0.0,
        max_tokens=settings.agent_router_max_tokens,
        timeout=settings.agent_model_timeout_s,
        max_retries=0,
        model_kwargs={"parallel_tool_calls": False},
        default_headers=_openrouter_headers(settings),
        extra_body=extra_body or None,
    )


def _openrouter_headers(settings: Settings) -> dict:
    """Build optional OpenRouter attribution headers without breaking requests."""
    headers = {}
    for name, value in (
        ("X-Title", settings.openrouter_app_title),
        ("HTTP-Referer", settings.openrouter_app_url),
    ):
        if not value:
            continue
        try:
            value.encode("latin-1")
        except UnicodeEncodeError:
            log.warning("openrouter_header_skipped name=%s reason=not_latin1", name)
            continue
        headers[name] = value
    return headers

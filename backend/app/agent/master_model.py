"""Small provider factory for the LangChain Master model only."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Callable, List, Optional, Sequence

from langchain_core.callbacks import (AsyncCallbackManagerForLLMRun,
                                      CallbackManagerForLLMRun)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from pydantic import ConfigDict

from app.config import Settings


log = logging.getLogger(__name__)
RETRY_AFTER_RE = re.compile(r"try again in ([0-9.]+)\s*s")
# Дольше ждать нет смысла: вызов упрётся в собственный таймаут Master.
MAX_RETRY_WAIT_S = 20.0
# Модель не смогла выдержать контракт вызова: у другой модели цепочки шанс есть.
CONTRACT_FAILURE_CODES = {"json_validate_failed", "tool_use_failed"}


def build_master_model(settings: Settings) -> Optional[BaseChatModel]:
    """Return the configured standard LangChain adapter, or offline fallback."""
    if settings.llm_mock:
        return None

    names = settings.master_model_chain()
    models = [model for model in (_single_model(settings, name) for name in names) if model]
    if not models:
        return None
    if len(models) == 1:
        return models[0]
    return FailoverChatModel(runnables=models, model_names=names)


def _single_model(settings: Settings, model_name: str) -> Optional[BaseChatModel]:
    common = {
        "model": model_name,
        "temperature": 0.0,
        "max_tokens": settings.agent_router_max_tokens,
        "timeout": settings.agent_model_timeout_s,
        "max_retries": 0,
        "model_kwargs": {"parallel_tool_calls": False},
    }
    api_key = settings.master_api_key()
    if not api_key:
        return None

    if settings.master_provider == "openrouter":
        extra_body = {}
        if settings.openrouter_reasoning_effort:
            extra_body["reasoning"] = {"effort": settings.openrouter_reasoning_effort}
        return ChatOpenAI(
            api_key=api_key,
            base_url=settings.openrouter_base_url.rstrip("/"),
            default_headers=_openrouter_headers(settings),
            extra_body=extra_body or None,
            **common,
        )

    if settings.master_provider == "polza":
        return ChatOpenAI(
            api_key=api_key,
            base_url=settings.polza_base_url.rstrip("/"),
            **common,
        )

    reasoning = {}
    if "gpt-oss" in model_name:
        reasoning["reasoning_format"] = "hidden"
        if settings.groq_reasoning_effort:
            reasoning["reasoning_effort"] = settings.groq_reasoning_effort
    return ChatGroq(
        api_key=api_key,
        base_url=_groq_sdk_base_url(settings.groq_base_url),
        **common,
        **reasoning,
    )


def _openrouter_headers(settings: Settings) -> dict:
    """Необязательная атрибуция вызова в статистике аккаунта OpenRouter.

    Значение уходит в HTTP-заголовок, поэтому непечатаемое в latin-1 имя
    отбрасывается: атрибуция не стоит того, чтобы уронить каждый вызов.
    """
    headers = {}
    for name, value in (("X-Title", settings.openrouter_app_title),
                        ("HTTP-Referer", settings.openrouter_app_url)):
        if not value:
            continue
        try:
            value.encode("latin-1")
        except UnicodeEncodeError:
            log.warning("openrouter_header_skipped name=%s reason=not_latin1", name)
            continue
        headers[name] = value
    return headers


def _groq_sdk_base_url(value: str) -> str:
    """Groq SDK appends /openai/v1 to its root base URL."""
    normalized = value.rstrip("/")
    suffix = "/openai/v1"
    return normalized[:-len(suffix)] if normalized.endswith(suffix) else normalized


def suggested_retry_delay(exc: BaseException) -> Optional[float]:
    """Пауза, которую назвал сам провайдер: заголовок retry-after или текст ошибки."""
    response = getattr(exc, "response", None)
    raw = (getattr(response, "headers", None) or {}).get("retry-after")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    match = RETRY_AFTER_RE.search(str(exc))
    return float(match.group(1)) if match else None


def is_retryable_on_next_model(exc: BaseException) -> bool:
    """Провайдер занят, упёрся в лимит или не выдержал контракт структурного ответа."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        code = (body.get("error") or {}).get("code")
        if code in CONTRACT_FAILURE_CODES:
            return True
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if status in {408, 409, 429, 500, 502, 503, 504}:
        return True
    return type(exc).__name__ in {
        "RateLimitError", "APIConnectionError", "APITimeoutError",
        "InternalServerError", "ServiceUnavailableError",
    }


class FailoverChatModel(BaseChatModel):
    """Один и тот же вызов Master по очереди на моделях с раздельными лимитами.

    Лимит токенов в минуту у Groq считается по каждой модели отдельно, поэтому
    после полной проверки основная модель Master оказывается занята. Вместо
    детерминированной заглушки тот же запрос уходит на следующую модель списка.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    runnables: List[Any]
    model_names: List[str]

    @property
    def _llm_type(self) -> str:
        return "master-failover"

    @property
    def _identifying_params(self) -> dict:
        return {"models": self.model_names}

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "FailoverChatModel":
        return FailoverChatModel(
            runnables=[runnable.bind_tools(tools, **kwargs) for runnable in self.runnables],
            model_names=self.model_names,
        )

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        wait: Optional[float] = None
        for attempt in (0, 1):
            if attempt:
                time.sleep(wait)
            result, error, wait = self._sweep(
                lambda runnable: runnable.invoke(messages, stop=stop, **kwargs)
            )
            if result is not None:
                return result
            if wait is None:
                break
        raise error

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        wait: Optional[float] = None
        for attempt in (0, 1):
            if attempt:
                await asyncio.sleep(wait)
            result, error, wait = await self._asweep(
                lambda runnable: runnable.ainvoke(messages, stop=stop, **kwargs)
            )
            if result is not None:
                return result
            if wait is None:
                break
        raise error

    def _sweep(self, call: Callable):
        """Один проход по цепочке моделей; возвращает ответ либо ошибку и паузу."""
        error: Optional[BaseException] = None
        delays: List[float] = []
        for name, runnable in zip(self.model_names, self.runnables):
            try:
                return _as_chat_result(call(runnable)), None, None
            except Exception as exc:  # noqa: BLE001
                if not is_retryable_on_next_model(exc):
                    raise
                error = exc
                self._note(name, exc, delays)
        return None, error, _retry_wait(delays)

    async def _asweep(self, call: Callable):
        error: Optional[BaseException] = None
        delays: List[float] = []
        for name, runnable in zip(self.model_names, self.runnables):
            try:
                return _as_chat_result(await call(runnable)), None, None
            except Exception as exc:  # noqa: BLE001
                if not is_retryable_on_next_model(exc):
                    raise
                error = exc
                self._note(name, exc, delays)
        return None, error, _retry_wait(delays)

    @staticmethod
    def _note(name: str, exc: BaseException, delays: List[float]) -> None:
        delay = suggested_retry_delay(exc)
        if delay is not None:
            delays.append(delay)
        log.info(
            "master_model_failover model=%s reason=%s retry_after_s=%s",
            name, type(exc).__name__, delay,
        )


def _retry_wait(delays: List[float]) -> Optional[float]:
    """Ждём столько, сколько попросила самая свободная модель цепочки."""
    if not delays:
        return None
    wait = min(delays)
    return wait + 0.25 if 0 < wait <= MAX_RETRY_WAIT_S else None


def _as_chat_result(message: Any) -> ChatResult:
    if not isinstance(message, AIMessage):
        raise ValueError("Master model returned an invalid message")
    return ChatResult(generations=[ChatGeneration(message=message)])

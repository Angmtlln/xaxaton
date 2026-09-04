"""Тонкий клиент Groq Chat Completions (OpenAI-совместимый API).

Две особенности бесплатного тарифа, которые здесь учтены.

1. Лимит токенов в минуту считается по каждой модели отдельно. Поэтому
   при 429 клиент не ждёт минуту, а сразу пробует следующую модель из
   списка запасных, и только когда кончились все — выдерживает паузу,
   которую назвал сам Groq.
2. Модели с рассуждениями иногда не укладывают JSON в лимит токенов и
   отдают ошибку json_validate_failed. Тогда повторяем запрос без
   response_format: ответ разберёт наш же парсер.
"""
import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import httpx

from app.config import Settings

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}

# «Please try again in 25.1775s» — Groq сам называет паузу по лимиту.
RETRY_AFTER_RE = re.compile(r"try again in ([0-9.]+)s")
MAX_RETRY_SLEEP_S = 35.0


class LLMError(RuntimeError):
    pass


class RateLimited(LLMError):
    """Модель упёрлась в лимит токенов в минуту."""

    def __init__(self, message: str, retry_after: float):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class LLMResponse:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    raw: Dict[str, Any]

    def json_payload(self) -> Dict[str, Any]:
        """Достаёт JSON из ответа модели, терпя обрамление текстом."""
        text = (self.content or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError as exc:
                    raise LLMError("Модель вернула невалидный JSON: %s" % exc) from exc
            raise LLMError("В ответе модели нет JSON-объекта")


def _retry_after(response: httpx.Response) -> float:
    header = response.headers.get("retry-after")
    if header:
        try:
            return min(float(header), MAX_RETRY_SLEEP_S)
        except ValueError:
            pass
    match = RETRY_AFTER_RE.search(response.text or "")
    if match:
        return min(float(match.group(1)) + 0.5, MAX_RETRY_SLEEP_S)
    return 5.0


class GroqClient:
    def __init__(self, settings: Settings, client: Optional[httpx.AsyncClient] = None):
        self.settings = settings
        self._client = client
        self._owns_client = client is None

    @property
    def enabled(self) -> bool:
        return bool(self.settings.groq_api_key) and not self.settings.llm_mock

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.settings.groq_timeout_s)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def complete_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float,
        max_tokens: Optional[int] = None,
        fallback_models: Optional[Sequence[str]] = None,
    ) -> LLMResponse:
        """Один логический вызов модели с требованием JSON-ответа."""
        if not self.enabled:
            raise LLMError("GROQ_API_KEY не задан, включите LLM_MOCK=true для работы без ключа")

        models: List[str] = [model]
        for candidate in fallback_models or ():
            if candidate and candidate not in models:
                models.append(candidate)

        last_error: Optional[str] = None
        pause: Optional[float] = None

        for index, current in enumerate(models):
            try:
                return await self._call_model(current, system, user, temperature, max_tokens)
            except RateLimited as exc:
                last_error = str(exc)
                pause = exc.retry_after
                if index < len(models) - 1:
                    log.info("Лимит токенов у %s, пробуем %s", current, models[index + 1])
                    continue
            except LLMError as exc:
                last_error = str(exc)
                if index < len(models) - 1:
                    log.info("Модель %s не ответила (%s), пробуем %s",
                             current, exc, models[index + 1])
                    continue

        # Все модели заняты: ждём столько, сколько попросил сервер, и пробуем ещё раз.
        if pause is not None:
            log.info("Все модели в лимите, ждём %.1f c и повторяем %s", pause, models[0])
            await asyncio.sleep(pause)
            try:
                return await self._call_model(models[0], system, user, temperature, max_tokens)
            except LLMError as exc:
                last_error = str(exc)

        raise LLMError("Groq не ответил (%s). Модели: %s" % (last_error, ", ".join(models)))

    async def _call_model(self, model: str, system: str, user: str,
                          temperature: float, max_tokens: Optional[int]) -> LLMResponse:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens or self.settings.max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        if "gpt-oss" in model and self.settings.groq_reasoning_effort:
            payload["reasoning_effort"] = self.settings.groq_reasoning_effort

        url = "%s/chat/completions" % self.settings.groq_base_url.rstrip("/")
        headers = {"Authorization": "Bearer %s" % self.settings.groq_api_key,
                   "Content-Type": "application/json"}
        client = await self._http()
        started = time.perf_counter()
        last_error = "неизвестная ошибка"

        for attempt in range(self.settings.groq_max_retries + 1):
            try:
                resp = await client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                last_error = "сеть: %s" % exc
                if attempt < self.settings.groq_max_retries:
                    await asyncio.sleep(1.5 * (attempt + 1))
                continue

            if resp.status_code == 200:
                data = resp.json()
                usage = data.get("usage") or {}
                return LLMResponse(
                    content=(data["choices"][0]["message"].get("content") or ""),
                    model=data.get("model", model),
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    raw=data,
                )

            body = resp.text or ""
            last_error = "HTTP %s: %s" % (resp.status_code, body[:300])

            if resp.status_code == 429:
                raise RateLimited("модель %s в лимите" % model, _retry_after(resp))

            # Ответ не уложился в JSON-схему: повторяем без неё.
            if resp.status_code == 400 and "json_validate_failed" in body:
                if payload.pop("response_format", None) is not None:
                    log.info("Модель %s не отдала JSON, повтор без response_format", model)
                    continue

            if resp.status_code not in RETRYABLE_STATUS:
                break
            if attempt < self.settings.groq_max_retries:
                await asyncio.sleep(1.5 * (attempt + 1))

        raise LLMError(last_error)

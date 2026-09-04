"""Нейтральная граница LLM и адаптер над существующим GroqClient."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Protocol, Sequence

from app.config import Settings
from app.llm.groq_client import GroqClient, LLMError


@dataclass(frozen=True)
class LLMMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ModelResponse:
    payload: Dict[str, Any]
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


class LLMClient(Protocol):
    async def chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[Dict[str, Any]],
        response_schema: Dict[str, Any],
    ) -> ModelResponse:
        """Возвращает структурированный ответ модели, не исполняя actions."""


class GroqLLMAdapter:
    """Переводит нейтральный chat-контракт в текущий complete_json()."""

    def __init__(self, client: GroqClient, settings: Settings):
        self.client = client
        self.settings = settings

    async def chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[Dict[str, Any]],
        response_schema: Dict[str, Any],
    ) -> ModelResponse:
        if not self.client.enabled:
            raise LLMError("LLM router недоступен в deterministic режиме")

        system_parts: List[str] = []
        conversation: List[Dict[str, str]] = []
        for message in messages:
            if message.role == "system":
                system_parts.append(message.content)
            else:
                conversation.append({"role": message.role, "content": message.content})

        user_payload = {
            "conversation": conversation,
            "available_tools": list(tools),
            "response_schema": response_schema,
        }
        response = await self.client.complete_json(
            model=self.settings.groq_master_model,
            system="\n\n".join(system_parts),
            user=json.dumps(user_payload, ensure_ascii=False, separators=(",", ":")),
            temperature=0.0,
            max_tokens=self.settings.agent_router_max_tokens,
            fallback_models=self.settings.fallback_models(),
        )
        return ModelResponse(
            payload=response.json_payload(),
            model=response.model,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            latency_ms=response.latency_ms,
        )

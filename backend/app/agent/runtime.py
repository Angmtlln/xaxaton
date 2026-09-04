"""Одношаговый Master Agent runtime с жёстким бюджетом одного tool call."""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Dict, Optional, Tuple

from app.config import Settings
from app.llm.groq_client import GroqClient

from .llm import GroqLLMAdapter, LLMClient, LLMMessage
from .models import (MASTER_ACTION_ADAPTER, FinalAction, ToolCallAction,
                     is_valid_inn)
from .prompt import MASTER_SYSTEM_PROMPT
from .response import (guard_response, runtime_timeout_response,
                       tool_result_to_assistant)
from .tools import ToolContext, ToolRegistry, build_tool_registry

log = logging.getLogger(__name__)

MAX_ITERATIONS = 1
MAX_TOOL_CALLS = 1
DIGIT_SEQUENCE_RE = re.compile(r"(?<![0-9])[0-9]+(?![0-9])")
CHECK_WORD_RE = re.compile(r"\bпров(?:ерь(?:те)?|ер(?:ить|ка|ку|ьте))\b", re.IGNORECASE)
BROAD_TARGET_RE = re.compile(
    r"\b(?:контрагент\w*|компан\w*|организац\w*|юрлиц\w*|инн)\b", re.IGNORECASE
)
FULL_PHRASE_RE = re.compile(
    r"\b(?:пол\w+|комплексн\w+)\s+(?:провер\w*|анализ\w*|отч[её]т\w*)\b",
    re.IGNORECASE,
)
NARROW_TOPIC_RE = re.compile(
    r"\b(?:выручк\w*|прибыл\w*|финанс\w*|суд\w*|арбитраж\w*|"
    r"исполнител\w*|закуп\w*|тендер\w*|лиценз\w*)\b",
    re.IGNORECASE,
)


class MasterAgentRuntime:
    def __init__(
        self,
        *,
        llm: LLMClient,
        registry: ToolRegistry,
        tool_context: ToolContext,
        model_timeout_s: float,
        run_timeout_s: float,
    ):
        self.llm = llm
        self.registry = registry
        self.tool_context = tool_context
        self.model_timeout_s = model_timeout_s
        self.run_timeout_s = run_timeout_s

    async def run(self, message: str):
        agent_run_id = str(uuid.uuid4())
        started = time.perf_counter()
        state = {"tool_calls": 0}
        log.info(
            "agent_run_started run_id=%s tools=full_company_check max_iterations=%s max_tool_calls=%s",
            agent_run_id,
            MAX_ITERATIONS,
            MAX_TOOL_CALLS,
        )
        try:
            response = await asyncio.wait_for(
                self._run_once(message, agent_run_id, started, state),
                timeout=self.run_timeout_s,
            )
        except asyncio.TimeoutError:
            log.warning("agent_run_timeout run_id=%s", agent_run_id)
            return runtime_timeout_response(
                agent_run_id, started, tool_calls=state["tool_calls"]
            )
        log.info(
            "agent_run_finished run_id=%s status=%s tool_calls=%s latency_ms=%s",
            agent_run_id,
            response.metadata.status,
            response.metadata.tool_calls,
            response.metadata.latency_ms,
        )
        return response

    async def _run_once(
        self,
        message: str,
        agent_run_id: str,
        started: float,
        state: Dict[str, int],
    ):
        reason, inn = inspect_request(message)
        if reason is not None:
            return guard_response(reason, agent_run_id, started)
        if inn is None or not is_full_check_request(message):
            return guard_response("unsupported_request", agent_run_id, started)

        routing = "model"
        model: Optional[str] = None
        action = None
        try:
            model_response = await asyncio.wait_for(
                self.llm.chat(
                    [
                        LLMMessage(role="system", content=MASTER_SYSTEM_PROMPT),
                        LLMMessage(role="user", content=message.strip()),
                    ],
                    tools=self.registry.visible_tools(),
                    response_schema=MASTER_ACTION_ADAPTER.json_schema(),
                ),
                timeout=self.model_timeout_s,
            )
            action = MASTER_ACTION_ADAPTER.validate_python(model_response.payload)
            model = model_response.model
        except Exception as exc:  # noqa: BLE001
            log.info("agent_router_fallback run_id=%s reason=%s", agent_run_id, type(exc).__name__)
            routing = "deterministic_fallback"

        # Очевидный широкий запрос с валидным ИНН не зависит от доступности router LLM.
        if action is None or isinstance(action, FinalAction):
            action = ToolCallAction(
                type="tool_call",
                tool="full_company_check",
                arguments={"inn": inn},
            )
            routing = "deterministic_fallback"

        if state["tool_calls"] >= MAX_TOOL_CALLS:
            return guard_response("unsupported_request", agent_run_id, started)
        state["tool_calls"] += 1

        arguments = dict(action.arguments)
        # Модель не может заменить явно указанный пользователем ИНН другим валидным ИНН.
        if action.tool == "full_company_check" and arguments.get("inn") != inn:
            arguments = {}

        log.info(
            "agent_tool_call run_id=%s tool=%s call=%s/%s",
            agent_run_id,
            action.tool,
            state["tool_calls"],
            MAX_TOOL_CALLS,
        )
        result = await self.registry.execute(action.tool, arguments, self.tool_context)
        log.info(
            "agent_tool_result run_id=%s tool=%s status=%s latency_ms=%s",
            agent_run_id,
            result.metadata.tool,
            result.status,
            result.metadata.latency_ms,
        )
        return tool_result_to_assistant(
            result,
            agent_run_id=agent_run_id,
            routing=routing,
            model=model,
            started=started,
        )


def build_master_runtime(
    settings: Settings, client: GroqClient, *, persist: bool = True
) -> MasterAgentRuntime:
    return MasterAgentRuntime(
        llm=GroqLLMAdapter(client, settings),
        registry=build_tool_registry(settings),
        tool_context=ToolContext(settings=settings, client=client, persist=persist),
        model_timeout_s=settings.agent_model_timeout_s,
        run_timeout_s=settings.agent_run_timeout_s,
    )


def inspect_request(message: str) -> Tuple[Optional[str], Optional[str]]:
    sequences = DIGIT_SEQUENCE_RE.findall(message or "")
    if not sequences:
        return "missing_inn", None
    valid = sorted({value for value in sequences if is_valid_inn(value)})
    if len(valid) > 1:
        return "ambiguous_inn", None
    if not valid:
        return "invalid_inn", None
    return None, valid[0]


def is_full_check_request(message: str) -> bool:
    text = message.strip()
    if FULL_PHRASE_RE.search(text):
        return True
    if not CHECK_WORD_RE.search(text):
        return False
    if BROAD_TARGET_RE.search(text):
        return True
    return not NARROW_TOPIC_RE.search(text)

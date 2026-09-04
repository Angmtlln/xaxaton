"""LangChain create_agent facade для первого agent-first vertical slice."""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Optional, Tuple

from langchain.agents import create_agent
from langchain.agents.middleware import (ModelCallLimitMiddleware,
                                         ToolCallLimitMiddleware,
                                         wrap_model_call)
from langchain_core.language_models import BaseChatModel
from langchain_groq import ChatGroq

from app.config import Settings
from app.llm.groq_client import GroqClient

from .langchain_tools import (LangChainToolExecution, build_langchain_tools,
                              tool_result_from_state)
from .models import is_valid_inn
from .prompt import MASTER_SYSTEM_PROMPT
from .response import (guard_response, runtime_timeout_response,
                       tool_result_to_assistant)
from .tools import ToolContext, ToolRegistry, build_tool_registry

log = logging.getLogger(__name__)

MAX_MODEL_CALLS = 1
MAX_TOOL_CALLS = 1
GRAPH_RECURSION_LIMIT = 8
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
    """Application boundary вокруг compiled LangGraph от ``create_agent``."""

    def __init__(
        self,
        *,
        model: Optional[BaseChatModel],
        model_name: Optional[str],
        registry: ToolRegistry,
        tool_context: ToolContext,
        model_timeout_s: float,
        run_timeout_s: float,
    ):
        self.model = model
        self.model_name = model_name
        self.registry = registry
        self.tool_context = tool_context
        self.model_timeout_s = model_timeout_s
        self.run_timeout_s = run_timeout_s

    async def run(self, message: str):
        agent_run_id = str(uuid.uuid4())
        started = time.perf_counter()
        execution = LangChainToolExecution()
        log.info(
            "agent_run_started run_id=%s harness=langchain.create_agent "
            "max_model_calls=%s max_tool_calls=%s",
            agent_run_id,
            MAX_MODEL_CALLS,
            MAX_TOOL_CALLS,
        )
        try:
            response = await asyncio.wait_for(
                self._run_once(message, agent_run_id, started, execution),
                timeout=self.run_timeout_s,
            )
        except asyncio.TimeoutError:
            log.warning("agent_run_timeout run_id=%s", agent_run_id)
            return runtime_timeout_response(
                agent_run_id, started, tool_calls=execution.tool_calls
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
        execution: LangChainToolExecution,
    ):
        reason, inn = inspect_request(message)
        if reason is not None:
            return guard_response(reason, agent_run_id, started)
        if inn is None or not is_full_check_request(message):
            return guard_response("unsupported_request", agent_run_id, started)

        if self.model is None:
            return await self._deterministic_fallback(
                inn, agent_run_id, started, execution
            )

        tools = build_langchain_tools(
            self.registry,
            self.tool_context,
            agent_run_id=agent_run_id,
            expected_inn=inn,
            execution=execution,
        )
        agent = create_agent(
            model=self.model,
            tools=tools,
            system_prompt=MASTER_SYSTEM_PROMPT,
            middleware=[
                _model_policy(self.model_timeout_s),
                ModelCallLimitMiddleware(
                    run_limit=MAX_MODEL_CALLS, exit_behavior="error"
                ),
                ToolCallLimitMiddleware(
                    run_limit=MAX_TOOL_CALLS, exit_behavior="error"
                ),
            ],
            name="counterparty_master_agent",
        )

        state = None
        try:
            state = await agent.ainvoke(
                {"messages": [{"role": "user", "content": message.strip()}]},
                config={"recursion_limit": GRAPH_RECURSION_LIMIT},
            )
        except Exception as exc:  # noqa: BLE001
            log.info(
                "agent_router_fallback run_id=%s reason=%s",
                agent_run_id,
                type(exc).__name__,
            )

        result = tool_result_from_state(state) or execution.result
        if result is None:
            if execution.started:
                # Не повторяем уже начавшийся domain call при неизвестном сбое graph.
                return runtime_timeout_response(
                    agent_run_id, started, tool_calls=execution.tool_calls
                )
            return await self._deterministic_fallback(
                inn, agent_run_id, started, execution
            )

        routing = "deterministic_fallback" if execution.used_fallback else "model"
        return tool_result_to_assistant(
            result,
            agent_run_id=agent_run_id,
            routing=routing,
            model=self.model_name,
            started=started,
        )

    async def _deterministic_fallback(
        self,
        inn: str,
        agent_run_id: str,
        started: float,
        execution: LangChainToolExecution,
    ):
        if execution.started:
            if execution.result is not None:
                return tool_result_to_assistant(
                    execution.result,
                    agent_run_id=agent_run_id,
                    routing="deterministic_fallback",
                    model=self.model_name,
                    started=started,
                )
            return runtime_timeout_response(
                agent_run_id, started, tool_calls=execution.tool_calls
            )

        execution.started = True
        execution.tool_calls = 1
        execution.used_fallback = True
        log.info(
            "agent_tool_call run_id=%s tool=full_company_check "
            "routing=deterministic_fallback call=1/1",
            agent_run_id,
        )
        result = await self.registry.execute(
            "full_company_check", {"inn": inn}, self.tool_context
        )
        execution.result = result
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
            routing="deterministic_fallback",
            model=None,
            started=started,
        )


def build_master_runtime(
    settings: Settings, client: GroqClient, *, persist: bool = True
) -> MasterAgentRuntime:
    model = build_master_model(settings) if client.enabled else None
    return MasterAgentRuntime(
        model=model,
        model_name=settings.groq_master_model if model is not None else None,
        registry=build_tool_registry(settings),
        tool_context=ToolContext(settings=settings, client=client, persist=persist),
        model_timeout_s=settings.agent_model_timeout_s,
        run_timeout_s=settings.agent_run_timeout_s,
    )


def build_master_model(settings: Settings) -> Optional[BaseChatModel]:
    if settings.llm_mock or not settings.groq_api_key:
        return None
    reasoning = {}
    if "gpt-oss" in settings.groq_master_model:
        reasoning["reasoning_format"] = "hidden"
        if settings.groq_reasoning_effort:
            reasoning["reasoning_effort"] = settings.groq_reasoning_effort
    return ChatGroq(
        model=settings.groq_master_model,
        api_key=settings.groq_api_key,
        base_url=_groq_sdk_base_url(settings.groq_base_url),
        temperature=0.0,
        max_tokens=settings.agent_router_max_tokens,
        timeout=settings.agent_model_timeout_s,
        max_retries=0,
        model_kwargs={"parallel_tool_calls": False},
        **reasoning,
    )


def _groq_sdk_base_url(value: str) -> str:
    """Groq SDK сам добавляет /openai/v1 к корневому base URL."""
    normalized = value.rstrip("/")
    suffix = "/openai/v1"
    return normalized[:-len(suffix)] if normalized.endswith(suffix) else normalized


def _model_policy(timeout_s: float):
    @wrap_model_call
    async def enforce_master_model_policy(request, handler):
        settings = dict(request.model_settings)
        settings["parallel_tool_calls"] = False
        bounded = request.override(
            tool_choice="required",
            model_settings=settings,
        )
        return await asyncio.wait_for(handler(bounded), timeout=timeout_s)

    return enforce_master_model_policy


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

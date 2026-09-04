"""Bounded LangChain Master loop and checkpointed conversation boundary."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from contextlib import AsyncExitStack
from typing import Optional, Tuple

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware, wrap_model_call
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatResult
from langchain_groq import ChatGroq

from app.config import Settings
from app.llm.groq_client import GroqClient
from .conversations import ConversationStore, ConversationState, UnknownConversation, ConversationCapacityError
from .langchain_tools import LangChainToolExecution, build_langchain_tools
from .models import CompanyRef, TextBlock, is_valid_inn
from .prompt import MASTER_SYSTEM_PROMPT, MASTER_PROMPT_VERSION
from .response import guard_response, runtime_timeout_response, tool_result_to_assistant
from .tools import ToolContext, ToolRegistry, build_tool_registry

log = logging.getLogger(__name__)
MAX_MODEL_CALLS = 2
MAX_TOOL_CALLS = 1
GRAPH_RECURSION_LIMIT = 12
TOOL_BUNDLE_VERSION = "counterparty-tools-2.0.0"
DIGIT_SEQUENCE_RE = re.compile(r"(?<![0-9])[0-9]+(?![0-9])")
CHECK_WORD_RE = re.compile(r"\bпров(?:ерь(?:те)?|ер(?:ить|ка|ку|ьте))\b", re.I)
BROAD_TARGET_RE = re.compile(r"\b(?:контрагент\w*|компан\w*|организац\w*|юрлиц\w*|инн)\b", re.I)
FULL_PHRASE_RE = re.compile(r"\b(?:пол\w+|комплексн\w+)\s+(?:провер\w*|анализ\w*|отч[её]т\w*)\b", re.I)
FINANCE_TOPIC_RE = re.compile(r"\b(?:выручк\w*|прибыл\w*|финанс\w*|рентабельн\w*|капитал\w*|баланс\w*)\b", re.I)
LEGAL_TOPIC_RE = re.compile(r"\b(?:суд\w*|арбитраж\w*|исполнител\w*|юридическ\w*|надежност\w*|надёжност\w*|банкрот\w*|иск[аи]?|иски)\b", re.I)
NARROW_TOPIC_RE = re.compile(r"\b(?:выручк\w*|прибыл\w*|финанс\w*|суд\w*|арбитраж\w*|исполнител\w*|закуп\w*|тендер\w*|лиценз\w*)\b", re.I)


class _OfflineStateModel(BaseChatModel):
    """Checkpoint handle in offline mode; never invoked for generation."""
    @property
    def _llm_type(self):
        return "offline-checkpoint-only"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        raise RuntimeError("Offline model must not be called")


class MasterAgentRuntime:
    def __init__(self, *, model: Optional[BaseChatModel], model_name: Optional[str],
                 registry: ToolRegistry, tool_context: ToolContext,
                 model_timeout_s: float, run_timeout_s: float,
                 conversation_store: Optional[ConversationStore] = None):
        self.model = model
        self.model_name = model_name
        self.registry = registry
        self.tool_context = tool_context
        self.model_timeout_s = model_timeout_s
        self.run_timeout_s = run_timeout_s
        self.conversation_store = conversation_store or ConversationStore()

    async def run(self, message: str, conversation_id: Optional[str] = None):
        run_id, started = str(uuid.uuid4()), time.perf_counter()
        deadline = time.monotonic() + self.run_timeout_s
        log.info("agent_run_queued run_id=%s conversation_id=%s model=%s provider=%s "
                 "prompt_version=%s tool_bundle_version=%s model_limit=%s tool_limit=%s "
                 "recursion_limit=%s timeout_s=%s", run_id, conversation_id, self.model_name,
                 "groq" if isinstance(self.model, ChatGroq) else "local",
                 MASTER_PROMPT_VERSION, TOOL_BUNDLE_VERSION, MAX_MODEL_CALLS,
                 MAX_TOOL_CALLS, GRAPH_RECURSION_LIMIT, self.run_timeout_s)
        try:
            async with AsyncExitStack() as stack:
                # The same run budget covers both queue admission and execution.
                cid, _ = await asyncio.wait_for(
                    stack.enter_async_context(self.conversation_store.session(conversation_id)),
                    timeout=max(0, deadline - time.monotonic()),
                )
                return await self._run_conversation(message, cid, run_id, started, deadline)
        except asyncio.TimeoutError:
            # Admission timed out: do not create a turn or overwrite the active run.
            response = runtime_timeout_response(run_id, started, tool_calls=0)
            response.conversation_id = conversation_id
            if conversation_id is not None:
                checkpoint = await self.conversation_store.checkpointer.aget_tuple(
                    {"configurable": {"thread_id": conversation_id}}
                )
                active = checkpoint.checkpoint["channel_values"].get("active_company") if checkpoint else None
                response.active_company = CompanyRef.model_validate(active) if active else None
            log.info("agent_run_finished run_id=%s conversation_id=%s status=timeout "
                     "routing=deterministic_guard model_calls=0 tool_calls=0 latency_ms=%s",
                     run_id, conversation_id, int((time.perf_counter() - started) * 1000))
            return response
        except (UnknownConversation, ConversationCapacityError) as exc:
            response = guard_response("missing_inn", run_id, started)
            unknown = isinstance(exc, UnknownConversation)
            response.message = ("Диалог не найден или истёк. Начните новый диалог и укажите ИНН."
                                if unknown else "Все диалоги сейчас заняты. Повторите запрос позже.")
            response.blocks = [TextBlock(text=response.message)]
            response.metadata.error_code = "unknown_conversation" if unknown else "conversation_capacity"
            log.info("agent_run_finished run_id=%s conversation_id=%s status=%s "
                     "routing=deterministic_guard model_calls=0 tool_calls=0 latency_ms=%s",
                     run_id, conversation_id, response.metadata.error_code,
                     int((time.perf_counter() - started) * 1000))
            return response

    async def _run_conversation(self, message, cid, run_id, started, deadline):
        execution = LangChainToolExecution()
        config = {"configurable": {"thread_id": cid}, "recursion_limit": GRAPH_RECURSION_LIMIT}
        # Same create_agent state schema/checkpointer for online and offline paths.
        state_agent = create_agent(model=self.model or _OfflineStateModel(), tools=[],
                                   state_schema=ConversationState,
                                   checkpointer=self.conversation_store.checkpointer)
        previous = (await state_agent.aget_state(config)).values
        active = previous.get("active_company")
        reason, inn = inspect_request(message)
        if reason == "missing_inn" and active:
            reason, inn = None, active["inn"]
        target = requested_tool(message)
        log.info("agent_run_started run_id=%s conversation_id=%s model=%s provider=%s "
                 "prompt_version=%s tool_bundle_version=%s tools_visible=%s inn=%s "
                 "model_limit=%s tool_limit=%s remaining_ms=%s", run_id, cid, self.model_name,
                 "groq" if isinstance(self.model, ChatGroq) else "local",
                 MASTER_PROMPT_VERSION, TOOL_BUNDLE_VERSION, [target] if target else [], inn,
                 MAX_MODEL_CALLS, MAX_TOOL_CALLS, max(0, int((deadline - time.monotonic()) * 1000)))
        response, result = None, None
        if reason is not None or target is None:
            response = guard_response(reason or "unsupported_request", run_id, started)
        else:
            try:
                response = await asyncio.wait_for(
                    self._execute(message, inn, target, run_id, started, execution, config),
                    timeout=max(0, deadline - time.monotonic()),
                )
            except asyncio.TimeoutError:
                response = runtime_timeout_response(run_id, started, tool_calls=execution.tool_calls)
            result = execution.result
        # Only this execution's verified result can change active company.
        if result is not None and result.status in ("success", "partial"):
            company = result.data.get("company")
            if isinstance(company, dict) and company.get("inn") == inn and is_valid_inn(inn):
                active = CompanyRef(inn=inn, name=company.get("name") or company.get("short_name")
                                    or company.get("full_name")).model_dump(mode="json")
        response.conversation_id = cid
        response.active_company = CompanyRef.model_validate(active) if active else None
        response.metadata.model_calls = execution.model_calls
        # Replace work-in-progress and model-generated messages with trusted turns.
        history = list(previous.get("messages", [])) + [HumanMessage(content=message), AIMessage(content=response.message)]
        history = history[-2 * self.conversation_store.max_turns:]
        # InMemorySaver otherwise retains every intermediate checkpoint indefinitely.
        # Per-conversation lease makes replacing the checkpoint history atomic to callers.
        await self.conversation_store.checkpointer.adelete_thread(cid)
        await state_agent.aupdate_state(config, {"messages": history, "active_company": active}, as_node="model")
        log.info("agent_run_finished run_id=%s conversation_id=%s status=%s model_calls=%s "
                 "tool_calls=%s routing=%s synthesis=%s latency_ms=%s input_tokens=%s output_tokens=%s",
                 run_id, cid, response.metadata.status, execution.model_calls, execution.tool_calls,
                 response.metadata.routing, response.metadata.synthesis,
                 int((time.perf_counter() - started) * 1000), execution.input_tokens, execution.output_tokens)
        return response

    async def _execute(self, message, inn, target, run_id, started, execution, config):
        synthesis = None
        if self.model is not None:
            agent = create_agent(
                model=self.model,
                tools=build_langchain_tools(self.registry, self.tool_context,
                    agent_run_id=run_id, expected_inn=inn, execution=execution, expected_tool=target),
                system_prompt=MASTER_SYSTEM_PROMPT + "\nДоверенный контекст текущего запроса: ИНН " + inn,
                state_schema=ConversationState, checkpointer=self.conversation_store.checkpointer,
                middleware=[_model_policy(self.model_timeout_s, execution, target, self.registry.get_definition(target).input_model),
                            ModelCallLimitMiddleware(run_limit=MAX_MODEL_CALLS, exit_behavior="error"),
                            ToolCallLimitMiddleware(run_limit=MAX_TOOL_CALLS, exit_behavior="error")],
                name="counterparty_master_agent",
            )
            try:
                state = await agent.ainvoke({"messages": [HumanMessage(content=message.strip())]}, config=config)
                final = state["messages"][-1]
                if isinstance(final, AIMessage) and not final.tool_calls:
                    try:
                        synthesis = json.loads(final.content)
                    except (TypeError, ValueError):
                        synthesis = {"invalid_model_synthesis": True}
            except Exception as exc:  # noqa: BLE001
                log.info("agent_model_fallback run_id=%s reason=%s", run_id, type(exc).__name__)
                synthesis = {"invalid_model_synthesis": True}
        if execution.result is None:
            if execution.started:
                return runtime_timeout_response(run_id, started, tool_calls=execution.tool_calls)
            execution.started = True
            execution.tool_calls = 1
            execution.used_fallback = True
            log.info("agent_tool_call run_id=%s tool=%s inn=%s routing=deterministic_fallback call=1/1",
                     run_id, target, inn)
            execution.result = await self.registry.execute(target, {"inn": inn}, self.tool_context)
            log.info("agent_tool_result run_id=%s tool=%s status=%s latency_ms=%s routing=deterministic_fallback",
                     run_id, target, execution.result.status, execution.result.metadata.latency_ms)
        response = tool_result_to_assistant(
            execution.result, agent_run_id=run_id,
            routing="deterministic_fallback" if execution.used_fallback else "model",
            model=self.model_name, started=started, synthesis=synthesis,
        )
        return response


def _model_policy(timeout_s, execution, expected_tool, input_model):
    @wrap_model_call
    async def enforce(request, handler):
        if execution.model_calls >= MAX_MODEL_CALLS:
            raise RuntimeError("Model call budget exhausted")
        after_tool = execution.result is not None
        settings = dict(request.model_settings)
        settings["parallel_tool_calls"] = False
        overrides = {"tool_choice": "none" if after_tool else "required", "model_settings": settings}
        if after_tool:
            # This finite schema is built from backend findings, never model output.
            findings = execution.result.data.get("findings", [])
            allowed_ids = [item["id"] for item in findings if isinstance(item, dict) and isinstance(item.get("id"), str)][:10]
            schema = {
                "type": "object", "additionalProperties": False,
                "required": ["finding_ids"],
                "properties": {"finding_ids": {
                    "type": "array", "uniqueItems": True,
                    "minItems": 1 if allowed_ids else 0, "maxItems": len(allowed_ids),
                    "items": {"type": "string", "enum": allowed_ids} if allowed_ids else {"type": "string"},
                }},
            }
            base = request.system_message.content if request.system_message else ""
            overrides["system_message"] = SystemMessage(content=str(base) +
                "\nСхема финального ответа для ТЕКУЩЕГО ToolResult: " +
                json.dumps(schema, ensure_ascii=False, separators=(",", ":")) +
                "\nВыбирай только перечисленные id. При непустом наборе выбери хотя бы одно наблюдение. "
                "Возвращай только JSON, без Markdown и пояснений.")
        bounded = request.override(**overrides)
        execution.model_calls += 1
        response = await asyncio.wait_for(handler(bounded), timeout=timeout_s)
        messages = response.result
        proposal = messages[-1] if messages else None
        if not isinstance(proposal, AIMessage):
            raise ValueError("Invalid model message")
        usage = proposal.usage_metadata or {}
        for key in ("input_tokens", "output_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and value >= 0:
                setattr(execution, key, (getattr(execution, key) or 0) + value)
        if not after_tool:
            calls = proposal.tool_calls
            if len(calls) != 1 or calls[0]["name"] != expected_tool:
                raise ValueError("Invalid native tool proposal")
            input_model.model_validate(calls[0]["args"])
        elif proposal.tool_calls:
            raise ValueError("Repeated native tool proposal")
        return response
    return enforce


def requested_tool(message: str) -> Optional[str]:
    # Small deterministic admission/fallback policy; no second agent/router loop.
    if re.search(r"\b(?:сравн\w*|compare\w*)\b", message, re.I):
        return None
    if FULL_PHRASE_RE.search(message):
        return "full_company_check"
    finance, legal = bool(FINANCE_TOPIC_RE.search(message)), bool(LEGAL_TOPIC_RE.search(message))
    if finance and legal:
        return None
    if finance:
        return "get_financial_data"
    if legal:
        return "get_legal_data"
    return "full_company_check" if is_full_check_request(message) else None


def build_master_runtime(settings: Settings, client: GroqClient, *, persist: bool = True,
                         conversation_store: Optional[ConversationStore] = None) -> MasterAgentRuntime:
    model = build_master_model(settings) if client.enabled else None
    return MasterAgentRuntime(model=model, model_name=settings.groq_master_model if model else None,
        registry=build_tool_registry(settings), tool_context=ToolContext(settings=settings, client=client, persist=persist),
        model_timeout_s=settings.agent_model_timeout_s, run_timeout_s=settings.agent_run_timeout_s,
        conversation_store=conversation_store)


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


def inspect_request(message: str) -> Tuple[Optional[str], Optional[str]]:
    text = message or ""
    sequences = DIGIT_SEQUENCE_RE.findall(text)
    # Reporting years and short quantities are not company identifiers.
    candidates = [value for value in sequences if len(value) >= 8]
    explicit = re.findall(r"\bинн\s*[:№#-]?\s*([0-9]+)", text, re.I)
    candidates = sorted(set(candidates + explicit))
    if not candidates:
        return "missing_inn", None
    valid = [value for value in candidates if is_valid_inn(value)]
    if len(valid) > 1:
        return "ambiguous_inn", None
    if len(valid) != len(candidates):
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

"""LangChain adapter; validated domain artifacts never come from model text."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from langchain_core.tools import StructuredTool

from .models import ToolResult
from .synthesis import normalized_tool_context
from .tools import ToolContext, ToolRegistry

log = logging.getLogger(__name__)


@dataclass
class LangChainToolExecution:
    started: bool = False
    tool_calls: int = 0
    model_calls: int = 0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    used_fallback: bool = False
    result: Optional[ToolResult] = None
    # Per-turn external provenance, never part of trusted domain context.
    news_requested: bool = False
    news_annotations: list = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


def build_langchain_tools(
    registry: ToolRegistry, tool_context: ToolContext, *, agent_run_id: str,
    expected_inn: Optional[str] = None, execution: LangChainToolExecution,
    expected_tool: str = "full_company_check",
    expected_inns: Optional[List[str]] = None,
    detail_args: Optional[dict] = None,
) -> List[StructuredTool]:
    """Идентификаторы компаний остаются за бэкендом: аргумент модели только сверяется."""
    definition = registry.get_definition(expected_tool)
    if definition is None:
        raise ValueError("Tool отсутствует в domain registry")

    async def reserve() -> None:
        async with execution.lock:
            if execution.started:
                raise RuntimeError("Domain tool call budget exhausted")
            execution.started = True
            execution.tool_calls = 1

    async def run(arguments: Dict[str, object], subject: str) -> tuple[str, Dict[str, object]]:
        log.info("agent_tool_call run_id=%s tool=%s inn=%s routing=%s call=1/1",
                 agent_run_id, expected_tool, subject,
                 "deterministic_fallback" if execution.used_fallback else "model")
        result = await registry.execute(expected_tool, arguments, tool_context)
        execution.result = result
        log.info("agent_tool_result run_id=%s tool=%s status=%s latency_ms=%s",
                 agent_run_id, expected_tool, result.status, result.metadata.latency_ms)
        return _observation(result)

    async def execute(inn: str, section: str = "default", year: Optional[int] = None, offset: int = 0) -> tuple[str, Dict[str, object]]:
        await reserve()
        if inn != expected_inn:
            execution.used_fallback = True
        arguments = {"inn": expected_inn}
        if expected_tool != "full_company_check":
            if section != "default": arguments["section"] = section
            if year is not None: arguments["year"] = year
            if offset: arguments["offset"] = offset
            arguments.update(detail_args or {})
        return await run(arguments, expected_inn)

    async def execute_comparison(
        inns: List[str], focus: str = "both"
    ) -> tuple[str, Dict[str, object]]:
        await reserve()
        if sorted(inns or []) != sorted(expected_inns or []):
            execution.used_fallback = True
        # focus — выбор агента, но только из перечисления контракта.
        selected = focus if focus in {"finance", "legal", "both"} else "both"
        return await run({"inns": list(expected_inns or []), "focus": selected},
                         ", ".join(expected_inns or []))

    coroutine = execute_comparison if expected_tool == "compare_companies" else execute
    return [StructuredTool.from_function(
        coroutine=coroutine, name=definition.name, description=definition.description,
        args_schema=definition.input_model, response_format="content_and_artifact",
        return_direct=False,
    )]


def _observation(result: ToolResult) -> tuple[str, Dict[str, object]]:
    payload = result.model_dump(mode="json")
    # The model sees normalized verified data. The full ToolResult remains a
    # backend-owned artifact for hydration and never becomes model-authored UI.
    observation = (
        normalized_tool_context(result)
        if result.status != "error"
        else {"tool": result.metadata.tool, "status": "error", "error": payload.get("error")}
    )
    return json.dumps(observation, ensure_ascii=False, separators=(",", ":")), payload

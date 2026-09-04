"""LangChain adapter; validated domain artifacts never come from model text."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from langchain_core.tools import StructuredTool

from .models import ToolResult
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
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


def build_langchain_tools(
    registry: ToolRegistry, tool_context: ToolContext, *, agent_run_id: str,
    expected_inn: str, execution: LangChainToolExecution,
    expected_tool: str = "full_company_check",
) -> List[StructuredTool]:
    definition = registry.get_definition(expected_tool)
    if definition is None:
        raise ValueError("Tool отсутствует в domain registry")

    async def execute(inn: str) -> tuple[str, Dict[str, object]]:
        async with execution.lock:
            if execution.started:
                raise RuntimeError("Domain tool call budget exhausted")
            execution.started = True
            execution.tool_calls = 1
        if inn != expected_inn:
            execution.used_fallback = True
        log.info("agent_tool_call run_id=%s tool=%s inn=%s routing=%s call=1/1",
                 agent_run_id, expected_tool, expected_inn,
                 "deterministic_fallback" if execution.used_fallback else "model")
        result = await registry.execute(expected_tool, {"inn": expected_inn}, tool_context)
        execution.result = result
        log.info("agent_tool_result run_id=%s tool=%s status=%s latency_ms=%s",
                 agent_run_id, expected_tool, result.status, result.metadata.latency_ms)
        return _observation(result)

    return [StructuredTool.from_function(
        coroutine=execute, name=definition.name, description=definition.description,
        args_schema=definition.input_model, response_format="content_and_artifact",
        return_direct=False,
    )]


def _observation(result: ToolResult) -> tuple[str, Dict[str, object]]:
    payload = result.model_dump(mode="json")
    # The large full-check is still hydrated by its established response adapter.
    # Targeted capabilities already provide a compact, domain-only payload.
    if result.metadata.tool == "full_company_check":
        observation = {"tool": result.metadata.tool, "status": result.status,
                       "company": result.data.get("company"), "findings": [],
                       "warnings": result.warnings,
                       "error": payload.get("error")}
    else:
        observation = payload
    return json.dumps(observation, ensure_ascii=False, separators=(",", ":")), payload

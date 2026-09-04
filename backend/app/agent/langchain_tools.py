"""Тонкий LangChain adapter над framework-agnostic domain tool registry."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool

from .models import ToolResult
from .tools import ToolContext, ToolRegistry

log = logging.getLogger(__name__)


@dataclass
class LangChainToolExecution:
    """Backend-owned состояние одного запуска, не conversation memory."""

    started: bool = False
    tool_calls: int = 0
    used_fallback: bool = False
    result: Optional[ToolResult] = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


def build_langchain_tools(
    registry: ToolRegistry,
    tool_context: ToolContext,
    *,
    agent_run_id: str,
    expected_inn: str,
    execution: LangChainToolExecution,
) -> List[StructuredTool]:
    """Экспортирует allowlisted domain tool в native LangChain interface."""
    definition = registry.get_definition("full_company_check")
    if definition is None:
        raise ValueError("full_company_check отсутствует в domain Tool Registry")

    async def full_company_check(inn: str) -> tuple[str, Dict[str, object]]:
        async with execution.lock:
            if execution.started:
                log.warning(
                    "agent_tool_call_rejected run_id=%s tool=full_company_check "
                    "reason=tool_call_limit",
                    agent_run_id,
                )
                duplicate = await registry.execute(
                    "full_company_check", {}, tool_context
                )
                return _observation(duplicate)
            execution.started = True
            execution.tool_calls = 1

        # Канонический ИНН определяет deterministic preflight, а не модель.
        if inn != expected_inn:
            execution.used_fallback = True
        log.info(
            "agent_tool_call run_id=%s tool=full_company_check routing=%s call=1/1",
            agent_run_id,
            "deterministic_fallback" if execution.used_fallback else "model",
        )
        result = await registry.execute(
            "full_company_check", {"inn": expected_inn}, tool_context
        )
        execution.result = result
        log.info(
            "agent_tool_result run_id=%s tool=%s status=%s latency_ms=%s",
            agent_run_id,
            result.metadata.tool,
            result.status,
            result.metadata.latency_ms,
        )
        return _observation(result)

    tool = StructuredTool.from_function(
        coroutine=full_company_check,
        name=definition.name,
        description=definition.description,
        args_schema=definition.input_model,
        response_format="content_and_artifact",
        return_direct=True,
        handle_validation_error=(
            "Аргументы full_company_check не прошли локальную валидацию."
        ),
    )
    return [tool]


def tool_result_from_state(state: object) -> Optional[ToolResult]:
    """Берёт только backend-created artifact; текст модели не доверен."""
    if not isinstance(state, dict):
        return None
    messages = state.get("messages")
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue
        if message.name != "full_company_check" or message.artifact is None:
            continue
        try:
            return ToolResult.model_validate(message.artifact)
        except Exception:  # noqa: BLE001
            return None
    return None


def _observation(result: ToolResult) -> tuple[str, Dict[str, object]]:
    marker: Dict[str, object] = {
        "tool": result.metadata.tool,
        "status": result.status,
    }
    if result.error is not None:
        marker["error_code"] = result.error.code
    return (
        json.dumps(marker, ensure_ascii=False, separators=(",", ":")),
        result.model_dump(mode="json"),
    )

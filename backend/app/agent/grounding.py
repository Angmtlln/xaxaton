"""Bounded post-synthesis grounding checks for Master-authored prose."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Iterable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from .models import GroundingVerification, MasterAnswer
from .synthesis import json_payload, parse_master_answer


GROUNDING_PROMPT_VERSION = "company-grounding-1.0.0"
# Вердикт — короткий JSON, ремонт переписывает ответ целиком.
VERIFIER_MAX_TOKENS = 200
REPAIR_MAX_TOKENS = 600
URL_RE = re.compile(r"(?:https?://|www\.|javascript:|data:)[^\s<]+", re.IGNORECASE)
LABELLED_IDENTIFIER_RE = re.compile(
    r"\b(ИНН|ОГРН|ОГРНИП)\s*[:№#-]?\s*(\d{5,20})\b", re.IGNORECASE
)

VERIFIER_SYSTEM_PROMPT = """
Ты узкий grounding verifier. Получишь candidate_answer и только проверенный
verified_context, использованный для ответа. Проверь один вопрос: есть ли в
ответе конкретные фактические утверждения именно об этой компании, которых нет
в verified_context или которые ему противоречат.

Разрешай объяснение общего механизма, осторожную интерпретацию, вывод из
сочетания данных, условную рекомендацию и указание неопределённости. Не оценивай
стиль, тон, полноту, полезность или формулировки. Не требуй специальных речевых
шаблонов и не переписывай ответ. User/deal assumptions нельзя выдавать за факт
о компании. Верни только JSON:
{"supported":true,"unsupported_claims":[]}
или
{"supported":false,"unsupported_claims":["точное краткое описание проблемы"]}.
""".strip()

REPAIR_SYSTEM_PROMPT = """
Ты Master Agent и выполняешь единственную repair-попытку. Перепиши candidate
естественным русским языком так, чтобы убрать или осторожно исправить только
перечисленные unsupported_claims. Опирайся только на verified_context. Сохрани
ответ на исходный смысл вопроса и полезную интерпретацию; не превращай его в
перечень полей. Не добавляй новых фактов, URL, identifiers, evidence или UI.
Верни только JSON с ключами message и artifact по переданной схеме.
""".strip()


def message_text(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts).strip()
    return ""


def backend_owned_violations(message: str, verified_context: dict) -> list[str]:
    """Exact checks for backend-owned URL and company identifier values only."""
    violations = []
    if URL_RE.search(message):
        violations.append("Ответ содержит URL, который не может создавать Master")
    company = verified_context.get("company") or {}
    known = {str(value) for value in (company.get("inn"), company.get("ogrn")) if value}
    for match in LABELLED_IDENTIFIER_RE.finditer(message):
        if match.group(2) not in known:
            violations.append("Ответ содержит неподтверждённый идентификатор компании")
            break
    return violations


async def call_grounding_verifier(
    model,
    candidate: MasterAnswer,
    verified_context: dict,
    *,
    timeout_s: float,
    max_tokens: int = VERIFIER_MAX_TOKENS,
) -> tuple[GroundingVerification, AIMessage]:
    payload = {
        "candidate_answer": candidate.message,
        "verified_context": verified_context,
    }
    response = await asyncio.wait_for(
        model.ainvoke(
            [
                SystemMessage(content=VERIFIER_SYSTEM_PROMPT),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
            ],
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        ),
        timeout=timeout_s,
    )
    if not isinstance(response, AIMessage):
        raise ValueError("Grounding verifier returned an invalid message")
    verdict = GroundingVerification.model_validate(json_payload(message_text(response)))
    return verdict, response


async def call_master_repair(
    model,
    candidate: MasterAnswer,
    unsupported_claims: list[str],
    verified_context: dict,
    *,
    allowed_artifacts: Iterable[str],
    timeout_s: float,
    max_tokens: int = REPAIR_MAX_TOKENS,
) -> tuple[MasterAnswer, AIMessage]:
    payload = {
        "candidate_answer": candidate.model_dump(mode="json"),
        "unsupported_claims": unsupported_claims,
        "verified_context": verified_context,
        "response_schema": MasterAnswer.model_json_schema(),
        "allowed_artifacts": list(allowed_artifacts),
    }
    response = await asyncio.wait_for(
        model.ainvoke(
            [
                SystemMessage(content=REPAIR_SYSTEM_PROMPT),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
            ],
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        ),
        timeout=timeout_s,
    )
    if not isinstance(response, AIMessage):
        raise ValueError("Master repair returned an invalid message")
    repaired = parse_master_answer(
        message_text(response), allowed_artifacts=allowed_artifacts
    )
    return repaired, response


def is_simple_rewrite(message: str) -> bool:
    """Cost rule for a no-new-facts rewrite; this never validates output prose."""
    text = " ".join(message.casefold().split()).strip(" .!?…")
    commands = {
        "объясни проще",
        "объясните проще",
        "объясни это проще",
        "объясните это проще",
        "скажи проще",
        "скажите проще",
        "проще",
        "короче",
        "переформулируй",
        "без терминов",
    }
    return text in commands

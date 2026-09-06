"""Exact backend-value checks and optional eval/debug LLM verification."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Iterable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from .models import GroundingVerification, MasterAnswer
from .synthesis import json_payload, parse_master_answer


GROUNDING_PROMPT_VERSION = "company-grounding-1.5.0"
# Вердикт — короткий JSON, ремонт переписывает ответ целиком.
# Reasoning tokens входят в output budget OpenRouter-модели, поэтому
# даже короткий JSON-вердикт получает ограниченный запас.
VERIFIER_MAX_TOKENS = 4096
REPAIR_MAX_TOKENS = 4096
URL_RE = re.compile(r"(?:https?://|www\.|javascript:|data:)[^\s<]+", re.IGNORECASE)
LABELLED_IDENTIFIER_RE = re.compile(
    r"\b(ИНН|ОГРН|ОГРНИП)\s*[:№#-]?\s*(\d{5,20})\b", re.IGNORECASE
)

VERIFIER_SYSTEM_PROMPT = """
Ты узкий grounding verifier. Получишь candidate_answer, проверенный
verified_context и user_context со словами пользователя. Проверь:
1. нет ли конкретных фактических утверждений о компании, которых нет в
   verified_context или которые ему противоречат;
2. соответствует ли сила утверждения силе данных: причинная связь, неизбежность,
   вероятность неплатежа/банкротства или иной исход не становятся фактом только
   из сочетания косвенных показателей;
3. не дана ли конкретная рекомендация по условиям сделки без минимального
   контекста о роли контрагента и действии пользователя;
4. верны ли заявленные числовые сравнения и присутствует ли каждое названное
   значение в текущем verified_context или в явных словах пользователя.

Считай unsupported, если candidate категорично утверждает, что компания живёт
на заёмные/чужие деньги, обязательно не сможет платить, при любом сбое рухнет
или близка к банкротству, когда verified_context содержит лишь высокую
кредиторскую задолженность, низкий капитал или резкую динамику. Эти же сценарии
можно допустить только как явно обозначенную гипотезу с указанием, чего не
хватает для проверки. Блокировка счетов — подтверждённый сигнал, но её полный
операционный охват и будущий исход нельзя додумывать.
Проверяй каждую клаузу отдельно. Последующая оговорка «это не приговор», «так
бывает» или осторожная гипотеза о другом исходе не делает предшествующий
категорический причинный тезис supported. Например, фраза «тонкий капитал и
кредиторка показывают, что компания работает на заёмных ресурсах» unsupported
даже если следующий текст осторожный: происхождение обязательств не раскрыто.
Нулевая выручка одного периода не подтверждает, что компания не работала или
была «спящей». Отсутствие дел в роли истца не подтверждает мотивы компании,
качество её дебиторов или отсутствие взыскания долгов. Если процента, суммы или
сравнения нет в verified_context, проверь вычисление по доступным значениям;
ошибочное или невоспроизводимое сравнение unsupported.
Предъявленные исковые требования не являются установленным долгом до исхода
дела: «компания уже должна сумму исков» unsupported. Даже подтверждённая метка
блокировки без её актуального охвата не подтверждает, что компания физически не
может провести любой платёж; категорическое утверждение об этом unsupported.

Если пользователь просит решить, работать ли с компанией, но не назвал свою
роль и действие, candidate должен остановиться на одном коротком уточняющем
вопросе. Ветвящиеся советы для вымышленных ролей, обязательная предоплата,
запрет аванса/отсрочки и придуманные числовые пороги в таком ответе unsupported.
На общий вопрос «Насколько это критично?» без условий сделки разреши описание
значимости, но считай unsupported собственную формальную категорию риска и
конкретную схему оплаты. Не путай зафиксированный банковский LOW или policy
hard-stop с новым интегральным score Master.
Если пользователь лишь просит объяснить прежний ответ, незапрошенный вопрос об
условиях сделки не нужен; не считай его дефектом grounding сам по себе, но при
repair не добавляй такую анкету.

Разрешай общую оценку значимости сигнала, осторожную интерпретацию, явно
обозначенную гипотезу, условную рекомендацию при достаточном user_context и
указание неопределённости. Не оценивай стиль, тон, полноту или полезность, не
требуй специальных речевых шаблонов и не переписывай ответ. User/deal
assumptions нельзя выдавать за факт о компании. Верни только JSON:
{"supported":true,"unsupported_claims":[]}
или
{"supported":false,"unsupported_claims":["точное краткое описание проблемы"]}.
""".strip()

REPAIR_SYSTEM_PROMPT = """
Ты Master Agent и выполняешь единственную repair-попытку. Перепиши candidate
естественным русским языком так, чтобы убрать или осторожно исправить только
перечисленные unsupported_claims. Опирайся на verified_context и отделяй слова
пользователя из user_context от фактов о компании. Сохрани ответ на исходный
смысл вопроса и полезную интерпретацию; если для конкретной рекомендации по
сделке не хватает роли или действия пользователя, не предлагай сценариев за
него: кратко обозначь зависимость решения от контекста и задай ровно один
короткий уточняющий вопрос. Не превращай ответ в перечень полей и не добавляй новых
фактов, URL, identifiers, evidence или UI.
Не повторяй отвергнутый тезис синонимами и не пытайся исправить категоричность
оговоркой в соседнем предложении. Если кредиторская задолженность и капитал не
раскрывают источник финансирования, оставь только наблюдение об обязательствах
и небольшой бухгалтерской подушке; причину назови непроверенной гипотезой либо
не называй совсем. Перед JSON повторно примени правила силы утверждения из
unsupported_claims ко всему переписанному ответу.
Не превращай сумму исков в признанный долг и не утверждай невозможность любого
платежа из одной метки блокировки. Для объясняющего follow-up не добавляй вопрос
об условиях сделки, если пользователь ещё не просил принять решение.
Верни только один JSON-объект с ключами message и artifact по переданной схеме.
Значение message — обычный русский текст ответа, не вложенный JSON.
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
    companies = [verified_context.get("company") or {},
                 *verified_context.get("companies", []),
                 *(verified_context.get("connections") or {}).get("nodes", [])]
    known = {str(company[key]) for company in companies for key in ("inn", "ogrn")
             if company.get(key)}
    known.update(str(e["via"]) for e in (verified_context.get("connections") or {}).get("edges", [])
                 if e.get("via") and str(e["via"]).isdigit())
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
    user_context: list[str] | None = None,
    reasoning_effort: str | None = None,
    timeout_s: float,
    max_tokens: int = VERIFIER_MAX_TOKENS,
) -> tuple[GroundingVerification, AIMessage]:
    payload = {
        "candidate_answer": candidate.message,
        "verified_context": verified_context,
        "user_context": list(user_context or []),
    }
    invoke_options = {
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    if reasoning_effort:
        invoke_options["extra_body"] = {
            **(getattr(model, "extra_body", None) or {}),
            "reasoning": {"effort": reasoning_effort},
        }
    response = await asyncio.wait_for(
        model.ainvoke(
            [
                SystemMessage(content=VERIFIER_SYSTEM_PROMPT),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
            ],
            **invoke_options,
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
    user_context: list[str] | None = None,
    allowed_artifacts: Iterable[str],
    timeout_s: float,
    max_tokens: int = REPAIR_MAX_TOKENS,
) -> tuple[MasterAnswer, AIMessage]:
    payload = {
        "candidate_answer": candidate.model_dump(mode="json"),
        "unsupported_claims": unsupported_claims,
        "verified_context": verified_context,
        "user_context": list(user_context or []),
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

"""One bounded name lookup before the existing INN-based analytical routing."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import re
import time
import uuid

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware, wrap_model_call
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool

from app.domain.company_search import CompanySearchArgs, CompanySearchResult
from app.infrastructure import repository
from app.mcp_data.errors import CompanySourceError
from .models import CompanyChoiceBlock, is_valid_inn
from .response import guard_response

log = logging.getLogger(__name__)
NAME_RESOLUTION_PROMPT = """Разреши упоминание компании перед анализом.
Если пользователь явно называет одну компанию, вызови search_companies один раз.
query — дословный непрерывный фрагмент ТЕКУЩЕГО сообщения с названием, без вопроса,
без @, без изменения падежа или исправления названия. Не придумывай ИНН.
Название новой компании важнее ранее активной компании. Одно название без вопроса
тоже требует поиска. «Найди Электролид» — поиск названия, а подбор по деятельности,
ОКВЭД, финансовым критериям без конкретного названия — не поиск названия.
Если названия нет, НЕ вызывай инструмент, ответь только JSON:
{"resolution":"continue"} — обычный вопрос, подбор, приветствие или продолжение;
{"resolution":"needs_company"} — пользователь просит ДРУГУЮ/новую компанию, но не называет её;
{"resolution":"multiple"} — явно названы несколько компаний (включая сравнение).
Примеры:
Пользователь: Какая выручка у Электролид?
Действие: search_companies(query="Электролид")
Пользователь: Есть ли суды у СКВ СПБ?
Действие: search_companies(query="СКВ СПБ")
Пользователь: Какая выручка у Строй?
Действие: search_companies(query="Строй") — даже неполное название ищем в базе.
Пользователь: Проверь Ромашка
Действие: search_companies(query="Ромашка")
Пользователь: Какая выручка?
Ответ: {"resolution":"continue"}
Пользователь: Проверь другую компанию
Ответ: {"resolution":"needs_company"}
Название может быть без кавычек и ООО. «У Электролид» содержит название;
не отвечай needs_company, если название прямо присутствует в вопросе.
История и название активной компании не являются аргументами поиска. Не отвечай
на исходный аналитический вопрос: следующий шаг выполнит его после выбора ИНН.
"""


@dataclass
class Resolution:
    message: str
    response: object = None
    pending: dict | None = None
    model_calls: int = 0
    search_calls: int = 0


def _resolved_message(message, query, inn):
    match = re.search(re.escape(query), message, re.I)
    if not match:
        raise ValueError("Name must occur in the user message")
    if message.strip().strip('@«»" .!?') == query.strip().strip('«»" .!?'):
        return f"Проверь контрагента {inn}"
    return message[:match.start()].rstrip('@') + f"ИНН {inn}" + message[match.end():]


async def resolve_name(runtime, message, previous, model, run_id, started, deadline, selection=None):
    from .runtime import inspect_request, is_shortlist_request
    result = Resolution(message)
    pending = previous.get("pending_company_search")

    def reply(text, code=None):
        response = guard_response("missing_inn", run_id, started)
        response.message = text
        response.suggested_actions = []
        response.metadata.error_code = code
        response.metadata.model = runtime.model_name
        response.metadata.model_calls = result.model_calls
        response.metadata.tool_calls = result.search_calls
        result.response = response
        return result

    # A structured click is bound to the current search; a stale click cannot
    # fall through into an unrelated INN check.
    candidate_inn = None
    choice = re.fullmatch(r"(?:инн\s*)?([0-9]{10}(?:[0-9]{2})?)[.!]?", message.strip(), re.I)
    ordinal = re.fullmatch(r"(?:выбираю\s+)?(перв(?:ая|ую)|втор(?:ая|ую)|треть(?:я|ю)|четв[её]рт(?:ая|ую)|пят(?:ая|ую)|[1-5])[.!]?", message.strip(), re.I)
    if selection:
        if not pending or selection["search_id"] != pending["search_id"]:
            result.pending = pending
            return reply("Этот список выбора уже не актуален. Повторите поиск по названию.", "stale_company_choice")
        candidate_inn = selection["inn"]
    elif pending and choice:
        candidate_inn = choice[1]
    elif pending and ordinal:
        word = ordinal[1].lower()
        index = int(word) - 1 if word.isdigit() else next(i for i, prefix in enumerate(("перв", "втор", "треть", "четв", "пят")) if word.startswith(prefix))
        if index < len(pending["rows"]):
            candidate_inn = pending["rows"][index]["inn"]
    if selection or (pending and (choice or ordinal)):
        row = next((row for row in pending["rows"] if row["inn"] == candidate_inn), None)
        if row is None or not is_valid_inn(candidate_inn):
            result.pending = pending
            return reply("Выберите компанию из последнего списка или начните новый поиск по названию.", "invalid_company_choice")
        result.message = _resolved_message(pending["message"], pending["query"], candidate_inn)
        return result
    if ordinal:
        return reply("Нет актуального списка компаний. Введите название или ИНН.", "stale_company_choice")

    # Explicit identifiers retain all existing guards, including invalid INNs.
    if inspect_request(message)[0] != "missing_inn":
        return result
    # Only complete, unambiguous contextual commands skip model and data access.
    if re.fullmatch(r"\s*(?:почему|почему эти|объясни проще|объясни|что это значит|насколько это критично)[?!.\s]*", message, re.I):
        if pending:
            result.pending = pending
            reply("Сначала нужно подтвердить компанию. Выберите вариант по ИНН или адресу — затем продолжу исходный запрос.")
            result.response.blocks = [CompanyChoiceBlock(search_id=pending["search_id"],
                rows=pending["rows"], total=pending.get("total", len(pending["rows"])))]
        return result

    if is_shortlist_request(message):
        return result
    if re.search(r"\bкак\s+ты\s+можешь\s+помочь\b|\bчто\s+ты\s+умеешь\b", message, re.I):
        return result
    if previous.get("active_company") and re.match(
        r"\s*(?:но\b|тогда\b|значит\b|а\s+(?:это|если|сколько|почему)\b|связь\b)",
        message, re.I,
    ):
        return result
    if previous.get("active_company") and re.search(
        r"\b(?:граф|схем)\w*\s+связ|(?:отч[её]т|анализ).*?(?:связанн|соседн)",
        message, re.I,
    ):
        return result
    if model is None and re.fullmatch(r"\s*(?:а что у них с (?:финансами|судами)|покажи (?:финансы|суды)|привет|здравствуйте)[?!.\s]*", message, re.I):
        return result

    found = None
    source_exception = None
    query = None

    async def search_companies(query: str, limit: int = 5):
        nonlocal found, source_exception
        if result.search_calls:
            raise ValueError("Only one company lookup per turn")
        if not re.search(re.escape(query), message, re.I):
            raise ValueError("Search name must be copied from the current message")
        result.search_calls += 1
        try:
            found = (query, CompanySearchResult.model_validate(
                await repository.search_companies(query=query, limit=limit)))
        except CompanySourceError as exc:
            source_exception = exc
            raise
        return found[1].model_dump_json()

    try:
        if model is None:
            # Explicit @ input remains usable during provider outages.
            mention = re.fullmatch(r"\s*@([^@]{2,256})\s*", message)
            if not mention:
                return reply("Для поиска по названию выберите подсказку или укажите ИНН. Модель сейчас недоступна.", "name_resolution_unavailable")
            query = mention[1].strip()
            await search_companies(query)
        else:
            @wrap_model_call
            async def bounded(request, handler):
                result.model_calls += 1
                settings = {**request.model_settings, "max_tokens": 768, "parallel_tool_calls": False}
                return await asyncio.wait_for(handler(request.override(model_settings=settings)),
                                              timeout=min(runtime.model_timeout_s, max(0, deadline-time.monotonic())))

            tool = StructuredTool.from_function(coroutine=search_companies, name="search_companies",
                description="Найти одну явно названную компанию в загруженной базе; query скопировать из сообщения.",
                args_schema=CompanySearchArgs, return_direct=True)
            agent = create_agent(model=model, tools=[tool], system_prompt=NAME_RESOLUTION_PROMPT,
                middleware=[bounded, ModelCallLimitMiddleware(run_limit=1, exit_behavior="error"),
                            ToolCallLimitMiddleware(run_limit=1, exit_behavior="error")])
            state = await asyncio.wait_for(agent.ainvoke({"messages": [HumanMessage(content=message)]},
                config={"recursion_limit": 6}), timeout=max(0, deadline-time.monotonic()))
            if source_exception is not None:
                raise source_exception
            if found is None:
                final = state["messages"][-1]
                decision = json.loads(final.content) if isinstance(final, AIMessage) else {}
                if decision.get("resolution") == "continue":
                    return result
                if decision.get("resolution") == "multiple":
                    return reply("По названию пока можно выбрать одну компанию за запрос. Для сравнения укажите ИНН компаний.")
                if decision.get("resolution") == "needs_company":
                    return reply("Укажите название или ИНН другой компании.")
                raise ValueError("Invalid name-resolution decision")
        query, matches = found
        if matches.exact_total == 1:
            row = next(row for row in matches.rows if row.match == "exact")
            if not is_valid_inn(row.inn):
                raise ValueError("Invalid source INN")
            result.message = _resolved_message(message, query, row.inn)
            return result
        if not matches.total:
            return reply("В нашей загруженной базе совпадений нет. Уточните название или укажите ИНН.", "company_name_not_found")
        result.pending = {"search_id": str(uuid.uuid4()), "message": message, "query": query,
                          "rows": [row.model_dump() for row in matches.rows], "total": matches.total}
        reply("Выберите нужную компанию." + (f" Найдено {matches.total}, показаны первые пять. Если нужной нет, уточните название или укажите ИНН." if matches.total > 5 else ""))
        result.response.blocks = [CompanyChoiceBlock(search_id=result.pending["search_id"],
            rows=matches.rows, total=matches.total)]
        return result
    except CompanySourceError as exc:
        reply(exc.message, exc.code)
        result.response.metadata.status = "error"
        return result
    except Exception as exc:
        log.info("company_name_resolution_failed run_id=%s reason=%s", run_id, type(exc).__name__)
        return reply("Не удалось определить компанию по названию. Выберите подсказку или укажите ИНН и повторите запрос.", "name_resolution_unavailable")
    finally:
        log.info("company_name_resolution run_id=%s model_calls=%s search_calls=%s duration_ms=%s",
                 run_id, result.model_calls, result.search_calls, int((time.perf_counter()-started)*1000))

"""Четыре блочных агента и Summary-LLM.

Схема прохода: детерминированные факты → 4 параллельных агента (каждый
видит только свой блок) → Summary-LLM поверх четырёх резюме.

Поверх ответа модели работают два защитных слоя:
  * grounding  — fact_id из ответа сверяется с реестром фактов, ссылка
                 на несуществующий факт помечается UNVERIFIED (S5);
  * guardrails — сигнал блока и итоговая группа не могут быть мягче
                 жёстких фактов, посчитанных кодом (H3).
"""
import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.config import Settings
from app.domain.facts import BLOCK_KEYS, BLOCK_TITLES, Fact, FactBlock
from app.llm import prompts
from app.llm.groq_client import GroqClient, LLMError, LLMResponse

log = logging.getLogger(__name__)

SIGNALS = ("NORM", "ATTENTION", "RISK", "NO_DATA")
SIGNAL_RANK = {"NO_DATA": 0, "NORM": 1, "ATTENTION": 2, "RISK": 3}
VERDICTS = ("STOP", "ENHANCED_CHECK", "CONDITIONALLY_OK", "NO_DATA")
SEVERITIES = ("high", "medium", "low")
SUMMARY_POINT_MIN = 2
SUMMARY_POINT_MAX = 3
SUMMARY_POINT_CHAR_LIMIT = 135
SUMMARY_POINTS_TOTAL_LIMIT = 360
SUMMARY_SAFE_FALLBACK_POINT = "Перед сделкой проверьте факты и пробелы данных в подробных блоках."
SUMMARY_ALT_FALLBACK_POINT = "Откройте подробные блоки, чтобы сопоставить вывод с исходными фактами."


@dataclass
class BlockResult:
    block: str
    signal: str = "NO_DATA"
    headline: str = ""
    facts_sentence: str = ""
    interpretation: str = ""
    findings: List[Dict[str, Any]] = field(default_factory=list)
    data_gaps: List[str] = field(default_factory=list)
    cannot_assess: List[str] = field(default_factory=list)
    facts_input: List[Dict[str, Any]] = field(default_factory=list)
    model: str = ""
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw_response: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    degraded: bool = False

    @property
    def title(self) -> str:
        return BLOCK_TITLES.get(self.block, self.block)

    def to_summary_input(self) -> Dict[str, Any]:
        return {
            "блок": self.title,
            "block_key": self.block,
            "сигнал": self.signal,
            "заголовок": self.headline,
            "факты": self.facts_sentence,
            "интерпретация": self.interpretation,
            "наблюдения": self.findings,
            "нет_данных": self.data_gaps,
            "невозможно_оценить": self.cannot_assess,
        }


@dataclass
class SummaryResult:
    verdict_group: str = "NO_DATA"
    headline: str = ""
    narrative: str = ""
    narrative_points: List[str] = field(default_factory=list)
    key_numbers: List[Dict[str, Any]] = field(default_factory=list)
    top_risks: List[Dict[str, Any]] = field(default_factory=list)
    positives: List[Dict[str, Any]] = field(default_factory=list)
    data_gaps: List[str] = field(default_factory=list)
    questions_to_ask: List[str] = field(default_factory=list)
    model: str = ""
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw_response: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    degraded: bool = False


# ------------------------------ утилиты ------------------------------

# Бесплатный тариф Groq ограничен токенами в минуту, а четыре агента идут
# параллельно. Списки в фактах обрезаем: модели для вывода хватает
# нескольких примеров, полный список остаётся в ответе API и на экране.
LLM_LIST_LIMIT = 3
LLM_SCALAR_LIST_LIMIT = 8


def compact_for_llm(fact: Dict[str, Any]) -> Dict[str, Any]:
    """Урезанная копия факта для промпта. Значения не искажаются, только
    длинные перечисления сворачиваются с пометкой, сколько осталось."""
    out = dict(fact)
    value = out.get("value")
    if isinstance(value, list) and value:
        scalars = all(not isinstance(v, (dict, list)) for v in value)
        limit = LLM_SCALAR_LIST_LIMIT if scalars else LLM_LIST_LIMIT
        if len(value) > limit:
            out["value"] = value[:limit]
            out["value_truncated"] = "показаны %d из %d, полный список в данных отчёта" % (
                limit, len(value))
    return out


def facts_for_llm(facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [compact_for_llm(f) for f in facts]

def _text(value: Any, limit: int = 600) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = " ".join(str(v) for v in value)
    return str(value).strip()[:limit]


def _str_list(value: Any, limit: int = 12) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        value = list(value.values())
    out = []
    for item in list(value)[:limit]:
        if isinstance(item, dict):
            item = item.get("text") or item.get("value") or item.get("label")
        text = _text(item, 300)
        if text:
            out.append(text)
    return out


def _word_chunks(text: str, limit: int) -> List[str]:
    """Делит длинный тезис по границам слов, не добавляя новый смысл."""
    chunks: List[str] = []
    rest = re.sub(r"\s+", " ", text).strip()
    while len(rest) > limit:
        cut = rest.rfind(" ", 0, limit + 1)
        if cut < limit // 2:
            cut = limit
        chunk = rest[:cut].strip(" ,;:-")
        if chunk:
            chunks.append(chunk)
        rest = rest[cut:].strip()
    if rest:
        chunks.append(rest)
    return chunks


def normalize_summary_points(value: Any, legacy_narrative: Any = None) -> List[str]:
    """Нормализует вывод модели в компактные тезисы для одного экрана.

    Сначала принимается новый массив narrative_points. Старое поле narrative,
    лишние элементы и длинные ответы проходят детерминированное разбиение по
    предложениям/словам. Код ограничивает объём независимо от промпта.
    """
    if isinstance(value, (list, tuple)):
        raw = [_text(item, 2000) for item in value]
    elif isinstance(value, str):
        raw = [_text(value, 2000)]
    else:
        raw = []
    raw = [re.sub(r"\s+", " ", item).strip() for item in raw if item and item.strip()]

    if not raw:
        legacy = _text(legacy_narrative, 4000)
        if legacy:
            raw = [re.sub(r"\s+", " ", legacy).strip()]
    if not raw:
        return []

    within_limits = (
        SUMMARY_POINT_MIN <= len(raw) <= SUMMARY_POINT_MAX
        and all(len(item) <= SUMMARY_POINT_CHAR_LIMIT for item in raw)
        and sum(map(len, raw)) <= SUMMARY_POINTS_TOTAL_LIMIT
    )
    if within_limits:
        return raw

    units: List[str] = []
    for item in raw:
        sentences = re.split(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z0-9])", item)
        units.extend(sentence.strip() for sentence in sentences if sentence.strip())

    if len(units) == 1:
        clauses = re.split(
            r"(?<=[,;])\s+|\s+(?=(?:но|поэтому|при этом|перед сделкой)\b)",
            units[0], flags=re.IGNORECASE)
        if len(clauses) > 1:
            units = [clause.strip() for clause in clauses if clause.strip()]

    chunks = (units if len(units) > 1
              else _word_chunks(units[0], SUMMARY_POINT_CHAR_LIMIT))
    points: List[str] = []
    used = 0
    for chunk in chunks:
        if len(points) >= SUMMARY_POINT_MAX:
            break
        available = min(SUMMARY_POINT_CHAR_LIMIT, SUMMARY_POINTS_TOTAL_LIMIT - used)
        if available < 24:
            break
        point = _word_chunks(chunk, available)[0]
        if len(point) < len(chunk):
            point = point.rstrip(" ,;:.-") + "…"
        points.append(point[:available])
        used += len(points[-1])
    if len(points) == 1:
        fallback = (SUMMARY_SAFE_FALLBACK_POINT
                    if points[0] != SUMMARY_SAFE_FALLBACK_POINT
                    else SUMMARY_ALT_FALLBACK_POINT)
        points.append(fallback)
    return points


def fact_value(fact: Fact) -> Any:
    return fact.to_dict()["value"]


def _hard_stop_facts(blocks: Dict[str, FactBlock]) -> List[Dict[str, Any]]:
    """Жёсткие факты, посчитанные кодом. Основание для guardrails."""
    index = {f.id: f for blk in blocks.values() for f in blk.facts}
    hard: List[Dict[str, Any]] = []

    hs = index.get("flags.hard_stop_codes")
    if hs is not None and isinstance(fact_value(hs), list):
        for item in fact_value(hs):
            hard.append({"fact_id": "flags.hard_stop_codes",
                         "text": item.get("meaning") or item.get("code")})

    capitals = index.get("fin.negative_capitals")
    if capitals is not None and fact_value(capitals) is True:
        hard.append({"fact_id": "fin.negative_capitals", "text": "отрицательный собственный капитал"})

    active = index.get("company.is_active")
    if active is not None and fact_value(active) is False:
        hard.append({"fact_id": "company.is_active", "text": "компания не в статусе действующей"})

    return hard


# Факты, поднимающие сигнал. Разделены по домену: сигнал блока
# надёжности не должен расти из-за числа кодов ОКВЭД или убытка — за них
# отвечают свои блоки, а в итоговую группу они входят через scope "all".
ATTENTION_CHECKS = {
    "reliability": [
        ("execproc.active_count", lambda v: isinstance(v, (int, float)) and v > 0,
         "есть действующие исполнительные производства"),
        ("court.defendant_count", lambda v: isinstance(v, (int, float)) and v > 0,
         "компания выступает ответчиком в арбитраже"),
        ("inspections.violations_count", lambda v: isinstance(v, (int, float)) and v > 0,
         "по проверкам выявлены нарушения"),
    ],
    "finance": [
        ("fin.proceeds_drop_20", lambda v: v is True, "выручка снизилась более чем на 20 %"),
        ("fin.proceeds_two_year_decline", lambda v: v is True, "выручка снижается два года подряд"),
        ("fin.has_loss", lambda v: v is True, "есть убыточные годы"),
    ],
    "identity": [
        ("okved.is_many", lambda v: v is True, "много кодов ОКВЭД"),
        ("owners.share_capital_is_minimal", lambda v: v is True, "минимальный уставный капитал"),
    ],
}


def _attention_facts(blocks: Dict[str, FactBlock], scope: str = "all") -> List[Dict[str, Any]]:
    """Факты, требующие уточнения. scope ограничивает домен проверок."""
    index = {f.id: f for blk in blocks.values() for f in blk.facts}
    out: List[Dict[str, Any]] = []

    if scope in ("all", "reliability"):
        att = index.get("flags.attention_codes")
        if att is not None and isinstance(fact_value(att), list):
            for item in fact_value(att):
                out.append({"fact_id": "flags.attention_codes",
                            "text": item.get("meaning") or item.get("code")})

    domains = ATTENTION_CHECKS.keys() if scope == "all" else [scope]
    for domain in domains:
        for fact_id, predicate, text in ATTENTION_CHECKS.get(domain, []):
            fact = index.get(fact_id)
            if fact is not None and predicate(fact_value(fact)):
                out.append({"fact_id": fact_id, "text": text})
    return out


# --------------------------- блочный агент ---------------------------

async def run_block_agent(client: GroqClient, settings: Settings, block: str,
                          fact_block: FactBlock, company: Dict[str, Any],
                          coverage: Dict[str, Any]) -> BlockResult:
    facts = [f.to_dict() for f in fact_block.facts]
    result = BlockResult(block=block, facts_input=facts)

    if not fact_block.has_data and not facts:
        result.signal = "NO_DATA"
        result.headline = "Данных блока «%s» в карточке нет" % fact_block.title
        result.facts_sentence = "Карточка не содержит данных этого блока."
        result.interpretation = "Оценить по этому критерию невозможно."
        result.cannot_assess = fact_block.missing or ["Нет данных блока"]
        result.data_gaps = fact_block.missing
        result.model = "deterministic"
        return result

    if not client.enabled:
        return _mock_block_result(block, fact_block, facts)

    system = prompts.block_system_prompt(block)
    user = prompts.build_block_user_message(
        block, fact_block.title, company, facts_for_llm(facts), fact_block.missing, coverage)

    try:
        response = await client.complete_json(
            model=settings.model_for_block(block), system=system, user=user,
            temperature=settings.block_temperature,
            fallback_models=settings.fallback_models())
        payload = response.json_payload()
    except LLMError as exc:
        log.warning("Блок %s: модель недоступна (%s), включён детерминированный режим", block, exc)
        fallback = _mock_block_result(block, fact_block, facts)
        fallback.error = str(exc)
        fallback.degraded = True
        return fallback

    result = _parse_block_payload(block, payload, facts, response)
    result.facts_input = facts
    if not result.data_gaps:
        result.data_gaps = fact_block.missing
    return result


def _parse_block_payload(block: str, payload: Dict[str, Any], facts: List[Dict[str, Any]],
                         response: LLMResponse) -> BlockResult:
    known = {f["id"] for f in facts}
    findings = []
    for item in (payload.get("findings") or [])[:8]:
        if not isinstance(item, dict):
            continue
        text = _text(item.get("text") or item.get("finding"), 400)
        if not text:
            continue
        fact_id = _text(item.get("fact_id") or item.get("factId"), 120) or None
        severity = _text(item.get("severity"), 20).lower()
        findings.append({
            "text": text,
            "severity": severity if severity in SEVERITIES else "medium",
            "fact_id": fact_id,
            "grounded": bool(fact_id and fact_id in known),
        })

    signal = _text(payload.get("signal"), 20).upper()
    return BlockResult(
        block=block,
        signal=signal if signal in SIGNALS else "ATTENTION",
        headline=_text(payload.get("headline"), 200),
        facts_sentence=_text(payload.get("facts_sentence") or payload.get("facts"), 800),
        interpretation=_text(payload.get("interpretation"), 800),
        findings=findings,
        data_gaps=_str_list(payload.get("data_gaps")),
        cannot_assess=_str_list(payload.get("cannot_assess")),
        model=response.model,
        latency_ms=response.latency_ms,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        raw_response=payload,
    )


async def run_block_agents(client: GroqClient, settings: Settings,
                           blocks: Dict[str, FactBlock], company: Dict[str, Any],
                           coverage: Dict[str, Any]) -> Dict[str, BlockResult]:
    """Четыре агента идут параллельно: у них нет общих данных."""
    tasks = [run_block_agent(client, settings, key, blocks[key], company, coverage)
             for key in BLOCK_KEYS if key in blocks]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: Dict[str, BlockResult] = {}
    for key, res in zip([k for k in BLOCK_KEYS if k in blocks], results):
        if isinstance(res, Exception):
            log.exception("Агент блока %s упал", key)
            failed = BlockResult(block=key, signal="NO_DATA", error=str(res), degraded=True,
                                 headline="Блок не проанализирован из-за технической ошибки")
            out[key] = failed
        else:
            out[key] = res
    return out


# --------------------------- Summary-LLM -----------------------------

async def run_summary_agent(client: GroqClient, settings: Settings,
                            company: Dict[str, Any], block_results: Dict[str, BlockResult],
                            key_facts: List[Dict[str, Any]],
                            coverage: Dict[str, Any],
                            all_fact_ids: Optional[set] = None) -> SummaryResult:
    blocks_payload = [block_results[k].to_summary_input() for k in BLOCK_KEYS if k in block_results]

    if not client.enabled:
        return _mock_summary_result(company, block_results, key_facts, coverage)

    user = prompts.build_summary_user_message(company, blocks_payload, key_facts, coverage)
    try:
        response = await client.complete_json(
            model=settings.groq_summary_model, system=prompts.SUMMARY_SYSTEM_PROMPT,
            user=user, temperature=settings.summary_temperature,
            fallback_models=settings.fallback_models())
        payload = response.json_payload()
    except LLMError as exc:
        log.warning("Summary: модель недоступна (%s), включён детерминированный режим", exc)
        fallback = _mock_summary_result(company, block_results, key_facts, coverage)
        fallback.error = str(exc)
        fallback.degraded = True
        return fallback

    # Модель вправе сослаться на любой факт прогона, не только на ключевой.
    known = set(all_fact_ids or ()) | {f["fact_id"] for f in key_facts if f.get("fact_id")}
    verdict = _text(payload.get("verdict_group"), 30).upper()
    narrative_points = normalize_summary_points(
        payload.get("narrative_points"), payload.get("narrative"))
    if not narrative_points:
        narrative_points = [
            "Итоговое пояснение модели не сформировано.",
            SUMMARY_SAFE_FALLBACK_POINT,
        ]
    return SummaryResult(
        verdict_group=verdict if verdict in VERDICTS else "ENHANCED_CHECK",
        headline=_text(payload.get("headline"), 90),
        narrative=" ".join(narrative_points),
        narrative_points=narrative_points,
        key_numbers=_ref_list(payload.get("key_numbers"), known, "label", 4),
        top_risks=_ref_list(payload.get("top_risks"), known, "text", 5),
        positives=_ref_list(payload.get("positives"), known, "text", 3),
        data_gaps=_str_list(payload.get("data_gaps"), 4),
        questions_to_ask=_str_list(payload.get("questions_to_ask"), 4) or DEFAULT_QUESTIONS,
        model=response.model,
        latency_ms=response.latency_ms,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        raw_response=payload,
    )


def _ref_list(items: Any, known: set, main_key: str, limit: int = 8) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in (items or [])[:limit]:
        if isinstance(item, str):
            item = {main_key: item}
        if not isinstance(item, dict):
            continue
        text = _text(item.get(main_key) or item.get("text") or item.get("label"), 400)
        if not text:
            continue
        fact_id = _text(item.get("fact_id") or item.get("factId"), 120) or None
        entry: Dict[str, Any] = {main_key: text, "fact_id": fact_id,
                                 "grounded": bool(fact_id and fact_id in known)}
        if "value" in item and main_key != "value":
            entry["value"] = _text(item.get("value"), 120)
        if "severity" in item:
            sev = _text(item.get("severity"), 20).lower()
            entry["severity"] = sev if sev in SEVERITIES else "medium"
        out.append(entry)
    return out


# --------------------------- guardrails ------------------------------

def enforce_guardrails(blocks: Dict[str, FactBlock], block_results: Dict[str, BlockResult],
                       summary: SummaryResult) -> Tuple[Dict[str, BlockResult], SummaryResult, List[str]]:
    """Вывод модели не может быть мягче фактов, посчитанных кодом."""
    notes: List[str] = []
    hard = _hard_stop_facts(blocks)
    attention = _attention_facts(blocks, scope="all")

    reliability = block_results.get("reliability")
    if hard and reliability is not None and SIGNAL_RANK.get(reliability.signal, 0) < SIGNAL_RANK["RISK"]:
        reliability.signal = "RISK"
        _sync_headline(reliability)
        notes.append("Сигнал блока надёжности повышен до RISK: в данных есть жёсткие факты")
    elif reliability is not None and reliability.signal == "NORM" \
            and _attention_facts(blocks, scope="reliability"):
        reliability.signal = "ATTENTION"
        _sync_headline(reliability)
        notes.append("Сигнал блока надёжности повышен до ATTENTION по посчитанным фактам")

    if hard and summary.verdict_group in ("CONDITIONALLY_OK", "NO_DATA"):
        summary.verdict_group = "STOP"
        notes.append("Итоговая группа повышена до STOP: в данных есть жёсткие факты")
    elif attention and summary.verdict_group == "CONDITIONALLY_OK":
        summary.verdict_group = "ENHANCED_CHECK"
        notes.append("Итоговая группа повышена до ENHANCED_CHECK по посчитанным фактам")

    # Жёсткий факт обязан присутствовать в списке рисков итогового экрана.
    existing = {(r.get("fact_id"), r.get("text", "")[:40]) for r in summary.top_risks}
    for item in hard:
        if not any(item["fact_id"] == fid for fid, _ in existing):
            summary.top_risks.insert(0, {
                "text": "Обращаем внимание: %s" % item["text"],
                "severity": "high", "fact_id": item["fact_id"], "grounded": True,
                "added_by": "guardrail",
            })
            notes.append("В итог добавлен жёсткий факт: %s" % item["text"])
    return block_results, summary, notes


DEFAULT_QUESTIONS = [
    "Запросите пояснение по фактам, отмеченным выше",
    "Уточните, какой из заявленных видов деятельности является основным",
    "Попросите свежую бухгалтерскую отчётность за последний год",
]

SIGNAL_SUFFIX = {"NORM": "без стоп-факторов", "ATTENTION": "есть что уточнить",
                 "RISK": "есть жёсткие факты", "NO_DATA": "нет данных"}


def _sync_headline(res: BlockResult) -> None:
    """Заголовок шаблонного режима не должен противоречить сигналу."""
    if res.model == "deterministic":
        res.headline = "%s: %s" % (res.title, SIGNAL_SUFFIX[res.signal])


# ------------------- детерминированный режим (mock) ------------------

def _rub(value: Any) -> str:
    """Крупные суммы в шаблонном режиме пишем словами, а не сырым float."""
    if value is None:
        return "нет данных"
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if amount >= 1e9:
        return "%s%.1f млрд руб" % (sign, amount / 1e9)
    if amount >= 1e6:
        return "%s%.1f млн руб" % (sign, amount / 1e6)
    if amount >= 1e3:
        return "%s%d тыс. руб" % (sign, round(amount / 1e3))
    return "%s%d руб" % (sign, round(amount))


def _fmt(value: Any) -> str:
    """Пустое значение печатаем словами, а не как None."""
    if value is None or value == [] or value == {}:
        return "нет данных"
    if isinstance(value, bool):
        return "да" if value else "нет"
    return str(value)


def _mock_block_result(block: str, fact_block: FactBlock,
                       facts: List[Dict[str, Any]]) -> BlockResult:
    """Ответ без обращения к модели: тот же формат, только шаблонный.

    Нужен для демо и тестов без ключа Groq и как запасной путь, если
    модель недоступна. Ничего не выдумывает, только пересказывает факты.
    """
    index = {f["id"]: f for f in facts}
    findings: List[Dict[str, Any]] = []
    signal = "NORM"

    def add(fact_id: str, text: str, severity: str = "medium") -> None:
        if fact_id in index:
            findings.append({"text": text, "severity": severity,
                             "fact_id": fact_id, "grounded": True})

    if block == "identity":
        age = index.get("company.age_years", {}).get("value")
        okved = index.get("okved.total_count", {}).get("value")
        cofounders = index.get("owners.cofounders_count", {}).get("value")
        related = index.get("related.count", {}).get("value")
        facts_sentence = ("Компания зарегистрирована %s лет назад, статус %s, кодов ОКВЭД %s, "
                          "учредителей %s, связанных компаний %s."
                          % (_fmt(age), _fmt(index.get("company.status", {}).get("value")),
                             _fmt(okved), _fmt(cofounders), _fmt(related)))
        if isinstance(age, int) and age <= 2:
            add("company.age_years", "Компания молодая, менее трёх лет с регистрации", "medium")
            signal = "ATTENTION"
        if index.get("okved.is_many", {}).get("value") is True:
            add("okved.total_count", "Заявлено %s кодов ОКВЭД, стоит уточнить профильное направление" % okved)
            signal = "ATTENTION"
        if index.get("owners.share_capital_is_minimal", {}).get("value") is True:
            add("owners.share_capital", "Уставный капитал минимальный", "medium")
            signal = "ATTENTION"
        interpretation = "Идентификационные данные компании прослеживаются по карточке."

    elif block == "reliability":
        hard = index.get("flags.hard_stop_codes", {}).get("value") or []
        att = index.get("flags.attention_codes", {}).get("value") or []
        active = index.get("execproc.active_count", {}).get("value")
        facts_sentence = ("Оценка банка %s, светофор ЗСК %s, негативных меток %s, "
                          "действующих исполнительных производств %s."
                          % (_fmt(index.get("bank.risk_level", {}).get("value")),
                             _fmt(index.get("bank.zsk_level", {}).get("value")),
                             _fmt(index.get("flags.negative_count", {}).get("value")),
                             _fmt(active) if active is not None else "нет записей"))
        for item in hard:
            add("flags.hard_stop_codes", "Обращаем внимание: %s" % item.get("meaning"), "high")
        for item in att:
            add("flags.attention_codes", "Требует уточнения: %s" % item.get("meaning"), "medium")
        if isinstance(active, int) and active > 0:
            add("execproc.active_count", "Действующих исполнительных производств: %s" % active, "high")
        signal = "RISK" if hard else ("ATTENTION" if findings else "NORM")
        interpretation = ("Оценка банка приведена как есть, факты показаны отдельно от цвета."
                          if hard else "Жёстких фактов в данных не обнаружено.")

    elif block == "finance":
        if not fact_block.has_data:
            return BlockResult(
                block=block, signal="NO_DATA",
                headline="Финансовой отчётности в карточке нет",
                facts_sentence="Финансовая отчётность в данных отсутствует.",
                interpretation="Оценить финансовое состояние по этим данным невозможно.",
                data_gaps=fact_block.missing, cannot_assess=fact_block.missing,
                facts_input=facts, model="deterministic")
        change = index.get("fin.proceeds_change_pct", {}).get("value")
        facts_sentence = ("Выручка за %s год: %s, прибыль: %s, изменение год к году: %s."
                          % (_fmt(index.get("fin.last_year", {}).get("value")),
                             _rub(index.get("fin.proceeds_last", {}).get("value")),
                             _rub(index.get("fin.profit_last", {}).get("value")),
                             ("%s %%" % change) if change is not None else "нет данных"))
        if index.get("fin.proceeds_drop_20", {}).get("value") is True:
            add("fin.proceeds_change_pct", "Выручка снизилась более чем на 20 %", "high")
        if index.get("fin.has_loss", {}).get("value") is True:
            add("fin.loss_years", "В отчётности есть убыточные годы", "medium")
        if index.get("fin.negative_capitals", {}).get("value") is True:
            add("fin.capitals_last", "Собственный капитал отрицательный", "high")
        signal = "RISK" if any(f["severity"] == "high" for f in findings) else (
            "ATTENTION" if findings else "NORM")
        interpretation = "Показатели приведены по данным отчётности из карточки."

    else:  # experience
        if not fact_block.has_data:
            return BlockResult(
                block=block, signal="NO_DATA",
                headline="Подтверждённого опыта в данных нет",
                facts_sentence="Госзакупок, лицензий и позитивных маркеров в карточке нет.",
                interpretation="Отсутствие записей не означает отсутствия опыта в реальности.",
                data_gaps=fact_block.missing, cannot_assess=fact_block.missing,
                facts_input=facts, model="deterministic")
        facts_sentence = ("Выигранных тендеров %s, подписанных контрактов %s, лицензий %s, "
                          "позитивных маркеров %s."
                          % (index.get("procurement.tenders_won", {}).get("value", 0),
                             index.get("procurement.contracts_signed", {}).get("value", 0),
                             index.get("license.count", {}).get("value", 0),
                             index.get("positive.count", {}).get("value", 0)))
        add("procurement.contracts_signed", "Есть подтверждённый опыт госконтрактов", "low")
        add("license.active_count", "Есть действующие лицензии", "low")
        add("positive.count", "Пройдены проверки по реестрам", "low")
        signal = "NORM"
        interpretation = "Позитивные маркеры означают отсутствие компании в негативных реестрах."

    return BlockResult(
        block=block, signal=signal,
        headline="%s: %s" % (fact_block.title, SIGNAL_SUFFIX[signal]),
        facts_sentence=facts_sentence,
        interpretation=interpretation,
        findings=findings[:5],
        data_gaps=fact_block.missing,
        cannot_assess=fact_block.missing,
        facts_input=facts,
        model="deterministic",
    )


def _mock_summary_result(company: Dict[str, Any], block_results: Dict[str, BlockResult],
                         key_facts: List[Dict[str, Any]],
                         coverage: Dict[str, Any]) -> SummaryResult:
    risks: List[Dict[str, Any]] = []
    positives: List[Dict[str, Any]] = []
    gaps: List[str] = []
    for res in block_results.values():
        for finding in res.findings:
            target = risks if finding["severity"] in ("high", "medium") else positives
            target.append({"text": finding["text"], "fact_id": finding.get("fact_id"),
                           "severity": finding["severity"], "grounded": finding.get("grounded", False)})
        gaps.extend(res.cannot_assess)

    worst = max((SIGNAL_RANK.get(r.signal, 0) for r in block_results.values()), default=0)
    verdict = {3: "STOP", 2: "ENHANCED_CHECK", 1: "CONDITIONALLY_OK", 0: "NO_DATA"}[worst]
    headline = {
        "STOP": "В данных есть жёсткие факты, до сделки их стоит снять",
        "ENHANCED_CHECK": "Работать можно, но часть фактов стоит уточнить до сделки",
        "CONDITIONALLY_OK": "Стоп-факторов в данных не найдено",
        "NO_DATA": "Данных в карточке слишком мало для вывода",
    }[verdict]

    first = ("%s: оценка банка %s, ЗСК %s; заполнено %s из %s блоков данных."
             % (company.get("short_name") or "Контрагент", company.get("risk_level"),
                company.get("zsk_risk_level"), coverage.get("filled_blocks"),
                coverage.get("total_blocks")))
    second = ("Главный факт внимания: %s." % risks[0]["text"]
              if risks else "Жёстких фактов в доступных данных не найдено.")
    third = ("До сделки стоит уточнить: %s." % gaps[0]
             if gaps else "До сделки стоит проверить актуальность доступных данных.")
    narrative_points = normalize_summary_points([first, second, third])
    return SummaryResult(
        verdict_group=verdict, headline=headline, narrative=" ".join(narrative_points),
        narrative_points=narrative_points,
        key_numbers=key_facts[:4], top_risks=risks[:5], positives=positives[:3],
        data_gaps=list(dict.fromkeys(gaps))[:4],
        questions_to_ask=list(DEFAULT_QUESTIONS)[:4],
        model="deterministic",
    )

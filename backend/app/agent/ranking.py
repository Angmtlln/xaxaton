"""Bounded parsing of user selection commands, never assistant prose.

The pending proposal contains validated arguments, not a model-generated answer.
Unknown filter text is rejected rather than silently broadening the selection.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import FindCompaniesArgs
from .shortlist import direct_shortlist_arguments

METRICS = {
    "proceeds": (r"выручк\w*", "выручка", "desc"),
    "profit": (r"прибыл\w*", "прибыль", "desc"),
    "claims": (r"(?:сумм\w*\s+)?иск\w*", "сумма исков к компании", "asc"),
    "enforcement": (r"(?:(?:количеств\w*|числ\w*)\s+)?исполнительн\w*\s+производств\w*", "число исполнительных производств", "asc"),
}
SELECTION = re.compile(r"\b(?:выбери|отбери|выбрать|отобрать|топ\s*\d*|лучш\w*|наибольш\w*|наименьш\w*|максимальн\w*|минимальн\w*|отсортируй)\b", re.I)
REFERENCE = re.compile(r"\b(?:из\s+(?:найденных|них|подборки|списка)|среди\s+(?:найденных|них)|этих\s+компаний)\b", re.I)


def ranking_labels(ranking: list[dict]) -> list[str]:
    return ["%s — %s" % (METRICS[item["metric"]][1],
            "по убыванию" if item["order"] == "desc" else "по возрастанию") for item in ranking]


@dataclass
class SelectionTurn:
    arguments: dict | None = None
    clarification: str | None = None
    pending: dict | None = None


def selection_turn(message: str, shortlist: dict | None, pending: dict | None) -> SelectionTurn | None:
    text = message.strip().rstrip(".!?")
    if pending and re.fullmatch(r"(?:да|согласен|подтверждаю|подходит|ок|хорошо|давай|да,?\s+так|да,?\s+выбирай)", text, re.I):
        return SelectionTurn(arguments=FindCompaniesArgs.model_validate(pending["arguments"]).model_dump(exclude_none=True))
    if pending and re.fullmatch(r"(?:нет|отмена|отмени|не надо)", text, re.I):
        return SelectionTurn(clarification="Выбор отменён. Можно задать другой показатель или порядок.")
    if not pending and not REFERENCE.search(text) and not re.match(
            r"(?:найди|покажи|подбери|выбери|отбери|выбрать|отобрать|отсортируй|топ|лучш\w*)\b", text, re.I):
        return None
    if not SELECTION.search(text) and re.match(r"(?:найди|покажи|подбери)\s+", text, re.I):
        return None
    if not SELECTION.search(text) and not (pending and any(re.search(spec[0], text, re.I) for spec in METRICS.values())):
        return None

    # Separate the filter command from the requested ordering before parsing metrics.
    split = re.search(r"(?:,?\s+и\s+|,\s*)(?=(?:выбери|отбери|отсортируй)\b)", text, re.I)
    filter_text, selection = (text[:split.start()], text[split.end():]) if split else ("", text)
    # Also allow 'Найди ... с наибольшей прибылью' as one request.
    if not filter_text and re.match(r"(?:найди|покажи|подбери)\s+", text, re.I):
        split = re.search(r"\s+(?:с\s+)?(?=(?:наибольш|наименьш|максимальн|минимальн|лучш))", text, re.I)
        if split:
            filter_text, selection = text[:split.start()], text[split.end():]

    reference = bool(REFERENCE.search(text))
    base = {}
    if filter_text:
        # Explicit, bounded alias; unknown activity phrases still require clarification.
        filter_text = re.sub(r"торговые\s+компании", "компании занимающиеся торговлей", filter_text, flags=re.I)
        filter_text = re.sub(r"(занимающиеся\s+торговлей)\s+с\s+", r"\1 и с ", filter_text, flags=re.I)
        if re.fullmatch(r"(?:найди|покажи|подбери)\s+(?:компании|контрагентов)", filter_text, re.I):
            base = {}
        else:
            parsed = direct_shortlist_arguments(filter_text)
            if parsed is None:
                return SelectionTurn(clarification="Не удалось однозначно разобрать условия. Сначала задайте подборку, например: «Найди компании занимающиеся торговлей и с выручкой от 10 млн», затем — «Из найденных выбери 5 по прибыли».")
            base = parsed
    elif reference:
        if not shortlist or "filters" not in shortlist:
            return SelectionTurn(clarification="Нет сохранённых условий подборки. Сначала укажите, какие компании найти.")
        base = dict(shortlist["filters"])
    elif pending:
        base = dict(pending["arguments"])
    elif shortlist and "filters" in shortlist:
        base = dict(shortlist["filters"])

    if pending:
        selection = re.sub(r"^нет,?\s+", "", selection, flags=re.I)
        count_only = re.fullmatch(r"(?:выбери|отбери|первые)\s+(-?\d+)(?:\s+компани\w*)?", selection, re.I)
        if count_only:
            count = int(count_only[1])
            if not 1 <= count <= 25:
                return SelectionTurn(clarification="Укажите от 1 до 25 компаний.")
            args = {**pending["arguments"], "limit": count}
            return SelectionTurn(pending={**pending, "arguments": args}, clarification=(
                "Количество для выбора: %s. Порядок: %s. Подтверждаете?" % (count, "; затем ".join(ranking_labels(args["ranking"])))))

    matches = sorted((m.start(), m.end(), key) for key, spec in METRICS.items()
                     for m in re.finditer(spec[0], selection, re.I))
    if not matches:
        return SelectionTurn(clarification="По какому показателю выбрать: выручке, прибыли, сумме исков к компании или количеству исполнительных производств?")
    ranking = []
    previous_end = 0
    for start, end, key in matches:
        if any(item["metric"] == key for item in ranking):
            return SelectionTurn(clarification="Показатель указан несколько раз. Укажите для каждого одно направление и его приоритет.")
        qualifier = selection[previous_end:start]
        suffix = selection[end:matches[len(ranking)+1][0] if len(ranking)+1 < len(matches) else len(selection)]
        # Directions immediately following a measure also apply ('прибыль по возрастанию').
        direction = METRICS[key][2]
        if re.search(r"минимальн|наименьш|по\s+возрастанию", qualifier, re.I) or re.match(r"\s+по\s+возрастанию", suffix, re.I):
            direction = "asc"
        if re.search(r"максимальн|наибольш|по\s+убыванию", qualifier, re.I) or re.match(r"\s+по\s+убыванию", suffix, re.I):
            direction = "desc"
        ranking.append({"metric": key, "order": direction})
        trailing = re.match(r"\s+по\s+(?:возрастанию|убыванию)", suffix, re.I)
        previous_end = end + (trailing.end() if trailing else 0)

    # Do not consume unknown constraints as part of an ordering phrase.
    remainder = selection
    for spec in METRICS.values():
        remainder = re.sub(spec[0], " ", remainder, flags=re.I)
    remainder = REFERENCE.sub(" ", remainder)
    remainder = re.sub(r"\b(?:выбери|отбери|выбрать|отобрать|отсортируй|топ|лучш\w*|перв\w*|компани\w*|контрагент\w*|по|к|с|и|затем|потом|сначала|приоритет|главное|важнее|наибольш\w*|наименьш\w*|максимальн\w*|минимальн\w*|убыванию|возрастанию)\b|\d+|[,;:—–-]", " ", remainder, flags=re.I)
    if remainder.strip():
        return SelectionTurn(clarification="Не удалось однозначно разобрать выбор. Укажите показатель и направление, например «Из найденных выбери 5 с наибольшей прибылью». Дополнительные условия задайте отдельным поиском.")
    number = re.search(r"\b(?:выбери|отбери|выбрать|отобрать|топ|первые)\s+(-?\d+)\b", selection, re.I)
    limit = int(number[1]) if number else (pending["arguments"]["limit"] if pending else 5)
    base = {key: value for key, value in base.items() if key not in {"ranking", "sort_by", "order", "limit"}}
    try:
        args = FindCompaniesArgs(**base, ranking=ranking, limit=limit).model_dump(exclude_none=True)
    except ValueError:
        return SelectionTurn(clarification="Укажите от 1 до 25 компаний и непротиворечивые условия выбора.")
    explicit_order = bool(re.search(r"\bсначала\b.+\b(?:затем|потом)\b", selection, re.I))
    if len(ranking) > 1 and not explicit_order:
        proposal = {"arguments": args, "source": "shortlist" if (reference or (shortlist and not filter_text)) else "query"}
        return SelectionTurn(pending=proposal, clarification=(
            "Предлагаю такой порядок: " + "; затем ".join(ranking_labels(ranking)) +
            ". Следующий показатель используется только при равенстве предыдущего. Количество для выбора: %s. Подтверждаете порядок?" % limit))
    return SelectionTurn(arguments=args)

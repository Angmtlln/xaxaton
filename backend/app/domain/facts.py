"""Детерминированный слой фактов (гипотеза S2).

Всё, что попадает в LLM, считается здесь кодом из сырых полей карточки.
Готовые текстовые формулировки отчёта (``reputationalRisks[].name``)
передаются только как справочная подпись к коду метки и никогда не
являются основанием для вывода: в 46 карточках из 100 они противоречат
цифрам той же карточки (H4).

Каждый факт несёт ``field_ref`` — путь к полю исходной карточки. Это
основа заземления ответов (S5): модель обязана возвращать fact_id, а мы
проверяем, что такой факт существует.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.mongo import as_float, as_int, dig, parse_date

CALCULATOR_VERSION = "facts-1.0.0"

BLOCK_KEYS = ("identity", "reliability", "finance", "experience")

BLOCK_TITLES = {
    "identity": "Кто это",
    "reliability": "Надёжность и правовые риски",
    "finance": "Финансовое состояние",
    "experience": "Опыт и позитивные сигналы",
}

# Коды меток, которые обязаны подниматься наверх независимо от цвета
# светофора банка. Основание: все 9 компаний с блокировкой счетов ФНС и
# единственная компания в банкротстве помечены GREEN + LOW (H3).
HARD_STOP_CODES = {
    "liquidationStatus": "процедура банкротства или ликвидации",
    "fnsBlocking": "блокировка счетов по постановлению ФНС",
    "invalidAddress": "фиктивный адрес регистрации",
    "invalidRegistrationData": "недостоверные регистрационные данные",
    "invalidAuthpersonsData": "номинальный руководитель или недостоверные данные о нём",
    "dishonestProvider": "реестр недобросовестных поставщиков",
    "disqualifiedAuthpersons": "дисквалифицированный руководитель",
}

ATTENTION_CODES = {
    "massAddress": "массовый адрес регистрации",
    "massAuthpersons": "массовый директор или учредитель",
    "taxArrears": "задолженность по налогам",
    "taxReporting": "непредставление налоговой отчётности",
    "executionProceedings": "действующие исполнительные производства",
    "arbitrationDefendant": "арбитражные дела в роли ответчика",
    "аrbitrationDefendant": "арбитражные дела в роли ответчика",
}

RISK_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "UNKNOWN": 0}
ZSK_RANK = {"GREEN": 1, "YELLOW": 2, "RED": 3, "UNKNOWN": 0}

# Порог, при котором «много ОКВЭД» перестаёт быть узкой специализацией.
OKVED_MANY_THRESHOLD = 10


@dataclass
class Fact:
    """Один проверяемый факт с указателем на поле исходной карточки."""

    id: str
    label: str
    value: Any
    field_ref: str
    unit: Optional[str] = None
    source: str = "computed"     # computed | raw | derived_flag
    comment: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "value": _jsonable(self.value),
            "field_ref": self.field_ref,
            "source": self.source,
        }
        if self.unit:
            payload["unit"] = self.unit
        if self.comment:
            payload["comment"] = self.comment
        return payload


@dataclass
class FactBlock:
    """Набор фактов одного блока плюс признаки полноты данных."""

    block: str
    title: str
    facts: List[Fact] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)     # чего нет в карточке
    has_data: bool = True

    def add(self, fact_id: str, label: str, value: Any, field_ref: str,
            unit: Optional[str] = None, source: str = "computed",
            comment: Optional[str] = None) -> None:
        self.facts.append(Fact(fact_id, label, value, field_ref, unit, source, comment))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "block": self.block,
            "title": self.title,
            "has_data": self.has_data,
            "missing": self.missing,
            "facts": [f.to_dict() for f in self.facts],
        }

    def index(self) -> Dict[str, Fact]:
        return {f.id: f for f in self.facts}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _report(document: Dict[str, Any]) -> Dict[str, Any]:
    """Принимает и весь документ снапшота, и уже вынутый report."""
    if isinstance(document, dict) and "report" in document:
        return document["report"] or {}
    return document or {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


# ======================== Блок 1. Кто это ============================

def build_identity(document: Dict[str, Any]) -> FactBlock:
    rep = _report(document)
    base = rep.get("baseInfo") or {}
    blk = FactBlock("identity", BLOCK_TITLES["identity"])

    blk.add("company.name", "Название", base.get("shortName"), "report.baseInfo.shortName", source="raw")
    blk.add("company.full_name", "Полное название", base.get("fullName"), "report.baseInfo.fullName", source="raw")
    blk.add("company.inn", "ИНН", base.get("inn"), "report.baseInfo.inn", source="raw")
    blk.add("company.ogrn", "ОГРН", base.get("ogrn"), "report.baseInfo.ogrn", source="raw")
    blk.add("company.address", "Адрес", base.get("address"), "report.baseInfo.address", source="raw")

    reg_date = parse_date(dig(base, "registrationInfo", "registrationDate"))
    blk.add("company.registration_date", "Дата регистрации", reg_date,
            "report.baseInfo.registrationInfo.registrationDate", source="raw")
    blk.add("company.age_years", "Лет с регистрации",
            as_int(dig(base, "registrationInfo", "yearsFromRegistration")),
            "report.baseInfo.registrationInfo.yearsFromRegistration", unit="лет", source="raw")

    status = dig(rep, "status", "status")
    blk.add("company.status", "Статус", status, "report.status.status", source="raw")
    if dig(rep, "status", "reasonName"):
        blk.add("company.status_reason", "Причина статуса", dig(rep, "status", "reasonName"),
                "report.status.reasonName", source="raw")
    blk.add("company.is_active", "Компания действующая", status == "CURRENT",
            "report.status.status", source="derived_flag")

    size = base.get("companySize")
    blk.add("company.size", "Размер бизнеса", size or "не указан", "report.baseInfo.companySize", source="raw")

    # Контактность: сайт и телефоны нужны для оценки «живая ли компания».
    phones = [p for p in _arr(rep.get("phones")) if p]
    blk.add("contacts.phones_count", "Телефонов в отчёте", len(phones), "report.phones[]", unit="шт")
    blk.add("contacts.has_website", "Есть сайт", bool(base.get("website")),
            "report.baseInfo.website", source="derived_flag")
    if base.get("website"):
        blk.add("contacts.website", "Сайт", base.get("website"), "report.baseInfo.website", source="raw")
    blk.add("contacts.has_email", "Есть email", bool(base.get("email")),
            "report.baseInfo.email", source="derived_flag")

    # ОКВЭД. Пересчитываем сами: готовая формулировка отчёта о «небольшом
    # количестве кодов» расходится с фактом в 46 карточках из 100 (H4).
    kinds = rep.get("kindsOfActivityInfo") or {}
    main = kinds.get("mainKindOfActivity") or {}
    others = [o for o in _arr(kinds.get("otherKindsOfActivity")) if isinstance(o, dict)]
    okved_total = (1 if main.get("code") else 0) + len(others)
    blk.add("okved.main", "Основной вид деятельности",
            _fmt_okved(main), "report.kindsOfActivityInfo.mainKindOfActivity", source="raw")
    blk.add("okved.other_count", "Дополнительных кодов ОКВЭД", len(others),
            "report.kindsOfActivityInfo.otherKindsOfActivity[]", unit="шт")
    blk.add("okved.total_count", "Всего кодов ОКВЭД", okved_total,
            "report.kindsOfActivityInfo", unit="шт",
            comment="Посчитано напрямую по кодам, а не взято из текста отчёта")
    blk.add("okved.is_many", "Кодов ОКВЭД много (>= %d)" % OKVED_MANY_THRESHOLD,
            okved_total >= OKVED_MANY_THRESHOLD, "report.kindsOfActivityInfo", source="derived_flag")
    blk.add("okved.top_other", "Примеры дополнительных кодов",
            [_fmt_okved(o) for o in others[:5]],
            "report.kindsOfActivityInfo.otherKindsOfActivity[]", source="raw")

    tax = [t.get("shortName") or t.get("fullName") for t in _arr(rep.get("taxSystem")) if isinstance(t, dict)]
    blk.add("tax.systems", "Система налогообложения", tax or None, "report.taxSystem[]", source="raw")

    branches = _arr(dig(rep, "branchesInfo", "branches"))
    blk.add("branches.count", "Филиалов",
            as_int(dig(rep, "branchesInfo", "branchesCount")) or len(branches),
            "report.branchesInfo.branchesCount", unit="шт")

    # --- Владельцы и связи ---
    founders = rep.get("foundersInfo") or {}
    auth = founders.get("authPerson") or {}
    if auth.get("name"):
        blk.add("owners.auth_person", "Руководитель",
                {"name": auth.get("name"), "position": auth.get("positionName"),
                 "inn": auth.get("inn"),
                 "since": parse_date(auth.get("positionDate"))},
                "report.foundersInfo.authPerson", source="raw")
    else:
        blk.missing.append("Руководитель не указан (report.foundersInfo.authPerson)")

    cofounders = [c for c in _arr(founders.get("cofounders")) if isinstance(c, dict)]
    active_cofounders = [c for c in cofounders if c.get("active") is not False]
    blk.add("owners.cofounders_count", "Учредителей", len(cofounders),
            "report.foundersInfo.cofounders[]", unit="шт")
    blk.add("owners.cofounders_active", "Из них действующих", len(active_cofounders),
            "report.foundersInfo.cofounders[].active", unit="шт")
    if cofounders:
        blk.add("owners.cofounders", "Учредители",
                [{"name": c.get("name"), "inn": c.get("inn"),
                  "share_pct": as_float(c.get("share")),
                  "amount": as_float(c.get("amount")),
                  "active": c.get("active")} for c in cofounders[:10]],
                "report.foundersInfo.cofounders[]", source="raw")
    else:
        blk.missing.append("Состав учредителей отсутствует (report.foundersInfo.cofounders)")

    share_capital = as_float(founders.get("shareCapital"))
    blk.add("owners.share_capital", "Уставный капитал", share_capital,
            "report.foundersInfo.shareCapital", unit="руб", source="raw")
    if share_capital is not None:
        blk.add("owners.share_capital_is_minimal", "Уставный капитал минимальный (<= 10 000 руб)",
                share_capital <= 10000, "report.foundersInfo.shareCapital", source="derived_flag")

    related = [r for r in _arr(rep.get("relatedCompanies")) if isinstance(r, dict)]
    blk.add("related.count", "Связанных компаний", len(related), "report.relatedCompanies[]", unit="шт")
    if related:
        blk.add("related.sample", "Связанные компании",
                [{"name": r.get("name"), "inn": r.get("inn"),
                  "auth_person": r.get("authPersonName")} for r in related[:10]],
                "report.relatedCompanies[]", source="raw")
        auth_name = (auth.get("name") or "").strip().lower()
        same_person = [r for r in related
                       if auth_name and (r.get("authPersonName") or "").strip().lower() == auth_name]
        blk.add("related.same_auth_person", "Компаний с тем же руководителем", len(same_person),
                "report.relatedCompanies[].authPersonName", unit="шт")
    else:
        blk.missing.append("Связанные компании отсутствуют (report.relatedCompanies)")

    parents = [p for r in related for p in _arr(r.get("parentOrganizations")) if isinstance(p, dict)]
    blk.add("related.parent_orgs_count", "Материнских организаций у связанных компаний", len(parents),
            "report.relatedCompanies[].parentOrganizations[]", unit="шт")

    blk.has_data = bool(base.get("inn"))
    return blk


def _fmt_okved(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict) or not item.get("code"):
        return None
    return {"code": item.get("code"), "description": item.get("description")}


# ============ Блок 2. Надёжность и правовые риски ====================

def build_reliability(document: Dict[str, Any]) -> FactBlock:
    rep = _report(document)
    base = rep.get("baseInfo") or {}
    blk = FactBlock("reliability", BLOCK_TITLES["reliability"])

    # Оценки банка передаём как есть. Свой скоринг не считаем (ограничение Q&A).
    risk = base.get("riskLevel") or "UNKNOWN"
    zsk = rep.get("zskRiskLevel") or "UNKNOWN"
    blk.add("bank.risk_level", "Уровень риска банка", risk, "report.baseInfo.riskLevel", source="raw")
    blk.add("bank.zsk_level", "Светофор ЗСК", zsk, "report.zskRiskLevel", source="raw")
    blk.add("bank.levels_diverge", "Оценки банка расходятся по рангу",
            RISK_RANK.get(risk, 0) != ZSK_RANK.get(zsk, 0),
            "report.baseInfo.riskLevel + report.zskRiskLevel", source="derived_flag")

    negative = [n for n in _arr(dig(rep, "reputationalRisks", "negative")) if isinstance(n, dict)]
    codes = [n.get("code") for n in negative if n.get("code")]
    hard = [c for c in codes if c in HARD_STOP_CODES]
    attention = [c for c in codes if c in ATTENTION_CODES]

    blk.add("flags.negative_count", "Негативных меток в отчёте", len(negative),
            "report.reputationalRisks.negative[]", unit="шт")
    blk.add("flags.negative_codes", "Коды негативных меток",
            [{"code": n.get("code"), "chapter": n.get("chapter")} for n in negative],
            "report.reputationalRisks.negative[].code", source="raw")
    blk.add("flags.hard_stop_codes", "Жёсткие стоп-факторы",
            [{"code": c, "meaning": HARD_STOP_CODES[c]} for c in hard],
            "report.reputationalRisks.negative[].code",
            comment="Факты, которые светофор банка может не отражать")
    blk.add("flags.attention_codes", "Метки, требующие уточнения",
            [{"code": c, "meaning": ATTENTION_CODES[c]} for c in attention],
            "report.reputationalRisks.negative[].code")

    # Ядро продукта: зелёный цвет при наличии жёстких фактов (H3).
    green_conflict = zsk == "GREEN" and risk in ("LOW", "UNKNOWN") and bool(hard)
    blk.add("flags.green_with_hard_stop", "Зелёная оценка при наличии жёстких фактов",
            green_conflict, "report.zskRiskLevel + report.reputationalRisks.negative[]",
            source="derived_flag",
            comment="Оценку банка не оспариваем, но факты показываем отдельно")

    # --- Суды ---
    cases = [c for c in _arr(rep.get("arbitrationCases")) if isinstance(c, dict)]
    summary = rep.get("arbitrationByStatus") or {}
    if cases or summary:
        years = sorted({as_int(c.get("year")) for c in cases if as_int(c.get("year")) is not None})
        def_count = sum(as_int(c.get("defendantCount")) or 0 for c in cases)
        def_amount = sum(as_float(c.get("defendantAmount")) or 0.0 for c in cases)
        pl_count = sum(as_int(c.get("plaintiffCount")) or 0 for c in cases)
        pl_amount = sum(as_float(c.get("plaintiffAmount")) or 0.0 for c in cases)

        blk.add("court.years", "Годы с арбитражными делами", years, "report.arbitrationCases[].year")
        blk.add("court.defendant_count", "Дел в роли ответчика", def_count,
                "report.arbitrationCases[].defendantCount", unit="шт")
        blk.add("court.defendant_amount", "Сумма исков к компании", round(def_amount, 2),
                "report.arbitrationCases[].defendantAmount", unit="руб")
        blk.add("court.plaintiff_count", "Дел в роли истца", pl_count,
                "report.arbitrationCases[].plaintiffCount", unit="шт")
        blk.add("court.plaintiff_amount", "Сумма исков компании", round(pl_amount, 2),
                "report.arbitrationCases[].plaintiffAmount", unit="руб")
        blk.add("court.by_year", "Дела по годам",
                [{"year": as_int(c.get("year")),
                  "defendant_count": as_int(c.get("defendantCount")),
                  "defendant_amount": as_float(c.get("defendantAmount")),
                  "plaintiff_count": as_int(c.get("plaintiffCount")),
                  "plaintiff_amount": as_float(c.get("plaintiffAmount"))}
                 for c in sorted(cases, key=lambda x: as_int(x.get("year")) or 0)],
                "report.arbitrationCases[]")

        pending = _pair(summary, "defandantArbitration", "defandantArbitrationPending", "dp")
        appealed = _pair(summary, "defandantArbitration", "defandantArbitrationAppealed", "da")
        finished = _pair(summary, "defandantArbitration", "defandantArbitrationFinished", "df")
        blk.add("court.defendant_pending", "Незавершённые дела против компании", pending,
                "report.arbitrationByStatus.defandantArbitration.defandantArbitrationPending")
        blk.add("court.defendant_appealed", "Обжалуемые дела против компании", appealed,
                "report.arbitrationByStatus.defandantArbitration.defandantArbitrationAppealed")
        blk.add("court.defendant_finished", "Завершённые дела против компании", finished,
                "report.arbitrationByStatus.defandantArbitration.defandantArbitrationFinished")
        blk.add("court.common_count", "Всего дел по сводке банка",
                as_int(summary.get("commonCount")), "report.arbitrationByStatus.commonCount", unit="шт")
        blk.add("court.common_amount", "Общая сумма дел",
                as_float(summary.get("commonAmount")), "report.arbitrationByStatus.commonAmount", unit="руб")
    else:
        blk.missing.append(
            "Арбитражных дел в карточке нет (report.arbitrationCases). "
            "Отсутствие записей не означает отсутствия судов в реальности")

    # --- Исполнительные производства ---
    procs = [p for p in _arr(rep.get("executionProceedings")) if isinstance(p, dict)]
    if procs:
        active = [p for p in procs if p.get("active") is True]
        amounts = [as_float(p.get("amount")) for p in procs]
        known = [a for a in amounts if a is not None]
        active_known = [as_float(p.get("amount")) for p in active]
        active_known = [a for a in active_known if a is not None]
        dates = [parse_date(p.get("date")) for p in procs]
        dates = [d for d in dates if d]

        blk.add("execproc.total_count", "Исполнительных производств всего", len(procs),
                "report.executionProceedings[]", unit="шт")
        blk.add("execproc.active_count", "Действующих производств", len(active),
                "report.executionProceedings[].active", unit="шт")
        blk.add("execproc.active_amount", "Сумма действующих производств",
                round(sum(active_known), 2), "report.executionProceedings[].amount", unit="руб",
                comment="Сумма посчитана по %d из %d действующих записей, у остальных сумма не раскрыта"
                        % (len(active_known), len(active)))
        blk.add("execproc.total_amount", "Сумма всех производств", round(sum(known), 2),
                "report.executionProceedings[].amount", unit="руб")
        blk.add("execproc.amount_unknown_count", "Производств без суммы в источнике",
                len(amounts) - len(known), "report.executionProceedings[].amount", unit="шт")
        blk.add("execproc.max_amount", "Крупнейшее производство",
                round(max(known), 2) if known else None,
                "report.executionProceedings[].amount", unit="руб")
        blk.add("execproc.last_date", "Дата последнего производства",
                max(dates) if dates else None, "report.executionProceedings[].date")
        blk.add("execproc.recent_active", "Последние действующие производства",
                [{"number": p.get("number"), "date": parse_date(p.get("date")),
                  "amount": as_float(p.get("amount"))}
                 for p in sorted(active, key=lambda x: parse_date(x.get("date")) or date.min,
                                 reverse=True)[:5]],
                "report.executionProceedings[]", source="raw")
    else:
        blk.missing.append("Исполнительных производств в карточке нет (report.executionProceedings)")

    # --- Проверки надзорных органов ---
    inspections = [i for i in _arr(rep.get("inspections")) if isinstance(i, dict)]
    if inspections:
        by_status: Dict[str, int] = {}
        for i in inspections:
            key = i.get("inspectionStatus") or "UNKNOWN"
            by_status[key] = by_status.get(key, 0) + 1
        violations = sum(v for k, v in by_status.items() if "ViolationDetected" in k)
        blk.add("inspections.count", "Проверок надзорных органов", len(inspections),
                "report.inspections[]", unit="шт")
        blk.add("inspections.by_status", "Проверки по результату", by_status,
                "report.inspections[].inspectionStatus")
        blk.add("inspections.violations_count", "Проверок с выявленными нарушениями", violations,
                "report.inspections[].inspectionStatus", unit="шт")
        blk.add("inspections.authorities", "Проверяющие органы",
                sorted({i.get("authorityName") for i in inspections if i.get("authorityName")})[:5],
                "report.inspections[].authorityName", source="raw")
    else:
        blk.missing.append("Проверок надзорных органов в карточке нет (report.inspections)")

    blk.has_data = True
    return blk


def _pair(summary: Dict[str, Any], group: str, node: str, prefix: str) -> Dict[str, Any]:
    data = dig(summary, group, node) or {}
    return {"count": as_int(data.get(prefix + "Count")) or 0,
            "amount": as_float(data.get(prefix + "Amount")) or 0.0}


# =================== Блок 3. Финансовое состояние ====================

def build_finance(document: Dict[str, Any]) -> FactBlock:
    rep = _report(document)
    blk = FactBlock("finance", BLOCK_TITLES["finance"])

    reports = [r for r in _arr(rep.get("finReports")) if isinstance(r, dict)]
    rows = []
    for item in reports:
        common = item.get("common") or {}
        assets = item.get("assets") or {}
        liab = item.get("liabilities") or {}
        year = as_int(common.get("year"))
        if year is None:
            continue
        rows.append({
            "year": year,
            "proceeds": as_float(common.get("proceeds")),
            "profit": as_float(common.get("profit")),
            "total_assets": as_float(assets.get("totalAssets")),
            "current_assets": as_float(dig(assets, "currentAssets", "total")),
            "receivables": as_float(dig(assets, "currentAssets", "receivables")),
            "bankroll": as_float(dig(assets, "currentAssets", "bankroll")),
            "total_liabilities": as_float(liab.get("totalLiabilities")),
            "capitals": as_float(liab.get("capitals")),
            "short_term_total": as_float(dig(liab, "shortTermLiabilities", "total")),
            "accounts_payable": as_float(dig(liab, "shortTermLiabilities", "accountsPayable")),
            "long_term_total": as_float(dig(liab, "longTermDuties", "total")),
        })
    rows.sort(key=lambda r: r["year"])

    if not rows:
        blk.has_data = False
        blk.missing.append(
            "Финансовой отчётности в карточке нет (report.finReports). "
            "Оценить финансовое состояние по этим данным невозможно")
        _add_coefficients(rep, blk)
        return blk

    blk.add("fin.years", "Годы с отчётностью", [r["year"] for r in rows],
            "report.finReports[].common.year")
    blk.add("fin.series", "Показатели по годам", rows, "report.finReports[]")

    last = rows[-1]
    blk.add("fin.last_year", "Последний год отчётности", last["year"], "report.finReports[].common.year")
    blk.add("fin.proceeds_last", "Выручка за последний год", last["proceeds"],
            "report.finReports[].common.proceeds", unit="руб")
    blk.add("fin.profit_last", "Прибыль за последний год", last["profit"],
            "report.finReports[].common.profit", unit="руб")

    # Динамика строится только там, где есть два года подряд. Иначе виджет
    # обязан показывать «нет данных», а не пустую ось (ограничение S7).
    if len(rows) >= 2 and rows[-2]["proceeds"] and last["proceeds"] is not None:
        prev = rows[-2]["proceeds"]
        change = (last["proceeds"] - prev) / abs(prev) * 100 if prev else None
        blk.add("fin.proceeds_change_pct", "Изменение выручки год к году",
                round(change, 1) if change is not None else None,
                "report.finReports[].common.proceeds", unit="%")
        blk.add("fin.proceeds_drop_20", "Выручка упала более чем на 20 %",
                bool(change is not None and change <= -20),
                "report.finReports[].common.proceeds", source="derived_flag")
    else:
        blk.missing.append("Динамику выручки построить нельзя, нужен минимум два года отчётности")

    proceeds_series = [r["proceeds"] for r in rows if r["proceeds"] is not None]
    if len(proceeds_series) >= 3:
        blk.add("fin.proceeds_two_year_decline", "Выручка снижается два года подряд",
                proceeds_series[-1] < proceeds_series[-2] < proceeds_series[-3],
                "report.finReports[].common.proceeds", source="derived_flag")

    loss_years = [r["year"] for r in rows if r["profit"] is not None and r["profit"] < 0]
    blk.add("fin.loss_years", "Годы с убытком", loss_years, "report.finReports[].common.profit")
    blk.add("fin.has_loss", "Есть убыточные годы", bool(loss_years),
            "report.finReports[].common.profit", source="derived_flag")

    if last["capitals"] is not None:
        blk.add("fin.capitals_last", "Собственный капитал", last["capitals"],
                "report.finReports[].liabilities.capitals", unit="руб")
        blk.add("fin.negative_capitals", "Отрицательный собственный капитал",
                last["capitals"] < 0, "report.finReports[].liabilities.capitals", source="derived_flag")

    if last["total_liabilities"] and last["capitals"] is not None and last["total_liabilities"] != 0:
        blk.add("fin.capital_share_pct", "Доля собственного капитала в пассивах",
                round(last["capitals"] / last["total_liabilities"] * 100, 1),
                "report.finReports[].liabilities", unit="%")

    if last["proceeds"] and last["accounts_payable"] is not None and last["proceeds"] != 0:
        blk.add("fin.payables_to_proceeds_pct", "Кредиторская задолженность к выручке",
                round(last["accounts_payable"] / last["proceeds"] * 100, 1),
                "report.finReports[].liabilities.shortTermLiabilities.accountsPayable", unit="%")

    _add_coefficients(rep, blk)
    return blk


def _add_coefficients(rep: Dict[str, Any], blk: FactBlock) -> None:
    coef = rep.get("coefficient") or {}
    if coef and any(coef.get(k) is not None for k in ("sustainability", "solvency", "profitability")):
        blk.add("fin.coefficients", "Коэффициенты",
                {"year": as_int(coef.get("year")),
                 "sustainability": as_float(coef.get("sustainability")),
                 "solvency": as_float(coef.get("solvency")),
                 "profitability": as_float(coef.get("profitability"))},
                "report.coefficient", source="raw")
    else:
        blk.missing.append("Финансовых коэффициентов в карточке нет (report.coefficient)")


# ============ Блок 4. Опыт и позитивные сигналы ======================

def build_experience(document: Dict[str, Any]) -> FactBlock:
    rep = _report(document)
    blk = FactBlock("experience", BLOCK_TITLES["experience"])
    has_any = False

    procurements = [p for p in _arr(rep.get("procurements")) if isinstance(p, dict)]
    if procurements:
        has_any = True
        won = sum(as_int(p.get("tenderWinnerCnt")) or 0 for p in procurements)
        signed = sum(as_int(p.get("contractSignedCnt")) or 0 for p in procurements)
        amount = sum(as_float(p.get("contractSignedAmt")) or 0.0 for p in procurements)
        years = sorted({as_int(p.get("procurementsYear")) for p in procurements
                        if as_int(p.get("procurementsYear")) is not None})
        blk.add("procurement.years", "Годы участия в закупках", years,
                "report.procurements[].procurementsYear")
        blk.add("procurement.tenders_won", "Выигранных тендеров", won,
                "report.procurements[].tenderWinnerCnt", unit="шт")
        blk.add("procurement.contracts_signed", "Заключённых контрактов", signed,
                "report.procurements[].contractSignedCnt", unit="шт")
        blk.add("procurement.contracts_amount", "Сумма заключённых контрактов", round(amount, 2),
                "report.procurements[].contractSignedAmt", unit="руб")
        blk.add("procurement.laws", "Законы о закупках",
                sorted({p.get("federalLawCode") for p in procurements if p.get("federalLawCode")}),
                "report.procurements[].federalLawCode", source="raw")
        blk.add("procurement.conversion_gap", "Тендеры выиграны, но контракты не подписаны",
                bool(won > 0 and signed == 0), "report.procurements[]", source="derived_flag")
    else:
        blk.missing.append("Данных о госзакупках в карточке нет (report.procurements)")

    licenses = [l for l in _arr(rep.get("licenses")) if isinstance(l, dict)]
    if licenses:
        has_any = True
        active = [l for l in licenses if (l.get("status") or "").upper() in ("ACTIVE", "INDEFINITE")]
        blk.add("license.count", "Лицензий", len(licenses), "report.licenses[]", unit="шт")
        blk.add("license.active_count", "Действующих лицензий", len(active),
                "report.licenses[].status", unit="шт")
        blk.add("license.items", "Лицензии",
                [{"name": l.get("name"), "number": l.get("number"),
                  "status": l.get("status"), "authority": l.get("issuingAuthority"),
                  "issue_date": parse_date(l.get("issueDate")),
                  "end_date": parse_date(l.get("endDate"))} for l in licenses[:10]],
                "report.licenses[]", source="raw")
    else:
        blk.missing.append("Лицензий в карточке нет (report.licenses)")

    positive = [p for p in _arr(dig(rep, "reputationalRisks", "positive")) if isinstance(p, dict)]
    if positive:
        has_any = True
        by_chapter: Dict[str, int] = {}
        for p in positive:
            chapter = p.get("chapter") or "other"
            by_chapter[chapter] = by_chapter.get(chapter, 0) + 1
        blk.add("positive.count", "Пройденных проверок-маркеров", len(positive),
                "report.reputationalRisks.positive[]", unit="шт")
        blk.add("positive.codes", "Коды позитивных маркеров",
                [{"code": p.get("code"), "chapter": p.get("chapter")} for p in positive],
                "report.reputationalRisks.positive[].code", source="raw",
                comment="Позитивный маркер означает, что проверка по этому реестру пройдена")
        blk.add("positive.by_chapter", "Позитивные маркеры по разделам", by_chapter,
                "report.reputationalRisks.positive[].chapter")
    else:
        blk.missing.append("Позитивных маркеров в карточке нет (report.reputationalRisks.positive)")

    blk.has_data = has_any
    return blk


# ===================== Полнота данных (S6) ===========================

COVERAGE_BLOCKS = [
    ("founders", "Учредители", "report.foundersInfo.cofounders"),
    ("related", "Связанные компании", "report.relatedCompanies"),
    ("arbitration", "Судебные дела", "report.arbitrationCases"),
    ("execproc", "Исполнительные производства", "report.executionProceedings"),
    ("inspections", "Проверки", "report.inspections"),
    ("fin_reports", "Финансовая отчётность", "report.finReports"),
    ("coefficients", "Финансовые коэффициенты", "report.coefficient"),
    ("licenses", "Лицензии", "report.licenses"),
    ("procurements", "Госзакупки", "report.procurements"),
]


def build_coverage(document: Dict[str, Any]) -> Dict[str, Any]:
    """Паспорт полноты: какие блоки данных заполнены, а какие пусты."""
    rep = _report(document)
    blocks = []
    for key, title, path in COVERAGE_BLOCKS:
        node = rep
        for part in path.split(".")[1:]:
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, list):
            filled = len(node) > 0
            count = len(node)
        elif isinstance(node, dict):
            filled = any(v is not None for v in node.values())
            count = 1 if filled else 0
        else:
            filled = node is not None
            count = 1 if filled else 0
        blocks.append({"key": key, "title": title, "field_ref": path,
                       "filled": filled, "items": count})
    filled_count = sum(1 for b in blocks if b["filled"])
    return {
        "blocks": blocks,
        "filled_blocks": filled_count,
        "total_blocks": len(COVERAGE_BLOCKS),
        "coverage_pct": round(filled_count / len(COVERAGE_BLOCKS) * 100, 1),
        "empty_blocks": [b["title"] for b in blocks if not b["filled"]],
    }


BUILDERS = {
    "identity": build_identity,
    "reliability": build_reliability,
    "finance": build_finance,
    "experience": build_experience,
}


def build_all_blocks(document: Dict[str, Any]) -> Dict[str, FactBlock]:
    """Четыре блока фактов по одной карточке.

    Документ берём как есть: обёртки Mongo (``$date``, ``$numberLong``)
    снимаются точечно в геттерах ``as_int``, ``as_float``, ``parse_date``.
    """
    return {key: builder(document) for key, builder in BUILDERS.items()}


def fact_index(blocks: Dict[str, FactBlock]) -> Dict[str, Fact]:
    """Плоский индекс всех фактов прогона: fact_id -> Fact (для S5)."""
    index: Dict[str, Fact] = {}
    for blk in blocks.values():
        index.update(blk.index())
    return index

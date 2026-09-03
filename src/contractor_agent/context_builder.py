"""Детерминированная сборка релевантных фактов для будущего LLM-слоя."""

from __future__ import annotations

from typing import Any, Mapping

from .models import FactEvidence, FactSignal, NormalizedCompanyProfile


_DOMAIN_BLOCKS = (
    ("COMPANY_IDENTITY", "company_identity"),
    ("BANK_RISK", "bank_risk"),
    ("OWNERSHIP", "ownership"),
    ("RELATED_COMPANIES", "related_companies"),
    ("BUSINESS_PROFILE", "business_profile"),
    ("FINANCIAL_HEALTH", "financial_health"),
    ("LEGAL_RISKS", "legal_risks"),
    ("ENFORCEMENT", "enforcement"),
    ("COMPLIANCE", "compliance"),
    ("PROCUREMENT", "procurement"),
)
_DOMAIN_KEYWORDS = {
    "COMPANY_IDENTITY": (
        "инн",
        "огрн",
        "назван",
        "адрес",
        "регистрац",
        "возраст",
        "статус",
        "identity",
    ),
    "BANK_RISK": ("zsk", "зск", "светофор", "банковск"),
    "OWNERSHIP": (
        "учред",
        "владел",
        "директор",
        "руковод",
        "ownership",
    ),
    "RELATED_COMPANIES": ("связан", "аффилир", "дочерн", "related"),
    "BUSINESS_PROFILE": (
        "оквэд",
        "деятельност",
        "филиал",
        "налоговый режим",
        "business profile",
    ),
    "FINANCIAL_HEALTH": (
        "финанс",
        "выруч",
        "прибыл",
        "убыт",
        "актив",
        "обязательств",
        "капитал",
        "ликвид",
        "дебитор",
        "кредитор",
        "finance",
        "revenue",
        "profit",
    ),
    "LEGAL_RISKS": (
        "суд",
        "арбитраж",
        "ответчик",
        "истец",
        "иск",
        "legal",
    ),
    "ENFORCEMENT": (
        "исполнительн",
        "пристав",
        "взыскан",
        "enforcement",
    ),
    "COMPLIANCE": (
        "фнс",
        "налоговая задолж",
        "налоговые долг",
        "налоговые проблем",
        "налоговая отчет",
        "репутац",
        "лиценз",
        "проверк",
        "реестр",
        "compliance",
    ),
    "PROCUREMENT": ("закуп", "тендер", "госконтракт", "procurement"),
}
_METRIC_DOMAINS = {
    "company_age_years": "COMPANY_IDENTITY",
    "is_active_company": "COMPANY_IDENTITY",
    "founder_count": "OWNERSHIP",
    "max_owner_share": "OWNERSHIP",
    "director_tenure_years": "OWNERSHIP",
    "is_director_also_founder": "OWNERSHIP",
    "related_company_count": "RELATED_COMPANIES",
    "unique_related_director_count": "RELATED_COMPANIES",
    "okved_count": "BUSINESS_PROFILE",
    "branches_count": "BUSINESS_PROFILE",
    "revenue_growth_yoy": "FINANCIAL_HEALTH",
    "profit_margin": "FINANCIAL_HEALTH",
    "revenue_trend": "FINANCIAL_HEALTH",
    "profit_trend": "FINANCIAL_HEALTH",
    "current_assets_to_short_term_liabilities": "FINANCIAL_HEALTH",
    "cash_to_short_term_liabilities": "FINANCIAL_HEALTH",
    "receivables_share": "FINANCIAL_HEALTH",
    "liabilities_to_assets": "FINANCIAL_HEALTH",
    "total_arbitration_cases": "LEGAL_RISKS",
    "defendant_cases_count": "LEGAL_RISKS",
    "defendant_pending_cases_count": "LEGAL_RISKS",
    "defendant_pending_amount": "LEGAL_RISKS",
    "defendant_cases_share": "LEGAL_RISKS",
    "arbitration_amount_to_revenue": "LEGAL_RISKS",
    "total_execution_count": "ENFORCEMENT",
    "active_execution_count": "ENFORCEMENT",
    "active_execution_amount": "ENFORCEMENT",
    "latest_execution_date": "ENFORCEMENT",
    "tender_count": "PROCUREMENT",
    "winner_count": "PROCUREMENT",
    "signed_contract_amount": "PROCUREMENT",
}
_CORE_DOMAINS = {"COMPANY_IDENTITY", "BANK_RISK"}


def _profile_dict(
    profile: NormalizedCompanyProfile | Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(profile, NormalizedCompanyProfile):
        return profile.to_dict()
    return dict(profile)


def select_relevant_domains(question: str) -> list[str]:
    """Выбирает домены простым и проверяемым keyword routing."""
    normalized_question = question.casefold().replace("ё", "е")
    selected = {
        domain
        for domain, keywords in _DOMAIN_KEYWORDS.items()
        if any(keyword in normalized_question for keyword in keywords)
    }
    if not selected:
        selected = {domain for domain, _ in _DOMAIN_BLOCKS}
    selected.update(_CORE_DOMAINS)
    return [domain for domain, _ in _DOMAIN_BLOCKS if domain in selected]


def _raw_domain_facts(
    profile: dict[str, Any], domain: str, block_name: str
) -> list[FactSignal]:
    value = profile.get(block_name)
    if domain == "COMPLIANCE" and isinstance(value, dict):
        value = {key: item for key, item in value.items() if key != "source_signals"}
    facts: list[FactSignal] = []

    def append_value(current: Any, path: list[str]) -> None:
        is_money = (
            isinstance(current, dict)
            and set(current) == {"value", "unit"}
            and current.get("unit") == "RUB"
        )
        if isinstance(current, dict) and current and not is_money:
            for key, item in current.items():
                append_value(item, [*path, key])
            return

        relative_path = ".".join(path)
        source = f"normalized_profile.{relative_path}"
        code = "_".join([domain, *path[1:]]).upper()
        facts.append(
            FactSignal(
                code=code,
                domain=domain,
                type="RAW_FACT",
                value=current,
                description=f"Нормализованный исходный факт {relative_path}.",
                source=source,
                evidence=[
                    FactEvidence(
                        source_type="RAW_FACT",
                        source_path=source,
                        metric=relative_path,
                        value=current,
                    )
                ],
            )
        )

    append_value(value, [block_name])
    return facts


def _derived_metric_fact(
    metric_name: str, metric: dict[str, Any], domain: str
) -> FactSignal:
    source = f"normalized_profile.derived_metrics.{metric_name}"
    metric_value = {
        "value": metric.get("value"),
        "status": metric.get("status"),
        "unit": metric.get("unit"),
        "formula": metric.get("formula"),
        "source_fields": metric.get("source_fields", []),
    }
    return FactSignal(
        code=metric_name.upper(),
        domain=domain,
        type="DERIVED_METRIC",
        value=metric_value,
        description=f"Прозрачно рассчитанная метрика {metric_name}.",
        source=source,
        evidence=[
            FactEvidence(
                source_type="DERIVED_METRIC",
                source_path=source,
                metric=metric_name,
                value=metric_value,
            )
        ],
    )


def build_context(
    profile: NormalizedCompanyProfile | Mapping[str, Any], question: str
) -> dict[str, Any]:
    """Собирает минимальный контекст по релевантным бизнес-доменам."""
    data = _profile_dict(profile)
    selected_domains = select_relevant_domains(question)
    selected_set = set(selected_domains)
    facts: list[FactSignal] = []

    for domain, block_name in _DOMAIN_BLOCKS:
        if domain in selected_set:
            facts.extend(_raw_domain_facts(data, domain, block_name))

    derived_metrics = data.get("derived_metrics")
    if isinstance(derived_metrics, dict):
        for metric_name, metric in derived_metrics.items():
            domain = _METRIC_DOMAINS.get(metric_name)
            if domain in selected_set and isinstance(metric, dict):
                facts.append(_derived_metric_fact(metric_name, metric, domain))

    compliance = data.get("compliance")
    source_signals = (
        compliance.get("source_signals", [])
        if isinstance(compliance, dict)
        else []
    )
    for signal in source_signals:
        if (
            isinstance(signal, dict)
            and signal.get("type") == "SOURCE_SIGNAL"
            and signal.get("domain") in selected_set
        ):
            facts.append(
                FactSignal(
                    code=str(signal.get("code") or "UNKNOWN_SOURCE_SIGNAL"),
                    domain=str(signal["domain"]),
                    type="SOURCE_SIGNAL",
                    value=signal.get("value"),
                    description=str(signal.get("description") or ""),
                    source=str(signal.get("source") or ""),
                    evidence=[
                        FactEvidence(
                            source_type=str(evidence.get("source_type") or "SOURCE_SIGNAL"),
                            source_path=str(evidence.get("source_path") or ""),
                            metric=str(evidence.get("metric") or "source_signal"),
                            value=evidence.get("value"),
                        )
                        for evidence in signal.get("evidence", [])
                        if isinstance(evidence, dict)
                    ],
                )
            )

    data_quality = data.get("data_quality")
    if not isinstance(data_quality, dict):
        data_quality = {"conflicts": [], "warnings": []}
    return {
        "question": question,
        "selected_domains": selected_domains,
        "facts": [fact.to_dict() for fact in facts],
        "data_quality": data_quality,
    }

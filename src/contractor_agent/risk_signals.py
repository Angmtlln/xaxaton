"""Детерминированные risk signals поверх нормализованного профиля."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .models import NormalizedCompanyProfile, RiskSignal, SignalEvidence


@dataclass(frozen=True, slots=True)
class RiskRuleConfig:
    """Явные MVP-пороги, которые можно заменить после экспертной калибровки."""

    low_liquidity_ratio: float = 1.0
    high_defendant_amount_rub: float = 1_000_000.0
    high_defendant_amount_to_revenue: float = 0.10
    repeated_arbitration_cases: int = 3
    mass_auth_person_codes: tuple[str, ...] = ("MASS_AUTHPERSONS",)
    ownership_conflict_codes: tuple[str, ...] = (
        "MASS_AUTHPERSONS",
        "INVALID_AUTHPERSONS_DATA",
        "DISQUALIFIED_AUTHPERSONS",
    )
    tax_reputation_codes: tuple[str, ...] = (
        "FNS_BLOCKING",
        "TAX_ARREARS",
        "TAX_REPORTING",
    )

    def __post_init__(self) -> None:
        if self.low_liquidity_ratio <= 0:
            raise ValueError("low_liquidity_ratio должен быть больше нуля")
        if self.high_defendant_amount_rub < 0:
            raise ValueError("high_defendant_amount_rub не может быть отрицательным")
        if self.high_defendant_amount_to_revenue < 0:
            raise ValueError(
                "high_defendant_amount_to_revenue не может быть отрицательным"
            )
        if self.repeated_arbitration_cases < 1:
            raise ValueError("repeated_arbitration_cases должен быть не меньше 1")


def _profile_dict(
    profile: NormalizedCompanyProfile | Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(profile, NormalizedCompanyProfile):
        return profile.to_dict()
    return dict(profile)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _format_number(value: int | float) -> str:
    if float(value).is_integer():
        return f"{value:,.0f}".replace(",", " ")
    return f"{value:,.2f}".rstrip("0").rstrip(".").replace(",", " ")


def _derived_metric(profile: dict[str, Any], name: str) -> dict[str, Any] | None:
    metric = _as_dict(_as_dict(profile.get("derived_metrics")).get(name))
    if metric.get("status") not in {"CALCULATED", "PARTIAL"}:
        return None
    if metric.get("value") is None:
        return None
    return metric


def _derived_evidence(profile: dict[str, Any], name: str) -> SignalEvidence | None:
    metric = _derived_metric(profile, name)
    if metric is None:
        return None
    source_fields = metric.get("source_fields")
    return SignalEvidence(
        metric=name,
        value=metric.get("value"),
        source_fields=list(source_fields) if isinstance(source_fields, list) else [],
    )


def _latest_statement_evidence(
    profile: dict[str, Any], field_path: tuple[str, ...], metric_name: str
) -> SignalEvidence | None:
    statements = _dict_items(_as_dict(profile.get("financial_health")).get("statements"))
    candidates: list[tuple[int | float, int | float]] = []
    for statement in statements:
        year = _number(statement.get("year"))
        current: Any = statement
        for key in field_path:
            current = current.get(key) if isinstance(current, dict) else None
        value = _number(current)
        if year is not None and value is not None:
            candidates.append((year, value))
    if not candidates:
        return None
    year, value = max(candidates, key=lambda item: item[0])
    normalized_path = ".".join(field_path)
    return SignalEvidence(
        metric=metric_name,
        value=value,
        source_fields=[
            f"financial_health.statements[year={year}].{normalized_path}"
        ],
    )


def _signal_evidence(
    signals: Iterable[dict[str, Any]], codes: set[str], metric_name: str
) -> list[SignalEvidence]:
    evidence: list[SignalEvidence] = []
    for signal in signals:
        if signal.get("code") not in codes:
            continue
        source = signal.get("source")
        evidence.append(
            SignalEvidence(
                metric=metric_name,
                value=signal.get("code"),
                source_fields=[source] if isinstance(source, str) else [],
            )
        )
    return evidence


def _add_signal(
    signals: list[RiskSignal],
    *,
    code: str,
    domain: str,
    severity: str,
    description: str,
    evidence: Iterable[SignalEvidence | None],
    rule: str,
) -> None:
    resolved_evidence = [item for item in evidence if item is not None]
    if not resolved_evidence:
        return
    signals.append(
        RiskSignal(
            code=code,
            domain=domain,
            severity=severity,  # type: ignore[arg-type]
            description=description,
            evidence=resolved_evidence,
            rule=rule,
        )
    )


def generate_risk_signals(
    profile: NormalizedCompanyProfile | Mapping[str, Any],
    config: RiskRuleConfig | None = None,
) -> list[RiskSignal]:
    """Применяет независимые правила и возвращает только сработавшие сигналы."""
    data = _profile_dict(profile)
    rules = config or RiskRuleConfig()
    result: list[RiskSignal] = []

    latest_profit = _latest_statement_evidence(
        data, ("profit",), "latest_profit"
    )
    if latest_profit and latest_profit.value < 0:
        _add_signal(
            result,
            code="NEGATIVE_PROFIT",
            domain="FINANCE",
            severity="MEDIUM",
            description=(
                "Последняя доступная прибыль отрицательная: "
                f"{_format_number(latest_profit.value)} ₽."
            ),
            evidence=[latest_profit],
            rule="latest_profit < 0",
        )

    revenue_growth = _derived_evidence(data, "revenue_growth_yoy")
    if revenue_growth and _number(revenue_growth.value) is not None and revenue_growth.value < 0:
        _add_signal(
            result,
            code="REVENUE_DECLINE",
            domain="FINANCE",
            severity="MEDIUM",
            description=f"Выручка снизилась год к году на {abs(revenue_growth.value):.1%}.",
            evidence=[revenue_growth],
            rule="revenue_growth_yoy < 0",
        )

    latest_equity = _latest_statement_evidence(
        data, ("liabilities", "capital"), "latest_equity"
    )
    if latest_equity and latest_equity.value < 0:
        _add_signal(
            result,
            code="NEGATIVE_EQUITY",
            domain="FINANCE",
            severity="HIGH",
            description=(
                "Последний доступный капитал отрицательный: "
                f"{_format_number(latest_equity.value)} ₽."
            ),
            evidence=[latest_equity],
            rule="latest_equity < 0",
        )

    liquidity = _derived_evidence(
        data, "current_assets_to_short_term_liabilities"
    )
    if (
        liquidity
        and _number(liquidity.value) is not None
        and liquidity.value < rules.low_liquidity_ratio
    ):
        _add_signal(
            result,
            code="LOW_LIQUIDITY",
            domain="FINANCE",
            severity="HIGH",
            description=(
                "Оборотные активы ниже краткосрочных обязательств: "
                f"коэффициент {liquidity.value:.3f}."
            ),
            evidence=[liquidity],
            rule=(
                "current_assets_to_short_term_liabilities "
                f"< {rules.low_liquidity_ratio:g}"
            ),
        )

    pending_cases = _derived_evidence(data, "defendant_pending_cases_count")
    if pending_cases and _number(pending_cases.value) is not None and pending_cases.value > 0:
        _add_signal(
            result,
            code="OPEN_DEFENDANT_CASES",
            domain="LEGAL",
            severity="MEDIUM",
            description=(
                "Есть открытые дела в роли ответчика: "
                f"{_format_number(pending_cases.value)}."
            ),
            evidence=[pending_cases],
            rule="defendant_pending_cases_count > 0",
        )

    pending_amount = _derived_evidence(data, "defendant_pending_amount")
    amount_to_revenue = _derived_evidence(data, "arbitration_amount_to_revenue")
    amount_is_high = bool(
        pending_amount
        and _number(pending_amount.value) is not None
        and pending_amount.value >= rules.high_defendant_amount_rub
    )
    relative_amount_is_high = bool(
        amount_to_revenue
        and _number(amount_to_revenue.value) is not None
        and amount_to_revenue.value >= rules.high_defendant_amount_to_revenue
    )
    if amount_is_high or relative_amount_is_high:
        evidence = []
        if pending_amount is not None:
            evidence.append(pending_amount)
        if amount_to_revenue is not None:
            evidence.append(amount_to_revenue)
        _add_signal(
            result,
            code="HIGH_DEFENDANT_AMOUNT",
            domain="LEGAL",
            severity="HIGH",
            description=(
                "Сумма требований к компании существенна по абсолютному размеру "
                "или относительно выручки."
            ),
            evidence=evidence,
            rule=(
                f"defendant_pending_amount >= {rules.high_defendant_amount_rub:g} "
                "OR arbitration_amount_to_revenue >= "
                f"{rules.high_defendant_amount_to_revenue:g}"
            ),
        )

    defendant_cases = _derived_evidence(data, "defendant_cases_count")
    if (
        defendant_cases
        and _number(defendant_cases.value) is not None
        and defendant_cases.value >= rules.repeated_arbitration_cases
    ):
        _add_signal(
            result,
            code="REPEATED_ARBITRATION",
            domain="LEGAL",
            severity="MEDIUM",
            description=(
                "Компания выступала ответчиком минимум в "
                f"{_format_number(defendant_cases.value)} делах."
            ),
            evidence=[defendant_cases],
            rule=f"defendant_cases_count >= {rules.repeated_arbitration_cases}",
        )

    active_executions = _derived_evidence(data, "active_execution_count")
    active_execution_amount = _derived_evidence(data, "active_execution_amount")
    if (
        active_executions
        and _number(active_executions.value) is not None
        and active_executions.value > 0
    ):
        _add_signal(
            result,
            code="ACTIVE_EXECUTION_PROCEEDINGS",
            domain="ENFORCEMENT",
            severity="HIGH",
            description=(
                "Есть активные исполнительные производства: "
                f"{_format_number(active_executions.value)}."
            ),
            evidence=[active_executions, active_execution_amount],
            rule="active_execution_count > 0",
        )

    compliance = _as_dict(data.get("compliance"))
    negative_reputational = _dict_items(compliance.get("negative_signals"))
    positive_reputational = _dict_items(compliance.get("positive_signals"))

    mass_auth_evidence = _signal_evidence(
        negative_reputational,
        set(rules.mass_auth_person_codes),
        "negative_reputational_signal",
    )
    if mass_auth_evidence:
        _add_signal(
            result,
            code="MASS_AUTH_PERSON",
            domain="OWNERSHIP",
            severity="HIGH",
            description="Есть негативный реестровый признак массового руководителя или учредителя.",
            evidence=mass_auth_evidence,
            rule=(
                "negative_reputational_signal.code IN "
                f"{sorted(rules.mass_auth_person_codes)}"
            ),
        )

    ownership_conflict_codes = set(rules.ownership_conflict_codes)
    ownership_conflict_evidence: list[SignalEvidence] = []
    for index, conflict in enumerate(_dict_items(data.get("conflicts"))):
        source_fields = conflict.get("source_fields")
        normalized_sources = list(source_fields) if isinstance(source_fields, list) else []
        code = conflict.get("code")
        is_ownership_conflict = code in ownership_conflict_codes or any(
            "foundersInfo" in str(source) for source in normalized_sources
        )
        if is_ownership_conflict:
            ownership_conflict_evidence.append(
                SignalEvidence(
                    metric="ownership_source_conflict",
                    value=code or conflict.get("type"),
                    source_fields=normalized_sources or [f"conflicts[{index}]"],
                )
            )
    positive_ownership_codes = {
        item.get("code")
        for item in positive_reputational
        if item.get("domain") == "ownership"
    }
    negative_ownership_codes = {
        item.get("code")
        for item in negative_reputational
        if item.get("domain") == "ownership"
    }
    for code in sorted(
        positive_ownership_codes & negative_ownership_codes & ownership_conflict_codes
    ):
        ownership_conflict_evidence.extend(
            _signal_evidence(
                positive_reputational + negative_reputational,
                {code},
                "ownership_reputational_signal_conflict",
            )
        )
    if ownership_conflict_evidence:
        _add_signal(
            result,
            code="OWNERSHIP_DATA_CONFLICT",
            domain="OWNERSHIP",
            severity="MEDIUM",
            description="Источники содержат противоречивые сведения о владельцах или руководителях.",
            evidence=ownership_conflict_evidence,
            rule=(
                "SOURCE_CONFLICT.code IN "
                f"{sorted(ownership_conflict_codes)} OR ownership signal has both polarities"
            ),
        )

    is_active = _derived_evidence(data, "is_active_company")
    if is_active and is_active.value is False:
        _add_signal(
            result,
            code="COMPANY_CLOSED",
            domain="REGISTRY",
            severity="HIGH",
            description="Статус компании отличается от CURRENT.",
            evidence=[is_active],
            rule="is_active_company == false",
        )

    tax_evidence = _signal_evidence(
        negative_reputational,
        set(rules.tax_reputation_codes),
        "negative_tax_reputational_signal",
    )
    if tax_evidence:
        _add_signal(
            result,
            code="TAX_REPUTATION_RISK",
            domain="REGISTRY",
            severity="HIGH",
            description="Есть негативный сигнал ФНС о блокировках, задолженности или отчётности.",
            evidence=tax_evidence,
            rule=(
                "negative_reputational_signal.code IN "
                f"{sorted(rules.tax_reputation_codes)}"
            ),
        )

    return result


def generate_risk_signal_dicts(
    profile: NormalizedCompanyProfile | Mapping[str, Any],
    config: RiskRuleConfig | None = None,
) -> list[dict[str, Any]]:
    """Возвращает JSON-совместимый список сработавших сигналов."""
    return [signal.to_dict() for signal in generate_risk_signals(profile, config)]

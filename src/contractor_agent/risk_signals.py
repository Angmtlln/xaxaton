"""Детерминированные risk signals поверх нормализованного профиля."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .models import NormalizedCompanyProfile, RiskSignal, SignalEvidence


DEFAULT_RULE_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "risk_rules.json"
_IMPACT_LEVELS = {"LOW", "MEDIUM", "HIGH"}


@dataclass(frozen=True, slots=True)
class RiskRuleConfig:
    """Версионированная конфигурация порогов deterministic rules."""

    values: Mapping[str, Any]

    def __post_init__(self) -> None:
        version = self.values.get("rule_version")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("risk config должен содержать непустой rule_version")
        _validate_rule_config(self)

    @property
    def rule_version(self) -> str:
        return str(self.values["rule_version"])

    def get(self, path: Sequence[str]) -> Any:
        current: Any = self.values
        for key in path:
            if not isinstance(current, Mapping) or key not in current:
                raise ValueError(f"В risk config отсутствует {'.'.join(path)}")
            current = current[key]
        return current


def load_rule_config(path: str | Path = DEFAULT_RULE_CONFIG_PATH) -> RiskRuleConfig:
    """Загружает и минимально валидирует JSON-конфигурацию правил."""
    with Path(path).open(encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError("risk config должен быть JSON-объектом")
    return RiskRuleConfig(value)


@lru_cache(maxsize=1)
def _default_rule_config() -> RiskRuleConfig:
    return load_rule_config()


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
    if isinstance(value, dict) and value.get("unit") == "RUB":
        return _number(value.get("value"))
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _config_number(config: RiskRuleConfig, *path: str) -> int | float:
    value = _number(config.get(path))
    if value is None:
        raise ValueError(f"Порог {'.'.join(path)} должен быть числом")
    return value


def _config_impact(config: RiskRuleConfig, *path: str) -> str:
    value = config.get(path)
    if value not in _IMPACT_LEVELS:
        raise ValueError(
            f"impact_level {'.'.join(path)} должен быть LOW, MEDIUM или HIGH"
        )
    return str(value)


def _config_codes(config: RiskRuleConfig, *path: str) -> set[str]:
    value = config.get(path)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{'.'.join(path)} должен быть списком строк")
    return set(value)


def _validate_rule_config(config: RiskRuleConfig) -> None:
    revenue_warning = _config_number(
        config, "finance", "revenue_decline", "warning"
    )
    revenue_critical = _config_number(
        config, "finance", "revenue_decline", "critical"
    )
    liquidity_warning = _config_number(
        config, "finance", "low_liquidity", "warning"
    )
    liquidity_critical = _config_number(
        config, "finance", "low_liquidity", "critical"
    )
    if not revenue_critical <= revenue_warning < 0:
        raise ValueError("revenue_decline: critical <= warning < 0")
    if not 0 < liquidity_critical <= liquidity_warning:
        raise ValueError("low_liquidity: 0 < critical <= warning")

    ordered_thresholds = (
        ("legal", "open_defendant_cases", "warning_count", "critical_count"),
        ("legal", "high_defendant_amount", "warning_rub", "critical_rub"),
        (
            "legal",
            "high_defendant_amount",
            "warning_to_revenue",
            "critical_to_revenue",
        ),
        ("legal", "repeated_arbitration", "warning_count", "critical_count"),
        ("enforcement", "active_proceedings", "warning_count", "critical_count"),
    )
    for domain, rule, warning_name, critical_name in ordered_thresholds:
        warning = _config_number(config, domain, rule, warning_name)
        critical = _config_number(config, domain, rule, critical_name)
        if warning < 0 or critical < warning:
            raise ValueError(
                f"{domain}.{rule}: 0 <= {warning_name} <= {critical_name}"
            )

    for path in (
        ("finance", "negative_profit", "impact_level"),
        ("finance", "negative_equity", "impact_level"),
        ("ownership", "mass_auth_person", "impact_level"),
        ("ownership", "data_conflict", "impact_level"),
        ("registry", "company_closed", "impact_level"),
        ("registry", "tax_reputation_risk", "impact_level"),
    ):
        _config_impact(config, *path)
    for path in (
        ("ownership", "mass_auth_person", "source_codes"),
        ("ownership", "data_conflict", "source_codes"),
        ("registry", "tax_reputation_risk", "source_codes"),
    ):
        _config_codes(config, *path)


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
    return SignalEvidence(
        source_type="DERIVED_METRIC",
        source_path=f"derived_metrics.{name}",
        metric=name,
        value=metric.get("value"),
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
        source_type="NORMALIZED_FIELD",
        source_path=f"financial_health.statements[year={year}].{normalized_path}",
        metric=metric_name,
        value=value,
    )


def _source_signal_evidence(
    signals: Iterable[tuple[int, dict[str, Any]]], codes: set[str]
) -> list[SignalEvidence]:
    evidence: list[SignalEvidence] = []
    for index, signal in signals:
        if signal.get("code") not in codes or signal.get("type") != "NEGATIVE":
            continue
        evidence.append(
            SignalEvidence(
                source_type="SOURCE_SIGNAL",
                source_path=f"compliance.source_signals[{index}]",
                metric="reputational_signal_code",
                value=signal.get("code"),
            )
        )
    return evidence


def _add_signal(
    signals: list[RiskSignal],
    *,
    code: str,
    domain: str,
    signal_type: str,
    impact_level: str,
    description: str,
    evidence: Iterable[SignalEvidence | None],
    rule: str,
    rule_version: str,
) -> None:
    resolved_evidence = [item for item in evidence if item is not None]
    if not resolved_evidence:
        return
    signals.append(
        RiskSignal(
            code=code,
            domain=domain,
            type=signal_type,  # type: ignore[arg-type]
            impact_level=impact_level,  # type: ignore[arg-type]
            origin="DERIVED_RULE",
            description=description,
            evidence=resolved_evidence,
            rule=rule,
            rule_version=rule_version,
        )
    )


def generate_risk_signals(
    profile: NormalizedCompanyProfile | Mapping[str, Any],
    config: RiskRuleConfig | None = None,
) -> list[RiskSignal]:
    """Применяет независимые правила и возвращает только derived signals."""
    data = _profile_dict(profile)
    rules = config or _default_rule_config()
    version = rules.rule_version
    result: list[RiskSignal] = []

    latest_profit = _latest_statement_evidence(data, ("profit",), "latest_profit")
    profit_threshold = _config_number(
        rules, "finance", "negative_profit", "threshold_rub"
    )
    if latest_profit and latest_profit.value < profit_threshold:
        _add_signal(
            result,
            code="NEGATIVE_PROFIT",
            domain="FINANCE",
            signal_type="NEGATIVE",
            impact_level=_config_impact(
                rules, "finance", "negative_profit", "impact_level"
            ),
            description=(
                "Последняя доступная прибыль отрицательная: "
                f"{_format_number(latest_profit.value)} ₽."
            ),
            evidence=[latest_profit],
            rule=f"latest_profit < {profit_threshold:g} RUB",
            rule_version=version,
        )

    revenue_growth = _derived_evidence(data, "revenue_growth_yoy")
    revenue_value = _number(revenue_growth.value) if revenue_growth else None
    revenue_warning = _config_number(
        rules, "finance", "revenue_decline", "warning"
    )
    revenue_critical = _config_number(
        rules, "finance", "revenue_decline", "critical"
    )
    if revenue_value is not None and revenue_value <= revenue_warning:
        _add_signal(
            result,
            code="REVENUE_DECLINE",
            domain="FINANCE",
            signal_type="NEGATIVE",
            impact_level="HIGH" if revenue_value <= revenue_critical else "MEDIUM",
            description=f"Выручка снизилась год к году на {abs(revenue_value):.1%}.",
            evidence=[revenue_growth],
            rule=(
                f"revenue_growth_yoy <= {revenue_warning:g}; HIGH if <= "
                f"{revenue_critical:g}, else MEDIUM"
            ),
            rule_version=version,
        )

    latest_equity = _latest_statement_evidence(
        data, ("liabilities", "capital"), "latest_equity"
    )
    equity_threshold = _config_number(
        rules, "finance", "negative_equity", "threshold_rub"
    )
    if latest_equity and latest_equity.value < equity_threshold:
        _add_signal(
            result,
            code="NEGATIVE_EQUITY",
            domain="FINANCE",
            signal_type="NEGATIVE",
            impact_level=_config_impact(
                rules, "finance", "negative_equity", "impact_level"
            ),
            description=(
                "Последний доступный капитал отрицательный: "
                f"{_format_number(latest_equity.value)} ₽."
            ),
            evidence=[latest_equity],
            rule=f"latest_equity < {equity_threshold:g} RUB",
            rule_version=version,
        )

    liquidity = _derived_evidence(data, "current_assets_to_short_term_liabilities")
    liquidity_value = _number(liquidity.value) if liquidity else None
    liquidity_warning = _config_number(rules, "finance", "low_liquidity", "warning")
    liquidity_critical = _config_number(
        rules, "finance", "low_liquidity", "critical"
    )
    if liquidity_value is not None and liquidity_value < liquidity_warning:
        _add_signal(
            result,
            code="LOW_LIQUIDITY",
            domain="FINANCE",
            signal_type="NEGATIVE",
            impact_level="HIGH" if liquidity_value <= liquidity_critical else "MEDIUM",
            description=(
                "Оборотные активы ниже краткосрочных обязательств: "
                f"коэффициент {liquidity_value:.3f}."
            ),
            evidence=[liquidity],
            rule=(
                "current_assets_to_short_term_liabilities "
                f"< {liquidity_warning:g}; HIGH if <= {liquidity_critical:g}, "
                "else MEDIUM"
            ),
            rule_version=version,
        )

    pending_cases = _derived_evidence(data, "defendant_pending_cases_count")
    pending_count = _number(pending_cases.value) if pending_cases else None
    pending_warning = _config_number(
        rules, "legal", "open_defendant_cases", "warning_count"
    )
    pending_critical = _config_number(
        rules, "legal", "open_defendant_cases", "critical_count"
    )
    if pending_count is not None and pending_count >= pending_warning:
        _add_signal(
            result,
            code="OPEN_DEFENDANT_CASES",
            domain="LEGAL",
            signal_type="NEGATIVE",
            impact_level="HIGH" if pending_count >= pending_critical else "MEDIUM",
            description=f"Есть открытые дела в роли ответчика: {_format_number(pending_count)}.",
            evidence=[pending_cases],
            rule=(
                f"defendant_pending_cases_count >= {pending_warning:g}; HIGH if >= "
                f"{pending_critical:g}, else MEDIUM"
            ),
            rule_version=version,
        )

    pending_amount = _derived_evidence(data, "defendant_pending_amount")
    amount_to_revenue = _derived_evidence(data, "arbitration_amount_to_revenue")
    amount_value = _number(pending_amount.value) if pending_amount else None
    ratio_value = _number(amount_to_revenue.value) if amount_to_revenue else None
    amount_warning = _config_number(
        rules, "legal", "high_defendant_amount", "warning_rub"
    )
    amount_critical = _config_number(
        rules, "legal", "high_defendant_amount", "critical_rub"
    )
    ratio_warning = _config_number(
        rules, "legal", "high_defendant_amount", "warning_to_revenue"
    )
    ratio_critical = _config_number(
        rules, "legal", "high_defendant_amount", "critical_to_revenue"
    )
    amount_is_high = amount_value is not None and amount_value >= amount_warning
    relative_amount_is_high = ratio_value is not None and ratio_value >= ratio_warning
    if amount_is_high or relative_amount_is_high:
        critical = bool(
            (amount_value is not None and amount_value >= amount_critical)
            or (ratio_value is not None and ratio_value >= ratio_critical)
        )
        _add_signal(
            result,
            code="HIGH_DEFENDANT_AMOUNT",
            domain="LEGAL",
            signal_type="NEGATIVE",
            impact_level="HIGH" if critical else "MEDIUM",
            description=(
                "Сумма требований к компании существенна по абсолютному размеру "
                "или относительно выручки."
            ),
            evidence=[pending_amount, amount_to_revenue],
            rule=(
                f"defendant_pending_amount >= {amount_warning:g} RUB OR "
                f"arbitration_amount_to_revenue >= {ratio_warning:g}; HIGH if amount "
                f">= {amount_critical:g} RUB OR ratio >= {ratio_critical:g}, else MEDIUM"
            ),
            rule_version=version,
        )

    defendant_cases = _derived_evidence(data, "defendant_cases_count")
    defendant_count = _number(defendant_cases.value) if defendant_cases else None
    repeated_warning = _config_number(
        rules, "legal", "repeated_arbitration", "warning_count"
    )
    repeated_critical = _config_number(
        rules, "legal", "repeated_arbitration", "critical_count"
    )
    if defendant_count is not None and defendant_count >= repeated_warning:
        _add_signal(
            result,
            code="REPEATED_ARBITRATION",
            domain="LEGAL",
            signal_type="NEGATIVE",
            impact_level="HIGH" if defendant_count >= repeated_critical else "MEDIUM",
            description=(
                "Компания выступала ответчиком минимум в "
                f"{_format_number(defendant_count)} делах."
            ),
            evidence=[defendant_cases],
            rule=(
                f"defendant_cases_count >= {repeated_warning:g}; HIGH if >= "
                f"{repeated_critical:g}, else MEDIUM"
            ),
            rule_version=version,
        )

    active_executions = _derived_evidence(data, "active_execution_count")
    active_execution_amount = _derived_evidence(data, "active_execution_amount")
    execution_count = _number(active_executions.value) if active_executions else None
    execution_warning = _config_number(
        rules, "enforcement", "active_proceedings", "warning_count"
    )
    execution_critical = _config_number(
        rules, "enforcement", "active_proceedings", "critical_count"
    )
    if execution_count is not None and execution_count >= execution_warning:
        _add_signal(
            result,
            code="ACTIVE_EXECUTION_PROCEEDINGS",
            domain="ENFORCEMENT",
            signal_type="NEGATIVE",
            impact_level="HIGH" if execution_count >= execution_critical else "MEDIUM",
            description=(
                "Есть активные исполнительные производства: "
                f"{_format_number(execution_count)}."
            ),
            evidence=[active_executions, active_execution_amount],
            rule=(
                f"active_execution_count >= {execution_warning:g}; HIGH if >= "
                f"{execution_critical:g}, else MEDIUM"
            ),
            rule_version=version,
        )

    compliance = _as_dict(data.get("compliance"))
    indexed_source_signals = list(enumerate(_dict_items(compliance.get("source_signals"))))

    mass_auth_codes = _config_codes(
        rules, "ownership", "mass_auth_person", "source_codes"
    )
    mass_auth_evidence = _source_signal_evidence(indexed_source_signals, mass_auth_codes)
    if mass_auth_evidence:
        _add_signal(
            result,
            code="MASS_AUTH_PERSON",
            domain="OWNERSHIP",
            signal_type="NEGATIVE",
            impact_level=_config_impact(
                rules, "ownership", "mass_auth_person", "impact_level"
            ),
            description="Есть негативный реестровый признак массового руководителя или учредителя.",
            evidence=mass_auth_evidence,
            rule=f"source_signal.code IN {sorted(mass_auth_codes)}",
            rule_version=version,
        )

    ownership_conflict_codes = _config_codes(
        rules, "ownership", "data_conflict", "source_codes"
    )
    ownership_conflict_evidence: list[SignalEvidence] = []
    data_quality = _as_dict(data.get("data_quality"))
    for index, conflict in enumerate(_dict_items(data_quality.get("conflicts"))):
        source_fields = conflict.get("source_fields")
        normalized_sources = list(source_fields) if isinstance(source_fields, list) else []
        code = conflict.get("code")
        is_ownership_conflict = code in ownership_conflict_codes or any(
            "foundersInfo" in str(source) for source in normalized_sources
        )
        if is_ownership_conflict:
            ownership_conflict_evidence.append(
                SignalEvidence(
                    source_type="DATA_QUALITY",
                    source_path=f"data_quality.conflicts[{index}]",
                    metric="ownership_source_conflict",
                    value=code or conflict.get("type"),
                )
            )
    if ownership_conflict_evidence:
        _add_signal(
            result,
            code="OWNERSHIP_DATA_CONFLICT",
            domain="OWNERSHIP",
            signal_type="CONFLICT",
            impact_level=_config_impact(
                rules, "ownership", "data_conflict", "impact_level"
            ),
            description="Источники содержат противоречивые сведения о владельцах или руководителях.",
            evidence=ownership_conflict_evidence,
            rule=f"data_quality.conflicts[].code IN {sorted(ownership_conflict_codes)}",
            rule_version=version,
        )

    is_active = _derived_evidence(data, "is_active_company")
    if is_active and is_active.value is False:
        _add_signal(
            result,
            code="COMPANY_CLOSED",
            domain="REGISTRY",
            signal_type="NEGATIVE",
            impact_level=_config_impact(
                rules, "registry", "company_closed", "impact_level"
            ),
            description="Статус компании отличается от CURRENT.",
            evidence=[is_active],
            rule="is_active_company == false",
            rule_version=version,
        )

    tax_codes = _config_codes(
        rules, "registry", "tax_reputation_risk", "source_codes"
    )
    tax_evidence = _source_signal_evidence(indexed_source_signals, tax_codes)
    if tax_evidence:
        _add_signal(
            result,
            code="TAX_REPUTATION_RISK",
            domain="REGISTRY",
            signal_type="NEGATIVE",
            impact_level=_config_impact(
                rules, "registry", "tax_reputation_risk", "impact_level"
            ),
            description="Есть негативный сигнал ФНС о блокировках, задолженности или отчётности.",
            evidence=tax_evidence,
            rule=f"source_signal.code IN {sorted(tax_codes)}",
            rule_version=version,
        )

    return result


def generate_risk_signal_dicts(
    profile: NormalizedCompanyProfile | Mapping[str, Any],
    config: RiskRuleConfig | None = None,
) -> list[dict[str, Any]]:
    """Возвращает JSON-совместимый список derived signals."""
    return [signal.to_dict() for signal in generate_risk_signals(profile, config)]

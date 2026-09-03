"""Бизнес-модель нормализованного профиля контрагента."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


MetricStatus = Literal["CALCULATED", "PARTIAL", "NOT_AVAILABLE", "NOT_APPLICABLE"]
RiskType = Literal["POSITIVE", "NEGATIVE", "CONFLICT"]
RiskImpactLevel = Literal["LOW", "MEDIUM", "HIGH"]
RiskOrigin = Literal["SOURCE_SIGNAL", "DERIVED_RULE"]


@dataclass(slots=True)
class DerivedMetric:
    metric: str
    value: Any
    source_fields: list[str]
    formula: str
    status: MetricStatus = "CALCULATED"
    unit: str | None = None


@dataclass(slots=True)
class Conflict:
    type: str
    description: str
    source_fields: list[str]
    code: str | None = None


@dataclass(slots=True)
class SignalEvidence:
    source_type: str
    source_path: str
    metric: str
    value: Any


@dataclass(slots=True)
class RiskSignal:
    code: str
    domain: str
    type: RiskType
    impact_level: RiskImpactLevel
    origin: RiskOrigin
    description: str
    evidence: list[SignalEvidence]
    rule: str
    rule_version: str

    def to_dict(self) -> dict[str, Any]:
        """Возвращает JSON-совместимый risk signal."""
        return asdict(self)


@dataclass(slots=True)
class CompanyIdentity:
    source_record_id: str | None
    inn: str | None
    ogrn: str | None
    name: str | None
    full_name: str | None
    address: str | None
    email: str | None
    website: str | None
    company_size: str | None
    registration_date: str | None
    reported_company_age_years: int | float | None
    status: str | None
    status_date: str | None
    status_reason: str | None


@dataclass(slots=True)
class BankRisk:
    base_risk_level: str | None
    zsk_risk_level: str | None
    report_date: str | None


@dataclass(slots=True)
class Ownership:
    source_available: bool
    founders: list[dict[str, Any]] = field(default_factory=list)
    share_capital: dict[str, Any] = field(default_factory=dict)
    director: dict[str, Any] | None = None


@dataclass(slots=True)
class RelatedCompanies:
    source_available: bool
    companies: list[dict[str, Any]] = field(default_factory=list)
    directors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BusinessProfile:
    main_okved: dict[str, Any] | None
    additional_okveds: list[dict[str, Any]] = field(default_factory=list)
    branches_source_available: bool = False
    branches: list[dict[str, Any]] = field(default_factory=list)
    tax_systems: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class FinancialHealth:
    source_available: bool
    statements: list[dict[str, Any]] = field(default_factory=list)
    coefficients: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LegalRisks:
    yearly_cases_source_available: bool
    yearly_cases: list[dict[str, Any]] = field(default_factory=list)
    by_status_source_available: bool = False
    by_status: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Enforcement:
    source_available: bool
    proceedings: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class Compliance:
    source_signals: list[RiskSignal] = field(default_factory=list)
    inspections_source_available: bool = False
    inspections: list[dict[str, Any]] = field(default_factory=list)
    licenses_source_available: bool = False
    licenses: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class Procurement:
    source_available: bool
    yearly_activity: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class DataQuality:
    conflicts: list[Conflict] = field(default_factory=list)
    warnings: list[Conflict] = field(default_factory=list)


@dataclass(slots=True)
class NormalizedCompanyProfile:
    company_identity: CompanyIdentity
    bank_risk: BankRisk
    ownership: Ownership
    related_companies: RelatedCompanies
    business_profile: BusinessProfile
    financial_health: FinancialHealth
    legal_risks: LegalRisks
    enforcement: Enforcement
    compliance: Compliance
    procurement: Procurement
    derived_metrics: dict[str, DerivedMetric] = field(default_factory=dict)
    data_quality: DataQuality = field(default_factory=DataQuality)

    def to_dict(self) -> dict[str, Any]:
        """Возвращает JSON-совместимое представление профиля."""
        return asdict(self)

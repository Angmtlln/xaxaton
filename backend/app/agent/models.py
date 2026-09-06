"""Типизированные контракты Master Agent, tools и rich UI."""
from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Dict, List, Literal, Optional, Union

from pydantic import (AfterValidator, BaseModel, ConfigDict, Field, HttpUrl, JsonValue,
                      field_validator, model_validator)


UNSAFE_MARKUP_RE = re.compile(
    r"<\s*/?\s*[a-zA-Z][^>]*>",
    re.IGNORECASE,
)


def contains_unsafe_markup(value: str) -> bool:
    """Отсекает исполняемую/векторную разметку до передачи в renderer."""
    return bool(UNSAFE_MARKUP_RE.search(value or ""))


def _validate_safe_text(value: str) -> str:
    if contains_unsafe_markup(value):
        raise ValueError("HTML, JavaScript и SVG в AssistantResponse запрещены")
    return value


SafeText = Annotated[str, AfterValidator(_validate_safe_text)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def is_valid_inn(value: str) -> bool:
    """Проверяет формат и контрольные цифры российского ИНН."""
    if (
        not value.isascii()
        or not value.isdigit()
        or len(value) not in (10, 12)
        or len(set(value)) == 1
    ):
        return False
    digits = [int(char) for char in value]
    if len(digits) == 10:
        weights = (2, 4, 10, 3, 5, 9, 4, 6, 8)
        return sum(a * b for a, b in zip(digits[:9], weights)) % 11 % 10 == digits[9]

    first_weights = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
    second_weights = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
    first = sum(a * b for a, b in zip(digits[:10], first_weights)) % 11 % 10
    second = sum(a * b for a, b in zip(digits[:11], second_weights)) % 11 % 10
    return first == digits[10] and second == digits[11]


class FullCompanyCheckArgs(StrictModel):
    inn: str = Field(pattern=r"^(?:\d{10}|\d{12})$")

    @field_validator("inn")
    @classmethod
    def validate_checksum(cls, value: str) -> str:
        if not is_valid_inn(value):
            raise ValueError("Некорректные контрольные цифры ИНН")
        return value


class CompanyRef(StrictModel):
    inn: str
    name: Optional[SafeText] = None

    @field_validator("inn")
    @classmethod
    def validate_inn(cls, value: str) -> str:
        if not is_valid_inn(value):
            raise ValueError("Некорректный ИНН активной компании")
        return value


class Evidence(StrictModel):
    id: SafeText = Field(min_length=1, max_length=160)
    fact_id: SafeText = Field(min_length=1, max_length=160)
    source: Literal["raw_fact", "derived_metric", "source_signal"]
    title: SafeText = Field(min_length=1, max_length=240)
    field_ref: SafeText = Field(min_length=1, max_length=500)
    display_value: SafeText = Field(max_length=1200)
    unit: Optional[SafeText] = Field(default=None, max_length=40)


class CompareCompaniesArgs(StrictModel):
    """Сравнение нескольких контрагентов одним вызовом, без N полных отчётов."""

    inns: List[str] = Field(min_length=2, max_length=3)
    focus: Literal["finance", "legal", "both"] = "both"

    @field_validator("inns")
    @classmethod
    def validate_inns(cls, values: List[str]) -> List[str]:
        checked: List[str] = []
        for value in values:
            if not re.fullmatch(r"\d{10}|\d{12}", value) or not is_valid_inn(value):
                raise ValueError("Некорректные контрольные цифры ИНН")
            if value in checked:
                raise ValueError("Компании в сравнении не должны повторяться")
            checked.append(value)
        return checked


class ToolError(StrictModel):
    code: Literal[
        "unknown_tool",
        "invalid_arguments",
        "not_found",
        "timeout",
        "result_too_large",
        "internal_error",
    ]
    user_safe_message: SafeText
    retryable: bool = False


class ToolFreshness(StrictModel):
    report_date: Optional[SafeText] = None


class ToolResultMetadata(StrictModel):
    tool: SafeText
    run_id: Optional[SafeText] = None
    latency_ms: int = Field(ge=0)
    calculator_version: Optional[SafeText] = None


class ToolResult(StrictModel):
    status: Literal["success", "partial", "error"]
    data: Dict[str, JsonValue] = Field(default_factory=dict)
    evidence: List[Evidence] = Field(default_factory=list, max_length=60)
    warnings: List[SafeText] = Field(default_factory=list, max_length=20)
    freshness: Optional[ToolFreshness] = None
    error: Optional[ToolError] = None
    metadata: ToolResultMetadata

    @model_validator(mode="after")
    def validate_error_shape(self) -> "ToolResult":
        if self.status == "error" and self.error is None:
            raise ValueError("Для error ToolResult требуется error")
        if self.status != "error" and self.error is not None:
            raise ValueError("Успешный ToolResult не должен содержать error")
        return self


class ToolFact(StrictModel):
    id: SafeText
    label: SafeText
    value: JsonValue = None
    field_ref: SafeText
    unit: Optional[SafeText] = None
    source: SafeText
    comment: Optional[SafeText] = None


class PolicySignal(StrictModel):
    """A backend-owned policy/status signal, never a prose conclusion catalog."""

    id: SafeText
    kind: Literal[
        "official_hard_stop",
        "source_attention",
        "bank_risk_status",
        "zsk_status",
    ]
    label: SafeText
    value: JsonValue = None
    evidence_ids: List[SafeText] = Field(default_factory=list, max_length=8)


class DataSection(StrictModel):
    """Bounded source section; paths refer to the enclosing company's snapshot."""
    field_ref: SafeText
    inputs: Dict[str, JsonValue] = Field(default_factory=dict)
    state: Literal["data", "missing", "null", "invalid", "empty", "not_calculable"] = "data"
    value: JsonValue = None
    total: Optional[int] = Field(default=None, ge=0)
    offset: int = Field(default=0, ge=0)
    included: Optional[int] = Field(default=None, ge=0)
    truncated: bool = False
    scope: SafeText = "source snapshot"
    next_offset: Optional[int] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_section(self):
        if self.included is not None and isinstance(self.value, list) and self.included != len(self.value):
            raise ValueError("Section included count must match records")
        if isinstance(self.value, list):
            for item in self.value:
                if isinstance(item, dict) and "input_refs" in item:
                    if set(item["input_refs"]) - self.inputs.keys():
                        raise ValueError("Unknown calculation input")
        return self


class DomainDataArgs(FullCompanyCheckArgs):
    section: Literal["default", "profile", "finance", "legal", "proceedings", "inspections", "licenses", "procurements", "signals", "activity", "connections"] = "default"
    year: Optional[int] = Field(default=None, ge=1900, le=2100)
    offset: int = Field(default=0, ge=0, le=100000)


class FinancialDataArgs(DomainDataArgs):
    section: Literal["default", "finance", "profile", "inspections", "licenses", "procurements", "signals", "activity", "connections"] = "default"


class LegalDataArgs(DomainDataArgs):
    section: Literal["default", "legal", "profile", "proceedings", "inspections", "licenses", "procurements", "signals", "activity", "connections"] = "default"


class FullCheckCompany(StrictModel):
    inn: SafeText
    ogrn: Optional[SafeText] = None
    short_name: Optional[SafeText] = None
    full_name: Optional[SafeText] = None
    address: Optional[SafeText] = None
    status: Optional[SafeText] = None
    registration_date: Optional[SafeText] = None
    status_reason: Optional[SafeText] = None
    status_date: Optional[SafeText] = None
    snapshot_id: Optional[SafeText] = None
    years_from_registration: Optional[int] = None
    risk_level: Optional[SafeText] = None
    zsk_risk_level: Optional[SafeText] = None
    report_date: Optional[SafeText] = None


class FullCheckCoverage(StrictModel):
    filled_blocks: int = Field(ge=0)
    total_blocks: int = Field(ge=0)
    coverage_pct: float = Field(ge=0, le=100)
    empty_blocks: List[SafeText] = Field(default_factory=list)


class NewsSelection(StrictModel):
    url: HttpUrl
    company_match: SafeText = Field(min_length=1, max_length=400)
    summary: SafeText = Field(min_length=1, max_length=500)


class ExternalNews(StrictModel):
    title: SafeText = Field(min_length=1, max_length=400)
    date: date
    source: SafeText = Field(min_length=1, max_length=253)
    url: HttpUrl
    summary: SafeText = Field(min_length=1, max_length=500)


class ConnectionNode(StrictModel):
    inn: SafeText
    name: SafeText
    snapshot_id: Optional[SafeText] = None
    report_date: Optional[SafeText] = None
    review_state: Literal["root", "reviewed", "partial", "unavailable"] = "unavailable"
    observations: List[ToolFact] = Field(default_factory=list, max_length=12)
    gaps: List[SafeText] = Field(default_factory=list, max_length=8)


class ConnectionEdge(StrictModel):
    source: SafeText
    target: SafeText
    kind: Literal["shared_founder", "shared_director", "founder_director", "ownership",
                  "related_company", "shared_related", "address", "email", "website", "phone"]
    label: SafeText
    via: Optional[SafeText] = None
    field_refs: List[SafeText] = Field(min_length=1, max_length=4)


class CompanyConnections(StrictModel):
    state: Literal["complete", "partial", "unavailable"] = "complete"
    root_inn: SafeText
    nodes: List[ConnectionNode] = Field(default_factory=list, max_length=7)
    edges: List[ConnectionEdge] = Field(default_factory=list, max_length=30)
    total_companies: int = Field(default=0, ge=0)
    total_edges: int = Field(default=0, ge=0)
    external_references: int = Field(default=0, ge=0)
    note: SafeText = "Проверены связи внутри доступного набора карточек, один шаг от компании."

    @model_validator(mode="after")
    def validate_graph(self):
        known = {node.inn for node in self.nodes}
        if len(known) != len(self.nodes) or any(not is_valid_inn(inn) for inn in known):
            raise ValueError("Invalid connection identifiers")
        if any(e.source == e.target or {e.source, e.target} - known for e in self.edges):
            raise ValueError("Invalid connection edge")
        return self


class FullCompanyCheckData(StrictModel):
    check_run_id: Optional[SafeText] = None
    pipeline_status: Literal["SUCCEEDED", "PARTIAL"]
    availability: Literal["DATA", "PARTIAL", "NO_DATA"]
    inn: SafeText
    company: FullCheckCompany
    coverage: FullCheckCoverage
    facts: Dict[str, ToolFact]
    metric_ids: List[SafeText] = Field(default_factory=list, max_length=20)
    series_ids: List[SafeText] = Field(default_factory=list, max_length=8)
    event_ids: List[SafeText] = Field(default_factory=list, max_length=8)
    status_ids: List[SafeText] = Field(default_factory=list, max_length=8)
    policy_signals: List[PolicySignal] = Field(default_factory=list, max_length=12)
    calculator_version: SafeText
    connections: Optional[CompanyConnections] = None
    sections: Dict[str, DataSection] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_verified_links(self) -> "FullCompanyCheckData":
        if self.inn != self.company.inn:
            raise ValueError("Full-check company identifier mismatch")
        if any(key != fact.id for key, fact in self.facts.items()):
            raise ValueError("Fact keys must match fact IDs")
        references = set(self.metric_ids + self.series_ids + self.event_ids + self.status_ids)
        references.update(ref for signal in self.policy_signals for ref in signal.evidence_ids)
        if references - self.facts.keys():
            raise ValueError("Unknown full-check fact reference")
        return self


class SuggestedAction(StrictModel):
    """A bounded next question, never executable code or an external action."""

    label: SafeText = Field(min_length=1, max_length=80)
    prompt: SafeText = Field(min_length=1, max_length=300)
    mode: Literal["submit", "compose"] = "submit"


class MasterAnswer(StrictModel):
    """Natural-language answer authored by Master; UI remains backend-owned."""

    message: SafeText = Field(min_length=1, max_length=5000)
    artifact: Literal["none", "metrics", "chart"] = "none"
    suggested_actions: List[SuggestedAction] = Field(default_factory=list, max_length=4)
    news_selection: Optional[List[NewsSelection]] = Field(default=None, max_length=4)


class GroundingVerification(StrictModel):
    """Narrow verdict about unsupported company-specific factual claims."""

    supported: bool
    unsupported_claims: List[SafeText] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_verdict(self) -> "GroundingVerification":
        if self.supported and self.unsupported_claims:
            raise ValueError("Supported answer cannot contain unsupported claims")
        if not self.supported and not self.unsupported_claims:
            raise ValueError("Unsupported answer must identify at least one claim")
        return self


class CompanyCardBlock(StrictModel):
    type: Literal["company_card"] = "company_card"
    name: SafeText
    inn: SafeText
    ogrn: Optional[SafeText] = None
    status: Optional[SafeText] = None
    address: Optional[SafeText] = None
    years_from_registration: Optional[int] = None
    bank_risk_level: Optional[SafeText] = None
    zsk_risk_level: Optional[SafeText] = None
    report_date: Optional[SafeText] = None
    report_url: SafeText = Field(pattern=r"^/report\?inn=(?:\d{10}|\d{12})$")
    evidence_ids: List[SafeText] = Field(default_factory=list)


class TextBlock(StrictModel):
    type: Literal["text"] = "text"
    title: Optional[SafeText] = None
    text: SafeText
    evidence_ids: List[SafeText] = Field(default_factory=list)


class CompanySummaryBlock(StrictModel):
    """Compact deterministic identity, reserved for a completed full check."""
    type: Literal["company_summary"] = "company_summary"
    name: SafeText
    inn: SafeText
    status: Optional[SafeText] = None
    years_from_registration: Optional[int] = None
    bank_risk_level: Optional[SafeText] = None
    zsk_risk_level: Optional[SafeText] = None
    report_url: SafeText = Field(pattern=r"^/report\?inn=(?:\d{10}|\d{12})$")
    evidence_ids: List[SafeText] = Field(default_factory=list)
    metrics: List["MetricItem"] = Field(default_factory=list, max_length=4)


class MetricItem(StrictModel):
    id: SafeText
    label: SafeText
    value: Union[int, float, SafeText, bool, None] = None
    display_value: SafeText
    unit: Optional[SafeText] = None
    state: Literal["data", "no_data"]
    evidence_id: Optional[SafeText] = None


class MetricGridBlock(StrictModel):
    type: Literal["metric_grid"] = "metric_grid"
    title: SafeText
    items: List[MetricItem] = Field(min_length=1, max_length=8)


class ChartPoint(StrictModel):
    x: SafeText
    value: Optional[float] = None


class ChartSeries(StrictModel):
    key: SafeText
    label: SafeText
    points: List[ChartPoint] = Field(max_length=20)
    evidence_id: SafeText


class LineChartBlock(StrictModel):
    type: Literal["line_chart"] = "line_chart"
    title: SafeText
    description: SafeText
    unit: Optional[SafeText] = None
    state: Literal["data", "no_data"]
    series: List[ChartSeries] = Field(default_factory=list, max_length=4)
    empty_message: Optional[SafeText] = None


class FindingItem(StrictModel):
    title: SafeText
    text: SafeText
    evidence_ids: List[SafeText] = Field(default_factory=list, max_length=8)


class FindingListBlock(StrictModel):
    type: Literal["finding_list"] = "finding_list"
    title: SafeText
    items: List[FindingItem] = Field(default_factory=list, max_length=10)
    empty_message: Optional[SafeText] = None


class ComparisonCell(StrictModel):
    display_value: SafeText
    state: Literal["data", "no_data"]
    evidence_id: Optional[SafeText] = None


class ComparisonRow(StrictModel):
    id: SafeText
    label: SafeText
    unit: Optional[SafeText] = None
    cells: List[ComparisonCell] = Field(min_length=2, max_length=3)


class ComparisonColumn(StrictModel):
    inn: SafeText
    name: SafeText
    availability: Literal["DATA", "PARTIAL", "NO_DATA"]


class ComparisonTableBlock(StrictModel):
    """Компактное сравнение: значения гидратирует backend, не модель."""

    type: Literal["comparison_table"] = "comparison_table"
    title: SafeText
    columns: List[ComparisonColumn] = Field(min_length=2, max_length=3)
    rows: List[ComparisonRow] = Field(default_factory=list, max_length=10)
    empty_message: Optional[SafeText] = None

    @model_validator(mode="after")
    def validate_row_width(self) -> "ComparisonTableBlock":
        if any(len(row.cells) != len(self.columns) for row in self.rows):
            raise ValueError("Строка сравнения должна покрывать все компании")
        return self


class ConnectionGraphBlock(StrictModel):
    type: Literal["connection_graph"] = "connection_graph"
    title: SafeText = "Связи внутри датасета"
    graph: CompanyConnections


class EvidenceListBlock(StrictModel):
    type: Literal["evidence_list"] = "evidence_list"
    title: SafeText
    evidence_ids: List[SafeText] = Field(default_factory=list, max_length=60)


UIBlock = Annotated[
    Union[
        CompanyCardBlock,
        TextBlock,
        MetricGridBlock,
        LineChartBlock,
        FindingListBlock,
        ComparisonTableBlock,
        ConnectionGraphBlock,
        EvidenceListBlock,
    ],
    Field(discriminator="type"),
]


class AssistantMetadata(StrictModel):
    agent_run_id: SafeText
    check_run_id: Optional[SafeText] = None
    status: Literal["completed", "partial", "needs_input", "error"]
    tool_calls: int = Field(ge=0, le=1)
    routing: Literal["model", "deterministic_fallback", "deterministic_guard"]
    model: Optional[SafeText] = None
    prompt_version: SafeText
    latency_ms: int = Field(ge=0)
    error_code: Optional[SafeText] = None
    model_calls: int = Field(default=0, ge=0, le=5)
    synthesis: Literal["deterministic", "model", "fallback"] = "deterministic"
    grounding_status: Literal[
        "not_requested",
        "not_required", "verified", "repaired", "skipped_rewrite", "fallback"
    ] = "not_required"
    repair_attempts: int = Field(default=0, ge=0, le=1)


class AssistantResponse(StrictModel):
    message: SafeText
    external_news: List[ExternalNews] = Field(default_factory=list, max_length=4)
    external_news_status: Optional[Literal[
        "completed", "not_configured", "unavailable", "selection_unavailable", "partial"
    ]] = None
    leading_artifact: Optional[CompanySummaryBlock] = None
    blocks: List[UIBlock] = Field(default_factory=list, max_length=10)
    evidence: List[Evidence] = Field(default_factory=list, max_length=60)
    suggested_actions: List[Union[SafeText, SuggestedAction]] = Field(default_factory=list, max_length=4)
    metadata: AssistantMetadata
    conversation_id: Optional[str] = None
    active_company: Optional[CompanyRef] = None

    @model_validator(mode="after")
    def validate_evidence_references(self) -> "AssistantResponse":
        evidence_ids = [item.id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Evidence.id должны быть уникальны")
        known = set(evidence_ids)
        referenced: List[str] = []
        if self.leading_artifact is not None:
            referenced.extend(self.leading_artifact.evidence_ids)
            referenced.extend(
                item.evidence_id for item in self.leading_artifact.metrics
                if item.evidence_id is not None
            )
        for block in self.blocks:
            if isinstance(block, (CompanyCardBlock, TextBlock, FindingListBlock)):
                if isinstance(block, FindingListBlock):
                    for item in block.items:
                        referenced.extend(item.evidence_ids)
                else:
                    referenced.extend(block.evidence_ids)
            elif isinstance(block, MetricGridBlock):
                referenced.extend(
                    item.evidence_id for item in block.items if item.evidence_id is not None
                )
            elif isinstance(block, LineChartBlock):
                referenced.extend(series.evidence_id for series in block.series)
            elif isinstance(block, ComparisonTableBlock):
                referenced.extend(
                    cell.evidence_id for row in block.rows for cell in row.cells
                    if cell.evidence_id is not None
                )
            elif isinstance(block, EvidenceListBlock):
                referenced.extend(block.evidence_ids)
        unknown = sorted(set(referenced) - known)
        if unknown:
            raise ValueError("Неизвестные evidence references: %s" % ", ".join(unknown))
        return self

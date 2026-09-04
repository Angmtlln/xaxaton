"""Типизированные контракты Master Agent, tools и rich UI."""
from __future__ import annotations

import re
from typing import Annotated, Dict, List, Literal, Optional, Union

from pydantic import (AfterValidator, BaseModel, ConfigDict, Field, JsonValue,
                      TypeAdapter, field_validator, model_validator)


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


class ToolCallAction(StrictModel):
    type: Literal["tool_call"]
    tool: str = Field(min_length=1, max_length=80)
    arguments: Dict[str, JsonValue]


class FinalAction(StrictModel):
    type: Literal["final"]
    reason: Literal["missing_inn", "invalid_inn", "ambiguous_inn", "unsupported_request"]


MasterAction = Annotated[Union[ToolCallAction, FinalAction], Field(discriminator="type")]
MASTER_ACTION_ADAPTER = TypeAdapter(MasterAction)


class Evidence(StrictModel):
    id: SafeText = Field(min_length=1, max_length=160)
    fact_id: SafeText = Field(min_length=1, max_length=160)
    source: Literal["raw_fact", "derived_metric", "source_signal"]
    title: SafeText = Field(min_length=1, max_length=240)
    field_ref: SafeText = Field(min_length=1, max_length=500)
    display_value: SafeText = Field(max_length=1200)
    unit: Optional[SafeText] = Field(default=None, max_length=40)


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


class FullCheckCompany(StrictModel):
    inn: SafeText
    ogrn: Optional[SafeText] = None
    short_name: Optional[SafeText] = None
    full_name: Optional[SafeText] = None
    address: Optional[SafeText] = None
    status: Optional[SafeText] = None
    registration_date: Optional[SafeText] = None
    years_from_registration: Optional[int] = None
    risk_level: Optional[SafeText] = None
    zsk_risk_level: Optional[SafeText] = None
    report_date: Optional[SafeText] = None


class FullCheckCoverage(StrictModel):
    filled_blocks: int = Field(ge=0)
    total_blocks: int = Field(ge=0)
    coverage_pct: float = Field(ge=0, le=100)
    empty_blocks: List[SafeText] = Field(default_factory=list)


class FullCheckSummary(StrictModel):
    verdict_group: Literal["STOP", "ENHANCED_CHECK", "CONDITIONALLY_OK", "NO_DATA"]
    headline: SafeText = ""
    narrative_points: List[SafeText] = Field(default_factory=list, max_length=3)
    data_gaps: List[SafeText] = Field(default_factory=list, max_length=8)
    questions_to_ask: List[SafeText] = Field(default_factory=list, max_length=8)


class FullCheckGrounding(StrictModel):
    statements: int = Field(ge=0)
    grounded: int = Field(ge=0)
    unverified: int = Field(ge=0)
    no_ref: int = Field(ge=0)
    grounded_pct: float = Field(ge=0, le=100)


class FullCompanyCheckData(StrictModel):
    check_run_id: Optional[SafeText] = None
    pipeline_status: Literal["SUCCEEDED", "PARTIAL"]
    inn: SafeText
    company: FullCheckCompany
    coverage: FullCheckCoverage
    summary: FullCheckSummary
    facts: Dict[str, ToolFact]
    grounding: FullCheckGrounding
    llm_mode: SafeText
    calculator_version: SafeText


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


class AssistantResponse(StrictModel):
    message: SafeText
    blocks: List[UIBlock] = Field(default_factory=list, max_length=10)
    evidence: List[Evidence] = Field(default_factory=list, max_length=60)
    suggested_actions: List[SafeText] = Field(default_factory=list, max_length=4)
    metadata: AssistantMetadata

    @model_validator(mode="after")
    def validate_evidence_references(self) -> "AssistantResponse":
        evidence_ids = [item.id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Evidence.id должны быть уникальны")
        known = set(evidence_ids)
        referenced: List[str] = []
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
            elif isinstance(block, EvidenceListBlock):
                referenced.extend(block.evidence_ids)
        unknown = sorted(set(referenced) - known)
        if unknown:
            raise ValueError("Неизвестные evidence references: %s" % ", ".join(unknown))
        return self

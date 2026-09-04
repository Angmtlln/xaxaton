"""Модели запросов и ответов API. Они же формируют схему Swagger."""
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChatMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Полная проверка, финансовый или юридический вопрос; продолжение использует активную компанию диалога",
        json_schema_extra={"example": "Проверь контрагента 6165169320"},
    )
    conversation_id: Optional[UUID] = Field(
        default=None,
        description="ID из предыдущего ответа. Без ID создаётся новый диалог; состояние временно хранится в памяти процесса.",
    )


class CheckRequest(BaseModel):
    inn: str = Field(..., min_length=10, max_length=12, pattern=r"^\d{10,12}$",
                     description="ИНН контрагента, 10 цифр для юрлица",
                     json_schema_extra={"example": "1684017097"})
    persist: bool = Field(True, description="Сохранять прогон в БД (audit.analysis_runs)")


class FactOut(BaseModel):
    id: str = Field(..., description="Идентификатор факта, на него ссылается агент")
    label: str
    value: Any = None
    field_ref: str = Field(..., description="Путь к полю исходной карточки отчёта")
    unit: Optional[str] = None
    source: str = Field("computed", description="computed | raw | derived_flag")
    comment: Optional[str] = None


class FindingOut(BaseModel):
    text: str
    severity: str = Field("medium", description="high | medium | low")
    fact_id: Optional[str] = None
    grounded: bool = Field(False, description="Ссылка на факт проверена по реестру фактов")
    added_by: Optional[str] = Field(None, description="guardrail, если наблюдение добавлено защитным слоем")


class BlockOut(BaseModel):
    block: str = Field(..., description="identity | reliability | finance | experience")
    title: str
    signal: str = Field(..., description="NORM | ATTENTION | RISK | NO_DATA")
    headline: str = ""
    facts_sentence: str = ""
    interpretation: str = ""
    findings: List[FindingOut] = []
    data_gaps: List[str] = []
    cannot_assess: List[str] = []
    facts: List[FactOut] = []
    model: Optional[str] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None


class KeyNumberOut(BaseModel):
    label: str
    value: Optional[str] = None
    fact_id: Optional[str] = None
    grounded: bool = False


class RiskOut(BaseModel):
    text: str
    severity: Optional[str] = None
    fact_id: Optional[str] = None
    grounded: bool = False
    added_by: Optional[str] = None


class SummaryOut(BaseModel):
    verdict_group: str = Field(..., description="STOP | ENHANCED_CHECK | CONDITIONALLY_OK | NO_DATA")
    headline: str = ""
    narrative: str = Field("", description="Совместимое строковое представление итоговых тезисов")
    narrative_points: List[str] = Field(
        default_factory=list, max_length=3,
        description="2–3 коротких тезиса Summary-агента; каждый до 135 символов")
    key_numbers: List[Dict[str, Any]] = []
    top_risks: List[Dict[str, Any]] = []
    positives: List[Dict[str, Any]] = []
    data_gaps: List[str] = []
    questions_to_ask: List[str] = []
    model: Optional[str] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None


class CoverageBlockOut(BaseModel):
    key: str
    title: str
    field_ref: str
    filled: bool
    items: int


class CoverageOut(BaseModel):
    blocks: List[CoverageBlockOut]
    filled_blocks: int
    total_blocks: int
    coverage_pct: float
    empty_blocks: List[str]


class CompanyOut(BaseModel):
    inn: str
    ogrn: Optional[str] = None
    short_name: Optional[str] = None
    full_name: Optional[str] = None
    address: Optional[str] = None
    status: Optional[str] = None
    registration_date: Optional[str] = None
    years_from_registration: Optional[int] = None
    risk_level: Optional[str] = Field(None, description="Оценка банка, приводится без изменений")
    zsk_risk_level: Optional[str] = Field(None, description="Светофор ЗСК, приводится без изменений")
    report_date: Optional[str] = None


class GroundingOut(BaseModel):
    statements: int
    grounded: int
    unverified: int
    no_ref: int
    grounded_pct: float


class LLMInfoOut(BaseModel):
    mode: str = Field(..., description="groq | mock")
    block_model: str
    block_models: Dict[str, str] = Field(
        default_factory=dict,
        description="Модель каждого блочного агента. Блоки разведены по разным "
                    "моделям, потому что лимит токенов Groq считается по модели")
    summary_model: str
    calculator_version: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


class CheckResponse(BaseModel):
    run_id: Optional[str] = None
    status: str = Field(..., description="SUCCEEDED | PARTIAL")
    inn: str
    company: CompanyOut
    coverage: CoverageOut
    summary: SummaryOut
    blocks: List[BlockOut]
    key_facts: List[Dict[str, Any]]
    grounding: GroundingOut
    guardrail_notes: List[str] = []
    llm: LLMInfoOut


class FactsResponse(BaseModel):
    inn: str
    company: CompanyOut
    coverage: CoverageOut
    blocks: List[Dict[str, Any]]
    calculator_version: str


class CompanyListItem(BaseModel):
    inn: str
    short_name: Optional[str] = None
    report_date: Optional[str] = None
    risk_level: Optional[str] = None
    zsk_risk_level: Optional[str] = None
    filled_blocks: int = 0
    negative_count: int = 0


class RunListItem(BaseModel):
    run_id: str
    inn: str
    status: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    latency_ms: Optional[int] = None
    llm_mode: Optional[str] = None
    verdict_group: Optional[str] = None
    headline: Optional[str] = None


class HealthOut(BaseModel):
    status: str
    database: bool
    llm_mode: str
    block_model: str
    block_models: Dict[str, str] = Field(default_factory=dict)
    summary_model: str
    version: str


class ErrorOut(BaseModel):
    detail: str

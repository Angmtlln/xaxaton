"""Framework-agnostic contracts for normalized targeted domain data."""
from typing import Dict, List, Literal

from pydantic import Field, model_validator

from .models import DataSection, FullCheckCompany, PolicySignal, SafeText, StrictModel, ToolFact


class TargetedData(StrictModel):
    domain: Literal["finance", "legal"]
    company: FullCheckCompany
    availability: Literal["DATA", "PARTIAL", "NO_DATA"]
    facts: Dict[str, ToolFact]
    sections: Dict[str, DataSection] = Field(default_factory=dict)
    metric_ids: List[SafeText] = Field(default_factory=list, max_length=8)
    series_ids: List[SafeText] = Field(default_factory=list, max_length=4)
    event_ids: List[SafeText] = Field(default_factory=list, max_length=8)
    status_ids: List[SafeText] = Field(default_factory=list, max_length=8)
    policy_signals: List[PolicySignal] = Field(default_factory=list, max_length=8)
    gaps: List[SafeText] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_fact_links(self):
        if any(key != fact.id for key, fact in self.facts.items()):
            raise ValueError("Fact keys must match fact IDs")
        references = set(self.metric_ids + self.series_ids + self.event_ids + self.status_ids)
        references.update(ref for signal in self.policy_signals for ref in signal.evidence_ids)
        if references - self.facts.keys():
            raise ValueError("Unknown targeted fact reference")
        return self


class ComparisonCompanyData(StrictModel):
    """Наблюдения по одной компании внутри сравнения; id фактов префиксованы ИНН."""

    inn: SafeText
    sections: Dict[str, DataSection] = Field(default_factory=dict)
    comparison_periods: Dict[str, int | None] = Field(default_factory=dict)
    company: FullCheckCompany
    availability: Literal["DATA", "PARTIAL", "NO_DATA"]
    metric_ids: List[SafeText] = Field(default_factory=list, max_length=16)
    status_ids: List[SafeText] = Field(default_factory=list, max_length=8)
    policy_signal_ids: List[SafeText] = Field(default_factory=list, max_length=8)
    gaps: List[SafeText] = Field(default_factory=list, max_length=10)


class ComparisonData(StrictModel):
    """Сравнение 2–5 контрагентов одним ToolResult вместо N полных отчётов."""

    domain: Literal["comparison"] = "comparison"
    focus: List[Literal["finance", "legal"]] = Field(min_length=1, max_length=2)
    companies: List[ComparisonCompanyData] = Field(min_length=2, max_length=5)
    facts: Dict[str, ToolFact]
    sections: Dict[str, DataSection] = Field(default_factory=dict)
    policy_signals: List[PolicySignal] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def validate_fact_links(self):
        if any(key != fact.id for key, fact in self.facts.items()):
            raise ValueError("Fact keys must match fact IDs")
        inns = [item.inn for item in self.companies]
        if len(set(inns)) != len(inns):
            raise ValueError("Comparison companies must be distinct")
        references = set()
        for item in self.companies:
            references.update(item.metric_ids + item.status_ids + item.policy_signal_ids)
        references.update(ref for signal in self.policy_signals for ref in signal.evidence_ids)
        if references - self.facts.keys():
            raise ValueError("Unknown comparison fact reference")
        return self

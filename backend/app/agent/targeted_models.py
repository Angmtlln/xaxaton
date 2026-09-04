"""Framework-agnostic contracts for normalized targeted domain data."""
from typing import Dict, List, Literal

from pydantic import Field, model_validator

from .models import FullCheckCompany, PolicySignal, SafeText, StrictModel, ToolFact


class TargetedData(StrictModel):
    domain: Literal["finance", "legal"]
    company: FullCheckCompany
    availability: Literal["DATA", "PARTIAL", "NO_DATA"]
    facts: Dict[str, ToolFact]
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

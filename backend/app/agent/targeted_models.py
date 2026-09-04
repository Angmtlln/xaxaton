"""Framework-agnostic data contracts for targeted domain observations."""
from typing import Dict, List, Literal

from pydantic import Field, model_validator

from .models import FullCheckCompany, SafeText, StrictModel, ToolFact


class TargetedFinding(StrictModel):
    id: SafeText
    title: SafeText
    text: SafeText
    evidence_ids: List[SafeText] = Field(default_factory=list, max_length=8)
    required: bool = False


class TargetedData(StrictModel):
    domain: Literal["finance", "legal"]
    company: FullCheckCompany
    availability: Literal["DATA", "PARTIAL", "NO_DATA"]
    facts: Dict[str, ToolFact]
    metric_ids: List[SafeText] = Field(default_factory=list, max_length=8)
    findings: List[TargetedFinding] = Field(default_factory=list, max_length=10)
    gaps: List[SafeText] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_fact_links(self):
        if any(key != fact.id for key, fact in self.facts.items()):
            raise ValueError("Fact keys must match fact IDs")
        if len({item.id for item in self.findings}) != len(self.findings):
            raise ValueError("Finding IDs must be unique")
        references = set(self.metric_ids)
        references.update(ref for item in self.findings for ref in item.evidence_ids)
        if references - self.facts.keys():
            raise ValueError("Unknown targeted fact reference")
        return self


class MasterSynthesis(StrictModel):
    """Model selects grounded observations; it cannot author fact values or prose."""
    finding_ids: List[SafeText] = Field(max_length=10)

"""Contracts for bounded, task-specific selection; reasoning is not factual data."""
from typing import Literal

from pydantic import Field, model_validator

from .models import FindCompaniesArgs, SafeText, StrictModel, ToolFact, ToolResult
from .targeted_models import ComparisonCompanyData


class SelectCounterpartiesArgs(StrictModel):
    filters: FindCompaniesArgs
    goal: SafeText = Field(min_length=2, max_length=1000)
    preferences: SafeText = Field(default="", max_length=1500)
    finalists: int = Field(default=5, ge=1, le=5)

    @model_validator(mode="after")
    def no_numeric_ranking(self):
        if self.filters.ranking:
            raise ValueError("Selection filters must not exclude companies by ranking completeness")
        return self


def filter_values(filters: FindCompaniesArgs) -> dict:
    return filters.model_dump(exclude_none=True, exclude={"limit", "ranking", "sort_by", "order"})


class CandidateProfile(StrictModel):
    inn: SafeText
    snapshot_id: int | None = None
    company: ComparisonCompanyData
    facts: dict[str, ToolFact]

    @model_validator(mode="after")
    def own_facts(self):
        if self.company.inn != self.inn or self.company.company.inn != self.inn:
            raise ValueError("Candidate identity mismatch")
        if any(key != fact.id or not key.startswith(self.inn + ":") for key, fact in self.facts.items()):
            raise ValueError("Foreign candidate fact")
        return self


class CandidateReview(StrictModel):
    inn: SafeText
    summary: SafeText = Field(max_length=450)
    strengths: SafeText = Field(max_length=350)
    limitations: SafeText = Field(max_length=450)
    missing_data: SafeText = Field(max_length=350)
    evidence_ids: list[SafeText] = Field(default_factory=list, max_length=6)


class ReviewBatch(StrictModel):
    reviews: list[CandidateReview] = Field(max_length=10)


class CandidateDecision(StrictModel):
    inn: SafeText
    reason: SafeText = Field(min_length=1, max_length=450)
    evidence_ids: list[SafeText] = Field(default_factory=list, max_length=6)


class SelectionDecision(StrictModel):
    finalists: list[SafeText] = Field(max_length=5)
    decisions: list[CandidateDecision] = Field(max_length=50)


class SelectionData(StrictModel):
    domain: Literal["selection"] = "selection"
    arguments: SelectCounterpartiesArgs
    total: int = Field(ge=0)
    state: Literal["complete", "partial", "too_many", "empty"]
    profiles: list[CandidateProfile] = Field(default_factory=list, max_length=50)
    reviews: list[CandidateReview] = Field(default_factory=list, max_length=50)
    decisions: list[CandidateDecision] = Field(default_factory=list, max_length=50)
    finalists: list[SafeText] = Field(default_factory=list, max_length=5)
    comparison: ToolResult | None = None
    notes: list[SafeText] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_selection(self):
        profiles = {p.inn: p for p in self.profiles}
        if len(profiles) != len(self.profiles):
            raise ValueError("Duplicate profiles")
        for items in (self.reviews, self.decisions):
            if len({r.inn for r in items}) != len(items):
                raise ValueError("Duplicate model records")
            for item in items:
                if item.inn not in profiles or set(item.evidence_ids) - profiles[item.inn].facts.keys():
                    raise ValueError("Foreign candidate or evidence")
        if len(set(self.finalists)) != len(self.finalists) or set(self.finalists) - profiles.keys():
            raise ValueError("Foreign or duplicate finalists")
        if len(self.finalists) > self.arguments.finalists:
            raise ValueError("Too many finalists")
        if self.state == "complete" and (len(self.reviews) != self.total or len(self.decisions) != self.total):
            raise ValueError("Incomplete selection cannot be complete")
        return self


class SelectionRoute(StrictModel):
    action: Literal["select", "explain", "clarify"]
    filters: FindCompaniesArgs | None = None
    use_previous_filters: bool = False
    goal: SafeText = Field(default="", max_length=1000)
    preferences: SafeText = Field(default="", max_length=1500)
    finalists: int = Field(default=5, ge=1, le=5)
    question: SafeText = Field(default="", max_length=600)
    explain_inns: list[SafeText] = Field(default_factory=list, max_length=5)


class SelectionAnswer(StrictModel):
    message: SafeText = Field(min_length=1, max_length=3500)
    order: list[SafeText] = Field(default_factory=list, max_length=5)

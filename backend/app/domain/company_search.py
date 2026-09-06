"""Bounded company identity search, shared by HTTP, MCP and the agent."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SearchModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CompanySearchArgs(SearchModel):
    query: str = Field(min_length=2, max_length=256)
    limit: int = Field(default=5, ge=1, le=5)


class CompanyMatch(SearchModel):
    inn: str = Field(pattern=r"^[0-9]{10}(?:[0-9]{2})?$")
    name: str
    full_name: str | None = None
    address: str | None = None
    snapshot_id: int = Field(gt=0)
    match: Literal["exact", "prefix", "partial"]


class CompanySearchResult(SearchModel):
    rows: list[CompanyMatch] = Field(default_factory=list, max_length=5)
    total: int = Field(ge=0)
    exact_total: int = Field(ge=0)

    @model_validator(mode="after")
    def counts_and_identity(self):
        if self.exact_total > self.total or len(self.rows) > self.total:
            raise ValueError("Inconsistent search counts")
        if len({row.inn for row in self.rows}) != len(self.rows):
            raise ValueError("Duplicate company")
        exact = sum(row.match == "exact" for row in self.rows)
        if exact != min(self.exact_total, len(self.rows)):
            raise ValueError("Exact matches must precede other matches")
        if self.total and not self.rows:
            raise ValueError("Missing search rows")
        return self


class CompanySelectionInput(SearchModel):
    search_id: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    inn: str = Field(pattern=r"^[0-9]{10}(?:[0-9]{2})?$")

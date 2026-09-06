"""Versioned wire types. Only relational scalars are converted; raw JSON is opaque."""
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from app.agent.models import FindCompaniesArgs

VERSION = 'company-data-v1'
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_HTTP_BYTES = 16 * 1024 * 1024
Inn = Annotated[str, Field(pattern=r'^[0-9]{10}(?:[0-9]{2})?$')]
PositiveId = Annotated[int, Field(strict=True, gt=0)]


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class SnapshotArgs(Strict):
    inn: Inn


class SnapshotIdsArgs(Strict):
    snapshot_ids: list[PositiveId] = Field(max_length=50)


class NeighboursArgs(Strict):
    inns: list[Inn] = Field(max_length=6)


class CandidatesArgs(Strict):
    limit: int = Field(default=10001, ge=1, le=10001)


class CatalogArgs(Strict):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    risk_level: Literal['LOW', 'MEDIUM', 'HIGH', 'UNKNOWN'] | None = None
    zsk_risk_level: Literal['GREEN', 'YELLOW', 'RED', 'UNKNOWN'] | None = None
    min_filled_blocks: int | None = Field(default=None, ge=0, le=9)
    query: str | None = Field(default=None, max_length=4000)


class SearchArgs(FindCompaniesArgs):
    # The public agent tool still has a limit of 25; selection reads up to 50.
    limit: int = Field(default=10, ge=1, le=50)


class EmptyArgs(Strict):
    pass


class Snapshot(Strict):
    snapshot_id: PositiveId
    company_id: PositiveId
    inn: Inn
    report_date: datetime
    document: dict[str, JsonValue] | None
    address: str | None
    email: str | None
    website: str | None
    company_size: str | None
    registration_date: date | None
    years_from_registration: int | None
    status: str | None
    status_reason: str | None
    status_date: date | None
    risk_level: str | None
    zsk_risk_level: str | None
    ogrn: str | None
    kpp: str | None
    okpo: str | None
    short_name: str | None
    full_name: str | None


class CatalogRow(Strict):
    inn: Inn
    short_name: str | None
    snapshot_id: PositiveId
    report_date: datetime
    risk_level: str | None
    zsk_risk_level: str | None
    filled_blocks: int
    negative_count: int


class Activity(Strict):
    code: str | None
    description: str | None
    is_main: bool | None
    field_ref: str | None


class SearchRow(Strict):
    inn: Inn
    short_name: str | None
    snapshot_id: PositiveId
    report_date: datetime
    fin_year: int | None
    proceeds: Decimal | None
    profit: Decimal | None
    claims_amount: Decimal | None
    hard_stops: int | None
    enforcement_count: int | None
    risk_level: str | None
    zsk_risk_level: str | None
    matched_activities: list[Activity]


class Candidate(Strict):
    inn: Inn
    snapshot_id: PositiveId
    report_date: datetime
    document: dict[str, JsonValue]


class Reply(Strict):
    version: Literal['company-data-v1'] = VERSION


class SnapshotReply(Reply):
    snapshot: Snapshot | None


class SnapshotsReply(Reply):
    rows: list[Snapshot] = Field(max_length=50)


class CatalogReply(Reply):
    rows: list[CatalogRow] = Field(max_length=200)


class SearchReply(Reply):
    rows: list[SearchRow] = Field(max_length=50)
    total: int = Field(ge=0)
    eligible_total: int | None = Field(default=None, ge=0)


class CandidatesReply(Reply):
    rows: list[Candidate] = Field(max_length=10001)


class StatusReply(Reply):
    database: bool


INPUTS = {
    'get_latest_snapshot': SnapshotArgs,
    'get_selection_snapshots': SnapshotIdsArgs,
    'list_companies': CatalogArgs,
    'find_companies': SearchArgs,
    'get_connection_candidates': CandidatesArgs,
    'get_snapshots_for_connections': NeighboursArgs,
    'data_source_status': EmptyArgs,
}
OUTPUTS = {
    'get_latest_snapshot': SnapshotReply,
    'get_selection_snapshots': SnapshotsReply,
    'list_companies': CatalogReply,
    'find_companies': SearchReply,
    'get_connection_candidates': CandidatesReply,
    'get_snapshots_for_connections': SnapshotsReply,
    'data_source_status': StatusReply,
}


def wrap_result(operation, value):
    if operation == 'get_latest_snapshot':
        return {'snapshot': value}
    if operation in {'find_companies', 'data_source_status'}:
        return value
    return {'rows': value}


def unwrap_result(operation, reply):
    value = reply.model_dump(mode='python', exclude_unset=True)
    value.pop('version', None)
    if operation == 'get_latest_snapshot':
        return value['snapshot']
    if operation in {'find_companies', 'data_source_status'}:
        return value
    return value['rows']

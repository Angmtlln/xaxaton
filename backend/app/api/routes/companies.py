"""Витрина карточек и детерминированные факты без обращения к модели."""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.api.deps import settings_dep
from app.api.schemas import CompanyListItem, CoverageOut, ErrorOut, FactsResponse
from app.api.serialization import company_out, isoformat_rows
from app.config import Settings
from app.domain import facts as facts_mod
from app.infrastructure import repository

router = APIRouter(prefix="/api/v1/companies", tags=["companies"])


@router.get("", response_model=List[CompanyListItem],
            summary="Доступные карточки контрагентов")
async def list_companies(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        risk_level: Optional[str] = Query(None, description="LOW | MEDIUM | HIGH | UNKNOWN"),
        zsk_risk_level: Optional[str] = Query(None, description="GREEN | YELLOW | RED"),
        min_filled_blocks: Optional[int] = Query(
            None, ge=0, le=9, description="Минимальная полнота данных, блоков из 9"),
        q: Optional[str] = Query(None, description="Часть ИНН или названия")) -> List[Dict[str, Any]]:
    rows = await repository.list_companies(limit=limit, offset=offset, risk_level=risk_level,
                                           zsk_risk_level=zsk_risk_level,
                                           min_filled_blocks=min_filled_blocks, query=q)
    return isoformat_rows(rows)


@router.get("/{inn}/facts", response_model=FactsResponse,
            summary="Детерминированные факты без обращения к LLM",
            responses={404: {"model": ErrorOut}})
async def get_facts(inn: str = Path(..., pattern=r"^\d{10,12}$"),
                    settings: Settings = Depends(settings_dep)) -> Dict[str, Any]:
    """Тот самый слой S2: что именно уходит в модель. Полезно для сверки
    вычисленных фактов с сырыми полями карточки."""
    snapshot = await _snapshot_or_404(inn)
    document = snapshot["document"]
    blocks = facts_mod.build_all_blocks(document)
    return {
        "inn": inn,
        "company": company_out(snapshot),
        "coverage": facts_mod.build_coverage(document),
        "blocks": [blocks[key].to_dict() for key in facts_mod.BLOCK_KEYS],
        "calculator_version": settings.calculator_version,
    }


@router.get("/{inn}/coverage", response_model=CoverageOut,
            summary="Паспорт полноты данных карточки",
            responses={404: {"model": ErrorOut}})
async def get_coverage(inn: str = Path(..., pattern=r"^\d{10,12}$")) -> Dict[str, Any]:
    snapshot = await _snapshot_or_404(inn)
    return facts_mod.build_coverage(snapshot["document"])


async def _snapshot_or_404(inn: str) -> Dict[str, Any]:
    snapshot = await repository.get_latest_snapshot(inn)
    if snapshot is None or not snapshot.get("document"):
        raise HTTPException(status_code=404, detail="Карточка по ИНН %s не найдена" % inn)
    return snapshot

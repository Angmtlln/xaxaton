"""Полный проход по одному ИНН и история прогонов."""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.api.deps import groq_dep, settings_dep
from app.api.schemas import CheckRequest, CheckResponse, ErrorOut, RunListItem
from app.api.serialization import isoformat_row, isoformat_rows
from app.config import Settings
from app.domain.pipeline import CompanyNotFound, run_check
from app.infrastructure import repository
from app.llm.groq_client import GroqClient

router = APIRouter(prefix="/api/v1/checks", tags=["checks"])


@router.post("", response_model=CheckResponse, summary="Запустить проход по ИНН",
             responses={404: {"model": ErrorOut, "description": "Карточка по ИНН не найдена"},
                        503: {"model": ErrorOut, "description": "БД или модель недоступны"}})
async def create_check(payload: CheckRequest,
                       settings: Settings = Depends(settings_dep),
                       client: GroqClient = Depends(groq_dep)) -> Dict[str, Any]:
    """Полный проход: факты → 4 блочных агента → Summary-LLM.

    Ответ содержит итог для экрана, четыре блочных резюме, все факты со
    ссылками на поля карточки, паспорт полноты данных и метрику
    заземления утверждений.
    """
    try:
        return await run_check(payload.inn, settings, client, persist=payload.persist)
    except CompanyNotFound as exc:
        raise HTTPException(status_code=404, detail="Карточка по ИНН %s не найдена" % exc) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("", response_model=List[RunListItem], summary="История проходов")
async def list_checks(inn: Optional[str] = Query(None, pattern=r"^\d{10,12}$"),
                      limit: int = Query(20, ge=1, le=100)) -> List[Dict[str, Any]]:
    return isoformat_rows(await repository.list_runs(inn=inn, limit=limit))


@router.get("/{run_id}", summary="Результат прохода по идентификатору",
            responses={404: {"model": ErrorOut}})
async def get_check(run_id: str = Path(..., description="UUID прогона")) -> Dict[str, Any]:
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Прогон %s не найден" % run_id)
    return isoformat_row(run)

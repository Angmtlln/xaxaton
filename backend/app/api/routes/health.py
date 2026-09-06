"""Здоровье сервиса: доступность БД и текущая раскладка моделей."""
from fastapi import APIRouter, Depends

from app.api.deps import settings_dep
from app.api.schemas import HealthOut
from app.config import Settings
from app.infrastructure.db import healthcheck
from app.infrastructure.company_reader import get_company_reader

router = APIRouter(tags=["service"])


@router.get("/health", response_model=HealthOut, summary="Здоровье сервиса")
async def health(settings: Settings = Depends(settings_dep)) -> HealthOut:
    try:
        db_ok = await healthcheck()
    except Exception:                                     # noqa: BLE001
        db_ok = False
    if settings.company_data_backend == 'direct':
        source_ok = db_ok
    else:
        try:
            source_ok = (await get_company_reader(settings).data_source_status())['database']
        except Exception:
            source_ok = False
    llm_mode = "groq" if (settings.groq_api_key and not settings.llm_mock) else "mock"
    return HealthOut(
        status="ok" if db_ok and source_ok else "degraded",
        data_source=settings.company_data_backend,
        data_source_available=source_ok,
        database=db_ok,
        llm_mode=llm_mode,
        block_model=settings.groq_block_model if llm_mode == "groq" else "deterministic",
        block_models=settings.block_models() if llm_mode == "groq" else {},
        summary_model=settings.groq_summary_model if llm_mode == "groq" else "deterministic",
        version=settings.app_version,
    )

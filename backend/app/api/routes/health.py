"""Здоровье сервиса: доступность БД и текущая раскладка моделей."""
from fastapi import APIRouter, Depends

from app.api.deps import settings_dep
from app.api.schemas import HealthOut
from app.config import Settings
from app.infrastructure.db import healthcheck

router = APIRouter(tags=["service"])


@router.get("/health", response_model=HealthOut, summary="Здоровье сервиса")
async def health(settings: Settings = Depends(settings_dep)) -> HealthOut:
    try:
        db_ok = await healthcheck()
    except Exception:                                     # noqa: BLE001
        db_ok = False
    llm_mode = "groq" if (settings.groq_api_key and not settings.llm_mock) else "mock"
    return HealthOut(
        status="ok" if db_ok else "degraded",
        database=db_ok,
        llm_mode=llm_mode,
        block_model=settings.groq_block_model if llm_mode == "groq" else "deterministic",
        block_models=settings.block_models() if llm_mode == "groq" else {},
        summary_model=settings.groq_summary_model if llm_mode == "groq" else "deterministic",
        version=settings.app_version,
    )

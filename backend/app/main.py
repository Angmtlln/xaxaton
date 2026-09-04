"""FastAPI-приложение PoC «ИИ-агент для проверки контрагента».

Swagger UI: /docs, ReDoc: /redoc, схема OpenAPI: /openapi.json
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path as FilePath
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Path, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import repository
from app.agent.models import AssistantResponse
from app.agent.runtime import build_master_runtime
from app.api.schemas import (ChatMessageRequest, CheckRequest, CheckResponse,
                             CompanyListItem, CoverageOut, ErrorOut, FactsResponse,
                             HealthOut, RunListItem)
from app.config import Settings, get_settings
from app.db import close_pool, healthcheck, init_pool
from app.domain import facts as facts_mod
from app.llm.groq_client import GroqClient
from app.pipeline import CompanyNotFound, run_check

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("contractor-agent")

DESCRIPTION = """
Agent-first PoC проверки контрагента. Основной путь начинается с сообщения
`Проверь контрагента <ИНН>` в chat API, а существующий полный проход сохранён
как единственный allowlisted tool `full_company_check`.

Как устроен проход:

1. по ИНН из PostgreSQL берётся последняя карточка отчёта;
2. детерминированный слой считает факты кодом из сырых полей карточки
   и раскладывает их по четырём блокам;
3. четыре доменных агента Groq работают параллельно, каждый видит
   только свой блок;
4. Summary-LLM собирает четыре блочных резюме в один экран ответа.

Ограничения, заложенные в продукт:

* оценки банка `riskLevel` и `zskRiskLevel` приводятся без изменений,
  свой скоринг не считается;
* каждое утверждение агента несёт ссылку на факт и поле карточки;
* при отсутствии данных агент обязан сказать «невозможно оценить»;
* готовые текстовые формулировки отчёта не являются входом для выводов.
"""

TAGS = [
    {"name": "chat", "description": "Agent-first сообщения и rich AssistantResponse"},
    {"name": "checks", "description": "Проход проверки по одному ИНН"},
    {"name": "companies", "description": "Карточки и детерминированные факты без вызова LLM"},
    {"name": "service", "description": "Здоровье сервиса"},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.groq = GroqClient(settings)
    try:
        await init_pool(settings)
    except Exception as exc:                              # noqa: BLE001
        log.error("Не удалось подключиться к БД: %s", exc)
    yield
    await app.state.groq.aclose()
    await close_pool()


app = FastAPI(
    title="Контрагент-агент. PoC",
    description=DESCRIPTION,
    version=get_settings().app_version,
    openapi_tags=TAGS,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# Рабочий agent-first чат и legacy-отчёт раздаются одним FastAPI-сервисом.
STATIC_DIR = FilePath(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Agent-first чат для полной проверки по явному ИНН."""
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/report", include_in_schema=False)
async def report_page() -> FileResponse:
    """Страница отчёта. ИНН приходит query-параметром: /report?inn=..."""
    return FileResponse(str(STATIC_DIR / "report.html"))


def settings_dep() -> Settings:
    return get_settings()


def groq_dep() -> GroqClient:
    return app.state.groq


@app.post(
    "/api/v1/chat/messages",
    response_model=AssistantResponse,
    tags=["chat"],
    summary="Проверить контрагента через Master Agent",
)
async def create_chat_message(
    payload: ChatMessageRequest,
    settings: Settings = Depends(settings_dep),
    client: GroqClient = Depends(groq_dep),
) -> AssistantResponse:
    """Первый vertical slice: один явный ИНН → один full_company_check."""
    runtime = build_master_runtime(settings, client, persist=True)
    return await runtime.run(payload.message)


@app.get("/health", response_model=HealthOut, tags=["service"], summary="Здоровье сервиса")
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


@app.post("/api/v1/checks", response_model=CheckResponse, tags=["checks"],
          summary="Запустить проход по ИНН",
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


@app.get("/api/v1/checks", response_model=List[RunListItem], tags=["checks"],
         summary="История проходов")
async def list_checks(inn: Optional[str] = Query(None, pattern=r"^\d{10,12}$"),
                      limit: int = Query(20, ge=1, le=100)) -> List[Dict[str, Any]]:
    return _isoformat_rows(await repository.list_runs(inn=inn, limit=limit))


@app.get("/api/v1/checks/{run_id}", tags=["checks"], summary="Результат прохода по идентификатору",
         responses={404: {"model": ErrorOut}})
async def get_check(run_id: str = Path(..., description="UUID прогона")) -> Dict[str, Any]:
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Прогон %s не найден" % run_id)
    return _isoformat_row(run)


@app.get("/api/v1/companies", response_model=List[CompanyListItem], tags=["companies"],
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
    return _isoformat_rows(rows)


@app.get("/api/v1/companies/{inn}/facts", response_model=FactsResponse, tags=["companies"],
         summary="Детерминированные факты без обращения к LLM",
         responses={404: {"model": ErrorOut}})
async def get_facts(inn: str = Path(..., pattern=r"^\d{10,12}$"),
                    settings: Settings = Depends(settings_dep)) -> Dict[str, Any]:
    """Тот самый слой S2: что именно уходит в модель. Полезно для сверки
    вычисленных фактов с сырыми полями карточки."""
    snapshot = await repository.get_latest_snapshot(inn)
    if snapshot is None or not snapshot.get("document"):
        raise HTTPException(status_code=404, detail="Карточка по ИНН %s не найдена" % inn)
    document = snapshot["document"]
    blocks = facts_mod.build_all_blocks(document)
    return {
        "inn": inn,
        "company": _company_out(snapshot),
        "coverage": facts_mod.build_coverage(document),
        "blocks": [blocks[key].to_dict() for key in facts_mod.BLOCK_KEYS],
        "calculator_version": settings.calculator_version,
    }


@app.get("/api/v1/companies/{inn}/coverage", response_model=CoverageOut, tags=["companies"],
         summary="Паспорт полноты данных карточки",
         responses={404: {"model": ErrorOut}})
async def get_coverage(inn: str = Path(..., pattern=r"^\d{10,12}$")) -> Dict[str, Any]:
    snapshot = await repository.get_latest_snapshot(inn)
    if snapshot is None or not snapshot.get("document"):
        raise HTTPException(status_code=404, detail="Карточка по ИНН %s не найдена" % inn)
    return facts_mod.build_coverage(snapshot["document"])


@app.exception_handler(CompanyNotFound)
async def not_found_handler(request, exc: CompanyNotFound) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND,
                        content={"detail": "Карточка по ИНН %s не найдена" % exc})


# ------------------------------ утилиты ------------------------------

def _company_out(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "inn": snapshot["inn"],
        "ogrn": snapshot.get("ogrn"),
        "short_name": snapshot.get("short_name"),
        "full_name": snapshot.get("full_name"),
        "address": snapshot.get("address"),
        "status": snapshot.get("status"),
        "registration_date": _iso(snapshot.get("registration_date")),
        "years_from_registration": snapshot.get("years_from_registration"),
        "risk_level": snapshot.get("risk_level"),
        "zsk_risk_level": snapshot.get("zsk_risk_level"),
        "report_date": _iso(snapshot.get("report_date")),
    }


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _isoformat_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: (_iso(v) if hasattr(v, "isoformat") else v) for k, v in row.items()}


def _isoformat_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [_isoformat_row(r) for r in rows]

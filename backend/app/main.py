"""FastAPI-приложение PoC «ИИ-агент для проверки контрагента».

Здесь только сборка приложения: жизненный цикл, middleware, статика и
подключение роутеров. Обработчики живут в app/api/routes/.

Swagger UI: /docs, ReDoc: /redoc, схема OpenAPI: /openapi.json
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agent.conversations import ConversationStore
from app.api.routes import api_router, pages_router
from app.api.routes.pages import frontend_dir
from app.config import get_settings
from app.domain.pipeline import CompanyNotFound
from app.infrastructure.db import close_pool, init_pool
from app.llm.groq_client import GroqClient

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("contractor-agent")

DESCRIPTION = """
Agent-first PoC проверки контрагента. Основной путь начинается с сообщения
`Проверь контрагента <ИНН>` в chat API, а существующий полный проход сохранён
как allowlisted tool `full_company_check`. Финансовые и юридические follow-up
используют `get_financial_data` и `get_legal_data` без повторного полного анализа.
Передайте `conversation_id` из ответа, чтобы продолжить диалог об активной компании.
Состояние хранится в памяти одного процесса и теряется при перезапуске. Master Agent использует
LangChain `create_agent`, а rich response гидратируется backend-кодом.
Ответ чата начинается с компактного `leading_artifact` типа `company_summary`
только после полной проверки. Основной текст находится в `message`, дополнительные
артефакты — в `blocks`, источники — в `evidence`. Узкие вопросы не повторяют
сводку компании. `/report` остаётся отдельным полным отчётом.

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
    app.state.conversation_store = ConversationStore()
    try:
        await init_pool(settings)
    except Exception as exc:                              # noqa: BLE001
        log.error("Не удалось подключиться к БД: %s", exc)
    yield
    await app.state.groq.aclose()
    await close_pool()


def create_app() -> FastAPI:
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
    static_dir = frontend_dir()
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(api_router)
    app.include_router(pages_router)

    @app.exception_handler(CompanyNotFound)
    async def not_found_handler(request, exc: CompanyNotFound) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND,
                            content={"detail": "Карточка по ИНН %s не найдена" % exc})

    return app


app = create_app()

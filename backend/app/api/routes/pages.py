"""Отдача страниц интерфейса. Исходники лежат в отдельной папке frontend/."""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.config import get_settings

# backend/app/api/routes/pages.py → корень репозитория → frontend/
_DEFAULT_FRONTEND_DIR = Path(__file__).resolve().parents[4] / "frontend"


def frontend_dir() -> Path:
    """Каталог собранного интерфейса; переопределяется переменной FRONTEND_DIR."""
    configured = get_settings().frontend_dir
    return Path(configured) if configured else _DEFAULT_FRONTEND_DIR


router = APIRouter(include_in_schema=False)


@router.get("/")
async def index() -> FileResponse:
    """Agent-first чат с контекстом компании и targeted follow-up."""
    return FileResponse(str(frontend_dir() / "index.html"))


@router.get("/report")
async def report_page() -> FileResponse:
    """Страница отчёта. ИНН приходит query-параметром: /report?inn=..."""
    return FileResponse(str(frontend_dir() / "report.html"))

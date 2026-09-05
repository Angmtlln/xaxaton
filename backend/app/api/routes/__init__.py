"""HTTP-слой: по одному роутеру на ресурс, схемы Swagger — в app/api/schemas.py."""
from fastapi import APIRouter

from . import chat, checks, companies, health, pages

api_router = APIRouter()
for module in (chat, checks, companies, health):
    api_router.include_router(module.router)

pages_router = pages.router

__all__ = ["api_router", "pages_router"]

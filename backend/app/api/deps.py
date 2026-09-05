"""Зависимости HTTP-слоя. Состояние живёт на приложении, а не в модуле."""
from fastapi import Request

from app.agent.conversations import ConversationStore
from app.config import Settings, get_settings
from app.llm.groq_client import GroqClient


def settings_dep() -> Settings:
    return get_settings()


def groq_dep(request: Request) -> GroqClient:
    return request.app.state.groq


def conversation_store_dep(request: Request) -> ConversationStore:
    return request.app.state.conversation_store

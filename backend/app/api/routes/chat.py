"""Основной путь продукта: сообщение AI-аналитику и rich AssistantResponse."""
from fastapi import APIRouter, Depends

from app.agent.conversations import ConversationStore
from app.agent.models import AssistantResponse
from app.agent.runtime import build_master_runtime
from app.api.deps import conversation_store_dep, groq_dep, settings_dep
from app.api.schemas import ChatMessageRequest
from app.config import Settings
from app.llm.groq_client import GroqClient

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


@router.post("/messages", response_model=AssistantResponse,
             summary="Задать вопрос AI-аналитику о контрагенте")
async def create_chat_message(
    payload: ChatMessageRequest,
    settings: Settings = Depends(settings_dep),
    client: GroqClient = Depends(groq_dep),
    conversations: ConversationStore = Depends(conversation_store_dep),
) -> AssistantResponse:
    """Новый диалог или продолжение по conversation_id; неизвестный ID не запускает tools."""
    runtime = build_master_runtime(
        settings, client, persist=True, conversation_store=conversations,
    )
    return await runtime.run(
        payload.message,
        conversation_id=str(payload.conversation_id) if payload.conversation_id else None,
    )

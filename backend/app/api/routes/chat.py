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


async def chat_events(runtime, payload):
    """NDJSON events, bounded queue; cancel request work when the stream closes."""
    import asyncio
    import json
    import logging
    from contextlib import suppress
    from app.infrastructure.progress import emit_progress, progress_sink

    queue = asyncio.Queue(maxsize=64)
    def sink(event):
        # Progress is disposable; the final result is delivered with await put.
        if not queue.full():
            queue.put_nowait(event)

    async def run():
        token = progress_sink.set(sink)
        try:
            emit_progress('accepted')
            response = await runtime.run(payload.message,
                conversation_id=str(payload.conversation_id) if payload.conversation_id else None)
            await queue.put({'type': 'result', 'payload': response.model_dump(mode='json')})
        except Exception:
            logging.getLogger(__name__).exception('Chat stream failed')
            await queue.put({'type': 'error', 'detail': 'Не удалось завершить запрос. Попробуйте ещё раз.'})
        finally:
            progress_sink.reset(token)

    task = asyncio.create_task(run())
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=10)
            except asyncio.TimeoutError:
                event = {'type': 'heartbeat'}
            yield json.dumps(event, ensure_ascii=False, separators=(',', ':')) + '\n'
            if event['type'] in {'result', 'error'}:
                break
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@router.post('/messages/stream', summary='Ответ аналитика с публичными статусами этапов')
async def stream_chat_message(
    payload: ChatMessageRequest,
    settings: Settings = Depends(settings_dep),
    client: GroqClient = Depends(groq_dep),
    conversations: ConversationStore = Depends(conversation_store_dep),
):
    from fastapi.responses import StreamingResponse
    runtime = build_master_runtime(settings, client, persist=True, conversation_store=conversations)
    return StreamingResponse(chat_events(runtime, payload), media_type='application/x-ndjson',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

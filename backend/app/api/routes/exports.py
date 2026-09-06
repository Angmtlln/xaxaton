"""PDF creation and download within the existing process-local conversation lease."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from app.agent.conversations import ConversationStore, UnknownConversation
from app.agent.models import PdfAttachment
from app.agent.pdf_export import ExportUnavailable
from app.api.deps import conversation_store_dep

router = APIRouter(prefix='/api/v1/chat', tags=['chat'])


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    result_id: UUID


@router.post('/{conversation_id}/exports', response_model=PdfAttachment)
async def create_export(conversation_id: UUID, payload: ExportRequest,
                        store: ConversationStore = Depends(conversation_store_dep)):
    try:
        async with store.session(str(conversation_id)):
            store.exports.record(str(conversation_id), str(payload.result_id))
            try:
                return await store.exports.export_result_pdf(str(conversation_id), str(payload.result_id))
            except ExportUnavailable as exc:
                raise HTTPException(503, str(exc)) from exc
    except (UnknownConversation, ExportUnavailable) as exc:
        raise HTTPException(404, 'Результат недоступен или сессия истекла.') from exc


@router.get('/{conversation_id}/exports/{file_id}')
async def download_export(conversation_id: UUID, file_id: UUID,
                          store: ConversationStore = Depends(conversation_store_dep)):
    try:
        async with store.session(str(conversation_id)):
            attachment, data = store.exports.download(str(conversation_id), str(file_id))
            return Response(data, media_type='application/pdf', headers={
                'Content-Disposition': f'attachment; filename="{attachment.filename}"',
                'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
    except (UnknownConversation, ExportUnavailable) as exc:
        raise HTTPException(404, 'Файл недоступен или сессия истекла.') from exc

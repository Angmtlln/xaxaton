import asyncio
import json
from types import SimpleNamespace
import pytest
from app.api.routes.chat import chat_events
from app.api.schemas import ChatMessageRequest
from app.infrastructure.progress import emit_progress, progress_sink


class Runtime:
    def __init__(self, stage): self.stage = stage
    async def run(self, *args, **kwargs):
        emit_progress(self.stage)
        await asyncio.sleep(0)
        return SimpleNamespace(model_dump=lambda **kwargs: {'message': self.stage})


@pytest.mark.asyncio
async def test_progress_order_final_result_and_request_isolation():
    async def collect(stage):
        return [json.loads(line) async for line in chat_events(Runtime(stage), ChatMessageRequest(message='Привет'))]
    a, b = await asyncio.gather(collect('finance'), collect('legal'))
    assert [e.get('stage') for e in a if e['type'] == 'progress'] == ['accepted', 'finance']
    assert [e.get('stage') for e in b if e['type'] == 'progress'] == ['accepted', 'legal']
    assert a[-1] == {'type': 'result', 'payload': {'message': 'finance'}}
    assert progress_sink.get() is None


@pytest.mark.asyncio
async def test_disconnect_cancels_work():
    cancelled = asyncio.Event()
    class Slow:
        async def run(self, *args, **kwargs):
            try: await asyncio.Event().wait()
            finally: cancelled.set()
    stream = chat_events(Slow(), ChatMessageRequest(message='Привет'))
    assert json.loads(await anext(stream))['stage'] == 'accepted'
    await stream.aclose()
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_error_does_not_leak_exception():
    class Fail:
        async def run(self, *args, **kwargs): raise ValueError('private provider payload')
    events = [json.loads(line) async for line in chat_events(Fail(), ChatMessageRequest(message='Привет'))]
    assert events[-1]['type'] == 'error'
    assert 'private' not in json.dumps(events)

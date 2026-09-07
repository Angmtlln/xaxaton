"""Provider accounting across turns and concurrent conversations."""
import asyncio
import httpx
import pytest
from app.agent.master_model import OpenRouterChatModel
from app.agent.models import AssistantMetadata
from app.agent.usage import ConversationUsage, active_usage
from app.agent.response import guard_response
from test_agent_runtime import _runtime


def test_sum_and_missing_usage():
    usage = ConversationUsage()
    usage.record({'prompt_tokens': 100, 'completion_tokens': 20, 'cost': 0.001})
    usage.record({'prompt_tokens': 200, 'completion_tokens': 30, 'cost': 0.002})
    assert usage.model_dump() == dict(input_tokens=300, output_tokens=50, cost_usd=0.003, calls=2, incomplete=False)
    usage.record({'prompt_tokens': 1, 'completion_tokens': 2})
    assert usage.incomplete and usage.cost_usd == 0.003
    for cost in [None, -1, 'nan', 'inf', True]:
        usage.record({'cost': cost})
    assert usage.cost_usd == 0.003


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [False, True])
async def test_adapter_with_mock_http(failure):
    async def handler(request):
        if failure:
            return httpx.Response(500, json={'error': {'message': 'unavailable'}})
        return httpx.Response(200, json={
            'id': 'test-1', 'model': 'test-model', 'object': 'chat.completion', 'created': 1,
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'Ответ'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120, 'cost': 0.00123}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        model = OpenRouterChatModel(api_key='test', model='test-model', http_async_client=client, max_retries=0)
        usage = ConversationUsage()
        token = active_usage.set(usage)
        try:
            if failure:
                with pytest.raises(Exception):
                    await model.ainvoke('Вопрос')
            else:
                await model.ainvoke('Первый вопрос')
                await model.bind(max_tokens=20).ainvoke('Второй вопрос')
        finally:
            active_usage.reset(token)
    if failure:
        assert usage.calls == 1 and usage.incomplete
    else:
        assert usage.cost_usd == 0.00246
        assert usage.input_tokens == 200 and usage.output_tokens == 40
        assert usage.calls == 2 and not usage.incomplete


@pytest.mark.asyncio
async def test_runtime_accumulates_and_isolates(monkeypatch):
    runtime = _runtime(None)
    async def fake_turn(message, cid, run_id, started, *args):
        await asyncio.sleep(0)
        active_usage.get().record({'prompt_tokens': 10, 'completion_tokens': 2, 'cost': 0.01})
        response = guard_response('missing_inn', run_id, started)
        response.conversation_id = cid
        return response
    monkeypatch.setattr(runtime, '_run_conversation', fake_turn)
    first, other = await asyncio.gather(runtime.run('Первый'), runtime.run('Другой'))
    second = await runtime.run('Продолжение', first.conversation_id)
    assert first.metadata.conversation_usage.cost_usd == 0.01
    assert other.metadata.conversation_usage.cost_usd == 0.01
    assert second.metadata.conversation_usage.cost_usd == 0.02
    assert AssistantMetadata.model_validate(second.metadata.model_dump()).conversation_usage.calls == 2
    assert active_usage.get() is None

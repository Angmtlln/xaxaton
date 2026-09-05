"""Latency shortcuts keep factual validation, repair and legacy report behavior."""
import pytest
from langchain_core.messages import AIMessage
from app.agent.runtime import is_direct_request
from app.domain import pipeline
from app.llm.groq_client import GroqClient
from test_agent_runtime import _runtime, _model, _answer, _verified, _settings
from test_agent_multiturn import targeted_result


@pytest.mark.asyncio
async def test_direct_full_check_keeps_master_and_verifier(monkeypatch, check_payload):
    calls = []
    async def check(inn, settings, client, persist, *, include_summary):
        calls.append((inn, include_summary))
        return check_payload
    monkeypatch.setattr('app.agent.tools.run_check', check)
    model = _model(_answer(), _verified(), _answer('Проще: данные нужно уточнить.'), _verified())
    runtime = _runtime(model, direct_dispatch=True)
    first = await runtime.run('Проверь контрагента 6165169320')
    follow = await runtime.run('Объясни проще', first.conversation_id)
    assert calls == [('6165169320', False)]
    assert first.metadata.model_calls == follow.metadata.model_calls == 2
    assert first.metadata.tool_calls == 1 and follow.metadata.tool_calls == 0
    assert first.metadata.grounding_status == follow.metadata.grounding_status == 'verified'
    assert first.leading_artifact.type == 'company_summary'
    assert not model._tool_bindings


@pytest.mark.asyncio
async def test_targeted_reuse_is_domain_specific_and_refreshable(monkeypatch, check_payload):
    async def check(*args, **kwargs): return check_payload
    monkeypatch.setattr('app.agent.tools.run_check', check)
    runtime = _runtime(_model(*sum(([ _answer(), _verified()] for _ in range(7)), [])), direct_dispatch=True)
    first = await runtime.run('Проверь контрагента 6165169320')
    calls = []
    async def execute(name, args, context):
        calls.append(name)
        return targeted_result('finance' if name == 'get_financial_data' else 'legal')
    monkeypatch.setattr(runtime.registry, 'execute', execute)
    for question in ['А что у них с финансами?', 'А с судами?', 'Финансы?', 'Почему?', 'Обнови финансы', 'Финансы?']:
        response = await runtime.run(question, first.conversation_id)
        assert response.metadata.grounding_status == 'verified'
    assert calls == ['get_financial_data', 'get_legal_data', 'get_financial_data']
    state = await runtime.conversation_store.checkpointer.aget_tuple({'configurable': {'thread_id':first.conversation_id}})
    assert state.checkpoint['channel_values']['last_topic'] == 'finance'


@pytest.mark.asyncio
async def test_optional_debug_keeps_single_master_repair(monkeypatch, check_payload):
    async def check(*args, **kwargs): return check_payload
    monkeypatch.setattr('app.agent.tools.run_check', check)
    master = _model(_answer('Есть 99 филиалов.'), AIMessage(content='{"supported":false,"unsupported_claims":["Нет 99 филиалов"]}'), _answer('Число филиалов не подтверждено.'), _verified())
    runtime = _runtime(master, direct_dispatch=True)
    response = await runtime.run('Проверь контрагента 6165169320')
    assert master.calls == 4
    assert response.metadata.grounding_status == 'repaired'
    assert response.metadata.repair_attempts == 1
    assert response.metadata.model_calls == 4


@pytest.mark.parametrize('message,target,expected', [
    ('Проверь контрагента 6165169320','full_company_check', True),
    ('Если нужно, проверь контрагента 6165169320','full_company_check', False),
    ('Сравни 6165169320, 2901324364 и 0278949271','compare_companies', True),
    ('Сравни по финансам 6165169320 и 0278949271','compare_companies', False),
])
def test_direct_dispatch_only_admits_whole_simple_commands(message,target,expected):
    assert is_direct_request(message,target) is expected


@pytest.mark.asyncio
async def test_summary_skip_preserves_facts_and_legacy_default(monkeypatch, document):
    snapshot = {'inn':'6165169320', 'document':document}
    async def get_snapshot(inn): return snapshot
    monkeypatch.setattr(pipeline.repository, 'get_latest_snapshot', get_snapshot)
    settings = _settings()
    client = GroqClient(settings)
    calls = []
    original = pipeline.run_summary_agent
    async def summary(*args, **kwargs):
        calls.append(True)
        return await original(*args, **kwargs)
    monkeypatch.setattr(pipeline,'run_summary_agent',summary)
    chat = await pipeline.run_check('6165169320',settings,client,persist=False,include_summary=False)
    assert calls == []
    assert chat['llm']['summary_model'] == 'not_requested'
    assert chat['summary']['error'] is None
    legacy = await pipeline.run_check('6165169320',settings,client,persist=False)
    assert calls == [True]
    assert legacy['summary']['narrative']
    assert chat['coverage'] == legacy['coverage']
    assert [b['facts'] for b in chat['blocks']] == [b['facts'] for b in legacy['blocks']]
    assert chat['status'] == legacy['status']


@pytest.mark.asyncio
async def test_default_chat_never_calls_verifier_or_repair_even_for_rewrite(monkeypatch, check_payload):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Verifier/repair must not run in synchronous chat")
    monkeypatch.setattr('app.agent.runtime.call_grounding_verifier', forbidden)
    monkeypatch.setattr('app.agent.runtime.call_master_repair', forbidden)
    async def check(*args, **kwargs): return check_payload
    monkeypatch.setattr('app.agent.tools.run_check', check)
    model = _model(_answer(), _answer('Это требует уточнения.'), _answer('Проще: уточните данные.'))
    runtime = _runtime(model, direct_dispatch=True, grounding_debug=False)
    assert _settings().agent_grounding_debug is False
    cid = None
    for q in ['Проверь контрагента 6165169320', 'Почему это вообще плохо?', 'Объясни проще']:
        response = await runtime.run(q, cid)
        cid = response.conversation_id
        assert response.metadata.model_calls == 1
        assert response.metadata.grounding_status == 'not_requested'
        assert response.metadata.repair_attempts == 0
        assert response.metadata.synthesis == 'model'
    assert model.calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['ИНН 0278949271', 'Источник https://invented.example/data', '<svg>bad</svg>'])
async def test_structural_violation_falls_back_without_llm_repair(monkeypatch, check_payload, text):
    async def check(*args, **kwargs): return check_payload
    monkeypatch.setattr('app.agent.tools.run_check', check)
    model = _model(_answer(text))
    runtime = _runtime(model, direct_dispatch=True, grounding_debug=False)
    response = await runtime.run('Проверь контрагента 6165169320')
    assert model.calls == 1
    assert response.metadata.repair_attempts == 0
    assert response.metadata.synthesis == 'fallback'
    assert text not in response.message


def test_comparison_identifiers_use_all_verified_companies():
    from app.agent.grounding import backend_owned_violations
    context = {'companies': [{'inn':'6165169320'}, {'inn':'0278949271'}]}
    assert backend_owned_violations('ИНН 6165169320 и ИНН 0278949271',context) == []
    assert backend_owned_violations('ИНН 2901324364',context)


@pytest.mark.asyncio
async def test_direct_three_company_comparison_has_one_model_call(monkeypatch):
    from test_comparison import _snapshot
    inns = ['6165169320', '2901324364', '0278949271']
    async def snapshot(inn):
        assert inn in inns
        return _snapshot(inn, 'Компания ' + inn)
    monkeypatch.setattr('app.infrastructure.repository.get_latest_snapshot', snapshot)
    model = _model(_answer('Данных недостаточно для выбора.'))
    runtime = _runtime(model, direct_dispatch=True, grounding_debug=False)
    response = await runtime.run('Сравни 6165169320, 2901324364 и 0278949271')
    assert response.metadata.model_calls == response.metadata.tool_calls == 1
    assert response.metadata.synthesis == 'model'
    assert response.metadata.grounding_status == 'not_requested'
    table = next(block for block in response.blocks if block.type == 'comparison_table')
    assert [column.inn for column in table.columns] == inns
    assert not model._tool_bindings

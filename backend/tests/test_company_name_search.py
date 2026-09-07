"""User-visible name resolution, continuation and backend-owned identities."""
import json
import uuid

import pytest
from langchain_core.messages import AIMessage

from app.domain.company_search import CompanySearchResult
from app.mcp_data.errors import CompanySourceError
from test_agent_runtime import _answer, _model, _runtime, _tool_call, FailingToolCallingModel
from test_agent_multiturn import targeted_result
from test_chat_api import api_client

INN = "6165169320"
SECOND = "2311304742"


def row(inn=INN, name='ООО «Электролид»', match='exact'):
    return dict(inn=inn, name=name, full_name=None, address='Москва', snapshot_id=1, match=match)


def search_call(query='Электролид'):
    return AIMessage(content='', tool_calls=[_tool_call('search_companies', {'query': query})])


@pytest.fixture
def searches(monkeypatch):
    state = {'calls': [], 'found': {'rows': [row()], 'total': 1, 'exact_total': 1}}
    async def search(**kwargs):
        state['calls'].append(kwargs)
        return state['found']
    monkeypatch.setattr('app.infrastructure.repository.search_companies', search)
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize('message', ['Проверь Электролид', 'Электролид'])
async def test_exact_name_continues_full_check(message, searches, monkeypatch, check_payload):
    calls = []
    async def check(inn, *args, **kwargs):
        calls.append(inn)
        return check_payload
    monkeypatch.setattr('app.agent.tools.run_check', check)
    runtime = _runtime(_model(search_call(), _answer()), name_resolution=True,
                       direct_dispatch=True, grounding_debug=False)
    response = await runtime.run(message)
    assert calls == [INN]
    assert response.active_company.inn == INN
    assert response.metadata.tool_calls == 2
    assert response.metadata.model_calls == 2
    assert response.leading_artifact is not None


@pytest.mark.asyncio
async def test_unique_exact_with_partial_matches_still_continues(searches, monkeypatch, check_payload):
    searches['found'] = {'rows': [row(), row(SECOND, match='prefix')], 'total': 9, 'exact_total': 1}
    async def check(*args, **kwargs): return check_payload
    monkeypatch.setattr('app.agent.tools.run_check', check)
    response = await _runtime(_model(search_call(), _answer()), name_resolution=True,
        direct_dispatch=True, grounding_debug=False).run('Проверь Электролид')
    assert response.active_company.inn == INN
    assert not any(b.type == 'company_choice' for b in response.blocks)


@pytest.mark.asyncio
async def test_name_question_is_targeted_and_rewrite_uses_context(searches, monkeypatch):
    model = _model(search_call(), AIMessage(content='', tool_calls=[_tool_call('get_financial_data')]),
                   _answer('Данные получены.'), _answer('Объяснение проще.'))
    runtime = _runtime(model, name_resolution=True, grounding_debug=False)
    calls = []
    async def execute(name, args, context):
        calls.append((name, args))
        return targeted_result()
    monkeypatch.setattr(runtime.registry, 'execute', execute)
    first = await runtime.run('Какая выручка у Электролид?')
    assert calls == [('get_financial_data', {'inn': INN})]
    assert first.leading_artifact is None
    second = await runtime.run('Объясни проще', first.conversation_id)
    assert second.metadata.tool_calls == 0
    assert len(searches['calls']) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('choice_kind', ['ordinal', 'inn', 'button'])
async def test_ambiguous_choice_preserves_question(choice_kind, searches, monkeypatch):
    searches['found'] = {'rows': [row(SECOND), row()], 'total': 2, 'exact_total': 2}
    model = _model(search_call(), AIMessage(content='', tool_calls=[_tool_call('get_financial_data')]), _answer())
    runtime = _runtime(model, name_resolution=True, grounding_debug=False)
    calls = []
    async def execute(name, args, context):
        calls.append((name, args)); return targeted_result()
    monkeypatch.setattr(runtime.registry, 'execute', execute)
    first = await runtime.run('Какая выручка у Электролид?')
    block = first.blocks[0]
    assert first.metadata.status == 'needs_input' and block.type == 'company_choice'
    assert calls == [] and first.active_company is None
    selection = {'search_id': block.search_id, 'inn': INN} if choice_kind == 'button' else None
    second = await runtime.run('Выбираю компанию' if selection else INN if choice_kind == 'inn' else 'вторая',
                               first.conversation_id, company_selection=selection)
    assert second.active_company.inn == INN
    assert calls == [('get_financial_data', {'inn': INN})]
    assert len(searches['calls']) == 1


@pytest.mark.asyncio
async def test_partial_single_match_requires_choice(searches):
    searches['found'] = {'rows': [row(match='partial')], 'total': 1, 'exact_total': 0}
    response = await _runtime(_model(search_call()), name_resolution=True).run('Проверь Электролид')
    assert response.metadata.status == 'needs_input'
    assert response.blocks[0].rows[0].inn == INN


@pytest.mark.asyncio
async def test_stale_button_and_inn_outside_list_do_not_check(searches):
    searches['found'] = {'rows': [row(match='prefix')], 'total': 1, 'exact_total': 0}
    runtime = _runtime(_model(search_call()), name_resolution=True)
    first = await runtime.run('Проверь Электролид')
    wrong = await runtime.run(SECOND, first.conversation_id)
    assert wrong.metadata.error_code == 'invalid_company_choice'
    stale = await runtime.run('Выбираю компанию', first.conversation_id,
                             company_selection={'search_id': str(uuid.uuid4()), 'inn': INN})
    assert stale.metadata.error_code == 'stale_company_choice'
    assert stale.metadata.tool_calls == 0


@pytest.mark.asyncio
async def test_new_name_replaces_active_company(searches, monkeypatch):
    calls = []
    model = _model(AIMessage(content='', tool_calls=[_tool_call('get_financial_data')]), _answer(),
        search_call(), AIMessage(content='', tool_calls=[_tool_call('get_financial_data', {'inn': SECOND})]), _answer())
    runtime = _runtime(model, name_resolution=True, grounding_debug=False)
    async def execute(name, args, context):
        calls.append(args['inn'])
        result = targeted_result()
        result.data['company']['inn'] = args['inn']
        return result
    monkeypatch.setattr(runtime.registry, 'execute', execute)
    first = await runtime.run(f'Какая выручка у {INN}?')
    searches['found'] = {'rows': [row(SECOND)], 'total': 1, 'exact_total': 1}
    second = await runtime.run('Какая выручка у Электролид?', first.conversation_id)
    assert calls == [INN, SECOND]
    assert second.active_company.inn == SECOND


@pytest.mark.asyncio
@pytest.mark.parametrize('decision', ['needs_company', 'multiple'])
async def test_missing_other_company_and_multiple_names_need_input(decision):
    response = await _runtime(_model(AIMessage(content=json.dumps({'resolution': decision}))),
        name_resolution=True).run('Проверь другую компанию')
    assert response.metadata.status == 'needs_input' and response.metadata.tool_calls == 0


@pytest.mark.asyncio
async def test_no_matches_distinct_from_source_failure(searches, monkeypatch):
    searches['found'] = {'rows': [], 'total': 0, 'exact_total': 0}
    missing = await _runtime(_model(search_call()), name_resolution=True).run('Проверь Электролид')
    assert missing.metadata.error_code == 'company_name_not_found'
    async def unavailable(**kwargs): raise CompanySourceError()
    monkeypatch.setattr('app.infrastructure.repository.search_companies', unavailable)
    failed = await _runtime(_model(search_call()), name_resolution=True).run('Проверь Электролид')
    assert failed.metadata.error_code == 'source_unavailable'
    assert failed.metadata.status == 'error'


@pytest.mark.asyncio
async def test_model_failure_and_invented_query_never_search(searches):
    for model in [FailingToolCallingModel(responses=[_answer()]), _model(search_call('Выдуманная'))]:
        response = await _runtime(model, name_resolution=True).run('Проверь Электролид')
        assert response.metadata.error_code == 'name_resolution_unavailable'
    assert searches['calls'] == []


def test_public_search_and_query_validation(api_client, searches):
    response = api_client.get('/api/v1/companies/search', params={'q': 'Электролид'})
    assert response.status_code == 200
    assert response.json()['rows'][0]['inn'] == INN
    for params in [{'q': 'а'}, {'q': '  '}, {'q': 'Электролид', 'limit': 6}]:
        assert api_client.get('/api/v1/companies/search', params=params).status_code == 422


def test_result_rejects_inconsistent_identity_and_counts():
    for found in [{'rows': [row(), row()], 'total': 2, 'exact_total': 2},
                  {'rows': [row(match='partial')], 'total': 2, 'exact_total': 1}]:
        with pytest.raises(ValueError): CompanySearchResult.model_validate(found)


@pytest.mark.asyncio
async def test_pending_explanation_preserves_choice_without_old_company_data(searches):
    searches['found'] = {'rows': [row(match='partial')], 'total': 1, 'exact_total': 0}
    runtime = _runtime(_model(search_call()), name_resolution=True)
    first = await runtime.run('Проверь Электролид')
    explanation = await runtime.run('Объясни проще', first.conversation_id)
    assert explanation.metadata.tool_calls == explanation.metadata.model_calls == 0
    assert explanation.blocks[0].search_id == first.blocks[0].search_id
    assert len(searches['calls']) == 1


@pytest.mark.asyncio
async def test_cancelled_choice_is_not_reused(searches):
    searches['found'] = {'rows': [row(match='partial')], 'total': 1, 'exact_total': 0}
    runtime = _runtime(_model(search_call(), AIMessage(content='{"resolution":"continue"}'), _answer()),
                       name_resolution=True, grounding_debug=False)
    first = await runtime.run('Проверь Электролид')
    await runtime.run('Привет', first.conversation_id)
    stale = await runtime.run('вторая', first.conversation_id)
    assert stale.metadata.error_code == 'stale_company_choice'
    assert stale.metadata.tool_calls == 0


@pytest.mark.asyncio
async def test_contextual_question_with_resolution_does_not_read_again(searches, monkeypatch):
    model = _model(AIMessage(content='', tool_calls=[_tool_call('get_financial_data')]), _answer(),
        AIMessage(content='{"resolution":"continue"}'), _answer('Прибыль объясняется по уже полученным данным.'))
    runtime = _runtime(model, name_resolution=True, grounding_debug=False)
    calls = []
    async def execute(name, args, context):
        calls.append(name); return targeted_result()
    monkeypatch.setattr(runtime.registry, 'execute', execute)
    first = await runtime.run(f'Какая выручка у {INN}?')
    response = await runtime.run('Не запускай полную проверку, объясни значение прибыли', first.conversation_id)
    assert response.metadata.tool_calls == 0 and response.metadata.model_calls == 2
    assert calls == ['get_financial_data'] and searches['calls'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('message', [
    'Но суммы похожи. Значит да?',
    'Тогда сколько мы точно знаем?',
    'Связь означает общий риск?',
])
async def test_contextual_continuations_skip_name_resolution(message, searches, monkeypatch):
    model = _model(AIMessage(content='', tool_calls=[_tool_call('get_legal_data')]), _answer(),
                   _answer('Ответ из проверенного контекста.'))
    runtime = _runtime(model, name_resolution=True, grounding_debug=False)
    async def execute(name, args, context): return targeted_result(domain='legal')
    monkeypatch.setattr(runtime.registry, 'execute', execute)
    first = await runtime.run(f'Какие суды у {INN}?')
    follow = await runtime.run(message, first.conversation_id)
    assert follow.metadata.tool_calls == 0
    assert len(searches['calls']) == 0
    assert follow.message == 'Ответ из проверенного контекста.'


@pytest.mark.asyncio
async def test_capability_question_skips_name_resolution(searches):
    response = await _runtime(_model(_answer('Могу подобрать поставщиков.')),
                              name_resolution=True, grounding_debug=False).run(
        'Объясни, как ты можешь помочь выбрать поставщика.'
    )
    assert response.metadata.error_code is None
    assert response.message == 'Могу подобрать поставщиков.'
    assert searches['calls'] == []


@pytest.mark.parametrize('endpoint', ['/api/v1/chat/messages', '/api/v1/chat/messages/stream'])
def test_http_company_choice_resumes_original_question(endpoint, api_client, searches, monkeypatch):
    searches['found'] = {'rows': [row(match='partial')], 'total': 1, 'exact_total': 0}
    runtime = _runtime(_model(search_call(), AIMessage(content='', tool_calls=[_tool_call('get_financial_data')]), _answer()),
                       name_resolution=True, grounding_debug=False)
    async def execute(name, args, context):
        assert name == 'get_financial_data' and args['inn'] == INN
        return targeted_result()
    monkeypatch.setattr(runtime.registry, 'execute', execute)
    monkeypatch.setattr('app.api.routes.chat.build_master_runtime', lambda *args, **kwargs: runtime)
    first = api_client.post('/api/v1/chat/messages', json={'message':'Какая выручка у Электролид?'}).json()
    selection = {'search_id': first['blocks'][0]['search_id'], 'inn': INN}
    response = api_client.post(endpoint, json={'message': 'Выбираю компанию',
        'conversation_id': first['conversation_id'], 'company_selection': selection})
    assert response.status_code == 200
    result = next(json.loads(line)['payload'] for line in response.text.splitlines()
        if json.loads(line)['type'] == 'result') if endpoint.endswith('/stream') else response.json()
    assert result['active_company']['inn'] == INN and result['metadata']['tool_calls'] == 1
    stale = api_client.post('/api/v1/chat/messages', json={'message': 'Выбираю компанию',
        'conversation_id': first['conversation_id'], 'company_selection': selection}).json()
    assert stale['metadata']['error_code'] == 'stale_company_choice'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['database', 'timeout', 'invalid'])
async def test_repository_search_maps_source_errors(failure, monkeypatch):
    from app.infrastructure import repository
    from app.mcp_data.errors import SourceTimeout, InvalidSourceResponse
    from psycopg import OperationalError
    class Reader:
        async def search_companies(self, **kwargs):
            if failure == 'database': raise OperationalError('private database error')
            if failure == 'timeout': raise TimeoutError()
            return {'rows': [], 'total': 1, 'exact_total': 0}
    monkeypatch.setattr(repository, 'get_company_reader', lambda: Reader())
    expected = {'database': CompanySourceError, 'timeout': SourceTimeout, 'invalid': InvalidSourceResponse}[failure]
    with pytest.raises(expected): await repository.search_companies('Электролид')

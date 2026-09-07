import time

import pytest

from app.agent.models import FindCompaniesArgs
from app.agent.ranking import selection_turn
from app.agent.shortlist import execute_find_companies
from app.agent.response import tool_result_to_assistant
from app.agent.synthesis import normalized_tool_context
from app.agent.tools import ToolContext
from app.llm.groq_client import GroqClient
from test_agent_runtime import _runtime, _model, _answer, _settings, FailingToolCallingModel
from test_shortlist import found, _row


@pytest.mark.parametrize('phrase,metric,order', [
    ('по выручке', 'proceeds', 'desc'), ('по прибыли', 'profit', 'desc'),
    ('по сумме исков', 'claims', 'asc'),
    ('по количеству исполнительных производств', 'enforcement', 'asc'),
    ('с минимальной прибылью', 'profit', 'asc'),
    ('с максимальной суммой исков', 'claims', 'desc'),
    ('по прибыли по возрастанию', 'profit', 'asc'),
])
def test_metric_direction(phrase, metric, order):
    turn = selection_turn('Выбери 3 ' + phrase, None, None)
    assert turn.arguments['ranking'] == [{'metric': metric, 'order': order}]
    assert turn.arguments['limit'] == 3


def test_full_search_and_ranking_separate_filter_from_order():
    turn = selection_turn('Найди торговые компании с выручкой от 10 млн и выбери 5 с наибольшей прибылью', None, None)
    assert turn.arguments['activity_query'] == 'торговлей'
    assert turn.arguments['min_proceeds'] == 10_000_000
    assert turn.arguments['ranking'] == [{'metric': 'profit', 'order': 'desc'}]


@pytest.mark.parametrize('message, expected', [
    ('Найди 5 компаний с самой большой выручкой', [{'metric': 'proceeds', 'order': 'desc'}]),
    ('Выбери 3 компании: сначала прибыль по убыванию, затем число ИП по возрастанию',
     [{'metric': 'profit', 'order': 'desc'}, {'metric': 'enforcement', 'order': 'asc'}]),
    ('Найди 3 компании: сначала максимальная прибыль, при равенстве минимальное число ИП',
     [{'metric': 'profit', 'order': 'desc'}, {'metric': 'enforcement', 'order': 'asc'}]),
])
def test_defense_ranking_phrases(message, expected):
    turn = selection_turn(message, None, None)
    assert turn.arguments is not None
    assert turn.arguments['ranking'] == expected


def test_ranking_ignores_explicit_explanation_suffix():
    turn = selection_turn(
        'Найди торговые компании с выручкой от 10 млн, выбери 5 по прибыли и объясни исключение пропусков',
        None, None,
    )
    assert turn.arguments is not None
    assert turn.arguments['activity_query'].startswith('торговл')
    assert turn.arguments['min_proceeds'] == 10_000_000
    assert turn.arguments['ranking'] == [{'metric': 'profit', 'order': 'desc'}]


def test_two_inn_choice_is_not_metric_ranking():
    assert selection_turn(
        'Выбери между 3711039473 и 6165169320 покупателя на отсрочку 60 дней', None, None,
    ) is None


def test_direction_does_not_leak_to_next_metric():
    turn = selection_turn('Выбери 5 сначала по прибыли по возрастанию, затем по выручке', None, None)
    assert turn.arguments['ranking'] == [{'metric': 'profit', 'order': 'asc'}, {'metric': 'proceeds', 'order': 'desc'}]


@pytest.mark.parametrize('message', [
    'Выбери 0 по прибыли', 'Выбери -3 по прибыли', 'Выбери 26 по прибыли', 'Выбери лучших вообще',
    'Выбери 5 по рентабельности', 'Выбери 5 по прибыли без долгов',
    'Из найденных выбери 5 по прибыли', 'Выбери 5 по прибыли и прибыли',
])
def test_ambiguous_requests_do_not_search(message):
    turn = selection_turn(message, None, None)
    assert turn.arguments is None and turn.clarification


def test_ranking_schema():
    assert FindCompaniesArgs(ranking=[{'metric': 'profit', 'order': 'desc'}]).limit == 5
    for ranking in [[{'metric': 'profit; DROP TABLE', 'order': 'desc'}],
                    [{'metric': 'profit', 'order': 'wrong'}],
                    [{'metric': 'profit', 'order': 'desc'}] * 2]:
        with pytest.raises(ValueError):
            FindCompaniesArgs(ranking=ranking)


@pytest.mark.asyncio
async def test_selection_reuses_filters_not_visible_rows(found):
    model = _model(_answer('Найдены компании.'), _answer('Выбраны компании.'), _answer('Объяснение выбора.'))
    runtime = _runtime(model, direct_dispatch=True, grounding_debug=False)
    first = await runtime.run('Найди компании, занимающиеся торговлей, с выручкой от 10 млн, покажи первые 5')
    second = await runtime.run('Из найденных выбери 3 с минимальной суммой исков', first.conversation_id)
    assert found['min_proceeds'] == 10_000_000 and found['activity_query'] == 'торговлей'
    assert found['ranking'] == [{'metric': 'claims', 'order': 'asc'}]
    assert found['limit'] == 3
    assert second.metadata.tool_calls == second.metadata.model_calls == 1
    assert second.blocks[0].ranking[0].metric == 'claims'
    follow = await runtime.run('Почему эти?', first.conversation_id)
    assert follow.metadata.tool_calls == 0 and follow.blocks == []


@pytest.mark.asyncio
async def test_pending_confirmation_and_revision(found):
    runtime = _runtime(_model(_answer('Найдены.'), _answer('Выбраны.')), direct_dispatch=True, grounding_debug=False)
    first = await runtime.run('Найди компании с выручкой от 10 млн')
    question = await runtime.run('Выбери 5 по прибыли и количеству исполнительных производств', first.conversation_id)
    assert question.metadata.tool_calls == question.metadata.model_calls == 0
    assert 'по убыванию' in question.message and 'по возрастанию' in question.message
    revised = await runtime.run('По сумме исков и прибыли', first.conversation_id)
    assert revised.metadata.tool_calls == 0 and 'сумма исков' in revised.message
    result = await runtime.run('Да', first.conversation_id)
    assert result.metadata.tool_calls == 1
    assert found['min_proceeds'] == 10_000_000 and found['limit'] == 5
    assert found['ranking'] == [{'metric': 'claims', 'order': 'asc'}, {'metric': 'profit', 'order': 'desc'}]


@pytest.mark.asyncio
async def test_new_search_clears_pending(found):
    runtime = _runtime(_model(_answer('Найдены.'), _answer('Новые.'), _answer('Уточните запрос.')), direct_dispatch=True, grounding_debug=False)
    first = await runtime.run('Найди компании с выручкой от 10 млн')
    await runtime.run('Выбери 5 по прибыли и сумме исков', first.conversation_id)
    await runtime.run('Найди компании с выручкой от 20 млн', first.conversation_id)
    result = await runtime.run('Да', first.conversation_id)
    assert result.metadata.tool_calls == 0
    assert found['min_proceeds'] == 20_000_000 and found['ranking'] == []


@pytest.mark.asyncio
async def test_failure_still_renders_backend_ranking(found):
    model = FailingToolCallingModel(responses=[_answer()])
    result = await _runtime(model, grounding_debug=False).run('Выбери 5 по прибыли')
    assert result.metadata.tool_calls == 1
    assert result.blocks[0].ranking[0].metric == 'profit'
    assert 'Выбрано' in result.message


@pytest.mark.asyncio
async def test_counts_years_and_dates_reach_artifact(monkeypatch):
    async def find(**kwargs):
        return {'total': 8, 'eligible_total': 2, 'rows': [
            {**_row('6165169320', 'Первая', profit=-1, year=2024), 'report_date': '2025-04-01'},
            {**_row('7728380537', 'Вторая', profit=-5, year=2023), 'report_date': '2024-04-01'},
        ]}
    monkeypatch.setattr('app.infrastructure.repository.find_companies', find)
    settings = _settings()
    result = await execute_find_companies(ToolContext(settings=settings, client=GroqClient(settings), persist=False),
                                        FindCompaniesArgs(ranking=[{'metric': 'profit', 'order': 'desc'}], limit=5))
    context = normalized_tool_context(result)
    response = tool_result_to_assistant(result, trusted_context=context, master_answer=None,
        agent_run_id='ranking', routing='deterministic_fallback', model=None, started=time.perf_counter())
    block = response.blocks[0]
    assert block.total == 8 and block.eligible_total == 2
    assert block.rows[0].report_date == '2025-04-01'
    assert any('неполных показателей 6' in note for note in block.notes)
    assert any('годы' in note and 'различаются' in note for note in block.notes)
    assert 'прибыль' in response.message


@pytest.mark.asyncio
async def test_native_model_cannot_bypass_ranking_confirmation(found):
    from langchain_core.messages import AIMessage
    from test_agent_runtime import _tool_call
    model = _model(AIMessage(content='', tool_calls=[_tool_call('find_companies', {
        'min_proceeds': 10000000, 'ranking': [
            {'metric': 'profit', 'order': 'desc'}, {'metric': 'claims', 'order': 'asc'}],
    })]), _answer('Подборка.'))
    response = await _runtime(model, grounding_debug=False).run('Найди компании с выручкой от 10 млн')
    assert found == {} and response.metadata.tool_calls == 0
    assert response.blocks == []


def test_pending_count_change_keeps_filters_and_order():
    proposal = selection_turn('Выбери 5 по прибыли и сумме исков', {'filters': {'min_proceeds': 10000000}}, None)
    revised = selection_turn('Выбери 3', None, proposal.pending)
    assert revised.arguments is None
    confirmed = selection_turn('Да', None, revised.pending)
    assert confirmed.arguments['limit'] == 3
    assert confirmed.arguments['min_proceeds'] == 10000000
    assert len(confirmed.arguments['ranking']) == 2

"""Подборка по критериям: границы аргументов, гидратация и маршрутизация."""
import pytest

from app.agent.models import FindCompaniesArgs
from app.agent.runtime import is_shortlist_request, requested_tool
from app.agent.shortlist import describe, direct_shortlist_arguments, execute_find_companies, money
from app.agent.targeted_models import ShortlistData
from app.agent.tools import ToolContext
from app.llm.groq_client import GroqClient
from test_agent_runtime import _settings


def _row(inn, name, proceeds=None, profit=None, claims=None, stops=0, exec_count=0,
         risk="LOW", zsk="GREEN", year=2024):
    return {
        "inn": inn, "short_name": name, "fin_year": year, "proceeds": proceeds,
        "profit": profit, "claims_amount": claims, "hard_stops": stops,
        "enforcement_count": exec_count, "risk_level": risk, "zsk_risk_level": zsk,
    }


@pytest.fixture
def found(monkeypatch):
    captured = {}

    async def find_companies(**kwargs):
        captured.update(kwargs)
        return {"total": 51, "rows": [
            _row("5032257375", 'ООО "МАКСМАРКЕТ"', 116257852000, None, 2611475741, 4, 507),
            _row("7728380537", 'ООО "ЭЛЕКТРОЛИД"', 4749348000, 91019123, 0, 0, 178, zsk="YELLOW"),
        ]}

    monkeypatch.setattr("app.infrastructure.repository.find_companies", find_companies)
    return captured


async def _run(**kwargs):
    settings = _settings()
    context = ToolContext(settings=settings, client=GroqClient(settings), persist=False)
    return await execute_find_companies(context, FindCompaniesArgs(**kwargs))


@pytest.mark.asyncio
async def test_shortlist_reports_total_and_shown_rows(found):
    result = await _run(min_proceeds=10_000_000)
    data = ShortlistData.model_validate(result.data)

    assert result.metadata.tool == "find_companies"
    assert data.total == 51
    assert [item.inn for item in data.companies] == ["5032257375", "7728380537"]
    # Пользователь должен видеть, что показана только часть подборки.
    assert any("51" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_criteria_are_described_by_the_backend(found):
    result = await _run(min_proceeds=10_000_000, hard_stops="without", zsk_risk_level="GREEN")
    data = ShortlistData.model_validate(result.data)

    assert data.criteria == [
        "выручка от 10.0 млн ₽", "светофор ЗСК GREEN", "без жёстких стоп-факторов",
    ]


@pytest.mark.asyncio
async def test_shortlist_never_invents_missing_numbers(found):
    data = ShortlistData.model_validate((await _run(min_proceeds=1)).data)

    # У МАКСМАРКЕТа прибыль не раскрыта: она остаётся None, а не нулём.
    assert data.companies[0].profit is None


@pytest.mark.asyncio
async def test_arguments_reach_the_repository_unchanged(found):
    await _run(min_proceeds=5_000_000, sort_by="enforcement", order="asc", limit=3)

    assert found["min_proceeds"] == 5_000_000
    assert found["sort_by"] == "enforcement"
    assert found["order"] == "asc"
    assert found["limit"] == 3


def test_at_least_one_criterion_is_required():
    with pytest.raises(ValueError):
        FindCompaniesArgs()


def test_limit_stays_bounded():
    with pytest.raises(ValueError):
        FindCompaniesArgs(min_proceeds=1, limit=100)


@pytest.mark.parametrize("message,expected", [
    ("Сравни всех контрагентов, у которых выручка больше 10 млн", True),
    ("Найди компании без стоп-факторов", True),
    ("Покажи всех, у кого больше 100 исполнительных производств", True),
    # Конкретные ИНН — это сравнение, а не подборка.
    ("Сравни 6165169320 и 2311304742", False),
    ("Проверь контрагента 6165169320", False),
    ("А что с финансами?", False),
])
def test_shortlist_intent_is_separated_from_company_requests(message, expected):
    assert is_shortlist_request(message) is expected
    assert (requested_tool(message) == "find_companies") is expected


def test_money_keeps_missing_values_visible():
    assert money(None) == "Нет данных"
    assert money(0) == "0 ₽"
    assert "млрд" in money(116_257_852_000)


def test_describe_returns_nothing_without_criteria():
    assert describe(FindCompaniesArgs(min_proceeds=0)) == ["выручка от 0 ₽"]


@pytest.mark.parametrize("message, expected", [
    ("Найди компании с ОКВЭД 46 по основной деятельности.",
     {"okved_prefix": "46", "activity_scope": "main"}),
    ("Найди компании с отрицательной прибылью.", {"max_profit": -0.000001}),
    ("Найди компании с банковским риском LOW.", {"risk_level": "LOW"}),
    ("Найди компании со светофором ЗСК GREEN.", {"zsk_risk_level": "GREEN"}),
    ("Найди компании с прибылью от 0 до 1000000 рублей.",
     {"min_profit": 0, "max_profit": 1_000_000}),
    ("Найди компании с выручкой от 1 млрд и не более двух ИП.",
     {"min_proceeds": 1_000_000_000, "max_enforcement_count": 2}),
    ("Найди компании с ОКВЭД 46, прибылью от 0 и банковским LOW; покажи число совпадений и 5 строк.",
     {"okved_prefix": "46", "min_profit": 0, "risk_level": "LOW", "limit": 5}),
])
def test_explicit_supported_filters_are_backend_parsed(message, expected):
    arguments = direct_shortlist_arguments(message)
    assert arguments is not None
    for key, value in expected.items():
        assert arguments[key] == value


@pytest.mark.parametrize('args', [
    {'min_proceeds': 20, 'max_proceeds': 10},
    {'min_profit': float('nan')}, {'max_claims_amount': float('inf')},
])
def test_invalid_numeric_criteria_are_rejected(args):
    with pytest.raises(ValueError):
        FindCompaniesArgs(**args)


@pytest.mark.parametrize('message, expected', [
    ('Покажи компании с выручкой от 10000000000', True),
    ('Покажи иски больше 10 млн', False),
    ('Найди компании с выручкой от 10 млн', True),
])
def test_shortlist_does_not_steal_targeted_questions_or_money(message, expected):
    assert is_shortlist_request(message) is expected


@pytest.mark.asyncio
async def test_missing_legal_data_stays_missing_in_ui(monkeypatch):
    from app.agent.response import tool_result_to_assistant
    from app.agent.synthesis import normalized_tool_context
    import time
    async def find_companies(**kwargs):
        return {'total': 1, 'rows': [_row('6165169320', 'Компания', stops=None, exec_count=None)]}
    monkeypatch.setattr('app.infrastructure.repository.find_companies', find_companies)
    result = await _run(min_proceeds=0)
    response = tool_result_to_assistant(result, trusted_context=normalized_tool_context(result),
        master_answer=None, agent_run_id='test', routing='model', model=None, started=time.perf_counter())
    row = response.blocks[0].rows[0]
    assert row.enforcement_count is None and row.hard_stops is None
    assert row.claims_display == 'Нет данных'
    assert all('судебным' not in a.prompt for a in response.suggested_actions)


@pytest.mark.asyncio
@pytest.mark.parametrize('with_active', [False, True])
async def test_shortlist_followup_uses_its_trusted_context(found, monkeypatch, check_payload, with_active):
    from langchain_core.messages import AIMessage
    from test_agent_runtime import _model, _runtime, _answer, _tool_call, _verified_context
    prefix = [AIMessage(content='', tool_calls=[_tool_call()]), _answer()] if with_active else []
    model = _model(*prefix,
        AIMessage(content='', tool_calls=[_tool_call('find_companies', {'min_proceeds': 10_000_000})]),
        _answer('Подборка готова.'), _answer('Объясняю подборку.'))
    runtime = _runtime(model, grounding_debug=False)
    async def check(*args, **kwargs): return check_payload
    monkeypatch.setattr('app.agent.tools.run_check', check)
    cid = (await runtime.run('Проверь контрагента 6165169320')).conversation_id if with_active else None
    shortlist = await runtime.run('Найди компании с выручкой от 10 млн', cid)
    assert shortlist.metadata.tool_calls == 1 and shortlist.blocks[0].type == 'company_shortlist'
    follow = await runtime.run('Объясни проще', shortlist.conversation_id)
    assert follow.metadata.tool_calls == 0 and follow.metadata.model_calls == 1
    assert follow.blocks == [] and follow.leading_artifact is None
    assert _verified_context(model._messages[-1])['domain'] == 'shortlist'
    assert bool(follow.active_company) is with_active


@pytest.mark.asyncio
@pytest.mark.parametrize('second_valid', [True, False])
async def test_shortlist_repairs_invalid_tool_arguments_once_before_sql(found, second_valid):
    from langchain_core.messages import AIMessage, ToolMessage
    from test_agent_runtime import _model, _runtime, _answer, _tool_call
    invalid = {'min_proceeds ': 10000000, 'limit': 5}
    model = _model(
        AIMessage(content='', tool_calls=[_tool_call('find_companies', invalid)]),
        AIMessage(content='', tool_calls=[_tool_call('find_companies',
            {'min_proceeds': 10000000, 'limit': 5} if second_valid else invalid, call_id='corrected')]),
        _answer('Подборка готова.'),
    )
    response = await _runtime(model, grounding_debug=False).run('Найди компании с выручкой от 10 млн')
    assert any(isinstance(m, ToolMessage) and 'поиск не выполнялся' in m.content for m in model._messages[1])
    if second_valid:
        assert response.metadata.tool_calls == 1 and response.metadata.model_calls == 3
        assert found['min_proceeds'] == 10000000
    else:
        assert response.metadata.tool_calls == 0 and response.metadata.model_calls == 2
        assert found == {} and response.blocks == []


@pytest.mark.parametrize('message, expected', [
    ('Найди компании с выручкой от 10 млн рублей, покажи первые 5', {'min_proceeds': 10000000, 'limit': 5}),
    ('Подбери контрагентов с прибылью до 1,5 млн', {'max_profit': 1500000}),
    ('Найди компании без стоп-факторов', {'hard_stops': 'without'}),
    ('Найди компании с выручкой от 10 млн и без долгов', None),
    ('Найди компании с выручкой от 10 млн, покажи первые 100', None),
    ('Найди компании с выручкой от 10 млн за 2023 год', None),
])
def test_direct_shortlist_accepts_only_complete_unambiguous_commands(message, expected):
    from app.agent.shortlist import direct_shortlist_arguments
    actual = direct_shortlist_arguments(message)
    if expected is None:
        assert actual is None
    else:
        assert all(actual[key] == value for key, value in expected.items())


@pytest.mark.asyncio
async def test_direct_shortlist_needs_only_synthesis_model_call(found):
    from test_agent_runtime import _model, _runtime, _answer
    response = await _runtime(_model(_answer('Подборка готова.')),
        direct_dispatch=True, grounding_debug=False).run(
        'Найди компании с выручкой от 10 млн рублей, покажи первые 5')
    assert response.metadata.model_calls == response.metadata.tool_calls == 1
    assert response.metadata.synthesis == 'model'
    assert found['min_proceeds'] == 10000000 and found['limit'] == 5
    assert response.blocks[0].type == 'company_shortlist'


@pytest.mark.parametrize('message, expected', [
    ('найди компании занимающиеся торговлей и с выручкой от 10 млн',
     {'activity_query': 'торговлей', 'activity_scope': 'any', 'min_proceeds': 10000000}),
    ('Найди компании, занимающиеся торговлей, с выручкой от 10 млн, покажи первые 5',
     {'activity_query': 'торговлей', 'activity_scope': 'any', 'min_proceeds': 10000000, 'limit': 5}),
    ('Найди компании занимающиеся торговлей, и с выручкой от 10 млн, с прибылью от 1 млн',
     {'activity_query': 'торговлей', 'min_proceeds': 10000000, 'min_profit': 1000000}),
    ('Найди компании по деятельности торговля фруктами, овощами и орехами, с выручкой от 10 млн',
     {'activity_query': 'торговля фруктами, овощами и орехами', 'min_proceeds': 10000000}),
    ('Найди компании, занимающиеся оптовой торговлей фруктами и с выручкой от 10 млн, покажи первые 5',
     {'activity_query': 'оптовой торговлей фруктами', 'activity_scope': 'any', 'limit': 5}),
    ('Покажи компании по деятельности производство одежды', {'activity_query': 'производство одежды'}),
    ('Найди компании с ОКВЭД 46.7 и с выручкой от 10 млн', {'okved_prefix': '46.7'}),
    ('Найди компании занимающиеся торговлей только основной ОКВЭД', {'activity_scope': 'main'}),
])
def test_activity_and_finance_are_combined_without_model(message, expected):
    from app.agent.shortlist import direct_shortlist_arguments
    args = direct_shortlist_arguments(message)
    assert args is not None
    assert all(args[key] == value for key, value in expected.items())
    assert requested_tool(message) == 'find_companies'


@pytest.mark.parametrize('args', [
    {'activity_query':'  '}, {'activity_query':'не торговля'},
    {'okved_prefix':'46 OR 1=1'}, {'okved_prefix':'4'},
])
def test_activity_arguments_do_not_accept_empty_or_ambiguous_filter(args):
    with pytest.raises(ValueError):
        FindCompaniesArgs(**args)


@pytest.mark.asyncio
async def test_activity_reaches_repository_and_context(found):
    result = await _run(activity_query='торговля', min_proceeds=10000000)
    assert found['activity_query'] == 'торговля' and found['activity_scope'] == 'any'
    assert found['min_proceeds'] == 10000000
    assert 'дополнительный' in result.data['criteria'][0]


@pytest.mark.asyncio
async def test_native_router_cannot_drop_explicit_activity(found):
    from langchain_core.messages import AIMessage
    from test_agent_runtime import _model, _runtime, _answer, _tool_call
    model = _model(AIMessage(content='', tool_calls=[_tool_call('find_companies',
        {'min_proceeds': 10000000, 'activity_scope': 'main', 'okved_prefix': '47'})]), _answer('Подборка готова.'))
    response = await _runtime(model, direct_dispatch=False, grounding_debug=False).run(
        'Найди компании занимающиеся торговлей и с выручкой от 10 млн')
    assert response.metadata.tool_calls == 1
    assert found['activity_query'] == 'торговлей'
    assert found['activity_scope'] == 'any' and found['okved_prefix'] is None
    assert found['min_proceeds'] == 10000000

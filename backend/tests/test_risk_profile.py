import pytest
from pydantic import ValidationError
from app.agent.models import MasterAnswer, RiskAxis, RiskProfile, FullCompanyCheckData
from app.agent.response import _risk_profile
from app.agent.tools import _compact_check
from app.api.schemas import CheckResponse
from app.agent.synthesis import parse_master_answer


def proposal(level='low'):
    return MasterAnswer(message='Обзор', risk_profile=RiskProfile(**{
        key: RiskAxis(level=level, reason='В доступном срезе нет существенных неблагоприятных признаков.')
        for key in ('finance', 'courts', 'enforcement', 'regulatory')}))


@pytest.mark.parametrize('level', ['low', 'medium', 'high'])
def test_model_colors_axes_but_cannot_soften_source_hard_stop(check_payload, level):
    data, _ = _compact_check(CheckResponse.model_validate(check_payload))
    profile = _risk_profile(data, proposal(level))
    assert profile.finance.level == level
    assert profile.regulatory.level == 'high'
    assert data.company.risk_level == check_payload['company']['risk_level']


def test_no_data_stays_unknown_even_if_model_proposes_green(check_payload):
    data, _ = _compact_check(CheckResponse.model_validate(check_payload))
    data = data.model_copy(update={'facts': {}, 'policy_signals': [], 'sections': {}})
    assert all(axis['level'] == 'unknown' for axis in _risk_profile(data, proposal()).model_dump().values())


def test_provider_failure_does_not_invent_profile(check_payload):
    data, _ = _compact_check(CheckResponse.model_validate(check_payload))
    assert _risk_profile(data, None).finance.level == 'unknown'


def test_profile_has_no_numbers_or_arbitrary_axis():
    with pytest.raises(ValidationError):
        RiskAxis(level='green', reason='ok')
    with pytest.raises(ValidationError):
        RiskAxis(level='low', reason='ok', score=10)
    assert MasterAnswer(message='Старый ответ без профиля').risk_profile is None


def test_ignoring_unused_profile_preserves_other_contract_checks():
    value = {'message': 'Обзор', 'artifact': 'none', 'risk_profile': {'finance': {}}}
    parsed = parse_master_answer(value, allowed_artifacts=('none',), allow_risk_profile=False)
    assert parsed.risk_profile is None
    assert value['risk_profile'] == {'finance': {}}  # Не изменяем исходный ответ.
    with pytest.raises(ValidationError):
        parse_master_answer(value, allowed_artifacts=('none',), allow_risk_profile=True)
    with pytest.raises(ValidationError):
        parse_master_answer({**value, 'message': ''}, allowed_artifacts=('none',), allow_risk_profile=False)
    with pytest.raises(ValueError, match='Artifact is unavailable'):
        parse_master_answer({**value, 'artifact': 'chart'}, allowed_artifacts=('none',), allow_risk_profile=False)


@pytest.mark.asyncio
async def test_comparison_debug_repair_ignores_unused_broken_profile():
    import json
    from langchain_core.messages import AIMessage
    from app.agent.grounding import call_master_repair
    from test_agent_runtime import _model
    model = _model(AIMessage(content=json.dumps({
        'message': 'Исправленное пояснение', 'artifact': 'none', 'risk_profile': {'finance': {}},
    })))
    repaired, _ = await call_master_repair(
        model, MasterAnswer(message='Пояснение'), ['Нужно уточнение'], {'domain': 'comparison'},
        allowed_artifacts=('none',), timeout_s=1,
    )
    assert repaired.message == 'Исправленное пояснение'
    assert repaired.risk_profile is None


@pytest.mark.asyncio
@pytest.mark.parametrize('direct', [True, False])
async def test_full_check_keeps_ai_profile_and_requests_it_only_after_tool(monkeypatch, check_payload, direct):
    import json
    from langchain_core.messages import AIMessage
    from test_agent_runtime import _model, _runtime, _tool_call, _settings
    async def full(*args, **kwargs):
        return check_payload
    monkeypatch.setattr('app.agent.tools.run_check', full)
    answers = [AIMessage(content=proposal('medium').model_dump_json())]
    if not direct:
        answers.insert(0, AIMessage(content='', tool_calls=[_tool_call()]))
    model = _model(*answers)
    result = await _runtime(model, settings=_settings(web_news_enabled=False),
                            direct_dispatch=direct, grounding_debug=False).run('Проверь контрагента 6165169320')
    assert result.metadata.synthesis == 'model'
    assert result.leading_artifact.risk_profile.finance.level == 'medium'
    assert result.leading_artifact.risk_profile.regulatory.level == 'high'
    system = model._messages[-1][0].content
    schema = json.loads(system.split('Схема финального JSON: ')[1].split('\n')[0])
    assert 'risk_profile' in schema['required']

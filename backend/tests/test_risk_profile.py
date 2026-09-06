import pytest
from pydantic import ValidationError
from app.agent.models import MasterAnswer, RiskAxis, RiskProfile, FullCompanyCheckData
from app.agent.response import _risk_profile
from app.agent.tools import _compact_check
from app.api.schemas import CheckResponse


def proposal(level='low'):
    return MasterAnswer(message='Обзор', risk_profile=RiskProfile(**{
        key: RiskAxis(level=level, reason='В доступном срезе нет существенных неблагоприятных признаков.')
        for key in ('finance', 'courts', 'enforcement', 'regulatory')}))


def test_model_can_show_green_but_cannot_soften_source_hard_stop(check_payload):
    data, _ = _compact_check(CheckResponse.model_validate(check_payload))
    profile = _risk_profile(data, proposal())
    assert profile.finance.level == 'low'
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

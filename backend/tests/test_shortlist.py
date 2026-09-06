"""Подборка по критериям: границы аргументов, гидратация и маршрутизация."""
import pytest

from app.agent.models import FindCompaniesArgs
from app.agent.runtime import is_shortlist_request, requested_tool
from app.agent.shortlist import describe, execute_find_companies, money
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

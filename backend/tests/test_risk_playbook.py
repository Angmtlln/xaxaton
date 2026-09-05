"""The playbook belongs exactly once to existing answer calls, never routing."""
import runpy
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from app.agent.prompt import PLAYBOOK_PATH, MASTER_SYNTHESIS_INSTRUCTIONS
from test_agent_runtime import _runtime, _model, _answer, _tool_call
from test_data_coverage import _real_snapshot, _patch_snapshots, INN


@pytest.mark.asyncio
async def test_playbook_once_in_synthesis_followup_and_comparison(documents, monkeypatch):
    snapshots = {d['report']['baseInfo']['inn']: _real_snapshot(d) for d in documents}
    _patch_snapshots(monkeypatch, snapshots)
    model = _model(
        AIMessage(content='', tool_calls=[_tool_call('get_financial_data')]),
        _answer('Баланс содержит неполные сведения.'),
        _answer('Объясню значение доступного баланса.'),
        AIMessage(content='', tool_calls=[_tool_call('compare_companies',{'inns':[INN,'1684017097'],'focus':'finance'})]),
        _answer('У компаний различается полнота отчётности.'),
    )
    runtime = _runtime(model, grounding_debug=False)
    first = await runtime.run('Финансы ' + INN)
    second = await runtime.run('Почему?', first.conversation_id)
    third = await runtime.run('Сравни ' + INN + ' и 1684017097', first.conversation_id)
    assert (first.metadata.model_calls, second.metadata.model_calls, third.metadata.model_calls) == (2,1,2)
    assert second.metadata.tool_calls == 0
    for messages in model._messages:
        system = messages[0].content
        expected = int('verified_context (проверенные' in system)
        assert system.count(MASTER_SYNTHESIS_INSTRUCTIONS) == expected
        assert system.count('# RISK_PLAYBOOK — ALEPH') == expected
        assert 'CODEX_DATA_AUDIT.md' not in system
        assert 'DATA_COVERAGE_PLAN.md' not in system
        assert 'RISK_PLAYBOOK_NOTES.md' not in system
        assert 'official_hard_stop нельзя смягчать' not in system
    assert all(r.metadata.repair_attempts == 0 for r in (first,second,third))


def test_missing_playbook_fails_visibly(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'app/agent/prompt.py'
    (tmp_path / 'prompt.py').write_text(source.read_text())
    with pytest.raises(FileNotFoundError):
        runpy.run_path(str(tmp_path / 'prompt.py'))


def test_playbook_is_inside_existing_docker_copy_root():
    backend = Path(__file__).resolve().parents[1]
    assert PLAYBOOK_PATH == backend / 'app/agent/RISK_PLAYBOOK.md'
    assert 'COPY backend/ /srv/' in (backend/'Dockerfile').read_text()


@pytest.mark.asyncio
async def test_deal_role_answer_receives_both_verified_domains(documents, monkeypatch):
    snapshots = {d['report']['baseInfo']['inn']: _real_snapshot(d) for d in documents}
    _patch_snapshots(monkeypatch, snapshots)
    model = _model(AIMessage(content='',tool_calls=[_tool_call('get_financial_data')]),
                   _answer('Финансы получены.'),_answer('Судебные сведения получены.'),
                   _answer('Для аванса учту оба проверенных домена.'))
    runtime = _runtime(model, grounding_debug=False)
    first = await runtime.run('Финансы '+INN)
    await runtime.run('А что с судами?',first.conversation_id)
    result = await runtime.run('Теперь я покупаю с авансом',first.conversation_id)
    from test_agent_runtime import _verified_context
    context = _verified_context(model._messages[-1])
    assert context['domain'] == 'legal'
    assert context['related_domains']['finance']['series'][0]['value'][-1]['year'] == 2025
    assert context['related_domains']['finance']['company']['inn'] == INN
    assert result.metadata.tool_calls == 0
    assert result.metadata.model_calls == 1


@pytest.mark.asyncio
async def test_comparison_ui_names_actual_common_year(monkeypatch):
    from test_comparison import RICH, OTHER, _snapshot, _fin_row
    from app.agent.tools import ToolContext, build_tool_registry
    from test_agent_runtime import _settings
    from app.llm.groq_client import GroqClient
    from app.agent.response import _comparison_table
    from app.agent.targeted_models import ComparisonData
    sources = {RICH: _snapshot(RICH,'A',fin_rows=[_fin_row(2023,100),_fin_row(2025,150)]),
               OTHER: _snapshot(OTHER,'B',fin_rows=[_fin_row(2023,80)])}
    _patch_snapshots(monkeypatch,sources)
    s = _settings()
    result = await build_tool_registry(s).execute('compare_companies',{'inns':[RICH,OTHER],'focus':'finance'},ToolContext(s,GroqClient(s),False))
    table = _comparison_table(ComparisonData.model_validate(result.data),{e.id:e for e in result.evidence})
    row = next(row for row in table.rows if row.id=='proceeds')
    assert all(cell.display_value.endswith('2023') for cell in row.cells)
    assert 'последний' not in row.label

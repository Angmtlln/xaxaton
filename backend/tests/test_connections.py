"""Dataset identities, one-hop review and degraded data."""
import copy
import pytest
from app.agent.connections import discover_connections, cross_check
from app.agent.models import CompanyConnections

A, B = '7805327192', '4720028039'

def snapshots(documents):
    return [{'document': d, 'inn': d['report']['baseInfo']['inn'], 'short_name': d['report']['baseInfo'].get('shortName')} for d in documents]

def root(rows, inn=A):
    return next(s for s in rows if s['document']['report']['baseInfo']['inn'] == inn)

def test_real_dataset_cross_check(documents):
    rows = snapshots(documents)
    hits = {s['document']['report']['baseInfo']['inn']: discover_connections(s, rows) for s in rows}
    assert {inn for inn, g in hits.items() if g.edges} == {A, B}
    graph = hits[A]
    assert graph.total_companies == 1 and graph.total_edges == 5
    assert [n.inn for n in graph.nodes] == [A, B]
    assert {e.kind for e in graph.edges} == {'shared_founder', 'shared_director', 'shared_related', 'related_company'}
    assert len([e for e in graph.edges if e.kind == 'related_company']) == 1
    assert all(len(e.field_refs) == 2 for e in graph.edges)
    assert graph.external_references == 2

def test_name_match_alone_and_historical_owners_are_not_links(documents):
    rows = copy.deepcopy([root(snapshots(documents)), root(snapshots(documents), B)])
    for s in rows:
        r = s['document']['report']
        r['relatedCompanies'] = []
        r['foundersInfo']['authPerson'] = {}
        for f in r['foundersInfo']['cofounders']:
            f['active'] = False
    assert not discover_connections(rows[0], rows).edges

def test_reverse_reference_and_contact_coincidence(documents):
    rows = copy.deepcopy([root(snapshots(documents)), root(snapshots(documents), B)])
    for s in rows:
        r = s['document']['report']
        r['relatedCompanies'] = []
        r['foundersInfo'] = {}
        r['baseInfo']['email'] = ' TEST@example.org '
    rows[1]['document']['report']['relatedCompanies'] = [{'inn': A}]
    graph = discover_connections(rows[0], rows)
    assert {e.kind for e in graph.edges} == {'email', 'related_company'}

@pytest.mark.asyncio
async def test_review_no_llm_and_correct_company(monkeypatch, documents):
    rows = snapshots(documents)
    calls = []
    async def candidates(): return rows
    async def batch(inns):
        calls.append(inns)
        return [root(rows, i) for i in inns]
    monkeypatch.setattr('app.agent.connections.repository.get_connection_candidates', candidates)
    monkeypatch.setattr('app.agent.connections.repository.get_snapshots_for_connections', batch)
    graph = await cross_check(root(rows))
    assert calls == [[B]]
    neighbour = graph.nodes[1]
    assert neighbour.review_state == 'partial'
    stops = next(f for f in neighbour.observations if f.id == 'flags.hard_stop_codes')
    assert 'fnsBlocking' in {v['code'] for v in stops.value}
    assert neighbour.gaps
    CompanyConnections.model_validate(graph.model_dump())

@pytest.mark.asyncio
async def test_failed_cross_check_is_not_no_connections(monkeypatch, documents):
    async def fail(): raise OSError('offline')
    monkeypatch.setattr('app.agent.connections.repository.get_connection_candidates', fail)
    graph = await cross_check(root(snapshots(documents)))
    assert graph.state == 'unavailable' and 'не подтверждено' in graph.note

@pytest.mark.asyncio
async def test_full_check_graph_and_neighbour_report(monkeypatch, documents):
    from app.agent.runtime import MasterAgentRuntime
    from app.agent.tools import ToolContext, build_tool_registry
    from app.config import Settings
    from app.llm.groq_client import GroqClient
    rows = snapshots(documents)
    async def get(inn): return root(rows, inn)
    async def candidates(): return rows
    async def batch(inns): return [root(rows, inn) for inn in inns]
    monkeypatch.setattr('app.infrastructure.repository.get_latest_snapshot', get)
    monkeypatch.setattr('app.infrastructure.repository.get_connection_candidates', candidates)
    monkeypatch.setattr('app.infrastructure.repository.get_snapshots_for_connections', batch)
    settings = Settings(llm_mock=True)
    runtime = MasterAgentRuntime(model=None, model_name='offline', registry=build_tool_registry(settings), model_timeout_s=5, run_timeout_s=20,
                                 tool_context=ToolContext(settings, GroqClient(settings), False))
    first = await runtime.run('Проверь контрагента ' + A)
    assert first.active_company.inn == A
    assert first.metadata.tool_calls == 1
    assert first.suggested_actions[0].label == 'Построить граф связей'
    assert B in first.message and 'fnsBlocking' not in first.message  # readable source meaning
    graph = await runtime.run('Построй граф связей', first.conversation_id)
    assert graph.metadata.tool_calls == graph.metadata.model_calls == 0
    assert graph.leading_artifact is None and graph.blocks[0].type == 'connection_graph'
    assert graph.blocks[0].graph.total_edges == 5
    assert graph.active_company.inn == A
    second = await runtime.run('Сделай отдельный отчёт по связанной компании', graph.conversation_id)
    assert second.active_company.inn == B and second.metadata.tool_calls == 1
    assert second.leading_artifact.inn == B


def test_many_neighbours_are_bounded_and_not_reported_as_complete(documents):
    rows = copy.deepcopy(snapshots(documents)[:10])
    for row in rows:
        row['document']['report']['baseInfo']['email'] = 'shared@example.org'
    graph = discover_connections(rows[0], rows)
    assert graph.state == 'partial'
    assert graph.total_companies == 9 and len(graph.nodes) == 7
    assert len(graph.edges) <= 30
    assert {e.target for e in graph.edges} <= {n.inn for n in graph.nodes}


@pytest.mark.asyncio
async def test_neighbour_report_needs_inn_when_ambiguous(monkeypatch, documents):
    from app.agent.runtime import MasterAgentRuntime
    from app.agent.tools import ToolContext, build_tool_registry
    from app.config import Settings
    from app.llm.groq_client import GroqClient
    rows = copy.deepcopy(snapshots(documents))
    for inn in (A, '1684017097'):
        root(rows, inn)['document']['report']['baseInfo']['email'] = 'shared@example.org'
    async def get(inn): return root(rows, inn)
    async def candidates(): return rows
    async def batch(inns): return [root(rows, inn) for inn in inns]
    monkeypatch.setattr('app.infrastructure.repository.get_latest_snapshot', get)
    monkeypatch.setattr('app.infrastructure.repository.get_connection_candidates', candidates)
    monkeypatch.setattr('app.infrastructure.repository.get_snapshots_for_connections', batch)
    settings = Settings(llm_mock=True)
    runtime = MasterAgentRuntime(model=None, model_name='offline', registry=build_tool_registry(settings),
        model_timeout_s=5, run_timeout_s=20, tool_context=ToolContext(settings, GroqClient(settings), False))
    first = await runtime.run('Проверь контрагента ' + A)
    second = await runtime.run('Отдельный отчёт по связанной компании', first.conversation_id)
    assert second.metadata.status == 'needs_input' and second.metadata.tool_calls == 0
    assert second.active_company.inn == A
    assert 'Укажите ИНН' in second.message

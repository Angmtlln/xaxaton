"""Dataset identities, one-hop review and degraded data."""
import copy
import pytest
from app.agent.connections import discover_connections, cross_check
from app.agent.models import CompanyConnections

A, B = '7805327192', '4720028039'

def snapshots(documents):
    return [{'document': d} for d in documents]

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

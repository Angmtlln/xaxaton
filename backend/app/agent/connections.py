"""One-hop dataset cross-check. Exact identities, bounded review, zero LLM calls."""
from __future__ import annotations

import asyncio
import logging
import re

from app.infrastructure import repository
from app.infrastructure.progress import emit_progress
from .data_sections import company_from_snapshot, report_of, safe_value
from .models import CompanyConnections, ConnectionEdge, ConnectionNode, is_valid_inn

log = logging.getLogger(__name__)
MAX_NEIGHBOURS = 6
MAX_EDGES = 30
CANDIDATE_LIMIT = 10000


def _rows(value):
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _identifier(value):
    text = str(value or '').strip()
    return text if is_valid_inn(text) else None


def _keys(snapshot):
    report = report_of(snapshot)
    base = report.get('baseInfo') or {}
    founders = report.get('foundersInfo') or {}
    result = {k: {} for k in ('founder', 'director', 'related', 'address', 'email', 'website', 'phone')}
    if isinstance(founders, dict):
        for i, row in enumerate(_rows(founders.get('cofounders'))):
            inn = _identifier(row.get('inn'))
            # Unknown/historical membership must not become a current shared owner.
            if inn and row.get('active') is True:
                result['founder'][inn] = f'report.foundersInfo.cofounders[{i}]'
        auth = founders.get('authPerson') or {}
        if isinstance(auth, dict) and (inn := _identifier(auth.get('inn'))):
            result['director'][inn] = 'report.foundersInfo.authPerson'
    for i, row in enumerate(_rows(report.get('relatedCompanies'))):
        if inn := _identifier(row.get('inn')):
            result['related'][inn] = f'report.relatedCompanies[{i}]'
    for key in ('address', 'email', 'website'):
        value = base.get(key) if isinstance(base, dict) else None
        if isinstance(value, str) and value.strip():
            normalized = ' '.join(value.casefold().split())
            if key == 'website':
                normalized = re.sub(r'^https?://', '', normalized).rstrip('/')
            result[key][normalized] = 'report.baseInfo.' + key
    for i, row in enumerate(_rows(report.get('phones'))):
        value = re.sub(r'\D', '', str(row.get('phoneCode') or '') + str(row.get('phoneNumber') or ''))
        if len(value) >= 10:
            result['phone'][value] = f'report.phones[{i}]'
    return result


def discover_connections(root_snapshot, candidates):
    """O(dataset size), comparing only root keys; reverse references included."""
    company = company_from_snapshot(root_snapshot)
    root = company.inn
    left = _keys(root_snapshot)
    candidates = {company_from_snapshot(s).inn: s for s in candidates}
    edges = []
    labels = {'founder': 'Общий действующий учредитель', 'director': 'Общий руководитель',
              'related': 'Общая связанная организация', 'address': 'Совпадает адрес',
              'email': 'Совпадает email', 'website': 'Совпадает сайт', 'phone': 'Совпадает телефон'}
    kinds = {'founder': 'shared_founder', 'director': 'shared_director', 'related': 'shared_related'}
    for inn, snapshot in sorted(candidates.items()):
        if inn == root or not is_valid_inn(inn):
            continue
        right = _keys(snapshot)
        def add(kind, label, via, refs):
            edges.append(ConnectionEdge(source=root, target=inn, kind=kind,
                                        label=label, via=safe_value(via), field_refs=refs))
        for key in left:
            for value in sorted(left[key].keys() & right[key].keys()):
                add(kinds.get(key, key), labels[key], value,
                    [f'{root}:{left[key][value]}', f'{inn}:{right[key][value]}'])
        # Founder/director overlap is meaningful only when the roles differ.
        for a, b in [('founder', 'director'), ('director', 'founder')]:
            for value in sorted(left[a].keys() & right[b].keys()):
                if value in left[b] and value in right[a]:
                    continue  # already represented by two shared-role edges
                add('founder_director', 'Учредитель одной компании руководит другой', value,
                    [f'{root}:{left[a][value]}', f'{inn}:{right[b][value]}'])
        refs = ([f'{root}:{left["related"][inn]}'] if inn in left['related'] else [])
        refs += ([f'{inn}:{right["related"][root]}'] if root in right['related'] else [])
        if refs:
            add('related_company', 'Связь указана в карточке', None, refs)
        for owner, owned, keys in [(inn, root, left), (root, inn, right)]:
            if owner in keys['founder']:
                add('ownership', f'Компания {owner} — учредитель {owned}', owner,
                    [f'{owned}:{keys["founder"][owner]}'])
    neighbours = sorted({e.target for e in edges})
    chosen = set(neighbours[:MAX_NEIGHBOURS])
    selected = [e for e in edges if e.target in chosen][:MAX_EDGES]
    represented = {e.target for e in selected}
    def node(snapshot, state='unavailable'):
        c = company_from_snapshot(snapshot)
        return ConnectionNode(inn=c.inn, name=c.short_name or c.full_name or c.inn,
                              snapshot_id=c.snapshot_id, report_date=c.report_date, review_state=state)
    return CompanyConnections(
        root_inn=root, nodes=[node(root_snapshot, 'root')] + [node(candidates[i]) for i in sorted(represented)],
        edges=selected, total_companies=len(neighbours), total_edges=len(edges),
        external_references=len(set(left['related']) - candidates.keys()),
        state='partial' if len(selected) < len(edges) else 'complete',
    )


def review_node(node, snapshot):
    """Reuse normalized finance/legal semantics, including missing vs zero."""
    from .finance import build_financial_data
    from .legal import build_legal_data
    finance = build_financial_data(snapshot, node.inn)
    legal = build_legal_data(snapshot)
    ids = ('court.defendant_count', 'court.defendant_amount', 'execproc.active_count',
           'execproc.active_amount', 'flags.hard_stop_codes', 'flags.attention_codes')
    observations = [legal.facts[i] for i in ids if i in legal.facts]
    # The latest financial row and independent bank values; no aggregate score.
    series = finance.facts.get('fin.series')
    if series and series.value:
        year = series.value[-1]['year']
        observations += [finance.facts[i] for i in (f'fin.proceeds.{year}', f'fin.capitals.{year}')
                         if i in finance.facts]
    for key in ('bank.risk_level', 'bank.zsk_level', 'company.status'):
        if key in legal.facts:
            observations.append(legal.facts[key])
    c = company_from_snapshot(snapshot)
    gaps = list(dict.fromkeys(finance.gaps + legal.gaps))[:8]
    return node.model_copy(update={'snapshot_id': c.snapshot_id, 'report_date': c.report_date,
        'observations': observations[:12], 'gaps': gaps,
        'review_state': 'partial' if gaps else 'reviewed'})


async def cross_check(snapshot):
    root = company_from_snapshot(snapshot).inn
    try:
        async with asyncio.timeout(4):
            emit_progress("connections")
            candidates = await repository.get_connection_candidates()
            graph = discover_connections(snapshot, candidates[:CANDIDATE_LIMIT])
            if len(candidates) > CANDIDATE_LIMIT:
                graph.state = 'partial'
                graph.note = 'Достигнут лимит 10 000 карточек; поиск связей неполный.'
            if graph.nodes[1:]:
                emit_progress("neighbours")
            snapshots = await repository.get_snapshots_for_connections([n.inn for n in graph.nodes[1:]])
            by_inn = {company_from_snapshot(s).inn: s for s in snapshots}
            nodes = [graph.nodes[0]]
            for node in graph.nodes[1:]:
                try:
                    nodes.append(review_node(node, by_inn[node.inn]))
                except (KeyError, ValueError, TypeError):
                    nodes.append(node)
                    graph.state = 'partial'
            graph.nodes = nodes
            return graph
    except Exception:
        log.warning('Dataset cross-check unavailable for %s', root, exc_info=True)
        return CompanyConnections(root_inn=root, state='unavailable',
                                  note='Внутренняя кросс-проверка недоступна; отсутствие связей не подтверждено.')


def fallback_connections_text(connections):
    parts = []
    for node in connections.get('nodes', [])[1:]:
        reasons = list(dict.fromkeys(e['label'] for e in connections['edges'] if e['target'] == node['inn']))
        facts = node.get('observations', [])
        stops = [v.get('meaning', v.get('code')) for f in facts if f['id'] == 'flags.hard_stop_codes'
                 for v in (f.get('value') or [])]
        review = ('В кратком срезе есть сигналы источника: ' + ', '.join(stops) + '.') if stops else (
            'Краткий срез получен; автоматический вывод о благонадёжности не формировался.'
            if node['review_state'] in {'reviewed', 'partial'} else 'Данные для краткого обзора недоступны.')
        parts.append(f"{node['name']}, ИНН {node['inn']}: {', '.join(reasons)}. {review} Можно запросить отдельный отчёт по этому ИНН.")
    return '\n\nСвязанные компании: ' + ' '.join(parts) if parts else ''

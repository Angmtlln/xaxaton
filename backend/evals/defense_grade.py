"""Exact structural checks. Prose is evaluated separately, never with regex."""
from __future__ import annotations

from .graders import check, structured_checks

GUARDS = {'missing_inn','invalid_inn','ambiguous_inn','stale_company_choice','invalid_company_choice'}


def grade(row, spec, docs):
    if row.get('error'):
        return [check('runtime_completed',False,row['error'])]
    response=row['response']; meta=response['metadata']
    error=meta.get('error_code')
    reads=row.get('reads',[]); names=[t['name'] for t in row.get('tools',[])]
    clarification=spec['outcome']=='boundary'
    # A deterministic outcome can be valid; its relevance still needs semantic review.
    valid_mode=meta.get('synthesis')=='model' or (clarification and (not error or error in GUARDS))
    selection='select_counterparties' in names or bool(row.get('after',{}).get('counterparty_selection'))
    max_models=8  # selection budget, or six regular/debug calls plus bounded name resolution
    out=[check('runtime_completed',True),
         check('valid_response_mode',valid_mode and (not error or (clarification and error in GUARDS)),
               {'synthesis':meta.get('synthesis'),'error_code':error}),
         check('model_budget',sum(c['kind']=='master' for c in row['calls'])<=max_models),
         check('domain_tool_budget',len(names)<= (1 if selection else 2)),
         check('no_duplicate_domain_tools',len(names)==len(set(names))),
         check('full_check_not_combined',not ('full_company_check' in names and len(names)>1)),
         check('allowed_tools',all(n in spec['allowed_tools'] for n in names) if spec.get('allowed_tools') is not None else None),
         check('required_reads',set(spec.get('required_reads',[])) <= {r['method'] for r in reads}),
         check('source_reads_completed',all(not r.get('error') for r in reads))]
    for t in row.get('tools',[]):
        args=t['arguments'] if 'arguments' in t else {}
        result=(t.get('result') or {}).get('data') or {}
        company=result.get('company') or {}
        if args.get('inn') and (company.get('inn') or result.get('inn')):
            out.append(check('tool_company_identity',args['inn']==(company.get('inn') or result.get('inn'))))
    for t in row.get('tools',[]):
        data=(t.get('result') or {}).get('data') or {}
        out.extend(structured_checks(data,docs))
        if t['name']=='find_companies':
            matching=next((r for r in reads if r['method']=='find_companies'),None)
            raw=(matching or {}).get('result') or {}
            companies=data.get('companies',[])
            out.append(check('shortlist_hydration', [c['inn'] for c in companies]==[str(c['inn']) for c in raw.get('rows',[])]
                             and data.get('total')==raw.get('total')))
        if t['name']=='select_counterparties':
            profiles=data.get('profiles',[]); finalists=data.get('finalists',[])
            out.append(check('finalists_within_profiles',set(finalists)<={p['inn'] for p in profiles}))
            if data.get('state')=='complete':
                out.append(check('selection_complete_coverage',len(profiles)==data.get('total')
                                 and {p['inn'] for p in profiles}=={d['inn'] for d in data.get('decisions',[])}))
    return out


def aggregate(case, rows, verdicts):
    """One scenario is one denominator unit, including incomplete/unjudged runs."""
    if len(rows)!=len(case['turns']): return 'INCOMPLETE'
    if any(r.get('error') or any(c['status']=='FAIL' for c in r['checks']) for r in rows): return 'FAIL'
    if len(verdicts)!=len(rows): return 'NOT_REVIEWED'
    states=[v['status'] for v in verdicts]
    if 'FAIL' in states: return 'FAIL'
    if any(s in ('JUDGE_ERROR','UNCERTAIN') for s in states): return 'UNCERTAIN'
    if 'PARTIAL' in states: return 'PARTIAL'
    return 'PASS' if all(s=='PASS' for s in states) else 'NOT_REVIEWED'

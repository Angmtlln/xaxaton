"""Measurement regressions, not tests mirroring agent prose."""
import copy
import json
import os
from types import SimpleNamespace

import pytest

from evals.defense_bank import compile_bank, load
from evals.defense_grade import grade, aggregate
from evals.defense_judge import validate_judgment, evidence_for
from evals.defense_run import select_cases, save, read


def example():
    return {'error':None,'calls':[{'kind':'master'}]*3,'reads':[],
            'tools':[{'name':'get_financial_data'},{'name':'get_legal_data'}],
            'after':{},'response':{'message':'Ответ.', 'metadata':{'synthesis':'model','error_code':None}}}


def test_bank_balanced_pinned_and_holdout_excluded_from_pilot():
    bank=load(); assert len(bank['cases'])==150
    assert sum(len(c['turns']) for c in bank['cases'])==194
    pilot=select_cases(bank,SimpleNamespace(suite='pilot',case=None))
    assert len(pilot)==20 and all(c['split']=='development' for c in pilot)
    assert {c['category'] for c in pilot}==set('GFLNRSECM')
    for c in bank['cases']:
        for t in c['turns']: assert '<A>' not in t['question']
    assert len({t['question'] for c in bank['cases'] if c['category']=='C' for t in c['turns']})==30


def test_two_targeted_tools_valid_full_plus_targeted_invalid():
    row=example(); spec={'outcome':'answer'}
    assert not any(c['status']=='FAIL' for c in grade(row,spec,{}))
    row['tools'][0]['name']='full_company_check'
    assert any(c['name']=='full_check_not_combined' and c['status']=='FAIL' for c in grade(row,spec,{}))


def test_eight_selection_calls_valid_ninth_fails():
    row=example(); row['calls']=[{'kind':'master'}]*8; row['tools']=[{'name':'select_counterparties'}]
    assert not any(c['status']=='FAIL' for c in grade(row,{'outcome':'answer'},{}))
    row['calls'].append({'kind':'master'})
    assert any(c['name']=='model_budget' and c['status']=='FAIL' for c in grade(row,{'outcome':'answer'},{}))


def test_guard_can_be_valid_but_provider_failure_never_is():
    row=example(); row['tools']=[]; row['calls']=[]
    row['response']['metadata']={'synthesis':'deterministic','error_code':'missing_inn'}
    assert not any(c['status']=='FAIL' for c in grade(row,{'outcome':'boundary'},{}))
    assert any(c['status']=='FAIL' for c in grade(row,{'outcome':'answer'},{}))
    row['response']['metadata']['error_code']='model_timeout'
    assert any(c['status']=='FAIL' for c in grade(row,{'outcome':'boundary'},{}))


def test_scenario_denominator_and_missing_review():
    case={'turns':[{},{}]}; row={'checks':[]}
    assert aggregate(case,[row],[])=='INCOMPLETE'
    assert aggregate(case,[row,row],[])=='NOT_REVIEWED'
    assert aggregate(case,[row,row],[{'status':'PASS'},{'status':'PARTIAL'}])=='PARTIAL'
    assert aggregate(case,[row,row],[{'status':'PASS'},{'status':'JUDGE_ERROR'}])=='UNCERTAIN'
    assert aggregate(case,[row,row],[{'status':'PASS'},{'status':'FAIL'}])=='FAIL'


def test_judge_requires_all_parts_real_quotes_and_resolvable_paths():
    spec={'requirements':[{'id':'r1'},{'id':'r2'}]}; evidence={'source':{'profit':0}}
    p={'requirements':[{'id':'r1','status':'met','reason':'Значение верно','quote':'ноль','source_paths':[['source','profit']]},
                       {'id':'r2','status':'missed','reason':'Вторая часть отсутствует'}]}
    assert validate_judgment(p,spec,'Прибыль ноль.',evidence)['status']=='PARTIAL'
    bad=copy.deepcopy(p); bad['requirements'][0]['quote']='сто миллионов'
    with pytest.raises(ValueError): validate_judgment(bad,spec,'Прибыль ноль.',evidence)
    bad=copy.deepcopy(p); bad['requirements'][0]['source_paths']=[['source','absent']]
    with pytest.raises(ValueError): validate_judgment(bad,spec,'Прибыль ноль.',evidence)
    bad=copy.deepcopy(p); bad['requirements'].pop()
    with pytest.raises(ValueError): validate_judgment(bad,spec,'Прибыль ноль.',evidence)


def test_candidate_index_does_not_claim_all_reports_were_read():
    row=example(); row['after']={'active_company':{'inn':'123'}}
    row['reads']=[{'method':'get_connection_candidates','result':[{'inn':'456','document':{'private':'duplicate'}}]}]
    evidence=evidence_for(row,[],{'123':{'data':'root'},'456':{'data':'other'}})
    assert set(evidence['source'])=={'123'}
    assert 'document' not in evidence['observed']['reads'][0]['result'][0]


def test_atomic_trace_round_trip(tmp_path):
    path=tmp_path/'row.json.gz'; save(path,{'message':'Пример','value':None})
    assert read(path)=={'message':'Пример','value':None}
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.skipif(os.getenv('TEST_DEFENSE_POSTGRES')!='1',reason='Opt-in disposable Docker PostgreSQL')
def test_disposable_postgres_source_parity_permissions_and_real_ranking():
    import asyncio
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool
    from evals.defense_data import temporary_database
    from app.infrastructure.company_postgres import PostgresCompanyDataReader
    with temporary_database() as db:
        assert db['documents']==100 and db['source_verified']
        with psycopg.connect(db['dsn'],autocommit=True) as conn:
            conn.execute('SET default_transaction_read_only=off')
            with pytest.raises(psycopg.errors.InsufficientPrivilege): conn.execute('DELETE FROM core.companies WHERE false')
        async def verify():
            async with AsyncConnectionPool(db['dsn'],open=False,kwargs={'row_factory':dict_row}) as pool:
                reader=PostgresCompanyDataReader(pool)
                result=await reader.find_companies(activity_query='торговля',min_proceeds=10000000,
                    ranking=[{'metric':'profit','order':'desc'}],limit=5)
                assert result['total']==43 and result['eligible_total']==17
                assert [r['inn'] for r in result['rows']]==['7728380537','3123346195','3711039473','7724398540','7802932240']
                assert (await reader.search_companies('Электролид'))['exact_total']==1
                assert len(await reader.get_connection_candidates())==100
        asyncio.run(verify())

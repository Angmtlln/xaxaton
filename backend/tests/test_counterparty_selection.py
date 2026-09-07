"""Bounded goal-driven selection, failures and conversation continuity."""
import asyncio
import json
import pytest
from langchain_core.messages import AIMessage
from app.agent.selection import SelectionSession, compact_profile, execute_selection
from app.agent.selection_models import (CandidateDecision, CandidateReview, ReviewBatch,
    SelectCounterpartiesArgs, SelectionData, SelectionDecision)
from app.agent.selection_runtime import explicit_selection_route, handles_selection
from app.agent.tools import ToolContext
from app.llm.groq_client import GroqClient
from test_agent_runtime import _model, _runtime, _settings
from test_comparison import _snapshot, _fin_row, RICH


def inn_for(i):
    digits = [int(c) for c in f'77000{i:04d}']
    return ''.join(map(str, digits)) + str(sum(a*b for a,b in zip(digits,[2,4,10,3,5,9,4,6,8])) % 11 % 10)


@pytest.mark.parametrize("message, goal, finalists, filters", [
    ("Подбери 3 поставщиков с выручкой от 999999999999999 рублей.",
     "поставщик", 3, {"min_proceeds": 999999999999999}),
    ("Подбери 2 поставщиков с выручкой от 1 млрд, главное минимальная юридическая нагрузка.",
     "поставщик", 2, {"min_proceeds": 1_000_000_000}),
    ("Подбери 2 торговые компании с выручкой от 1 млрд для закупки без аванса, важны стабильные поставки.",
     "поставщик без аванса", 2, {"activity_query": "торговля", "min_proceeds": 1_000_000_000}),
    ("Подбери 2 торговых поставщиков с выручкой от 1 млрд без аванса, укажи причины выбора и ограничения.",
     "поставщик без аванса", 2, {"activity_query": "торговля", "min_proceeds": 1_000_000_000}),
])
def test_explicit_defense_selection_skips_model_guard(message, goal, finalists, filters):
    route = explicit_selection_route(message)
    assert route is not None
    assert route.goal == goal and route.finalists == finalists
    for key, value in filters.items():
        assert getattr(route.filters, key) == value


def test_two_inns_are_comparison_before_goal_selection():
    assert not handles_selection(
        "Выбери между 3711039473 и 6165169320 покупателя на отсрочку 60 дней", {}
    )


@pytest.fixture
def database(monkeypatch):
    calls, store = {'search': [], 'snapshots': []}, {}
    def populate(n):
        store.clear()
        for i in range(n):
            inn = inn_for(i)
            store[inn] = {**_snapshot(inn, f'Компания {i}', fin_rows=[_fin_row(2024,100+i,20+i)]),
                          'snapshot_id':i+1,'report_date':'2026-01-01'}
    async def find(**args):
        calls['search'].append(args)
        return {'total':len(store),'rows':[{'inn':s['inn'],'snapshot_id':s['snapshot_id']} for s in list(store.values())[:args['limit']]]}
    async def snapshots(ids):
        calls['snapshots'].append(ids)
        return [s for s in store.values() if s['snapshot_id'] in ids]
    monkeypatch.setattr('app.infrastructure.repository.find_companies',find)
    monkeypatch.setattr('app.infrastructure.repository.get_selection_snapshots',snapshots)
    return store,calls,populate


def args():
    return SelectCounterpartiesArgs(filters={'min_proceeds':10},goal='поставщик без аванса')


def review_for(p):
    return CandidateReview(inn=p.inn,summary='Есть финансовые данные для обсуждения поставки.',
        strengths='Доступна отчётность.',limitations='Неизвестна способность исполнить поставку.',
        missing_data='Нужны условия договора.',evidence_ids=list(p.facts)[:1])


def decision_for(profiles,finalists):
    return SelectionDecision(finalists=finalists,decisions=[CandidateDecision(inn=p.inn,
        reason='Соответствие задаче требует проверки условий договора.',evidence_ids=list(p.facts)[:1]) for p in profiles])


def context(ask,previous=None):
    settings=_settings()
    return ToolContext(settings,GroqClient(settings),persist=False,selection_session=SelectionSession(ask,previous=previous))


@pytest.mark.asyncio
@pytest.mark.parametrize('n',[0,1,5,50,51])
async def test_entire_filtered_set_and_limits(database,n):
    store,calls,populate=database
    populate(n)
    batches=[]
    async def ask(prompt,payload,schema):
        profiles=[compact_profile(store[p['inn']],p['inn']) for p in payload['verified_profiles']]
        if schema is ReviewBatch:
            batches.append([p.inn for p in profiles])
            return ReviewBatch(reviews=[review_for(p) for p in profiles])
        return decision_for(profiles,[p.inn for p in profiles[-min(5,n):]])
    data=SelectionData.model_validate((await execute_selection(context(ask),args())).data)
    assert data.total==n
    assert calls['search'][0]['min_proceeds']==10 and calls['search'][0]['limit']==50
    assert 'ranking' not in calls['search'][0]
    if n>50 or n==0:
        assert not batches and not calls['snapshots']
        assert data.state==('too_many' if n>50 else 'empty')
    else:
        assert data.state=='complete'
        assert set(sum(batches,[]))==set(store)
        assert all(len(b)<=10 for b in batches)
        assert list(store)[-1] in data.finalists
        assert bool(data.comparison)==(n>=2)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['exception','foreign_inn','foreign_evidence','duplicate'])
async def test_bad_batch_never_produces_global_winners(database,failure):
    store,_,populate=database
    populate(11)
    async def ask(prompt,payload,schema):
        assert schema is ReviewBatch
        profiles=[compact_profile(store[p['inn']],p['inn']) for p in payload['verified_profiles']]
        reviews=[review_for(p) for p in profiles]
        if len(profiles)==10:
            if failure=='exception': raise RuntimeError('provider down')
            if failure=='foreign_inn': reviews[0].inn=RICH
            if failure=='foreign_evidence': reviews[0].evidence_ids=[RICH+':fin.profit.2024']
            if failure=='duplicate': reviews[0]=reviews[1]
        return ReviewBatch(reviews=reviews)
    data=SelectionData.model_validate((await execute_selection(context(ask),args())).data)
    assert data.state=='partial' and len(data.reviews)==1
    assert not data.finalists and not data.comparison


@pytest.mark.asyncio
async def test_reuses_profiles_on_goal_change_and_limits_concurrency(database):
    store,calls,populate=database
    populate(21)
    active=peak=0
    async def ask(prompt,payload,schema):
        nonlocal active,peak
        profiles=[compact_profile(store[p['inn']],p['inn']) for p in payload['verified_profiles']]
        if schema is ReviewBatch:
            active+=1
            peak=max(peak,active)
            await asyncio.sleep(.01)
            active-=1
            return ReviewBatch(reviews=[review_for(p) for p in profiles])
        winner=profiles[0] if payload['goal']=='покупатель с отсрочкой' else profiles[-1]
        return decision_for(profiles,[winner.inn])
    first=SelectionData.model_validate((await execute_selection(context(ask),args())).data)
    second=SelectionData.model_validate((await execute_selection(context(ask,first),args().model_copy(update={'goal':'покупатель с отсрочкой'}))).data)
    assert len(calls['search'])==1 and len(calls['snapshots'])==1
    assert first.finalists!=second.finalists and peak==2


def ai(value): return AIMessage(content=json.dumps(value,ensure_ascii=False))


@pytest.mark.asyncio
async def test_runtime_clarification_selection_and_contextual_explanation(database):
    store,calls,populate=database
    populate(2)
    profiles=[compact_profile(s,inn) for inn,s in store.items()]
    model=_model(
        ai({'action':'clarify','filters':{'min_proceeds':10},'question':'Для какой задачи выбираем?'}),
        ai({'action':'select','use_previous_filters':True,'goal':'поставщик'}),
        ai(ReviewBatch(reviews=[review_for(p) for p in profiles]).model_dump()),
        ai(decision_for(profiles,[p.inn for p in profiles]).model_dump()),
        ai({'message':'Уточните условия поставки: данных о фактическом исполнении нет.','order':[p.inn for p in reversed(profiles)]}),
        ai({'action':'explain','explain_inns':[profiles[0].inn]}),
        ai({'message':'Эта компания рассмотрена; необходимо уточнить условия поставки.', 'order':[profiles[0].inn]}))
    runtime=_runtime(model)
    first=await runtime.run('Выбери лучших контрагентов с выручкой от 10 рублей')
    assert first.metadata.status=='needs_input' and first.metadata.tool_calls==0
    assert not calls['search']
    second=await runtime.run('Поставщик',first.conversation_id)
    assert second.metadata.tool_calls==1 and second.metadata.model_calls==4
    assert second.metadata.synthesis=='model'
    assert any(b.type=='comparison_table' for b in second.blocks)
    assert second.active_company is None
    third=await runtime.run('Почему не первая компания?',first.conversation_id)
    assert third.metadata.tool_calls==0 and third.metadata.model_calls==2
    assert len(calls['search'])==1 and not third.blocks


@pytest.mark.parametrize('message,expected',[
    ('Найди компании, занимающиеся поставками оборудования',False),
    ('Найди компании с выручкой от 10 млн',False),('Выбери 5 с наибольшей прибылью',False),
    ('Подбери лучших поставщиков с выручкой от 10 млн',True),('Выбери лучших контрагентов',True),
    ('Подбери контрагента с выручкой от 10 млрд рублей для поставок без аванса; важна устойчивость поставок.',True),
    ('Проверь 6165169320',False)])
def test_routing_preserves_basic_flows(message,expected):
    assert handles_selection(message,{})==expected


@pytest.mark.asyncio
async def test_invalid_finalist_and_missing_decision_are_partial(database):
    store,_,populate=database
    populate(2)
    for kind in ('foreign','missing','duplicate'):
        async def ask(prompt,payload,schema):
            profiles=[compact_profile(store[p['inn']],p['inn']) for p in payload['verified_profiles']]
            if schema is ReviewBatch:
                return ReviewBatch(reviews=[review_for(p) for p in profiles])
            decision=decision_for(profiles,[profiles[0].inn])
            if kind=='foreign': decision.finalists=[RICH]
            if kind=='missing': decision.decisions=decision.decisions[:1]
            if kind=='duplicate': decision.finalists=[profiles[0].inn,profiles[0].inn]
            return decision
        data=SelectionData.model_validate((await execute_selection(context(ask),args())).data)
        assert data.state=='partial' and not data.finalists


@pytest.mark.asyncio
async def test_timeout_preserves_completed_reviews(database):
    store,_,populate=database
    populate(11)
    async def ask(prompt,payload,schema):
        profiles=[compact_profile(store[p['inn']],p['inn']) for p in payload['verified_profiles']]
        if len(profiles)==10: raise asyncio.TimeoutError()
        return ReviewBatch(reviews=[review_for(p) for p in profiles])
    data=SelectionData.model_validate((await execute_selection(context(ask),args())).data)
    assert data.state=='partial' and len(data.reviews)==1 and not data.finalists


@pytest.mark.asyncio
async def test_model_budget_is_shared_and_stops_at_eight():
    import time
    from app.agent.selection_runtime import SelectionModelBudget
    from app.agent.selection_models import SelectionAnswer
    from app.agent.langchain_tools import LangChainToolExecution
    model=_model(ai({'message':'Ответ.'}))
    execution=LangChainToolExecution(model_calls=7)
    budget=SelectionModelBudget(model,execution,time.monotonic()+5,5)
    await budget.ask('Ответь',{},SelectionAnswer)
    with pytest.raises(RuntimeError,match='budget'):
        await budget.ask('Ответь',{},SelectionAnswer)
    assert execution.model_calls==8 and model.calls==1


@pytest.mark.asyncio
@pytest.mark.parametrize('repair', ['valid', 'too_long', 'foreign_inn', 'changed_evidence'])
async def test_review_length_repair_is_bounded_and_preserves_fields(database, repair):
    import time
    from app.agent.selection_runtime import SelectionModelBudget
    from app.agent.langchain_tools import LangChainToolExecution
    store, _, populate = database
    populate(1)
    p = compact_profile(next(iter(store.values())), next(iter(store)))
    original = ReviewBatch(reviews=[review_for(p)]).model_dump()
    original['reviews'][0]['summary'] = 'Длинный обзор. ' * 40
    corrected = ReviewBatch(reviews=[review_for(p)]).model_dump()
    if repair == 'too_long': corrected = original
    if repair == 'foreign_inn': corrected['reviews'][0]['inn'] = RICH
    if repair == 'changed_evidence': corrected['reviews'][0]['evidence_ids'] = []
    model = _model(ai(original), ai(corrected))
    execution = LangChainToolExecution()
    budget = SelectionModelBudget(model, execution, time.monotonic()+10, 5)
    if repair == 'valid':
        result = await budget.ask('Обзор', {}, ReviewBatch)
        assert result.reviews[0].inn == p.inn
        assert result.reviews[0].evidence_ids == original['reviews'][0]['evidence_ids']
    else:
        with pytest.raises(ValueError): await budget.ask('Обзор', {}, ReviewBatch)
    assert model.calls == execution.model_calls == 2
    assert budget.repair_attempts == 1


@pytest.mark.asyncio
async def test_runtime_completes_after_length_repair(database):
    store, calls, populate = database
    populate(1)
    profiles = [compact_profile(s, inn) for inn, s in store.items()]
    valid = ReviewBatch(reviews=[review_for(p) for p in profiles]).model_dump()
    invalid = json.loads(json.dumps(valid))
    invalid['reviews'][0]['summary'] = 'Длинный обзор. ' * 40
    model = _model(
        ai({'action':'select','filters':{'min_proceeds':10000000000},'goal':'поставщик без аванса','finalists':1}),
        ai(invalid), ai(valid), ai(decision_for(profiles,[profiles[0].inn]).model_dump()),
        ai({'message':'Пригодность поставщика требует проверки условий договора.'}))
    response = await _runtime(model).run('Подбери контрагента с выручкой от 10 млрд рублей для поставок без аванса; важна устойчивость поставок.')
    assert response.metadata.status == 'completed'
    assert response.metadata.model_calls == 5 and response.metadata.repair_attempts == 1
    assert response.metadata.tool_calls == 1 and response.metadata.synthesis == 'model'
    assert calls['search'][0]['min_proceeds'] == 10000000000


@pytest.mark.asyncio
async def test_length_repair_never_exceeds_shared_call_budget(database):
    import time
    from app.agent.selection_runtime import SelectionModelBudget
    from app.agent.langchain_tools import LangChainToolExecution
    store, _, populate = database
    populate(1)
    p = compact_profile(next(iter(store.values())), next(iter(store)))
    invalid = ReviewBatch(reviews=[review_for(p)]).model_dump()
    invalid['reviews'][0]['summary'] = 'Обзор. ' * 100
    model = _model(ai(invalid))
    execution = LangChainToolExecution(model_calls=7)
    budget = SelectionModelBudget(model, execution, time.monotonic()+10, 5)
    with pytest.raises(ValueError): await budget.ask('Обзор', {}, ReviewBatch)
    assert execution.model_calls == 8 and model.calls == 1 and budget.repair_attempts == 0


@pytest.mark.asyncio
async def test_no_finalists_is_completed_with_explanation(database):
    store, _, populate = database
    populate(1)
    profiles = [compact_profile(s, inn) for inn, s in store.items()]
    model = _model(
        ai({'action':'select','filters':{'min_proceeds':10},'goal':'поставщик'}),
        ai(ReviewBatch(reviews=[review_for(p) for p in profiles]).model_dump()),
        ai(decision_for(profiles, []).model_dump()),
        ai({'message':'Не рекомендую кандидата под эти условия поставки; уточните ограничения.'}))
    response = await _runtime(model).run('Подбери лучших поставщиков с выручкой от 10 рублей')
    assert response.metadata.status == 'completed' and response.metadata.synthesis == 'model'
    assert not response.blocks
    assert 'Не рекомендую' in response.message


@pytest.mark.asyncio
async def test_final_answer_rejects_foreign_inn(database):
    store,_,populate=database
    populate(1)
    profiles=[compact_profile(s,inn) for inn,s in store.items()]
    model=_model(ai({'action':'select','filters':{'min_proceeds':10},'goal':'поставщик'}),
        ai(ReviewBatch(reviews=[review_for(p) for p in profiles]).model_dump()),
        ai(decision_for(profiles,[profiles[0].inn]).model_dump()),
        ai({'message':'Выберите ИНН 6165169320.'}))
    response=await _runtime(model).run('Подбери лучших поставщиков с выручкой от 10 рублей')
    assert response.metadata.synthesis=='fallback'
    assert '6165169320' not in response.message


@pytest.mark.asyncio
async def test_new_filters_repeat_search(database):
    store,calls,populate=database
    populate(1)
    async def ask(prompt,payload,schema):
        profiles=[compact_profile(store[p['inn']],p['inn']) for p in payload['verified_profiles']]
        return ReviewBatch(reviews=[review_for(p) for p in profiles]) if schema is ReviewBatch else decision_for(profiles,[profiles[0].inn])
    first=SelectionData.model_validate((await execute_selection(context(ask),args())).data)
    changed=SelectCounterpartiesArgs(filters={'min_proceeds':20},goal='поставщик')
    await execute_selection(context(ask,first),changed)
    assert len(calls['search'])==2 and calls['search'][1]['min_proceeds']==20

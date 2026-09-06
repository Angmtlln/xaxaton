"""Scenario denominators, requirement coverage, latency and auditable answer report."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from .defense_grade import aggregate
from .defense_run import read, save


def percentile(values,p):
    return sorted(values)[min(len(values)-1,int((len(values)-1)*p))] if values else None


def report(out,label=None):
    out=Path(out); manifest=read(out/'manifest.json'); bank=read(out/'bank.json')
    items=read(out/'progress.json')['rows'] if (out/'progress.json').exists() else []
    traces=[read(out/i['trace']) for i in items]; scored=[r for r in traces if r['scored']]
    judgments={}
    if label:
        for r in scored:
            path=out/('judge-'+label)/(r['id']+'.json')
            if path.exists(): judgments[r['id']]=read(path)
    results=[]; groups=defaultdict(Counter); usage=defaultdict(lambda:Counter())
    for c in bank['cases']:
        if c['id'] not in manifest['case_ids']: continue
        for attempt in range(1,manifest['repetitions']+1):
            rows=[r for r in scored if r['case_id']==c['id'] and r['attempt']==attempt]
            verdicts=[judgments[r['id']] for r in rows if r['id'] in judgments]
            status=aggregate(c,rows,verdicts)
            results.append(dict(case_id=c['id'],attempt=attempt,category=c['category'],split=c['split'],status=status,
                                technical_failures=sorted({x['name'] for r in rows for x in r['checks'] if x['status']=='FAIL'})))
            groups[c['category']][status]+=1
    for row in traces:
        for call in row['calls']:
            u=call.get('usage') or {}; key=call['kind']+':'+str(call.get('actual_model') or call.get('model'))
            usage[key]['calls']+=1
            usage[key]['input_tokens']+=u.get('input_tokens',u.get('prompt_tokens',0)) or 0
            usage[key]['output_tokens']+=u.get('output_tokens',u.get('completion_tokens',0)) or 0
            usage[key]['calls_with_usage']+=bool(u)
    for j in judgments.values():
        u=j.get('usage') or {}; key='judge:'+str(j.get('actual_model') or 'configured')
        usage[key]['calls']+=bool(j.get('raw_output')); usage[key]['input_tokens']+=u.get('input_tokens',0) or 0
        usage[key]['output_tokens']+=u.get('output_tokens',0) or 0
    counts=Counter(r['status'] for r in results)
    req=Counter(x['status'] for j in judgments.values() for x in j.get('requirements',[]))
    planned_req=sum(len(t['requirements']) for c in bank['cases'] if c['id'] in manifest['case_ids'] for t in c['turns'])*manifest['repetitions']
    summary=dict(manifest=manifest,authority='automated_provisional_not_human_acceptance',judge_label=label,
                 counts=dict(counts),categories={k:dict(v) for k,v in groups.items()},results=results,
                 pass_rate=counts['PASS']/manifest['planned_attempts'],
                 requirements=dict(req),planned_requirements=planned_req,
                 requirement_completion_rate=req['met']/planned_req,
                 latency_ms={'p50':percentile([r['wall_ms'] for r in scored],.5),'p95':percentile([r['wall_ms'] for r in scored],.95)},
                 usage={k:dict(v) for k,v in usage.items()},cost_usd=None,
                 cost_note='Provider dollar cost unavailable here; token usage is measured, price is not guessed.')
    suffix=label or 'technical'; save(out/('summary-'+suffix+'.json'),summary)
    lines=['# Проверка агента для защиты','',
           f"Версия: {bank['version']}. Commit: `{manifest['git_commit']}`. Модель: `{manifest.get('master_model','unknown')}`.",
           f"Набор: {manifest['planned_scenarios']} сценариев, {manifest['planned_attempts']} попыток, {manifest['planned_scored_turns']} оцениваемых реплик.",
           f"Полностью выполнено: **{counts['PASS']}/{manifest['planned_attempts']} ({summary['pass_rate']:.1%})**.",
           f"Остальные статусы: `{dict(counts)}`. Обязательные части выполнены: {req['met']}/{planned_req}.",
           '', '**Это предварительная автоматическая оценка, не человеческая приёмка.** NOT_REVIEWED, UNCERTAIN и незавершённые попытки не засчитываются в PASS.',
           'Один диалог — один сценарий. Повторы считаются попытками; их нельзя выдавать за новые независимые вопросы.',
           'Техническое выполнение и смысл оцениваются отдельно. Latency не превращает правильный ответ в смысловой FAIL.',
           f"Данные: изолированная PostgreSQL, неизменные 100 карточек; web={manifest.get('web_mode')}. Это runtime eval, не HTTP/MCP/UI приёмка.",
           f"Latency по всем scored-репликам, включая ошибки: {summary['latency_ms']}. Цена в долларах не рассчитана; фактические токены ниже.",
           '', '| Категория | Статусы |','|---|---|']
    for k,v in groups.items(): lines.append(f'| {k} | {dict(v)} |')
    lines+=['','| Модель / этап | Вызовы | Входные токены | Выходные токены |','|---|---:|---:|---:|']
    for k,v in usage.items(): lines.append(f"| {k} | {v['calls']} | {v['input_tokens']} | {v['output_tokens']} |")
    lines+=['','| Сценарий | Попытка | Статус | Технические ошибки |','|---|---:|---|---|']
    for r in results: lines.append(f"| {r['case_id']} | {r['attempt']} | {r['status']} | {', '.join(r['technical_failures'])} |")
    (out/('report-'+suffix+'.md')).write_text('\n'.join(lines)+'\n')
    answers=['# Вопросы, ответы и оценка обязательных частей','']
    for row in sorted(traces,key=lambda r:(r['case_id'],r['attempt'],r['turn'])):
        answers += [f"## {row['id']}"+(' — setup, вне оценки' if not row['scored'] else ''),'',row['spec']['question'],'',
                    row.get('response',{}).get('message') or '**Ответ отсутствует**','',f"Trace: [{row['id']}.json.gz]({row['id']}.json.gz)",'']
        j=judgments.get(row['id'])
        if j:
            answers += [f"Оценка: **{j['status']}**",'']
            for req_item in j.get('requirements',[]): answers.append(f"- {req_item['id']}: {req_item['status']} — {req_item['reason']}")
            for err in j.get('critical_errors',[]): answers.append(f"- Ошибка: «{err['quote']}» — {err['reason']}")
            answers.append('')
    (out/('answers-'+suffix+'.md')).write_text('\n'.join(answers)+'\n')
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--run',required=True); p.add_argument('--judge-label')
    args=p.parse_args(); report(args.run,args.judge_label)

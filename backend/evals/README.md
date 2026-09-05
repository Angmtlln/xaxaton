# Behavioral evals ALEPH

Источник вопросов, порядка M01–M10, ожиданий и taxonomy — только
[`AGENT_EVALS.md`](../../AGENT_EVALS.md). `bank.py` извлекает формулировки дословно,
меняет только `<A>/<B>/<C>` и сохраняет source line / SHA256. Новые fixture probes
используют исходный вопрос «Проверь <A>.», не подменяют им содержательные cases.

## Запуск

Из `backend/`, с существующей `.venv` и настроенным `.env`:

```bash
PYTHONPATH=. .venv/bin/python -m evals.bank
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_eval_harness.py
PYTHONPATH=. .venv/bin/python -m evals.run_local --suite killer --output evals/results/my-killer
PYTHONPATH=. .venv/bin/python -m evals.run_local --suite solo --output evals/results/my-solo
PYTHONPATH=. .venv/bin/python -m evals.run_local --suite comparison --output evals/results/my-comparison
PYTHONPATH=. .venv/bin/python -m evals.run_local --suite traps --output evals/results/my-traps
PYTHONPATH=. .venv/bin/python -m evals.run_local --suite multiturn --output evals/results/my-multiturn
PYTHONPATH=. .venv/bin/python -m evals.run_local --suite full --output evals/results/my-full
```

Это реальные оплачиваемые вызовы настроенных OpenRouter Master и доменных Groq.
Нужны `LLM_MOCK=false`, `OPENROUTER_API_KEY`, `GROQ_API_KEY`. Ключи не пишутся в
артефакты. Никаких API SDK/моделей/провайдеров runtime не меняется.
Вызов идёт через тот же `build_master_runtime().run()`, что и chat route; это
не HTTP/browser/e2e deployment тест. В процессе eval только repository read
подменяется фиксированным snapshot, `persist=False`; нормализованные tools,
четыре доменных агента, Master, budgets и conversation store настоящие.
DB impact отсутствует: SQL, schema, repository и app-файлы не меняются.

`--concurrency 1..8` ограничивает число независимых диалогов (по умолчанию 3).
Внутри каждого диалога запросы последовательны. `--session M04` позволяет
запустить конкретный диалог соответствующей suite. Output directory не
перезаписывается; результаты сохраняются после каждой реплики. После аварийного
прерывания `latest.json` содержит completed/planned; это частичный прогон, не PASS.
Автоматического resume посреди диалога нет: восстановить только prose вместо
LangGraph state было бы неверно. Незавершённый диалог можно повторить отдельно
в новой директории с `--session`, сохранив предыдущую попытку.

## Банк и фикстуры

`scenarios.json` — machine-readable bank: 25 killer, 213 solo (включая 24
дополнительных fixture probes), 92 comparison, 31 traps, 55 реплик M01–M10;
`full` — объединение 406 реплик без двойного исполнения пересечений suites.
Тематические разделы 2–14 выполняются как самостоятельные последовательные
диалоги. Для обращений «у них» явно выполняется setup; traps с единственной
компанией и со сравнением имеют отдельные сессии. Setup и зависимости K-сценариев
логируются, но не входят в scored denominator. `traps` сохраняет предыдущие
реплики K01…K09 / K15…K18, необходимые для исходных follow-up вопросов.

Все 100 исходных документов проверяются source-предикатами при компиляции.
Для каждого fixture фиксируются ИНН, rationale, JSON path, наличие/отсутствие
и значение. Отсутствующий profit отличается от null и zero в evidence.
«Богатая история» означает максимум доступной здесь глубины: в snapshot не
более трёх лет; выбран случай с тремя годами и двумя раскрытыми прибылями.
Слабый капитал — локальный критерий выбора `<5% assets`, не продуктовый score.
Source conflict — позитивный текст о стабильном доходе при proceeds=0.
Unknown execution требует одновременно известных и неизвестных активных сумм.
Представлены все восемь встреченных пар bank risk / ЗСК, включая UNKNOWN.

Состав фикстур после обнаружения ошибки продукта не упрощается.
Компиляция банка после изменения source требует review diff; runner отвергает
дрейф source/snapshot/bank. В каждой директории прогона лежит именно её копия банка.
Факт наличия записи в snapshot не доказывает, что агент запросил нужную страницу:
это проверяется в trace и учитывается в содержательном review.
Для K24 setup явно запрашивает страницу и порядковую запись с UnknownResult.
Дословный scored-вопрос не меняется. Ошибки первоначальных setup сохранены в отчёте.

## Что оценивается

`graders.py` проверяет только структуры: expected/forbidden tools, targeted tool
или доступный trusted domain, повторный full check, fallback, активную компанию,
состав сравнения, смешение company context, наблюдаемый бюджет, latency,
reuse, provenance identifiers, скалярные source values, missing fixture,
числовые count/amount, общий период comparison и запрещённые знаменатели.
Derived source checks опираются на input_refs/точные пути. Это не доказательство
семантики прозы и не исчерпывающая проверка всех финансовых формул.
Latency threshold по умолчанию 60 s — выбранный eval SLO (`--latency-ms`),
не числовое требование исходного документа. NA не засчитывается как PASS.
Domain transport retries отражены в calls; no_fallback относится к итоговому
Master, pipeline status сохраняется в ToolResult. Успех доменных prose-ответов
не объявляется по одной только успешности HTTP-запроса.

`latest.json` и `report.md` — технический результат, **не behavioral acceptance**.
Полные `*.json.gz` содержат вопрос/ответ, setup, before/after trusted state,
аргументы и ToolResult, usage/latency/finish_reason. Hidden reasoning не сохраняется.

Дополнительная содержательная оценка, только после прогона:

```bash
PYTHONPATH=. .venv/bin/python -m evals.judge --run evals/results/my-full --suite full
PYTHONPATH=. .venv/bin/python -m evals.regrade --run evals/results/my-full
```

Judge получает исходную rubric, неизменный вопрос, ожидание, предыдущие реплики,
trusted state и полные source-документы соответствующих компаний. Он не вызывает
tools продукта, не исправляет ответ и не включается в runtime пользователя.
Возможен `--model <другая-доступная-модель-OpenRouter>` для независимого reviewer.
По умолчанию используется текущий Master: `same_model_as_subject=true` явно
фиксирует bias. Все автоматические verdicts предварительные. В FAIL обязательны
категория §19, точная цитата ответа и ссылка на источник; quote/category проверяются
кодом, смысл source_reference требует review. Возможны PASS, FAIL, UNCERTAIN,
BLOCKED_RUNTIME и JUDGE_ERROR. Последние три — не успешная приёмка.
Judge raw final output сохраняется отдельно; usage одной batch нельзя суммировать
повторно по каждому её verdict. Повтор judge не перезаписывает предыдущую попытку.

`regrade` пересчитывает точные проверки на сохранённых ответах без новых LLM
вызовов, пишет отдельный `regraded.json` с хешем grader и сохраняет исходную оценку.
В финальном отчёте ручная оценка и ошибки измерения отделяются от judge verdicts.

Полный архив и Markdown-отчёт для репозитория:

```bash
PYTHONPATH=. .venv/bin/python -m evals.export_report --run evals/results/my-full --destination ../docs/evals/my-run
```

Первый сохранённый результат: [report.md](../../docs/evals/2026-09-05/report.md).

## Сравнение моделей на одинаковом контексте

`compare_models` повторяет выбранные **контекстные** реплики завершённых
`run_local`-прогонов через тот же runtime. Каждая попытка получает отдельный
ConversationStore с сохранённым trusted state и исходной историей сообщений.
Ошибки в прежней прозе намеренно сохраняются для обеих моделей; она не
становится trusted data. Ответы одной попытки не попадают в следующую.

```bash
PYTHONPATH=. .venv/bin/python -m evals.compare_models \
  --run evals/results/structured-scope-final-comparison \
  --run evals/results/structured-scope-final-targets \
  --case K19 --case K21 --case S15_12 \
  --model z-ai/glm-5.3-flash --model anthropic/claude-sonnet-4.6 \
  --repetitions 3 --concurrency 2 --output evals/results/model-comparison-replay
```

Нужен только настроенный live OpenRouter; `grounding_debug=false`. Доступ к
domain tools и repository заблокирован внутри eval-процесса: это проверка
рассуждения по готовому контексту, не end-to-end проверка маршрутизации.
Источник, банк и snapshot проверяются по hash; заменённый вопрос, потеря
истории или разрыв trusted state отвергаются. Сохраняются frozen inputs,
фактические сообщения к модели и их hashes, usage/latency, ответы и точные
технические проверки. Hidden reasoning в артефакты не пишется.

`identical_model_messages=true` подтверждает равенство сообщений по каждому
кейсу во всех попытках; это не равенство токенизации или внутреннего reasoning
у разных провайдеров. Поля модели и лимиты фиксируются в manifest. Semantic
оценка остаётся отдельной и ручной. Три повтора — малая целевая выборка, не
оценка качества модели на всём продукте. Настройки сервиса и `.env` не меняются.

Для проверки новой модели в обычной цепочке используйте существующий runner:
`MASTER_MODEL=anthropic/claude-sonnet-4.6 PYTHONPATH=. .venv/bin/python -m evals.run_local --suite killer --session K-comparison --output evals/results/candidate-comparison`.
Переменная действует только на этот процесс; новая история при таком прогоне
у каждой модели своя. Проверки воспроизводимости replay:
`PYTHONPATH=. .venv/bin/python -m pytest tests/test_compare_models.py tests/test_eval_harness.py -q`.

Если исходных output-каталогов нет после свежего checkout, восстановите их
из закоммиченного архива (существующие каталоги не перезаписываются):

```bash
PYTHONPATH=. .venv/bin/python - <<'PY'
import gzip, json
from pathlib import Path
from evals.run_local import save_trace
archive = Path('../docs/evals/2026-09-05/structured-scope-fix/runs.json.gz')
runs = json.loads(gzip.decompress(archive.read_bytes()))
for name in ('structured-scope-final-comparison', 'structured-scope-final-targets'):
    out = Path('evals/results') / name
    if out.exists():
        continue
    out.mkdir(parents=True)
    (out / 'latest.json').write_text(json.dumps(runs[name]['manifest'], ensure_ascii=False))
    for turn in runs[name]['turns']:
        save_trace(out / (turn['case_id'] + '.json.gz'), turn)
PY
```

Результат первого парного сравнения:
[отчёт, все ответы и ограничения](../../docs/evals/2026-09-05/model-comparison/report.md).

## Нулевая выручка и ресурсы для оплаты

`payment_capacity` проверяет текущую GLM на исходных K19 и S15_10. Модель
фиксирована: другая настройка `MASTER_MODEL` вызывает отказ до live-вызовов.
Вариант `original` сохраняет реальный контекст и прежнюю историю. Дополнительные
`fixed_assets` и `cash_rich` используют те же вопросы с контролируемым setup:
подтверждением получения данных без аналитического вывода.

```bash
PYTHONPATH=. .venv/bin/python -m evals.payment_capacity --variant original --output evals/results/payment-replay-original
PYTHONPATH=. .venv/bin/python -m evals.payment_capacity --variant fixed_assets --output evals/results/payment-replay-fixed
PYTHONPATH=. .venv/bin/python -m evals.payment_capacity --variant cash_rich --output evals/results/payment-replay-cash
```

Это дополнительные eval-пробы, не новые реальные сведения о компании:
у АПРЕЛЬ в `cash_rich` взаимно переставлены деньги 49 тыс. ₽ и основные
средства 7,35 млн ₽. Нулевая выручка, активы 8,046 млн ₽, капитал, обязательства
и пропуски сохранены. В `fixed_assets` сохранён реальный состав баланса;
setup всё равно контролируемый. Все расчёты/представления пересобираются
продуктовым comparison capability из копии snapshot. Repository write и
доменных LLM-вызовов нет. Source bank не изменяется и исходный replay не
заменяется этими вариантами.

`variant.json` содержит признак synthetic, изменённую строку баланса и hash
эффективного набора документов; точные проверки используют именно этот набор.
Ручная оценка должна отличать отсутствие ресурсов, их неизвестную доступность
и отсутствие гарантии оплаты. Сумма капитала не прибавляется к активам.
До/после сравниваются равные frozen inputs, меняется только методология Master.
[Результаты и все попытки](../../docs/evals/2026-09-05/payment-capacity/report.md).

Подход eval-only дополнительно сверялся с официальными
[Agents](https://developers.openai.com/api/docs/guides/agents) и
[Agent evals](https://developers.openai.com/api/docs/guides/agent-evals);
продукт остаётся на LangChain согласно AGENTS.md.

## Влияние предыдущих ответов

`python -m evals.history_ablation --variant original|neutral|absent --output <new-directory>`
(из `backend/`, с `PYTHONPATH=.` и `.venv/bin/python`) запускает K19/S15_10
на текущей GLM, по три повтора. Меняется только assistant history: исходная,
нейтральные подтверждения или удалённые ответы. Пользовательские вопросы,
trusted context и методология сохраняются. Это eval-only диагностика.
[Результат: удаления истории недостаточно](../../docs/evals/2026-09-05/history-ablation/report.md).

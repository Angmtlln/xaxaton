# Chat latency — 05.09.2026

Baseline: `64431e5`. Файлы runtime, grounding, pipeline и adapter в исходном Docker-образе сверены с этим commit по SHA-256. Один последовательный диалог на фазу, реальные PostgreSQL/Groq/OpenRouter; Master во всех фазах `z-ai/glm-5.3-flash`. Это наблюдения отдельных прогонов, не p95 и не гарантия SLA. Внешняя нагрузка провайдеров менялась.

По последней инструкции пользователя verifier и repair исключены из synchronous chat path. Они остались только в `AGENT_GROUNDING_DEBUG=true` (по умолчанию false), без отдельной verifier-модели и без заменяющего LLM/regex/critic. Новый штатный путь: routing при необходимости → tool → trusted ToolResult → Master synthesis → deterministic structural validation → response.

## Before / after

Промежуточная фаза уже использует прямой dispatch и отключённый verifier/repair, но для измерения принудительно сохраняет legacy Summary. Финальная фаза дополнительно исключает Summary и использует provider.sort=throughput. Изменение общей длительности нельзя целиком приписывать одному фактору.

| Turn | Baseline, с | Без verifier/repair, с | Финал, с | Master calls до → после | Всего LLM attempts до → после | Tools до → после |
|---|---:|---:|---:|---:|---:|---:|
| 1. Проверь контрагента 6165169320 | 87.79 | 20.20 | 10.06 | 3 → 1 | 8 → 6 | 1 → 1 |
| 2. Почему это вообще плохо? | 13.05 | 22.34 | 15.06 | 2 → 1 | 2 → 1 | 0 → 0 |
| 3. Объясни проще | 13.83 | 10.97 | 4.85 | 2 → 1 | 2 → 1 | 0 → 0 |
| 4. А что у них с финансами? | 50.52 | 8.63 | 17.74 | 4 → 1 | 4 → 1 | 1 → 1 |
| 5. А с судами? | 119.39 | 13.83 | 10.53 | 4 → 1 | 4 → 1 | 1 → 1 |
| 6. Сравни 6165169320, 2901324364 и 0278949271 | 86.26 | 16.59 | 9.13 | 3 → 1 | 3 → 1 | 1 → 1 |

У full check четыре логических domain calls; в финальном прогоне один Groq request получил RateLimited и был повторён на запасной модели, поэтому в таблице 6 HTTP attempts вместе с Master, а не 5. Это существующая domain retry policy; итоговый domain run SUCCEEDED.

В финале 6/6 ответов имеют synthesis=model, grounding_status=not_requested, repair_attempts=0. Это **не** вердикт о semantic groundedness. Все ответы PARTIAL из-за покрытия данных; финальный full-check domain run SUCCEEDED, четыре доменных результата сохранены. В baseline full check и comparison завершились fallback после timeout verifier. В промежуточной фазе Summary попала в лимит Groq и вернула deterministic fallback; ускорение этой фазы не считается чистым качественным успехом.

Цели достигнуты для full check, rewrite, legal и comparison; «Почему?» (15,06 с) и finance (17,74 с) всё ещё выше ориентиров. На них практически всё время занимает единственный Master synthesis. Output budgets не уменьшались: synthesis/debug verifier/repair 4096, routing 512.

## Вызовы и reasoning tokens

| Turn | Verifier до → после | Repair до → после | Reasoning до → после (известные) | Fallback до → после |
|---|---:|---:|---:|---|
| 1 | 1 → 0 | 0 → 0 | 277 → 560 | True → False |
| 2 | 1 → 0 | 0 → 0 | 147 → 79 | False → False |
| 3 | 1 → 0 | 0 → 0 | 53 → 0 | False → False |
| 4 | 2 → 0 | 1 → 0 | 496 → 14 | False → False |
| 5 | 2 → 0 | 1 → 0 | 3593 → 15 | False → False |
| 6 | 1 → 0 | 0 → 0 | 0 → 9 | True → False |

Для timeout и Groq response без reasoning field токены неизвестны, поэтому это суммы только возвращённой usage, не полные итоги биллинга.

## Главные источники задержки baseline

1. GLM verifier: два timeout по 75 с (full check и comparison), обычные проверки 3,78–14,12 с.
2. Последовательный repair + повторный verifier: finance 7,94 + 9,05 с; legal 88,29 + 14,12 с. Только legal repair израсходовал 3337 reasoning tokens.
3. Master synthesis 5,74–20,06 с. Routing (1,21–1,65 с) и legacy Summary (2,13 с) меньше, но тоже лишние последовательные вызовы для простого chat full check.

Targeted finance/legal и comparison не содержат внутренних LLM. У финального full check остались четыре параллельных domain calls. DB/tool сами по себе не объясняют минутные задержки; в baseline finance чтение БД заняло около 4 с, в финале targeted tools — 11–20 мс.

## OpenRouter routing

Повторяли реальный captured prompt второго turn, тот же Master, max_tokens=4096, reasoning=low, JSON output. `throughput`: 9,716 / 7,075 / 11,238 с, все finish_reason=stop; медиана 9,716 с. `latency` и `latency` + preferred_max_latency=3.0: wall timeout 90 с. Один предварительный probe latency без общего wall timeout остановлен после >2 минут и не включён в таблицу. Выбран **throughput**; preferred_max_latency доступен настройкой, но выключен. Выборка мала, потребуется повторять при смене нагрузки/доступных провайдеров.

Параметры передаются стандартным ChatOpenAI extra_body. [OpenRouter provider routing](https://openrouter.ai/docs/features/provider-routing) и [latency guidance](https://openrouter.ai/docs/guides/best-practices/latency-and-performance). Adapter не раскрывает достоверный upstream provider name в этих ответах; OpenRouter — подтверждённый gateway, закрепление конкретного upstream не заявляется.

## Waterfall

Начало и длительность — wall-clock мс от начала turn. Вложенные spans tool/domain/DB **не суммировать** с их внутренними HTTP calls. Токены I/O/R: input/output/reasoning. `—` означает не возвращено или неприменимо, а не ноль. Для Groq без reasoning field число неизвестно.

В исходном baseline tracer не записал CancelledError: для turns 1 и 6 verifier исчерпал настроенные 75 с, usage/finish_reason отсутствуют. Это следует из таймаута runtime и хвоста wall time, а не из отдельного завершённого model event. Финальный tracer фиксирует и отменённые вызовы.

### before

| Turn | Stage / tool | Provider / model | Start, мс | Latency, мс | I / O / R | Finish / error |
|---|---|---|---:|---:|---|---|
| 1 | routing | openrouter / z-ai/glm-5.3-flash | 85 | 1654 | 2049 / 19 / 0 | tool_calls |
| 1 | db.get_latest_snapshot | backend | 1763 | 121 | — / — / — | — |
| 1 | tool / full_company_check | backend | 1763 | 5182 | — / — / — | — |
| 1 | db.create_run | backend | 1886 | 55 | — / — / — | — |
| 1 | db.save_facts | backend | 1942 | 25 | — / — / — | — |
| 1 | domain_http | groq / openai/gpt-oss-20b | 1967 | 1865 | 2658 / 350 / 5 | stop |
| 1 | domain_identity | openai/gpt-oss-20b | 1967 | 1865 | — / — / — | — |
| 1 | domain_reliability | openai/gpt-oss-120b | 1979 | 1861 | — / — / — | — |
| 1 | domain_http | groq / openai/gpt-oss-20b | 1980 | 1793 | 1546 / 392 / 127 | stop |
| 1 | domain_experience | openai/gpt-oss-20b | 1980 | 1794 | — / — / — | — |
| 1 | domain_http | groq / openai/gpt-oss-120b | 1980 | 1861 | 4373 / 380 / 79 | stop |
| 1 | domain_http | groq / qwen/qwen3.8-27b | 1980 | 2514 | 2338 / 603 / — | stop |
| 1 | domain_finance | qwen/qwen3.8-27b | 1980 | 2514 | — / — / — | — |
| 1 | legacy_summary | backend | 4494 | 2128 | — / — / — | — |
| 1 | domain_http | groq / openai/gpt-oss-120b | 4496 | 2126 | 3732 / 702 / 66 | stop |
| 1 | db.save_block_result | backend | 6624 | 143 | — / — / — | — |
| 1 | db.save_block_result | backend | 6768 | 26 | — / — / — | — |
| 1 | db.save_block_result | backend | 6793 | 16 | — / — / — | — |
| 1 | db.save_block_result | backend | 6809 | 13 | — / — / — | — |
| 1 | db.save_summary | backend | 6822 | 48 | — / — / — | — |
| 1 | db.save_statements | backend | 6870 | 56 | — / — / — | — |
| 1 | db.finish_run | backend | 6926 | 5 | — / — / — | — |
| 1 | synthesis | openrouter / z-ai/glm-5.3-flash | 6972 | 5737 | 5201 / 324 / 0 | stop |
| 1 | verifier (timeout, хвост turn) | OpenRouter / GLM | — | ≈75000 | — / — / — | timeout, usage отсутствует |
| 2 | synthesis | openrouter / z-ai/glm-5.3-flash | 36 | 8031 | 5218 / 353 / 26 | stop |
| 2 | verifier | openrouter / z-ai/glm-5.3-flash | 8088 | 4955 | 4453 / 133 / 121 | stop |
| 3 | synthesis | openrouter / z-ai/glm-5.3-flash | 20 | 9778 | 5539 / 161 / 0 | stop |
| 3 | verifier | openrouter / z-ai/glm-5.3-flash | 9915 | 3782 | 4312 / 65 / 53 | stop |
| 4 | tool / get_financial_data | backend | 73 | 4032 | — / — / — | — |
| 4 | db.get_latest_snapshot | backend | 91 | 3966 | — / — / — | — |
| 4 | synthesis | openrouter / z-ai/glm-5.3-flash | 4641 | 20063 | 4181 / 552 / 169 | stop |
| 4 | verifier | openrouter / z-ai/glm-5.3-flash | 24964 | 7192 | 3001 / 233 / 167 | stop |
| 4 | repair | openrouter / z-ai/glm-5.3-flash | 32801 | 7936 | 2619 / 415 / 11 | stop |
| 4 | verifier | openrouter / z-ai/glm-5.3-flash | 40773 | 9054 | 3001 / 166 / 149 | stop |
| 5 | tool / get_legal_data | backend | 110 | 226 | — / — / — | — |
| 5 | db.get_latest_snapshot | backend | 111 | 163 | — / — / — | — |
| 5 | synthesis | openrouter / z-ai/glm-5.3-flash | 430 | 11273 | 4441 / 356 / 9 | stop |
| 5 | verifier | openrouter / z-ai/glm-5.3-flash | 11725 | 5198 | 2825 / 143 / 87 | stop |
| 5 | repair | openrouter / z-ai/glm-5.3-flash | 16929 | 88291 | 2431 / 3650 / 3337 | stop |
| 5 | verifier | openrouter / z-ai/glm-5.3-flash | 105234 | 14121 | 2770 / 174 / 160 | stop |
| 6 | routing | openrouter / z-ai/glm-5.3-flash | 138 | 1212 | 3352 / 38 / 0 | tool_calls |
| 6 | tool / compare_companies | backend | 1380 | 92 | — / — / — | — |
| 6 | db.get_latest_snapshot | backend | 1382 | 70 | — / — / — | — |
| 6 | db.get_latest_snapshot | backend | 1460 | 3 | — / — / — | — |
| 6 | db.get_latest_snapshot | backend | 1463 | 7 | — / — / — | — |
| 6 | synthesis | openrouter / z-ai/glm-5.3-flash | 1487 | 9628 | 6837 / 514 / 0 | stop |
| 6 | verifier (timeout, хвост turn) | OpenRouter / GLM | — | ≈75000 | — / — / — | timeout, usage отсутствует |

### no-verifier

| Turn | Stage / tool | Provider / model | Start, мс | Latency, мс | I / O / R | Finish / error |
|---|---|---|---:|---:|---|---|
| 1 | db.get_latest_snapshot | backend | 8 | 104 | — / — / — | — |
| 1 | tool / full_company_check | backend | 8 | 10508 | — / — / — | — |
| 1 | db.create_run | backend | 113 | 35 | — / — / — | — |
| 1 | db.save_facts | backend | 148 | 17 | — / — / — | — |
| 1 | domain_http | groq / openai/gpt-oss-20b | 165 | 2113 | 2658 / 341 / 5 | stop |
| 1 | domain_identity | openai/gpt-oss-20b | 165 | 2113 | — / — / — | — |
| 1 | domain_reliability | openai/gpt-oss-120b | 389 | 2949 | — / — / — | — |
| 1 | domain_http | groq / openai/gpt-oss-120b | 390 | 2948 | 2837 / 594 / 337 | stop |
| 1 | domain_finance | openai/gpt-oss-120b | 391 | 2616 | — / — / — | — |
| 1 | domain_http | groq / qwen/qwen3.8-27b | 393 | 1240 | — / — / — | RateLimited |
| 1 | domain_http | groq / openai/gpt-oss-20b | 393 | 1789 | 1546 / 390 / 95 | stop |
| 1 | domain_experience | openai/gpt-oss-20b | 393 | 1789 | — / — / — | — |
| 1 | domain_http | groq / openai/gpt-oss-20b | 1632 | 205 | — / — / — | RateLimited |
| 1 | domain_http | groq / openai/gpt-oss-120b | 1838 | 1169 | 2231 / 363 / 87 | stop |
| 1 | legacy_summary | backend | 3339 | 7059 | — / — / — | — |
| 1 | domain_http | groq / openai/gpt-oss-120b | 3340 | 325 | — / — / — | RateLimited |
| 1 | domain_http | groq / openai/gpt-oss-20b | 3666 | 206 | — / — / — | RateLimited |
| 1 | domain_http | groq / qwen/qwen3.8-27b | 3872 | 211 | — / — / — | RateLimited |
| 1 | domain_http | groq / openai/gpt-oss-120b | 9091 | 1298 | — / — / — | RateLimited |
| 1 | db.save_block_result | backend | 10404 | 49 | — / — / — | — |
| 1 | db.save_block_result | backend | 10453 | 15 | — / — / — | — |
| 1 | db.save_block_result | backend | 10468 | 2 | — / — / — | — |
| 1 | db.save_block_result | backend | 10471 | 4 | — / — / — | — |
| 1 | db.save_summary | backend | 10474 | 18 | — / — / — | — |
| 1 | db.save_statements | backend | 10493 | 14 | — / — / — | — |
| 1 | db.finish_run | backend | 10507 | 2 | — / — / — | — |
| 1 | synthesis | openrouter / z-ai/glm-5.3-flash | 10554 | 9625 | 5170 / 594 / 0 | stop |
| 2 | synthesis | openrouter / z-ai/glm-5.3-flash | 22 | 22304 | 5759 / 540 / 7 | stop |
| 3 | synthesis | openrouter / z-ai/glm-5.3-flash | 21 | 10931 | 6304 / 275 / 10 | stop |
| 4 | db.get_latest_snapshot | backend | 11 | 17 | — / — / — | — |
| 4 | tool / get_financial_data | backend | 11 | 20 | — / — / — | — |
| 4 | synthesis | openrouter / z-ai/glm-5.3-flash | 50 | 8556 | 5046 / 527 / 27 | stop |
| 5 | db.get_latest_snapshot | backend | 9 | 9 | — / — / — | — |
| 5 | tool / get_legal_data | backend | 9 | 11 | — / — / — | — |
| 5 | synthesis | openrouter / z-ai/glm-5.3-flash | 37 | 13770 | 5423 / 353 / 3 | stop |
| 6 | tool / compare_companies | backend | 5 | 34 | — / — / — | — |
| 6 | db.get_latest_snapshot | backend | 6 | 8 | — / — / — | — |
| 6 | db.get_latest_snapshot | backend | 16 | 5 | — / — / — | — |
| 6 | db.get_latest_snapshot | backend | 22 | 15 | — / — / — | — |
| 6 | synthesis | openrouter / z-ai/glm-5.3-flash | 58 | 16511 | 7882 / 667 / 25 | stop |

### after

| Turn | Stage / tool | Provider / model | Start, мс | Latency, мс | I / O / R | Finish / error |
|---|---|---|---:|---:|---|---|
| 1 | db.get_latest_snapshot | backend | 3 | 8 | — / — / — | — |
| 1 | tool / full_company_check | backend | 3 | 2417 | — / — / — | — |
| 1 | db.create_run | backend | 11 | 4 | — / — / — | — |
| 1 | db.save_facts | backend | 15 | 6 | — / — / — | — |
| 1 | domain_http | groq / openai/gpt-oss-20b | 20 | 1397 | 2658 / 333 / 5 | stop |
| 1 | domain_identity | openai/gpt-oss-20b | 20 | 1397 | — / — / — | — |
| 1 | domain_http | groq / openai/gpt-oss-120b | 85 | 2314 | 4629 / 471 / 170 | stop |
| 1 | domain_reliability | openai/gpt-oss-120b | 85 | 2315 | — / — / — | — |
| 1 | domain_http | groq / qwen/qwen3.8-27b | 86 | 767 | — / — / — | RateLimited |
| 1 | domain_http | groq / openai/gpt-oss-20b | 86 | 1958 | 1546 / 556 / 310 | stop |
| 1 | domain_experience | openai/gpt-oss-20b | 86 | 1958 | — / — / — | — |
| 1 | domain_finance | openai/gpt-oss-20b | 86 | 1958 | — / — / — | — |
| 1 | domain_http | groq / openai/gpt-oss-20b | 853 | 1191 | 2231 / 414 / 75 | stop |
| 1 | db.save_block_result | backend | 2400 | 7 | — / — / — | — |
| 1 | db.save_block_result | backend | 2407 | 3 | — / — / — | — |
| 1 | db.save_block_result | backend | 2410 | 3 | — / — / — | — |
| 1 | db.save_block_result | backend | 2413 | 1 | — / — / — | — |
| 1 | db.save_statements | backend | 2414 | 4 | — / — / — | — |
| 1 | db.finish_run | backend | 2417 | 1 | — / — / — | — |
| 1 | synthesis | openrouter / z-ai/glm-5.3-flash | 2446 | 7601 | 5095 / 334 / 0 | stop |
| 2 | synthesis | openrouter / z-ai/glm-5.3-flash | 17 | 15014 | 5440 / 550 / 79 | stop |
| 3 | synthesis | openrouter / z-ai/glm-5.3-flash | 20 | 4821 | 5917 / 309 / 0 | stop |
| 4 | db.get_latest_snapshot | backend | 6 | 8 | — / — / — | — |
| 4 | tool / get_financial_data | backend | 6 | 11 | — / — / — | — |
| 4 | synthesis | openrouter / z-ai/glm-5.3-flash | 33 | 17680 | 4753 / 887 / 14 | stop |
| 5 | db.get_latest_snapshot | backend | 21 | 11 | — / — / — | — |
| 5 | tool / get_legal_data | backend | 21 | 19 | — / — / — | — |
| 5 | synthesis | openrouter / z-ai/glm-5.3-flash | 67 | 10445 | 5001 / 330 / 15 | stop |
| 6 | db.get_latest_snapshot | backend | 9 | 14 | — / — / — | — |
| 6 | tool / compare_companies | backend | 9 | 20 | — / — / — | — |
| 6 | db.get_latest_snapshot | backend | 25 | 1 | — / — / — | — |
| 6 | db.get_latest_snapshot | backend | 26 | 1 | — / — / — | — |
| 6 | synthesis | openrouter / z-ai/glm-5.3-flash | 43 | 9056 | 7476 / 662 / 9 | stop |

## Качество и сохранённые границы

Структурные regression checks сохраняют строгие INN/tool args, output schema, allowlisted artifacts, provenance/evidence, backend hydration метрик/графиков/URL, hard stops и NO_DATA/PARTIAL. Повторный contextual вопрос не запускает tool; повторный finance/legal использует нужный раздел. Явное обновление и запрос года снова запускают targeted capability.

Ручное чтение финальных ответов выявило semantic defects: в full check сказано «банкротных производств нет» без соответствующего наблюдения; finance называет компанию «почти спящей» из нулевой выручки; «Почему?»/rewrite утверждают риск «выше среднего/обычного» без базы сравнения. Legal подтягивает капитал из предыдущей прозы вне своего текущего targeted context. Поэтому groundedness/conversation quality **не объявлены прошедшими**. Ответы сохранены в JSON для ручной оценки; никакой новый синхронный проверяющий механизм вместо удалённого не добавлен.

Начатый до изменения задания эксперимент отдельного verifier остановлен по новой инструкции. В production отдельная verifier-модель не добавлена.

## DB / compatibility

Миграции не нужны: схема/SQL/repository не менялись. Chat run сохраняет факты, четыре domain blocks и audit statements; summary_model=not_requested, строки run_summaries нет. Проверено в живой БД: baseline run d1210239-88b2-42cb-b34a-e0ae831e69f0 — 4 blocks/1 summary; final f91f2e8f-0a29-48cc-a6ed-3728215a406f — 4 blocks/0 summaries. Legacy run_check по умолчанию include_summary=True; /api/v1/checks и /report продолжают полный pipeline. Ссылка из chat открывает /report?inn=... и запускает отдельный legacy check.

## Воспроизведение

```bash
cd backend
# На macOS локальный PostgreSQL может перехватывать 5432; используем сеть Compose.
docker compose exec -e BENCH_OUTPUT=/tmp/chat-latency.json api python scripts/benchmark_chat_latency.py
# Промежуточный замер с legacy Summary:
docker compose exec -e BENCH_INCLUDE_SUMMARY=1 -e BENCH_OUTPUT=/tmp/with-summary.json api python scripts/benchmark_chat_latency.py
# Debug/eval старого bounded verifier/repair (не обычный chat):
docker compose exec -e AGENT_GROUNDING_DEBUG=true -e BENCH_OUTPUT=/tmp/debug.json api python scripts/benchmark_chat_latency.py
# Для routing replay сначала сохранить prompt fixtures:
docker compose exec -e BENCH_CAPTURE_PROMPTS=1 -e BENCH_OUTPUT=/tmp/capture.json api python scripts/benchmark_chat_latency.py
docker compose exec api python scripts/benchmark_openrouter_routing.py --prompt-file /tmp/capture-prompts-2.json --output /tmp/routing.json
PYTHONPATH=. .venv/bin/pytest -q
```

Измерение охватывает runtime, реальные внешние API, tools и audit DB; browser/network overhead chat HTTP измеряется отдельно в live API smoke. Проверки: полный backend regression 204 passed, один существующий Starlette/AnyIO DeprecationWarning; Docker build; git diff --check. Данные: [JSON waterfall и ответы](benchmarks/chat-latency-2026-09-05.json).

Ручная проверка: содержательность и осторожность «Почему?»/«Объясни проще»; отсутствие неподтверждённых выводов; источники, «Полный анализ» и состояния неполноты в браузере. UI-код не изменялся.

## Проверка пересобранного HTTP-сервиса

Docker API пересобран и перезапущен. GET /health, / и /report?inn=6165169320 — HTTP 200.
Четыре последовательных POST /api/v1/chat/messages: full check 12,834 с,
«Почему?» 10,537 с, finance 11,698 с, повтор finance 6,235 с.
Везде один Master call, zero verifier/repair, synthesis=model; tool counts 1/0/1/0.
Разница HTTP wall и server runtime metadata составила 31–517 мс (включая
создание adapter и сериализацию); подробный runtime waterfall не выдаётся за
сетевой/browser timing.
Legacy POST /api/v1/checks также выполнен: Summary и narrative присутствуют,
summary_model не not_requested. Его длительность — 4.477 с.

## Изменённые файлы

- app/agent/runtime.py, grounding.py, models.py: optional debug и default без verifier/repair, прямой dispatch, reuse, точные identifiers для comparison.
- app/agent/master_model.py, app/config.py, .env.example: OpenRouter routing и AGENT_GROUNDING_DEBUG=false.
- app/domain/pipeline.py, app/agent/tools.py: include_summary=False только в chat.
- scripts/benchmark_chat_latency.py, benchmark_openrouter_routing.py: воспроизводимые live измерения; smoke_multiturn.py и smoke_openrouter_master.py учитывают новый default.
- tests/test_agent_latency.py и обновлённые runtime/multiturn/chat/provider tests: default path и debug regression.
- README.md, docs/AI_INDEX.md, AGENT_FIRST_ARCHITECTURE.md, MULTI_TURN_CHAT.md, этот отчёт и JSON: актуальное поведение и измерения.

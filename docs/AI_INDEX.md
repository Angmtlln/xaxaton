# AI Index

Навигация для разработчиков и AI-агентов. Начинай отсюда после чтения
корневого [`AGENTS.md`](../AGENTS.md), затем открывай только материалы,
относящиеся к текущей задаче.

## Быстрый маршрут

| Если задача про… | Сначала прочитать | Основные файлы |
|---|---|---|
| agent-first продукт, Master Agent, tools, chat API, rich UI | [`AGENT_FIRST_ARCHITECTURE.md`](AGENT_FIRST_ARCHITECTURE.md) | `backend/app/agent/runtime.py`, `backend/app/agent/langchain_tools.py`, `backend/app/agent/tools.py`, `backend/app/agent/models.py`, `backend/app/agent/response.py`, `backend/app/agent/conversations.py`, `backend/app/agent/finance.py`, `backend/app/agent/legal.py`, `backend/app/agent/comparison.py`, `backend/app/api/routes/chat.py`, `frontend/js/chat/main.js` |
| выбор инструмента по смыслу, отрицания, сумма vs ИНН | [`CHAT_ROUTING.md`](CHAT_ROUTING.md) | `backend/app/agent/runtime.py`, `langchain_tools.py`, `tests/test_agent_request_routing.py`, `backend/scripts/smoke_agent_routing.py` |
| продукт, скоуп, критерии успеха | [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md), [`product_materials.md`](../product_materials.md) | `project_description.md`, `hypotheses.md` |
| продуктовые гипотезы и приоритеты | [`hypotheses.md`](../hypotheses.md) | `product_materials.md` |
| состав четырёх блоков анализа | [`blocks_summary_design.md`](../blocks_summary_design.md) | `backend/app/domain/facts.py`, `backend/app/llm/prompts.py` |
| запуск и общая архитектура backend | [`backend/README.md`](../backend/README.md) | `backend/app/domain/pipeline.py`, `backend/app/main.py`, `backend/app/api/routes/` |
| факты, расчёты, полнота данных | `backend/app/domain/facts.py` | `backend/app/infrastructure/mongo.py`, `backend/tests/test_facts.py` |
| LLM, grounding, guardrails | `backend/app/llm/agents.py`, `backend/app/llm/prompts.py` | `backend/app/llm/groq_client.py`, `backend/tests/test_groq_and_grounding.py` |
| API и формат ответа | `backend/app/api/routes/`, `backend/app/api/schemas.py` | `backend/app/domain/pipeline.py`, Swagger `/docs` |
| MCP-доступ к карточкам, запуск и откат | [`MCP_DATA_ACCESS.md`](MCP_DATA_ACCESS.md) | `backend/app/mcp_data/`, `backend/app/infrastructure/company_reader.py`, `company_postgres.py`, `backend/scripts/setup_mcp_access.py` |
| PostgreSQL и аудит | [`backend/docs/db_design.md`](../backend/docs/db_design.md), `backend/db/schema.sql` | `backend/app/infrastructure/repository.py`, `backend/scripts/load_snapshot.py` |
| поиск по деятельности / ОКВЭД | [`ACTIVITY_SEARCH.md`](ACTIVITY_SEARCH.md) | `backend/app/agent/shortlist.py`, `backend/app/infrastructure/repository.py`, `backend/db/migrations/005_shortlist_activity.sql` |
| выбор N по показателям, «из найденных», подтверждение порядка | [`ACTIVITY_SEARCH.md`](ACTIVITY_SEARCH.md), [`RANKING_ACCEPTANCE.md`](RANKING_ACCEPTANCE.md) | `backend/app/agent/ranking.py`, `shortlist.py`, `runtime.py`, `backend/tests/test_ranking.py` |
| агентный подбор под задачу, мини-сводки, финалисты | [`COUNTERPARTY_SELECTION.md`](COUNTERPARTY_SELECTION.md) | `backend/app/agent/selection.py`, `selection_runtime.py`, `selection_models.py`, `backend/tests/test_counterparty_selection.py` |
| подборка по критериям, боковая навигация | [`AMIR_INTEGRATION.md`](AMIR_INTEGRATION.md) | `backend/app/agent/shortlist.py`, `backend/db/migrations/004_company_shortlist.sql`, `frontend/js/chat/navigation.js` |
| экспорт результата в PDF | [`PDF_EXPORT.md`](PDF_EXPORT.md) | `backend/app/agent/pdf_export.py`, `backend/app/api/routes/exports.py`, `frontend/pdf.html` |
| рабочий интерфейс демо | [`CHAT_UI.md`](CHAT_UI.md), `frontend/index.html`, `frontend/report.html` | `frontend/js/chat/main.js`, `frontend/js/report/main.js`, `frontend/css/chat.css` |
| визуальный React-прототип | [`design/prototype/README.md`](../design/prototype/README.md) | `design/prototype/app/` |
| тестирование всего прохода | `backend/tests/test_pipeline_mock.py` | `backend/tests/conftest.py`, `backend/scripts/demo_offline.py` |
| содержательные behavioral evals Master | [`AGENT_EVALS.md`](../AGENT_EVALS.md), [`evals/README.md`](../backend/evals/README.md) | `backend/evals/scenarios.json`, `bank.py`, `run_local.py`, `graders.py`, `judge.py` |
| внешние новости только при full check | [`WEB_NEWS.md`](WEB_NEWS.md) | `backend/app/agent/news.py`, `runtime.py`, `master_model.py`, `tests/test_company_news.py` |
| безопасность агента, prompt injection и лимиты демо | [`Правки 07.09.2026`](security/DEMO_HARDENING_2026-09-07.md), [`аудит`](security/AGENT_SECURITY_AUDIT_2026-09-07.md) | Разделение ролей, входные лимиты; роза сохраняет мнение AI по продуктовому решению; авторизация исключена из scope демо |

Последняя проверка исправлений structured context без изменения prompt:
[коэффициенты исков, source commentary и учредители](evals/2026-09-05/structured-scope-fix/report.md).
В отчёте технические результаты отделены от оставшихся ошибок рассуждения.

Исправление сбоя мини-сводок подбора и утверждений об актуальности снимков:
[selection-fix](evals/2026-09-06/selection-fix/report.md). Одна попытка исправления
длины в общем бюджете, без ослабления проверки ИНН/evidence; версия методологии
`0.3.6-snapshot-freshness`.

Парное сравнение GLM и Claude на неизменных сообщениях:
[missing и source conflict](evals/2026-09-05/model-comparison/report.md).
`backend/evals/compare_models.py` — eval-only повтор контекстных реплик;
оснований переключать рабочую модель по этой выборке не получено.

## Источники истины и свежесть

1. `AGENTS.md` — обязательные рабочие правила и AI-инварианты.
2. `docs/AGENT_FIRST_ARCHITECTURE.md` — целевая agent-first архитектура и
   порядок развития продукта; она не доказывает, что перечисленные компоненты
   уже реализованы.
3. `docs/PROJECT_CONTEXT.md` — стабильная постановка кейса, MVP и критерии
   успеха.
4. Исполняемый код и тесты — источник истины о текущей реализации.
5. `backend/README.md` — операционные команды и обзор backend.
6. `README.md` и `project_description.md` — входной и продуктовый обзоры,
   сверенные с кодом 06.09.2026. Датированные eval/latency-отчёты сохраняют
   исходные результаты и не подтверждают состояние нового запуска.
7. `product_materials.md`, `hypotheses.md`, `blocks_summary_design.md` —
   обоснование продуктовых решений; это не runtime-документация.

При расхождении документа с кодом не молча выбирай одну версию: проверь тесты,
зафиксируй расхождение и обнови ближайший к изменению документ.

## Архитектурный путь данных

```text
POST /api/v1/chat/messages
  -> MasterAgentRuntime
  -> LangChain create_agent / LangGraph runtime
  -> standard ChatOpenAI adapter -> OpenRouter -> z-ai/glm-5.3-flash
  -> LangChain StructuredTool adapter
  -> ToolRegistry: full_company_check | get_financial_data | get_legal_data | compare_companies
     | find_companies | select_counterparties
  -> run_check() | build_finance() | build_reliability() | build_comparison()
  -> normalized ToolResult: metrics / series / events / statuses / policy / evidence
  -> естественный ответ Master + необязательный allowlisted artifact
  -> deterministic structural validation
  -> optional eval/debug verifier/repair (AGENT_GROUNDING_DEBUG=false по умолчанию)
  -> backend hydration AssistantResponse
  -> InMemorySaver: bounded messages + отдельный trusted_context по conversation_id
  -> allowlisted UIBlock renderer

ИНН
  -> repository -> выбранный reader (Docker: MCP-клиент -> MCP-сервер)
  -> последний снимок карточки в PostgreSQL
  -> нормализация Mongo Extended JSON
  -> детерминированные факты + паспорт полноты
  -> 4 параллельных доменных LLM-блока
  -> итоговая LLM-сводка
  -> grounding + guardrails
  -> audit.* + REST-ответ
  -> веб-отчёт
```

Границы компонентов:

- `facts.py` отвечает за вычисляемую истину;
- `agent/runtime.py` отвечает за LangChain harness, budgets и deterministic
  fallback;
- `agent/langchain_tools.py` экспортирует framework-agnostic domain tool в
  LangChain и сохраняет проверенный `ToolResult` artifact;
- `prompts.py` отвечает за правила интерпретации и формат ответа модели;
- `agents.py` отвечает за вызовы, валидацию, fallback, grounding и guardrails;
- `pipeline.py` отвечает за порядок прохода и сохранение результатов;
- `infrastructure/repository.py` отвечает за SQL, но не за продуктовые выводы;
- `api/routes/` отвечает за HTTP-контракт, `main.py` — только за сборку приложения;
- `frontend/` отвечает за рабочий интерфейс поверх API и раздаётся тем же сервисом;
- `design/prototype/` — источник дизайна, в рантайме не участвует.

## Текущий функциональный статус

Сверка основных возможностей с кодом: 06.09.2026. Это статус реализации,
не новый live-прогон и не утверждение о полном semantic PASS:

- реализован анализ одного ИНН;
- реализованы четыре блока фактов, итоговая сводка, grounding, guardrails,
  аудит и fallback без LLM;
- реализованы agent-first чат, legacy-отчёт, диаграммы и паспорт полноты;
- восстановлен красный лендинг со свободным первым запросом и подставляемыми
  подсказками; Master уточняет задачу без ИНН и tools, выбирает контекстные кнопки
  продолжения; frontend поддерживает заголовки и жирный текст ([CHAT_UI.md](CHAT_UI.md));
- реализован conversation-first full check: сообщение с одним валидным ИНН,
  LangChain `create_agent` + underlying LangGraph, `full_company_check`,
  естественный post-tool ответ Master, structural validation и backend
  hydration; компактный `company_summary`
  перед основным сообщением, выборочные артефакты и свёрнутые источники;
- отдельный React/Vinext-прототип содержит моковый интерфейс;
- реализованы multi-turn context в InMemorySaver, одна active_company, targeted
  finance/legal без полного pipeline, отдельный trusted context и контекстные
  follow-up без повторного tool call;
- реализовано сравнение 2–5 контрагентов: `compare_companies` собирает все
  компании одним вызовом; сначала показаны компактные карточки и короткий вывод,
  затем таблица `comparison_table`. Карточки и таблицу строит backend, а состояние
  сравнения хранится отдельно от `trusted_context` и не перезаписывает активную
  компанию;
- реализован поиск по деятельности в основном и дополнительных ОКВЭД одновременно
  с финансовыми условиями; источники совпадений показывает backend ([ACTIVITY_SEARCH.md](ACTIVITY_SEARCH.md));
- реализован подбор под цель сотрудничества: до 50 кандидатов, пакетные LLM-сводки,
  до пяти финалистов и сравнение; факты и интерпретации хранятся раздельно
  ([COUNTERPARTY_SELECTION.md](COUNTERPARTY_SELECTION.md));
- штатный online path Master — `z-ai/glm-5.3-flash` через OpenRouter;
  доменные агенты независимо используют Groq;
- full-check synthesis включает OpenRouter web plugin: 0–4 новости возвращаются
  отдельно в `external_news`, без изменения внутренних фактов и оценок;
  frontend показывает их горизонтальной полосой внизу полного ответа;
- компактный `company_summary` содержит четыре проверенные метрики, банк/ЗСК
  и качественный профиль AI по четырём направлениям с пояснениями; банковские оценки независимы;
- реализованы PDF-экспорт, кросс-проверка внутренних связей и граф по запросу;
  при числе рёбер > 2 полная проверка предлагает открыть граф;
- persistent history в БД, универсальный name resolution, отдельный deal-risk
  tool и потоковая выдача текста Master не реализованы; публичные статусы
  этапов передаются NDJSON;
- реализован MCP-сервер чтения и клиент; Docker использует MCP по умолчанию,
  локальный Python — direct. Все шесть чтений карточек/поиска переключаются
  единым reader; запись аудита остаётся отдельно, скрытого fallback на SQL нет;
- банковские интеграции и большая база не относятся к готовому текущему проходу.

Перед изменением статуса сверяйся с кодом и обновляй этот раздел в том же
коммите.

## Команды входа

Backend и тесты:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

Офлайн-демо:

```bash
cd backend
LLM_MOCK=true python scripts/demo_offline.py --inn 6165169320
```

Рабочий сервис после настройки БД:

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

Agent-first chat:

```bash
curl -X POST http://localhost:8000/api/v1/chat/messages \
     -H 'Content-Type: application/json' \
     -d '{"message":"Проверь контрагента 6165169320"}'
```

UI-прототип:

```bash
cd design/prototype
npm install
npm run dev
```

## Документационный долг

- Не дублируй большие таблицы полей: держи раскладку в
  `blocks_summary_design.md`, а реализацию — в `facts.py`.
- Не записывай подтверждённые метрики без команды воспроизведения или теста.
- Помечай датой снимки результатов, но не добавляй дату в стабильные правила.
- При добавлении новой подсистемы дополни этот индекс одной строкой маршрута и
  ссылкой на её источник истины.

## Multi-turn entrypoints и проверки

- `backend/app/agent/conversations.py`: LangChain AgentState, InMemorySaver и
  lifecycle сессий (30 минут бездействия, 100 диалогов, последние 6 turns),
  отдельно bounded messages, trusted tool context и условия сделки.
- `backend/app/agent/finance.py`, `legal.py`: targeted snapshot adapters;
  `targeted_models.py`: framework-agnostic контракты нормализованных данных.
- `runtime.py`, `master_model.py`, `langchain_tools.py`, `prompt.py`: provider-neutral
  `create_agent`, выбранный при создании conversation Master provider/model,
  до 6 model calls с debug-проверкой, до двух разных targeted чтений в auto path,
  recursion limit 16; неверный routing использует
  ограниченный deterministic fallback.
- `response.py`: строгая связь evidence с фактами, hydration verified data,
  deterministic policy-блоки, отдельный `leading_artifact` только для full
  check, conversational `message` Master и выборочные `blocks`. Узкие ответы
  не повторяют карточку компании.
- `backend/app/agent/synthesis.py`: нормализация metrics, series, events,
  statuses, coverage, policy и evidence для Master и trusted context.
- `backend/app/agent/grounding.py`: optional eval/debug проверка company-specific утверждений,
  точные URL/ИНН/ОГРН и максимум одна repair-попытка.
- `tests/test_agent_multiturn.py`, `tests/test_agent_runtime.py`: routing, state,
  tool/result turns, budgets и fallback.
- `tests/test_grounding_behavior.py`: естественное рассуждение, подмена факта в
  истории, неизвестные URL/идентификаторы, repair/fallback и rewrite fast path.
- `tests/test_financial_capability.py`, `tests/test_legal_capability.py`,
  `tests/test_targeted_response.py`, `tests/test_chat_api.py`: данные, grounding и API.
- Клиент `frontend/js/chat/main.js` передаёт conversation_id и сохраняет текущий
  диалог в sessionStorage вкладки. Новый диалог сбрасывает клиентский контекст.
- Подробности, API-пример и проверка: [MULTI_TURN_CHAT.md](MULTI_TURN_CHAT.md).

## Доступность исходных данных

- [DATA_COVERAGE.md](DATA_COVERAGE.md): source → ToolResult → Master, формулы,
  тематические разделы и страницы существующих tools.
- [DATA_SOURCE_INVENTORY.md](DATA_SOURCE_INVENTORY.md): все 19 разделов 100 снимков.
- [GET_FULL_REPORT_FIELDS.md](GET_FULL_REPORT_FIELDS.md): предоставленная расшифровка.

## Latency chat path

- [CHAT_LATENCY.md](CHAT_LATENCY.md): before/after waterfall, provider routing и
  команды воспроизведения; `scripts/benchmark_chat_latency.py`.
- По умолчанию verifier/repair отключены. Прямой dispatch простых команд,
  reuse проверенного контекста и Master synthesis сохраняют структурную валидацию.
- Свободные вопросы об одной компании при доступной модели используют выбор
  инструмента или ответа по смыслу в существующем Master-вызове; одиночные слова
  больше не фиксируют единственный tool. Финансы и юридические данные можно прочитать
  за один ход; ввод до 4000 символов, истечение сессии возвращает черновик.
  Границы — в [CHAT_ROUTING](CHAT_ROUTING.md).
- Chat `full_company_check` пропускает только legacy Summary; `/api/v1/checks`
  и `/report` продолжают вызывать её. Схема БД не меняется.

## Risk Playbook v0.3.2: данные и проверка

- [DATA_COVERAGE](DATA_COVERAGE.md): source → ToolResult → Master, расчёты и границы.
- [PLAYBOOK_VALIDATION](PLAYBOOK_VALIDATION.md): регрессия, Docker, live tokens/latency и оставшиеся поведенческие FAIL.
- Каноническая runtime-методология: `backend/app/agent/RISK_PLAYBOOK.md`.
- [Нулевая выручка и ресурсы для оплаты](evals/2026-09-05/payment-capacity/report.md):
  один уточнённый абзац методологии, исходные вопросы и дополнительные пробы
  с разным составом активов на текущей GLM; полный semantic PASS не подразумевается.
- [Semantic fixes: K15–K20 и traps](evals/2026-09-05/semantic-fix-v03/report.md):
  нулевой знаменатель, количество кредиторов и смена критерия; исходные данные,
  последовательные live-прогоны и оставшиеся semantic failures.
- [Влияние истории на ошибку оценки ресурсов](evals/2026-09-05/history-ablation/report.md):
  18 GLM-вызовов с одинаковыми данными; ошибка воспроизводится без прежних
  ответов assistant. Runtime и методология не менялись.

## Внутренняя кросс-проверка

[COMPANY_CONNECTIONS.md](COMPANY_CONNECTIONS.md): аудит датасета, ограниченный
поиск связей, краткий обзор соседей и миграция read-only view.

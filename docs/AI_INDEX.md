# AI Index

Навигация для разработчиков и AI-агентов. Начинай отсюда после чтения
корневого [`AGENTS.md`](../AGENTS.md), затем открывай только материалы,
относящиеся к текущей задаче.

## Быстрый маршрут

| Если задача про… | Сначала прочитать | Основные файлы |
|---|---|---|
| agent-first продукт, Master Agent, tools, chat API, rich UI | [`AGENT_FIRST_ARCHITECTURE.md`](AGENT_FIRST_ARCHITECTURE.md) | `backend/app/agent/runtime.py`, `backend/app/agent/langchain_tools.py`, `backend/app/agent/tools.py`, `backend/app/agent/models.py`, `backend/app/agent/response.py`, `backend/app/agent/conversations.py`, `backend/app/agent/finance.py`, `backend/app/agent/legal.py`, `backend/app/agent/comparison.py`, `backend/app/api/routes/chat.py`, `frontend/js/chat/main.js` |
| продукт, скоуп, критерии успеха | [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md), [`product_materials.md`](../product_materials.md) | `project_description.md`, `hypotheses.md` |
| продуктовые гипотезы и приоритеты | [`hypotheses.md`](../hypotheses.md) | `product_materials.md` |
| состав четырёх блоков анализа | [`blocks_summary_design.md`](../blocks_summary_design.md) | `backend/app/domain/facts.py`, `backend/app/llm/prompts.py` |
| запуск и общая архитектура backend | [`backend/README.md`](../backend/README.md) | `backend/app/domain/pipeline.py`, `backend/app/main.py`, `backend/app/api/routes/` |
| факты, расчёты, полнота данных | `backend/app/domain/facts.py` | `backend/app/infrastructure/mongo.py`, `backend/tests/test_facts.py` |
| LLM, grounding, guardrails | `backend/app/llm/agents.py`, `backend/app/llm/prompts.py` | `backend/app/llm/groq_client.py`, `backend/tests/test_groq_and_grounding.py` |
| API и формат ответа | `backend/app/api/routes/`, `backend/app/api/schemas.py` | `backend/app/domain/pipeline.py`, Swagger `/docs` |
| PostgreSQL и аудит | [`backend/docs/db_design.md`](../backend/docs/db_design.md), `backend/db/schema.sql` | `backend/app/infrastructure/repository.py`, `backend/scripts/load_snapshot.py` |
| рабочий интерфейс демо | `frontend/index.html`, `frontend/report.html` | `frontend/js/chat/main.js`, `frontend/js/report/main.js`, `frontend/css/chat.css` |
| визуальный React-прототип | [`design/prototype/README.md`](../design/prototype/README.md) | `design/prototype/app/` |
| тестирование всего прохода | `backend/tests/test_pipeline_mock.py` | `backend/tests/conftest.py`, `backend/scripts/demo_offline.py` |
| содержательные behavioral evals Master | [`AGENT_EVALS.md`](../AGENT_EVALS.md), [`evals/README.md`](../backend/evals/README.md) | `backend/evals/scenarios.json`, `bank.py`, `run_local.py`, `graders.py`, `judge.py` |

## Источники истины и свежесть

1. `AGENTS.md` — обязательные рабочие правила и AI-инварианты.
2. `docs/AGENT_FIRST_ARCHITECTURE.md` — целевая agent-first архитектура и
   порядок развития продукта; она не доказывает, что перечисленные компоненты
   уже реализованы.
3. `docs/PROJECT_CONTEXT.md` — стабильная постановка кейса, MVP и критерии
   успеха.
4. Исполняемый код и тесты — источник истины о текущей реализации.
5. `backend/README.md` — операционные команды и обзор backend.
6. `project_description.md` — подробный промежуточный снимок состояния на
   03.09.2026; числовые результаты и план в нём могут устаревать.
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
  -> run_check() | build_finance() | build_reliability() | build_comparison()
  -> normalized ToolResult: metrics / series / events / statuses / policy / evidence
  -> естественный ответ Master + необязательный allowlisted artifact
  -> deterministic structural validation
  -> optional eval/debug verifier/repair (AGENT_GROUNDING_DEBUG=false по умолчанию)
  -> backend hydration AssistantResponse
  -> InMemorySaver: bounded messages + отдельный trusted_context по conversation_id
  -> allowlisted UIBlock renderer

ИНН
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

По состоянию кода на момент создания индекса:

- реализован анализ одного ИНН;
- реализованы четыре блока фактов, итоговая сводка, grounding, guardrails,
  аудит и fallback без LLM;
- реализованы agent-first чат, legacy-отчёт, диаграммы и паспорт полноты;
- реализован conversation-first full check: сообщение с одним валидным ИНН,
  LangChain `create_agent` + underlying LangGraph, `full_company_check`,
  естественный post-tool ответ Master, structural validation и backend
  hydration; компактный `company_summary`
  перед основным сообщением, выборочные артефакты и свёрнутые источники;
- отдельный React/Vinext-прототип содержит моковый интерфейс;
- реализованы multi-turn context в InMemorySaver, одна active_company, targeted
  finance/legal без полного pipeline, отдельный trusted context и контекстные
  follow-up без повторного tool call;
- реализовано сравнение двух-трёх контрагентов: `compare_companies` собирает все
  компании одним вызовом, таблицу `comparison_table` строит backend, а состояние
  сравнения хранится отдельно от `trusted_context` и не перезаписывает активную
  компанию;
- штатный online path Master — `z-ai/glm-5.3-flash` через OpenRouter;
  доменные агенты независимо используют Groq;
- persistent history в БД, name resolution, deal risk и streaming не реализованы;
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
  до 5 model calls, 1 domain call, recursion limit 12; неверный routing использует
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
  reuse finance/legal и один Master synthesis сохраняют структурную валидацию.
- Chat `full_company_check` пропускает только legacy Summary; `/api/v1/checks`
  и `/report` продолжают вызывать её. Схема БД не меняется.

## Risk Playbook v0.2: данные и проверка

- [DATA_COVERAGE](DATA_COVERAGE.md): source → ToolResult → Master, расчёты и границы.
- [PLAYBOOK_VALIDATION](PLAYBOOK_VALIDATION.md): регрессия, Docker, live tokens/latency и оставшиеся поведенческие FAIL.
- Каноническая runtime-методология: `backend/app/agent/RISK_PLAYBOOK.md`.

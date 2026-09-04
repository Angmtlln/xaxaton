# AI Index

Навигация для разработчиков и AI-агентов. Начинай отсюда после чтения
корневого [`AGENTS.md`](../AGENTS.md), затем открывай только материалы,
относящиеся к текущей задаче.

## Быстрый маршрут

| Если задача про… | Сначала прочитать | Основные файлы |
|---|---|---|
| agent-first продукт, Master Agent, tools, chat API, rich UI | [`AGENT_FIRST_ARCHITECTURE.md`](AGENT_FIRST_ARCHITECTURE.md) | `backend/app/agent/runtime.py`, `backend/app/agent/langchain_tools.py`, `backend/app/agent/tools.py`, `backend/app/agent/models.py`, `backend/app/agent/response.py`, `backend/app/agent/conversations.py`, `backend/app/agent/finance.py`, `backend/app/agent/legal.py`, `backend/app/main.py`, `backend/static/landing.js` |
| продукт, скоуп, критерии успеха | [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md), [`product_materials.md`](../product_materials.md) | `project_description.md`, `hypotheses.md` |
| продуктовые гипотезы и приоритеты | [`hypotheses.md`](../hypotheses.md) | `product_materials.md` |
| состав четырёх блоков анализа | [`blocks_summary_design.md`](../blocks_summary_design.md) | `backend/app/domain/facts.py`, `backend/app/llm/prompts.py` |
| запуск и общая архитектура backend | [`backend/README.md`](../backend/README.md) | `backend/app/pipeline.py`, `backend/app/main.py` |
| факты, расчёты, полнота данных | `backend/app/domain/facts.py` | `backend/app/mongo.py`, `backend/tests/test_facts.py` |
| LLM, grounding, guardrails | `backend/app/llm/agents.py`, `backend/app/llm/prompts.py` | `backend/app/llm/groq_client.py`, `backend/tests/test_groq_and_grounding.py` |
| API и формат ответа | `backend/app/main.py`, `backend/app/api/schemas.py` | `backend/app/pipeline.py`, Swagger `/docs` |
| PostgreSQL и аудит | [`backend/docs/db_design.md`](../backend/docs/db_design.md), `backend/db/schema.sql` | `backend/app/repository.py`, `backend/scripts/load_snapshot.py` |
| рабочий интерфейс демо | `backend/static/index.html`, `backend/static/report.html` | `backend/static/landing.js`, `backend/static/report.js`, `backend/static/styles.css` |
| визуальный React-прототип | [`alfa-counterparty-prototype/README.md`](../alfa-counterparty-prototype/README.md) | `alfa-counterparty-prototype/app/` |
| тестирование всего прохода | `backend/tests/test_pipeline_mock.py` | `backend/tests/conftest.py`, `backend/scripts/demo_offline.py` |

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
  -> ChatGroq native tool call
  -> LangChain StructuredTool adapter
  -> ToolRegistry: full_company_check | get_financial_data | get_legal_data
  -> run_check() | build_finance() | build_reliability()
  -> compact ToolResult -> Master synthesis (выбор наблюдений и артефакта)
  -> backend hydration AssistantResponse
  -> InMemorySaver: bounded messages + active_company по conversation_id
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
- `repository.py` отвечает за SQL, но не за продуктовые выводы;
- `backend/static/` отвечает за рабочий интерфейс поверх API.

## Текущий функциональный статус

По состоянию кода на момент создания индекса:

- реализован анализ одного ИНН;
- реализованы четыре блока фактов, итоговая сводка, grounding, guardrails,
  аудит и fallback без LLM;
- реализованы agent-first чат, legacy-отчёт, диаграммы и паспорт полноты;
- реализован conversation-first full check: сообщение с одним валидным ИНН,
  LangChain `create_agent` + underlying LangGraph, `full_company_check`,
  post-tool synthesis и backend hydration; компактный `company_summary`
  перед основным сообщением, выборочные артефакты и свёрнутые источники;
- отдельный React/Vinext-прототип содержит моковый интерфейс;
- реализованы multi-turn context в InMemorySaver, одна active_company, targeted
  finance/legal без полного pipeline и второй шаг Master с выбором grounded findings;
- persistent history в БД, name resolution, comparison, deal risk и streaming не реализованы;
- сравнение нескольких контрагентов, банковские интеграции и большая база не
  относятся к готовому текущему проходу.

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
cd alfa-counterparty-prototype
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
  lifecycle сессий (30 минут бездействия, 100 диалогов, последние 6 turns).
- `backend/app/agent/finance.py`, `legal.py`: targeted snapshot adapters;
  `targeted_models.py`: framework-agnostic контракты и MasterSynthesis.
- `runtime.py`, `langchain_tools.py`, `prompt.py`: 2 model steps, 1 domain call,
  recursion limit 12; неверный routing использует очевидный deterministic fallback.
- `response.py`: строгая связь evidence с фактами, hydration verified data,
  обязательные findings и gaps независимо от выбора модели; отдельный
  `leading_artifact` только для full check, conversational `message` и
  выборочные `blocks`. Узкие ответы не повторяют карточку компании.
- `backend/app/agent/synthesis.py`: общий каталог grounded observations для
  post-tool контекста и hydration; проверка evidence и ограниченный выбор
  наблюдений/артефакта с безопасным fallback.
- `tests/test_agent_multiturn.py`, `tests/test_agent_runtime.py`: routing, state,
  второй model step, budgets и fallback.
- `tests/test_financial_capability.py`, `tests/test_legal_capability.py`,
  `tests/test_targeted_response.py`, `tests/test_chat_api.py`: данные, grounding и API.
- Клиент `backend/static/landing.js` передаёт conversation_id и сохраняет текущий
  диалог в sessionStorage вкладки. Новый диалог сбрасывает клиентский контекст.
- Подробности, API-пример и проверка: [MULTI_TURN_CHAT.md](MULTI_TURN_CHAT.md).

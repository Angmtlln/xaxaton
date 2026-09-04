# Multi-turn чат с активной компанией

Сценарий работает через `/api/v1/chat/messages`:

1. «Проверь контрагента 6165169320» → `full_company_check` → active company.
2. «А что у них с финансами?» → `get_financial_data` для той же компании.
3. «А что у них с судами?» → `get_legal_data` для той же компании.

Узкий вопрос можно задать и первым сообщением с явным ИНН. Новый явный ИНН
меняет активную компанию только после успешного/частичного результата tool.
Comparison, поиск по названию, deal risk, SSE и persistent history не входят
в этот срез. Произвольные аналитические follow-up вне поддерживаемых доменов
могут потребовать уточнения.

## API и состояние

Первый запрос:

```json
{"message":"Проверь контрагента 6165169320"}
```

Ответ дополняет существующий `AssistantResponse`:

```json
{
  "conversation_id":"<UUID из ответа>",
  "active_company":{"inn":"6165169320","name":"<название из snapshot>"},
  "message":"<подтверждённый ответ>",
  "blocks":[],
  "evidence":[],
  "metadata":{"model_calls":2,"tool_calls":1,"synthesis":"deterministic"}
}
```

Продолжение:

```json
{"conversation_id":"<UUID из ответа>","message":"А что у них с финансами?"}
```

`ConversationState` расширяет штатный LangChain `AgentState`. Сообщения и
`active_company` сохраняются в `InMemorySaver` по `thread_id=conversation_id`.
`ConversationStore` управляет только временем жизни и блокировками, а не
создаёт отдельную систему памяти: 30 минут бездействия, максимум 100 диалогов,
последние 6 завершённых turns. Старые checkpoints удаляются после сохранения
текущего проверенного состояния. Запросы одного диалога выполняются последовательно;
ожидание очереди входит в общий deadline.

Неизвестный/истёкший ID возвращает HTTP 200 с
`metadata.error_code=unknown_conversation`, `status=needs_input`, без tools.
Невалидный формат UUID возвращает HTTP 422. Отсутствие ID создаёт новый диалог.
Перезапуск процесса удаляет серверные диалоги; для этого MVP нужен один worker.
Никаких таблиц чата, SQL-изменений или миграций нет. Targeted tools только читают
snapshot; full-check сохраняет прежний operational audit.

В рабочем UI видно активную компанию. История текущего диалога (до 24 последних
сообщений, включая сетевые ошибки) сохраняется в `sessionStorage` вкладки.
«Новый диалог» сбрасывает клиентский контекст. Источники прошлых ответов остаются
привязаны к соответствующему сообщению.

## Runtime и граница данных

```text
message + conversation_id
  → LangChain create_agent + checkpoint
  → Master tool proposal
  → schema / allowlist / canonical INN / deadline
  → domain ToolResult
  → второй model step: выбор и порядок finding_ids
  → backend hydration AssistantResponse
  → trusted state checkpoint
```

Лимиты на turn: 2 model calls, 1 domain tool call, recursion limit 12;
model/tool/run timeouts остаются в `Settings`. Routing неподдерживаемого tool,
невалидных аргументов или недоступной модели для очевидного запроса переходит
на один deterministic call ожидаемого tool. Уже начатая capability не повторяется.

Synthesis намеренно ограничен: Master выбирает и упорядочивает существующие
наблюдения из текущего `ToolResult`. Он не публикует свободный текст с новыми
утверждениями. Backend гидратирует формулировки, company identifiers, числа,
series, evidence и ссылки. Выдуманные IDs, дополнительные поля, markup или
пустой выбор при наличии findings дают fallback. Required findings и data gaps
показываются независимо от выбора Master.

Evidence проверяется не только по существованию ID: `fact_id`, `field_ref`,
source, display value и прочие поля должны совпасть с evidence, построенным из
того же backend fact. UI читает результат текущего execution, не старый artifact
из checkpoint. В trace записываются model/provider, версии prompt/tools, calls,
безопасные аргументы, статусы, latency и доступная token usage, без reasoning.

## Targeted capabilities

- `get_financial_data`: `get_latest_snapshot` → `build_finance`. До пяти лет
  выручки, прибыли, капитала и кредиторской задолженности; точные пути полей
  каждой строки. Годовая динамика только для последовательных лет и ненулевой
  базы. Неизвестные значения не заменяются нулями; дробные и дублирующиеся годы
  исключаются. Наблюдения убытков, отрицательного капитала и падения выручки
  нельзя скрыть выбором модели.
- `get_legal_data`: `get_latest_snapshot` → `build_reliability`. Судебные
  количества/суммы, исполнительные производства, надзорные проверки и метки
  источника. Неполные агрегаты не публикуются как полные; повреждение одной
  секции даёт gaps/PARTIAL, сохраняя доступные факты остальных секций.

Оба tools возвращают framework-agnostic `ToolResult` и `TargetedData` с
`availability=DATA|PARTIAL|NO_DATA`. Они не вызывают `run_check`, четыре доменных
LLM или summary. Старый full-check pipeline и `/report` не переписаны.

## Проверки и запуск

```bash
cd backend
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q app tests scripts
node --check static/landing.js
node --check static/report.js
.venv/bin/python scripts/smoke_multiturn.py --base-url http://localhost:8000 --pause-seconds 60
```

Live smoke требует PostgreSQL и настоящий ChatGroq. Проверяет одну active company,
один conversation_id, по два model steps на turn, model routing и model synthesis
для finance/legal, а также HTTP 200 для `/` и `/report`. Пауза нужна только при
общем TPM-лимите провайдера; runtime не добавляет автоматических повторов.
`PARTIAL` при неполной карточке допустим. Provider `429` сохраняет факты через
fallback, но строгий live smoke в таком случае завершится ошибкой.

Основные тесты: `test_agent_runtime.py`, `test_agent_multiturn.py`,
`test_conversations.py`, `test_financial_capability.py`, `test_legal_capability.py`,
`test_targeted_response.py`, `test_chat_api.py`. Существующие pipeline/facts/
grounding tests проверяют legacy-регрессию.

Ручной smoke: пройти три сообщения выше, перезагрузить вкладку, открыть
источники, проверить «Новый диалог» и `/report?inn=6165169320`.
Дополнительно проверить ширину 390 px и отправку Enter/перенос Shift+Enter.

## Карта изменения

| Область | Файлы |
|---|---|
| Runtime и состояние | `backend/app/agent/runtime.py`, `conversations.py`, `langchain_tools.py`, `prompt.py` |
| Domain tools | `backend/app/agent/tools.py`, `finance.py`, `legal.py`, `targeted_models.py` |
| Контракты и hydration | `backend/app/agent/models.py`, `response.py`, `backend/app/api/schemas.py`, `backend/app/main.py` |
| Рабочий чат | `backend/static/index.html`, `landing.js`, `styles.css` |
| Проверки | Семь профильных test-файлов выше и `backend/scripts/smoke_multiturn.py` |
| Сборка | `backend/.dockerignore` исключает локальные секреты и virtualenv из Docker context |
| Документация | `AGENTS.md`, `docs/AI_INDEX.md`, `docs/AGENT_FIRST_ARCHITECTURE.md`, этот файл, `backend/README.md` |

## Результат проверки 04.09.2026

- Полный backend regression: **137 passed** (одно предупреждение внешнего
  Starlette/AnyIO о deprecated alias); compileall, оба JS syntax checks и
  `git diff --check` успешны.
- Независимый regression review: все найденные дефекты исправлены; отдельно
  выполнены 74 профильных теста, блокирующих замечаний нет.
- Playwright: ID следующего turn, восстановление истории, активная компания,
  loading/partial/NO_DATA, HTTP/network errors после reload, escaping HTML,
  unknown/prototype block fallback, reset/focus, desktop и 390 px — успешно.
- Live PostgreSQL + ChatGroq: full → finance → legal, один conversation_id,
  одна компания, по два model calls и одному tool call. Full completed за
  4,8 с; finance partial за 1,8 с; legal partial за 1,5 с. У обоих targeted
  ответов `routing=model`, `synthesis=model`; partial отражает пропуски данных.
  Между turns в smoke использовалась пауза 60 с из-за общего TPM-лимита Groq.
- Первый live-прогон подтвердил безопасный fallback при provider 429.

Проверенная локальная версия запущена на `http://localhost:8001` в контейнере
`contractors-multiturn-smoke`, с текущими app/static, подключёнными как read-only
volumes, и установленными зависимостями из requirements.txt. Основной сервис
на 8000 остаётся отдельным. Обычная пересборка Docker-образа была прервана
timeout Docker Hub при получении `python:3.11-slim`; новый образ не опубликован.
Для обычного запуска после восстановления Docker Hub:

```bash
cd backend
docker compose build api
docker compose up -d api
```

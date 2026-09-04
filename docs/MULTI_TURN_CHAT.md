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
  "leading_artifact":null,
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

## Conversation-first ответ

Полная проверка возвращает `leading_artifact` типа `company_summary`: название,
ИНН, статус, возраст и два независимых показателя банка из проверенных фактов,
а также локальную ссылку «Полный анализ». Backend добавляет этот блок всегда
для успешного/частичного full check; модель не может убрать его или подставить URL.
Схема доступна в Swagger `/docs` и `/openapi.json`.

Следом UI показывает основной `message`: прямой вывод, выбранные наблюдения
с объяснением значения для проверки и предложение продолжить разговор.
Full-check ToolResult больше не используется как готовая страница отчёта:
второй вызов Master получает компактные структурированные наблюдения и выбирает
их порядок и необязательный вспомогательный артефакт. Backend собирает текст
из проверенных наблюдений; свободная генерация новых фактических утверждений
в этом срезе по-прежнему не разрешена.

`blocks` содержат только вспомогательные артефакты, `evidence` — источники.
Источники свёрнуты по умолчанию и относятся к конкретному ответу. На узкий
вопрос «А что с прибылью?» возвращается `leading_artifact=null`, текст и при
необходимости один график или блок показателей. Такой вопрос начинается со
значения последней доступной прибыли либо явного отсутствия данных о ней;
артефакт показывает прибыль. Смешанный вопрос о выручке или кредиторской
задолженности вместе с прибылью сохраняет оба показателя. Старый набор из шести блоков
не добавляется автоматически. `/report?inn=6165169320` остаётся отдельным
подробным режимом.

Изменена только схема HTTP-ответа и представление, модели хранения и SQL
не затронуты; DB impact отсутствует, миграция не требуется.

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

## Предыдущая проверка multi-turn 04.09.2026 (до conversation-first)

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

## Проверка conversation-first 04.09.2026

- Полный backend regression: **164 passed**, одно прежнее предупреждение
  Starlette/AnyIO. `compileall`, оба `node --check` и `git diff --check` успешны.
- Browser smoke на desktop 1366 × 900 и mobile 390 × 844: компактная сводка
  перед ответом, отсутствие автоматического dashboard, продолжение без ИНН,
  один график прибыли, свёрнутые источники и переход к `field_ref`, Enter /
  Shift+Enter, восстановление истории и reset/focus — успешно.
- Управляемые ответы API в браузере: `NO_DATA`, HTTP 404, network error,
  восстановление ошибок после reload, неизвестный/prototype UIBlock,
  HTML-подобные строки, недопустимый URL и длинные названия — успешно.
- Реальный browser → API → PostgreSQL/Groq full check завершился и отобразился
  в новом формате. Legacy `/report?inn=6165169320` загрузил все четыре блока
  без горизонтального переполнения на desktop и mobile.
- Live API подтвердил `model_calls=2`, `tool_calls=1`, `synthesis=model`
  у full check и finance. Самостоятельный legal-запрос также дал model synthesis.
  Однако два строгих прогона full → finance → legal завершились ошибкой smoke
  на последнем требовании `synthesis=model`: legal использовал безопасный
  fallback. В отдельном browser full check fallback тоже сработал. Данные,
  сводка и источники сохранились. Поэтому стабильный model synthesis на каждом
  live turn не заявляется; malformed synthesis проверен unit-тестами.

Актуальный локальный UI доступен на `http://localhost:8001`, контейнер
`contractors-multiturn-smoke` перезапущен с текущими app/static. Сервис на 8000
не обновлялся. Для ручной приёмки: полный запрос → «А что с прибылью?» →
источники → «Полный анализ», затем новый диалог. Строгий live smoke можно
повторить командой выше, оставив паузу 60 секунд при ограничениях Groq.

Файлы conversation-first изменения:

| Область | Изменённые файлы |
|---|---|
| Контракт и синтез | `backend/app/agent/models.py`, `targeted_models.py`, `response.py`, новый `synthesis.py` |
| Runtime и tool context | `backend/app/agent/runtime.py`, `langchain_tools.py`, `prompt.py`, `tools.py` |
| API и live smoke | `backend/app/main.py`, `backend/scripts/smoke_multiturn.py` |
| Интерфейс | `backend/static/index.html`, `landing.js`, `styles.css` |
| Регрессии | `backend/tests/test_agent_response.py`, `test_targeted_response.py`, `test_agent_runtime.py`, `test_agent_multiturn.py`, `test_chat_api.py` |
| Документация | `backend/README.md`, `docs/AI_INDEX.md`, `docs/AGENT_FIRST_ARCHITECTURE.md`, `docs/MULTI_TURN_CHAT.md` |

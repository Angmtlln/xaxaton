# Multi-turn чат с активной компанией

Основной сценарий работает через `POST /api/v1/chat/messages`:

1. «Проверь контрагента 6165169320» → `full_company_check`.
2. «Что с финансами?» → `get_financial_data` для активной компании.
3. «Почему это плохо?» → ответ из уже сохранённого доверенного контекста.
4. «Объясни проще» → переформулировка последнего подтверждённого ответа.
5. «Насколько это критично для сделки с отсрочкой?» → интерпретация с учётом
   пользовательского контекста сделки.
6. «А что с судами?» → `get_legal_data` для активной компании.
7. «Что здесь самое плохое?» → сопоставление уже полученных финансовых и
   юридических данных без повторного полного прогона.

Узкий вопрос можно задать первым сообщением с явным ИНН. Новый явный ИНН
становится активным только после успешного или частичного результата tool.
Поиск по названию, SSE и persistent history не входят в этот срез.

Сравнение живёт рядом: «Сравни 6165169320 и 2311304742, важнее суды» вызывает
`compare_companies` один раз на все компании. Ответ содержит таблицу
`comparison_table`, которую строит backend, а не выбирает модель. Состояние
сравнения хранится отдельно от `trusted_context`: тот привязан к одной активной
компании, поэтому сравнение не перезаписывает её. Продолжения вроде «Кого
выбрать и почему?» отвечают из сохранённого сравнения без нового вызова tool.

## API и состояние

Первый запрос:

```json
{"message":"Проверь контрагента 6165169320"}
```

Сокращённый пример ответа:

```json
{
  "conversation_id": "<UUID>",
  "active_company": {"inn": "6165169320", "name": "<название>"},
  "message": "<естественный вывод Master>",
  "leading_artifact": {"type": "company_summary", "data": {}},
  "blocks": [],
  "evidence": [],
  "metadata": {
    "model_calls": 3,
    "tool_calls": 1,
    "grounding_status": "verified",
    "repair_attempts": 0
  }
}
```

Продолжение передаёт полученный `conversation_id`:

```json
{
  "conversation_id": "<UUID>",
  "message": "Что с финансами?"
}
```

`ConversationState` расширяет штатный LangChain `AgentState`. В checkpoint
раздельно хранятся:

- bounded message history — только для разговорной связности;
- `active_company`;
- `trusted_context` — последние нормализованные результаты domain tools;
- `user_context` — условия сделки, названные пользователем;
- `last_topic` и статус заземления предыдущего ответа.

Текст прошлых сообщений не является доверенной фактической базой. Предыдущая
фраза модели не попадает в `trusted_context` и сама по себе не становится
истиной.

`ConversationStore` управляет TTL и блокировками: 30 минут бездействия,
максимум 100 диалогов, последние 6 завершённых turns. Запросы одного диалога
выполняются последовательно; ожидание входит в общий deadline. История
process-local: перезапуск процесса её удаляет, SQL-таблиц и миграций для чата нет.

Неизвестный или истёкший ID возвращает HTTP 200 с
`metadata.error_code=unknown_conversation`, `status=needs_input`, без tools.
Невалидный UUID возвращает HTTP 422. В UI текущий диалог сохраняется в
`sessionStorage`; «Новый диалог» очищает клиентский контекст.

## Runtime и граница данных

```text
message + conversation_id
  → LangChain create_agent + checkpoint
  → Master выбирает domain tool либо отвечает из trusted context
  → schema / allowlist / canonical INN / deadline
  → domain ToolResult
  → backend нормализует metrics / series / events / statuses / coverage
  → Master формирует естественный ответ + необязательный allowlisted artifact
  → отдельная LLM-проверка company-specific утверждений
  → при ошибке: одна repair-попытка, затем conservative fallback
  → backend гидратирует identifiers / числа / evidence / UI
  → trusted state checkpoint
```

На turn разрешён один domain tool call и не более пяти model calls. Обычный
tool-turn использует три: routing/tool call, ответ Master и verifier. Repair
добавляет один вызов. Контекстный follow-up обычно использует два вызова —
ответ и verifier. Простая переформулировка последнего уже проверенного ответа
может пропустить verifier, но строгая проверка неизвестных URL и подписанных
ИНН/ОГРН остаётся.

Неверный routing, невалидные аргументы, timeout или недоступная модель приводят
к ограниченному deterministic fallback. Уже полученная capability не
запрашивается повторно, если текущий вопрос можно закрыть из `trusted_context`.

## Verified data и свободное рассуждение

Domain tools не передают Master каталог готовых выводов. Их компактный контракт
содержит проверяемые данные:

- `metrics` — значения и единицы измерения;
- `series` — временные ряды;
- `events` — нормализованные события;
- `statuses` и `coverage`;
- `policy_signals` — только детерминированные флаги предметной области;
- `evidence` и provenance;
- `availability=DATA|PARTIAL|NO_DATA` и gaps.

Backend владеет идентификаторами компании, значениями, ссылками, evidence,
allowlisted UI и состояниями неполноты. Master сам выбирает существенное,
сопоставляет наблюдения, объясняет смысл, учитывает контекст сделки и пишет
естественный основной `message`. Его речь не ограничивается словарём готовых
русских предложений или `finding_ids`.

Детерминированными остаются только действительно предметные правила:
официальные hard-stop/attention flags, независимые банковские `riskLevel` и
`zskRiskLevel`, а также строгая схема/ИНН/evidence/UI-валидация. Финансовые
коэффициенты и динамика передаются как данные, а не как backend-owned выводы.

Grounding verifier получает черновик ответа и нормализованный контекст. Он
проверяет только substantive company-specific утверждения: есть ли опора в
данных и нет ли противоречия. Стиль, степень упрощения и осторожная бизнес-
интерпретация разрешены. После одной неуспешной repair-попытки пользователь
получает conservative fallback на проверенных данных.

## Conversation-first ответ и UI

Полная проверка может предварять ответ детерминированным компактным
`company_summary`: название, ИНН, статус, возраст, два независимых банковских
индикатора и локальная ссылка «Полный анализ». Это вторичный артефакт; основной
контент turn — `message` Master.

`blocks` содержат только allowlisted вспомогательные артефакты. Метрики,
series, policy items и evidence гидратирует backend из текущего ToolResult.
Модель не генерирует HTML, SVG, произвольный JS, URL источников или значения
графиков. `/report?inn=6165169320` остаётся отдельным подробным режимом.

Evidence проверяется по точному совпадению `fact_id`, `field_ref`, source и
display value с backend registry. UI читает результат текущего execution, а не
старый artifact из checkpoint. Trace сохраняет model/provider, версии prompt и
tools, calls, безопасные аргументы, статусы, latency и доступную token usage —
без скрытого reasoning.

## Targeted capabilities

- `get_financial_data`: читает snapshot и возвращает до пяти лет выручки,
  прибыли, капитала и кредиторской задолженности, производные метрики и точные
  пути источников. Неизвестное не превращается в ноль.
- `get_legal_data`: возвращает арбитраж, исполнительные производства,
  надзорные проверки и метки источника. Только официальные hard-stop/attention
  flags становятся `policy_signals`; остальные данные интерпретирует Master.

Обе capabilities возвращают framework-agnostic `ToolResult` и не вызывают
`run_check`, четыре доменных LLM или summary. Старый full-check pipeline,
`/api/v1/checks` и `/report` сохранены.

## Проверки и запуск

```bash
cd backend
.venv/bin/pytest -q
.venv/bin/python -m compileall -q app tests scripts
# модули интерфейса: node --check не разрешает импорты, проверяем загрузкой
cd .. && for f in frontend/js/**/*.js; do node --input-type=module \
  --eval "await import('./$f')" 2>&1 | grep -qE "SyntaxError|MODULE_NOT_FOUND" \
  && echo "СЛОМАН: $f"; done; cd backend
.venv/bin/python scripts/smoke_multiturn.py --base-url http://localhost:8000 --pause-seconds 60
.venv/bin/python scripts/smoke_openrouter_master.py --base-url http://localhost:8000
```

Live smoke использует одну active company и один `conversation_id`, проходит
семь реплик acceptance-сценария и требует tools только на turns 1, 2 и 6.
Он проверяет связность, отсутствие лишних tool calls, grounding metadata,
evidence и доступность `/` и `/report`. PostgreSQL, `OPENROUTER_API_KEY` и
доменный Groq должны быть настроены заранее; `PARTIAL` допустим для неполной
карточки. Расширенный smoke перед диалогом последовательно проверяет обычный
completion, structured output и LangChain tool calling через OpenRouter.

Основные тесты:

- `test_agent_runtime.py`, `test_agent_multiturn.py`, `test_conversations.py`;
- `test_grounding_behavior.py` — выдуманный факт, URL/идентификатор,
  противоречие, repair/fallback и простая переформулировка;
- `test_financial_capability.py`, `test_legal_capability.py`;
- `test_agent_response.py`, `test_targeted_response.py`, `test_chat_api.py`.

Ручной browser smoke: пройти семь реплик, открыть источники и «Полный анализ»,
перезагрузить вкладку, проверить «Новый диалог», Enter / Shift+Enter и ширину
390 px.

## Проверка 05.09.2026

- После интеграции ветки Amir полный backend regression: `188 passed`, одно
  предупреждение Starlette/AnyIO. Профильный comparison/OpenRouter/runtime/chat
  набор: `57 passed`. `compileall`, frontend module check и `git diff --check`
  прошли.
- Точный семирепличный сценарий прошёл behavioral-тест с одним
  `conversation_id`: tools вызваны только на turns 1, 2 и 6; turn 4 использовал
  rewrite fast path, остальные содержательные ответы прошли verifier.
- Docker API пересобран; `/health`, `/` и `/report` отвечают `200`. Live
  comparison трёх компаний прошёл одним tool call: `routing=model`,
  `synthesis=model`, `grounding_status=verified`, таблица из 9 строк и 14
  проверенных evidence rows.
- Browser smoke того же сравнения: таблица и кнопки источников отрисовались,
  ошибок консоли нет, при ширине 830 px `scrollWidth=clientWidth=830`.
- Три прямые OpenRouter-пробы прошли на `z-ai/glm-5.3-flash`: обычный ответ,
  structured output и LangChain native tool calling. Для обязательных reasoning
  tokens сохранены раздельные конечные output-бюджеты: routing 512,
  synthesis 4096, verifier 2048 и repair 4096. `OPENROUTER_REASONING_EFFORT=low`.
- После разделения бюджетов live-диалог `Проверь контрагента
  6165169320` → `Почему это вообще плохо?` прошёл без fallback. На втором
  turn synthesis завершился с `stop` на 308 output / 12 reasoning tokens,
  verifier — с `stop` на 90 output / 78 reasoning tokens; `tool_calls=0`,
  `repair_attempts=0`, `grounding_status=verified`.
- Полный семиходовый live-сценарий после интеграции ещё нужно повторить; успешным
  считается `routing=model`, `synthesis=model` и отсутствие
  `grounding_status=fallback` на всех семи turns.

## DB impact

Модели хранения, SQL и legacy audit не менялись. Новые trusted-context и
grounding поля живут в process-local LangGraph checkpoint и HTTP metadata.
Миграция БД не требуется.

## Карта реализации

| Область | Файлы |
|---|---|
| Runtime и trusted state | `backend/app/agent/runtime.py`, `conversations.py`, `langchain_tools.py`, `prompt.py` |
| Grounding | `backend/app/agent/grounding.py`, `synthesis.py` |
| Domain data | `backend/app/agent/tools.py`, `finance.py`, `legal.py`, `targeted_models.py` |
| Контракты и hydration | `backend/app/agent/models.py`, `response.py` |
| Live smoke | `backend/scripts/smoke_multiturn.py`, `smoke_openrouter_master.py` |
| Регрессии | профильные тесты в `backend/tests/` |

# Контрагент-агент. Agent-first PoC

Сверка обзора с кодом: 06.09.2026; новый live-прогон в эту дату не подразумевается.
Основной интерфейс — диалог: полная проверка, узкие вопросы, сравнение 2–5
компаний, поиск и подбор. Подробный отчёт остаётся отдельным сценарием.

Текущий путь чата:

```text
POST /api/v1/chat/messages
  → LangChain create_agent (LangGraph runtime)
  → StructuredTool adapter / domain Tool Registry
  → full_company_check | finance/legal | compare_companies | find_companies | select_counterparties
  → run_check() или targeted builders / поиск / подбор
  → verified ToolResult → естественный ответ Master
  → backend hydration AssistantResponse
  → rich chat в frontend/
```

Legacy-проход по-прежнему доступен отдельно:

```
POST /api/v1/checks {"inn": "..."}
        │
        ├─ 1. карточка отчёта из PostgreSQL (последний снапшот)
        ├─ 2. детерминированный слой: факты считаются кодом из сырых полей,
        │     раскладываются по 4 блокам, каждый факт несёт ссылку на поле
        ├─ 3. 4 агента Groq параллельно, у каждого своя доменная спецификация
        │     identity | reliability | finance | experience
        ├─ 4. Summary-LLM поверх четырёх блочных резюме
        └─ 5. guardrails + заземление + запись прогона в audit.*
```

## Быстрый старт

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # положите OPENROUTER_API_KEY и GROQ_API_KEY

docker compose up -d db                                  # PostgreSQL 16
python scripts/load_snapshot.py --create-schema \
       --file ../contractors_audit.snapshot.json         # схема + 100 карточек

# Для PDF при локальном запуске (в Docker браузер установлен образом):
python -m playwright install --only-shell chromium
uvicorn app.main:app --reload --port 8000
```

Веб-интерфейс: <http://localhost:8000/>. Swagger UI: <http://localhost:8000/docs>,
ReDoc: <http://localhost:8000/redoc>.

Без Docker, локальный PostgreSQL (так проверялось на macOS):

```bash
brew install postgresql@16
export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"
pg_ctl -D /opt/homebrew/var/postgresql@16 -l /tmp/pg16.log start
psql -d postgres -c "CREATE ROLE postgres LOGIN SUPERUSER PASSWORD 'postgres'"
psql -d postgres -c "CREATE DATABASE contractors OWNER postgres"
python scripts/load_snapshot.py --create-schema --file ../contractors_audit.snapshot.json
```

Проход по ИНН:

```bash
curl -X POST http://localhost:8000/api/v1/checks \
     -H 'Content-Type: application/json' \
     -d '{"inn":"6165169320"}' | jq .summary
```

Тот же проход через Master Agent и chat API:

```bash
curl -X POST http://localhost:8000/api/v1/chat/messages \
     -H 'Content-Type: application/json' \
     -d '{"message":"Проверь контрагента 6165169320"}' | jq .
```

Runtime принимает полную проверку и узкие финансовые/юридические вопросы.
Передайте `conversation_id` из предыдущего ответа, чтобы использовать активную
компанию без повторного ИНН. Явный dispatch обычно выполняет один domain tool;
в auto path доступны до двух разных targeted чтений, например finance и legal.
Однозначные команды полной проверки и сравнения идут прямо в allowlisted tool.
Свободные вопросы при доступной модели маршрутизируются по смыслу;
подробные границы — в [CHAT_ROUTING](../docs/CHAT_ROUTING.md). Master пишет ответ из trusted ToolResult; backend проверяет
схему, идентификаторы, ссылки и гидратирует UI. Обычный turn использует один
вызов Master; model routing добавляется только при необходимости.
`ChatOpenAI` обращается к OpenRouter (`z-ai/glm-5.3-flash`,
`OPENROUTER_PROVIDER_SORT=throughput`); модель фиксируется на conversation.
Verifier и repair **выключены по умолчанию**, в том числе для «Объясни проще».
`AGENT_GROUNDING_DEBUG=true` включает прежний bounded механизм только для
явного eval/debug. Нового LLM/regex-классификатора вместо него нет.
Без ключа, при ошибке провайдера или структурной валидации остаётся conservative
fallback. Output-бюджеты сохранены: routing 512, synthesis 4096, debug verifier
4096, debug repair 4096. Измерения: [latency waterfall](../docs/CHAT_LATENCY.md).
LangGraph устанавливается транзитивно через LangChain; LangSmith tracing и API
key для запуска не требуются.

Без базы и без LLM-ключей — тот же конвейер прямо по файлу выгрузки:

```bash
LLM_MOCK=true python scripts/demo_offline.py --inn 6165169320
```

## Модели

Ниже — значения по умолчанию из `app/config.py`; `.env` может их переопределить.
Это не перечень гарантированно доступных моделей провайдера. Доступность и
лимиты зависят от аккаунта и проверяются перед живым запуском.

Legacy `run_check()` делает пять вызовов: четыре блочных агента параллельно и
Summary-LLM. Chat вызывает `run_check(include_summary=False)`: четыре доменных
агента → structured ToolResult → Master, без промежуточной Summary-сводки;
в mock-режиме routing детерминированный. Targeted finance/legal читают только
snapshot и нужный builder, не вызывая полный pipeline и доменные LLM.
Подробнее: [multi-turn chat](../docs/MULTI_TURN_CHAT.md).
Модели заданы в `.env`, код к конкретной модели не привязан.

| Роль | Модель | Почему |
|---|---|---|
| Master | `z-ai/glm-5.3-flash` через OpenRouter | routing при необходимости и естественный ответ из trusted context |
| Блок «Кто это» | `qwen/qwen3.8-27b` | отдельный TPM-бюджет для параллельного прохода |
| Блок «Надёжность и правовые риски» | `openai/gpt-oss-120b` | самый ответственный блок, отдаём сильнейшей модели |
| Блок «Финансовое состояние» | `qwen/qwen3.8-27b` | устойчиво держит JSON-схему на числовых данных |
| Блок «Опыт и позитивные сигналы» | `qwen/qwen3.6-27b` | отдельный TPM-бюджет для параллельного прохода |
| Итоговое summary | `openai/gpt-oss-120b` | один вызов, собирает четыре блока в 2–3 коротких тезиса для одного экрана |

Блоки распределены по моделям для уменьшения конкуренции за лимиты
провайдера. `identity` и `finance` сейчас используют одну модель и делят её
бюджет. Точные TPM/RPM и доступность зависят от аккаунта; прежние измерения
не гарантируют отсутствие 429. При запуске сверяйте фактическую конфигурацию
и ответы провайдера, не копируйте исторический лимит как текущий.

Ещё два решения по лимиту:

* `GROQ_REASONING_EFFORT=low` для `gpt-oss` — ответ короче вчетверо
  (1387 → 325 токенов на блоке «Кто это») и перестаёт обрываться на середине
  JSON;
* при 429 вызов сразу уходит на следующую модель из `GROQ_FALLBACK_MODELS`,
  и только когда все модели в лимите — клиент выдерживает паузу, которую
  назвал сам Groq (`Please try again in 25.1775s`).

Master настраивается отдельно от доменных агентов:

```dotenv
OPENROUTER_API_KEY=...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
MASTER_MODEL=z-ai/glm-5.3-flash
OPENROUTER_REASONING_EFFORT=low
```

`GROQ_API_KEY` по-прежнему используется четырьмя доменными агентами и summary:

```
GROQ_API_KEY=gsk_...
```

Список доступных моделей зависит от тарифа, проверить свой можно так:

```bash
curl -s https://api.groq.com/openai/v1/models \
     -H "Authorization: Bearer $GROQ_API_KEY" | jq -r '.data[].id'
```
Если ключа нет или модель недоступна, сервис не падает: он переходит в
детерминированный режим (`llm_mode: mock`), где те же ответы собираются
шаблоном по фактам. Это же режим для тестов.

## Веб-интерфейс

Основные страницы раздаёт тот же сервис:

- `/` — чат: основной текст аналитика, компактная сводка после полной проверки,
  выборочные артефакты, источники, подбор и контекстные действия;
- `/report?inn=...` — отдельный полный отчёт.

Frontend отображает только разрешённые типы артефактов; backend гидратирует
метрики, графики, сравнение и граф связей из проверенных данных. Модель не
генерирует HTML/SVG или доверенные значения интерфейса. Качественный профиль
AI — интерпретация по направлениям, а не банковский или общий числовой рейтинг.
Неизвестный тип блока обрабатывается безопасным fallback.

После полной проверки доступны новости и кросс-проверка связей. PDF-экспорт,
навигация по подборкам и сравнения описаны в [CHAT_UI](../docs/CHAT_UI.md) и
[PDF_EXPORT](../docs/PDF_EXPORT.md). Последующие абзацы о Summary и карточках
направлений относятся к отдельному legacy-отчёту.



Базовый визуальный язык взят из
[design/prototype](../design/prototype), но рабочий
отчёт адаптирован под плотный сценарий проверки. На первом экране находятся
компактная сводка, независимые оценки банка и ЗСК, ссылки на четыре направления,
мини-графики и 2–3 тезиса Summary-агента. Подробные направления остаются
карточками `risk-card` на `<details>`, а строки `evidence-row` показывают тип
доказательства и путь в исходном JSON.

Summary-агент возвращает `narrative_points`: 2–3 тезиса до 135 символов каждый
и не более 360 символов суммарно. Backend повторно проверяет эти границы,
нормализует избыточный ответ и сохраняет совместимую строку `narrative` для
старых клиентов. Интерфейс использует массив, а при его отсутствии делит
старую строку на предложения.

Что добавлено к прототипу, потому что у нас есть данные, которых в моке
не было:

| Блок | Зачем |
|---|---|
| Состояние `state-none` у карточки | у нас есть сигнал `NO_DATA`, у прототипа его не было |
| Раздел «Показатели и динамика» | четыре графика: выручка, прибыль, арбитраж, исполнительные производства |
| Раздел «Что есть в карточке, а чего нет» | паспорт полноты по 9 блокам |
| Строка прогона внизу | модели, время, доля утверждений со ссылкой на факт |
| Экран загрузки | проход занимает несколько секунд, шаги показываются явно |

Палитра графиков проверена валидатором из навыка dataviz на цветах
прототипа: чёрный `#111111` для одиночной серии, `#ef3124` и `#0c8b48`
для двух серий (ΔE 9.5 при протанопии, плюс подписи значений и легенда).

## Endpoints

| Метод | Путь | Что делает |
|---|---|---|
| POST | `/api/v1/chat/messages` | сообщение и необязательный conversation_id → ответ Master и артефакты |
| POST | `/api/v1/chat/messages/stream` | статусы этапов и готовый ответ в NDJSON |
| POST | `/api/v1/chat/{conversation_id}/exports` | создать PDF из контекста диалога |
| GET | `/api/v1/chat/{conversation_id}/exports/{file_id}` | получить созданный PDF |
| POST | `/api/v1/checks` | полный проход по ИНН |
| GET | `/api/v1/checks` | история проходов |
| GET | `/api/v1/checks/{run_id}` | сохранённый результат прохода |
| GET | `/api/v1/companies` | какие карточки загружены, с фильтрами по риску и полноте |
| GET | `/api/v1/companies/{inn}/facts` | детерминированные факты без вызова LLM |
| GET | `/api/v1/companies/{inn}/coverage` | паспорт полноты данных |
| GET | `/health` | состояние БД и режим LLM |
| GET | `/` | веб-интерфейс |

## Что заложено в архитектуру из продуктовых гипотез

**Факты считаются кодом, а не пересказываются моделью (S2).**
`app/domain/facts.py` собирает числа и флаги из сырых полей карточки. Готовые
текстовые формулировки отчёта в выводы не попадают: в 46 карточках из 100 они
противоречат цифрам той же карточки. Что именно ушло в модель, видно через
`/api/v1/companies/{inn}/facts` и в `audit.run_blocks.facts_input`.

**Provenance фактов и утверждений legacy-блоков (S5).**
Факт имеет `id` и `field_ref`. Агент возвращает `fact_id`, сервис сверяет его с
реестром фактов. Ссылка на несуществующий факт помечается `UNVERIFIED` и
попадает в метрику `grounding` ответа и в `audit.run_statements`.
Это контракт доменных/legacy результатов. Естественная проза Master не
ограничивается каталогом готовых заключений; её качество проверяют behavioral
и live evals, структурную достоверность — backend.

**Факты отдельно от цвета светофора (H3, S1).**
Оценки банка `riskLevel` и `zskRiskLevel` приводятся без изменений, свой скоринг
не считается. При этом жёсткие факты — блокировка счетов ФНС, банкротство,
недостоверные данные — поднимаются наверх независимо от цвета. Защитный слой
`enforce_guardrails` не даёт выводу быть мягче посчитанных фактов и пишет об
этом в `guardrail_notes`.

**Отсутствие данных — штатный режим (H9, S6).**
Паспорт полноты по 9 блокам считается для каждой карточки. Пустой блок даёт
сигнал `NO_DATA` и явное «невозможно оценить», а не правдоподобный текст.

**Три группы вместо числового рейтинга (S4).**
`STOP`, `ENHANCED_CHECK`, `CONDITIONALLY_OK`, плюс `NO_DATA` — формат из
эталонной аналитики кейсодателя.

## Структура

Репозиторий разделён на три верхнеуровневые части: сервис, интерфейс и дизайн.

```
backend/
  app/
    main.py                 сборка приложения: lifespan, CORS, статика, роутеры
    config.py               настройки из окружения
    api/
      routes/chat.py        POST /api/v1/chat/messages — основной путь продукта
      routes/checks.py      POST/GET /api/v1/checks — полный проход и история
      routes/companies.py   витрина карточек, факты и полнота данных
      routes/health.py      GET /health
      routes/pages.py       отдача / и /report из frontend/
      schemas.py            модели ответов, они же схема Swagger
      deps.py               зависимости: настройки, Groq-клиент, стор диалогов
      serialization.py      приведение строк БД к JSON
    agent/
      runtime.py            provider-neutral create_agent, бюджеты, timeout, fallback
      master_model.py       ChatOpenAI/OpenRouter adapter только для Master
      tools.py              registry и wrapper full_company_check → run_check()
      comparison.py         сравнение 2–3 компаний одним вызовом инструмента
      langchain_tools.py    LangChain adapter над domain Tool Registry
      models.py             строгие ToolResult, AssistantResponse и UIBlock schemas
      prompt.py             versioned prompt native tool calling
      response.py           deterministic ToolResult → rich UI adapter
      conversations.py      доверенный контекст диалога отдельно от истории
      grounding.py          точные значения и optional eval/debug verifier/repair
      synthesis.py          нормализация проверенных данных и разбор ответа Master
    domain/
      facts.py              детерминированный слой фактов и паспорт полноты
      pipeline.py           один проход: факты → 4 агента → summary → запись
    llm/
      prompts.py            доменные спецификации 4 агентов и Summary-LLM
      agents.py             вызовы агентов, guardrails, шаблонный режим
      groq_client.py        клиент Groq с запасными моделями и паузами
    infrastructure/
      db.py                 пул подключений
      repository.py         SQL-запросы
      mongo.py              нормализация $date и $numberLong
  db/schema.sql             схема PostgreSQL
  db/seed_dictionary.sql    справочник кодов меток
   docs/db_design.md         пояснение к дизайну БД
  scripts/load_snapshot.py  загрузка выгрузки в базу
  scripts/demo_offline.py   проход по файлу без базы
  tests/                    pipeline regression и agent-first routing/API/UI contracts

frontend/                   интерфейс, сборка не нужна: браузер грузит ES-модули
  index.html                чат
  report.html               отчёт
  css/tokens.css            палитра и переменные
  css/base.css              сброс, типографика, общие примитивы
  css/chat.css              экран диалога и артефакты
  css/report.css            экран отчёта
  js/shared/dom.js          создание узлов, общее для обеих страниц
  js/chat/main.js           состояние диалога и подписки на события
  js/chat/artifacts.js      рендер allowlisted UIBlock
  js/chat/chart.js          SVG-график динамики
  js/chat/api.js            единственная точка обращения к chat API
  js/report/main.js         запрос прогона и сборка страницы
  js/report/sections.js     секции отчёта
  js/report/charts.js       колонки, парные серии, спарклайны
  js/report/format.js       иконки, словари кодов, форматирование
  js/report/facts.js        индекс фактов текущего прогона

design/prototype/           исходный Next.js-прототип, в рантайме не участвует
```

Интерфейс раздаётся тем же сервисом: `app/api/routes/pages.py` отдаёт
`index.html` и `report.html`, а `main.py` монтирует `frontend/` на `/static`.
Каталог переопределяется переменной `FRONTEND_DIR` — в Docker-образе это
`/srv/frontend`.

## Тесты

```bash
PYTHONPATH=. python -m pytest -q tests/test_agent_runtime.py tests/test_agent_response.py tests/test_chat_api.py
PYTHONPATH=. python -m pytest -q
```

Файл `pytest.ini` уже задаёт `pythonpath = .`, поэтому из каталога `backend/`
достаточно `pytest -q`.

Для синтаксиса изменённого JS из корня используйте `node --check <путь.js>`.
Эта проверка не валидирует импортируемые зависимости и работу DOM; пользовательский
сценарий проверяется в desktop-браузере. Мобильная приёмка приостановлена.

Запускайте узкие тесты изменённой функции. Behavioral evals с настоящими
моделями описаны в [evals/README.md](evals/README.md): они расходуют токены,
используют фиксированные снимки и не являются HTTP/DB e2e-проверкой.
`AGENT_EVALS.md` сохраняется дословно; технический PASS не доказывает
корректность аналитической прозы.

## Исторические проверки данных

Ниже сохранены результаты раннего legacy-прохода из обзора 03.09.2026.
Они не являются повторным замером текущего Master Agent или запущенной БД.
Актуальная навигация по датированным behavioral/live-отчётам — в
[AI_INDEX](../docs/AI_INDEX.md); новые проверки нужно фиксировать отдельно.

Схема накатана на PostgreSQL 16, выгрузка из 100 карточек загружена, все 100
ИНН прогнаны через `POST /api/v1/checks` в детерминированном режиме.

| Проверка | Результат |
|---|---|
| Загрузка выгрузки | 100 компаний, 100 снапшотов, 3873 исполнительных производства (770 без суммы), 2228 кодов ОКВЭД, 1643 репутационные метки, 194 года финотчётности |
| Повторный запуск загрузчика | счётчики не меняются, дублей нет |
| Прогон 100 ИНН через API | 100 успешных, 0 ошибок |
| Ссылки на несуществующие факты | 0 из 1352 утверждений, заземление 100 % |
| Пустой финансовый блок | 33 карточки получили сигнал `NO_DATA` и явное «невозможно оценить» — совпадает с фактом «финотчётности нет у 33 из 100» |
| `audit.v_okved_contradictions` | 46 карточек, ровно та цифра, что зафиксирована в гипотезе H4 |
| `audit.v_green_with_negatives` | 40 карточек, из них 8 с блокировкой счетов ФНС при зелёном светофоре и LOW |
| Область этого замера | Legacy pipeline; текущий regression этим замером не подтверждается |

Проход на живых моделях проверен на трёх контрагентах: все четыре блочных
агента и summary отвечают, статус `SUCCEEDED`, заземление от 88 до 100 %.
Ссылки на несуществующие факты модель иногда всё же выдаёт (3 из 26
утверждений в одном прогоне) — они помечаются `UNVERIFIED` и видны в метрике,
ради чего проверка и сделана.

## Ограничения PoC и MCP

- Запросы выполняются в рамках текущего API-вызова; очереди фоновых работ нет.
  NDJSON endpoint передаёт статусы этапов и готовый ответ, не токены модели.
- Контекст диалога живёт в памяти процесса и теряется при перезапуске.
  Аудит проверок в PostgreSQL не является постоянной историей чата.
- Данные статичны; нет промышленного обновления из системы банка.
- Новости доступны только при полной проверке и не меняют внутренние факты.
  Граф ограничен связями загруженного набора; подробнее в
  [WEB_NEWS](../docs/WEB_NEWS.md) и [COMPANY_CONNECTIONS](../docs/COMPANY_CONNECTIONS.md).
- Дополнительный verifier/repair выключен по умолчанию. Корректная структура
  и provenance не гарантируют правильность рассуждений Master.
- MCP пока не реализован. Сегодня backend читает PostgreSQL напрямую;
  планируется сервер чтения между backend и БД без изменения расчёта фактов.
  [Архитектура, DB impact и приёмка](../docs/MCP_DATA_ACCESS.md).

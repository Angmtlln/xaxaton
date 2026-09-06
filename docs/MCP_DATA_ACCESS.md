# MCP: доступ к данным контрагентов

Реализовано 06.09.2026. Docker по умолчанию читает карточки и выполняет поиск
через настоящий MCP-сервер. Прямой режим сохраняется для локальных скриптов,
тестов и явного отката; скрытого fallback на SQL нет.

## Архитектура и контракты

```text
Чат / REST API → domain tools → repository → CompanyDataReader
                                             ├─ mcp → MCP-сервер → PostgreSQL reader
                                             └─ direct → PostgreSQL reader
Аудит и история проверок → repository → PostgreSQL (отдельно от MCP)
```

Исходные JSON по-прежнему хранятся в `raw.report_documents.document`,
нормализованные карточки — в `core.*`. MCP предоставляет чтение этих данных,
а сервер обращается к БД через psycopg и существующие SQL. Наш backend является
MCP-клиентом; внешний harness не требуется. Дополнительных LLM-вызовов нет.

Сервер и клиент используют официальный `mcp==2.1.1`: `MCPServer`, Streamable
HTTP, JSON-ответы и режим без серверного состояния сессий. Доступны только
восемь allowlisted tools; SQL, запись, загрузка файлов и LLM-инструменты не публикуются.

| Tool | Аргументы внутри `params` | Результат помимо `version` |
|---|---|---|
| `get_latest_snapshot` | `inn` | `snapshot`, включая `null` для неизвестной компании |
| `list_companies` | прежние фильтры каталога, `limit` 1–200, `offset` ≥ 0 | `rows` |
| `search_companies` | `query` 2–256 символов, `limit` 1–5 | `rows`, `total`, `exact_total`; поиск идентичности по названию |
| `find_companies` | прежние фильтры и ranking, `limit` 1–50 | `rows`, `total`, при ranking — `eligible_total` |
| `get_selection_snapshots` | `snapshot_ids`, до 50 положительных ID | `rows` точных снимков |
| `get_connection_candidates` | `limit` 1–10 001 | `rows` проекций идентичности/связей |
| `get_snapshots_for_connections` | `inns`, до 6 ИНН | `rows` последних снимков соседей |
| `data_source_status` | без аргументов | `database` |

Версия результата — `company-data-v1`. Аргументы и результаты валидируются
Pydantic; ошибочный/несовместимый ответ не передаётся builders. Денежные поля
SQL `Decimal` передаются строками и восстанавливаются как Decimal; даты — ISO
и обратно в типизированные даты. Содержимое исходного `document` не нормализуется
на транспортной границе: сохраняются Mongo Extended JSON, пропуски, null и нули.
Пользовательский лимит поиска 25 не менялся; 50 нужны внутреннему подбору.

Клиент читает только structured content, не парсит текстовые описания.
Сервер ограничивает структурированный ответ 8 МиБ; клиент ограничивает HTTP-ответ
16 МиБ до разбора SDK, включая поток без Content-Length. Ответ не обрезается.
Каждое чтение владеет собственной MCP-сессией/транспортом в одной async-задаче;
timeout 10 с покрывает весь обмен. Отмена закрывает транспорт, повторов нет.
Четырёхсекундный deadline кросс-проверки связей сохраняется.

Поиск по названию использует дополнительную read-only view и функцию из миграции 008: [сценарии и контракт](COMPANY_NAME_SEARCH.md).

Основные файлы: `backend/app/mcp_data/` (контракты, сервер, клиент, ошибки),
`backend/app/infrastructure/company_reader.py` (выбор режима),
`backend/app/infrastructure/company_postgres.py` (SQL),
`backend/app/infrastructure/repository.py` (совместимый фасад и аудит).

## Запуск Docker

Команды выполняются из `backend/`. В `.env` должны быть обычные настройки проекта
и непустой `MCP_DB_PASSWORD`: отдельный URL-safe пароль. Его можно создать через
`python -c 'import secrets; print(secrets.token_urlsafe(32))'` и сохранить в локальном
`.env`, который исключён из Git. MCP-сервис получает только свой DB DSN, без
ключей моделей; `.env` не попадает в Docker-образ.

```bash
docker compose build api
docker compose up -d db
# Для новой БД или первоначальной загрузки предоставленной выгрузки:
docker compose run --rm --no-deps api python scripts/load_snapshot.py \
  --create-schema --file /data/contractors_audit.snapshot.json
# Для новой И существующей БД, после подготовки актуальной схемы:
docker compose run --rm --no-deps api python scripts/setup_mcp_access.py
docker compose up -d company-data-mcp api
curl --fail http://127.0.0.1:8001/health
curl --fail http://127.0.0.1:8000/health
```

Для существующей БД с актуальными витринами повторный импорт не требуется.
Если схема старая, сначала применить существующие миграции 003–006 по инструкциям
соответствующих функций, затем настроить MCP-доступ. `setup_mcp_access.py`
идемпотентно применяет 007 и 008 и устанавливает пароль; существующие данные не трогает.
При смене пароля повторить setup и пересоздать MCP-сервис.

API использует `http://company-data-mcp:8001/mcp`. Для внешнего по отношению к
Docker локального клиента — `http://127.0.0.1:8001/mcp`. Порт привязан только к
loopback; публичный интернет-доступ не настроен. Host/Origin ограничены именем
сервиса и локальными адресами; защита от DNS rebinding включена.

### Локальный MCP-клиент

Из `backend/`, после установки requirements; запущенный Docker MCP обязателен:

```bash
PYTHONPATH=. .venv/bin/python - <<'PY'
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main():
    async with streamable_http_client('http://127.0.0.1:8001/mcp') as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print([tool.name for tool in (await session.list_tools()).tools])
            result = await session.call_tool('get_latest_snapshot',
                {'params': {'inn': '7805327192'}})
            print('error:', result.is_error)
            print('version:', result.structured_content['version'])
            if not result.is_error:
                card = result.structured_content['snapshot']
                print('inn:', card['inn'], 'snapshot_id:', card['snapshot_id'])
asyncio.run(main())
PY
```

Для API вне Docker явно задать `COMPANY_DATA_BACKEND=mcp` и
`MCP_SERVER_URL=http://127.0.0.1:8001/mcp`. Без переменной локальный Python
использует `direct`. Для отдельного запуска самого сервера задать
`MCP_DATABASE_URL` и выполнить `python -m app.mcp_data.server`; по умолчанию
он слушает `127.0.0.1:8001` и не читает `.env` автоматически. Docker явно
передаёт `--host 0.0.0.0` внутри контейнера и публикует порт только на loopback.
Офлайн-демо по файлу остаётся отдельным режимом.

### Откат

Установить `COMPANY_DATA_BACKEND=direct` в `.env`, затем:

```bash
docker compose up -d --force-recreate api
```

Для возврата установить `mcp` и повторить команду. MCP-контейнер можно оставить
запущенным; в direct-режиме API не обращается к нему. Переключение не меняет данные.

## Права, ошибки и диагностика

DB impact: миграция `007_mcp_read_access.sql` добавляет роль
`contractors_mcp_reader` с USAGE на `core`/`raw` и SELECT на девять конкретных
таблиц/витрин. Скрипт создаёт LOGIN `contractors_mcp` с этим членством и включает
`default_transaction_read_only`. Запись запрещается правами, а не только этим
параметром; доступ к `audit.*` не выдаётся. Схема таблиц и записи не меняются.
Пароль передаётся setup через окружение, не через CLI/SQL-файл или логи.

- Неизвестный ИНН: `snapshot: null`, существующий `not_found`/404.
- Недоступный MCP: `source_unavailable`, REST 503; timeout: `timeout`, REST 503.
- Повреждённые данные/версия: безопасная ошибка, REST 502.
- Превышение размера: `result_too_large`, без частично обрезанного документа.
- Сбой дополнительной кросс-проверки: прежний `unavailable`, основная проверка
  продолжается. Отсутствие связей при этом не утверждается.

Чат сохраняет формат `AssistantResponse`; ошибки источника передаются через
существующий путь ошибки, включая NDJSON. `/health` сохраняет старые поля и
добавляет `data_source`, `data_source_available`; `ok` требует доступности и
аудитной БД, и выбранного источника. Ошибка MCP не переключает режим.

В логах клиента/сервера: operation, request ID, длительность, число строк,
размер результата и код ошибки. Исходные документы и секреты не логируются.

```bash
docker compose logs --tail=100 company-data-mcp api
```

## Проверки и границы результата

Быстрые протокольные и API-тесты (отдельный MCP-процесс с fixture reader):

```bash
PYTHONPATH=.:tests .venv/bin/python -m pytest -q \
  tests/test_mcp_protocol.py tests/test_mcp_integration.py
```

Интеграция с PostgreSQL создаёт **временную отдельную БД** в локальном Docker
кластере, загружает туда 100 карточек, применяет права, запускает отдельный MCP
и удаляет только созданную тестовую БД после завершения. Нужны права CREATE
DATABASE/ROLE; это команда для локального демо-кластера, не промышленного банка.

```bash
docker compose run --rm --no-deps \
  -e PYTHONPATH=/data/backend:/data/backend/tests \
  -e COMPANY_DATA_BACKEND=direct \
  -e TEST_MCP_ADMIN_DATABASE_URL=postgresql://postgres:postgres@db:5432/contractors \
  -w /data/backend api python -m pytest -q -s -p no:cacheprovider tests/test_mcp_database.py
```

Проверяются точность 100 документов и фактов, шесть операций, Decimal/null/даты,
фильтры/ranking, размер, timeout, отмена, параллельные чтения, перезапуск,
запрет записи даже при отключении transaction read-only и сохранение аудита.
Тесты фасада запрещают локальный PostgreSQL reader в MCP-режиме, исключая скрытый
обход. Банк `AGENT_EVALS.md` не менялся.

Ручной desktop-сценарий: полный анализ → «Объясни проще» → граф связей →
сравнение → подбор → PDF. Для демонстрации отказа временно остановить только
MCP-сервис, проверить degraded/503 и восстановить его; приложение не должно
объявлять отсутствие компании. Живые ответы моделей проверяются отдельно от
протокола и могут содержать ошибки интерпретации.

[Результаты приёмки и локальные замеры](evals/2026-09-06/mcp/report.md).
Реальная банковская интеграция и удалённая авторизация остаются за пределами v1.
Основание трактовки требования заказчика — сообщение пользователя; оригинальная
формулировка заказчика не предоставлена.

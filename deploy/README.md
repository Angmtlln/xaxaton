# ALEPH: временный сервер для защиты

Развёртывание: `/opt/aleph`, отдельный Compose project `aleph`, отдельный volume
`aleph_pgdata`. Backend вместе с frontend слушает только `127.0.0.1:18081`;
PostgreSQL и MCP доступны внутри Docker. Один worker сохраняет диалоги в памяти.
Перезапуск API завершает текущие сессии.

На сервере нужны исходники, `contractors_audit.snapshot.json`, ключи моделей в
`backend/.env` и случайные URL-safe `ALEPH_DB_PASSWORD`, `MCP_DB_PASSWORD` в
`deploy/.env`. Оба env-файла: права 600, не коммитить.

Команды из `/opt/aleph`:

```sh
docker compose --env-file deploy/.env -f deploy/compose.server.yml build api
docker compose --env-file deploy/.env -f deploy/compose.server.yml up -d db
docker compose --env-file deploy/.env -f deploy/compose.server.yml run --rm --no-deps api python scripts/load_snapshot.py --file /data/contractors_audit.snapshot.json
docker compose --env-file deploy/.env -f deploy/compose.server.yml run --rm --no-deps api python scripts/setup_mcp_access.py
docker compose --env-file deploy/.env -f deploy/compose.server.yml up -d company-data-mcp api
curl --fail http://127.0.0.1:18081/health
```

DB impact: новая изолированная БД использует существующие `schema.sql`,
`seed_dictionary.sql`, штатный импорт снимков и существующие миграции 007/008
через `setup_mcp_access.py`. Схема приложения не меняется; существующие базы
других проектов не затрагиваются. Не выполнять `down -v`.

Для демо временно используется HTTPS-адрес `analytics.neurovisya.ru`.
Сервис `metabase` остановлен; его nginx-конфигурация сохранена в
`/opt/aleph/rollback/metabase.nginx`. nginx проксирует на 18081 с отключённой
буферизацией ответа и таймаутом 300 секунд. GymBRO и exam-bot продолжают работать.

Вернуть прежний сайт и остановить ALEPH с сохранением базы:

```sh
sh /opt/aleph/deploy/rollback-server.sh
```

После деплоя проверить `/health`, полный запрос по ИНН, контекстный follow-up,
поток статусов, PDF и `/report`. Ответ HTTP 200 сам по себе не подтверждает
успех модели: проверить metadata и отсутствие provider fallback.

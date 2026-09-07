# Приёмка поиска компании по названию — 07.09.2026
Поиск, подтверждение неоднозначной компании и подсказки реализованы.
Изменение проверено на загруженных 100 карточках. Исходные данные не переписаны.

## Автоматические проверки

- Выбранная регрессия маршрутизации, multi-turn, latency, chat API/NDJSON, UX,
  shortlist/ranking, counterparty selection и MCP: **254 passed**.
- После добавления трёх проверок ошибок источника повторён затронутый набор
  `test_company_name_search.py`, `test_mcp_protocol.py`, `test_mcp_integration.py`:
  **41 passed** (включает ранее проверенные тесты, числа не складываются).
- Изолированная настоящая PostgreSQL + отдельный MCP-процесс:
  `tests/test_mcp_database.py` — **4 passed**. Включает parity 100 исходных
  карточек, невозможность записи ролью MCP, прежний audit flow, поиск названий,
  точные количества до LIMIT, полное/краткое название, кавычки, `е/ё`, пробелы,
  `%`/`_`, повторное применение миграции. Тестовые карточки жили только во временной БД.
- Четыре проверки JS-фрагмента поиска и вставки: короткий/длинный запрос,
  обычное название, `@` внутри вопроса с сохранением текста справа от курсора.
- `git diff --check` — без ошибок. Docker image пересобран, API/MCP пересозданы.
- Предупреждение тестов: существующий DeprecationWarning Starlette/AnyIO.

Изолированные старые routing fixtures выключают предварительную стадию разрешения
названия; новая feature-suite явно включает её и проверяет весь путь, включая
обычные и потоковые HTTP-запросы с выбором компании.

## Живой Master

OpenRouter, `z-ai/glm-5.3-flash`, prompt `master-risk-playbook-0.3.9-company-names`.
Счётчики ниже включают предварительное разрешение названия, но не внутренние
вызовы доменных моделей Groq в полной проверке. Это наблюдения отдельных прогонов,
а не SLA или полный semantic PASS. `grounding_status=not_requested` не означает
проверенное рассуждение.

| Сообщение | Секунды | Model calls | Tool calls | Результат |
|---|---:|---:|---:|---|
| Какая выручка у Электролид? | 12.72 | 3 | 2 | Выбран ИНН 7728380537, финансовый ответ без полной проверки |
| Объясни проще | 2.28 | 1 | 0 | Контекст без новых tools |
| Проверь другую компанию | 0.56 | 1 | 0 | Уточнение без повторной проверки активной компании |
| Какая выручка у Строй? | 0.65 | 1 | 1 | Четыре варианта, ожидание выбора |
| вторая | 2.31 | 2 | 1 | Выбран ИНН 4000036100; продолжен финансовый вопрос, отсутствие данных сохранено |
| Проверь Электролид | 12.76 | 2 | 2 | Полная проверка ИНН 7728380537 |

Master synthesis в этих ответах — `model`, кроме детерминированных уточнений.
На первом пробном прогоне модель пропускала имя в вопросе о выручке; исправлены
инструкция разрешения имени и устаревший intro prompt, затем повторён весь набор.
В полной проверке наблюдались 429 от Groq и штатное переключение доменной модели;
это отдельный provider fallback, поиск от Groq не зависит.

## Desktop

Playwright, 1440×1000: обычное название, `@` внутри вопроса, мышь,
ArrowDown + Enter (вставляет, не отправляет), Escape, сохранение исходного вопроса,
неоднозначный ответ чата. Скриншоты визуально просмотрены:
`output/playwright/name-suggestions.png`, `output/playwright/company-choice.png`.
Кнопка третьего варианта продолжила именно финансовый вопрос по ИНН 7751352552;
старые кнопки отключены. Скриншот `output/playwright/name-selected.png` просмотрен.
Лог `1b3efe27-8ec0-4b2d-9413-c61d1ba53ade`: get_financial_data, model_calls=2,
tool_calls=1, synthesis=model, latency_ms=44241; задержка основной модели высокая.
Исправлен контраст названия в подсказке на красном стартовом экране.
Артефакты браузера локальные, исключены из Git. Мобильная приёмка не проводилась.
Повторный браузерный поиск «Строй» занял около 19,6 с на стадии модели и поиска;
задержка провайдера заметно меняется между запусками.

## DB impact и окружение

Применена миграция 008: SQL-функция нормализации, read-only view, права MCP.
Количество исходных компаний после восстановления окружения — 100.
`/health`: status=ok, data_source=mcp, data_source_available=true, database=true.

Во время пересоздания сервисов Compose попытался пересоздать и старый контейнер БД;
порт 5432 занят macOS PostgreSQL. Docker-база восстановлена на
`127.0.0.1:55432` с прежним томом `backend_pgdata`, API остаётся на 8000,
MCP на 8001. Локальный PostgreSQL не останавливался. Конфигурация репозитория
не менялась; использовался временный Compose override.
Повторять обновление API/MCP следует с `--no-deps`, как описано в документации.
Для пересоздания самой Docker-базы с этой привязкой создайте Compose override
и укажите путь к нему в `DB_COMPOSE_OVERRIDE`:

```bash
docker compose -f docker-compose.yml -f "${DB_COMPOSE_OVERRIDE:?Укажите путь к Compose override}" up -d db
```

## Что проверить вручную

Обновить страницу, написать «Какая выручка у Электролид?», затем «Объясни проще».
Попробовать «Какая выручка у Строй?» и выбрать компанию; убедиться, что продолжается
именно вопрос о выручке. В новом черновике ввести «Какая выручка у @Элек» и выбрать
подсказку. Обычный ИНН, подбор и legacy report сохраняют прежний сценарий.

## Изменённые файлы

- `backend/app/agent/conversations.py`
- `backend/app/agent/models.py`
- `backend/app/agent/name_resolution.py`
- `backend/app/agent/prompt.py`
- `backend/app/agent/runtime.py`
- `backend/app/api/routes/chat.py`
- `backend/app/api/routes/companies.py`
- `backend/app/api/schemas.py`
- `backend/app/domain/company_search.py`
- `backend/app/infrastructure/company_postgres.py`
- `backend/app/infrastructure/company_reader.py`
- `backend/app/infrastructure/repository.py`
- `backend/app/mcp_data/client.py`
- `backend/app/mcp_data/contracts.py`
- `backend/app/mcp_data/server.py`
- `backend/db/migrations/008_company_name_search.sql`
- `backend/db/schema.sql`
- `backend/scripts/setup_mcp_access.py`
- `backend/tests/mcp_source_fixture.py`
- `backend/tests/test_agent_runtime.py`
- `backend/tests/test_company_name_search.py`
- `backend/tests/test_mcp_database.py`
- `backend/tests/test_mcp_integration.py`
- `backend/tests/test_mcp_protocol.py`
- `docs/AI_INDEX.md`
- `docs/COMPANY_NAME_SEARCH.md`
- `docs/MCP_DATA_ACCESS.md`
- `docs/evals/2026-09-07/company-name-search/report.md`
- `frontend/css/chat.css`
- `frontend/js/chat/api.js`
- `frontend/js/chat/artifacts.js`
- `frontend/js/chat/company-search.js`
- `frontend/js/chat/main.js`

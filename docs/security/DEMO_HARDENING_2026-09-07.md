# Локальные защиты демо — 07.09.2026

Выполнено по аудиту `525d131`. Владелец диалога, авторизация и изоляция пользователей
**не добавлены по явному решению пользователя**: это демо для хакатона.
Изменения ограничены текущим приложением, без новых сервисов/агентов.

## Что изменилось

| Аудит | Результат |
|---|---|
| S1: владельцы | Исключено из scope. Доступ по известному conversation UUID сохранён. CORS ограничен отдельно. |
| S2: системная роль | Данные и пользовательские реплики удалены из системной роли Master. Текущий нормализованный результат — ToolMessage, кэш — отдельное сообщение данных в user-роли, условия пользователя — user-роль. Нет фиктивных tool calls и второго полного payload. Добавлены правила недоверенных строк для legacy, selection routing и debug verifier/repair. Устойчивость live-модели к любым инъекциям не доказана. |
| S3: цвета модели | Модель не заполняет профиль и её risk_profile игнорируется. Backend задаёт unknown, кроме high по официальному регуляторному hard stop. Независимые банковские оценки сохранены. Интерпретация остаётся в прозе. |
| S4: нагрузка | Частично: лимит HTTP body, времени чтения тела, частоты по IP/процессу и одновременных запросов. Слот удерживается до конца stream; ошибки/отмена освобождают его. Новый диалог не вытесняет неистёкшие сессии. Денежного/суточного token-budget пока нет. |
| S5: смысл ответа | Не закрыто. Нет нового regex-фильтра, дополнительного LLM-stage или включения дорогого debug-grounding по умолчанию. Проверенные структурированные поля остаются под контролем backend; смысл свободного текста требует отдельной live-приёмки. |
| S6: новости | Частично: добавлен `WEB_NEWS_ENABLED=false`, отключающий web plugin и загрузку публикаций. При включённом plugin конфиденциальность поискового запроса и устойчивость к инструкциям страниц не гарантированы. |
| S7: периметр | Частично: PostgreSQL публикуется только на loopback; Docker image работает как UID 10001. API остаётся доступным на порту 8000 для показа демо. Права API-role, демо-пароль, mount `/data` и схема БД не менялись. |

## Как пользоваться

Обычный чат, targeted finance/legal, подбор, сравнение, новости и PDF используются
как раньше. В профиле справа серые оси означают «уровень не назначен», а не
«риск отсутствует». Красная регуляторная ось указывает на официальный сигнал
в снимке; его актуальность и охват по-прежнему требуют проверки. Подробности
раскрываются через «Что означают цвета?».

Настройки backend в `.env` (образец — `backend/.env.example`):

```dotenv
API_MAX_BODY_BYTES=32768
API_MAX_CONCURRENT=4
API_REQUESTS_PER_MINUTE=120
API_REQUESTS_PER_IP_PER_MINUTE=30
CORS_ALLOWED_ORIGINS=[]
WEB_NEWS_ENABLED=true
```

Лимиты применяются ко всем POST/PUT/PATCH/DELETE под `/api/`, в том числе chat,
stream, legacy checks и экспорту. Тело читается не более 10 секунд; превышение
размера — HTTP 413, времени загрузки — 408. Частота — 429, занятые слоты — 503;
два последних ответа содержат `Retry-After`. Health, чтение результатов и статика
не занимают слоты анализа. Квоты считаются на процесс и на последние 60 секунд,
включая запросы с неверным телом; IP берётся из ASGI peer, без самостоятельного
доверия к `X-Forwarded-For`. За reverse proxy нужно корректно ограничить доверенные
proxy в ASGI-сервере; иначе пользователи могут делить одну IP-квоту. Несколько
workers имеют раздельные квоты — текущий демо-запуск использует один worker.

CORS по умолчанию рассчитан на frontend и API с одного origin. Если frontend
размещён отдельно, явно задать, например:

```dotenv
CORS_ALLOWED_ORIGINS=["http://localhost:5173"]
```

Не включать wildcard ради обхода настройки. CORS не является авторизацией.
При заполнении 100 сессий продолжить существующий диалог или дождаться TTL
(30 минут бездействия). Новый запрос получает `conversation_capacity`, а
не удаляет чей-либо существующий контекст/PDF. Это не защищает от полного
заполнения общего пула анонимным клиентом, но устраняет вытеснение до TTL.

Для конфиденциальной сделки: `WEB_NEWS_ENABLED=false` и перезапуск backend.
Внешний поиск и загрузка новостей не выполняются, статус `not_configured`.
**Сам LLM по-прежнему получает контекст** через настроенного провайдера.
Это не автономный/offline режим и не гарантия конфиденциальности провайдера.

## Проверки

Из `backend/`:

```sh
PYTHONPATH=. .venv/bin/python -m pytest -q -rs tests/test_api_admission.py tests/test_agent_security.py tests/test_agent_runtime.py tests/test_agent_multiturn.py tests/test_agent_request_routing.py tests/test_grounding_behavior.py tests/test_agent_latency.py tests/test_risk_profile.py tests/test_chat_api.py tests/test_company_news.py tests/test_counterparty_selection.py tests/test_pdf_export.py tests/test_groq_and_grounding.py tests/test_agent_response.py tests/test_comparison.py tests/test_mcp_protocol.py
```

**304 passed, 2 skipped**; эти два opt-in browser/PDF-теста затем выполнены
отдельно в новом Docker image **с результатом 2 passed**. Реальные PDF и desktop
скачивание проверены под непривилегированным пользователем, с fixture-данными
и без внешней сети. Один warning — deprecated BlockingPortal в Starlette.
Fake-модели в unit-тестах не являются доказательством live-защиты GLM.
Отдельно `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_chat_progress.py`:
**3 passed**, регрессия протокола статусов и завершения стриминга.

Проверены атаки через source/user context и последующий follow-up; отсутствие
system authority; сохранение одного observation; игнорирование model risk colors;
отключение поиска; частота и ограничение IP без доверия к поддельному header;
Content-Length и chunked oversized body; общая ёмкость stream/legacy;
освобождение слота после ошибки/отмены; сохранение сессии при заполненном store.

Дополнительно: `node --check frontend/js/chat/dashboard.js`, `git diff --check`,
`docker compose -f backend/docker-compose.yml config --quiet` и сборка
`xaxaton-security-check:20260907` прошли. В Docker-тест передан только файл
fixture в read-only mount, без `.env`/ключей; контейнер удалён после теста.

Применение к уже запущенному демо требует пересборки/recreate соответствующих
контейнеров. В этой работе использовался отдельный проверочный image;
существующие API/MCP/БД не перезапускались. Изменение bind БД действует только
после её recreate; существующий volume сохраняется.

## Что проверить вручную после применения

- Обычный full check → «Почему?» → targeted finance/legal, подбор и сравнение.
- Профиль: нейтральные оси, красный только по официальному сигналу; банковские
  значения совпадают с источником. Новый PDF отражает этот же профиль.
- Настоящие origins демо, настройки proxy и доступность backend со стенда показа.
- `WEB_NEWS_ENABLED=false`: новости недоступны, внутренний анализ работает;
  с true — обычный full-check flow. Проверить actual provider requests отдельно.
- Сетевой bind БД после recreate, фактические DB grants и live prompt-injection
  corpus. Не считать эту ограниченную правку полноценным production-hardening.

Мобильные сценарии не дорабатывались. DB impact отсутствует: нет изменений
SQL, repository, хранимых моделей и grants; миграция не нужна. JSON-контракт
профиля сохранён. Новых зависимостей нет.

## Изменённые файлы

- Backend runtime/контекст: `backend/app/agent/runtime.py`, `conversations.py`,
  `prompt.py`, `response.py`, `news.py`, `grounding.py`, `selection_runtime.py`;
  `backend/app/llm/prompts.py`.
- HTTP/настройки: `backend/app/api/admission.py`, `backend/app/main.py`,
  `backend/app/config.py`, `backend/.env.example`.
- Запуск: `backend/Dockerfile`, `backend/docker-compose.yml`.
- Отображение: `frontend/js/chat/dashboard.js`.
- Тесты: `backend/tests/test_api_admission.py`, `test_agent_security.py`,
  `test_agent_runtime.py`, `test_agent_multiturn.py`, `test_chat_api.py`,
  `test_risk_profile.py`.
- Документация: `docs/AI_INDEX.md`, `docs/CHAT_UI.md`, `docs/MULTI_TURN_CHAT.md`,
  `docs/WEB_NEWS.md`, `docs/security/AGENT_SECURITY_AUDIT_2026-09-07.md`, этот файл.

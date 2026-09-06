# Перенос изменений Amir — 6 сентября 2026

Объединены `Amir@7a5747e` и `codex/chat-dashboard-ui@2bb3d7b` обычным merge.
Сохранены доработки сравнения до пяти компаний: компактные карточки, возраст,
семантические цвета, фильтр различий, источники, прокрутка и краткий AI-вывод.

## Что доступно пользователю

- «Найди компании с выручкой от 10 млн рублей, покажи первые 5» — подборка
  из загруженных снимков с применёнными условиями и общим числом совпадений.
  Поддержаны выручка, прибыль, сумма исков к ответчику, количество всех
  исполнительных производств, исходные оценки банка/ЗСК и стоп-факторы.
  Поиск по названию, отрасли и всему интернету этим инструментом не реализован.
- «Объясни проще» после подборки использует её проверенный контекст без
  повторного SQL/tool. Активная компания хранится отдельно.
- После проверки компании «Сравни с 3711039473» использует ИНН активной компании.
  Можно явно назвать от двух до пяти ИНН.
- Боковая панель показывает проверки и сравнения текущего диалога. Нажатие
  прокручивает к ответу; повторная проверка обновляет ссылку. На телефоне панель
  открывается кнопкой «Разделы», закрывается по выбору, Escape или нажатию вне.
  Перезагрузка восстанавливает её из sessionStorage; «Новый диалог» очищает.
  Это история внутри вкладки, не постоянная история диалогов в БД.
- В полном отчёте технические ссылки на поля перенесены в подсказки при
  наведении; исходные факты и API отчёта сохранены.
- JS/CSS повторно валидируются браузером через `Cache-Control: no-cache` и ETag.

## DB impact

Добавлена read-only view `core.v_company_shortlist`; таблицы, сохранённые
карточки и загрузчик не меняются. Для новой БД view включена в `schema.sql`.
Для существующей БД перед запуском нового API:

```bash
docker exec -i contractors-db psql -U postgres -d contractors -v ON_ERROR_STOP=1 \
  < backend/db/migrations/004_company_shortlist.sql
docker compose -f backend/docker-compose.yml up -d --build api
```

Миграция идемпотентна. В локальной Docker БД применена. NULL в отсутствующих
разделах не превращается в ноль: фильтр «без стоп-факторов» требует явного
массива отрицательных меток, а сумма исков — всех трёх компонент ответчика.
Финансы берутся за последний доступный год отдельно для каждой компании;
год показан в строке. Подборка не является полным анализом и не ранжирует риск.

## Проверки

Регрессия: shortlist, comparison, runtime, chat UX/API, multiturn, latency,
legacy pipeline, response, targeted response, risk profile, news, progress,
connections — 252 passed. PostgreSQL integration на временных таблицах —
1 passed: пропуск и null отделены от подтверждённого нуля, фильтры исключают
неизвестные значения. node --check изменённых JS и git diff --check — без ошибок.
Есть прежнее предупреждение Starlette/AnyIO.

Ручная приёмка: подборка → объяснение → сравнение выбранных ИНН;
проверка компании → «сравни с …»; desktop-навигация после перезагрузки.
Мобильная версия заморожена по запросу пользователя; дальнейшая доработка и
отдельная мобильная приёмка не выполняются.
Качество всех свободных формулировок AI не следует из автоматических тестов.


Живой runtime на текущей Docker БД и OpenRouter GLM-5.3-Flash:
подборка «выручка от 10 млн, первые 5» — 51 совпадение / 5 строк,
1 tool / 1 model call, 14,9 с; «Объясни проще» — 0 tools / 1 model call,
7,7 с, без fallback. Живой HTTP comparison двух ИНН — 10,2 с,
1 tool / 1 model call, без fallback. `grounding_status=not_requested`:
это проверка работоспособности, не полная семантическая приёмка AI-ответов.

Однозначные команды с выручкой/прибылью «от / до», без стоп-факторов и
необязательным «покажи первые N» разбираются backend только целиком; непонятный
остаток не игнорируется. Более сложные условия остаются на native tool routing.
В живых пробах этот маршрут модели выдавал некорректные аргументы: бюджет
его вызова увеличен до 2048 токенов, разрешена одна структурная коррекция
до SQL (до 3 model calls вместе с синтезом). При повторной ошибке поиск
не выполняется. Надёжность произвольных формулировок пока не подтверждена.

## Изменённые файлы

- `AGENTS.md`
- `backend/app/agent/conversations.py`
- `backend/app/agent/langchain_tools.py`
- `backend/app/agent/models.py`
- `backend/app/agent/prompt.py`
- `backend/app/agent/response.py`
- `backend/app/agent/runtime.py`
- `backend/app/agent/shortlist.py`
- `backend/app/agent/suggestions.py`
- `backend/app/agent/synthesis.py`
- `backend/app/agent/targeted_models.py`
- `backend/app/agent/tools.py`
- `backend/app/infrastructure/repository.py`
- `backend/app/main.py`
- `backend/db/migrations/004_company_shortlist.sql`
- `backend/db/schema.sql`
- `backend/tests/test_agent_runtime.py`
- `backend/tests/test_comparison.py`
- `backend/tests/test_shortlist.py`
- `backend/tests/test_shortlist_sql.py`
- `docs/AI_INDEX.md`
- `docs/AMIR_INTEGRATION.md`
- `docs/CHAT_UI.md`
- `frontend/css/chat.css`
- `frontend/css/report.css`
- `frontend/index.html`
- `frontend/js/chat/artifacts.js`
- `frontend/js/chat/main.js`
- `frontend/js/chat/navigation.js`
- `frontend/js/report/sections.js`

Desktop browser QA (1440×1000): пять строк живой подборки в renderer, два пункта
навигации, переходы к проверке/сравнению, восстановление после перезагрузки,
сброс в новом диалоге, без переполнения страницы и ошибок консоли.
Полная карточка для UI smoke получена с mock LLM на текущем снимке БД;
сравнение и подборка — из описанных выше живых прогонов.

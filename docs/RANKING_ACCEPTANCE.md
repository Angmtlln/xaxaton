# Приёмка выбора по показателям — 6 сентября 2026

## Автоматические проверки

189 passed: подборка, 25 сценариев ranking, runtime, multiturn, chat API,
comparison и legacy pipeline. После выделения инструкций ответа по ranking
повторно пройдены 115 тестов ranking/runtime/comparison.

```bash
cd backend
PYTHONPATH=. .venv/bin/python -m pytest tests/test_ranking.py tests/test_shortlist.py tests/test_agent_runtime.py tests/test_agent_multiturn.py tests/test_chat_api.py tests/test_comparison.py tests/test_pipeline_mock.py -q
```

PostgreSQL integration: 2 passed. Проверены идемпотентность миграции, сохранение
NULL и известных нулей, поиск победителя за пределами прежнего LIMIT, четыре
показателя и направления, отрицательная прибыль, последовательная сортировка,
равенства/ИНН, полнота и отсутствие дублей дополнительных ОКВЭД. Fixtures живут
в pg_temp и откатываются; core/raw не заполняются тестовыми компаниями.

```bash
docker exec -e PYTHONPATH=/data/backend -e TEST_SHORTLIST_DATABASE_URL=postgresql://postgres:postgres@db:5432/contractors -w /data/backend contractors-api python -m pytest tests/test_shortlist_sql.py -q -p no:cacheprovider
```

Host PostgreSQL на localhost:5432 конфликтует с Docker и отвечает «role postgres
does not exist», поэтому integration выполнена внутри Docker-сети.
`node --check frontend/js/chat/artifacts.js` и `git diff --check` — без ошибок.

## SQL и данные

Независимый SQL по текущей БД: торговая деятельность в основных или дополнительных
ОКВЭД AND proceeds >= 10000000 — 43 компании. С заполненной прибылью — 17;
26 исключаются из ранжирования по причине пропуска, а не из-за надёжности.

| Место | ИНН | Прибыль, ₽ | Финансовый год |
|---|---|---:|---:|
| 1 | 7728380537 | 615 769 000 | 2025 |
| 2 | 3123346195 | 328 673 000 | 2025 |
| 3 | 3711039473 | 276 311 000 | 2025 |
| 4 | 7724398540 | 96 981 000 | 2025 |
| 5 | 7802932240 | 71 554 000 | 2025 |

При порядке profit DESC, enforcement_count ASC, inn первые три ИНН совпадают
с первыми тремя строками этой таблицы. Прибыль различна, поэтому второй критерий
не влияет на этот конкретный результат; влияние второго критерия проверено
на изолированных SQL fixtures.

## Живой Master после финального обновления

OpenRouter, `z-ai/glm-5.3-flash`, prompt
`master-risk-playbook-0.3.4-metric-selection-v1`.
[Полные ответы и metadata](evals/2026-09-06/ranking/live.json).

| Реплика | Tool calls | Model calls | HTTP, с |
|---|---:|---:|---:|
| Найди торговые компании с выручкой от 10 млн и выбери 5 с наибольшей прибылью | 1 | 1 | 4.193 |
| Выбери 3 по прибыли и количеству исполнительных производств | 0 | 0 | 0.024 |
| Да | 1 | 1 | 2.780 |
| Почему эти? | 0 | 1 | 2.755 |

Оба выполненных выбора совпали с независимым SQL по составу и порядку ИНН:
43 совпадения, 17 доступных, 5/3 строки. Synthesis=model, без fallback/repair;
уточнение порядка детерминированное. «Почему эти?» использовало контекст без tool.

До выделения кратких инструкций ranking общий Risk Playbook провоцировал лишний
построчный анализ; один ответ назвал 41 вместо проверенных 43. SQL и UI оставались
корректными. Для ranking теперь используются отдельные короткие инструкции
объяснения порядка и полноты; в повторном проходе числа верны. Остальные
аналитические сценарии сохраняют прежнюю методологию.

`grounding_status=not_requested`: это проверка фильтрации, ранжирования, состояния
и ручная оценка приведённых ответов, не доказательство безошибочности любой прозы
Master. В ответе остаётся общая оговорка «годы могут различаться», хотя у выбранных
здесь компаний год одинаковый — 2025; годы в таблице точные.

## Desktop и развёртывание

В реальном браузере проверены состав топ-5, точные суммы выбранного показателя,
даты снимков, финансовые годы, подписи полноты, предложение нескольких критериев
и таблица топ-3 после «Да». Мобильная приёмка не проводилась.

Миграция 006 применена к локальной БД. Стандартная сборка упёрлась в таймаут
Docker Hub при получении python:3.11-slim. Локальный образ собран из установленного
backend-api с копированием обновлённых app/frontend без изменений зависимостей.
Compose пересоздал API из нового образа; /health подтверждает БД и API.

## Изменённые файлы

- `backend/app/agent/ranking.py`: разбор выбора и ожидающее подтверждения предложение.
- `backend/app/agent/models.py`, `targeted_models.py`: контракты порядка и результата.
- `backend/app/agent/shortlist.py`: нормализованные фильтры, полнота и подписи.
- `backend/app/infrastructure/repository.py`: сортировка и проверка полноты до LIMIT.
- `backend/app/agent/runtime.py`, `conversations.py`: выбор и подтверждение в диалоге.
- `backend/app/agent/langchain_tools.py`: запрет обхода подтверждения через native ranking.
- `backend/app/agent/synthesis.py`, `prompt.py`: доверенный контекст и инструкции объяснения.
- `backend/app/agent/response.py`: гидратация таблицы и fallback.
- `frontend/js/chat/artifacts.js`: порядок, полнота и даты в таблице.
- `backend/db/migrations/006_shortlist_ranking.sql`, `backend/db/schema.sql`: дата снимка во view.
- `backend/tests/test_ranking.py`, `backend/tests/test_shortlist_sql.py`: регрессия.
- `docs/ACTIVITY_SEARCH.md`, `docs/AI_INDEX.md`, `docs/RANKING_ACCEPTANCE.md`: описание и приёмка.

- `docs/evals/2026-09-06/ranking/live.json`: воспроизводимые свидетельства live-приёмки.

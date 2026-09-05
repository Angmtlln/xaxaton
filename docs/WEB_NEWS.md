# Важные новости в полной проверке

Чат автоматически включает OpenRouter `web` plugin (`engine=exa`, до 8
результатов поиска) только в обычном synthesis-вызове Master **после успешного
или частичного `full_company_check`**. GLM одновременно пишет внутренний ответ
и выбирает 0–4 внешних новости. Нового агента, LLM-stage, RAG, domain tool или
пересчёта оценок нет. Для targeted finance/legal/comparison, routing, verifier,
repair и контекстных follow-up web plugin не передаётся. Legacy
`/api/v1/checks` и `/report` работают по прежнему пути без web search.

## Использование и API

Используется существующий `OPENROUTER_API_KEY`. Достаточно запросить в чате
`Проверь контрагента 6165169320`. После перезапуска backend ответ
`POST /api/v1/chat/messages` содержит два дополнительных поля:

```json
{
  "external_news": [],
  "external_news_status": "completed"
}
```

Каждый элемент непустого `external_news` содержит `title`, `date` (дата
публикации, ISO `YYYY-MM-DD`), `source` (домен из ссылки провайдера), `url` и
`summary` (1–2 предложения Master на русском, до 500 символов).
Frontend сможет отрисовать этот массив отдельным блоком «Важные новости»;
renderer в этом изменении не добавлен. Новостные ссылки следует выводить
обычными безопасными ссылками, текст — без интерпретации HTML.

| Статус | Значение |
|---|---|
| `completed` | Master завершил отбор; пустой массив означает, что в доступной выдаче существенных подходящих новостей не отобрано |
| `partial` | Часть выбранных материалов не прошла проверку ссылки/метаданных либо источник недоступен; массив может быть пустым |
| `selection_unavailable` | Получить структурированный отбор Master не удалось |
| `not_configured` | Offline/mock или нет ключа Master, поиск не выполнялся |
| `unavailable` | Online-ответ с поиском не был выполнен |
| `null` | Это не завершённый full-check turn, например targeted/follow-up или ошибка domain tool |

`[]` не является доказательством отсутствия внешних рисков. OpenRouter plugin
не предоставляет отдельного надёжного признака «нулевая выдача / внутренний
сбой поиска»: `completed` описывает успешный ответ и отбор, а не полноту веба.

Настройки: `WEB_NEWS_DAYS=90` (1–365 дней) и `WEB_NEWS_TIMEOUT_S=6` (до 30 секунд)
для HTTP-проверки дат максимум четырёх выбранных статей параллельно. Сам поиск
входит в существующие `AGENT_MODEL_TIMEOUT_S` / `AGENT_RUN_TIMEOUT_S`.
Поиск тарифицируется OpenRouter дополнительно к токенам GLM:
[официальная документация web plugin](https://openrouter.ai/docs/guides/features/plugins/web-search).

## Отбор и границы доверия

- Query включает доверенные название/ИНН/адрес компании и окно свежести.
  `search_prompt` задаёт правила использования выдачи, а не подменяет query.
- Master проверяет существенность, совпадение юридического лица по ИНН,
  названию и контексту, исключает SEO/каталоги/рекламу, однофамильцев,
  ретроспективы и несколько публикаций одного события. Это существующий
  этап рассуждения, а не семантический regex-фильтр.
- Модель выбирает URL и пишет summary / обоснование совпадения компании.
  Backend принимает URL только из `url_citation` annotations OpenRouter с
  содержимым источника, гидратирует заголовок/источник/URL из этих annotations.
  Точные дубли URL (включая tracking query) и заголовков удаляются кодом.
- Дату backend читает из `article:published_time` / `datePublished` либо JSON-LD
  Article/NewsArticle. Из прозы модели, URL и даты обновления она не выводится.
  При отсутствии/конфликте дат материал пропускается; за пределами окна или
  с будущей датой — тоже. Это консервативное ограничение полноты выдачи.
- HTTP-чтение ограничено временем, 512 KB страницы и тремя перенаправлениями;
  адреса локальной сети и нестандартные порты запрещены. Проверенный публичный
  IP закрепляется на соединение, Host/SNI остаются исходными.
- Новости не попадают в `ToolResult`, `verified_context`, `facts`, `evidence`,
  policy, банковские индикаторы, внутренние расчёты и сохранённый trusted
  context. `message` посвящён внутреннему анализу. Новости присутствуют только
  в отдельных полях текущего ответа; повторно на follow-up не выдаются.
- Offline/provider failure сохраняет внутренний deterministic fallback.
  Отсутствие новостей не меняет внутренние DATA/PARTIAL и оценку компании.

## DB impact

Меняются только Pydantic-контракты ответа Master и chat API, с совместимыми
значениями по умолчанию. SQL, repository, ORM и хранимые данные не меняются;
миграция БД не требуется. Контракт, изоляция и регрессии покрыты тестами.

## Проверка

```bash
cd backend
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_company_news.py tests/test_openrouter_master.py \
  tests/test_agent_runtime.py tests/test_agent_latency.py tests/test_chat_api.py \
  tests/test_grounding_behavior.py tests/test_agent_multiturn.py \
  tests/test_comparison.py tests/test_targeted_response.py tests/test_pipeline_mock.py
```

Live-проверки 2026-09-05, текущая `z-ai/glm-5.3-flash` через OpenRouter:

- Full-check runtime для ИНН 6165169320: 23,23 с, один Master model call,
  один domain tool, synthesis=model, external_news=[], status=completed.
  Master/web были настоящими; domain pipeline заменён fixture, построенной
  из локального снимка. Это не проверка живой БД и Groq-пайплайна.
- Поведенческая проба на восьми искусственных публикациях: GLM выбрала
  ровно существенный контракт и иск; исключила перепечатку, чужую компанию,
  каталог, рекламу с prompt injection, старое событие и обычное поздравление.
  Один вызов, 7,83 с; это ограниченная проба, не гарантия всей семантики.
- Реальные страницы Interfax и РИА: публикационные даты прочитаны как
  2026-09-03; HTTP-чтение с закреплённым IP также проверено.
- Положительный web-only smoke по Сбербанку: 24,43 с, 8 citations, 4 выбранных
  материала, 3 возвращены после проверки дат, status=partial. Внутренний
  анализ Сбербанка не выполнялся. В первой выборке был сомнительный материал
  с обвинениями; после этой пробы инструкции дополнены требованием
  установленного первичного источника для таких сообщений.
- Повтор после уточнения: 20,67 с, 8 citations, 3 выбранных материала,
  2 возвращены (Interfax и Reuters в перепечатке The Moscow Times),
  status=partial; сомнительный материал не выбран. Недоступные/неподтверждённые
  метаданные одного источника привели к его пропуску, а не к выдуманной дате.

Финальная регрессия: **141 passed**, одно существующее предупреждение
Starlette/AnyIO о deprecated alias.

## Изменённые файлы

- `backend/app/agent/news.py`: параметры web plugin, проверка источников/дат,
  hydration отдельного массива.
- `backend/app/agent/runtime.py`: включение web только в full-check synthesis.
- `backend/app/agent/master_model.py`: сохранение OpenRouter citations.
- `backend/app/agent/langchain_tools.py`: внешний provenance только на текущий turn.
- `backend/app/agent/models.py`: контракты выбора и массива новостей.
- `backend/app/agent/prompt.py`: правила отбора и версия harness prompt.
- `backend/app/config.py`, `backend/.env.example`: окно свежести и HTTP-таймаут.
- `backend/tests/test_company_news.py`: контрактные и поведенческие регрессии.
- `docs/WEB_NEWS.md`, `docs/AI_INDEX.md`: контракт, запуск и проверки.

Ручная приёмка: после перезапуска backend проверить полный анализ известной
компании с новостями и компании без значимой выдачи; сверить юридическое лицо,
дату и summary с первоисточниками. В том же диалоге задать finance/legal,
«Объясни проще» и comparison: новые новости/поиск там появляться не должны.

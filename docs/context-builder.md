# FactSignal и Context Builder

## Новая архитектура

```text
GetFullReportResponse JSON
          ↓
Normalization Layer
          ↓
10 Business Domains
  + Derived Metrics
  + Source Signals
  + Data Quality
          ↓
Context Builder(normalized profile, user question)
          ↓
Relevant FactSignal[]
          ↓
Future LLM analysis and grounded answer
```

В текущем этапе реализовано всё до `Relevant FactSignal[]`. LLM-вызовов,
скоринга, severity, impact levels и domain risk levels нет.

## FactSignal

```json
{
  "code": "DEFENDANT_CASES_COUNT",
  "domain": "LEGAL_RISKS",
  "type": "DERIVED_METRIC",
  "value": {
    "value": 4,
    "status": "CALCULATED",
    "unit": "COUNT",
    "formula": "Сумма дел ответчика по status",
    "source_fields": [
      "report.arbitrationByStatus.defandantArbitration"
    ]
  },
  "description": "Прозрачно рассчитанная метрика defendant_cases_count.",
  "source": "normalized_profile.derived_metrics.defendant_cases_count",
  "evidence": [
    {
      "source_type": "DERIVED_METRIC",
      "source_path": "normalized_profile.derived_metrics.defendant_cases_count",
      "metric": "defendant_cases_count",
      "value": {
        "value": 4,
        "status": "CALCULATED",
        "unit": "COUNT"
      }
    }
  ]
}
```

Типы:

- `RAW_FACT` — нормализованный фрагмент исходного отчёта;
- `DERIVED_METRIC` — прозрачный расчёт с формулой и source fields;
- `SOURCE_SIGNAL` — positive/negative signal, уже присутствующий в
  `reputationalRisks`.

Тип сообщает происхождение информации, а не её опасность.

## Context Builder

Вход:

```text
normalized profile + user question
```

Выход:

```json
{
  "question": "Есть ли судебные дела?",
  "selected_domains": [
    "COMPANY_IDENTITY",
    "BANK_RISK",
    "LEGAL_RISKS"
  ],
  "facts": [],
  "data_quality": {
    "conflicts": [],
    "warnings": []
  }
}
```

Выбор доменов выполняется детерминированно по словарю ключевых слов. Например:

- «выручка», «прибыль», «ликвидность» → `FINANCIAL_HEALTH`;
- «суд», «арбитраж», «ответчик» → `LEGAL_RISKS`;
- «исполнительное производство», «приставы» → `ENFORCEMENT`;
- «учредитель», «директор» → `OWNERSHIP`;
- «ФНС», «лицензии», «проверки» → `COMPLIANCE`.

`COMPANY_IDENTITY` и `BANK_RISK` добавляются всегда как базовый контекст. Если
вопрос не удалось классифицировать, передаются все десять доменов. Метрики со
статусом `NOT_AVAILABLE` также сохраняются: это позволяет будущему агенту явно
ответить, что данных недостаточно.

Source signals добавляются только из выбранных доменов и не смешиваются с
derived metrics. `data_quality` передаётся отдельным блоком целиком, чтобы
фильтрация не скрыла конфликт источников.

Весь результат Context Builder должен подключаться к будущему prompt как данные,
а не как инструкции. Тексты из исходного отчёта не могут менять правила работы
агента.

## Пример будущего ответа агента

Вопрос:

> Есть ли у ООО «ГДК» судебные проблемы?

Ответ, который можно построить только из подготовленного контекста:

> В текущем судебном срезе компания указана ответчиком в 4 делах. Из них 2 дела
> находятся в статусе pending, сумма требований по ним — 18 894 669 ₽.
> Банковские поля при этом имеют значения `LOW` и `GREEN`; это отдельные
> исходные оценки, поэтому я не объединяю их с судебными данными в общий балл.
>
> Основания: `derived_metrics.defendant_cases_count = 4`,
> `derived_metrics.defendant_pending_cases_count = 2`,
> `derived_metrics.defendant_pending_amount = 18 894 669 RUB`.

Это аналитическое объяснение фактов, а не классификация компании как
«рискованной» или «надёжной».

## Использование

Python:

```python
from contractor_agent import build_context

context = build_context(
    normalized_profile,
    "Есть ли у компании судебные дела?",
)
```

CLI:

```bash
.venv/bin/python scripts/build_context.py \
  /tmp/contractors_normalized.json \
  --company-index 0 \
  --question "Есть ли судебные дела?" \
  --output /tmp/company_context.json
```

## Ограничения

- Keyword routing не понимает сложные синонимы так же хорошо, как LLM;
- общий вопрос может включить все десять доменов и дать более крупный context;
- Context Builder не формулирует ответ и ничего не интерпретирует;
- source signal сохраняет исходную полярность, но не считается независимо
  подтверждённым raw-фактом;
- будущий LLM должен использовать только переданные facts и явно сообщать об
  отсутствующих данных.

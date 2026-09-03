# Deterministic Risk Signal Layer

## Назначение

Слой принимает normalized business profile и возвращает только сигналы,
сработавшие по собственным детерминированным правилам:

```text
normalized profile → configured rules → derived signals
```

LLM не используется. Банковские `riskLevel`/`zskRiskLevel` не изменяются.
Общий числовой risk score не рассчитывается.

## RiskSignal

Source и derived signals имеют общий контракт:

```json
{
  "code": "REVENUE_DECLINE",
  "domain": "FINANCE",
  "type": "NEGATIVE",
  "impact_level": "MEDIUM",
  "origin": "DERIVED_RULE",
  "description": "Выручка снизилась год к году на 20.0%.",
  "evidence": [
    {
      "source_type": "DERIVED_METRIC",
      "source_path": "derived_metrics.revenue_growth_yoy",
      "metric": "revenue_growth_yoy",
      "value": -0.2
    }
  ],
  "rule": "revenue_growth_yoy <= -0.1",
  "rule_version": "1.0.0"
}
```

Допустимые значения:

- `type`: `POSITIVE`, `NEGATIVE`, `CONFLICT`;
- `origin`: `SOURCE_SIGNAL`, `DERIVED_RULE`;
- `impact_level`: `LOW`, `MEDIUM`, `HIGH`.

`impact_level` — уровень внимания по конкретному фактору. Он не является
вероятностью дефолта, банковским рейтингом или интегральной оценкой компании.

## Source signals и derived signals

Они не объединяются в один список:

- `compliance.source_signals` — нормализованные записи из
  `reputationalRisks`, `origin = SOURCE_SIGNAL`;
- `derived_signals` — результат `generate_risk_signal_dicts`,
  `origin = DERIVED_RULE`.

Некоторые derived rules, например `MASS_AUTH_PERSON`, используют source signal
как вход. Evidence при этом явно имеет `source_type = SOURCE_SIGNAL`; исходный
source signal продолжает существовать отдельно.

## Evidence

Каждый элемент evidence имеет ровно четыре поля:

```json
{
  "source_type": "DERIVED_METRIC",
  "source_path": "derived_metrics.active_execution_count",
  "metric": "active_execution_count",
  "value": 2
}
```

Используемые `source_type`:

- `NORMALIZED_FIELD` — нормализованный raw-факт;
- `DERIVED_METRIC` — прозрачная рассчитанная метрика;
- `SOURCE_SIGNAL` — сигнал из `reputationalRisks`;
- `DATA_QUALITY` — запись из `data_quality.conflicts`.

## Правила

| Code | Domain | Условие активации |
| --- | --- | --- |
| `NEGATIVE_PROFIT` | `FINANCE` | Последняя прибыль ниже configured threshold |
| `REVENUE_DECLINE` | `FINANCE` | `revenue_growth_yoy <= warning` |
| `NEGATIVE_EQUITY` | `FINANCE` | Последний капитал ниже configured threshold |
| `LOW_LIQUIDITY` | `FINANCE` | Коэффициент ликвидности ниже warning |
| `OPEN_DEFENDANT_CASES` | `LEGAL` | Pending-дела ответчика не меньше warning count |
| `HIGH_DEFENDANT_AMOUNT` | `LEGAL` | Pending-сумма или отношение всей defendant-суммы к выручке достигли warning |
| `REPEATED_ARBITRATION` | `LEGAL` | Число дел ответчика достигло warning count |
| `ACTIVE_EXECUTION_PROCEEDINGS` | `ENFORCEMENT` | Есть active proceedings |
| `MASS_AUTH_PERSON` | `OWNERSHIP` | Есть configured negative source signal |
| `OWNERSHIP_DATA_CONFLICT` | `OWNERSHIP` | Есть соответствующий `data_quality.conflicts` |
| `COMPANY_CLOSED` | `REGISTRY` | `is_active_company == false` |
| `TAX_REPUTATION_RISK` | `REGISTRY` | Есть configured negative source signal ФНС |

Для правил с `warning`/`critical` impact определяется прозрачно: warning даёт
`MEDIUM`, critical — `HIGH`. Остальные impact values также находятся в конфиге.

## Конфигурация и версионирование

Все числовые пороги и списки source codes находятся в
`config/risk_rules.json`. Код не содержит дублирующих пороговых констант.
Каждый derived signal сохраняет `rule_version` из конфига.

Фрагмент:

```json
{
  "rule_version": "1.0.0",
  "calibration_status": "UNKNOWN_DECISION",
  "finance": {
    "revenue_decline": {
      "warning": -0.1,
      "critical": -0.3
    }
  }
}
```

Конфиг валидируется при загрузке: обязательные пороги должны быть числами,
critical не может быть слабее warning, impact — только `LOW/MEDIUM/HIGH`.
`calibration_status = UNKNOWN_DECISION` явно фиксирует отсутствие экспертной
калибровки. Пороги являются MVP-гипотезами и требуют согласования с банковскими
риск-аналитиками перед production-эксплуатацией.

## Отсутствующие и неполные данные

- `NOT_AVAILABLE`, `NOT_APPLICABLE` и `value = null` не создают derived signal;
- `PARTIAL` может создать сигнал, если известная часть уже удовлетворяет правилу;
- неполнота остаётся доступна в `data_quality.warnings`;
- отсутствие source signal не доказывает отсутствие риска.

## CLI

```bash
.venv/bin/python scripts/generate_risk_signals.py \
  /tmp/contractors_normalized.json \
  --output /tmp/contractors_signals.json
```

Каждая запись вывода содержит отдельные `source_signals` и `derived_signals`.
Свой конфиг можно передать через `--config`.

Coverage по 100 компаниям:

```bash
.venv/bin/python scripts/signal_coverage_report.py \
  /tmp/contractors_normalized.json \
  --output output/risk-signals/signal_coverage_100.json
```

Отчёт содержит пары `signal_code`/`count` отдельно для source и derived layers;
`count` — число компаний, у которых встречается код, а не число дублей внутри
одной карточки.

## Ограничения

- Нет экспертной калибровки impact levels и порогов.
- Нет ground-truth выборки дефолтов или мошенничества.
- Source signals могут противоречить raw data; конфликт не скрывается и не
  разрешается автоматически.
- `MASS_AUTH_PERSON` и `TAX_REPUTATION_RISK` не имеют независимого raw-аналога
  в текущем файле и наследуют ограничение source signal.
- Финансовые метрики используют последний доступный год, который не обязательно
  совпадает с датой отчёта.
- Судебные метрики используют current status source с историческим fallback;
  два источника не суммируются.

Результаты проверки текущего snapshot: `docs/data-quality-audit.md`.

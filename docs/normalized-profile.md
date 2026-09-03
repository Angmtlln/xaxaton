# Нормализованный профиль контрагента

## Назначение

Слой детерминированно преобразует одну карточку `GetFullReportResponse` в
бизнес-профиль. Исходный JSON остаётся source of truth. Нормализатор не вызывает
LLM, не меняет банковские оценки и не рассчитывает общий risk score.

## Верхнеуровневая модель

```json
{
  "company_identity": {},
  "bank_risk": {},
  "ownership": {},
  "related_companies": {},
  "business_profile": {},
  "financial_health": {},
  "legal_risks": {},
  "enforcement": {},
  "compliance": {},
  "procurement": {},
  "derived_metrics": {},
  "data_quality": {
    "conflicts": [],
    "warnings": []
  }
}
```

Ровно десять бизнес-блоков:

| Блок | Содержимое | Исходные разделы |
| --- | --- | --- |
| `bank_risk` | Исходные `riskLevel`, ZSK и дата отчёта | `baseInfo.riskLevel`, `zskRiskLevel`, `reportDate` |
| `company_identity` | Идентификаторы, контакты, регистрация и статус | `baseInfo`, `status` |
| `ownership` | Учредители, доли, капитал и руководитель | `foundersInfo` |
| `related_companies` | Связанные компании и их руководители | `relatedCompanies` |
| `business_profile` | ОКВЭД, филиалы и налоговые режимы | `kindsOfActivityInfo`, `branchesInfo`, `taxSystem` |
| `financial_health` | Отчётность и коэффициенты | `finReports`, `coefficient` |
| `legal_risks` | История дел и текущее состояние по статусам | `arbitrationCases`, `arbitrationByStatus` |
| `enforcement` | Исполнительные производства | `executionProceedings` |
| `compliance` | Source signals, проверки и лицензии | `reputationalRisks`, `inspections`, `licenses` |
| `procurement` | Победы и контракты по годам | `procurements` |

`derived_metrics` и `data_quality` — сквозные служебные слои, а не
дополнительные бизнес-домены.

## Денежный формат

Каждое денежное значение внутри бизнес-блоков хранится одинаково:

```json
{
  "value": 1500.5,
  "unit": "RUB"
}
```

Это относится к капиталу и взносам учредителей, финансовой отчётности,
судебным суммам, исполнительным производствам и контрактам. Неизвестная сумма
имеет `value: null`, но сохраняет `unit: "RUB"`. Денежная `DerivedMetric`
использует тот же смысл через собственные поля `value` и `unit`.

## Derived metrics

```json
{
  "metric": "revenue_growth_yoy",
  "value": -0.31,
  "source_fields": [
    "report.finReports[0].common.proceeds",
    "report.finReports[1].common.proceeds"
  ],
  "formula": "(latest_revenue - previous_revenue) / previous_revenue",
  "status": "CALCULATED",
  "unit": "RATIO"
}
```

Статусы:

- `CALCULATED` — метрика рассчитана из всех нужных значений;
- `PARTIAL` — известная часть рассчитана, но один или несколько компонентов отсутствуют;
- `NOT_AVAILABLE` — данных недостаточно;
- `NOT_APPLICABLE` — формула неприменима, например знаменатель равен нулю.

Рассчитываются:

| Домен | Метрики |
| --- | --- |
| Identity | `company_age_years`, `is_active_company` |
| Ownership | `founder_count`, `max_owner_share`, `director_tenure_years`, `is_director_also_founder` |
| Relations | `related_company_count`, `unique_related_director_count` |
| Business | `okved_count`, `branches_count` |
| Finance | `revenue_growth_yoy`, `profit_margin`, `revenue_trend`, `profit_trend`, `current_assets_to_short_term_liabilities`, `cash_to_short_term_liabilities`, `receivables_share`, `liabilities_to_assets` |
| Legal | `total_arbitration_cases`, `defendant_cases_count`, `defendant_pending_cases_count`, `defendant_pending_amount`, `defendant_cases_share`, `arbitration_amount_to_revenue` |
| Enforcement | `total_execution_count`, `active_execution_count`, `active_execution_amount`, `latest_execution_date` |
| Procurement | `tender_count`, `winner_count`, `signed_contract_amount` |

## Source signals

`reputationalRisks.positive/negative` нормализуются в единый
`compliance.source_signals`. Это факты исходного отчёта, а не результат наших
правил:

```json
{
  "code": "ARBITRATION_DEFENDANT",
  "domain": "LEGAL",
  "type": "NEGATIVE",
  "impact_level": "MEDIUM",
  "origin": "SOURCE_SIGNAL",
  "description": "Описание из отчёта",
  "evidence": [
    {
      "source_type": "SOURCE_SIGNAL",
      "source_path": "report.reputationalRisks.negative[0]",
      "metric": "reputational_signal_code",
      "value": "ARBITRATION_DEFENDANT"
    }
  ],
  "rule": "Сигнал перенесён из reputationalRisks без изменения полярности.",
  "rule_version": "source-signal/v1"
}
```

Для source signals `impact_level` — приоритет внимания в интерфейсе, а не
вероятность дефолта: positive получает `LOW`, negative — `MEDIUM`. Исходная
полярность не меняется. Derived signals хранятся отдельно и описаны в
`docs/risk-signals.md`.

## Судебные источники

- `arbitrationCases` — исторические годовые агрегаты;
- `arbitrationByStatus` — текущее состояние по ролям и статусам.

Значения этих блоков никогда не складываются. Для текущих агрегированных метрик
используется `arbitrationByStatus`; `arbitrationCases` служит fallback только
при отсутствии status-блока. Разница между источниками допустима из-за разных
временных срезов и фиксируется warning `ARBITRATION_SCOPE_DIFFERENCE`.

## Data quality

`data_quality.conflicts` содержит подтверждённые противоречия raw data и
`reputationalRisks`, включая одинаковый source code одновременно с обеими
полярностями. `data_quality.warnings` содержит неполноту или различия срезов,
которые нельзя объявить конфликтом:

- `PARTIAL_DERIVED_METRICS`;
- `ARBITRATION_SCOPE_DIFFERENCE`.

Отсутствие source signal не трактуется как отсутствие риска. Проверяются только
коды, для которых в текущем JSON есть однозначный raw-аналог.

## Известные ограничения качества данных

- `tender_count` остаётся `NOT_AVAILABLE`: отдельного числа участий нет;
- некоторые суммы позволяют только `PARTIAL`-агрегацию;
- `liabilities.totalLiabilities` в данных похоже на итог баланса, поэтому
  `liabilities_to_assets = (assets - capital) / assets`;
- единица `RUB` принята из согласованной семантики отчёта, но в исходных полях
  нет отдельного машинно-читаемого currency code;
- реестровые source signals без raw-аналога нельзя независимо подтвердить;
- `arbitrationCases` и `arbitrationByStatus` могут закономерно различаться.

## Обработка типов и пропусков

- Mongo-даты `{"$date": ...}` переводятся в ISO 8601;
- Mongo-числа `{"$numberLong": ...}` и числовые строки — в `int`/`float`;
- поддерживаются пробелы и запятая как десятичный разделитель;
- отсутствующие коллекции становятся `[]`, наличие блока отражает `source_available`;
- пропуск не превращается в искусственный ноль, кроме явно документированных
  пустых коллекций и отсутствующих status buckets с нулевым count.

## Запуск

```bash
.venv/bin/python scripts/normalize_contractors.py \
  /path/to/contractors_audit.snapshot.json \
  --output /tmp/contractors_normalized.json
```

Пример пяти профилей: `output/normalized/contractors_sample_5.json`.
Изменения контракта v2 описаны в `docs/migrations/normalized-profile-v2.md`.

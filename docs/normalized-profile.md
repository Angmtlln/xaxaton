# Нормализованный профиль контрагента

## Назначение

Слой преобразует одну карточку `GetFullReportResponse` в бизнес-профиль,
пригодный для deterministic analytics и последующего объяснения. Исходный JSON
остаётся source of truth. Нормализатор не вызывает LLM и не рассчитывает общий
risk score.

## Верхнеуровневая схема

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
  "conflicts": []
}
```

Десять бизнес-блоков:

| Блок | Содержимое | Исходные разделы |
| --- | --- | --- |
| `bank_risk` | Банковский risk level, ZSK и дата отчёта без преобразования оценок | `baseInfo.riskLevel`, `zskRiskLevel`, `reportDate` |
| `company_identity` | Идентификаторы, названия, контакты, регистрация и статус | `baseInfo`, `status` |
| `ownership` | Учредители, доли, капитал и руководитель | `foundersInfo` |
| `related_companies` | Связанные компании и их руководители | `relatedCompanies` |
| `business_profile` | ОКВЭД, филиалы и налоговые режимы | `kindsOfActivityInfo`, `branchesInfo`, `taxSystem` |
| `financial_health` | Отчётность по годам и финансовые коэффициенты | `finReports`, `coefficient` |
| `legal_risks` | Годовые агрегаты дел и разрез по статусам/ролям | `arbitrationCases`, `arbitrationByStatus` |
| `enforcement` | Исполнительные производства | `executionProceedings` |
| `compliance` | Нормализованные сигналы, проверки и лицензии | `reputationalRisks`, `inspections`, `licenses` |
| `procurement` | Победы и подписанные контракты по годам | `procurements` |

`derived_metrics` и `conflicts` — сквозные аналитические слои, а не
дополнительные бизнес-домены.

## Формат рассчитанной метрики

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

- `CALCULATED` — значение рассчитано из всех нужных полей;
- `PARTIAL` — рассчитана сумма известных значений, но часть составляющих отсутствует;
- `NOT_AVAILABLE` — исходных данных недостаточно;
- `NOT_APPLICABLE` — формула неприменима, например знаменатель равен нулю.

## Пример профиля

Сокращённый пример; полный результат для пяти исходных карточек находится в
`output/normalized/contractors_sample_5.json`.

```json
{
  "company_identity": {
    "inn": "0000000000",
    "ogrn": "0000000000000",
    "name": "Пример",
    "registration_date": "2020-01-01T00:00:00.000Z",
    "status": "CURRENT"
  },
  "bank_risk": {
    "base_risk_level": "LOW",
    "zsk_risk_level": "GREEN",
    "report_date": "2026-08-27T21:00:00.000Z"
  },
  "ownership": {
    "source_available": true,
    "founders": [],
    "share_capital": null,
    "director": null
  },
  "related_companies": {
    "source_available": false,
    "companies": [],
    "directors": []
  },
  "business_profile": {},
  "financial_health": {
    "source_available": true,
    "amount_unit": "RUB",
    "statements": [],
    "coefficients": {}
  },
  "legal_risks": {},
  "enforcement": {},
  "compliance": {
    "positive_signals": [],
    "negative_signals": [],
    "inspections": [],
    "licenses": []
  },
  "procurement": {},
  "derived_metrics": {
    "is_active_company": {
      "metric": "is_active_company",
      "value": true,
      "source_fields": ["report.status.status"],
      "formula": "status == 'CURRENT'",
      "status": "CALCULATED",
      "unit": null
    }
  },
  "conflicts": []
}
```

## Нормализация сигналов

Каждый элемент `reputationalRisks` приводится к виду:

```json
{
  "code": "ARBITRATION_DEFENDANT",
  "domain": "legal_risks",
  "polarity": "negative",
  "description": "Описание из исходного отчёта",
  "source": "report.reputationalRisks.negative[0]"
}
```

Коды переводятся в `UPPER_SNAKE_CASE`; похожая на латинскую кириллическая `а`
в исходном коде `аrbitrationDefendant` нормализуется в латинскую `A`.

Если проверяемый raw-факт не соответствует полярности готового сигнала,
добавляется конфликт:

```json
{
  "type": "SOURCE_CONFLICT",
  "description": "Raw-признак ... противоречит полярности reputational signal.",
  "source_fields": ["raw JSON path", "signal JSON path"],
  "code": "ARBITRATION_DEFENDANT"
}
```

Проверяются только признаки, у которых в JSON есть однозначный raw-аналог:
арбитраж ответчиком, активные исполнительные производства, сайт, филиалы,
лицензии, связанные компании и госконтракты. Реестровые сигналы без raw-аналога
не перепроверяются искусственно.

## Рассчитываемые метрики

| Метрика | Формула или правило |
| --- | --- |
| `company_age_years` | `(report_date - registration_date) / 365.2425` |
| `is_active_company` | `status == "CURRENT"` |
| `founder_count` | Число учредителей |
| `max_owner_share` | Максимальная доля учредителя |
| `director_tenure_years` | `(report_date - appointment_date) / 365.2425` |
| `is_director_also_founder` | Совпадение по ИНН, иначе по нормализованному ФИО |
| `related_company_count` | Число связанных компаний |
| `unique_related_director_count` | Число уникальных ФИО руководителей связей |
| `okved_count` | Основной ОКВЭД плюс дополнительные |
| `branches_count` | `branchesCount`, fallback — длина списка филиалов |
| `revenue_growth_yoy` | `(latest_revenue - previous_revenue) / previous_revenue` |
| `profit_margin` | `profit / revenue` для последнего совместно заполненного года |
| `revenue_trend` | `GROWING`, `DECLINING`, `STABLE`, `MIXED` минимум по двум годам |
| `profit_trend` | То же правило для прибыли |
| `current_assets_to_short_term_liabilities` | `current_assets / short_term_liabilities` |
| `cash_to_short_term_liabilities` | `cash / short_term_liabilities` |
| `receivables_share` | `receivables / total_assets` |
| `liabilities_to_assets` | `(total_assets - capital) / total_assets` |
| `total_arbitration_cases` | `commonCount`, fallback — сумма status, затем yearly |
| `defendant_cases_count` | Сумма дел ответчика по статусам, fallback — yearly |
| `defendant_pending_cases_count` | Число pending-дел ответчика |
| `defendant_pending_amount` | Сумма pending-требований к ответчику |
| `defendant_cases_share` | `defendant_cases_count / total_arbitration_cases` |
| `arbitration_amount_to_revenue` | `defendant_amount_rub / latest_revenue_rub` |
| `total_execution_count` | Число исполнительных производств |
| `active_execution_count` | Число производств с `active == true` |
| `active_execution_amount` | Сумма известных `amount` активных производств |
| `latest_execution_date` | Максимальная дата производства |
| `tender_count` | Всегда `NOT_AVAILABLE`: отдельного поля участий в JSON нет |
| `winner_count` | Сумма `tenderWinnerCnt` |
| `signed_contract_amount` | Сумма `contractSignedAmt` |

## Решения по неоднозначным полям

- `liabilities.totalLiabilities` в 192 из 194 заполненных строк совпадает с
  `assets.totalAssets`, то есть фактически представляет итог баланса. Поэтому
  долговая доля считается как `(assets - capital) / assets`, а не как почти
  всегда бесполезное `totalLiabilities / totalAssets`.
- `receivables_share` означает долю дебиторской задолженности во всех активах.
- Денежные поля `finReports` считаются заданными в рублях: например, raw-выручка
  `60746000` отображается готовым сигналом как `60746 тыс. руб.`. Поэтому
  `arbitration_amount_to_revenue` делит две суммы без дополнительного масштаба.
- `tender_count` не подменяется числом побед. Значение остаётся `null` со
  статусом `NOT_AVAILABLE`.

## Обработка пропусков и типов

- Mongo-даты `{"$date": ...}` переводятся в ISO 8601.
- Mongo-числа `{"$numberLong": ...}` и другие стандартные числовые обёртки
  переводятся в `int` или `float`.
- Числовые строки поддерживают пробелы, неразрывные пробелы и запятую как
  десятичный разделитель.
- Отсутствующие скалярные значения сохраняются как `null`.
- Отсутствующие необязательные коллекции нормализуются в `[]`; наличие самого
  исходного блока отражается полем `source_available`.
- Финансовые коэффициенты и отношения не получают искусственный ноль при
  отсутствии исходных значений.

## Запуск

Первые пять карточек:

```bash
.venv/bin/python scripts/normalize_contractors.py \
  /path/to/contractors_audit.snapshot.json \
  --limit 5 \
  --output output/normalized/contractors_sample_5.json
```

Все карточки нормализуются той же командой без `--limit`.

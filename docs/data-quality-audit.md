# Data quality audit

Дата проверки: 2026-09-03.

Источник: 100 карточек `contractors_audit.snapshot.json`, нормализованных текущей
версией кода.

## Результат

- 100 из 100 профилей успешно нормализованы и JSON-сериализуемы;
- 1 643 source signals сохранены как `FactSignal` без собственной оценки риска;
- 3 100 рассчитанных показателей сохранены как `DerivedMetric` и не
  классифицируются;
- у всех source signals есть source path и evidence;
- Context Builder проверен на финансовом, судебном и общем вопросах для всех
  100 компаний;
- все проверенные денежные поля имеют `{value, unit: "RUB"}`;
- судебные current/history значения не суммируются.

## Data quality issues

Найдено 16 конфликтов raw data и `reputationalRisks`:

| Code | Count |
| --- | ---: |
| `GOVERNMENT_CONTRACT` | 10 |
| `ARBITRATION_DEFENDANT` | 6 |

Найдено 37 warnings:

| Type | Count |
| --- | ---: |
| `ARBITRATION_SCOPE_DIFFERENCE` | 26 |
| `PARTIAL_DERIVED_METRICS` | 11 |

`ARBITRATION_SCOPE_DIFFERENCE` не является ошибкой: исторический и текущий
срезы могут различаться. Warning нужен, чтобы downstream agent не пытался
автоматически разрешить расхождение или сложить значения.

## Ограничения проверки

- единица `RUB` подтверждается семантикой отчёта, но не отдельным currency field;
- source signals без raw-аналога проверены только на структуру, но не на
  фактическую истинность;
- аудит относится к указанному JSON snapshot и должен повторяться при замене
  датасета или логики нормализации.

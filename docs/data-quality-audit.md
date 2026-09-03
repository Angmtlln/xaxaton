# Data quality audit

Дата проверки: 2026-09-03.

Источник: 100 карточек `contractors_audit.snapshot.json`, нормализованных текущей
версией кода.

## Результат

- 100 из 100 профилей успешно нормализованы и JSON-сериализуемы;
- 1 643 source signals сохранены отдельно от 119 derived signals;
- у всех source/derived signals полный контракт `RiskSignal`;
- у каждого derived signal есть evidence с `source_type`, `source_path`,
  `metric`, `value`;
- все проверенные денежные поля имеют `{value, unit: "RUB"}`;
- duplicate derived codes внутри одной компании не обнаружены;
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

## Signal coverage

Полный отчёт по кодам и числу компаний находится в
`output/risk-signals/signal_coverage_100.json`. Counts считаются по компаниям:
повтор кода внутри одной карточки не увеличивает coverage.

## Ограничения проверки

- единица `RUB` подтверждается семантикой отчёта, но не отдельным currency field;
- корректность бизнес-порогов не валидировалась на дефолтах или экспертной
  разметке — такой выборки нет;
- source signals без raw-аналога проверены только на структуру, но не на
  фактическую истинность;
- аудит относится к указанному JSON snapshot и должен повторяться при замене
  датасета или изменении версии правил.

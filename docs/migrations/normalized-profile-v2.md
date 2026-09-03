# Migration: normalized profile v1 → v2

Изменение обратно несовместимо на уровне JSON-контракта.

## Mapping

| v1 | v2 |
| --- | --- |
| `conflicts` | `data_quality.conflicts` |
| отсутствовало | `data_quality.warnings` |
| `compliance.positive_signals` + `negative_signals` | `compliance.source_signals[]` с полем `type` |
| `RiskSignal.severity` | `RiskSignal.impact_level` |
| отсутствовало | `RiskSignal.type`, `origin`, `rule_version` |
| evidence `source_fields[]` | evidence `source_type`, `source_path`, `metric`, `value` |
| денежный scalar | `{ "value": number|null, "unit": "RUB" }` |
| CLI `risk_signals` | CLI `source_signals` + `derived_signals` |

## Consumer changes

1. Не объединять `source_signals` и `derived_signals` без явной маркировки
   `origin`.
2. Читать денежное число из `.value`, проверяя `.unit == "RUB"`.
3. Использовать `impact_level` только как приоритет конкретного фактора, не как
   вероятность дефолта.
4. Показывать или логировать `data_quality` рядом с выводами, которые используют
   затронутые источники.
5. При кэшировании профилей пересоздать их из исходного JSON новым
   нормализатором, а не преобразовывать частично старые документы.

## DB impact

База данных в MVP отсутствует, поэтому SQL/NoSQL migration не требуется.
Если normalized profiles сохраняются внешним потребителем, нужна миграция его
документной схемы по mapping выше либо полная регенерация из source-of-truth
JSON.

## Проверка миграции

Контракт покрыт unit-тестами normalization/risk signals и интеграционными
тестами на пяти реальных компаниях. Файл
`output/normalized/contractors_sample_5.json` пересоздан в формате v2.

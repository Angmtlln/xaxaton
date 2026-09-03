# Migration: RiskSignal → FactSignal

Архитектура больше не создаёт собственные оценочные risk decisions.

## Удалено

- `RiskSignal`;
- `severity`, `impact_level`, `origin`, `rule`, `rule_version`;
- `RiskRuleConfig` и `config/risk_rules.json`;
- deterministic risk rules и risk coverage report;
- derived codes вроде `LOW_LIQUIDITY`, `REPEATED_ARBITRATION` и
  `HIGH_DEFENDANT_AMOUNT`;
- CLI генерации risk signals.

Исходные показатели, на которых основывались эти коды, не удалены: они остаются
в `derived_metrics` без оценочной классификации.

## Добавлено

`FactSignal`:

```json
{
  "code": "...",
  "domain": "...",
  "type": "RAW_FACT | DERIVED_METRIC | SOURCE_SIGNAL",
  "value": null,
  "description": "...",
  "source": "...",
  "evidence": []
}
```

`build_context(normalized_profile, user_question)` выбирает релевантные домены и
возвращает факты для будущего LLM.

## Source signal mapping

| Старое поле | Новое поле |
| --- | --- |
| `origin = SOURCE_SIGNAL` | `type = SOURCE_SIGNAL` |
| `type = POSITIVE/NEGATIVE` | `value = POSITIVE/NEGATIVE` |
| `impact_level` | удалено |
| `rule`, `rule_version` | удалено |
| source path только в evidence | `source` + evidence |

## DB impact

Базы данных в MVP нет. Сохранённые normalized JSON нужно пересоздать из
source-of-truth JSON, потому что контракт `compliance.source_signals` изменился.
Сохранённые outputs risk-signal layer больше не должны использоваться.

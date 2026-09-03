# Deterministic Risk Signal Layer

## Назначение

Слой принимает только нормализованный бизнес-профиль и возвращает список
сработавших risk signals:

```text
normalized business profile
↓
deterministic rules
↓
risk signals with metric evidence
```

Он не использует LLM, не меняет банковские `riskLevel`/`zskRiskLevel` и не
создаёт общий risk score.

## Модель сигнала

```json
{
  "code": "REVENUE_DECLINE",
  "domain": "FINANCE",
  "severity": "MEDIUM",
  "description": "Выручка снизилась год к году на 20.0%.",
  "evidence": [
    {
      "metric": "revenue_growth_yoy",
      "value": -0.2,
      "source_fields": [
        "report.finReports[0].common.proceeds",
        "report.finReports[1].common.proceeds"
      ]
    }
  ],
  "rule": "revenue_growth_yoy < 0"
}
```

Допустимые `severity`: `LOW`, `MEDIUM`, `HIGH`. Сейчас все правила независимы:
severity не складываются и не преобразуются в числовой score.

## Правила

| Code | Domain | Severity | Детерминированное условие | Source metric |
| --- | --- | --- | --- | --- |
| `NEGATIVE_PROFIT` | `FINANCE` | `MEDIUM` | Последняя доступная прибыль `< 0` | `latest_profit` из `financial_health.statements` |
| `REVENUE_DECLINE` | `FINANCE` | `MEDIUM` | `revenue_growth_yoy < 0` | `revenue_growth_yoy` |
| `NEGATIVE_EQUITY` | `FINANCE` | `HIGH` | Последний доступный капитал `< 0` | `latest_equity` из `financial_health.statements` |
| `LOW_LIQUIDITY` | `FINANCE` | `HIGH` | `current_assets_to_short_term_liabilities < 1` | `current_assets_to_short_term_liabilities` |
| `OPEN_DEFENDANT_CASES` | `LEGAL` | `MEDIUM` | `defendant_pending_cases_count > 0` | `defendant_pending_cases_count` |
| `HIGH_DEFENDANT_AMOUNT` | `LEGAL` | `HIGH` | Pending-сумма `>= 1 000 000 ₽` или вся defendant-сумма `>= 10%` выручки | `defendant_pending_amount`, `arbitration_amount_to_revenue` |
| `REPEATED_ARBITRATION` | `LEGAL` | `MEDIUM` | `defendant_cases_count >= 3` | `defendant_cases_count` |
| `ACTIVE_EXECUTION_PROCEEDINGS` | `ENFORCEMENT` | `HIGH` | `active_execution_count > 0` | `active_execution_count`, `active_execution_amount` |
| `MASS_AUTH_PERSON` | `OWNERSHIP` | `HIGH` | Есть negative signal `MASS_AUTHPERSONS` | Нормализованный reputational signal |
| `OWNERSHIP_DATA_CONFLICT` | `OWNERSHIP` | `MEDIUM` | Есть ownership `SOURCE_CONFLICT` или один ownership signal имеет две полярности | `conflicts`, нормализованные signals |
| `COMPANY_CLOSED` | `REGISTRY` | `HIGH` | `is_active_company == false` | `is_active_company` |
| `TAX_REPUTATION_RISK` | `REGISTRY` | `HIGH` | Есть negative signal `FNS_BLOCKING`, `TAX_ARREARS` или `TAX_REPORTING` | Нормализованный reputational signal |

## Конфигурация порогов

Пороговые значения находятся в `RiskRuleConfig`, а не разбросаны по функциям:

```python
from contractor_agent import RiskRuleConfig, generate_risk_signal_dicts

config = RiskRuleConfig(
    low_liquidity_ratio=1.0,
    high_defendant_amount_rub=1_000_000,
    high_defendant_amount_to_revenue=0.10,
    repeated_arbitration_cases=3,
)

signals = generate_risk_signal_dicts(normalized_profile, config)
```

Это MVP-гипотезы, а не статистически или экспертно откалиброванная модель.
Перед production-использованием пороги и severity нужно согласовать с
юристами, риск-аналитиками и кейсодателем.

## Правила работы с отсутствующими данными

- Сигнал не создаётся, если source metric имеет `NOT_AVAILABLE`,
  `NOT_APPLICABLE` или `value = null`.
- Метрика со статусом `PARTIAL` может создать сигнал, если известного значения
  уже достаточно для выполнения правила. Ее неполнота сохраняется в исходном
  normalized profile.
- Отсутствие готового reputational signal не интерпретируется как отсутствие
  риска.
- `MASS_AUTH_PERSON` и `TAX_REPUTATION_RISK` используют готовые сигналы, потому
  что в предоставленном JSON нет независимых raw-полей соответствующих
  реестров. Они не выдаются за самостоятельно подтверждённые raw-факты.

## Запуск

Сначала создать нормализованные профили:

```bash
.venv/bin/python scripts/normalize_contractors.py \
  /path/to/contractors_audit.snapshot.json \
  --limit 5 \
  --output output/normalized/contractors_sample_5.json
```

Затем применить правила:

```bash
.venv/bin/python scripts/generate_risk_signals.py \
  output/normalized/contractors_sample_5.json \
  --output output/risk-signals/contractors_sample_5.json
```

CLI сохраняет идентификацию компании, исходные банковские оценки и список
детерминированных сигналов.

## Покрытие реальным JSON

Интеграционный тест использует пять карточек с индексами `21`, `16`, `68`,
`55`, `63`. Вместе они покрывают все десять типов сигналов, фактически
встречающихся в предоставленных 100 карточках. `COMPANY_CLOSED` и
`OWNERSHIP_DATA_CONFLICT` в этом датасете не встречаются и проверяются отдельным
unit-тестом.

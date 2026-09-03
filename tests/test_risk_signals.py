from __future__ import annotations

import json
import unittest

from contractor_agent.risk_signals import RiskRuleConfig, generate_risk_signal_dicts


def metric(name: str, value: object, status: str = "CALCULATED") -> dict[str, object]:
    return {
        "metric": name,
        "value": value,
        "source_fields": [f"report.test.{name}"],
        "formula": f"test formula for {name}",
        "status": status,
        "unit": None,
    }


class RiskSignalsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = {
            "financial_health": {
                "statements": [
                    {
                        "year": 2024,
                        "profit": 100,
                        "liabilities": {"capital": 500},
                    },
                    {
                        "year": 2025,
                        "profit": -200,
                        "liabilities": {"capital": -50},
                    },
                ]
            },
            "derived_metrics": {
                "revenue_growth_yoy": metric("revenue_growth_yoy", -0.20),
                "current_assets_to_short_term_liabilities": metric(
                    "current_assets_to_short_term_liabilities", 0.75
                ),
                "defendant_pending_cases_count": metric(
                    "defendant_pending_cases_count", 2
                ),
                "defendant_pending_amount": metric(
                    "defendant_pending_amount", 2_000_000
                ),
                "arbitration_amount_to_revenue": metric(
                    "arbitration_amount_to_revenue", 0.20
                ),
                "defendant_cases_count": metric("defendant_cases_count", 4),
                "active_execution_count": metric("active_execution_count", 1),
                "active_execution_amount": metric(
                    "active_execution_amount", 50_000
                ),
                "is_active_company": metric("is_active_company", False),
            },
            "compliance": {
                "negative_signals": [
                    {
                        "code": "MASS_AUTHPERSONS",
                        "domain": "ownership",
                        "polarity": "negative",
                        "description": "Массовый руководитель",
                        "source": "report.reputationalRisks.negative[0]",
                    },
                    {
                        "code": "FNS_BLOCKING",
                        "domain": "compliance",
                        "polarity": "negative",
                        "description": "Есть блокировка ФНС",
                        "source": "report.reputationalRisks.negative[1]",
                    },
                ],
                "positive_signals": [],
            },
            "conflicts": [
                {
                    "type": "SOURCE_CONFLICT",
                    "code": "INVALID_AUTHPERSONS_DATA",
                    "description": "Противоречие в данных руководителя",
                    "source_fields": [
                        "report.foundersInfo.authPerson",
                        "report.reputationalRisks.positive[0]",
                    ],
                }
            ],
        }

    def test_generates_all_initial_signal_types(self) -> None:
        signals = generate_risk_signal_dicts(self.profile)

        self.assertEqual(
            {signal["code"] for signal in signals},
            {
                "NEGATIVE_PROFIT",
                "REVENUE_DECLINE",
                "NEGATIVE_EQUITY",
                "LOW_LIQUIDITY",
                "OPEN_DEFENDANT_CASES",
                "HIGH_DEFENDANT_AMOUNT",
                "REPEATED_ARBITRATION",
                "ACTIVE_EXECUTION_PROCEEDINGS",
                "MASS_AUTH_PERSON",
                "OWNERSHIP_DATA_CONFLICT",
                "COMPANY_CLOSED",
                "TAX_REPUTATION_RISK",
            },
        )

    def test_every_signal_has_metric_evidence_and_rule(self) -> None:
        signals = generate_risk_signal_dicts(self.profile)

        for signal in signals:
            self.assertIn(signal["severity"], {"LOW", "MEDIUM", "HIGH"})
            self.assertTrue(signal["description"])
            self.assertTrue(signal["rule"])
            self.assertTrue(signal["evidence"])
            for evidence in signal["evidence"]:
                self.assertTrue(evidence["metric"])
                self.assertTrue(evidence["source_fields"])
        self.assertNotIn("risk_score", json.dumps(signals, ensure_ascii=False))

    def test_thresholds_are_configurable_and_boundaries_are_explicit(self) -> None:
        config = RiskRuleConfig(
            low_liquidity_ratio=0.5,
            high_defendant_amount_rub=3_000_000,
            high_defendant_amount_to_revenue=0.3,
            repeated_arbitration_cases=5,
        )

        codes = {
            signal["code"]
            for signal in generate_risk_signal_dicts(self.profile, config)
        }

        self.assertNotIn("LOW_LIQUIDITY", codes)
        self.assertNotIn("HIGH_DEFENDANT_AMOUNT", codes)
        self.assertNotIn("REPEATED_ARBITRATION", codes)

    def test_missing_data_does_not_create_signals(self) -> None:
        self.assertEqual(generate_risk_signal_dicts({}), [])

    def test_generation_does_not_mutate_profile(self) -> None:
        before = json.dumps(self.profile, ensure_ascii=False, sort_keys=True)

        generate_risk_signal_dicts(self.profile)

        after = json.dumps(self.profile, ensure_ascii=False, sort_keys=True)
        self.assertEqual(after, before)

    def test_invalid_thresholds_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RiskRuleConfig(low_liquidity_ratio=0)


if __name__ == "__main__":
    unittest.main()

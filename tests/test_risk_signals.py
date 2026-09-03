from __future__ import annotations

import copy
import json
import unittest

from contractor_agent.risk_signals import (
    RiskRuleConfig,
    generate_risk_signal_dicts,
    load_rule_config,
)


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
                        "profit": {"value": 100, "unit": "RUB"},
                        "liabilities": {
                            "capital": {"value": 500, "unit": "RUB"}
                        },
                    },
                    {
                        "year": 2025,
                        "profit": {"value": -200, "unit": "RUB"},
                        "liabilities": {
                            "capital": {"value": -50, "unit": "RUB"}
                        },
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
                "source_signals": [
                    {
                        "code": "MASS_AUTHPERSONS",
                        "domain": "OWNERSHIP",
                        "type": "NEGATIVE",
                        "origin": "SOURCE_SIGNAL",
                        "description": "Массовый руководитель",
                    },
                    {
                        "code": "FNS_BLOCKING",
                        "domain": "REGISTRY",
                        "type": "NEGATIVE",
                        "origin": "SOURCE_SIGNAL",
                        "description": "Есть блокировка ФНС",
                    },
                ],
            },
            "data_quality": {
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
                "warnings": [],
            },
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
            self.assertIn(signal["type"], {"POSITIVE", "NEGATIVE", "CONFLICT"})
            self.assertIn(signal["impact_level"], {"LOW", "MEDIUM", "HIGH"})
            self.assertEqual(signal["origin"], "DERIVED_RULE")
            self.assertTrue(signal["description"])
            self.assertTrue(signal["rule"])
            self.assertEqual(signal["rule_version"], "1.0.0")
            self.assertTrue(signal["evidence"])
            for evidence in signal["evidence"]:
                self.assertEqual(
                    set(evidence),
                    {"source_type", "source_path", "metric", "value"},
                )
                self.assertTrue(evidence["source_type"])
                self.assertTrue(evidence["source_path"])
                self.assertTrue(evidence["metric"])
        self.assertNotIn("risk_score", json.dumps(signals, ensure_ascii=False))
        by_code = {signal["code"]: signal for signal in signals}
        self.assertEqual(by_code["OWNERSHIP_DATA_CONFLICT"]["type"], "CONFLICT")
        self.assertTrue(
            all(
                signal["type"] == "NEGATIVE"
                for code, signal in by_code.items()
                if code != "OWNERSHIP_DATA_CONFLICT"
            )
        )

    def test_thresholds_are_configurable_and_boundaries_are_explicit(self) -> None:
        values = copy.deepcopy(load_rule_config().values)
        values["finance"]["low_liquidity"]["warning"] = 0.5
        values["finance"]["low_liquidity"]["critical"] = 0.25
        values["legal"]["high_defendant_amount"]["warning_rub"] = 3_000_000
        values["legal"]["high_defendant_amount"]["warning_to_revenue"] = 0.3
        values["legal"]["repeated_arbitration"]["warning_count"] = 5
        config = RiskRuleConfig(values)

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
        values = copy.deepcopy(load_rule_config().values)
        values["finance"]["low_liquidity"]["warning"] = 0
        with self.assertRaises(ValueError):
            RiskRuleConfig(values)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from contractor_agent.normalization import load_records, normalize_record
from contractor_agent.risk_signals import generate_risk_signal_dicts


DATASET_PATH = os.environ.get("CONTRACTORS_JSON")
REAL_COMPANY_INDEXES = (21, 16, 68, 55, 63)


@unittest.skipUnless(DATASET_PATH, "задайте CONTRACTORS_JSON для интеграционного теста")
class RealRiskSignalsTest(unittest.TestCase):
    def test_five_real_companies_produce_grounded_signal_lists(self) -> None:
        records = load_records(Path(DATASET_PATH))
        profiles = [normalize_record(records[index]) for index in REAL_COMPANY_INDEXES]
        signal_lists = [generate_risk_signal_dicts(profile) for profile in profiles]

        self.assertEqual(len(signal_lists), 5)
        self.assertEqual(
            {signal["code"] for signals in signal_lists for signal in signals},
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
                "TAX_REPUTATION_RISK",
            },
        )
        for signals in signal_lists:
            for signal in signals:
                self.assertEqual(
                    set(signal),
                    {
                        "code",
                        "domain",
                        "type",
                        "impact_level",
                        "origin",
                        "description",
                        "evidence",
                        "rule",
                        "rule_version",
                    },
                )
                self.assertEqual(signal["origin"], "DERIVED_RULE")
                self.assertTrue(signal["evidence"])
                self.assertTrue(
                    all(item["source_path"] for item in signal["evidence"])
                )
            json.dumps(signals, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()

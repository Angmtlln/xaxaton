from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from contractor_agent.context_builder import build_context
from contractor_agent.normalization import load_records, normalize_records


DATASET_PATH = os.environ.get("CONTRACTORS_JSON")


@unittest.skipUnless(DATASET_PATH, "задайте CONTRACTORS_JSON для интеграционного теста")
class RealJsonSampleTest(unittest.TestCase):
    def test_first_five_profiles_are_normalized_and_serializable(self) -> None:
        records = load_records(Path(DATASET_PATH))
        profiles = normalize_records(records[:5])

        self.assertEqual(len(profiles), 5)
        for profile in profiles:
            self.assertEqual(len(profile), 12)
            self.assertEqual(len(profile) - 2, 10)
            self.assertTrue(profile["derived_metrics"])
            self.assertTrue(
                all(metric["source_fields"] for metric in profile["derived_metrics"].values())
            )
            context = build_context(profile, "Есть ли судебные дела?")
            self.assertEqual(
                context["selected_domains"],
                ["COMPANY_IDENTITY", "BANK_RISK", "LEGAL_RISKS"],
            )
            self.assertTrue(context["facts"])
            self.assertNotIn(
                "impact_level", json.dumps(context, ensure_ascii=False)
            )
            json.dumps(profile, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()

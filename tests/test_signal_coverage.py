from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SignalCoverageTest(unittest.TestCase):
    def test_reports_source_and_derived_codes_separately(self) -> None:
        profiles = [
            {
                "financial_health": {"statements": []},
                "derived_metrics": {
                    "revenue_growth_yoy": {
                        "metric": "revenue_growth_yoy",
                        "value": -0.2,
                        "source_fields": ["report.finReports"],
                        "formula": "test",
                        "status": "CALCULATED",
                        "unit": "RATIO",
                    }
                },
                "compliance": {
                    "source_signals": [
                        {
                            "code": "ARBITRATION_DEFENDANT",
                            "type": "NEGATIVE",
                            "origin": "SOURCE_SIGNAL",
                        },
                        {
                            "code": "ARBITRATION_DEFENDANT",
                            "type": "NEGATIVE",
                            "origin": "SOURCE_SIGNAL",
                        },
                    ]
                },
                "data_quality": {"conflicts": [], "warnings": []},
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "profiles.json"
            output_path = Path(directory) / "coverage.json"
            input_path.write_text(json.dumps(profiles), encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "signal_coverage_report.py"),
                    str(input_path),
                    "--output",
                    str(output_path),
                ],
                check=True,
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            report = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(
            report["source_signals"],
            [{"signal_code": "ARBITRATION_DEFENDANT", "count": 1}],
        )
        self.assertEqual(
            report["derived_signals"],
            [{"signal_code": "REVENUE_DECLINE", "count": 1}],
        )


if __name__ == "__main__":
    unittest.main()

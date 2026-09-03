from __future__ import annotations

import json
import unittest

from contractor_agent.context_builder import build_context, select_relevant_domains


class ContextBuilderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = {
            "company_identity": {"inn": "123", "name": "ООО Тест"},
            "bank_risk": {
                "base_risk_level": "LOW",
                "zsk_risk_level": "GREEN",
            },
            "ownership": {"founders": []},
            "related_companies": {"companies": []},
            "business_profile": {"main_okved": None},
            "financial_health": {"statements": []},
            "legal_risks": {"yearly_cases": [], "by_status": {}},
            "enforcement": {"proceedings": []},
            "compliance": {
                "source_signals": [
                    {
                        "code": "FNS_BLOCKING",
                        "domain": "COMPLIANCE",
                        "type": "SOURCE_SIGNAL",
                        "value": "NEGATIVE",
                        "description": "Исходный сигнал ФНС",
                        "source": "report.reputationalRisks.negative[0]",
                        "evidence": [
                            {
                                "source_type": "SOURCE_SIGNAL",
                                "source_path": "report.reputationalRisks.negative[0]",
                                "metric": "reputational_signal_polarity",
                                "value": "NEGATIVE",
                            }
                        ],
                    }
                ],
                "inspections": [],
                "licenses": [],
            },
            "procurement": {"yearly_activity": []},
            "derived_metrics": {
                "revenue_growth_yoy": {
                    "metric": "revenue_growth_yoy",
                    "value": -0.2,
                    "source_fields": ["report.finReports[0].common.proceeds"],
                    "formula": "(latest - previous) / previous",
                    "status": "CALCULATED",
                    "unit": "RATIO",
                },
                "profit_margin": {
                    "metric": "profit_margin",
                    "value": None,
                    "source_fields": ["report.finReports[].common.profit"],
                    "formula": "profit / revenue",
                    "status": "NOT_AVAILABLE",
                    "unit": "RATIO",
                },
                "defendant_cases_count": {
                    "metric": "defendant_cases_count",
                    "value": 3,
                    "source_fields": ["report.arbitrationByStatus"],
                    "formula": "sum(status counts)",
                    "status": "CALCULATED",
                    "unit": "COUNT",
                },
            },
            "data_quality": {"conflicts": [], "warnings": []},
        }

    def test_selects_only_finance_plus_core_domains(self) -> None:
        context = build_context(self.profile, "Как менялись выручка и прибыль?")

        self.assertEqual(
            context["selected_domains"],
            ["COMPANY_IDENTITY", "BANK_RISK", "FINANCIAL_HEALTH"],
        )
        metric_codes = {
            fact["code"]
            for fact in context["facts"]
            if fact["type"] == "DERIVED_METRIC"
        }
        self.assertEqual(metric_codes, {"REVENUE_GROWTH_YOY", "PROFIT_MARGIN"})
        self.assertNotIn("DEFENDANT_CASES_COUNT", metric_codes)

    def test_preserves_missing_metric_in_context(self) -> None:
        context = build_context(self.profile, "Покажи финансовые показатели")
        profit_margin = next(
            fact for fact in context["facts"] if fact["code"] == "PROFIT_MARGIN"
        )

        self.assertEqual(profit_margin["value"]["status"], "NOT_AVAILABLE")
        self.assertIsNone(profit_margin["value"]["value"])

    def test_source_signal_is_included_as_fact_not_decision(self) -> None:
        context = build_context(self.profile, "Есть ли налоговые проблемы?")
        source_signal = next(
            fact for fact in context["facts"] if fact["code"] == "FNS_BLOCKING"
        )

        self.assertEqual(source_signal["type"], "SOURCE_SIGNAL")
        self.assertEqual(source_signal["value"], "NEGATIVE")
        raw_sources = {
            fact["source"]
            for fact in context["facts"]
            if fact["type"] == "RAW_FACT"
        }
        self.assertNotIn("normalized_profile.compliance.source_signals", raw_sources)

    def test_general_question_selects_all_ten_domains(self) -> None:
        self.assertEqual(len(select_relevant_domains("Расскажи о компании")), 10)

    def test_tax_system_question_does_not_pull_compliance(self) -> None:
        self.assertEqual(
            select_relevant_domains("Какой у компании налоговый режим?"),
            ["COMPANY_IDENTITY", "BANK_RISK", "BUSINESS_PROFILE"],
        )

    def test_fact_signal_contract_has_no_risk_classification(self) -> None:
        context = build_context(self.profile, "Расскажи о компании")
        for fact in context["facts"]:
            self.assertEqual(
                set(fact),
                {
                    "code",
                    "domain",
                    "type",
                    "value",
                    "description",
                    "source",
                    "evidence",
                },
            )
            self.assertIn(
                fact["type"], {"RAW_FACT", "DERIVED_METRIC", "SOURCE_SIGNAL"}
            )
        rendered = json.dumps(context, ensure_ascii=False)
        self.assertNotIn("impact_level", rendered)
        self.assertNotIn("severity", rendered)
        self.assertNotIn("risk_score", rendered)


if __name__ == "__main__":
    unittest.main()

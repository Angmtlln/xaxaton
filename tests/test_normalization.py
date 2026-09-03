from __future__ import annotations

import copy
import json
import unittest

from contractor_agent.normalization import normalize_record


class NormalizationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.record = {
            "_id": {"ogrn": "1234567890123"},
            "report": {
                "reportDate": {"$date": "2026-01-01T00:00:00.000Z"},
                "baseInfo": {
                    "inn": "1234567890",
                    "ogrn": "1234567890123",
                    "shortName": "ООО Тест",
                    "fullName": "Общество с ограниченной ответственностью Тест",
                    "address": "Тестовый адрес",
                    "email": "test@example.test",
                    "website": "https://example.test",
                    "companySize": "Микропредприятие",
                    "riskLevel": "MEDIUM",
                    "registrationInfo": {
                        "registrationDate": {"$date": "2020-01-01T00:00:00.000Z"},
                        "yearsFromRegistration": 6,
                    },
                },
                "zskRiskLevel": "GREEN",
                "status": {
                    "status": "CURRENT",
                    "date": {"$date": "2020-01-01T00:00:00.000Z"},
                },
                "foundersInfo": {
                    "shareCapital": {"$numberLong": "10000"},
                    "authPerson": {
                        "inn": "111111111111",
                        "name": "Иванов Иван Иванович",
                        "positionName": "Директор",
                        "positionDate": {"$date": "2022-01-01T00:00:00.000Z"},
                    },
                    "cofounders": [
                        {
                            "inn": "111111111111",
                            "name": "Иванов Иван Иванович",
                            "share": "75",
                            "amount": {"$numberLong": "7500"},
                            "active": True,
                            "dateFrom": {"$date": "2020-01-01T00:00:00.000Z"},
                        },
                        {
                            "inn": "222222222222",
                            "name": "Петров Пётр Петрович",
                            "share": 25,
                            "amount": 2500,
                            "active": True,
                            "dateFrom": {"$date": "2020-01-01T00:00:00.000Z"},
                        },
                    ],
                },
                "relatedCompanies": [
                    {
                        "inn": "9999999999",
                        "ogrn": "9999999999999",
                        "name": "Связанная компания",
                        "authPersonName": "Иванов Иван Иванович",
                        "authPersonPosition": "Директор",
                    }
                ],
                "kindsOfActivityInfo": {
                    "mainKindOfActivity": {"code": "62.01", "description": "ПО"},
                    "otherKindsOfActivity": [
                        {"code": "62.02", "description": "Консалтинг"}
                    ],
                },
                "branchesInfo": {
                    "branchesCount": 1,
                    "branches": [{"name": "Филиал", "address": "Адрес"}],
                },
                "taxSystem": [{"shortName": "УСН", "fullName": "Упрощённая"}],
                "finReports": [
                    {
                        "common": {"year": 2024, "proceeds": {"$numberLong": "100"}, "profit": 10},
                        "assets": {
                            "totalAssets": 200,
                            "currentAssets": {"total": 100, "receivables": 50, "bankroll": 10},
                        },
                        "liabilities": {
                            "totalLiabilities": 200,
                            "capitals": 50,
                            "shortTermLiabilities": {"total": 50, "accountsPayable": 20},
                        },
                    },
                    {
                        "common": {"year": 2025, "proceeds": "200", "profit": "20"},
                        "assets": {
                            "totalAssets": 400,
                            "currentAssets": {"total": 200, "receivables": 100, "bankroll": 20},
                        },
                        "liabilities": {
                            "totalLiabilities": 400,
                            "capitals": 100,
                            "shortTermLiabilities": {"total": 100, "accountsPayable": 30},
                        },
                    },
                ],
                "coefficient": {
                    "year": 2025,
                    "sustainability": "0,75",
                    "solvency": "1.50",
                    "profitability": "0.10",
                },
                "arbitrationCases": [
                    {
                        "year": 2025,
                        "defendantCount": 1,
                        "defendantAmount": 100000,
                        "plaintiffCount": 0,
                        "plaintiffAmount": 0,
                    }
                ],
                "arbitrationByStatus": {
                    "commonCount": 1,
                    "commonAmount": 100000,
                    "defandantArbitration": {
                        "defandantArbitrationFinished": {},
                        "defandantArbitrationAppealed": {},
                        "defandantArbitrationPending": {
                            "dpCount": 1,
                            "dpAmount": 100000,
                        },
                    },
                    "plaintiffArbitration": {
                        "plaintiffArbitrationFinished": {},
                        "plaintiffArbitrationAppealed": {},
                        "plaintiffArbitrationPending": {},
                    },
                },
                "executionProceedings": [
                    {
                        "number": "TEST-1",
                        "date": {"$date": "2025-12-01T00:00:00.000Z"},
                        "active": True,
                        "amount": "1 500,50",
                    }
                ],
                "reputationalRisks": {
                    "positive": [
                        {
                            "code": "аrbitrationDefendant",
                            "chapter": "arbitr",
                            "name": "Дела ответчиком не найдены",
                        },
                        {
                            "code": "webSite",
                            "chapter": "site",
                            "name": "Есть сайт",
                        },
                    ],
                    "negative": [],
                },
                "inspections": [],
                "licenses": [],
                "procurements": [
                    {
                        "procurementsYear": 2025,
                        "federalLawCode": "44-ФЗ",
                        "tenderWinnerCnt": 2,
                        "contractSignedCnt": 1,
                        "contractSignedAmt": "1000",
                    }
                ],
            },
        }

    def test_normalizes_ten_business_blocks(self) -> None:
        profile = normalize_record(self.record).to_dict()

        self.assertEqual(
            list(profile),
            [
                "company_identity",
                "bank_risk",
                "ownership",
                "related_companies",
                "business_profile",
                "financial_health",
                "legal_risks",
                "enforcement",
                "compliance",
                "procurement",
                "derived_metrics",
                "data_quality",
            ],
        )
        self.assertEqual(profile["bank_risk"]["base_risk_level"], "MEDIUM")
        self.assertEqual(profile["bank_risk"]["zsk_risk_level"], "GREEN")
        self.assertNotIn("risk_score", json.dumps(profile, ensure_ascii=False))
        self.assertEqual(
            set(profile["derived_metrics"]),
            {
                "company_age_years",
                "is_active_company",
                "founder_count",
                "max_owner_share",
                "director_tenure_years",
                "is_director_also_founder",
                "related_company_count",
                "unique_related_director_count",
                "okved_count",
                "branches_count",
                "revenue_growth_yoy",
                "profit_margin",
                "revenue_trend",
                "profit_trend",
                "current_assets_to_short_term_liabilities",
                "cash_to_short_term_liabilities",
                "receivables_share",
                "liabilities_to_assets",
                "total_arbitration_cases",
                "defendant_cases_count",
                "defendant_pending_cases_count",
                "defendant_pending_amount",
                "defendant_cases_share",
                "arbitration_amount_to_revenue",
                "total_execution_count",
                "active_execution_count",
                "active_execution_amount",
                "latest_execution_date",
                "tender_count",
                "winner_count",
                "signed_contract_amount",
            },
        )

    def test_parses_types_and_calculates_transparent_metrics(self) -> None:
        profile = normalize_record(self.record).to_dict()
        metrics = profile["derived_metrics"]

        self.assertEqual(
            profile["ownership"]["share_capital"],
            {"value": 10000, "unit": "RUB"},
        )
        self.assertEqual(
            profile["enforcement"]["proceedings"][0]["amount_rub"],
            {"value": 1500.5, "unit": "RUB"},
        )
        self.assertEqual(
            profile["financial_health"]["statements"][0]["revenue"],
            {"value": 100, "unit": "RUB"},
        )
        self.assertEqual(
            profile["legal_risks"]["by_status"]["common_amount_rub"],
            {"value": 100000, "unit": "RUB"},
        )
        self.assertEqual(
            profile["procurement"]["yearly_activity"][0][
                "signed_contract_amount_rub"
            ],
            {"value": 1000, "unit": "RUB"},
        )
        self.assertEqual(profile["financial_health"]["coefficients"]["sustainability"], 0.75)
        self.assertEqual(metrics["revenue_growth_yoy"]["value"], 1.0)
        self.assertEqual(metrics["profit_margin"]["value"], 0.1)
        self.assertEqual(metrics["current_assets_to_short_term_liabilities"]["value"], 2.0)
        self.assertEqual(metrics["cash_to_short_term_liabilities"]["value"], 0.2)
        self.assertEqual(metrics["receivables_share"]["value"], 0.25)
        self.assertEqual(metrics["liabilities_to_assets"]["value"], 0.75)
        self.assertEqual(metrics["revenue_trend"]["value"], "GROWING")
        self.assertTrue(metrics["is_director_also_founder"]["value"])
        self.assertEqual(metrics["active_execution_amount"]["value"], 1500.5)
        self.assertEqual(metrics["arbitration_amount_to_revenue"]["value"], 500.0)
        self.assertEqual(metrics["tender_count"]["status"], "NOT_AVAILABLE")

        for name, metric in metrics.items():
            self.assertEqual(name, metric["metric"])
            self.assertTrue(metric["source_fields"])

    def test_detects_reputational_source_conflict(self) -> None:
        profile = normalize_record(self.record).to_dict()
        signal = profile["compliance"]["source_signals"][0]

        self.assertEqual(signal["code"], "ARBITRATION_DEFENDANT")
        self.assertEqual(signal["domain"], "LEGAL")
        self.assertEqual(signal["type"], "POSITIVE")
        self.assertEqual(signal["origin"], "SOURCE_SIGNAL")
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
        self.assertTrue(
            any(
                conflict["type"] == "SOURCE_CONFLICT"
                and conflict["code"] == "ARBITRATION_DEFENDANT"
                for conflict in profile["data_quality"]["conflicts"]
            )
        )

    def test_missing_optional_blocks_are_safe(self) -> None:
        record = {
            "report": {
                "baseInfo": {"riskLevel": "UNKNOWN"},
                "zskRiskLevel": "RED",
                "status": {},
                "reputationalRisks": {"positive": [], "negative": []},
            }
        }
        profile = normalize_record(record).to_dict()

        self.assertEqual(profile["related_companies"]["companies"], [])
        self.assertEqual(profile["financial_health"]["statements"], [])
        self.assertEqual(profile["derived_metrics"]["related_company_count"]["value"], 0)
        self.assertEqual(profile["derived_metrics"]["revenue_growth_yoy"]["status"], "NOT_AVAILABLE")
        self.assertEqual(profile["derived_metrics"]["is_active_company"]["status"], "NOT_AVAILABLE")
        json.dumps(profile, ensure_ascii=False)

    def test_arbitration_sources_are_not_summed(self) -> None:
        record = copy.deepcopy(self.record)
        record["report"]["arbitrationCases"][0]["defendantCount"] = 50

        profile = normalize_record(record).to_dict()
        metrics = profile["derived_metrics"]

        self.assertEqual(metrics["total_arbitration_cases"]["value"], 1)
        self.assertEqual(metrics["defendant_cases_count"]["value"], 1)
        self.assertTrue(
            any(
                warning["type"] == "ARBITRATION_SCOPE_DIFFERENCE"
                for warning in profile["data_quality"]["warnings"]
            )
        )

    def test_parses_nested_mongo_date_number(self) -> None:
        record = copy.deepcopy(self.record)
        record["report"]["status"]["date"] = {
            "$date": {"$numberLong": "1767225600000"}
        }

        profile = normalize_record(record).to_dict()

        self.assertEqual(
            profile["company_identity"]["status_date"],
            "2026-01-01T00:00:00.000Z",
        )


if __name__ == "__main__":
    unittest.main()

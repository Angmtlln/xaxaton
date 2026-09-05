#!/usr/bin/env python3
"""Загрузка выгрузки contractors_audit.snapshot.json в PostgreSQL.

    python scripts/load_snapshot.py --file ../contractors_audit.snapshot.json --create-schema

Скрипт идемпотентный: повторный запуск обновляет уже загруженные
снапшоты (ключ — ОГРН + дата отчёта), а не плодит дубли.
"""
import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.facts import build_coverage           # noqa: E402
from app.infrastructure.mongo import as_float, as_int, dig, parse_date, parse_datetime  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("loader")

RISK_LEVELS = {"LOW", "MEDIUM", "HIGH", "UNKNOWN"}
ZSK_LEVELS = {"GREEN", "YELLOW", "RED", "UNKNOWN"}


def arr(value: Any) -> List[Any]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def load_document(cur, doc: Dict[str, Any], source_file: str) -> Optional[int]:
    report = doc.get("report") or {}
    base = report.get("baseInfo") or {}
    inn = base.get("inn")
    if not inn:
        log.warning("Пропуск записи без ИНН")
        return None

    source_ogrn = dig(doc, "_id", "ogrn") or base.get("ogrn")
    source_date = parse_datetime(dig(doc, "_id", "date")) or parse_datetime(report.get("reportDate"))
    payload = json.dumps(doc, ensure_ascii=False)

    cur.execute(
        """INSERT INTO raw.report_documents
               (source_ogrn, source_date, inn, document, document_bytes, source_file)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (source_ogrn, source_date) DO UPDATE
               SET document = EXCLUDED.document,
                   document_bytes = EXCLUDED.document_bytes,
                   loaded_at = now()
           RETURNING id""",
        (source_ogrn, source_date, inn, Jsonb(doc), len(payload.encode("utf-8")), source_file))
    raw_id = cur.fetchone()["id"]

    cur.execute(
        """INSERT INTO core.companies (inn, ogrn, kpp, okpo, short_name, full_name)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (inn) DO UPDATE
               SET ogrn = EXCLUDED.ogrn, kpp = EXCLUDED.kpp, okpo = EXCLUDED.okpo,
                   short_name = EXCLUDED.short_name, full_name = EXCLUDED.full_name,
                   updated_at = now()
           RETURNING id""",
        (inn, base.get("ogrn"), base.get("kpp"), base.get("okpo"),
         base.get("shortName"), base.get("fullName")))
    company_id = cur.fetchone()["id"]

    risk = (base.get("riskLevel") or "UNKNOWN").upper()
    zsk = (report.get("zskRiskLevel") or "UNKNOWN").upper()
    branches = report.get("branchesInfo") or {}

    cur.execute(
        """INSERT INTO core.report_snapshots
               (company_id, raw_document_id, report_date, address, email, website, company_size,
                registration_date, years_from_registration, status, status_reason, status_date,
                risk_level, zsk_risk_level, share_capital, branches_count)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                   %s::core.risk_level, %s::core.zsk_level, %s, %s)
           ON CONFLICT (company_id, report_date) DO UPDATE
               SET raw_document_id = EXCLUDED.raw_document_id,
                   address = EXCLUDED.address, email = EXCLUDED.email,
                   website = EXCLUDED.website, company_size = EXCLUDED.company_size,
                   registration_date = EXCLUDED.registration_date,
                   years_from_registration = EXCLUDED.years_from_registration,
                   status = EXCLUDED.status, status_reason = EXCLUDED.status_reason,
                   status_date = EXCLUDED.status_date, risk_level = EXCLUDED.risk_level,
                   zsk_risk_level = EXCLUDED.zsk_risk_level,
                   share_capital = EXCLUDED.share_capital,
                   branches_count = EXCLUDED.branches_count, loaded_at = now()
           RETURNING id""",
        (company_id, raw_id, parse_datetime(report.get("reportDate")) or source_date,
         base.get("address"), base.get("email"), base.get("website"), base.get("companySize"),
         parse_date(dig(base, "registrationInfo", "registrationDate")),
         as_int(dig(base, "registrationInfo", "yearsFromRegistration")),
         dig(report, "status", "status"), dig(report, "status", "reasonName"),
         parse_date(dig(report, "status", "date")),
         risk if risk in RISK_LEVELS else "UNKNOWN",
         zsk if zsk in ZSK_LEVELS else "UNKNOWN",
         as_float(dig(report, "foundersInfo", "shareCapital")),
         as_int(branches.get("branchesCount")) or len(arr(branches.get("branches")))))
    snapshot_id = cur.fetchone()["id"]

    _reload_children(cur, snapshot_id, report)
    _save_coverage(cur, snapshot_id, doc)
    return snapshot_id


CHILD_TABLES = [
    "core.phones", "core.activity_codes", "core.tax_systems", "core.branches",
    "core.founders", "core.related_companies", "core.reputational_risks",
    "core.arbitration_cases", "core.arbitration_summary", "core.execution_proceedings",
    "core.inspections", "core.fin_reports", "core.fin_coefficients",
    "core.licenses", "core.procurements",
]


def _reload_children(cur, snapshot_id: int, report: Dict[str, Any]) -> None:
    for table in CHILD_TABLES:
        cur.execute("DELETE FROM %s WHERE snapshot_id = %%s" % table, (snapshot_id,))

    # --- Блок 1 ---
    for idx, phone in enumerate(arr(report.get("phones"))):
        cur.execute(
            "INSERT INTO core.phones (snapshot_id, idx, phone_code, phone_number) VALUES (%s,%s,%s,%s)",
            (snapshot_id, idx, phone.get("phoneCode"), phone.get("phoneNumber")))

    kinds = report.get("kindsOfActivityInfo") or {}
    main = kinds.get("mainKindOfActivity") or {}
    if main.get("code"):
        cur.execute(
            """INSERT INTO core.activity_codes (snapshot_id, code, description, is_main, idx)
               VALUES (%s,%s,%s,true,0)""",
            (snapshot_id, main.get("code"), main.get("description")))
    for idx, item in enumerate(arr(kinds.get("otherKindsOfActivity")), start=1):
        cur.execute(
            """INSERT INTO core.activity_codes (snapshot_id, code, description, is_main, idx)
               VALUES (%s,%s,%s,false,%s)""",
            (snapshot_id, item.get("code"), item.get("description"), idx))

    for item in arr(report.get("taxSystem")):
        cur.execute(
            "INSERT INTO core.tax_systems (snapshot_id, short_name, full_name) VALUES (%s,%s,%s)",
            (snapshot_id, item.get("shortName"), item.get("fullName")))

    for item in arr(dig(report, "branchesInfo", "branches")):
        cur.execute("INSERT INTO core.branches (snapshot_id, name, address) VALUES (%s,%s,%s)",
                    (snapshot_id, item.get("name"), item.get("address")))

    founders = report.get("foundersInfo") or {}
    auth = founders.get("authPerson") or {}
    if auth.get("name"):
        cur.execute(
            """INSERT INTO core.founders (snapshot_id, role, name, inn, position_name, position_date)
               VALUES (%s,'AUTH_PERSON',%s,%s,%s,%s)""",
            (snapshot_id, auth.get("name"), auth.get("inn"), auth.get("positionName"),
             parse_date(auth.get("positionDate"))))
    for idx, item in enumerate(arr(founders.get("cofounders"))):
        cur.execute(
            """INSERT INTO core.founders
                   (snapshot_id, role, name, inn, amount, share, date_from, active, idx)
               VALUES (%s,'COFOUNDER',%s,%s,%s,%s,%s,%s,%s)""",
            (snapshot_id, item.get("name"), item.get("inn"), as_float(item.get("amount")),
             as_float(item.get("share")), parse_date(item.get("dateFrom")),
             item.get("active"), idx))

    for item in arr(report.get("relatedCompanies")):
        cur.execute(
            """INSERT INTO core.related_companies
                   (snapshot_id, inn, ogrn, name, registration_date, auth_person_name,
                    auth_person_position)
               VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (snapshot_id, item.get("inn"), item.get("ogrn"), item.get("name"),
             parse_date(item.get("registrationDate")), item.get("authPersonName"),
             item.get("authPersonPosition")))
        related_id = cur.fetchone()["id"]
        for parent in arr(item.get("parentOrganizations")):
            cur.execute(
                """INSERT INTO core.parent_organizations
                       (related_company_id, inn, ogrn, full_name, parent_date)
                   VALUES (%s,%s,%s,%s,%s)""",
                (related_id, parent.get("inn"), parent.get("ogrn"), parent.get("fullName"),
                 parse_date(parent.get("parentDate"))))

    # --- Блок 2 ---
    risks = report.get("reputationalRisks") or {}
    for polarity, key in (("NEGATIVE", "negative"), ("POSITIVE", "positive")):
        for item in arr(risks.get(key)):
            if not item.get("code"):
                continue
            cur.execute(
                """INSERT INTO core.reputational_risks (snapshot_id, polarity, chapter, code, name)
                   VALUES (%s,%s::core.rep_polarity,%s,%s,%s)""",
                (snapshot_id, polarity, item.get("chapter") or "other", item.get("code"),
                 item.get("name")))

    seen_years = set()
    for item in arr(report.get("arbitrationCases")):
        year = as_int(item.get("year"))
        if year is None or year in seen_years:
            continue
        seen_years.add(year)
        cur.execute(
            """INSERT INTO core.arbitration_cases
                   (snapshot_id, year, plaintiff_count, plaintiff_amount,
                    defendant_count, defendant_amount)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (snapshot_id, year, as_int(item.get("plaintiffCount")) or 0,
             as_float(item.get("plaintiffAmount")) or 0,
             as_int(item.get("defendantCount")) or 0,
             as_float(item.get("defendantAmount")) or 0))

    summary = report.get("arbitrationByStatus") or {}
    if summary:
        def pair(group: str, node: str, prefix: str):
            data = dig(summary, group, node) or {}
            return (as_int(data.get(prefix + "Count")) or 0,
                    as_float(data.get(prefix + "Amount")) or 0)
        pf, pa, pp = (pair("plaintiffArbitration", "plaintiffArbitrationFinished", "pf"),
                      pair("plaintiffArbitration", "plaintiffArbitrationAppealed", "pa"),
                      pair("plaintiffArbitration", "plaintiffArbitrationPending", "pp"))
        df, da, dp = (pair("defandantArbitration", "defandantArbitrationFinished", "df"),
                      pair("defandantArbitration", "defandantArbitrationAppealed", "da"),
                      pair("defandantArbitration", "defandantArbitrationPending", "dp"))
        cur.execute(
            """INSERT INTO core.arbitration_summary
                   (snapshot_id, common_count, common_amount,
                    pf_count, pf_amount, pa_count, pa_amount, pp_count, pp_amount,
                    df_count, df_amount, da_count, da_amount, dp_count, dp_amount)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (snapshot_id, as_int(summary.get("commonCount")) or 0,
             as_float(summary.get("commonAmount")) or 0,
             pf[0], pf[1], pa[0], pa[1], pp[0], pp[1],
             df[0], df[1], da[0], da[1], dp[0], dp[1]))

    for idx, item in enumerate(arr(report.get("executionProceedings"))):
        amount = as_float(item.get("amount"))
        cur.execute(
            """INSERT INTO core.execution_proceedings
                   (snapshot_id, number, proceeding_date, amount, amount_known, active, idx)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (snapshot_id, item.get("number"), parse_date(item.get("date")), amount,
             amount is not None, bool(item.get("active")), idx))

    for item in arr(report.get("inspections")):
        cur.execute(
            """INSERT INTO core.inspections
                   (snapshot_id, erp_id, type, form, authority_name, start_date, end_date,
                    inspection_status)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (snapshot_id, item.get("erpId"), item.get("type"), item.get("form"),
             item.get("authorityName"), parse_date(item.get("startDate")),
             parse_date(item.get("endDate")), item.get("inspectionStatus")))

    # --- Блок 3 ---
    seen_fin_years = set()
    for item in arr(report.get("finReports")):
        common = item.get("common") or {}
        assets = item.get("assets") or {}
        liab = item.get("liabilities") or {}
        year = as_int(common.get("year"))
        if year is None or year in seen_fin_years:
            continue
        seen_fin_years.add(year)
        cur.execute(
            """INSERT INTO core.fin_reports
                   (snapshot_id, year, proceeds, profit, total_assets, current_assets_total,
                    current_stocks, current_receivables, current_bankroll,
                    uncurrent_assets_total, uncurrent_fixed_assets, total_liabilities, capitals,
                    long_term_total, long_term_others, short_term_total, short_term_borrowed,
                    short_term_payables)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (snapshot_id, year, as_float(common.get("proceeds")), as_float(common.get("profit")),
             as_float(assets.get("totalAssets")),
             as_float(dig(assets, "currentAssets", "total")),
             as_float(dig(assets, "currentAssets", "stocks")),
             as_float(dig(assets, "currentAssets", "receivables")),
             as_float(dig(assets, "currentAssets", "bankroll")),
             as_float(dig(assets, "uncurrentAssets", "total")),
             as_float(dig(assets, "uncurrentAssets", "fixedAssets")),
             as_float(liab.get("totalLiabilities")), as_float(liab.get("capitals")),
             as_float(dig(liab, "longTermDuties", "total")),
             as_float(dig(liab, "longTermDuties", "others")),
             as_float(dig(liab, "shortTermLiabilities", "total")),
             as_float(dig(liab, "shortTermLiabilities", "borrowedFunds")),
             as_float(dig(liab, "shortTermLiabilities", "accountsPayable"))))

    coef = report.get("coefficient") or {}
    if coef and as_int(coef.get("year")) is not None:
        cur.execute(
            """INSERT INTO core.fin_coefficients
                   (snapshot_id, year, sustainability, solvency, profitability)
               VALUES (%s,%s,%s,%s,%s)""",
            (snapshot_id, as_int(coef.get("year")), as_float(coef.get("sustainability")),
             as_float(coef.get("solvency")), as_float(coef.get("profitability"))))

    # --- Блок 4 ---
    for item in arr(report.get("licenses")):
        cur.execute(
            """INSERT INTO core.licenses
                   (snapshot_id, number, name, issuing_authority, issue_date, end_date, status)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (snapshot_id, item.get("number"), item.get("name"), item.get("issuingAuthority"),
             parse_date(item.get("issueDate")), parse_date(item.get("endDate")),
             item.get("status")))

    for item in arr(report.get("procurements")):
        cur.execute(
            """INSERT INTO core.procurements
                   (snapshot_id, procurements_year, federal_law_code, tender_winner_cnt,
                    contract_signed_cnt, contract_signed_amt)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (snapshot_id, as_int(item.get("procurementsYear")), item.get("federalLawCode"),
             as_int(item.get("tenderWinnerCnt")) or 0, as_int(item.get("contractSignedCnt")) or 0,
             as_float(item.get("contractSignedAmt")) or 0))


def _save_coverage(cur, snapshot_id: int, doc: Dict[str, Any]) -> None:
    coverage = build_coverage(doc)
    flags = {b["key"]: b["filled"] for b in coverage["blocks"]}
    cur.execute(
        """INSERT INTO core.snapshot_coverage
               (snapshot_id, has_founders, has_related, has_arbitration, has_execproc,
                has_inspections, has_fin_reports, has_coefficients, has_licenses,
                has_procurements, filled_blocks)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (snapshot_id) DO UPDATE SET
               has_founders = EXCLUDED.has_founders, has_related = EXCLUDED.has_related,
               has_arbitration = EXCLUDED.has_arbitration, has_execproc = EXCLUDED.has_execproc,
               has_inspections = EXCLUDED.has_inspections,
               has_fin_reports = EXCLUDED.has_fin_reports,
               has_coefficients = EXCLUDED.has_coefficients,
               has_licenses = EXCLUDED.has_licenses,
               has_procurements = EXCLUDED.has_procurements,
               filled_blocks = EXCLUDED.filled_blocks, computed_at = now()""",
        (snapshot_id, flags["founders"], flags["related"], flags["arbitration"],
         flags["execproc"], flags["inspections"], flags["fin_reports"], flags["coefficients"],
         flags["licenses"], flags["procurements"], coverage["filled_blocks"]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Загрузка снапшота отчётов в PostgreSQL")
    parser.add_argument("--file", default="../contractors_audit.snapshot.json",
                        help="путь к JSON-выгрузке")
    parser.add_argument("--dsn", default=os.getenv("DATABASE_URL",
                        "postgresql://postgres:postgres@localhost:5432/contractors"))
    parser.add_argument("--create-schema", action="store_true",
                        help="накатить db/schema.sql и db/seed_dictionary.sql перед загрузкой")
    parser.add_argument("--limit", type=int, default=None, help="загрузить только первые N записей")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        log.error("Файл не найден: %s", path)
        return 1

    with psycopg.connect(args.dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            if args.create_schema:
                root = Path(__file__).resolve().parents[1]
                for sql_file in ("db/schema.sql", "db/seed_dictionary.sql"):
                    log.info("Накатываю %s", sql_file)
                    cur.execute((root / sql_file).read_text(encoding="utf-8"))
                conn.commit()

            documents = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(documents, dict):
                documents = [documents]
            if args.limit:
                documents = documents[: args.limit]

            loaded = 0
            for doc in documents:
                if load_document(cur, doc, path.name) is not None:
                    loaded += 1
                if loaded % 25 == 0 and loaded:
                    conn.commit()
                    log.info("Загружено %s карточек", loaded)
            conn.commit()

            cur.execute("SELECT count(*) AS n FROM core.report_snapshots")
            total = cur.fetchone()["n"]
    log.info("Готово. Загружено %s карточек, всего в базе %s снапшотов", loaded, total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

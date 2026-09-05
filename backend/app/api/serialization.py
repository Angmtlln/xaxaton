"""Приведение строк БД к JSON-виду ответа API."""
from typing import Any, Dict, List, Optional


def iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def isoformat_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: (iso(v) if hasattr(v, "isoformat") else v) for k, v in row.items()}


def isoformat_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [isoformat_row(row) for row in rows]


def company_out(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "inn": snapshot["inn"],
        "ogrn": snapshot.get("ogrn"),
        "short_name": snapshot.get("short_name"),
        "full_name": snapshot.get("full_name"),
        "address": snapshot.get("address"),
        "status": snapshot.get("status"),
        "registration_date": iso(snapshot.get("registration_date")),
        "years_from_registration": snapshot.get("years_from_registration"),
        "risk_level": snapshot.get("risk_level"),
        "zsk_risk_level": snapshot.get("zsk_risk_level"),
        "report_date": iso(snapshot.get("report_date")),
    }

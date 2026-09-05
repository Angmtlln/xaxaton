"""Нормализация Mongo Extended JSON.

В снапшоте одно и то же поле приходит и обычным числом, и обёрткой
``{"$numberLong": "..."}``, даты — ``{"$date": "..."}``. До подачи в
модель и до записи в БД это надо развернуть, иначе суммы читаются как
строки, а сравнение по годам ломается (см. blocks_summary_design.md §7).
"""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional


def unwrap(value: Any) -> Any:
    """Разворачивает обёртки Mongo в питоновские значения (рекурсивно)."""
    if isinstance(value, dict):
        if set(value.keys()) == {"$numberLong"}:
            return _to_int(value["$numberLong"])
        if set(value.keys()) == {"$numberInt"}:
            return _to_int(value["$numberInt"])
        if set(value.keys()) == {"$numberDouble"}:
            return _to_decimal(value["$numberDouble"])
        if set(value.keys()) == {"$date"}:
            return parse_datetime(value["$date"])
        if set(value.keys()) == {"$oid"}:
            return value["$oid"]
        return {k: unwrap(v) for k, v in value.items()}
    if isinstance(value, list):
        return [unwrap(v) for v in value]
    return value


def _to_int(raw: Any) -> Optional[int]:
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return None


def _to_decimal(raw: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(raw))
    except (TypeError, InvalidOperation):
        return None


def parse_datetime(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, dict):
        raw = raw.get("$date")
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw / 1000, tz=timezone.utc)
    if not isinstance(raw, str):
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_date(raw: Any) -> Optional[date]:
    """Даты приходят и как ``{"$date": ...}``, и как строка ``2024-02-07``."""
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    dt = parse_datetime(raw)
    if dt is not None:
        return dt.date()
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw.strip()[:10])
        except ValueError:
            return None
    return None


def num(raw: Any) -> Optional[Decimal]:
    """Число из любого представления: int, str, {$numberLong}, None."""
    value = unwrap(raw)
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def as_float(raw: Any) -> Optional[float]:
    value = num(raw)
    return float(value) if value is not None else None


def as_int(raw: Any) -> Optional[int]:
    value = num(raw)
    return int(value) if value is not None else None


def dig(obj: Any, *path: str) -> Any:
    """Безопасный обход вложенных словарей."""
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur

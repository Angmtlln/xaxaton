from datetime import date

from app.mongo import as_float, as_int, dig, parse_date, unwrap


def test_unwrap_number_long():
    assert unwrap({"$numberLong": "748359000"}) == 748359000


def test_number_from_string_amount():
    # executionProceedings[].amount приходит строкой
    assert as_float("517235.54") == 517235.54
    assert as_float(None) is None


def test_int_from_wrapper_and_plain():
    assert as_int({"$numberLong": "12"}) == 12
    assert as_int(12) == 12


def test_dates_in_both_formats():
    assert parse_date({"$date": "2024-01-14T21:00:00.000Z"}) == date(2024, 1, 14)
    assert parse_date("2023-11-17") == date(2023, 11, 17)   # inspections.startDate
    assert parse_date(None) is None


def test_dig_is_safe():
    assert dig({"a": {"b": 1}}, "a", "b") == 1
    assert dig({"a": None}, "a", "b") is None

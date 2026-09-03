"""Детерминированный слой данных для проверки контрагентов."""

from .models import NormalizedCompanyProfile
from .normalization import normalize_record, normalize_records

__all__ = ["NormalizedCompanyProfile", "normalize_record", "normalize_records"]

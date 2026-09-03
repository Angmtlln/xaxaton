"""Нормализация данных и сборка фактического контекста контрагента."""

from .context_builder import build_context, select_relevant_domains
from .models import FactSignal, NormalizedCompanyProfile
from .normalization import normalize_record, normalize_records

__all__ = [
    "FactSignal",
    "NormalizedCompanyProfile",
    "build_context",
    "normalize_record",
    "normalize_records",
    "select_relevant_domains",
]

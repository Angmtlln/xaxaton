#!/usr/bin/env python3
"""Строит deterministic risk signals из нормализованных профилей."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from contractor_agent.risk_signals import generate_risk_signal_dicts  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Генерация risk signals из normalized business profiles"
    )
    parser.add_argument("input", type=Path, help="JSON-массив нормализованных профилей")
    parser.add_argument("--limit", type=int, default=None, help="Обработать первые N профилей")
    parser.add_argument("--output", type=Path, help="Записать результат в JSON-файл")
    return parser.parse_args()


def load_profiles(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("Ожидается JSON-массив нормализованных профилей")
    return value


def main() -> None:
    args = parse_args()
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit должен быть положительным числом")

    profiles = load_profiles(args.input)
    if args.limit is not None:
        profiles = profiles[: args.limit]
    payload = [
        {
            "company_identity": profile.get("company_identity", {}),
            "bank_risk": profile.get("bank_risk", {}),
            "risk_signals": generate_risk_signal_dicts(profile),
        }
        for profile in profiles
    ]
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Сохранено профилей с сигналами: {len(payload)}; файл: {args.output}")
    else:
        print(rendered)


if __name__ == "__main__":
    main()

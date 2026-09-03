#!/usr/bin/env python3
"""Печатает или сохраняет нормализованные профили из исходного JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from contractor_agent.normalization import load_records, normalize_records  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Нормализация GetFullReportResponse в бизнес-профили"
    )
    parser.add_argument("input", type=Path, help="Исходный JSON-массив карточек")
    parser.add_argument(
        "--limit", type=int, default=None, help="Нормализовать только первые N карточек"
    )
    parser.add_argument("--output", type=Path, help="Записать результат в JSON-файл")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit должен быть положительным числом")

    records = load_records(args.input)
    if args.limit is not None:
        records = records[: args.limit]
    payload = normalize_records(records)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Сохранено профилей: {len(payload)}; файл: {args.output}")
    else:
        print(rendered)


if __name__ == "__main__":
    main()

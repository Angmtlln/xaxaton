#!/usr/bin/env python3
"""Собирает вопрос-ориентированный контекст из normalized profile."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from contractor_agent.context_builder import build_context  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Context Builder для normalized contractor profiles"
    )
    parser.add_argument("input", type=Path, help="JSON-массив normalized profiles")
    parser.add_argument("--question", required=True, help="Вопрос пользователя")
    parser.add_argument("--company-index", type=int, default=0)
    parser.add_argument("--output", type=Path, help="Записать контекст в JSON")
    return parser.parse_args()


def load_profiles(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("Ожидается JSON-массив normalized profiles")
    return value


def main() -> None:
    args = parse_args()
    profiles = load_profiles(args.input)
    if args.company_index < 0 or args.company_index >= len(profiles):
        raise SystemExit("--company-index выходит за границы массива")
    context = build_context(profiles[args.company_index], args.question)
    rendered = json.dumps(context, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Контекст сохранён: {args.output}")
    else:
        print(rendered)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Строит раздельный coverage report source и derived risk signals."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from contractor_agent.risk_signals import (  # noqa: E402
    generate_risk_signal_dicts,
    load_rule_config,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Coverage report для source и derived risk signals"
    )
    parser.add_argument("input", type=Path, help="JSON-массив normalized profiles")
    parser.add_argument("--config", type=Path, help="JSON-конфигурация risk rules")
    parser.add_argument("--output", type=Path, help="Записать отчёт в JSON-файл")
    return parser.parse_args()


def load_profiles(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("Ожидается JSON-массив normalized profiles")
    return value


def count_codes(signals: list[dict[str, Any]]) -> Counter[str]:
    return Counter(
        set(
            str(signal["code"])
            for signal in signals
            if isinstance(signal, dict) and signal.get("code")
        )
    )


def render_counts(counts: Counter[str]) -> list[dict[str, int | str]]:
    return [
        {"signal_code": code, "count": count}
        for code, count in sorted(counts.items())
    ]


def main() -> None:
    args = parse_args()
    profiles = load_profiles(args.input)
    config = load_rule_config(args.config) if args.config else load_rule_config()

    source_counts: Counter[str] = Counter()
    derived_counts: Counter[str] = Counter()
    for profile in profiles:
        compliance = profile.get("compliance")
        source_signals = (
            compliance.get("source_signals", [])
            if isinstance(compliance, dict)
            else []
        )
        source_counts.update(count_codes(source_signals))
        derived_counts.update(count_codes(generate_risk_signal_dicts(profile, config)))

    payload = {
        "company_count": len(profiles),
        "rule_version": config.rule_version,
        "source_signals": render_counts(source_counts),
        "derived_signals": render_counts(derived_counts),
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Coverage для {len(profiles)} компаний сохранён: {args.output}")
    else:
        print(rendered)


if __name__ == "__main__":
    main()

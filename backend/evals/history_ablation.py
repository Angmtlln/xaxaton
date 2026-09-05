"""GLM-only diagnostic: alter assistant history, preserve questions and trusted state."""
import argparse
import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from . import compare_models
from .payment_capacity import MODEL, SOURCE_RUNS


def ablate(cases, variant):
    if variant not in {'original', 'neutral', 'absent'}:
        raise ValueError('Unknown history variant')
    result = copy.deepcopy(cases)
    for case in result:
        history = case['frozen']['history']
        if variant == 'neutral':
            for message in history:
                if message['type'] == 'ai':
                    message['content'] = 'Данные компаний получены.'
        elif variant == 'absent':
            case['frozen']['history'] = [m for m in history if m['type'] != 'ai']
        case['input_sha256'] = compare_models.digest(case['frozen'])
    return result


async def run(args):
    if Settings().master_model != MODEL:
        raise ValueError('Only the current GLM is authorized')
    bank, original = compare_models.load_cases(SOURCE_RUNS, ['K19', 'S15_10'])
    cases = ablate(original, args.variant)
    args.run, args.case, args.model = SOURCE_RUNS, ['K19', 'S15_10'], [MODEL]
    args.latency_ms = 60000
    with patch.object(compare_models, 'load_cases', lambda *_: (bank, cases)):
        status = await compare_models.run(args)
    out = Path(args.output)
    manifest = json.loads((out / 'latest.json').read_text())
    manifest['mode'] = 'assistant-history ablation; trusted state, human history, question and prompt unchanged'
    manifest['history_variant'] = args.variant
    (out / 'latest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variant', choices=['original', 'neutral', 'absent'], required=True)
    parser.add_argument('--repetitions', type=int, choices=range(1, 6), default=3)
    parser.add_argument('--concurrency', type=int, choices=range(1, 5), default=2)
    parser.add_argument('--output', required=True)
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == '__main__':
    main()

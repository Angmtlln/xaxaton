"""Live UI contract smoke: four bounded turns, no retries. Uses configured server models."""
import argparse
import json
import time
from pathlib import Path
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://localhost:8000')
    parser.add_argument('--output', type=Path, default=Path('/tmp/xaxaton-night-smoke.json'))
    args = parser.parse_args()
    runs = []
    def turn(message, cid=None):
        started = time.monotonic()
        request = Request(args.base_url + '/api/v1/chat/messages/stream',
                          json.dumps({'message': message, 'conversation_id': cid}).encode(),
                          {'Content-Type': 'application/json'})
        events, payload = [], None
        with urlopen(request, timeout=90) as response:
            for line in response:
                event = json.loads(line)
                events.append({'seconds': round(time.monotonic()-started, 3), **event})
                if event['type'] == 'result': payload = event['payload']
                if event['type'] == 'error': raise RuntimeError(event['detail'])
        assert payload is not None, 'Incomplete stream'
        runs.append({'message': message, 'events': events})
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(runs, ensure_ascii=False, indent=2))
        print(json.dumps({'message': message, 'seconds': round(time.monotonic()-started, 2),
            'metadata': payload['metadata'], 'stages': [e['stage'] for e in events if e['type']=='progress'],
            'profile': {k: v['level'] for k,v in (payload.get('leading_artifact') or {}).get('risk_profile', {}).items()}}, ensure_ascii=False), flush=True)
        assert payload['metadata']['status'] != 'error', payload['metadata']
        return payload
    first = turn('Проверь контрагента 7805327192')
    assert first['active_company']['inn'] == '7805327192'
    assert first['suggested_actions'][0]['label'] == 'Построить граф связей'
    graph = turn('Построй граф связей', first['conversation_id'])
    assert graph['metadata']['tool_calls'] == graph['metadata']['model_calls'] == 0
    assert graph['blocks'][0]['graph']['total_edges'] == 5
    neighbour = turn('Сделай отдельный отчёт по связанной компании', graph['conversation_id'])
    assert neighbour['active_company']['inn'] == '4720028039'
    assert neighbour['leading_artifact']['inn'] == '4720028039'
    clean = turn('Проверь контрагента 1684017097')
    assert clean['leading_artifact']['risk_profile']
    print('PASS: full check, graph, neighbour resolution, independent profile; inspect saved prose separately.', flush=True)


if __name__ == '__main__':
    main()

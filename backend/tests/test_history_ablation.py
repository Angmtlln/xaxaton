import copy

from evals.compare_models import digest
from evals.history_ablation import ablate


def test_only_assistant_prose_changes_and_inputs_are_isolated():
    frozen = {'before': {'comparison_context': {'assets': 8046000}},
              'last_answer_verified': False, 'question': 'Кто точно расплатится по обязательствам?',
              'history': [{'type': 'human', 'content': 'Сравни компании'},
                          {'type': 'ai', 'content': 'Платить нечем.'}]}
    cases = [{'frozen': frozen, 'input_sha256': digest(frozen)}]
    saved = copy.deepcopy(cases)
    variants = [ablate(cases, v)[0] for v in ['original', 'neutral', 'absent']]
    assert variants[0] == cases[0]
    for case in variants:
        assert {k:v for k,v in case['frozen'].items() if k != 'history'} == {k:v for k,v in frozen.items() if k != 'history'}
        assert [m for m in case['frozen']['history'] if m['type'] == 'human'] == frozen['history'][:1]
    assert variants[1]['frozen']['history'][1]['content'] == 'Данные компаний получены.'
    assert variants[2]['frozen']['history'] == frozen['history'][:1]
    assert len({c['input_sha256'] for c in variants}) == 3
    assert cases == saved

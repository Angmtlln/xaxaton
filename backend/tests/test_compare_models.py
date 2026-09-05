"""Paired eval must preserve source questions, factual state and identical history."""
import copy
import gzip
import json

import pytest
from langchain_core.messages import AIMessage

from evals.bank import ROOT
from evals.compare_models import load_cases, seed_store, digest
from evals.run_local import save_trace, state
from test_agent_runtime import _model, _runtime, _verified_context


@pytest.fixture
def archived_runs(tmp_path):
    archive = ROOT / 'docs/evals/2026-09-05/structured-scope-fix/runs.json.gz'
    runs = json.loads(gzip.decompress(archive.read_bytes()))
    paths = []
    for name in ('structured-scope-final-comparison', 'structured-scope-final-targets'):
        run = runs[name]
        path = tmp_path / name
        path.mkdir()
        (path / 'latest.json').write_text(json.dumps(run['manifest']))
        for row in run['turns']:
            save_trace(path / (row['case_id'] + '.json.gz'), row)
        paths.append(path)
    return paths


def test_replay_rejects_source_drift_wrong_questions_and_new_tool_turns(archived_runs):
    paths = archived_runs
    with pytest.raises(ValueError, match='contextual'):
        load_cases(paths, ['K15'])
    with pytest.raises(ValueError, match='Missing requested'):
        load_cases(paths, ['nonexistent'])
    path = paths[0] / 'latest.json'
    manifest = json.loads(path.read_text())
    manifest['bank_sha256'] = 'changed'
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='drift'):
        load_cases(paths, ['K19'])


def test_replay_rejects_edited_trace_question(archived_runs):
    path = archived_runs[0] / 'K19.json.gz'
    row = json.loads(gzip.decompress(path.read_bytes()))
    row['question'] = 'An easier substitute'
    save_trace(path, row)
    with pytest.raises(ValueError, match='Question differs'):
        load_cases(archived_runs, ['K19'])


@pytest.mark.asyncio
@pytest.mark.parametrize('case_id', ['K19', 'K21', 'S15_12'])
async def test_models_get_identical_context_and_history_without_domain_access(archived_runs, case_id, monkeypatch):
    bank, cases = load_cases(archived_runs, [case_id])
    case = cases[0]
    if case_id == 'K21':
        assert case['session']['fixture_contract'] == bank['fixtures']['source_conflict']
    frozen = copy.deepcopy(case['frozen'])
    inputs = []
    for _ in range(2):
        model = _model(AIMessage(content='{"message":"Данные требуют уточнения.","artifact":"none"}'))
        runtime = _runtime(model, grounding_debug=False)
        async def forbidden(*a, **kw):
            pytest.fail('Frozen contextual replay must not call tools')
        monkeypatch.setattr(runtime.registry, 'execute', forbidden)
        cid = await seed_store(runtime, case)
        assert await state(runtime.conversation_store, cid) == frozen['before']
        response = await runtime.run(frozen['question'], cid)
        assert response.metadata.synthesis == 'model'
        assert response.metadata.tool_calls == 0 and model.calls == 1
        messages = model._messages[0]
        actual = [{'type': m.type, 'content': m.content} for m in messages]
        assert actual[-1]['content'] == frozen['question']
        assert actual[1:-1] == frozen['history']
        verified = _verified_context(messages)
        encoded = json.dumps(verified, ensure_ascii=False)
        if case_id == 'K21':
            prose = verified['sections']['finance_source_commentary']['value']
            original = frozen['before']['trusted_context']['domains']['full_check']['sections']['finance_source_commentary']['value']
            assert prose == original and any(item['code'] == 'proceeds' for item in prose)
        else:
            assert 'court_stages' in encoded and '"count": "missing"' in encoded
        inputs.append(digest(actual))
        await runtime.tool_context.client.aclose()
    assert inputs[0] == inputs[1]
    assert case['frozen'] == frozen  # One attempt must not contaminate the other.

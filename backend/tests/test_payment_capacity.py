import copy
import json

import pytest

from evals.bank import documents, BANK
from evals.payment_capacity import overlay_documents, build_probes, INN


@pytest.mark.asyncio
async def test_cash_probe_preserves_zero_revenue_balance_and_rebuilds_all_views():
    original = documents()
    before = copy.deepcopy(original)
    bank = json.loads(BANK.read_text())
    session = next(s for s in bank['sessions'] if s['id'] == 'S15_10')
    question = session['turns'][0]['question']
    cases = [{'case_id': 'S15_10', 'frozen': {'question': question}, 'session': session}]
    contexts = []
    for variant, cash in [('fixed_assets', 49000), ('cash_rich', 7350000)]:
        docs = overlay_documents(original, variant)
        row = docs[INN]['report']['finReports'][0]
        original_row = original[INN]['report']['finReports'][0]
        assert row['common'] == original_row['common'] and row['common']['proceeds'] == 0
        assert row['liabilities'] == original_row['liabilities']
        assert row['assets']['totalAssets'] == 8046000
        assert sum((row['assets']['currentAssets']['bankroll'], row['assets']['currentAssets']['receivables'], row['assets']['uncurrentAssets']['fixedAssets'])) == 8046000
        assert row['assets']['currentAssets']['bankroll'] == cash
        result = (await build_probes(cases, docs))[0]
        assert result['frozen']['question'] == question
        ctx = result['frozen']['before']['comparison_context']
        encoded = json.dumps(ctx)
        assert '7350000' in encoded and '49000' in encoded
        company = next(x for x in ctx['companies'] if x['inn'] == INN)
        latest = next(x for x in company['sections']['finance_series']['value'] if x['year'] == 2025)
        assert latest['bankroll'] == cash and latest['proceeds'] == 0
        assert latest['total_assets'] == 8046000 and latest['capitals'] == 6794000
        inputs = company['sections']['calculations']['inputs'].values()
        cash_inputs = [x for x in inputs if x.get('field_ref', '').endswith('currentAssets.bankroll') and x.get('year') == 2025]
        assert cash_inputs and all(x['value'] == cash for x in cash_inputs)
        contexts.append(result['input_sha256'])
    assert contexts[0] != contexts[1]
    assert original == before

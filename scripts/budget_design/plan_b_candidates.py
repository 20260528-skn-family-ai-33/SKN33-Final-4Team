"""Candidate hypotheses affect budget proposals only, never transaction classification."""
from copy import deepcopy
from fractions import Fraction
from math import isfinite

from plan_b_history import CATEGORY_CODES, transaction_identity


def _candidate(item):
    # Mixed LLM/ML scores and unscored rules cannot be compared as probabilities.
    if item.get('suggestion_source') not in (None, 'ml'):
        return None
    candidates = item.get('suggestion_candidates')
    if not isinstance(candidates, list) or not candidates:
        return None
    scores = {}
    for row in candidates:
        if not isinstance(row, dict):
            return None
        code, confidence = row.get('category'), row.get('confidence')
        if (not isinstance(code, str) or code not in CATEGORY_CODES or code in scores
                or type(confidence) not in (int, float) or not isfinite(confidence)
                or not 0 <= confidence <= 1):
            return None
        scores[code] = Fraction(str(confidence))
    if sum(scores.values()) > 1:
        return None
    best = max(scores.values())
    winners = [c for c in scores if scores[c] == best]
    return winners[0] if best > 0 and len(winners) == 1 else None


def adjust_from_candidates(inp, months, original, recalculate):
    """One shadow recalculation; take positive differences only, never fund new cuts."""
    from budgets.design_units import UNIT
    audit = {'method': 'candidate_hypothesis_recalculation', 'status': 'no_usable_candidates',
             'assumed_transaction_total': 0, 'corrections': []}
    if not original['history']['categories']['UNCLASSIFIED']['average']:
        return {**original, 'candidate_adjustment': audit}
    assumed = deepcopy(months)
    assumed_amount = 0
    content_counts = {}
    for month in sorted(assumed):
        record = assumed[month]
        for item in record.get('items', []):
            if (item.get('transaction_type') != 'spending' or item.get('funding') != 'living'
                    or item.get('net_amount', 0) <= 0):
                continue
            # Preserve original identities even for synthetic rows without DB transaction IDs.
            item['transaction_id'] = transaction_identity(item, month, content_counts)
            if item.get('category') != 'UNCLASSIFIED':
                continue
            code = _candidate(item)
            if code in ('HOUSING_TELECOM', 'TRAVEL_LODGING', None):
                continue
            item['category'] = code
            assumed_amount += item['net_amount']
    audit['assumed_transaction_total'] = assumed_amount
    if not assumed_amount or not original.get('unallocated', 0):
        if assumed_amount:
            audit['status'] = 'no_unallocated'
        return {**original, 'candidate_adjustment': audit}
    alternative = recalculate(assumed)
    if alternative['status'] != 'proposed':
        return {**original, 'candidate_adjustment': {**audit, 'status': 'hypothesis_unavailable'}}
    out = deepcopy(original)
    amounts = out['allocations']
    ranks = {r['category']: r['rank'] for r in inp['priorities']}
    extra = {c: max(0, alternative['allocations'][c] - amounts[c]) for c in amounts if c not in inp['locks']}
    free = out['unallocated']
    for code in sorted(extra, key=lambda c: (ranks.get(c, len(CATEGORY_CODES)+1), -extra[c], c)):
        addition = int(min(extra[code], free) // UNIT) * UNIT
        if not addition:
            continue
        amounts[code] += addition
        free -= addition
        audit['corrections'].append({'category': code, 'requested': extra[code], 'amount': addition})
        explanation = next(e for e in out['explanations'] if e['category'] == code)
        explanation['causes'].append({'code': 'unclassified_candidate_adjustment', 'amount': addition,
            'detail': {'method': audit['method'], 'hypothetical_difference': extra[code]}})
    from budgets.design_candidates import CATEGORY_NAMES
    from budgets.design_explain import explain_allocations
    from plan_b_budget import check_result_with_unallocated

    out['explanations'] = explain_allocations(
        {e['category']: e['reference_amount'] for e in out['explanations']}, amounts,
        {e['category']: e['causes'] for e in out['explanations']},
        reference_type='history_average', priorities=inp['priorities'], names=CATEGORY_NAMES)
    out['unallocated'] = free
    out['total_spare'] = out['spare_remaining'] + free
    summary = [s for s in out.get('summary', []) if s['code'] != 'surplus_left_as_free']
    if free:
        summary.append({'code': 'surplus_left_as_free', 'amount': free,
                        'text': f'예산을 배분하고 남는 {free:,}원은 여윳돈으로 남겼어요.'})
    out['summary'] = summary
    out['result'] = {**out['result'], 'allocations': amounts, 'unallocated': free,
                     'reasons': [e['text'] for e in out['explanations'] if e['text']],
                     'summary': ' '.join(s['text'] for s in summary)}
    floors = dict(out.get('system_floors', {}))
    start = out['start_allocations']
    floors = {c: min(v, start[c]) for c, v in floors.items()}
    for code, amount in out.get('ml_floors', {}).items():
        floors[code] = max(floors.get(code, 0), amount)
    check_result_with_unallocated(inp, out['result'], floors=floors)
    audit['status'] = 'applied' if audit['corrections'] else 'no_positive_difference'
    return {**out, 'candidate_adjustment': audit}

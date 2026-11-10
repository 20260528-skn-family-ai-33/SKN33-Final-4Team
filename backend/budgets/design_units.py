"""Final category amounts: prefer 10,000 KRW, use 5,000 when constraints require it."""
from copy import deepcopy
from fractions import Fraction
from math import ceil

from .recommend import EngineCheckFailed, check_result
from .design_explain import ADJUSTMENT_NOTICE_THRESHOLD, explain_allocations, priority_reduction_notices
from .design_candidates import CATEGORY_NAMES

UNIT = 5000
PREFERRED_UNIT = 10000


def round_allocations(allocations, target, *, minimums=None, locks=None, floors=None,
                      lower_bounds=None, upper_bounds=None, spare=0, priorities=(),
                      increase_reference=None, increase_limit=None):
    original = dict(allocations)
    minimums, locks, floors = minimums or {}, locks or {}, floors or {}
    if (type(target) is not int or type(spare) is not int or min(target, spare) < 0
            or any(type(v) is not int or v < 0 for v in original.values())
            or sum(original.values()) > target):
        raise EngineCheckFailed('invalid_rounding_input')
    ranks = {r['category']: r['rank'] for r in priorities}
    amounts, lower = {}, {}
    for code, amount in original.items():
        if code in locks:
            if locks[code] != amount or amount < minimums.get(code, 0):
                raise EngineCheckFailed('rounding_lock_conflict')
            amounts[code] = lower[code] = amount
            continue
        lo = max(minimums.get(code, 0), floors.get(code, 0), (lower_bounds or {}).get(code, 0), UNIT if amount else 0)
        hi = (upper_bounds or {}).get(code, max(ceil(Fraction(lo, UNIT))*UNIT, target + spare))
        lo, hi = ceil(Fraction(lo, UNIT)) * UNIT, int(Fraction(hi) // UNIT) * UNIT
        if lo > hi:
            return {'status': 'needs_upper_allocation_review', 'reason': 'budget_unit_conflict', 'category': code,
                    'allocations': None, 'shortfall': lo - hi}
        lower[code] = lo
        tens = {max(ceil(Fraction(lo, PREFERRED_UNIT))*PREFERRED_UNIT, min(int(Fraction(hi)//PREFERRED_UNIT)*PREFERRED_UNIT, candidate))
                for candidate in (amount//PREFERRED_UNIT*PREFERRED_UNIT, (amount//PREFERRED_UNIT+1)*PREFERRED_UNIT)}
        tens = [v for v in tens if lo <= v <= hi]
        choices = tens or [max(lo, min(hi, amount//UNIT*UNIT)), max(lo, min(hi, (amount//UNIT+1)*UNIT))]
        amounts[code] = min(choices, key=lambda v: (abs(v-amount), -v))
    def gain_excess():
        return (sum(max(0, amounts[c]-increase_reference[c]) for c in increase_reference)-increase_limit
                if increase_reference is not None else 0)
    excess = sum(amounts.values()) - target
    while excess > 0 or gain_excess() > 0:
        candidates = [c for c in amounts if c not in locks and amounts[c] - lower[c] >= UNIT]
        if gain_excess() > 0:
            candidates = [c for c in candidates if c in increase_reference and amounts[c] > increase_reference[c]]
        if not candidates:
            break
        step = PREFERRED_UNIT if max(excess, gain_excess()) >= PREFERRED_UNIT and any(amounts[c]-lower[c] >= PREFERRED_UNIT for c in candidates) else UNIT
        candidates = [c for c in candidates if amounts[c] - lower[c] >= step]
        code = min(candidates, key=lambda c: (
            0 if step == PREFERRED_UNIT or amounts[c] % PREFERRED_UNIT else 1,
            -(amounts[c]-original[c]), -ranks.get(c, len(amounts)+1), c))
        amounts[code] -= step
        excess -= step
    if gain_excess() > 0:
        return {'status': 'needs_upper_allocation_review', 'reason': 'budget_unit_policy_conflict',
                'allocations': None, 'shortfall': gain_excess()}
    transfer = max(0, sum(amounts.values())-target)
    if transfer > spare:
        return {'status': 'needs_upper_allocation_review', 'reason': 'budget_unit_unaffordable',
                'allocations': None, 'shortfall': transfer-spare}
    free = target + transfer - sum(amounts.values())
    return {'status': 'proposed', 'allocations': amounts, 'unallocated': free,
            'rounding_spare_transfer': transfer,
            'rounding': [{'category': c, 'amount': amounts[c]-original[c], 'unit': UNIT}
                         for c in amounts if amounts[c] != original[c]]}


def finalize_budget(inp, view, *, floors=None, lower_bounds=None, upper_bounds=None,
                    other_reserved=None, other_minimum=None, other_locked=False,
                    policy=None, b_cut_rates=None):
    """Round a checked presentation result; keep raw ML/history and calculation tools intact."""
    if view.get('status') != 'proposed' or view.get('allocations') is None:
        return deepcopy(view)
    view = deepcopy(view)
    original = view['allocations']
    prior_transfer = view.get('spare_transfer', 0)
    available = view.get('available_spare', 0)
    target = inp['target'] + prior_transfer
    source_result = view.get('result') or {'status': 'proposed', 'allocations': original,
        'unallocated': view.get('unallocated', 0), 'conflicts': [], 'reasons': [], 'summary': ''}
    check_result({**inp, 'target': target}, source_result)
    minimums, locks = dict(inp['minimums']), dict(inp['locks'])
    lower_bounds, upper_bounds = dict(lower_bounds or {}), dict(upper_bounds or {})
    candidate_audit = view.get('candidate_adjustment') or {}
    corrections = {r['category']: r['amount'] for r in candidate_audit.get('corrections', [])}
    floors = dict(floors or view.get('system_floors', {}))
    floors.update({c: max(floors.get(c, 0), v) for c, v in view.get('ml_floors', {}).items()})
    start = view.get('start_allocations')
    if start is not None:
        for code in floors:
            floors[code] = max(view.get('ml_floors', {}).get(code, 0), min(floors[code], start[code]))
        for code in original:
            if code in locks or code not in start:
                continue
            upper_bounds[code] = min(upper_bounds.get(code, target+available),
                                    ceil(Fraction(start[code] + corrections.get(code, 0), UNIT))*UNIT)
            if b_cut_rates is not None and code in b_cut_rates:
                lower_bounds[code] = max(lower_bounds.get(code, 0),
                    start[code] - int(Fraction(str(b_cut_rates[code]))*start[code]/100))
            if code in view.get('user_protected', []):
                lower_bounds[code] = max(lower_bounds.get(code, 0), start[code])
    reference, gain_limit = None, None
    if policy is not None and view.get('engine', {}).get('candidates'):
        base = view['engine']['candidates'][0]['result']['allocations']
        reference = dict(base)
        gain_limit = policy['total_limit']
        for code in original:
            lower_bounds[code] = max(lower_bounds.get(code, 0), base[code]-policy['decrease_limits'].get(code, 0))
            upper_bounds[code] = min(upper_bounds.get(code, target+available),
                max(reference[code], base[code]+policy['increase_limits'].get(code, 0)))
            if code in policy['ceilings']:
                upper_bounds[code] = min(upper_bounds[code], max(reference[code], policy['ceilings'][code]))
    reserved = 0 if other_reserved is None else other_reserved
    values = dict(original)
    if other_reserved is not None:
        values['ETC'] = reserved
        minimums['ETC'] = reserved if other_minimum is None else other_minimum
        if other_locked:
            locks['ETC'] = reserved
    rounding_options = dict(minimums=minimums, locks=locks, floors=floors, lower_bounds=lower_bounds,
        upper_bounds=upper_bounds, spare=available-prior_transfer, priorities=inp['priorities'],
        increase_reference=reference, increase_limit=gain_limit)
    baseline_rounded = None
    if corrections and not candidate_audit.get('rounding_reconciled'):
        # Reserve the original final-rounding costs before spending any remainder on hypotheses.
        baseline_values, baseline_upper = dict(values), dict(upper_bounds)
        for code, amount in corrections.items():
            baseline_values[code] -= amount
            if start is not None:
                baseline_upper[code] = min(baseline_upper[code],
                                           ceil(Fraction(start[code], UNIT))*UNIT)
        baseline_rounded = round_allocations(baseline_values, target+reserved,
                                             **{**rounding_options, 'upper_bounds': baseline_upper})
        if baseline_rounded['status'] == 'proposed':
            lower_bounds = {c: max(lower_bounds.get(c, 0), v) for c, v in baseline_rounded['allocations'].items()}
            upper_bounds = {c: min(upper_bounds.get(c, target+available), v+corrections.get(c, 0))
                            for c, v in baseline_rounded['allocations'].items()}
            rounding_options.update(lower_bounds=lower_bounds, upper_bounds=upper_bounds,
                                    spare=baseline_rounded['rounding_spare_transfer'])
    elif candidate_audit.get('rounding_reconciled'):
        # A second presentation pass must not spend or remove an already reconciled adjustment.
        rounding_options.update(lower_bounds={c: max(lower_bounds.get(c, 0), v) for c, v in values.items()},
                                upper_bounds={c: min(upper_bounds.get(c, target+available), v) for c, v in values.items()})
    rounded = (baseline_rounded if baseline_rounded is not None and baseline_rounded['status'] != 'proposed'
               else round_allocations(values, target+reserved, **rounding_options))
    if rounded['status'] != 'proposed':
        if corrections:
            view['candidate_adjustment'] = {**candidate_audit, 'status': 'rounding_unavailable', 'corrections': []}
        return {**view, **rounded, 'result': None, 'spare_transfer': 0, 'spare_remaining': available}
    amounts = dict(rounded['allocations'])
    final_additions = dict(corrections)
    if baseline_rounded is not None:
        rounded['rounding'] = deepcopy(baseline_rounded['rounding'])
        final_additions = {c: amounts[c]-baseline_rounded['allocations'][c] for c in corrections}
        rows = [{**row, 'proposed_amount': row['amount'], 'amount': final_additions[row['category']]}
                for row in candidate_audit['corrections'] if final_additions[row['category']] > 0]
        view['candidate_adjustment'] = {**candidate_audit, 'corrections': rows,
            'status': 'applied' if rows else 'no_effect_after_rounding', 'rounding_reconciled': True}
    new_reserved = amounts.pop('ETC', reserved)
    transfer = prior_transfer + rounded['rounding_spare_transfer']
    funded = inp['target'] + reserved + transfer - new_reserved
    base_result = view.get('result') or {'status': 'proposed', 'reasons': [], 'summary': '', 'conflicts': []}
    result = {**base_result, 'allocations': amounts, 'unallocated': rounded['unallocated']}
    check_result({**inp, 'target': funded}, result)
    explanations = view.get('explanations', [])
    if explanations:
        ledger = {}
        for e in explanations:
            code = e['category']
            ledger[code] = deepcopy(e['causes'])
            delta = amounts[code] - e['final_amount']
            if baseline_rounded is not None:
                ledger[code] = [c for c in ledger[code] if c['code'] != 'unclassified_candidate_adjustment']
                if final_additions.get(code, 0):
                    cause = next(c for c in e['causes'] if c['code'] == 'unclassified_candidate_adjustment')
                    ledger[code].append({**deepcopy(cause), 'amount': final_additions[code]})
                delta += corrections.get(code, 0) - final_additions.get(code, 0)
            if delta:
                ledger[code].append({'code': 'rounding', 'amount': delta, 'detail': {'unit': UNIT, 'preferred_unit': PREFERRED_UNIT, 'stage': 'final_budget'}})
        if view.get('variant') == 'b_forecast' and view.get('llm_status') == 'completed':
            for e in explanations:
                code = e['category']
                e.update(final_amount=amounts[code], diff=amounts[code]-e['reference_amount'], causes=ledger[code])
                why = e['causes'][0]['detail']['text']
                e['text'] = (f"{CATEGORY_NAMES[code]}: 지난 세 달 기준 {float(e['reference_amount']):,.0f}원 → 예산 {amounts[code]:,}원({float(e['diff']):+,.0f}원). {why}"
                             if abs(e['diff']) > ADJUSTMENT_NOTICE_THRESHOLD else '')
        else:
            explanations = explain_allocations({e['category']: e['reference_amount'] for e in explanations}, amounts, ledger,
                reference_type=explanations[0]['reference_type'], priorities=inp['priorities'], names=CATEGORY_NAMES,
                stop_reasons=view.get('stop_reasons'))
        explanations = priority_reduction_notices(explanations, amounts, inp['priorities'], locks,
                                                 view.get('history'), CATEGORY_NAMES)
        if any(sum(c['amount'] for c in e['causes']) != e['diff'] for e in explanations):
            raise EngineCheckFailed('rounded_explanation_mismatch')
        result['reasons'] = [e['text'] for e in explanations if e['text']]
    total_spare = available-transfer+rounded['unallocated']
    if sum(amounts.values()) + new_reserved + total_spare != inp['target'] + reserved + available:
        raise EngineCheckFailed('rounded_funding_mismatch')
    summary = view.get('summary', [])
    summary = [s for s in summary if s['code'] not in ('surplus_left_as_free', 'spare_to_living')] if isinstance(summary, list) else []
    if rounded['unallocated']:
        summary.append({'code': 'surplus_left_as_free', 'amount': rounded['unallocated'],
                        'text': f"예산을 배분하고 남는 {rounded['unallocated']:,}원은 여윳돈으로 남겼어요."})
    if transfer:
        summary.append({'code': 'spare_to_living', 'amount': transfer,
                        'text': f'금액 단위와 보호 기준을 지키기 위해 여윳돈 {transfer:,}원을 생활비에 포함했어요.'})
    if summary or baseline_rounded is not None:
        result['summary'] = ' '.join(s['text'] for s in summary)
    return {**view, 'allocations': amounts, 'result': result, 'explanations': explanations, 'summary': summary,
        'rounding': rounded['rounding'], 'unallocated': rounded['unallocated'], 'other_reserved': new_reserved,
        'spare_transfer': transfer, 'available_spare': available, 'spare_remaining': available-transfer,
        'total_spare': total_spare, 'funded_target': funded, 'requires_upper_allocation_approval': transfer > 0,
        'effective_input': {**view.get('effective_input', inp), 'target': funded}}

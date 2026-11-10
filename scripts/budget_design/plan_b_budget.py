"""Plan B stage 2: history baseline using Plan A's existing server engine.

Call after Django setup, with target already excluding upstream ETC reservation.
No ML, LLM, personalization policy, database access or upper-budget changes.
"""
from copy import deepcopy
from fractions import Fraction
from math import ceil, isfinite

from plan_b_history import summarize_history


def _minimum_reasons(inp, category_codes):
    """Optional user context, not instructions or extra financial protection."""
    raw = inp.pop('minimum_reasons', None)
    if raw is None:
        return {}
    if not isinstance(raw, dict) or set(raw) - set(category_codes):
        raise ValueError('invalid_minimum_reasons')
    if 'TRAVEL_LODGING' in raw:
        raise ValueError('travel_minimum_reason_not_allowed')
    if 'HOUSING_TELECOM' in raw:
        raise ValueError('housing_minimum_reason_not_allowed')
    reasons = {}
    for code, value in raw.items():
        if value is None:
            continue
        if not isinstance(value, str) or len(value) > 1000:
            raise ValueError('invalid_minimum_reason_text')
        value = value.strip()
        if not value:
            continue
        minimums = inp.get('minimums')
        amount = minimums.get(code) if isinstance(minimums, dict) else None
        if type(amount) is not int or amount <= 0:
            raise ValueError('minimum_reason_requires_positive_minimum')
        reasons[code] = value
    return reasons


def _gap_type(gap):
    return 'surplus' if gap > 0 else 'shortfall' if gap < 0 else 'match'


def ml_cut_floors(history, ml_reference):
    """Protect only history above the raw ML share; never infer a cost-of-living floor."""
    from budgets.recommend import CATEGORY_CODES
    if ml_reference is None:
        return {}
    if (not isinstance(ml_reference, dict) or set(ml_reference) != set(CATEGORY_CODES)
            or any(type(v) not in (int, float, Fraction) or not isfinite(v) or v < 0
                   for v in ml_reference.values())):
        raise ValueError('invalid_ml_reference')
    return {c: ceil(Fraction(str(v)) / 1000) * 1000 for c, v in ml_reference.items()
            if c not in ('HOUSING_TELECOM', 'TRAVEL_LODGING')
            and history['categories'][c]['baseline_amount'] > Fraction(str(v))}


def _baseline_ledger(history, active, targets=None):
    ledger = {c: [] for c in history['categories'] if c not in ('ETC', 'UNCLASSIFIED')}
    for c in ledger:
        row = history['categories'][c]
        if c not in active:
            ledger[c].append({'code': 'inactive_category', 'amount': -row['average'],
                              'detail': {'history_average': row['average']}})
        elif history['baseline'] == 'one_off_adjusted':
            ledger[c].extend({'code': 'one_off_excluded', 'amount': -Fraction(candidate['amount'], 3),
                             'detail': {k: candidate[k] for k in ('id', 'month', 'amount', 'method')}}
                            for candidate in history['one_off_candidates'] if candidate['category'] == c and candidate['excluded'])
        elif row['baseline_amount'] != row['average']:
            ledger[c].append({'code': 'history_baseline', 'amount': row['baseline_amount'] - row['average'],
                              'detail': {'baseline': history['baseline']}})
    for c, amount in (targets or {}).items():
        ledger[c].append({'code': 'category_target_start', 'amount': amount-history['categories'][c]['baseline_amount'],
                          'detail': {'requested': amount}})
    return ledger


def check_result_with_unallocated(inp, out, *, floors=None):
    """Use the shared unallocated contract and retain Plan B's system-floor check."""
    from budgets.recommend import EngineCheckFailed, check_inputs, check_result
    check_inputs(inp)
    free = out.get('unallocated')
    if type(free) is not int or free < 0 or free > inp['target'] or out.get('status') != 'proposed':
        raise EngineCheckFailed('invalid_unallocated')
    check_result(inp, out)
    if any(out['allocations'].get(c, 0) < amount for c, amount in (floors or {}).items()
           if c not in inp['locks']):
        raise EngineCheckFailed('system_floor_violated')


def _starting_amounts(inp, values, active, floors, rounding_floors=None):
    values = {**values, **inp.get('category_targets', {})}
    raw = {c: (Fraction(inp['locks'][c]) if c in inp['locks'] else
               max(values[c], inp['minimums'].get(c, 0), floors.get(c, 0)))
           if c in active else Fraction(0) for c in values}
    start = {c: (inp['locks'][c] if c in inp['locks'] else
                 max(int(values[c] / 1000 + Fraction(1, 2)) * 1000,
                     inp['minimums'].get(c, 0), floors.get(c, 0), (rounding_floors or {}).get(c, 0)))
             if c in active else 0 for c in values}
    return raw, start


def _surplus_budget(inp, response, values, active, floors):
    from budgets.design_explain import explain_allocations
    from budgets.design_candidates import CATEGORY_NAMES
    values = {c: values[c] for c in CATEGORY_NAMES}
    raw, start = _starting_amounts(inp, values, active, floors, response.get('ml_floors'))
    if sum(start.values()) > inp['target']:
        return None
    free = inp['target'] - sum(start.values())
    history = response['history']
    ledger = _baseline_ledger(history, active, inp.get('category_targets'))
    values = {**values, **inp.get('category_targets', {})}
    for c in active:
        if c in inp['locks']:
            ledger[c].append({'code': 'lock_fixed', 'amount': raw[c] - values[c], 'detail': {'locked': start[c]}})
        else:
            minimum_start = max(values[c], inp['minimums'].get(c, 0))
            if minimum_start > values[c]:
                ledger[c].append({'code': 'user_minimum_raise', 'amount': minimum_start - values[c],
                                  'detail': {'minimum': inp['minimums'][c]}})
            if raw[c] > minimum_start:
                ledger[c].append({'code': 'system_floor_raise', 'amount': raw[c] - minimum_start,
                                  'detail': {'floor': floors[c]}})
            if start[c] != raw[c]:
                ledger[c].append({'code': 'rounding', 'amount': start[c] - raw[c], 'detail': {'unit': 1000}})
    explanations = explain_allocations({c: history['categories'][c]['average'] for c in values}, start,
        ledger, reference_type='history_average', priorities=inp['priorities'], names=CATEGORY_NAMES)
    summary = [{'code': 'surplus_left_as_free', 'amount': free,
                'text': f'평소 소비만큼만 예산을 잡고 남는 {free:,}원은 여윳돈으로 남겼어요.'}]
    excluded = [c for c in history['one_off_candidates'] if c['excluded'] and c['category'] in active]
    if excluded and history['baseline'] == 'one_off_adjusted':
        first = excluded[0]
        category = '의료비' if first['category'] == 'MEDICAL_HEALTH' else CATEGORY_NAMES[first['category']]
        summary.append({'code': 'one_off_excluded', 'candidate_ids': [c['id'] for c in excluded],
                        'text': f"{int(first['month'].split('-')[1])}월 {category} 등 한 번만 있었던 지출은 월 예산 기준에서 뺐어요."})
    result = {'status': 'proposed', 'allocations': start, 'unallocated': free, 'conflicts': [],
              'summary': summary[0]['text'], 'reasons': [e['text'] for e in explanations if e['text']]}
    check_result_with_unallocated(inp, result, floors={c: max(floors.get(c, 0), response.get('ml_floors', {}).get(c, 0))
                                                      for c in active})
    return {**response, 'status': 'proposed', 'result': result, 'allocations': start,
            'effective_input': deepcopy(inp), 'draft_amounts': values, 'draft_available': True,
            'surplus_policy': 'leave_as_free', 'unallocated': free, 'summary': summary,
            'start_allocations': dict(start), 'cuts': [], 'system_floors': floors,
            'floor_ignored_locked': [c for c in floors if c in inp['locks']],
            'explanations': explanations, 'warnings': []}


def _base_engine(inp, weights):
    from budgets.design_candidates import build_candidates
    from budgets.recommend import CATEGORY_CODES, required_total

    try:
        return build_candidates(inp, weights)
    except ValueError as error:
        if str(error) not in ('zero_ratio_sum', 'zero_free_weights_requires_policy'):
            raise
        if required_total(inp) == inp['target']:
            return build_candidates(inp, {c: inp['minimums'].get(c, 0) for c in CATEGORY_CODES})
        return None


def _experiment_result(inp, weights, active, history_total):
    from budgets.recommend import CATEGORY_CODES, check_result

    experiment_input = deepcopy(inp)
    # An explicit integer-won experiment target; retain the exact reference too.
    experiment_input['target'] = int(history_total)
    experiment = {'experiment_only': True, 'eligible_for_approval': False, 'persistable': False,
                  'original_target': inp['target'], 'experiment_target': experiment_input['target'],
                  'reference_history_total': history_total,
                  'rounding_remainder': history_total - experiment_input['target'],
                  'result': None, 'effective_input': None}
    engine = _base_engine(experiment_input, weights)
    if engine is None:
        return {**experiment, 'status': 'needs_data', 'reason': 'no_history_for_remaining_allocation'}
    experiment_input['locks'].update({c: 0 for c in CATEGORY_CODES if c not in active})
    result = engine['result'] if engine['status'] == 'infeasible' else engine['candidates'][0]['result']
    check_result(experiment_input, result)
    return {**experiment, 'status': result['status'], 'result': result, 'effective_input': experiment_input}


def build_history_budget(inp, months, *, as_of, other_reserved=None,
                         unclassified_share_limit=None, experiment_capped=False, baseline='one_off_adjusted',
                         user_reincluded_one_offs=(), one_off_decisions=None, reality_profile=None, ml_reference=None):
    from budgets.recommend import CATEGORY_CODES, check_result

    inp = deepcopy(inp)
    minimum_reasons = _minimum_reasons(inp, CATEGORY_CODES)
    if type(inp.get('target')) is not int or inp['target'] < 0:
        raise ValueError('invalid_target')
    if other_reserved is not None and (type(other_reserved) is not int or other_reserved < 0):
        raise ValueError('invalid_other_reserved')
    if (unclassified_share_limit is not None and
            (type(unclassified_share_limit) not in (int, float) or not isfinite(unclassified_share_limit)
             or not 0 <= unclassified_share_limit <= 1)):
        raise ValueError('invalid_unclassified_share_limit')
    if type(experiment_capped) is not bool:
        raise ValueError('invalid_experiment_mode')
    history = summarize_history(months, as_of=as_of, baseline=baseline,
                                user_reincluded_one_offs=user_reincluded_one_offs, one_off_decisions=one_off_decisions)
    response = {'status': 'needs_data', 'source': 'history', 'history': history,
                'minimum_reasons': minimum_reasons,
                'draft_amounts': {}, 'result': None, 'effective_input': None,
                'variant': 'proportional', 'excluded_spending': {},
                'history_total': None, 'target': inp['target'], 'gap': None, 'gap_type': None,
                'comparison_history_total': None, 'shortfall_signal': None,
                'unclassified_share': None, 'experiment_capped_result': None}
    response.update(baseline=history['baseline'], one_off_candidates=history['one_off_candidates'],
                    stale_reincluded_ids=history['stale_reincluded_ids'],
                    one_off_unavailable=history['one_off_unavailable'])
    if history['status'] != 'ready':
        return {**response, 'reason': 'history_not_ready'}

    if reality_profile is not None:
        reality_profile = {**reality_profile, 'transport_history': history['categories']['TRANSPORT_CAR']['baseline_amount']}

    active = [c for c in CATEGORY_CODES if c not in ('TRAVEL_LODGING', 'HOUSING_TELECOM')]
    ml_floors = ml_cut_floors(history, ml_reference)
    response.update(ml_floors=ml_floors, ml_floor_status='applied' if ml_reference is not None else 'not_provided',
                    ml_floor_ignored_locked=[c for c in ml_floors if c in inp['locks']])
    # Compare only monthly allocation scope; retain excluded spending for analysis.
    values = {c: row['baseline_amount'] for c, row in history['categories'].items()}
    history_total = sum(values.values(), Fraction(0))
    etc = values['ETC']
    excluded = {c: values[c] for c in CATEGORY_CODES if c not in active}
    comparison_total = history_total - etc - sum(excluded.values())
    unclassified = values['UNCLASSIFIED']
    share = unclassified / comparison_total if comparison_total else None
    gap = inp['target'] - comparison_total
    etc_gap = None if other_reserved is None else other_reserved - etc
    response.update(history_total=history_total, original_history_total=history['baseline_totals']['average'],
                    comparison_history_total=comparison_total,
                    active_categories=active, excluded_spending=excluded,
                    gap=gap, gap_type=_gap_type(gap), shortfall_signal=gap < 0,
                    unclassified_share=share, unclassified_average=unclassified,
                    unclassified_share_limit=unclassified_share_limit,
                    etc_comparison={'history_average': etc, 'reserved_amount': other_reserved,
                                    'gap': etc_gap, 'gap_type': None if etc_gap is None else _gap_type(etc_gap)})

    # Equal three-month denominators cancel: integer totals preserve exact ratios.
    from budgets.design_explain import explain_allocations
    from budgets.design_candidates import CATEGORY_NAMES
    references = {c: history['categories'][c]['average'] for c in CATEGORY_CODES}
    ledger = _baseline_ledger(history, active)
    response['baseline_explanations'] = explain_allocations(references, {c: values[c] if c in active else 0 for c in CATEGORY_CODES},
        ledger, reference_type='history_average', priorities=inp['priorities'], names=CATEGORY_NAMES)
    weights = {c: int(values[c] * 3) for c in CATEGORY_CODES}
    # Goals are soft starting values in the priority path, not locks in the comparison baseline.
    baseline_input = {**inp, 'category_targets': {}} if inp.get('category_targets') else inp
    engine = _base_engine(baseline_input, weights)

    locked_remainder_only = (engine is not None and engine['status'] == 'infeasible'
        and all(c['type'] == 'locks_mismatch_living' and c['difference'] < 0 for c in engine['result']['conflicts']))
    if engine is not None and engine['status'] == 'infeasible' and not (
            inp['target'] > sum(values[c] for c in active) and locked_remainder_only):
        return {**response, 'status': 'infeasible', 'result': engine['result'],
                'next_action': engine['next_action']}

    active_total = sum(weights[c] for c in active)
    allocation_history_total = Fraction(active_total, 3)
    response.update(allocation_history_total=allocation_history_total,
                    allocation_gap=inp['target'] - allocation_history_total,
                    allocation_gap_type=_gap_type(inp['target'] - allocation_history_total))
    scope_mixed = response['gap_type'] != response['allocation_gap_type']
    response['scope_state'] = 'mixed' if scope_mixed else 'aligned'
    # Unclassified spending remains in the audit; its share does not disable B.
    if ml_floors:
        guarded = deepcopy(inp)
        guarded['minimums'].update({c: max(inp['minimums'].get(c, 0), v)
                                    for c, v in ml_floors.items() if c not in inp['locks']})
        required = sum(inp['locks'][c] if c in inp['locks'] else guarded['minimums'].get(c, 0) for c in active)
        if required > inp['target']:
            return {**response, 'status': 'needs_upper_allocation_review', 'reason': 'ml_floor_unaffordable',
                    'shortfall': required-inp['target'], 'allocations': None, 'result': None}
        engine = _base_engine({**guarded, 'category_targets': {}}, weights)
    if response['allocation_gap'] > 0:
        from budgets.design_reality import system_floors
        floors = system_floors(reality_profile) if reality_profile is not None else {}
        # The old capped-target output is opt-in comparison only, never the proposed budget.
        response['experiment_capped_result'] = (_experiment_result(inp, weights, active, allocation_history_total)
                                                if experiment_capped else None)
        surplus = _surplus_budget(inp, response, values, active, floors)
        if surplus is not None:
            return surplus
        response['surplus_policy'] = 'leave_as_free'
        response['settings_require_shortfall'] = True
        response['system_floors'] = floors
        if floors:
            from budgets.design_candidates import build_candidates
            engine = build_candidates(guarded if ml_floors else inp, weights, reality_profile=reality_profile)
            if engine['status'] != 'proposed':
                return {**response, 'status': engine['status'], 'reason': engine.get('reason'),
                        'result': engine.get('result'), 'shortfall': engine.get('shortfall')}
    if engine is None:
        return {**response, 'reason': 'no_history_for_remaining_allocation'}
    draft = {c: Fraction(inp['target'] * weights[c], active_total)
             if c in active and active_total else Fraction(0) for c in CATEGORY_CODES}
    effective = deepcopy(inp)
    effective['locks'].update({c: 0 for c in CATEGORY_CODES if c not in active})
    result = engine['candidates'][0]['result']
    check_result(effective, result)
    for c in active:
        delta = result['allocations'][c] - values[c]
        ledger[c].append({'code': 'lock_fixed' if c in inp['locks'] else 'constraint_redistribution',
                          'amount': delta, 'detail': {'stage': 'proportional_baseline'}})
    explanations = explain_allocations(references, result['allocations'], ledger, reference_type='history_average',
                                       priorities=inp['priorities'], names=CATEGORY_NAMES)
    result = {**result, 'unallocated': 0, 'reasons': [r['text'] for r in explanations if r['text']]}
    check_result_with_unallocated(effective, result, floors={c: max(response.get('system_floors', {}).get(c, 0),
                                                                  ml_floors.get(c, 0)) for c in active})
    return {**response, 'status': 'proposed', 'draft_amounts': draft, 'result': result,
            'explanations': explanations,
            'effective_input': effective, 'active_categories': active,
            'draft_available': bool(active_total), 'warnings': engine['warnings'], 'unallocated': 0}


ORDER_REASONS = ('recent_increase', 'one_off_spike', 'irregular', 'stable')


def shortfall_groups(baseline):
    """Rank tiers are server-owned; model ordering cannot cross these boundaries."""
    ranks = {r['category']: r['rank'] for r in baseline['effective_input']['priorities']}
    active = baseline['active_categories']
    groups = {}
    unranked = [c for c in active if c not in ranks]
    if unranked:
        groups['unranked'] = unranked
    for rank in sorted({ranks[c] for c in active if c in ranks}, reverse=True):
        groups[f'rank_{rank}'] = [c for c in active if ranks.get(c) == rank]
    return groups


def fixed_shortfall_order(baseline):
    history = baseline['history']['categories']

    def key(code):
        row = history[code]
        growth, spike = row['latest_growth_rate'], row['max_to_median_ratio']
        return (growth is None, -(growth or 0), spike is None, -(spike or 0), code)

    orders = {group: sorted(codes, key=key) for group, codes in shortfall_groups(baseline).items()}
    reasons = {}
    for code in baseline['active_categories']:
        row = history[code]
        if row['latest_growth_rate'] is not None and row['latest_growth_rate'] > 0:
            reason = 'recent_increase'
        elif sum(v > 0 for v in row['monthly_amounts']) == 1:
            reason = 'one_off_spike'
        elif len(set(row['monthly_amounts'])) == 1:
            reason = 'stable'
        else:
            reason = 'irregular'
        reasons[code] = reason
    return orders, reasons


def validate_shortfall_order(baseline, orders, reasons):
    groups = shortfall_groups(baseline)
    if not isinstance(orders, dict) or set(orders) != set(groups):
        raise ValueError('invalid_order_groups')
    for group, codes in groups.items():
        values = orders[group]
        if (not isinstance(values, list) or any(not isinstance(c, str) for c in values)
                or len(values) != len(codes) or set(values) != set(codes)):
            raise ValueError('group_permutation_required')
    if (not isinstance(reasons, dict) or set(reasons) != set(baseline['active_categories'])
            or any(v not in ORDER_REASONS for v in reasons.values())):
        raise ValueError('invalid_order_reason_codes')


def _spare_result(out, inp, available_spare, transfer):
    """Local proposal accounting; upstream spare is not the budget's unallocated balance."""
    from budgets.recommend import EngineCheckFailed
    proposed = out['status'] == 'proposed'
    out = {**out, 'initial_target': inp['target'], 'available_spare': available_spare,
           'spare_transfer': transfer if proposed else 0,
           'requires_upper_allocation_approval': proposed and transfer > 0,
           'funded_target': inp['target'] + transfer if proposed else inp['target'],
           'spare_remaining': available_spare - transfer if proposed else available_spare}
    if proposed:
        out['total_spare'] = out['spare_remaining'] + out.get('unallocated', 0)
        if (not 0 <= transfer <= available_spare
                or sum(out['allocations'].values()) + out['total_spare'] != inp['target'] + available_spare):
            raise EngineCheckFailed('spare_funding_mismatch')
        if transfer:
            text = f'감액 한도와 보호 기준을 지키기 위해 여윳돈 {transfer:,}원을 생활비로 옮겼어요.'
            summary = out.get('summary', [])
            summary = list(summary) if isinstance(summary, list) else []
            out['summary'] = summary + [{'code': 'spare_to_living', 'amount': transfer, 'text': text}]
            out['result'] = {**out['result'], 'summary': ' '.join(r['text'] for r in out['summary'])}
    return out


def build_priority_history_budget(inp, months, *, available_spare=0, **options):
    """Recalculate candidate hypotheses, funding additions only from the existing remainder."""
    from plan_b_candidates import adjust_from_candidates
    out = _fund_priority_history_budget(inp, months, available_spare=available_spare, **options)
    if out['status'] != 'proposed':
        return out
    funded = {**inp, 'target': out['funded_target']}

    def recalculate(assumed):
        trial_options = dict(options)
        if trial_options.get('one_off_decisions') is not None:
            history = summarize_history(assumed, as_of=options['as_of'],
                baseline=options.get('baseline', 'one_off_adjusted'),
                user_reincluded_one_offs=options.get('user_reincluded_one_offs', ()))
            decisions = trial_options['one_off_decisions']
            trial_options['one_off_decisions'] = {c['id']: decisions.get(c['id'], 'one_off')
                                                for c in history['one_off_candidates']}
        return _build_priority_history_budget(funded, assumed, **trial_options)

    return adjust_from_candidates(funded, months, out, recalculate)


def _fund_priority_history_budget(inp, months, *, available_spare=0, **options):
    """Try existing cuts first, then request only the shortage from explicit upstream spare."""
    if type(available_spare) is not int or available_spare < 0:
        raise ValueError('invalid_available_spare')
    funded = deepcopy(inp)
    while True:
        out = _build_priority_history_budget(funded, months, **options)
        transfer = funded['target'] - inp['target']
        shortage = 0
        if out.get('reason') in ('ml_floor_unaffordable', 'system_floor_unaffordable'):
            shortage = out.get('shortfall', 0)
        elif out.get('reason') == 'shortfall_exceeds_cut_limits':
            shortage = out.get('remaining_shortfall', 0)
        elif out['status'] == 'infeasible':
            conflicts = (out.get('result') or {}).get('conflicts', [])
            if conflicts and all(c['type'] == 'minimums_over_living' or
                                 (c['type'] == 'locks_mismatch_living' and c['difference'] > 0)
                                 for c in conflicts):
                shortage = max(c.get('shortfall', c.get('difference', 0)) for c in conflicts)
        if type(shortage) is not int or shortage <= 0:
            return _spare_result(out, inp, available_spare, transfer)
        if shortage > available_spare - transfer:
            out = {**out, 'funding_required': transfer + shortage,
                   'funding_shortfall': transfer + shortage - available_spare}
            return _spare_result(out, inp, available_spare, transfer)
        # The target only increases within the supplied spare; ML reference and user settings stay fixed.
        funded['target'] += shortage


def _build_priority_history_budget(inp, months, *, as_of, shortfall_cut_rates,
                                  user_protected=(), group_orders=None, reason_codes=None,
                                  other_reserved=None, unclassified_share_limit=None,
                                  experiment_capped=False, increased=None, reality_profile=None,
                                  baseline='one_off_adjusted', user_reincluded_one_offs=(), one_off_decisions=None,
                                  ml_reference=None):
    """Cut history amounts in rank order; numeric policy and user protection are server inputs."""
    from budgets.design_candidates import CATEGORY_NAMES
    from budgets.recommend import CATEGORY_CODES, check_result

    baseline = build_history_budget(inp, months, as_of=as_of, other_reserved=other_reserved,
        unclassified_share_limit=unclassified_share_limit, experiment_capped=experiment_capped,
        baseline=baseline, user_reincluded_one_offs=user_reincluded_one_offs, one_off_decisions=one_off_decisions,
        reality_profile=reality_profile, ml_reference=ml_reference)
    active = baseline.get('active_categories', [c for c in CATEGORY_CODES if c not in ('TRAVEL_LODGING', 'HOUSING_TELECOM')])
    if (not isinstance(user_protected, (list, tuple))
            or any(not isinstance(c, str) or c not in active for c in user_protected)
            or len(set(user_protected)) != len(user_protected)):
        raise ValueError('invalid_user_protected')
    if baseline['status'] == 'proposed' and baseline.get('surplus_policy') == 'leave_as_free' and not baseline.get('settings_require_shortfall'):
        return {**baseline, 'variant': 'priority_order', 'split': 'proportional', 'user_protected': list(user_protected)}
    conflicts = (baseline.get('result') or {}).get('conflicts', [])
    locked_remainder_only = (baseline['status'] == 'infeasible' and conflicts
        and all(c['type'] == 'locks_mismatch_living' and c['difference'] < 0 for c in conflicts))
    if (inp['locks'] or inp.get('category_targets')) and (baseline['status'] == 'proposed' or locked_remainder_only):
        # Initial B uses actual locked starting costs; legacy proportional tools keep their baseline.
        from budgets.design_reality import system_floors
        values = {c: row['baseline_amount'] for c, row in baseline['history']['categories'].items()}
        if locked_remainder_only:
            total = sum(values[c] for c in active)
            baseline = {**baseline, 'allocation_history_total': total,
                'allocation_gap': inp['target']-total, 'allocation_gap_type': _gap_type(inp['target']-total),
                'scope_state': 'aligned' if baseline['gap_type'] == _gap_type(inp['target']-total) else 'mixed',
                'next_action': None}
        floors = system_floors({**reality_profile, 'transport_history': values['TRANSPORT_CAR']}) if reality_profile is not None else {}
        surplus = _surplus_budget(inp, baseline, values, active, floors)
        if surplus is not None:
            return {**surplus, 'variant': 'priority_order', 'split': 'proportional',
                    'user_protected': list(user_protected)}
    if (baseline['status'] != 'proposed'
            or (baseline['allocation_gap_type'] not in ('shortfall', 'match')
                and not baseline.get('settings_require_shortfall'))):
        return baseline
    if (not isinstance(shortfall_cut_rates, dict) or set(active) - set(shortfall_cut_rates)
            or set(shortfall_cut_rates) - set(CATEGORY_CODES)
            or any(type(v) not in (int, float) or not isfinite(v) or not 0 < v <= 100
                   for v in shortfall_cut_rates.values())):
        raise ValueError('explicit_shortfall_cut_rates_required')
    source = 'rule' if group_orders is None and reason_codes is None else 'llm'
    if source == 'rule':
        group_orders, reason_codes = fixed_shortfall_order(baseline)
    validate_shortfall_order(baseline, group_orders, reason_codes)
    effective = baseline['effective_input']
    ranks = {r['category']: r['rank'] for r in effective['priorities']}
    averages = {c: baseline['history']['categories'][c]['baseline_amount'] for c in CATEGORY_CODES}
    references = {c: baseline['history']['categories'][c]['average'] for c in CATEGORY_CODES}
    from budgets.design_reality import system_floors
    floors = system_floors({**reality_profile, 'transport_history': averages['TRANSPORT_CAR']}) if reality_profile is not None else {}
    starting_floors = floors if baseline.get('settings_require_shortfall') else {}
    ml_floors = baseline.get('ml_floors', {})
    raw_start, start = _starting_amounts(effective, averages, active, starting_floors, ml_floors)
    remaining = sum(start.values()) - effective['target']
    output = {**baseline, 'variant': 'priority_order', 'result': None, 'allocations': None,
              'split': 'proportional', 'start_allocations': start, 'cuts': [], 'remaining_shortfall': max(0, remaining),
              'starting_rounding': {c: start[c] - raw_start[c] for c in active},
              'rounding_adjustments': [],
              'user_protected': list(user_protected), 'group_orders': deepcopy(group_orders),
              'reason_codes': deepcopy(reason_codes), 'order_source': source}
    if sum(raw_start.values()) < effective['target'] and remaining <= 0:
        return {**output, 'status': 'needs_upper_allocation_review', 'reason': 'shortfall_start_below_target'}
    amounts = {c: int(v) for c, v in start.items()}
    cuts = []
    if reality_profile is not None:
        output['system_floors'] = floors
        output['floor_ignored_locked'] = [c for c in floors if c in effective['locks']]
    cut_floors = {c: max(effective['minimums'].get(c, 0), min(floors.get(c, 0), start[c]), ml_floors.get(c, 0))
                  for c in active}
    capacities = {c: (0 if c in effective['locks'] or c in user_protected else int(min(
        start[c] * Fraction(str(shortfall_cut_rates[c])) / 100,
        start[c] - cut_floors[c]))) for c in active}

    def record_cut(code, cut):
        existing = next((row for row in cuts if row['category'] == code), None)
        if existing is not None:
            existing['amount'] += cut
            return
        rank = ranks.get(code)
        tier = 'unranked' if rank is None else 'top' if rank <= 3 else 'low'
        cuts.append({'category': code, 'amount': cut, 'tier': tier, 'rank': rank,
                     'order_source': source, 'reason_code': reason_codes[code],
                     'exhausted_categories': list(exhausted),
                     'reducible_before': [c for c in active
                         if capacities[c] > start[c] - amounts[c] - (cut if c == code else 0)]})

    if increased is not None and (not isinstance(increased, list)
            or any(not isinstance(c, str) or c not in shortfall_groups(baseline).get('unranked', []) for c in increased)
            or len(set(increased)) != len(increased)):
        raise ValueError('invalid_increased_categories')
    if increased is not None:
        output['variant'] = 'priority_increase_first'
        output['increased'] = list(increased)
    groups = []
    for group, codes in shortfall_groups(baseline).items():
        if group == 'unranked' and increased is not None:
            groups.extend([[c for c in codes if c in increased], [c for c in codes if c not in increased]])
        else:
            groups.append(codes)
    exhausted = {}
    for codes in groups:
        charge = min(max(0, remaining), sum(capacities[c] for c in codes))
        if not charge:
            if remaining > 0:
                for c in codes:
                    if start[c] and not capacities[c]:
                        exhausted[c] = {'capacity': 0, 'rate': shortfall_cut_rates[c],
                                        'minimum': effective['minimums'].get(c, 0), 'floor': cut_floors[c]}
            continue
        total_capacity = sum(capacities[c] for c in codes)
        exact = {c: Fraction(charge * capacities[c], total_capacity) for c in codes}
        shares = {c: int(exact[c] // 1000) * 1000 for c in codes}
        residual = charge - sum(shares.values())
        for c in sorted(codes, key=lambda c: (-(exact[c] - shares[c]), -averages[c], CATEGORY_CODES.index(c))):
            if residual < 1000:
                break
            if capacities[c] - shares[c] >= 1000:
                shares[c] += 1000
                residual -= 1000
        # Residual stays in this group, with larger historical amounts first.
        for c in sorted(codes, key=lambda c: (-averages[c], CATEGORY_CODES.index(c))):
            extra = min(residual, capacities[c] - shares[c])
            shares[c] += extra
            residual -= extra
            if extra % 1000:
                output['rounding_adjustments'].append({'category': c, 'amount': -(extra % 1000)})
            if not residual:
                break
        for c, cut in shares.items():
            if cut:
                amounts[c] -= cut
                remaining -= cut
                record_cut(c, cut)
        if remaining > 0:
            for c in codes:
                if start[c] and start[c] - amounts[c] >= capacities[c]:
                    exhausted[c] = {'capacity': capacities[c], 'rate': shortfall_cut_rates[c],
                                    'minimum': effective['minimums'].get(c, 0), 'floor': cut_floors[c]}
    # R-05's largest-weight residual principle, while respecting all hard caps/protections.
    residual_order = sorted((c for c in active if c not in effective['locks'] and c not in user_protected),
                            key=lambda c: (-averages[c], CATEGORY_CODES.index(c)))
    if 0 < remaining < 1000:
        # Keep rank tiers: rounding may not jump to a higher priority before lower tiers.
        residual_order = [c for codes in shortfall_groups(baseline).values()
                          for c in residual_order if c in codes]
        for code in residual_order:
            cut = min(remaining, capacities[code] - (start[code] - amounts[code]))
            if cut <= 0:
                continue
            amounts[code] -= cut
            remaining -= cut
            record_cut(code, cut)
            output['rounding_adjustments'].append({'category': code, 'amount': -cut})
            if not remaining:
                break
    elif remaining < 0:
        # Only compensate rounding loss up to the next thousand of the historical average.
        for code in residual_order:
            ceiling = -(-averages[code] // 1000) * 1000
            addition = min(-remaining, max(0, int(ceiling - amounts[code])))
            if not addition:
                continue
            amounts[code] += addition
            remaining += addition
            output['rounding_adjustments'].append({'category': code, 'amount': addition})
            if not remaining:
                break
    output.update(cuts=cuts, remaining_shortfall=remaining)
    if remaining < 0:
        return {**output, 'status': 'needs_upper_allocation_review', 'reason': 'rounding_remainder_unallocatable',
                'remaining_shortfall': 0, 'unallocated_amount': -remaining}
    if remaining:
        output['ml_floor_limited'] = any(c not in effective['locks'] and c not in user_protected
            and cut_floors[c] == ml_floors[c] and amounts[c] <= ml_floors[c] for c in ml_floors)
        output['system_floor_limited'] = any(
            c not in effective['locks'] and c not in user_protected
            and cut_floors[c] > effective['minimums'].get(c, 0)
            and amounts[c] <= cut_floors[c] for c in floors if c in active)
        return {**output, 'status': 'needs_upper_allocation_review', 'reason': 'shortfall_exceeds_cut_limits'}
    result = {**baseline['result'], 'allocations': amounts, 'reasons': [], 'unallocated': 0}
    check_result_with_unallocated(effective, result, floors=cut_floors)
    if any(amounts[c] < cut_floors[c] for c in floors if c in active and c not in effective['locks']):
        raise ValueError('system_floor_violated')
    if any(amounts[c] != start[c] for c in user_protected):
        raise ValueError('user_protection_violated')
    from budgets.design_explain import explain_allocations
    ledger = _baseline_ledger(baseline['history'], active, effective.get('category_targets'))
    averages = {**averages, **effective.get('category_targets', {})}
    def record(c, code, amount, **detail):
        ledger[c].append({'code': code, 'amount': amount, 'detail': detail})
    for c in CATEGORY_CODES:
        if c not in active:
            continue
        if c in effective['locks']:
            record(c, 'lock_fixed', start[c] - averages[c], locked=start[c])
        else:
            minimum_start = max(averages[c], effective['minimums'].get(c, 0))
            if minimum_start > averages[c]:
                record(c, 'user_minimum_raise', minimum_start - averages[c], minimum=effective['minimums'][c])
            if raw_start[c] > minimum_start:
                record(c, 'system_floor_raise', raw_start[c] - minimum_start, floor=floors[c])
            if start[c] != raw_start[c]:
                record(c, 'rounding', start[c] - raw_start[c], unit=1000, stage='history_start')
        if c in user_protected:
            record(c, 'user_protected', 0)
        if c not in effective['locks'] and c not in user_protected:
            rate_capacity = int(start[c] * Fraction(str(shortfall_cut_rates[c])) / 100)
            minimum = effective['minimums'].get(c, 0)
            if minimum and start[c] - minimum < rate_capacity and raw_start[c] == averages[c]:
                record(c, 'user_minimum_raise', 0, minimum=minimum, protected=True)
            without_floor = min(rate_capacity, start[c] - minimum)
            system_capacity = min(rate_capacity, start[c] - max(minimum, min(floors.get(c, 0), start[c])))
            if system_capacity < without_floor:
                record(c, 'system_floor_protected', 0, floor=floors[c],
                       cut_floor=cut_floors[c], capacity_reduction=without_floor - system_capacity)
            if capacities[c] < system_capacity:
                record(c, 'cut_limit_reached', 0, constraint='ml_reference_floor',
                       floor=ml_floors[c], capacity_reduction=system_capacity-capacities[c])
        if c in exhausted:
            record(c, 'cut_limit_reached', 0, **exhausted[c])
    for cut in cuts:
        c = cut['category']
        rounding_cut = sum(r['amount'] for r in output['rounding_adjustments'] if r['category'] == c and r['amount'] < 0)
        ordinary_cut = -cut['amount'] - rounding_cut
        if ordinary_cut:
            record(c, 'shortfall_' + {'unranked': 'unranked', 'low': 'low_rank', 'top': 'top_rank'}[cut['tier']] + '_cut',
                   ordinary_cut, rank=cut['rank'], tier=cut['tier'],
                   shortfall=max(0, sum(start.values()) - effective['target']),
                   exhausted_categories=cut['exhausted_categories'], reducible_before=cut['reducible_before'], increased=increased is not None and c in increased)
    for rounding in output['rounding_adjustments']:
        record(rounding['category'], 'rounding', rounding['amount'], unit=1000, stage='residual')
    category_explanations = explain_allocations(references, amounts, ledger, reference_type='history_average',
                                               priorities=effective['priorities'], names=CATEGORY_NAMES)
    result['reasons'] = [row['text'] for row in category_explanations if row['text']]
    return {**output, 'status': 'proposed', 'result': result, 'allocations': amounts,
            'explanations': category_explanations, 'unallocated': 0}

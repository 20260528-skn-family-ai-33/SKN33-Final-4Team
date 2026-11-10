"""Stage 3 server tools. Replay structured requests; never accept requested money.

No LLM/DB calls. Policy values must be supplied explicitly by the experiment.
Decrease rates are explicit policy inputs; no production default is supplied.
"""
from copy import deepcopy
from fractions import Fraction
from math import ceil, isfinite

from plan_b_budget import build_history_budget


def _totals(transfers, codes):
    received, donated = dict.fromkeys(codes, 0), dict.fromkeys(codes, 0)
    for transfer in transfers:
        received[transfer['to']] += transfer['amount']
        donated[transfer['from']] += transfer['amount']
    return received, donated


def _remaining(policy, base, amounts, minimums, transfers, targets, reduced):
    received, donated = _totals(transfers, base)
    remaining = deepcopy(policy)
    remaining['total_limit'] -= sum(t['amount'] for t in transfers)
    for code in targets:
        remaining['increase_limits'][code] = (0 if code in reduced else
                                              max(0, targets[code] - received[code]))
    for code in base:
        remaining['decrease_limits'][code] = max(0, min(
            policy['decrease_limits'][code] - donated[code],
            base[code] - minimums.get(code, 0) - donated[code],
            min(base[code], amounts[code]) - minimums.get(code, 0)))
    return remaining


def _validate(baseline, amounts, policy, transfers, targets, reduced, reduction_targets):
    from plan_b_budget import check_result_with_unallocated

    check_result_with_unallocated(baseline['effective_input'], {**baseline['result'], 'allocations': amounts})
    base = baseline['result']['allocations']
    received, donated = _totals(transfers, base)
    if (any(type(t['amount']) is not int or t['amount'] <= 0 or t['amount'] % 1000 for t in transfers)
            or sum(t['amount'] for t in transfers) > policy['total_limit']):
        raise ValueError('invalid_transfer_ledger')
    for code in base:
        if amounts[code] != base[code] + received[code] - donated[code]:
            raise ValueError('transfer_ledger_mismatch')
        if donated[code] > policy['decrease_limits'][code] or received[code] > targets.get(code, 0):
            raise ValueError('adjustment_limit_exceeded')
        if donated[code] > base[code] - baseline['effective_input']['minimums'].get(code, 0):
            raise ValueError('increased_money_reused_as_funding')
        if received[code] and amounts[code] > max(
                policy['ceilings'][code], baseline['effective_input']['minimums'].get(code, 0)):
            raise ValueError('increase_ceiling_exceeded')
    for code in reduced:
        if amounts[code] >= baseline['history']['categories'][code]['average']:
            raise ValueError('reduction_not_below_history')
        if amounts[code] > reduction_targets[code]:
            raise ValueError('reduction_target_not_met')


def _decrease(code, baseline, policy, amounts, transfers, targets, reduced, order, reduction_target):
    from budgets.design_candidates import DISCRETIONARY, _adjust

    index = baseline['history']['overspending_index']
    if index['status'] != 'available':
        return None, [], 'index_unavailable'
    if not index['review_reductions']:
        return None, [], 'index_below_review'
    if code not in baseline['active_categories']:
        return None, [], 'inactive_category'
    inp = baseline['effective_input']
    if code in inp['locks']:
        return None, [], 'locked_category'
    if code in order[:3] and (code not in DISCRETIONARY or code == order[0]):
        return None, [], 'protected_priority'
    average = baseline['history']['categories'][code]['average']
    if amounts[code] <= reduction_target and amounts[code] < average:
        return amounts.copy(), [], 'already_at_reduction_target'
    if average <= 0 or reduction_target < inp['minimums'].get(code, 0):
        return None, [], 'minimum_prevents_reduction'
    needed = ceil((amounts[code] - reduction_target) / 1000) * 1000
    remaining = _remaining(policy, baseline['result']['allocations'], amounts,
                           inp['minimums'], transfers, targets, reduced)
    if remaining['decrease_limits'][code] < needed:
        return None, [], 'decrease_limit_or_floor'
    if remaining['total_limit'] < needed:
        return None, [], 'total_limit'

    trial, moves = amounts.copy(), []
    # Keep A's donor ordering: a selected donor cannot jump ahead of available donors.
    for receiver in order[:3]:
        if receiver == code or receiver in reduced:
            continue
        available = _remaining(policy, baseline['result']['allocations'], trial,
                               inp['minimums'], transfers + moves, targets, reduced)
        available['total_limit'] = min(available['total_limit'], needed)
        changed, proposed, _, _ = _adjust(
            inp, trial, order, baseline['active_categories'], available,
            baseline['draft_amounts'], enabled_receivers=[receiver])
        if any(t['from'] != code for t in proposed):
            continue
        trial, moves = changed, moves + proposed
        needed -= sum(t['amount'] for t in proposed)
        if needed == 0:
            return trial, moves, 'below_history'
    # No partial reduction: insufficient recipient capacity/order rolls back this action.
    return None, [], 'donor_order_or_receiver_capacity'


def apply_history_adjustments(inp, months, *, as_of, policy, decisions,
                              other_reserved=None, unclassified_share_limit=None):
    """Replay from baseline so requests cannot reset cumulative limits.

    increase/keep visit active unlocked top-three categories in rank order.
    decrease may be requested between these visits; its recipients also follow
    rank order and consume the same cumulative increase budgets.
    """
    from budgets.design_candidates import _adjust, _policy, _priority_order
    from budgets.recommend import CATEGORY_CODES

    if not isinstance(decisions, list):
        raise ValueError('invalid_adjustment_decisions')
    for decision in decisions:
        if (not isinstance(decision, dict) or set(decision) != {'category', 'action'}
                or decision['category'] not in CATEGORY_CODES
                or decision['action'] not in ('increase', 'decrease', 'keep')):
            raise ValueError('invalid_adjustment_decision')
    baseline = build_history_budget(inp, months, as_of=as_of, other_reserved=other_reserved,
                                    unclassified_share_limit=unclassified_share_limit)
    if baseline['status'] != 'proposed':
        return {**baseline, 'adjustment_status': 'unavailable', 'transfers': [], 'actions': []}
    if baseline.get('surplus_policy') == 'leave_as_free' and not baseline.get('settings_require_shortfall'):
        return {**baseline, 'adjustment_status': 'surplus_retained', 'transfers': [], 'actions': []}
    if not isinstance(policy, dict):
        raise ValueError('explicit_adjustment_policy_required')
    policy = deepcopy(policy)
    rates = policy.pop('reduction_rates', None)
    requested = {d['category'] for d in decisions if d['action'] == 'decrease'}
    if (not isinstance(rates, dict) or set(rates) - set(CATEGORY_CODES) or requested - set(rates)
            or any(type(v) not in (int, float) or not isfinite(v) or not 0 < v <= 100
                   for v in rates.values())):
        raise ValueError('explicit_valid_reduction_rates_required')
    reduction_targets = {c: baseline['history']['categories'][c]['average']
                         * (1 - Fraction(str(rate)) / 100) for c, rate in rates.items()}
    effective = baseline['effective_input']
    order = _priority_order(effective['priorities'])
    pending = [c for c in order[:3] if c in baseline['active_categories'] and c not in effective['locks']]
    _policy(policy, pending)
    targets = {c: min(policy['increase_limits'][c], int(
        baseline['draft_amounts'][c] * Fraction(str(policy['increase_rates'][c])) / 100 / 1000
        + Fraction(1, 2)) * 1000) // 1000 * 1000 for c in pending}
    base = baseline['result']['allocations']
    amounts, transfers, actions, reduced = base.copy(), [], [], set()
    for decision in decisions:
        code, action = decision['category'], decision['action']
        if action == 'decrease':
            changed, moves, reason = _decrease(
                code, baseline, policy, amounts, transfers, targets, reduced, order, reduction_targets[code])
            if changed is not None:
                amounts, transfers = changed, transfers + moves
                reduced.add(code)
            status = 'unavailable' if changed is None else 'applied' if moves else 'satisfied'
        else:
            if not pending or code != pending[0]:
                raise ValueError('priority_order_required')
            pending.pop(0)
            moves = []
            if action == 'keep':
                status, reason = 'kept', 'no_change'
            elif code in reduced:
                status, reason = 'unavailable', 'reduction_protected'
            else:
                remaining = _remaining(policy, base, amounts, effective['minimums'],
                                       transfers, targets, reduced)
                amounts, moves, stops, _ = _adjust(
                    effective, amounts, order, baseline['active_categories'], remaining,
                    baseline['draft_amounts'], enabled_receivers=[code])
                transfers += moves
                status = 'applied' if moves else 'unavailable'
                reason = stops.get(code, 'no_permitted_transfer')
        actions.append({**decision, 'status': status, 'reason': reason, 'transfers': moves})
        _validate(baseline, amounts, policy, transfers, targets, reduced, reduction_targets)
    _validate(baseline, amounts, policy, transfers, targets, reduced, reduction_targets)
    return {**baseline, 'result': {**baseline['result'], 'allocations': amounts},
            'base_allocations': base, 'adjustment_status': 'checked', 'actions': actions,
            'transfers': transfers, 'reduced_categories': [c for c in CATEGORY_CODES if c in reduced],
            'pending_categories': pending,
            'reduction_targets': reduction_targets,
            'remaining_transfer_limit': policy['total_limit'] - sum(t['amount'] for t in transfers)}


def preview_history_adjustment(inp, months, *, as_of, policy, decisions, request,
                               other_reserved=None, unclassified_share_limit=None):
    """Return a complete replay with one additional request; do not persist it."""
    if not isinstance(decisions, list):
        raise ValueError('invalid_adjustment_decisions')
    return apply_history_adjustments(inp, months, as_of=as_of, policy=policy,
                                     decisions=decisions + [request], other_reserved=other_reserved,
                                     unclassified_share_limit=unclassified_share_limit)

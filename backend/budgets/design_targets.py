"""Soft recurring category starts, separate from user minima and locks."""
from .recommend import ALLOCATION_CODES


def conflict(code, requested, possible, reason):
    return {'type': 'target_conflict', 'category': code, 'requested_amount': requested,
            'categories': [code] if code else [], 'targets': {code: requested} if code else {},
            'possible_amount': possible, 'diff': None if possible is None else possible-requested, 'reason': reason}


def targeted_etc(targets, minimums, locks, living):
    current = locks.get('ETC', minimums.get('ETC', 0))
    if (not isinstance(targets, dict) or set(targets)-set((*ALLOCATION_CODES, 'ETC'))
            or any(type(v) is not int or v < 0 for v in targets.values())):
        return current, {}, [conflict(None, 0, None, 'invalid_target')]
    active = {c: v for c, v in targets.items() if c != 'ETC'}
    if 'ETC' not in targets:
        return current, active, []
    value = targets['ETC']
    reason, possible = None, value
    if 'ETC' in locks and value != locks['ETC']:
        reason, possible = 'locked_amount', locks['ETC']
    elif value < minimums.get('ETC', 0):
        reason, possible = 'minimum_amount', (minimums['ETC']+4999)//5000*5000
    elif 'ETC' not in locks and value % 5000:
        reason, possible = 'amount_unit', value//5000*5000
    elif type(living) is not int or value > living:
        reason, possible = 'living_budget', max(0, living or 0)//5000*5000
    return (current, active, [conflict('ETC', value, possible, reason)]) if reason else (value, active, [])


def target_conflicts(inp, floors):
    targets = inp.get('category_targets', {})
    if (not isinstance(targets, dict) or set(targets)-set(ALLOCATION_CODES)
            or any(type(v) is not int or v < 0 for v in targets.values())):
        return [conflict(None, 0, None, 'invalid_target')]
    result = list(inp.get('category_target_conflicts', []))
    for c, amount in targets.items():
        floor = max(inp['minimums'].get(c, 0), floors.get(c, 0))
        if c in inp['locks'] and amount != inp['locks'][c]:
            result.append(conflict(c, amount, inp['locks'][c], 'locked_amount'))
        elif c not in inp['locks'] and amount < floor:
            result.append(conflict(c, amount, (floor+4999)//5000*5000, 'minimum_or_floor'))
        elif c not in inp['locks'] and amount % 5000:
            result.append(conflict(c, amount, max(amount//5000*5000, (floor+4999)//5000*5000), 'amount_unit'))
    required = sum(inp['locks'].get(c, targets.get(c, max(inp['minimums'].get(c, 0), floors.get(c, 0))))
                   for c in ALLOCATION_CODES)
    if not result and targets and required > inp['target']:
        requested = sum(targets.values())
        result.append(conflict(None, requested, max(0, requested-required+inp['target'])//5000*5000, 'living_budget'))
    return result


def final_target_changes(targets, amounts, before_rounding):
    return [conflict(c, requested, amounts.get(c, 0),
                    'amount_unit' if before_rounding.get(c) == requested else 'living_budget_and_protections')
            for c, requested in targets.items() if requested != amounts.get(c, 0)]

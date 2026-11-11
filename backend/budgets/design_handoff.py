"""Server-only handoff contract; does not save, start chat, or change budgets/settings."""
from copy import deepcopy

from .recommend import ENGINE_VERSION, POLICY_VERSION


CONFLICT_TYPES = frozenset({'minimums_over_living', 'lock_below_minimum', 'locks_mismatch_living'})
TARGET_REASONS = frozenset({'locked_amount', 'minimum_amount', 'minimum_or_floor', 'amount_unit', 'living_budget'})
FUNDING_REASONS = frozenset({'system_floor_unaffordable', 'ml_floor_unaffordable',
                           'shortfall_exceeds_cut_limits', 'budget_unit_unaffordable'})
CONFLICT_FIELDS = ('type', 'category', 'categories', 'shortfall', 'difference', 'locked_amount',
                   'min_amount', 'requested_amount', 'possible_amount', 'targets', 'diff', 'reason')
ATTEMPT_FIELDS = ('funding_required', 'funding_shortfall', 'spare_transfer', 'remaining_shortfall')


def _issue(result):
    if not isinstance(result, dict) or not isinstance(result.get('status'), str):
        raise ValueError('invalid_handoff_result')
    status = result['status']
    detail = result.get('result') or result
    if not isinstance(detail, dict):
        raise ValueError('invalid_handoff_result')
    conflicts = detail.get('conflicts', [])
    if not isinstance(conflicts, list) or any(not isinstance(c, dict) for c in conflicts):
        raise ValueError('invalid_handoff_result')
    reason = result.get('reason')
    if status == 'infeasible':
        if not conflicts or any(c.get('type') not in CONFLICT_TYPES for c in conflicts):
            return None
        kind = 'settings_conflict'
        reason = reason or conflicts[0]['type']
    elif status == 'target_conflict':
        if not conflicts or any(c.get('type') != 'target_conflict' or c.get('reason') not in TARGET_REASONS
                                for c in conflicts):
            return None
        kind = 'target_conflict'
        reason = reason or 'category_target_conflict'
    elif status == 'needs_upper_allocation_review' and reason in FUNDING_REASONS | {'budget_unit_conflict'}:
        kind = 'funding_shortfall' if reason in FUNDING_REASONS else 'amount_unit_conflict'
    else:
        return None

    # Only explicit amounts: never infer a shortage from a Korean summary or sum overlapping conflicts.
    shortfall = None
    if kind == 'funding_shortfall':
        for field in ('funding_shortfall', 'remaining_shortfall', 'shortfall'):
            if result.get(field) is not None:
                shortfall = result[field]
                break
    elif status == 'infeasible' and len(conflicts) == 1:
        c = conflicts[0]
        if c['type'] == 'minimums_over_living':
            shortfall = c.get('shortfall')
        elif c['type'] == 'locks_mismatch_living' and c.get('difference', 0) > 0:
            shortfall = c['difference']
    if shortfall is not None and (type(shortfall) is not int or shortfall < 0):
        raise ValueError('invalid_handoff_shortfall')
    return {'kind': kind, 'status': status, 'reason': reason, 'shortfall': shortfall,
            'details': {k: deepcopy(result[k]) for k in
                        ('category', 'categories', 'system_floors', 'floor_ignored_locked') if k in result},
            'conflicts': [{k: deepcopy(c[k]) for k in CONFLICT_FIELDS if k in c} for c in conflicts]}


def build_design_handoff(task, cycle, snapshot, result, *, available_spare):
    """Build private task metadata, or None when adjustment is not the remedy.

    Call only with authoritative server inputs. run_recommend persists private task metadata;
    design_recovery rechecks ownership/freshness, while public task responses omit this payload.
    """
    issue = _issue(result)
    if issue is None:
        return None
    if task.task_type != 'budget_recommend' or task.user_id != cycle.user_id:
        raise ValueError('invalid_handoff_source')
    if not task.input_hash or str(snapshot['cycle']['id']) != str(cycle.pk):
        raise ValueError('invalid_handoff_source')
    saved, engine = snapshot['saved'], snapshot['engine_input']
    if any(type(v) is not int or v < 0 for v in (available_spare, snapshot['etc'],
            saved['living_budget'], saved['saving_floor'], saved['cushion_monthly'])):
        raise ValueError('invalid_handoff_budget')
    if type(engine['target']) is not int or engine['target'] != saved['living_budget'] - snapshot['etc']:
        raise ValueError('invalid_handoff_budget')
    attempts = {k: result[k] for k in ATTEMPT_FIELDS if result.get(k) is not None}
    if any(type(v) is not int or v < 0 for v in attempts.values()):
        raise ValueError('invalid_handoff_attempt')
    meta = result.get('_design') or {}
    return {
        'schema_version': 'budget-design-handoff-v1', 'source_task_id': str(task.pk),
        'cycle_id': str(cycle.pk), 'input_hash': task.input_hash,
        'versions': {'input': engine.get('input_version'), 'engine': ENGINE_VERSION,
                     'policy': POLICY_VERSION, 'policy_fingerprint': meta.get('policy_fingerprint')},
        'issue': issue,
        'budget': {**deepcopy(saved), 'target': engine['target'], 'other_reserved': snapshot['etc'],
                   'available_spare': available_spare},
        'constraints': deepcopy({k: snapshot[k] for k in ('minimums', 'locks', 'category_targets')}),
        'priorities': deepcopy(engine['priorities']),
        # Missing fields mean not recorded, not that a calculation/transfer never happened.
        'attempt': attempts,
    }

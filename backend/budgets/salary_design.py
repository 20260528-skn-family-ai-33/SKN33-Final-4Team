"""Reconsider only the salary left after explicit requests and chosen savings."""
from copy import deepcopy

from django.utils import timezone

from . import design_service
from .models import Budget, BudgetCycle
from .recommend import ALLOCATION_CODES, EngineCheckFailed, budget_input_snapshot


def _approved_requests(user, context):
    """A later salary review must not undo an earlier explicit category adjustment."""
    versions = {str(row['pk']): row for row in Budget.objects.filter(
        user=user, cycle_id=context['cycle_id']).values(
            'pk', 'base_budget_id', 'approved_at', 'validation_summary')}
    code, seen, protected, resolved = str(context['base_budget_id']), set(), set(), set()
    while code in versions and code not in seen:
        seen.add(code)
        row = versions[code]
        if row['approved_at']:
            meta = (row['validation_summary'] or {}).get('adjustment', {})
            changes = meta.get('approval', {}).get('target_changes', meta.get('target_changes', {}))
            resolved.update(category for category, value in changes.items() if value is None)
            request = meta.get('result', {}).get('requests', {})
            direct = {item['category'] for item in request.get('items', [])
                      if item.get('automatic') is not True and item.get('category') in ALLOCATION_CODES}
            protected.update(direct - resolved)
            resolved.update(direct)
        code = str(row['base_budget_id'])
    return protected


def review_remaining(user, context, targets, explicit, amount, *, excluded=(), clear_targets=(), today=None):
    """Return additional living amounts; never spend old spare or reduce approved budgets."""
    if type(amount) is not int or amount < 0:
        raise ValueError('invalid_salary_remainder')
    result = {'status': 'skipped', 'allocations': {}, 'unallocated': amount,
              'selection': None, 'reason': 'no_salary_remainder'}
    if not amount:
        return result
    today = today or timezone.localdate()
    baseline = {code: targets.get(code, context['allocations'].get(code, 0)) for code in ALLOCATION_CODES}
    if any(type(value) is not int or value < 0 for value in baseline.values()):
        raise ValueError('invalid_salary_category_amount')
    protected = (_approved_requests(user, context) | set(context.get('category_targets', {}))) - set(clear_targets)
    fixed = protected | set(explicit) | set(excluded) | set(context['locks'])
    # A zero allocation stays unused unless the user explicitly asked to fund it.
    fixed.update(code for code, value in baseline.items() if value == 0)
    if 'LIVING' in excluded:
        fixed.update(baseline)
    if set(baseline) <= fixed:
        return {**result, 'reason': 'all_categories_preserved'}
    cycle = BudgetCycle.objects.get(user=user, pk=context['cycle_id'])
    try:
        snapshot = deepcopy(budget_input_snapshot(user, cycle, today=today, card=False))
    except (ValueError, TypeError, KeyError):
        return {**result, 'status': 'needs_review', 'reason': 'salary_design_inputs_unavailable'}
    inp = snapshot['engine_input']
    minimums = {code: max(value, context['minimums'].get(code, 0), inp['minimums'].get(code, 0))
                for code, value in baseline.items()}
    locks = {code: baseline[code] for code in fixed if code in baseline}
    income = context.get('salary_review', {}).get('income_after', context.get('funding_amount'))
    if income is None:
        income = context.get('monthly_income', inp['take_home_income'])
    inp.update(target=sum(baseline.values()) + amount,
               take_home_income=income,
               minimums=minimums, locks=locks,
               category_targets={code: baseline[code] for code in protected | set(explicit) if code in baseline},
               category_target_conflicts=[])
    other = targets.get('ETC', context['allocations'].get('ETC', 0))
    snapshot.update(etc=other, minimums={**minimums, 'ETC': other}, locks={**locks, 'ETC': other},
                    category_targets=deepcopy(inp['category_targets']))
    try:
        out = design_service.recommend(user, snapshot, 0, today,
                                       allow_external=design_service.external_enabled())
        selection = out.get('_design', {}).get('selection')
        if out['status'] != 'proposed':
            return {**result, 'status': 'needs_review', 'selection': selection, 'reason': out.get('reason')}
        if out['_design']['references'].get('ml_reference_status') != 'ready':
            return {**result, 'status': 'needs_review', 'selection': selection, 'reason': 'ml_unavailable'}
        design_service.validate_funding(snapshot, out, 0)
        additions = {code: out['allocations'][code] - baseline[code] for code in baseline
                     if out['allocations'][code] != baseline[code]}
        used = sum(additions.values())
        if (any(value < 0 for value in additions.values()) or used > amount
                or any(code in fixed for code in additions)
                or out['_design']['other_reserved'] != other):
            raise EngineCheckFailed('salary_reallocation_protection_failed')
    except (EngineCheckFailed, ValueError, TypeError, KeyError):
        return {**result, 'status': 'needs_review', 'reason': 'salary_design_validation_failed'}
    return {**result, 'status': 'ready', 'allocations': additions, 'unallocated': amount - used,
            'selection': selection, 'reason': selection['reason']}

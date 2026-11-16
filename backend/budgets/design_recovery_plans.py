"""Verified first-budget recovery plans. No placeholder budget and no implicit setting changes."""
from copy import deepcopy
from datetime import timedelta
from fractions import Fraction
from uuid import uuid4

from django.db import connection
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone

from chat.models import AsyncTask
from finance.api import listed_transactions, transaction_item
from finance.spending import spending_summary
from spending_analysis.coverage import coverage
from spending_analysis.periods import Period
from users.models import FinancialProfile

from . import design_service
from .adjustment_context import AdjustmentContextError, _free_used
from .adjustment_diagnostics import reserved_spare
from .adjustment_service import AdjustmentConflict, AdjustmentExpired, AdjustmentNotReady
from .design_reality import system_floors
from .home import balance_json
from .models import Budget, BudgetCycle, CushionEntry
from .protect import cushion_required, fixed_base
from .recommend import ALLOCATION_CODES, EngineCheckFailed, budget_input_snapshot
from .scope import living_spent


SCHEMA = 'budget-design-recovery-plan-v1'
FINANCIAL_DATA_CHANGED = '최근 거래나 사용 가능한 재원 정보가 바뀌었어요. 변경안을 다시 계산해 주세요.'
SOURCES = {'spare': ('SPARE',), 'savings': ('SPARE', 'SAVINGS'),
           'cushion': ('SPARE', 'CUSHION_MONTHLY'), 'extended': ('SPARE', 'SAVINGS', 'CUSHION_MONTHLY')}
FIELDS = {'LIVING': 'living_budget', 'SAVINGS': 'saving_floor', 'CUSHION_MONTHLY': 'cushion_monthly'}


def _digest(plan):
    return design_service.fingerprint({k: v for k, v in plan.items() if k != 'approval'})


def finances(user, handoff, today):
    from .api import protect_json
    cycle = get_object_or_404(BudgetCycle, pk=handoff['cycle_id'], user=user)
    profile = get_object_or_404(FinancialProfile, user=user)
    state = protect_json(user, profile, today)
    if state['blocked'] or state['base'] is None or state['saved'] is None:
        raise AdjustmentExpired()
    snapshot = budget_input_snapshot(user, cycle, profile=profile, today=today, card=False)
    snapshot['fixed']['card_reserved'] = state['base']['fixed_total'] - fixed_base(snapshot['fixed'])
    snapshot['fixed_total'] = state['base']['fixed_total']
    if state['saved']['spare'] != handoff['budget']['available_spare']:
        raise AdjustmentExpired()
    span = Period(cycle.starts_on, min(today + timedelta(days=1), cycle.ends_before))
    complete = coverage(user, span)['state'] == 'complete'
    rows = listed_transactions(user).filter(occurred_on__gte=span.start, occurred_on__lt=span.end_before)
    summary = spending_summary((transaction_item(tx) for tx in rows), living_only=True)
    try:
        inside, outside, free_revision = _free_used(user, cycle, today)
    except AdjustmentContextError:
        raise AdjustmentExpired() from None
    used = outside + max(inside, max(living_spent(summary) - snapshot['saved']['living_budget'], 0))
    reserved, reserve_revision = reserved_spare(user, cycle, today)
    saving_floor = snapshot['savings_min']
    saving_available = max(snapshot['saved']['saving_floor'] - saving_floor, 0)
    saving_revision = design_service.fingerprint({'amount': snapshot['saved']['saving_floor'], 'minimum': saving_floor})
    balance = balance_json(user)
    required = cushion_required(balance['total'], profile.monthly_take_home_income)
    accrued = CushionEntry.objects.filter(user=user, cycle=cycle, entry_type='allocation').aggregate(n=Sum('amount'))['n'] or 0
    cushion_floor = max(1 if required else 0, accrued)
    cushion_known = complete and balance['total'] is not None and not balance['unknown_count']
    before = {c: snapshot['saved'][k] for c, k in FIELDS.items()}
    before['SPARE'] = state['saved']['spare']
    available = {'SPARE': max(before['SPARE']-used-reserved, 0) if complete and reserved is not None else None,
                 'SAVINGS': saving_available,
                 'CUSHION_MONTHLY': max(before['CUSHION_MONTHLY']-cushion_floor, 0) if cushion_known else None}
    data = {'snapshot': snapshot, 'before': before, 'available': available,
            'floors': {'SAVINGS': saving_floor, 'CUSHION_MONTHLY': cushion_floor},
            'spent': {c: summary['by_category'].get(c, 0) for c in (*ALLOCATION_CODES, 'ETC')},
            'complete': complete, 'saving_reason': None,
            'revisions': [free_revision, reserve_revision, saving_revision],
            'balance': balance['total'], 'unknown_balance': balance['unknown_count']}
    data['fingerprint'] = design_service.fingerprint(data)
    return data


def _floors(snapshot, out=None, *, history=None):
    profile = {'has_vehicle': snapshot['engine_input']['has_vehicle']}
    reference = (out or {}).get('_design', {}).get('references', {}).get('transport_history_reference', {})
    if reference.get('status') == 'ready':
        profile['transport_history'] = Fraction(reference['baseline_numerator'], reference['baseline_denominator'])
    elif history and history.get('status') == 'ready':
        profile['transport_history'] = history['categories']['TRANSPORT_CAR']['baseline_amount']
    return system_floors(profile)


def calculate(user, source, handoff, today, *, scope, funding='spare'):
    if scope not in ('cycle', 'ongoing') or funding not in SOURCES:
        raise ValueError('invalid_recovery_choice')
    data = finances(user, handoff, today)
    original, issue = data['snapshot'], handoff['issue']
    if not data['complete']:
        return {'status': 'needs_data', 'reason': 'spending_unverified'}
    if issue['reason'] == 'lock_below_minimum' or issue['kind'] == 'amount_unit_conflict':
        return {'status': 'infeasible', 'reason': 'settings_need_confirmation'}
    if issue['kind'] == 'target_conflict' and any(c.get('reason') != 'living_budget' for c in issue['conflicts']):
        return {'status': 'infeasible', 'reason': 'settings_need_confirmation'}
    snapshot = deepcopy(original)
    engine = snapshot['engine_input']
    # Already spent money is a calculation floor, never a write to CategoryMinimum.
    for code in ALLOCATION_CODES:
        engine['minimums'][code] = max(engine['minimums'].get(code, 0), data['spent'][code])
        if code in engine['locks'] and engine['locks'][code] < engine['minimums'][code]:
            return {'status': 'infeasible', 'reason': 'locked_below_spent_or_minimum'}
    other = max(snapshot['etc'], data['spent']['ETC'], snapshot['category_targets'].get('ETC', 0))
    if 'ETC' in snapshot['locks'] and snapshot['locks']['ETC'] < other:
        return {'status': 'infeasible', 'reason': 'locked_below_spent_or_minimum'}
    snapshot['etc'] = other
    engine['category_target_conflicts'] = [c for c in engine.get('category_target_conflicts', [])
                                           if c.get('reason') != 'living_budget']
    try:
        history = design_service.summarize_history(design_service.history_for_user(user, today), as_of=today)
    except (ValueError, TypeError, KeyError):
        return {'status': 'needs_data', 'reason': 'history_validation_failed'}
    floor = _floors(snapshot, history=history)
    required = other + sum(engine['locks'].get(c, ((max(engine['minimums'].get(c, 0), floor.get(c, 0),
                        engine.get('category_targets', {}).get(c, 0))+4999)//5000)*5000) for c in ALLOCATION_CODES)
    wanted = max(original['saved']['living_budget'], required)
    attempt = handoff.get('attempt', {})
    extra = attempt.get('funding_required')
    if extra is None and attempt.get('funding_shortfall') is not None:
        extra = attempt['funding_shortfall'] + handoff['budget']['available_spare']
    if extra is None:
        extra = issue['shortfall'] or 0
    wanted = max(wanted, original['saved']['living_budget']+extra)
    if all(c in engine['locks'] for c in ALLOCATION_CODES):
        wanted = other + sum(engine['locks'].values())
    else:
        wanted = ((wanted+4999)//5000)*5000
    for attempt_no in range(2):
        needed = max(wanted-original['saved']['living_budget'], 0)
        transfers, remaining = {}, needed
        for code in SOURCES[funding]:
            if not remaining:
                break
            available = data['available'][code]
            if available is None:
                return {'status': 'needs_data', 'reason': 'funding_unverified'}
            amount = min(remaining, available)
            transfers[code] = amount
            remaining -= amount
        if remaining:
            return {'status': 'infeasible', 'reason': 'funding_insufficient', 'shortfall': remaining}
        snapshot['saved']['living_budget'] = wanted
        for code in ('SAVINGS', 'CUSHION_MONTHLY'):
            snapshot['saved'][FIELDS[code]] = original['saved'][FIELDS[code]]-transfers.get(code, 0)
        engine['target'] = wanted-other
        spare = snapshot['take_home_income']-snapshot['fixed_total']-sum(snapshot['saved'].values())
        try:
            # The A/B engine can use only the remaining confirmed spare, never savings or cushion.
            spare_capacity = (data['available']['SPARE'] or 0) - transfers.get('SPARE', 0)
            out = design_service.recommend(user, snapshot, spare_capacity, today, allow_external=False)
            if out['status'] != 'proposed':
                reason = out.get('reason', 'recalculation_failed')
                shortage = out.get('funding_shortfall')
                if shortage is None:
                    shortage = out.get('shortfall')
                if (out['status'] == 'needs_upper_allocation_review' and reason in
                        ('budget_unit_unaffordable', 'system_floor_unaffordable',
                         'ml_floor_unaffordable', 'shortfall_exceeds_cut_limits')
                        and type(shortage) is int and shortage > 0):
                    selection = out.get('_design', {}).get('selection') or {}
                    # Only B receives confirmed spare during final rounding; A needs a larger target.
                    rounded_with_spare = reason == 'budget_unit_unaffordable' and selection.get('plan') == 'b'
                    net_shortage = rounded_with_spare or out.get('funding_shortfall') is not None
                    if net_shortage and (attempt_no or funding == 'spare'):
                        return {'status': 'infeasible', 'reason': 'funding_insufficient', 'shortfall': shortage}
                    if attempt_no == 0:
                        required_extra = out.get('funding_required')
                        extra = (required_extra if type(required_extra) is int and required_extra >= shortage
                                 else shortage + (spare_capacity if rounded_with_spare else 0))
                        # shortcut: retry once with the reported shortage, revisit if valid plans often need more passes.
                        wanted += ((extra+4999)//5000)*5000
                        continue
                return {'status': out['status'], 'reason': reason}
            used = design_service.validate_funding(snapshot, out, spare_capacity)
            if used:
                transfers['SPARE'] = transfers.get('SPARE', 0) + used
        except EngineCheckFailed:
            return {'status': 'failed', 'reason': 'engine_check_failed'}
        break
    plan = {'schema': SCHEMA, 'status': 'proposed', 'source_task_id': str(source.pk), 'scope': scope,
            'funding': funding, 'financial_fingerprint': data['fingerprint'],
            'before': data['before'], 'transfers': {c: n for c, n in transfers.items() if n},
            'snapshot': snapshot, 'out': out, 'expected_spare': spare,
            'system_floors': _floors(snapshot, out)}
    try:
        verify(data, plan)
    except ValueError:
        return {'status': 'failed', 'reason': 'engine_check_failed'}
    return plan


def verify(data, plan):
    from .api import recommended_budget_lines
    if plan.get('schema') != SCHEMA:
        raise AdjustmentExpired()
    if plan.get('financial_fingerprint') != data['fingerprint']:
        raise AdjustmentExpired(FINANCIAL_DATA_CHANGED)
    snapshot, out, original = plan['snapshot'], plan['out'], data['snapshot']
    if plan['scope'] not in ('cycle', 'ongoing') or plan['funding'] not in SOURCES:
        raise ValueError('invalid_recovery_plan')
    if (snapshot['minimums'] != original['minimums'] or snapshot['locks'] != original['locks']
            or snapshot['engine_input']['locks'] != original['engine_input']['locks']
            or snapshot['engine_input']['priorities'] != original['engine_input']['priorities']
            or snapshot['category_targets'] != original['category_targets'] or snapshot['fixed'] != original['fixed']):
        raise ValueError('recovery_protection_changed')
    if not data['complete']:
        raise AdjustmentExpired()
    if plan['system_floors'] != _floors(snapshot, out):
        raise ValueError('recovery_system_floor_changed')
    transfer = design_service.validate_funding(snapshot, out, plan['expected_spare'])
    allocated = {**out['allocations'], 'ETC': out.get('_design', {}).get('other_reserved', snapshot['etc'])}
    for code, amount in allocated.items():
        if amount < max(original['minimums'].get(code, 0), data['spent'].get(code, 0)):
            raise ValueError('recovery_below_minimum_or_spent')
        if code in original['locks'] and amount != original['locks'][code]:
            raise ValueError('recovery_lock_changed')
        if code not in original['locks'] and amount < plan['system_floors'].get(code, 0):
            raise ValueError('recovery_system_floor_violated')
    saved = snapshot['saved']
    actual = {'SAVINGS': saved['saving_floor'], 'CUSHION_MONTHLY': saved['cushion_monthly'],
              'SPARE': plan['expected_spare']-transfer}
    for code, amount in actual.items():
        decrease = max(data['before'][code]-amount, 0)
        if (amount < data['floors'].get(code, 0) or (decrease and
                (code not in SOURCES[plan['funding']] or data['available'][code] is None or decrease > data['available'][code]))):
            raise ValueError('recovery_funding_limit')
        if decrease != plan['transfers'].get(code, 0):
            raise ValueError('recovery_transfer_mismatch')
    lines = recommended_budget_lines(snapshot, out)
    if snapshot['take_home_income']-sum(row['allocated_amount'] for row in lines) != actual['SPARE']+out.get('unallocated', 0):
        raise ValueError('recovery_total_mismatch')
    return lines


def _verify_history(user, out, today):
    expected = out.get('_design', {}).get('history_fingerprint')
    if expected is not None and design_service.fingerprint(design_service.history_for_user(user, today)) != expected:
        raise AdjustmentExpired(FINANCIAL_DATA_CHANGED)


def save_plan(user, source, handoff, plan, conversation, today):
    from .api import save_recommended_budget
    if not connection.in_atomic_block:
        raise RuntimeError('recovery save requires user lock and transaction')
    data = finances(user, handoff, today)
    verify(data, plan)
    _verify_history(user, plan['out'], today)
    cycle = get_object_or_404(BudgetCycle, pk=handoff['cycle_id'], user=user)
    task = AsyncTask.objects.create(user=user, conversation=conversation, task_type='budget_adjust',
        request_key='design-recovery-'+str(uuid4()), status='succeeded', input_hash=_digest(plan),
        started_at=timezone.now(), completed_at=timezone.now())
    budget = save_recommended_budget(task, cycle, plan['snapshot'], plan['out'], plan['expected_spare'], expire_previous=False)
    budget.validation_summary = {**(budget.validation_summary or {}), 'design_recovery': deepcopy(plan)}
    budget.save(update_fields=['validation_summary'])
    # Replace only this recovery's drafts; other recommendations and approved budgets stay intact.
    Budget.objects.filter(user=user, cycle=cycle, status='validated',
        validation_summary__design_recovery__source_task_id=str(source.pk)).exclude(pk=budget.pk).update(status='expired')
    task.result = {'state': 'awaiting_approval', 'proposal_id': str(budget.pk), 'saved_ref': None}
    task.save(update_fields=['result'])
    return budget


def proposal_card(user, budget, *, conversation=None, today=None):
    from .adjustment_context import adjustment_today
    from .design_recovery import RecoveryExpired, read_design_handoff
    budget = get_object_or_404(Budget.objects.select_related('task'), pk=budget.pk, user=user,
                              task__task_type='budget_adjust')
    if conversation is not None and budget.task.conversation_id != conversation.pk:
        from django.http import Http404
        raise Http404
    plan = (budget.validation_summary or {}).get('design_recovery', {})
    if plan.get('schema') != SCHEMA:
        raise AdjustmentNotReady()
    state = 'saved' if budget.approved_at is not None else budget.status if budget.status != 'validated' else 'awaiting_approval'
    if state == 'awaiting_approval':
        try:
            if budget.input_hash != _digest(plan) or budget.task.input_hash != budget.input_hash:
                raise ValueError('stored_recovery_changed')
            current = adjustment_today(today)
            _, handoff = read_design_handoff(user, plan['source_task_id'], current)
            verify(finances(user, handoff, current), plan)
        except (AdjustmentExpired, RecoveryExpired, ValueError):
            state = 'expired'
    out = plan['out']
    allocations = {**out['allocations'], 'ETC': out.get('_design', {}).get('other_reserved', plan['snapshot']['etc'])}
    after = {'LIVING': sum(allocations.values()), 'SAVINGS': plan['snapshot']['saved']['saving_floor'],
             'CUSHION_MONTHLY': plan['snapshot']['saved']['cushion_monthly'],
             'SPARE': budget.funding_amount-sum(r.allocated_amount for r in budget.items.all())}
    changes = [{'category': c, 'before': plan['before'][c], 'after': n, 'diff': n-plan['before'][c], 'direct': True}
               for c, n in after.items() if n != plan['before'][c]]
    return {'kind': 'funding', 'proposal_id': str(budget.pk), 'task_ref': str(budget.task_id),
            'mode': 'INITIAL_ADJUSTMENT', 'base_budget_id': None, 'source_task_id': plan['source_task_id'],
            'scope': plan['scope'], 'state': state, 'can_approve': state == 'awaiting_approval',
            'allocations': allocations, 'buckets': {c: n for c, n in after.items() if c != 'LIVING'},
            'changes': changes, 'shortfalls': [],
            'transfers': [{'from': c, 'to': 'LIVING', 'amount': n} for c, n in plan['transfers'].items()],
            'target_changes': {}, 'unlock_categories': [], 'target_items': [],
            'persistence_candidates': after, 'persistence_items': [{'category': c, 'amount': after[c]} for c in FIELDS],
            'keep_for_next_cycle': plan.get('approval', {}).get('keep_for_next_cycle', plan['scope'] == 'ongoing'),
            **({'saved_ref': str(budget.pk)} if state == 'saved' else {})}


def approve(user, budget, *, keep_for_next_cycle, key, expected_mode, today):
    from .design_recovery import RecoveryExpired, read_design_handoff
    if not connection.in_atomic_block:
        raise RuntimeError('recovery approval requires user lock and transaction')
    if expected_mode != 'INITIAL_ADJUSTMENT':
        raise AdjustmentNotReady('최초 예산 승인 경로를 사용해 주세요.')
    plan = deepcopy((budget.validation_summary or {}).get('design_recovery', {}))
    if plan.get('schema') != SCHEMA:
        raise AdjustmentNotReady()
    if budget.approved_at is not None:
        if plan.get('approval', {}).get('keep_for_next_cycle') != keep_for_next_cycle:
            raise AdjustmentConflict()
        return proposal_card(user, budget, today=today)
    if budget.status == 'expired':
        raise AdjustmentExpired()
    if budget.status != 'validated' or budget.task.status != 'succeeded':
        raise AdjustmentNotReady()
    try:
        if budget.input_hash != _digest(plan) or budget.task.input_hash != budget.input_hash:
            raise ValueError('stored_recovery_changed')
        source, handoff = read_design_handoff(user, plan['source_task_id'], today, for_update=True)
        data = finances(user, handoff, today)
        expected = verify(data, plan)
        expected_snapshot = deepcopy(plan['snapshot'])
        expected_snapshot.update(deepcopy(plan['out'].get('_design', {}).get('references', {})))
        if budget.input_snapshot != expected_snapshot:
            raise ValueError('stored_snapshot_changed')
        actual = list(budget.items.values('bucket', 'category_id', 'category_detail', 'allocated_amount', 'reserve_id', 'user_id'))
        def line(row):
            return (row['bucket'], row.get('category_id'), row.get('category_detail') or '', row['allocated_amount'])
        if (sorted(map(line, actual), key=str) != sorted(map(line, expected), key=str)
                or any(r['user_id'] != user.pk or r['reserve_id'] is not None for r in actual)
                or budget.funding_amount != data['snapshot']['take_home_income']):
            raise ValueError('stored_recovery_changed')
        _verify_history(user, plan['out'], today)
    except (AdjustmentExpired, RecoveryExpired, ValueError) as error:
        budget.status = 'expired'
        budget.save(update_fields=['status'])
        if isinstance(error, AdjustmentExpired):
            raise
        raise AdjustmentExpired() from None
    if Budget.objects.filter(user=user, approval_key=key).exclude(pk=budget.pk).exists():
        raise AdjustmentConflict()
    if keep_for_next_cycle:
        profile = get_object_or_404(FinancialProfile.objects.select_for_update(), user=user)
        profile.living_budget = sum(r['allocated_amount'] for r in expected if r['bucket'] == 'living')
        profile.savings_floor = plan['snapshot']['saved']['saving_floor']
        profile.cushion_monthly = plan['snapshot']['saved']['cushion_monthly']
        profile.save(update_fields=['living_budget', 'savings_floor', 'cushion_monthly', 'updated_at'])
    Budget.objects.filter(user=user, cycle=budget.cycle, status='approved').update(status='superseded')
    Budget.objects.filter(user=user, cycle=budget.cycle, status__in=('draft', 'validated')).exclude(pk=budget.pk).update(status='expired')
    plan['approval'] = {'keep_for_next_cycle': keep_for_next_cycle}
    budget.validation_summary = {**budget.validation_summary, 'design_recovery': plan}
    budget.status, budget.approved_at, budget.approval_key = 'approved', timezone.now(), key
    budget.save(update_fields=['validation_summary', 'status', 'approved_at', 'approval_key'])
    budget.task.result = {'state': 'saved', 'proposal_id': str(budget.pk), 'saved_ref': str(budget.pk)}
    budget.task.save(update_fields=['result'])
    source.result['_design_handoff']['resolved_budget_id'] = str(budget.pk)
    source.result.pop('handoff', None)
    source.save(update_fields=['result'])
    return proposal_card(user, budget, today=today)

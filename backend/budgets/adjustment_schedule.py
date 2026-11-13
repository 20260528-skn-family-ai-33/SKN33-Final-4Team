"""Next-cycle planning and approved settings activation, without spending future income now."""
from copy import copy, deepcopy
from datetime import date

from django.utils import timezone
from users.models import CategoryMinimum, CategoryPriority

from .models import BudgetAdjustmentSchedule, BudgetCategorySetting

FIELDS = ('monthly_take_home_income', 'living_budget', 'savings_floor', 'cushion_monthly',
          'budget_category_targets', 'payday', 'payday_revision')


def profile_settings(profile):
    return {field: deepcopy(getattr(profile, field)) for field in FIELDS}


def protections(user, cycle_id):
    return {
        'minimums': dict(CategoryMinimum.objects.filter(user=user).values_list('category_id', 'min_amount')),
        'priorities': dict(CategoryPriority.objects.filter(user=user).values_list('category_id', 'rank')),
        'locks': {row.category_id: [row.locked, row.locked_amount, row.revision_no] for row in
                  BudgetCategorySetting.objects.filter(user=user, cycle_id=cycle_id)},
    }


def planning_context(context, planning):
    base = context.get('planning_base', context)
    if not planning:
        return deepcopy(base)
    if planning.get('scope') != 'next_cycle' or not base.get('next_cycle_starts_on'):
        raise ValueError('planning_date_required')
    income = base.get('monthly_income')
    if type(income) is not int or income <= 0:
        raise ValueError('planning_income_required')
    planned = deepcopy(base)
    income_before = income
    change = planning.get('income_change')
    if change:
        income = change['amount'] if change['mode'] == 'to' else income + change['amount'] * (1 if change['direction'] == 'up' else -1)
    if income <= 0:
        raise ValueError('planning_income_invalid')
    fixed = base['fixed_amount'] - base.get('reserved_budget_amount', 0)
    spare = income - fixed - sum(amount for code, amount in base['allocations'].items() if code != 'TRAVEL_LODGING') - sum(base['buckets'][c] for c in ('SAVINGS', 'CUSHION_MONTHLY'))
    # A negative projected remainder must be covered by the requested cuts before approval.
    planned.update(funding_amount=income, monthly_income=income, fixed_amount=fixed, spent={}, spending_status='complete',
                   planning_base=deepcopy(base), planning=deepcopy(planning),
                   effective_on=base['next_cycle_starts_on'], income_before=income_before, required_expenses={}, budget_preview=False)
    planned['buckets']['SPARE'] = spare
    planned['allocations'].pop('TRAVEL_LODGING', None)
    if planning.get('reserve_category') == 'TRAVEL_LODGING' or base.get('reserved_budget_amount'):
        planned['allocations'].setdefault('TRAVEL_LODGING', 0)
        planned.setdefault('category_names', {})['TRAVEL_LODGING'] = '여행·숙박'
        planned['ml_not_applicable'] = list(set(planned.get('ml_not_applicable', ['ETC'])) | {'TRAVEL_LODGING'})
    planned['bucket_available'] = {code: max(amount - planned['bucket_floors'].get(code, 0), 0)
                                   for code, amount in planned['buckets'].items()}
    planned['bucket_status'] = {code: 'ready' for code in planned['buckets']}
    planned['reserve_anchors'] = {code: max(amount, 0) for code, amount in planned['buckets'].items()}
    # Current-cycle spending cannot forecast an unstarted cycle; use verified budget examples.
    planned['diagnostic_inputs'] = {}
    planned.pop('context_hash', None)
    return planned


def schedule_settings(user, profile, budget, context, result, unlock_categories=()):
    before = profile_settings(profile)
    values = {**before, 'monthly_take_home_income': context['monthly_income'],
              'living_budget': sum(amount for code, amount in result['allocations'].items() if code != 'TRAVEL_LODGING'), 'savings_floor': result['buckets']['SAVINGS'],
              'cushion_monthly': result['buckets']['CUSHION_MONTHLY'],
              'budget_category_targets': {code: amount for code, amount in result['allocations'].items() if code not in {'ETC', 'UNCLASSIFIED', 'TRAVEL_LODGING'}}}
    values['one_off'] = bool(context['planning'].get('one_off'))
    values['reserve_amount'] = result['allocations'].get('TRAVEL_LODGING', 0)
    values['reserve_id'] = (budget.validation_summary or {})['adjustment'].get('reserve_id')
    values['expense'] = deepcopy(context['planning'].get('expense'))
    values['unlock_categories'] = list(unlock_categories)
    before['protections'] = protections(user, budget.cycle_id)
    BudgetAdjustmentSchedule.objects.filter(user=user, status='scheduled').update(status='replaced')
    return BudgetAdjustmentSchedule.objects.create(user=user, proposal=budget,
        effective_on=date.fromisoformat(context['effective_on']), settings=values, before_settings=before)


def activate_due_settings(user, profile, today):
    if profile is None:
        return
    rows = BudgetAdjustmentSchedule.objects.select_for_update().filter(user=user, status='scheduled', effective_on__lte=today)
    for row in rows:
        current = {**profile_settings(profile), 'protections': protections(user, row.proposal.cycle_id)}
        if current != row.before_settings:
            row.status = 'needs_review'
            row.save(update_fields=['status'])
            continue
        fields = ('monthly_take_home_income',) if row.settings.get('one_off') else FIELDS
        for field in fields:
            setattr(profile, field, deepcopy(row.settings[field]))
        profile.save(update_fields=[*fields, 'updated_at'])
        row.status, row.applied_at = 'applied', timezone.now()
        row.save(update_fields=['status', 'applied_at'])
        from .salary import expire_salary_drafts
        expire_salary_drafts(user)


def cycle_settings(user, cycle):
    """One-off allocations affect only the selected cycle, never recurring profile ratios."""
    if cycle is None:
        return {}
    row = BudgetAdjustmentSchedule.objects.filter(user=user, status='applied', effective_on=cycle.starts_on).first()
    return row.settings if row and row.settings.get('one_off') else {}


def cycle_profile(user, cycle, profile):
    planned = cycle_settings(user, cycle)
    if not planned:
        return profile
    profile = copy(profile)
    for field in ('living_budget', 'savings_floor', 'cushion_monthly', 'budget_category_targets'):
        setattr(profile, field, deepcopy(planned[field]))
    return profile

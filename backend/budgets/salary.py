from django.db import transaction
from django.db.models import F

from users.models import FinancialProfile
from .models import Budget


def expire_salary_drafts(user):
    Budget.objects.filter(user=user, status__in=["draft", "validated"]).update(status="expired")


def current_salary_profile(user, today):
    """회원 잠금 안에서 예약된 급여일을 적용한다."""
    profile = FinancialProfile.objects.select_for_update().filter(user=user).first()
    if (profile and profile.payday_changes_on and today >= profile.payday_changes_on
            and profile.payday != profile.scheduled_payday):
        profile.payday = profile.scheduled_payday
        profile.save(update_fields=["payday", "updated_at"])
        expire_salary_drafts(user)
        sync_salary_cycle(profile.user, profile, today)
    return profile


def activate_salary_if_due(user, today):
    from .api import lock_user

    pending = FinancialProfile.objects.filter(user=user, payday_changes_on__lte=today).exclude(payday=F("scheduled_payday"))
    if pending.exists():
        with transaction.atomic():
            lock_user(user)
            current_salary_profile(user, today)


def next_payday_on(payday, today):
    from .home import add_month, payday_in

    candidate = payday_in(today.year, today.month, payday)
    if candidate <= today:
        candidate = payday_in(*add_month(today.year, today.month, 1), payday)
    return candidate


def salary_cycle_dates(profile, today):
    from .home import cycle_dates

    if profile.payday_changes_on and today < profile.payday_changes_on:
        start = profile.payday_previous_start
        previous = cycle_dates(profile.payday, start)[2]
        return start, profile.payday_changes_on, previous
    start, end, previous = cycle_dates(profile.payday, today)
    if start == profile.payday_changes_on:
        previous = profile.payday_previous_start
    return start, end, previous


def prepare_salary_loans(user):
    from .due_periods import uses_monthly_dues
    from finance.api_loans import owned_loans, sync_loan_due
    from finance.models import Loan

    if not uses_monthly_dues(user):
        return
    loans = owned_loans(user).filter(status=Loan.Status.REPAYING).select_for_update(of=("self",)).order_by("pk")
    for loan in loans:
        sync_loan_due(loan)


def sync_salary_cycle(user, profile, today):
    from .due_periods import occurrence_cycles
    from .api import day_in_cycle, ensure_current_due
    from .home import ensure_cycle, registered_on
    from finance.models import RecurringExpense

    start, end, _ = salary_cycle_dates(profile, today)
    cycle = ensure_cycle(user, start, end)
    if not profile.payday_revision:
        return cycle
    expenses = RecurringExpense.objects.select_for_update().filter(
        user=user, status="confirmed", payment_card__isnull=True, payment_day__isnull=False,
    ).order_by("pk")
    for expense in expenses:
        for part in occurrence_cycles(user, cycle):
            due_date = day_in_cycle(part, expense.payment_day)
            if due_date is not None and (due_date >= today or registered_on(expense) <= due_date):
                ensure_current_due(expense, part, today, create_past=True, user_pick=True)
    prepare_salary_loans(user)
    return cycle

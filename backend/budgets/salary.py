from datetime import timedelta

from django.db import transaction
from django.db.models import Exists, F, OuterRef

from finance.models import FinancialTransaction
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


def salary_deposits(user, income):
    """월급으로 볼 수 있는 입금: 관리 대상 입출금 계좌로 들어온 실수령액 80% 이상 입금.
    같은 날 같은 금액이 내 다른 계좌에서 나간 입금(내 계좌끼리 옮긴 돈)은 뺀다"""
    moved_from_mine = FinancialTransaction.objects.filter(
        user=user, source_kind="bank", direction="out", is_canceled=False,  # 취소된 출금은 옮긴 돈이 아님
        occurred_on=OuterRef("occurred_on"), amount=OuterRef("amount"),
    ).exclude(account_id=OuterRef("account_id"))
    return (
        FinancialTransaction.objects.filter(
            user=user, source_kind="bank", direction="in", is_canceled=False,
            account__account_type="demand", account__is_selected=True, account__currency="KRW",
            account__connection__disconnected_at__isnull=True,
            amount__gte=-(-income * 8 // 10),   # 80% (올림)
        )
        .exclude(transaction_type="own_transfer")
        .exclude(Exists(moved_from_mine))
        .order_by("occurred_on", "occurred_time", "created_at")
    )


def salary_transaction_ids(user, today):
    """급여 주기마다 월급으로 본 첫 입금 ID 집합 (거래 목록 · 상세 isSalary, 11/4). 급여일 · 실수령액이 없으면 빈 집합
    - 이번 주기: 홈 period_json 과 같은 계산 → received(이번 월급) · early(다음 월급 조기 입금)
    - 지난 주기: 현재 급여일로 거꾸로 나눈 주기마다 같은 규칙(구간 7일 앞당김, 첫 입금 1건). 가장 오래된 월급 후보까지
    [임시] 급여일을 바꾼 회원의 지난 주기는 실제 경계와 다를 수 있다. 사용자가 확인한 월급을 저장하는 방식으로 바꿀 예정"""
    from .home import EARLY_DAYS, add_month, first_deposit, payday_in

    profile = FinancialProfile.objects.filter(user=user).first()
    income = profile.monthly_take_home_income if profile else None
    if not (profile and profile.payday and income and income > 0):
        return set()
    # 홈의 current_salary_profile 처럼 예약된 급여일을 적용하되 저장하지 않는다 (GET 이라서)
    if profile.payday_changes_on and today >= profile.payday_changes_on:
        profile.payday = profile.scheduled_payday
    start, next_payday, _ = salary_cycle_dates(profile, today)

    deposits = salary_deposits(user, income)
    early_days = timedelta(days=EARLY_DAYS)
    current = (first_deposit(deposits, start - early_days, next_payday - early_days - timedelta(days=1), today),
               first_deposit(deposits, next_payday - early_days, today, today))
    ids = {d.pk for d in current if d}

    # 지난 주기: [그 주기 시작 7일 전, 다음 주기 시작 8일 전] 마다 첫 입금 (deposits 는 오래된 순)
    past = list(deposits.filter(occurred_on__lt=start - early_days).values_list("pk", "occurred_on"))
    upper = start
    while past:
        lower = payday_in(*add_month(upper.year, upper.month, -1), profile.payday) - early_days
        in_cycle = [pk for pk, on in past if on >= lower]
        if in_cycle:
            ids.add(in_cycle[0])
        past = [(pk, on) for pk, on in past if on < lower]
        upper = lower + early_days
    return ids

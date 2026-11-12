import re
from datetime import date, timedelta

from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q

from finance.models import FinancialTransaction, PaymentMatch, TransactionLink
from users.models import FinancialProfile
from .models import Budget


def expire_salary_drafts(user):
    Budget.objects.filter(user=user, status__in=["draft", "validated"]).update(status="expired")


def current_salary_profile(user, today):
    """회원 잠금 안에서 예약된 급여일을 적용한다."""
    profile = FinancialProfile.objects.select_for_update().filter(user=user).first()
    from .adjustment_schedule import activate_due_settings
    activate_due_settings(user, profile, today)
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
    from .models import BudgetAdjustmentSchedule
    if pending.exists() or BudgetAdjustmentSchedule.objects.filter(user=user, status='scheduled', effective_on__lte=today).exists():
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


FIRST_SEEN = "first_seen_on"  # loan.field_states: 서비스가 대출을 처음 확인한 날(한국 날짜, YYYY-MM-DD). 한 번 저장하면 바꾸지 않는다


def loan_first_seen_on(loan, today):
    """처음 확인한 날. 없으면 남은 근거 중 가장 이른 날(첫 성공 기관 조회 시작 · 첫 납부 회차 생성, 둘 다 없으면 오늘)로 한 번 저장한다.
    fetched_at 은 조회할 때마다 바뀌어서 쓰지 않는다. 호출 쪽에서 회원·대출 잠금"""
    from django.db.models import Min
    from django.utils import timezone

    states = loan.field_states if isinstance(loan.field_states, dict) else {}
    try:
        return date.fromisoformat(states[FIRST_SEEN])
    except (KeyError, TypeError, ValueError):
        pass
    found = [today]
    for at in (loan.sync_runs.filter(status="success").aggregate(at=Min("started_at"))["at"],
               loan.payment_dues.aggregate(at=Min("created_at"))["at"]):
        if at is not None:
            found.append(timezone.localtime(at).date())
    first = min(found)
    loan.field_states = {**states, FIRST_SEEN: first.isoformat()}
    loan.save(update_fields=["field_states"])
    return first


def prepare_salary_loans(user):
    """이번 주기 대출 납부 회차 준비 (급여일 변경 이력과 무관, R05). 주기 키·월별 키 선택은 sync_loan_due 가 한다"""
    from django.utils import timezone
    from .api import day_in_cycle
    from .due_periods import uses_monthly_dues
    from .models import BudgetCycle
    from finance.api_loans import owned_loans, sync_loan_due
    from finance.models import Loan, PaymentDue

    today = timezone.localdate()
    monthly = uses_monthly_dues(user)
    cycle = BudgetCycle.objects.filter(user=user, status="active", starts_on__lte=today, ends_before__gt=today).first()
    loans = owned_loans(user).filter(status=Loan.Status.REPAYING).select_for_update(of=("self",)).order_by("pk")
    for loan in loans:
        # 처음 확인한 날보다 앞선 납부일(등록 전 의무)은 새로 만들지 않는다. 실제 출금이 있으면 rematch 가 만든다
        # 그 뒤 주기는 납부일이 지났어도 만든다 → 출금이 없으면 sync_loan_due 가 '확인 필요'로 남긴다
        first_seen = loan_first_seen_on(loan, today)
        due_date = day_in_cycle(cycle, loan.payment_day) if cycle and loan.payment_day else None
        if (not monthly and due_date is not None and due_date < first_seen and not PaymentDue.objects.filter(
                loan=loan, due_date__gte=cycle.starts_on, due_date__lt=cycle.ends_before).exists()):
            continue
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


# ---------------- 급여 인식 (R12) ----------------
# 후보: 판정 구간 안의 적격 입금 중 등록 실수령액 80% 이상 + 아직 판단 없음 → '급여 확인 필요' (자동 확정 안 함)
# 인정: SalaryDecision 의 confirmed(사용자) · auto(자동) 만. 홈 수령 상태 · confirmed_income · 거래 isSalary 가 같은 줄을 읽는다
# 자동 인정: 사용자가 기준으로 고른 단일 입금과 같은 계좌 · 같은 금액 · 같은 적요 규칙인 입금이 그 회차에 정확히 1건일 때만

GENERIC_MEMOS = {"입금", "이체", "급여", "월급"}
MONTH_ONLY = re.compile(r"(?:\d{4}년)?\d{1,2}월")
MONTH_PAY = re.compile(r"(?:(\d{4})년)?(\d{1,2})월(월급|급여)")  # '11월 월급' · '2026년 11월 급여' 만 날짜 부분을 무시한다


def memo_key(text):
    """적요 → (자동 인식 비교 키, None) 또는 (None, 기준으로 못 쓰는 이유). 공백 제거 · 대소문자 무시 후 전체 일치"""
    memo = re.sub(r"\s+", "", text or "").casefold()
    if not memo:
        return None, "적요가 비어 있어서 자동 인식 기준으로 쓸 수 없어요"
    if memo in GENERIC_MEMOS:
        return None, "‘입금’ · ‘급여’ 같은 일반 문구라 자동 인식 기준으로 쓸 수 없어요"
    found = MONTH_PAY.fullmatch(memo)
    if found and 1 <= int(found[2]) <= 12:
        return ("month", bool(found[1]), found[3]), None
    if MONTH_ONLY.fullmatch(memo):
        return None, "월 정보만 있어서 자동 인식 기준으로 쓸 수 없어요"
    return ("exact", memo), None


def confirmation_snapshot(tx):
    """사용자가 직접 확인하는 시점에만 저장. 적요는 패턴용 정규화를 하지 않고 원값을 비교한다."""
    return {"snapshot_amount": tx.amount, "snapshot_occurred_on": tx.occurred_on,
            "snapshot_description": tx.source_description, "snapshot_account_id": tx.account_id}


def valid_confirmed_ids(user):
    """읽기 전용: 사용자 확정이고 네 기준값이 현재 거래와 같은 거래 ID queryset.
    상태 전환 전이라도 변경/기준 누락 거래를 인정하지 않는다. NULL 적요와 빈 문자열도 구분한다."""
    from .models import SalaryDecision

    return SalaryDecision.objects.filter(
        Q(snapshot_description=F("transaction__source_description"))
        | Q(snapshot_description__isnull=True, transaction__source_description__isnull=True),
        user=user, transaction__user=user, state="confirmed",
        snapshot_amount__isnull=False, snapshot_occurred_on__isnull=False, snapshot_account_id__isnull=False,
        snapshot_amount=F("transaction__amount"), snapshot_occurred_on=F("transaction__occurred_on"),
        snapshot_account_id=F("transaction__account_id"),
    ).values("transaction_id")


def refresh_confirmed(user):
    """변경·기준 누락인 사용자 확정을 재확인으로. 회원→거래 순서로 잠근 뒤 다시 비교한다."""
    from .api import lock_user
    from .models import SalaryDecision

    decisions = SalaryDecision.objects.filter(user=user, state="confirmed")
    if not decisions.exclude(transaction_id__in=valid_confirmed_ids(user)).exists():
        return
    with transaction.atomic():
        lock_user(user)
        list(FinancialTransaction.objects.select_for_update().filter(
            user=user, pk__in=decisions.values("transaction_id"),
        ).order_by("pk").values_list("pk", flat=True))
        stale = list(decisions.exclude(transaction_id__in=valid_confirmed_ids(user)).values_list("pk", "pay_cycle_on"))
        if not stale:
            return
        SalaryDecision.objects.filter(pk__in=[pk for pk, _ in stale]).update(state="recheck", is_pattern=False)
        # 확정 시각과 기준값은 이력으로 남긴다. 영향 받은 과거 회차도 저장 합계에서 즉시 제외한다.
        for cycle_on in {cycle for _, cycle in stale}:
            store_income(user, cycle_on)


def deposit_pool(user):
    """원화 입출금 계좌 입금 (관리 대상 선택 무관). 취소 · 끊은 연결 · 내 계좌 이체(유형 또는 R11 연결) ·
    납부에 연결된 거래 · 저축 · 상환 · 카드값은 뺀다 (같은 날 같은 금액 출금만으로는 빼지 않음)"""
    own_link = TransactionLink.objects.filter(relation_type="OWN_TRANSFER").filter(
        Q(from_transaction=OuterRef("pk")) | Q(to_transaction=OuterRef("pk")))
    return (
        FinancialTransaction.objects.filter(
            user=user, source_kind="bank", direction="in", is_canceled=False,
            account__account_type="demand", account__currency="KRW",
            account__connection__disconnected_at__isnull=True,
        )
        .exclude(transaction_type__in=["own_transfer", "saving", "loan_repayment", "card_payment"])
        .exclude(Exists(own_link))
        .exclude(Exists(PaymentMatch.objects.filter(transaction=OuterRef("pk"))))
        .order_by("occurred_on", "occurred_time", "created_at")
    )


def eligible_deposits(user):
    """새로 급여로 고를 수 있는 입금 (금액 무관) — 새 후보 · 새 자동 인정은 관리 대상 계좌에서만"""
    return deposit_pool(user).filter(account__is_selected=True)


def known_deposits(user):
    """읽기 전용. 관리 계좌 + 이미 확인/재확인할 입금. 변경된 confirmed는 상태 갱신 전에도 제외한다."""
    return deposit_pool(user).filter(
        Q(account__is_selected=True) | Q(salary_decision__state__in=["confirmed", "recheck"]),
    ).exclude(Q(salary_decision__state="confirmed") & ~Q(pk__in=valid_confirmed_ids(user)))


def salary_windows(profile, today):
    """[(급여 회차 = 주기 시작일, 구간 시작, 구간 끝)] — 이번 주기 월급(월급날 7일 전 ~ 다음 월급날 8일 전)과
    다음 월급 조기 입금(다음 월급날 7일 전 ~ 오늘). 오늘 이후 날짜는 보지 않는다. 조기 입금은 다음 회차에 붙고 주기는 그대로"""
    from .home import EARLY_DAYS

    start, next_payday, _ = salary_cycle_dates(profile, today)
    early = timedelta(days=EARLY_DAYS)
    return [(start, start - early, min(next_payday - early - timedelta(days=1), today)),
            (next_payday, next_payday - early, today)]


def recognized(user, pay_cycle_on):
    """그 회차에 인정된 급여 입금 (사용자 확인 + 자동 인정). 나중에 취소 · 내 계좌 이체로 바뀐 거래는 빠진다"""
    return list(known_deposits(user).filter(
        salary_decision__pay_cycle_on=pay_cycle_on, salary_decision__state__in=["confirmed", "auto"],
    ).select_related("salary_decision"))


def salary_candidates(user, income, low, high):
    """새 후보는 80% 이상. 재확인 거래는 정정 금액이 작아져도 확인 대상으로 남긴다."""
    return known_deposits(user).filter(
        Q(salary_decision__isnull=True, amount__gte=-(-income * 8 // 10)) | Q(salary_decision__state="recheck"),
        occurred_on__range=(low, high),
    )


def refresh_auto(user, windows):
    """자동 인정 다시 맞추기 (회원 잠금 안). 기준은 바꾸지 않는다.
    조건이 맞는 입금이 정확히 1건일 때만 auto. 기준이 없거나 그 회차에 사용자 확인이 있으면 auto 를 지운다.
    이미 auto 였던 거래가 무효(취소 · 이체 등) · 모호(여러 건)해지면 다른 거래로 갈아 붙이지 않고 recheck 로 남긴다 →
    그 회차는 사용자가 확인(그 회차 판단을 바꿈)하거나 판단을 지울 때까지 자동 인정하지 않는다"""
    from .models import SalaryDecision

    refresh_confirmed(user)  # 바뀐 원거래를 자동 인식 기준으로 사용하기 전에 해제한다.
    pool = eligible_deposits(user)
    base = SalaryDecision.objects.filter(user=user, is_pattern=True).select_related("transaction").first()
    base_tx = base.transaction if base and pool.filter(pk=base.transaction_id).exists() else None
    key = memo_key(base_tx.source_description)[0] if base_tx else None
    for cycle_on, low, high in windows:
        autos = SalaryDecision.objects.filter(user=user, pay_cycle_on=cycle_on, state="auto")
        if key is None or low > high or SalaryDecision.objects.filter(
                user=user, pay_cycle_on=cycle_on, state="confirmed").exists():
            autos.delete()
            continue
        if SalaryDecision.objects.filter(user=user, pay_cycle_on=cycle_on, state="recheck").exists():
            continue
        taken = SalaryDecision.objects.filter(transaction=OuterRef("pk")).exclude(state="auto", pay_cycle_on=cycle_on)
        found = [tx for tx in pool.filter(account_id=base_tx.account_id, amount=base_tx.amount, occurred_on__range=(low, high))
                 .exclude(Exists(taken)) if memo_key(tx.source_description)[0] == key]
        if autos.exists():
            if len(found) != 1 or not autos.filter(transaction=found[0]).exists():
                autos.update(state="recheck")
            continue
        if len(found) == 1:
            SalaryDecision.objects.create(transaction=found[0], user=user, state="auto", pay_cycle_on=cycle_on)


def store_income(user, pay_cycle_on):
    """그 회차 주기(budget_cycle)가 있으면 confirmed_income = 인정된 급여 합계 (없으면 null). 등록 실수령액 · 승인 예산은 건드리지 않는다"""
    from .models import BudgetCycle

    total = sum(tx.amount for tx in recognized(user, pay_cycle_on)) or None
    BudgetCycle.objects.filter(user=user, starts_on=pay_cycle_on).exclude(confirmed_income=total).update(confirmed_income=total)


def salary_reviews(user):
    """{거래 ID: 'confirmed' | 'auto'} — 거래 목록 · 상세 isSalary (홈과 같은 저장 근거). 기존 자동 추정은 저장된 판단이 아니라서 없다"""
    refresh_confirmed(user)
    return dict(known_deposits(user).filter(salary_decision__state__in=["confirmed", "auto"])
                .values_list("pk", "salary_decision__state"))

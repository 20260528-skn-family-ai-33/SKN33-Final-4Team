"""
홈 화면 데이터 (GET /api/home/) — 명세: docs/api/home.md

  ① 급여 주기 (period)      설정한 월급날 기준. 주기를 budget_cycle 에 만들고, 월급이 들어왔는지 확인
  ② 계좌 잔액 (balance)     관리 대상 입출금 계좌 잔액 합계
  ③ 알림 줄 수              카테고리를 확인할 거래 수 · 출금일을 확인할 고정지출 수
  ④ 꼭 나갈 돈 (mustPay)    이번 주기 + 지난 주기 미납 납부 예정
  ⑤ 예산 (budget)          이번 주기 승인(approved) 예산이 있을 때만. 계산은 budgets.overview.budget_data 그대로

매일 도는 작업이 없어서 '새 주기 만들기 · 이번 회차 납부 예정 만들기 · 미납 확인 필요로 바꾸기'를 홈을 열 때 한다.
"""
from datetime import timedelta

from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from finance.api import UNCLASSIFIED, listed_transactions
from finance.models import BankAccount, FinancialTransaction, PaymentDue, RecurringExpense

from .api import account_json, day_in_month, lock_user
from .models import BudgetCategorySetting, BudgetCycle

EARLY_DAYS = 7             # 월급날 7일 전부터 들어온 입금은 그 월급으로 본다 (명절 전 지급)
REVIEW_AFTER_DAYS = 3      # 출금일 +3일이 지나도 출금을 못 찾으면 '미납 확인 필요'
SHOW_STATES = ["unpaid", "partial", "needs_review"]


# ---------------- ① 급여 주기 ----------------

def payday_in(year, month, payday):
    """그 달의 월급날 (없는 날이면 말일: 31일 → 9/30)"""
    return day_in_month(year, month, payday)


def add_month(year, month, n):
    month += n
    return year + (month - 1) // 12, (month - 1) % 12 + 1


def cycle_dates(payday, today):
    """(이번 주기 시작일, 다음 월급날, 지난 주기 시작일). 오늘이 이번 달 월급날과 같거나 지났으면 이번 달 월급날이 시작일"""
    year, month = today.year, today.month
    if today < payday_in(year, month, payday):
        year, month = add_month(year, month, -1)
    start = payday_in(year, month, payday)
    next_payday = payday_in(*add_month(year, month, 1), payday)
    previous_start = payday_in(*add_month(year, month, -1), payday)
    return start, next_payday, previous_start


def ensure_previous_cycle(user, previous_start, start):
    """실제 지난 급여 주기를 budget_cycle 에 남긴다 (끝남). 한 달을 건너뛰고 접속하면 그 주기 기록이 없어서 여기서 만든다"""
    previous, _ = BudgetCycle.objects.get_or_create(
        user=user, starts_on=previous_start,
        defaults={"ends_before": start, "expected_payday": start, "status": "closed"},
    )
    return previous


def ensure_cycle(user, start, next_payday):
    """이번 주기를 budget_cycle 에 만든다 (있으면 그대로). 다른 진행 중 주기는 끝남(closed)으로 (호출 쪽에서 회원 잠금)"""
    BudgetCycle.objects.filter(user=user, status="active").exclude(starts_on=start).update(status="closed")
    cycle = BudgetCycle.objects.filter(user=user, starts_on=start).first()
    if cycle is None:
        cycle = BudgetCycle.objects.create(
            user=user, starts_on=start, ends_before=next_payday, expected_payday=next_payday, status="active",
        )
        carry_over_locks(user, cycle)
        return cycle
    changed = []
    for field, value in (("ends_before", next_payday), ("expected_payday", next_payday), ("status", "active")):
        if getattr(cycle, field) != value:
            setattr(cycle, field, value)
            changed.append(field)
    if changed:
        cycle.save(update_fields=changed)
    return cycle


def carry_over_locks(user, cycle):
    """새 주기에 바로 전 주기의 자물쇠(잠긴 카테고리 · 잠긴 금액)를 이어받는다 (기획: 자물쇠는 다음 달에도 적용).

    바꾼 횟수는 '이번 주기' 잔소리 기준이라 0부터 다시 센다. 지난 주기에 푼 카테고리는 가져오지 않는다.
    """
    previous = BudgetCycle.objects.filter(user=user, starts_on__lt=cycle.starts_on).order_by("-starts_on").first()
    if previous is None:
        return
    BudgetCategorySetting.objects.bulk_create([
        BudgetCategorySetting(user=user, cycle=cycle, category_id=setting.category_id,
                              locked=True, locked_amount=setting.locked_amount)
        for setting in BudgetCategorySetting.objects.filter(user=user, cycle=previous, locked=True)
    ])


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


def first_deposit(deposits, start, end, today):
    """start ~ end 사이 첫 입금. 오늘 이후 날짜의 거래는 보지 않는다"""
    return deposits.filter(occurred_on__gte=start, occurred_on__lte=min(end, today)).first()


def period_json(user, profile, today, cycle, start, next_payday):
    """급여 주기 + 월급 상태. 월급 금액은 budget_cycle.confirmed_income 에 남긴다"""
    received = early = None
    income = profile.monthly_take_home_income if profile else None
    if income and income > 0:
        deposits = salary_deposits(user, income)
        # 이번 주기 월급: 이번 월급날 7일 전 ~ 다음 월급날 8일 전
        received = first_deposit(deposits, start - timedelta(days=EARLY_DAYS), next_payday - timedelta(days=EARLY_DAYS + 1), today)
        # 다음 달 월급이 미리 옴: 다음 월급날 7일 전 ~ 오늘
        early = first_deposit(deposits, next_payday - timedelta(days=EARLY_DAYS), today, today)
        state = "received" if received else "waiting"
    else:
        state = "unknown"

    confirmed_income = received.amount if received else None
    if cycle.confirmed_income != confirmed_income:
        cycle.confirmed_income = confirmed_income
        cycle.save(update_fields=["confirmed_income"])

    return {
        "start_date": start.isoformat(),
        "end_date": (next_payday - timedelta(days=1)).isoformat(),
        "next_payday": next_payday.isoformat(),
        "dday": (next_payday - today).days,
        "salary_state": state,
        "salary_received_on": received.occurred_on.isoformat() if received else None,
        "next_salary_early_on": early.occurred_on.isoformat() if early else None,
    }


# ---------------- ② 계좌 잔액 ----------------

def balance_json(user):
    """관리 대상(is_selected) 원화 입출금 계좌, 연결 해제 · 외화 제외. 잔액 모름은 0으로 더하지 않고 unknownCount 로"""
    accounts = list(BankAccount.objects.filter(
        user=user, is_selected=True, account_type="demand", currency="KRW",
        connection__disconnected_at__isnull=True,
    ).select_related("connection"))
    known = [a for a in accounts if a.balance is not None]
    fetched = [a.fetched_at for a in known if a.fetched_at is not None]
    return {
        "total": sum(a.balance for a in known) if known else None,
        "account_count": len(accounts),
        "unknown_count": len(accounts) - len(known),
        "relogin_count": sum(1 for a in accounts if a.connection.status == "relogin"),
        "as_of": timezone.localtime(min(fetched)).isoformat(timespec="seconds") if fetched else None,
    }


# ---------------- ③ 알림 줄 수 ----------------

def pending_count(user, today):
    """거래 목록 ?category=UNCLASSIFIED (기간 없이) 의 needsReviewCount 와 같은 조회"""
    return listed_transactions(user).filter(
        occurred_on__lte=today, transaction_type="spending", confirmed_category_id=UNCLASSIFIED,
    ).count()


def needs_payday_count(user):
    """출금일이 비어 있는 확정 고정지출 수 (계좌 · 카드 · 모름 모두)"""
    return RecurringExpense.objects.filter(user=user, status="confirmed", payment_day__isnull=True).count()


# ---------------- ④ 꼭 나갈 돈 ----------------

def registered_on(expense):
    """고정지출을 확정(등록)한 날 (한국 날짜)"""
    return timezone.localtime(expense.confirmed_at or expense.created_at).date()


def prepare_dues(user, cycle, today, previous_start, previous_cycle):
    """지난 주기 · 이번 주기 회차 납부 예정 만들기 (C-15, budgets/due_match.py) → 출금일 +3일이 지난 안 낸 회차는 '미납 확인 필요'로"""
    from .due_match import match_dues

    match_dues(user, cycle, today, previous_cycle)  # 계좌 회차 만들기 · 출금 연결 + 카드 고정비 연결 (파이프라인과 같은 함수)

    PaymentDue.objects.filter(
        user=user, obligation_state="active", payment_state="unpaid", paid_amount=0,
        due_date__gte=previous_start, due_date__lt=today - timedelta(days=REVIEW_AFTER_DAYS),
    ).exclude(pk__in=PaymentDue.objects.filter(matches__isnull=False).values("pk")).update(
        payment_state="needs_review",
    )


def due_name(due):
    if due.obligation_type == "recurring":
        return due.recurring.name
    if due.obligation_type == "card_bill":
        card = due.bill.card
        return f"{card.name} 청구서" if card and card.name else "카드 청구서"  # 카드사 이름 표가 생기면 "{카드사} 카드 청구서"
    loan = due.loan
    return " ".join(v for v in (loan.lender_name, loan.product_name) if v) or "대출 상환"


def due_source_id(due):
    return {"recurring": due.recurring_id, "card_bill": due.bill_id, "loan": due.loan_id}[due.obligation_type]


def must_pay_json(user, next_payday, previous_start):
    """이번 주기 회차 + 지난 주기에 아직 다 안 낸 회차. 나갈 날 빠른 순 → 남은 금액 큰 순"""
    dues = PaymentDue.objects.filter(
        user=user, obligation_state="active", payment_state__in=SHOW_STATES,
        obligation_type__in=["recurring", "card_bill", "loan"],
        due_date__gte=previous_start, due_date__lt=next_payday,
    ).select_related("recurring", "bill__card", "loan", "payment_account__connection")
    items = []
    for due in dues:
        items.append({
            "due_id": str(due.pk),
            "type": due.obligation_type,
            "source_id": str(due_source_id(due)),
            "name": due_name(due),
            "due_date": due.due_date.isoformat(),
            "amount": max(due.confirmed_amount - due.paid_amount, 0),
            "payment_state": due.payment_state,
            "is_estimated": due.obligation_type == "card_bill" and due.bill.confirmation_state != "confirmed",
            "payment_account": account_json(due.payment_account),
        })
    items.sort(key=lambda item: (item["due_date"], -item["amount"]))
    return items


# ---------------- ⑤ 예산 ----------------

def home_budget(user, today, cycle):
    """이번 주기 approved 예산만 (초안은 홈에 안 보임). 하루 금액 일수는 채팅 budget_left 의 days_left 와 같다"""
    from .models import Budget
    from .overview import budget_data  # overview 가 이 파일을 쓰므로 여기서 불러옴

    approved = Budget.objects.filter(user=user, cycle=cycle, status="approved").first()
    if approved is None:
        return None
    data = budget_data(user, today, selected=approved)
    living = data["living"]
    budget, spent = living["budget"], living["spent"]
    days_left = (cycle.ends_before - today).days - 1  # 오늘까지 쓴 돈을 뺀 뒤 내일부터 나눈다
    return {
        "budget_id": data["budget"]["budget_id"], "version_no": data["budget"]["version_no"],
        "living_budget": budget, "living_left": living["remaining"],
        "living_pct": (spent * 100 + budget // 2) // budget if budget else (100 if spent else 0),  # 반올림, 100 넘어도 그대로
        "spare": data["overspend"]["spare_remaining"],
        "daily": living["remaining"] // days_left if days_left > 0 else None,  # 마지막 날은 null
        "days_left": days_left,
        "out_of_budget": data["out_of_budget"],
        "out_of_budget_allocated": data["budget"]["out_of_budget_allocated"],
    }


# ---------------- 모으기 ----------------

@transaction.atomic
def home_data(user, today):
    from .salary import current_salary_profile, salary_cycle_dates

    lock_user(user)
    profile = current_salary_profile(user, today)
    payday = profile.payday if profile else None

    period = None
    must_pay = []
    if payday:
        start, next_payday, previous_start = salary_cycle_dates(profile, today)
        cycle = ensure_cycle(user, start, next_payday)
        previous_cycle = ensure_previous_cycle(user, previous_start, start)
        prepare_dues(user, cycle, today, previous_start, previous_cycle)
        from .salary import prepare_salary_loans
        prepare_salary_loans(user)
        period = period_json(user, profile, today, cycle, start, next_payday)
        must_pay = must_pay_json(user, next_payday, previous_start)

    return {
        "period": period,
        "balance": balance_json(user),
        "pending_count": pending_count(user, today),
        "needs_payday_count": needs_payday_count(user),
        "must_pay": must_pay,
        "budget": home_budget(user, today, cycle) if payday else None,  # H-06: 승인 예산이 없으면 null
    }

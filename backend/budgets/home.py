"""
홈 화면 데이터 (GET /api/home/) — 명세: docs/api/home.md

  ① 급여 주기 (period)      설정한 월급날 기준. 주기를 budget_cycle 에 만들고, 월급이 들어왔는지 확인
  ② 계좌 잔액 (balance)     관리 대상 입출금 계좌 잔액 합계
  ③ 알림 줄 수              카테고리를 확인할 거래 수 · 출금일을 확인할 고정지출 수
  ④ 꼭 나갈 돈 (mustPay)    이번 주기 + 지난 주기 미납 납부 예정
  ⑤ 예산 (budget)          이번 주기 승인(approved) 예산이 있을 때만. 계산은 budgets.overview.budget_data 그대로

매일 도는 작업이 없어서 '새 주기 만들기 · 이번 회차 납부 예정 만들기 · 카드값 · 대출 납부 다시 맞추기 · 미납 확인 필요로 바꾸기'를 홈을 열 때 한다.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from finance.api import UNCLASSIFIED, listed_transactions, transaction_item
from finance.models import BankAccount, PaymentDue, RecurringExpense

from .api import account_json, day_in_month, lock_user
from .models import BudgetCategorySetting, BudgetCycle
from .scope import OUT_OF_BUDGET

EARLY_DAYS = 7             # 월급날 7일 전부터 들어온 입금은 그 월급으로 본다 (명절 전 지급)
REVIEW_AFTER_DAYS = 3      # 출금일 +3일이 지나도 출금을 못 찾으면 '미납 확인 필요'
SHOW_STATES = ["unpaid", "partial", "needs_review"]
BIG_SPENDING_PCT = 10      # 한 건이 생활비 예산의 이 비율(%) 이상이면 '큰 지출'로 알린다 (돌발 지출 감지 1단계, 11/11 — 팀 확정 전 시작값)


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
    from .models import BudgetAdjustmentSchedule
    planned = BudgetAdjustmentSchedule.objects.filter(user=user, status='applied', effective_on=cycle.starts_on).first()
    unlock = planned.settings.get('unlock_categories', []) if planned else []
    BudgetCategorySetting.objects.bulk_create([
        BudgetCategorySetting(user=user, cycle=cycle, category_id=setting.category_id,
                              locked=True, locked_amount=setting.locked_amount)
        for setting in BudgetCategorySetting.objects.filter(user=user, cycle=previous, locked=True).exclude(category_id__in=unlock)
    ])


def period_json(user, profile, today, cycle, start, next_payday):
    """급여 주기 + 월급 상태 (R12). 인정된 급여(사용자 확인 · 자동 인정)만 받음으로 보고 합계를 budget_cycle.confirmed_income 에 남긴다.
    인정된 급여가 없고 후보만 있으면 needs_check — 후보를 받음으로도, '아직 안 들어옴'으로도 단정하지 않는다"""
    from .salary import recognized, refresh_auto, salary_candidates, salary_windows, store_income

    windows = salary_windows(profile, today)
    refresh_auto(user, windows)
    received, early = recognized(user, start), recognized(user, next_payday)
    income = profile.monthly_take_home_income if profile else None
    checks = 0
    if income and income > 0:
        checks = sum(salary_candidates(user, income, low, high).count()
                     for (cycle_on, low, high), got in zip(windows, (received, early)) if not got)
    state = "received" if received else "needs_check" if checks else "waiting" if income and income > 0 else "unknown"
    store_income(user, cycle.starts_on)

    return {
        "start_date": start.isoformat(),
        "end_date": (next_payday - timedelta(days=1)).isoformat(),
        "next_payday": next_payday.isoformat(),
        "dday": (next_payday - today).days,
        "salary_state": state,
        "salary_source": source(received),
        "salary_amount": sum(tx.amount for tx in received) or None,
        "salary_received_on": received[0].occurred_on.isoformat() if received else None,
        "next_salary_early_on": early[0].occurred_on.isoformat() if early else None,
        "salary_check_count": checks,
    }


def source(txs):
    """user(사용자 확인이 하나라도) · auto(자동 인정만) · null"""
    if not txs:
        return None
    return "user" if any(tx.salary_decision.state == "confirmed" for tx in txs) else "auto"


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


def issuer_connected_on(user):
    """카드사별 첫 연결일 = 그 카드사 카드 중 가장 이른 first_connected_at (첫 청구서 선차감과 같은 기준).
    그 카드사 카드 중 하나라도 연결 시각을 모르면 그 카드사는 넣지 않는다 (모르면 숨기지 않음)"""
    from .protect import provider_cards

    days, unknown = {}, set()
    for card in provider_cards(user):
        if card.first_connected_at is None:
            unknown.add(card.issuer_code)
            continue
        day = timezone.localdate(card.first_connected_at)
        days[card.issuer_code] = min(day, days.get(card.issuer_code, day))
    return {code: day for code, day in days.items() if code not in unknown}


def must_pay_json(user, next_payday, previous_start):
    """이번 주기 회차 + 지난 주기에 아직 다 안 낸 회차. 나갈 날 빠른 순 → 남은 금액 큰 순"""
    dues = PaymentDue.objects.filter(
        user=user, obligation_state="active", payment_state__in=SHOW_STATES,
        obligation_type__in=["recurring", "card_bill", "loan"],
        due_date__gte=previous_start, due_date__lt=next_payday,
    ).exclude(loan__status="repaid").select_related("recurring", "bill__card", "loan", "payment_account__connection")
    # 11/9: 전부 갚았다고 한(완납) 대출의 남은 오늘 · 지난 회차는 숨긴다. 회차는 지우지 않아 [납부 거래 확인]은 그대로, 완납 취소하면 다시 보인다
    from finance.card_payments import bill_status

    today = timezone.localdate()
    connected_on = issuer_connected_on(user)
    items = []
    for due in dues:
        amount, checked = max(due.confirmed_amount - due.paid_amount, 0), False
        if due.obligation_type == "card_bill":
            # 11/06: 사용자가 '전부 냈어요'로 확인한 청구서는 빼고, '일부'면 앞으로 낼 금액으로 (출금 연결액과 더하지 않음)
            check = bill_status(due.bill, due, today)
            if check["status"] == "user_confirmed":
                if check["remaining_amount"] == 0:
                    continue
                amount, checked = check["remaining_amount"], True
            elif due.payment_state != "partial" and due.due_date < connected_on.get(due.bill.issuer_code, due.due_date):
                # 11/09: 카드사를 처음 연결하기 전에 결제일이 지난 청구서는 서비스 밖에서 이미 냈을 수 있어 '납부 확인 필요'로 묻지 않는다.
                #   사용자가 직접 답했거나(위) 출금이 일부 연결된 청구서는 그대로 보여 준다. 납부 예정 행은 지우지 않는다
                continue
        items.append({
            "due_id": str(due.pk),
            "type": due.obligation_type,
            "source_id": str(due_source_id(due)),
            "name": due_name(due),
            "due_date": due.due_date.isoformat(),
            "amount": amount,
            "payment_state": due.payment_state,
            "user_checked": checked,  # 앞으로 낼 금액이 사용자 확인 값 (카드 청구서만)
            "is_estimated": due.obligation_type == "card_bill" and due.bill.confirmation_state != "confirmed",
            "payment_account": account_json(due.payment_account),
        })
    items.sort(key=lambda item: (item["due_date"], -item["amount"]))
    return items


# ---------------- ⑤ 예산 ----------------

def over_categories(categories):
    """쓴 돈이 배정 예산을 넘긴 카테고리 (홈 알림 줄 · 예산 화면 막대 색과 같은 기준). 많이 넘긴 순.
    예산이 0원인 칸은 넘겼다고 보지 않는다 — 예산을 잡지 않은 칸이라"""
    over = [{"category": c["category"], "category_name": c["category_name"], "allocated": c["allocated"],
             "spent": c["spent"], "over": c["spent"] - c["allocated"]}
            for c in categories if 0 < c["allocated"] < c["spent"]]
    return sorted(over, key=lambda c: -c["over"])


def big_spendings(user, cycle, today, living_budget):
    """이번 주기에 한 건이 생활비 예산의 BIG_SPENDING_PCT% 이상인 지출 (홈 알림 줄, 돌발 지출 감지 1단계). 최신순.
    대상: 생활비 쪽 소비(고정지출 연결 · 예산 밖 카테고리 제외) + 남에게 보낸 이체(납부로 연결된 이체 제외).
    내 계좌 간 이체 · 저축 · 카드값 · 대출 상환은 거래 유형이 달라 들어오지 않는다. 알리기만 하고 어떤 숫자도 바꾸지 않는다"""
    threshold = -(-living_budget * BIG_SPENDING_PCT // 100)  # 원 미만 올림
    if threshold <= 0:
        return []
    rows = listed_transactions(user).filter(
        direction="out", transaction_type__in=("spending", "transfer"), amount__gte=threshold,
        occurred_on__gte=cycle.starts_on, occurred_on__lt=cycle.ends_before, occurred_on__lte=today,
    ).order_by("-occurred_on", "-amount")
    found = []
    for tx in rows:
        item = transaction_item(tx)
        is_spending = item["transaction_type"] == "spending"
        if item["net_amount"] < threshold:  # 취소분을 뺀 금액으로 본다
            continue
        if is_spending and (item["funding"] == "fixed" or item["category"] in OUT_OF_BUDGET):
            continue
        if not is_spending and tx.payment_matches.all():
            continue
        found.append({
            "transaction_id": item["transaction_id"], "occurred_on": item["occurred_on"], "merchant_name": item["merchant_name"],
            "amount": item["net_amount"], "kind": item["transaction_type"],
            "category": item["category"], "category_name": item["category_name"],
        })
    return found


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
        "over_categories": over_categories(data["categories"]),  # 예산을 넘긴 카테고리 → 홈 알림 줄
        "big_spendings": big_spendings(user, cycle, today, budget),  # 생활비 예산 대비 큰 지출 → 홈 알림 줄
        "card_notes": data["card_notes"],
        "card_bills_ready": data["card_bills_ready"],  # 그중 지금은 청구서가 나온 카드 (11/12)
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
        from finance.card_payments import refresh_bill_dues
        from finance.pipeline import rematch_payments
        prepare_salary_loans(user)
        refresh_bill_dues(user, today=today)  # 저장된 청구서의 납부 예정 (R04). 예전에 저장한 청구서도 홈을 열면 여기서 채워진다
        rematch_payments(user)
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

"""대출 상환 출금 ↔ 대출 매칭 (pipeline.match_loan_repayments).

적요의 기관 이름 · 연결 계좌 · 매월 납부일 ±3일 · 월 상환액으로 확인하고, 납부 회차와 맞을 때만 loan_repayment 로 바꾼다.
맞지 않으면 유형이 그대로라 사용자가 고칠 수 있다. 대출을 나중에 등록해도 맞도록 후보 표시가 없는 출금도 본다.
한 출금에 맞는 대출이 여럿이거나 한 회차에 맞는 출금이 여럿이면 자동 연결하지 않는다 → 사용자가 '납부 거래 확인'에서 고른다 (11/06)

사용자 확인 (finance/api_loan_payments.py): 고른 출금마다 거래 field_states[LOAN_PAYMENT] 에
  due_id · settlement(full: 이번 회차를 다 냈어요 / partial: 일부만 냈어요 / None: 자동 연결) · source(user · rule)
  · before/after(이 연결이 바꾼 유형 · 카테고리 · 확인 상태 · 출처, 취소할 때 after 그대로일 때만 before 로 되돌림) 을 남긴다.
  대출 회차의 납부 상태는 refresh_loan_due 가 이 값으로 계산한다 (공통 90% 규칙을 쓰지 않음)
"""
from collections import Counter
from datetime import timedelta

from django.utils import timezone

from .institutions import BANK, name_of
from .models import FinancialTransaction as Tx, Loan, PaymentDue, PaymentMatch, TransactionLink
from .transfer_rules import link, squeeze

WINDOW = timedelta(days=3)
LOAN_PAYMENT = "loan_payment"   # 거래 field_states: 대출 회차 연결 기록 (위 설명)
SKIP_AUTO = "skip_auto_match"   # budgets.api.SKIP_AUTO 와 같은 칸: 사용자가 연결을 푼 회차(occurrence_key) → 자동으로 다시 붙이지 않음
CLASS_FIELDS = ("transaction_type", "confirmed_category_id", "confirmation_state", "classification_source")


def near_payment_day(loan, day):
    """앞뒤 달까지 본다: 31일 납부를 다음 달 1일에 낼 수 있어서"""
    from budgets.api import day_in_month

    first = day.replace(day=1)
    months = (first - timedelta(days=1), first, first + timedelta(days=31))
    return any(abs(day_in_month(m.year, m.month, loan.payment_day) - day) <= WINDOW for m in months)


def matching_loans(loans, tx):
    """[임시] 월 상환액이 정확히 같을 때만 (원금균등 · 변동금리처럼 달마다 금액이 바뀌면 안 붙는다). 연결 계좌를 모르면 계좌는 보지 않는다"""
    text = squeeze(tx.source_description)
    return [
        loan for loan in loans
        if loan.monthly_payment == tx.amount and loan.linked_account_id in (None, tx.account_id)
        # 대출 수집기는 짧은 이름표에 없는 은행을 기관 코드로 저장한다
        and squeeze(name_of(BANK, loan.lender_name)) in text and near_payment_day(loan, tx.occurred_on)
    ]


def open_due(loan, day):
    return PaymentDue.objects.filter(
        loan=loan, obligation_state=PaymentDue.ObligationState.ACTIVE, due_date__range=(day - WINDOW, day + WINDOW),
    ).exclude(pk__in=PaymentMatch.objects.filter(due__loan=loan).values("due")).order_by("due_date").first()


def settle(user, loan, due, tx, today):
    from budgets.api import match_withdrawal  # budgets.api 가 finance 를 불러서 여기서 불러온다

    # [임시] 대출 수집기가 거래를 저장하지 않아 연결할 대출 쪽 거래가 없으면 납부 처리만 한다
    loan_row = Tx.objects.filter(
        user=user, source_kind="loan", loan=loan, amount=tx.amount,
        occurred_on__range=(tx.occurred_on - WINDOW, tx.occurred_on + WINDOW),
    ).first()
    if loan_row is not None:
        link(user, tx, loan_row, TransactionLink.Relation.LOAN_REPAYMENT, tx.amount)
    record_link(tx, due, None, "rule", transaction_type=Tx.TransactionType.LOAN_REPAYMENT)
    match_withdrawal(due, tx, today)


def snapshot(tx):
    return {name: getattr(tx, name) for name in CLASS_FIELDS}


def record_link(tx, due, settlement, source, **changes):
    """거래 분류를 바꾸고 연결 기록을 남긴다. 이미 이 회차 기록이 있으면 처음 before 를 유지한다 (자동 → 사용자 확인)"""
    states = dict(tx.field_states or {})
    previous = states.get(LOAN_PAYMENT) if isinstance(states.get(LOAN_PAYMENT), dict) else {}
    before = previous.get("before") if previous.get("due_id") == str(due.pk) else snapshot(tx)
    for name, value in changes.items():
        setattr(tx, name, value)
    states[LOAN_PAYMENT] = {"due_id": str(due.pk), "settlement": settlement, "source": source,
                            "before": before, "after": snapshot(tx)}
    skipped = [key for key in states.get(SKIP_AUTO, []) if key != due.occurrence_key]
    if skipped:
        states[SKIP_AUTO] = skipped
    else:
        states.pop(SKIP_AUTO, None)
    if states == tx.field_states and snapshot(tx) == previous.get("after"):
        return  # 같은 확인을 다시 보냄 → 바꿀 것 없음 (updated_at 도 그대로)
    tx.field_states = states
    tx.save(update_fields=[*CLASS_FIELDS, "field_states", "updated_at"])


def release_plan(tx, due):
    """연결을 풀면 분류를 어떻게 할지 — restore(기록한 원래 분류로) · keep(연결 뒤 사용자가 바꿈 → 지금 값 그대로)
    · choose(원래 분류를 모르는데 지금 대출 상환 → 사용자가 고른다. 추측해서 되돌리지 않음: 예전 자동 연결 등)"""
    record = (tx.field_states or {}).get(LOAN_PAYMENT)
    record = record if isinstance(record, dict) and record.get("due_id") == str(due.pk) else {}
    before = record.get("before")
    if before and snapshot(tx) != record.get("after"):
        return "keep", None
    if before and before.get("transaction_type") != Tx.TransactionType.LOAN_REPAYMENT:
        return "restore", before
    if tx.transaction_type == Tx.TransactionType.LOAN_REPAYMENT:
        return "choose", None
    return "keep", None


def release_link(tx, due, chosen=None):
    """연결 취소: release_plan 대로 분류를 정하고 이 회차에 자동으로 다시 붙지 않게 표시. 실제로 한 일을 돌려준다
    chosen = choose 일 때 사용자가 고른 분류 {CLASS_FIELDS 값} (호출 쪽에서 검사)"""
    kind, before = release_plan(tx, due)
    if kind == "choose" and chosen is None:
        raise ValueError("choose 는 사용자가 고른 분류가 필요해요")
    target = before if kind == "restore" else chosen if kind == "choose" else {}
    for name, value in target.items():
        setattr(tx, name, value)
    states = dict(tx.field_states or {})
    record = states.get(LOAN_PAYMENT)
    if isinstance(record, dict) and record.get("due_id") == str(due.pk) or (record is not None and not isinstance(record, dict)):
        states.pop(LOAN_PAYMENT)  # 다른 회차 기록이면 그대로 둔다
    states[SKIP_AUTO] = sorted(set(states.get(SKIP_AUTO, [])) | {due.occurrence_key})
    tx.field_states = states
    tx.save(update_fields=[*CLASS_FIELDS, "field_states", "updated_at"])
    TransactionLink.objects.filter(from_transaction=tx, relation_type=TransactionLink.Relation.LOAN_REPAYMENT).delete()
    return {"transaction_id": str(tx.pk), "classification": {"restore": "restored", "choose": "chosen"}.get(kind, "kept"),
            "transaction_type": tx.transaction_type,
            "category": tx.confirmed_category_id if tx.transaction_type == Tx.TransactionType.SPENDING else None}


def settlement_of(tx, due):
    record = (tx.field_states or {}).get(LOAN_PAYMENT)
    if isinstance(record, dict) and record.get("due_id") == str(due.pk):
        return record.get("settlement"), record.get("source")
    return None, None


def refresh_loan_due(due, today):
    """대출 회차 납부 상태 — 사용자 확인이 먼저: 하나라도 '다 냈어요'면 paid, '일부만'이면 partial (금액 비율 무관)
    확인 없는 자동 연결은 예정액 이상일 때만 paid. 연결이 없으면 공통 규칙처럼 출금일 전 unpaid · 지남 needs_review"""
    matches = list(due.matches.select_related("transaction"))
    paid = sum(m.matched_amount for m in matches)
    judged = {settlement_of(m.transaction, due)[0] for m in matches}
    if "full" in judged:
        state = "paid"
    elif "partial" in judged:
        state = "partial"
    elif paid > 0:
        state = "paid" if paid >= due.confirmed_amount else "partial"
    else:
        state = "unpaid" if today <= due.due_date else "needs_review"
    due.paid_amount = paid
    due.payment_state = state
    due.matched_at = max((m.matched_at for m in matches), default=None)
    due.save(update_fields=["paid_amount", "payment_state", "matched_at"])


def match(user):
    """아직 납부로 안 붙은 출금을 모두 본다. 카드론은 적요가 카드사 이름이라 card_payment 도 본다 (청구서 매칭이 먼저 돈다).
    [임시] 납부 회차가 없는 과거 상환은 transfer 로 남는다. 사용자가 확정한 거래는 바꾸지 않는다"""
    from .api_loans import sync_loan_due

    loans = list(Loan.objects.filter(
        user=user, status=Loan.Status.REPAYING, monthly_payment__isnull=False,
        payment_day__isnull=False, lender_name__isnull=False,
    ).exclude(lender_name="").order_by("pk"))
    today = timezone.localdate()
    withdrawals = Tx.objects.filter(
        user=user, source_kind="bank", is_canceled=False, amount__in=[loan.monthly_payment for loan in loans],
        transaction_type__in=[Tx.TransactionType.TRANSFER, Tx.TransactionType.CARD_PAYMENT],
    ).exclude(confirmation_state=Tx.ConfirmationState.CONFIRMED).exclude(
        pk__in=PaymentMatch.objects.filter(user=user).values("transaction"),
    )
    synced = set()
    pairs = []
    for tx in withdrawals.order_by("occurred_on", "pk"):
        found = matching_loans(loans, tx)
        if len(found) != 1:
            continue  # 맞는 대출이 여럿이면 첫 번째를 고르지 않는다 (사용자 확인)
        loan = found[0]
        due = open_due(loan, tx.occurred_on)
        if due is None and loan.pk not in synced:
            synced.add(loan.pk)
            sync_loan_due(loan)  # 이번 주기 회차를 아직 안 만들었을 수 있다
            due = open_due(loan, tx.occurred_on)
        if due is not None and due.occurrence_key not in (tx.field_states or {}).get(SKIP_AUTO, []):
            pairs.append((loan, due, tx))
    per_due = Counter(due.pk for _, due, _ in pairs)
    for loan, due, tx in pairs:
        if per_due[due.pk] == 1:  # 한 회차에 맞는 출금이 여럿이면 사용자 확인
            settle(user, loan, due, tx, today)

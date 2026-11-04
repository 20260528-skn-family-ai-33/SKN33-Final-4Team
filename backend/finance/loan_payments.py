"""대출 상환 출금 ↔ 대출 매칭 (pipeline.match_loan_repayments).

후보(transfer_rules 가 표시한 출금)를 연결 계좌 · 매월 납부일 ±3일 · 월 상환액으로 확인하고, 납부 회차와 맞을 때만 loan_repayment 로 바꾼다.
맞지 않으면 transfer 그대로 남아 거래 목록에서 사용자가 고칠 수 있다.
"""
from datetime import timedelta

from django.utils import timezone

from .models import FinancialTransaction as Tx, Loan, PaymentDue, PaymentMatch, TransactionLink
from .transfer_rules import LOAN_CANDIDATE, link, squeeze

WINDOW = timedelta(days=3)


def near_payment_day(loan, day):
    """앞뒤 달까지 본다: 31일 납부를 다음 달 1일에 낼 수 있어서"""
    from budgets.api import day_in_month

    first = day.replace(day=1)
    months = (first - timedelta(days=1), first, first + timedelta(days=31))
    return any(abs(day_in_month(m.year, m.month, loan.payment_day) - day) <= WINDOW for m in months)


def find_loan(user, tx):
    """[임시] 월 상환액이 정확히 같을 때만 (원금균등 · 변동금리처럼 달마다 금액이 바뀌면 안 붙는다). 연결 계좌를 모르면 계좌는 보지 않는다"""
    text = squeeze(tx.source_description)
    loans = Loan.objects.filter(
        user=user, status=Loan.Status.REPAYING, monthly_payment=tx.amount,
        payment_day__isnull=False, lender_name__isnull=False,
    ).order_by("pk")
    return next((
        loan for loan in loans
        if squeeze(loan.lender_name) in text and loan.linked_account_id in (None, tx.account_id)
        and near_payment_day(loan, tx.occurred_on)
    ), None)


def open_due(loan, day):
    return PaymentDue.objects.filter(
        loan=loan, obligation_state=PaymentDue.ObligationState.ACTIVE, due_date__range=(day - WINDOW, day + WINDOW),
    ).exclude(pk__in=PaymentMatch.objects.filter(due__loan=loan).values("due")).order_by("due_date").first()


def match(user):
    """아직 납부로 안 붙은 후보를 모두 본다: 대출 정보를 나중에 고쳐도 다음 처리 때 맞도록"""
    from budgets.api import match_withdrawal  # budgets.api 가 finance 를 불러서 여기서 불러온다
    from .api_loans import sync_loan_due

    today = timezone.localdate()
    candidates = Tx.objects.filter(
        user=user, source_kind="bank", transaction_type=Tx.TransactionType.TRANSFER, is_canceled=False,
        field_states__has_key=LOAN_CANDIDATE,
    ).exclude(pk__in=PaymentMatch.objects.filter(user=user).values("transaction"))
    for tx in candidates.order_by("occurred_on", "pk"):
        loan = find_loan(user, tx)
        if loan is None:
            continue
        due = open_due(loan, tx.occurred_on)
        if due is None:
            sync_loan_due(loan)  # 이번 주기 회차를 아직 안 만들었을 수 있다
            due = open_due(loan, tx.occurred_on)
        if due is None:
            continue
        # [임시] 대출 수집기가 거래를 저장하지 않아 연결할 대출 쪽 거래가 없으면 납부 처리만 한다
        loan_row = Tx.objects.filter(
            user=user, source_kind="loan", loan=loan, amount=tx.amount,
            occurred_on__range=(tx.occurred_on - WINDOW, tx.occurred_on + WINDOW),
        ).first()
        if loan_row is not None:
            link(user, tx, loan_row, TransactionLink.Relation.LOAN_REPAYMENT, tx.amount)
        Tx.objects.filter(pk=tx.pk).update(transaction_type=Tx.TransactionType.LOAN_REPAYMENT)
        match_withdrawal(due, tx, today)

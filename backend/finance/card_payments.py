"""카드대금 출금 ↔ 카드 청구서 매칭 (pipeline.match_card_payments).

10/28 결정: 청구서는 카드사 단위 통합이고 카드론 명세서는 뺀다. 같은 날 사용 명세서가 여러 장이면 합친 금액 한 번으로 낸다.
"""
from datetime import timedelta

from django.db.models import Exists, OuterRef
from django.db.models.functions import Coalesce
from django.utils import timezone

from .card_deduction import usage_statements
from .models import Card, CardBill, FinancialTransaction as Tx, PaymentDue, PaymentMatch
from .transfer_rules import card_issuer

WINDOW = timedelta(days=3)


def unpaid_bills(user, issuer_code, day):
    """출금일(없으면 결제일)이 ±3일 안인 카드사 통합 청구서를 날짜별로. 이미 납부가 붙은 청구서와 낼 돈이 없는 0원 명세서는 뺀다.
    [임시] 청구 월은 따로 비교하지 않는다: 달이 다른 청구서는 날짜에서 걸러진다"""
    bills = CardBill.objects.filter(
        user=user, issuer_code=issuer_code, scope_type=CardBill.ScopeType.ISSUER, total_amount__gt=0,
    ).annotate(
        due_on=Coalesce("withdrawal_due_date", "payment_due_date"),
        paid=Exists(PaymentMatch.objects.filter(due__bill=OuterRef("pk"))),
    ).filter(due_on__range=(day - WINDOW, day + WINDOW), paid=False)
    by_due = {}
    for bill in bills:
        by_due.setdefault(bill.due_on, []).append(bill)
    return by_due


def paying_accounts(user, issuer_code, bills):
    """[임시] 청구서의 결제 계좌, 없으면 그 카드사 카드의 결제 계좌. 수집기가 아직 못 채워 모르면 빈 집합 = 계좌는 보지 않는다"""
    accounts = {bill.payment_account_id for bill in bills} - {None}
    return accounts or set(Card.objects.filter(
        user=user, issuer_code=issuer_code, payment_account__isnull=False,
    ).values_list("payment_account_id", flat=True))


def find_statements(user, tx):
    """이 출금이 낸 명세서들. [임시] 총액이 정확히 같을 때만 (분할 납부는 안 붙는다), 날짜가 가까운 쪽 먼저"""
    issuer_code = card_issuer(tx.source_description)
    if issuer_code is None:
        return []
    by_due = unpaid_bills(user, issuer_code, tx.occurred_on)
    for due_on in sorted(by_due, key=lambda day: abs((day - tx.occurred_on).days)):
        statements, reason = usage_statements(by_due[due_on])
        if reason or not statements or sum(bill.total_amount for bill in statements) != tx.amount:
            continue
        accounts = paying_accounts(user, issuer_code, statements)
        if not accounts or tx.account_id in accounts:
            return statements
    return []


def bill_due(bill, tx):
    """청구서의 납부 예정. [임시] 청구서를 저장할 때 납부 예정을 만드는 곳이 아직 없어서 납부를 찾았을 때 만든다"""
    due, _ = PaymentDue.objects.get_or_create(bill=bill, defaults={
        "user": bill.user, "obligation_type": PaymentDue.ObligationType.CARD_BILL,
        "occurrence_key": f"card_bill:{bill.pk}", "due_date": bill.due_on, "confirmed_amount": bill.total_amount,
        "payment_account": tx.account, "source_type": bill.source_type,
        "confirmation_state": bill.confirmation_state or "unknown",
    })
    return due


def match(user):
    """아직 안 붙은 카드값 출금을 모두 본다: 은행을 먼저 연결하면 청구서가 출금보다 늦게 들어오기 때문"""
    from budgets.api import refresh_due_state  # budgets.api 가 finance 를 불러서 여기서 불러온다

    today = timezone.localdate()
    payments = Tx.objects.filter(
        user=user, source_kind="bank", transaction_type=Tx.TransactionType.CARD_PAYMENT, is_canceled=False,
    ).exclude(pk__in=PaymentMatch.objects.filter(user=user).values("transaction")).select_related("account")
    for tx in payments.order_by("occurred_on", "pk"):
        for bill in find_statements(user, tx):
            due = bill_due(bill, tx)
            PaymentMatch.objects.get_or_create(due=due, transaction=tx, defaults={
                "user": user, "matched_amount": bill.total_amount,
            })
            refresh_due_state(due, today)

"""확정 정기 저축 이체 → saving (⑤, 11/4) — 거래 저장 뒤 처리(finance/pipeline.py match_savings)가 회원 잠금 안에서 부른다.

확정 저축과 보내는 계좌 · 금액 · 이체일 ±3일 · 받는 곳이 모두 맞는 새 출금을 saving 으로 바꾸고 근거 이체에 더한다.
받는 곳: 받는 계좌를 알면 내 계좌 이체 짝(OWN_TRANSFER)의 입금 계좌, 모르면(연결 안 한 계좌) 근거 이체와 같은 적요.
출금 쪽만 바꾼다 — [저축이에요](savings_confirm, S-06)와 같음. 내 계좌 입금 쪽은 own_transfer 그대로.
"""
from collections import defaultdict
from datetime import timedelta

from finance.merchant_rules import normalize_merchant
from finance.models import FinancialTransaction, PaymentMatch, RecurringExpense, RecurringSaving, TransactionLink

from .api import day_in_month, id_set

DAY_GAP = 3
TYPES = ["own_transfer", "transfer"]


def match_savings(user, tx_ids, today):
    """tx_ids: 새로 들어온 거래. 저축마다 한 달 1건 — 같은 달에 맞는 이체가 여러 건이면 아무것도 바꾸지 않는다"""
    new = set(tx_ids)
    savings = list(RecurringSaving.objects.select_for_update().filter(
        user=user, status="confirmed", from_account__isnull=False, transfer_day__isnull=False,
    ).order_by("pk"))  # [임시] 이체일을 모르는 저축은 맞출 날짜가 없어서 대상이 아님
    if not savings or not new:
        return
    taken = set()  # 다른 고정지출 · 저축의 근거 거래
    for ids in RecurringExpense.objects.filter(user=user).values_list("evidence", flat=True):
        taken |= id_set(ids)
    evidence = {s.pk: id_set(s.evidence) for s in savings}
    for s in savings:
        taken |= evidence[s.pk]
    own_to = dict(TransactionLink.objects.filter(
        user=user, relation_type=TransactionLink.Relation.OWN_TRANSFER,
    ).values_list("from_transaction_id", "to_transaction__account_id"))

    for s in savings:
        mine = list(FinancialTransaction.objects.filter(user=user, pk__in=list(evidence[s.pk])))
        labels = {normalize_merchant(t.source_description or t.merchant_name) for t in mine} - {None}
        saved_months = {(t.occurred_on.year, t.occurred_on.month) for t in mine}
        rows = FinancialTransaction.objects.filter(
            user=user, account_id=s.from_account_id, direction="out", is_canceled=False,
            transaction_type__in=TYPES, amount=s.amount, occurred_on__lte=today,
        ).exclude(pk__in=PaymentMatch.objects.values("transaction"))  # 청구서 · 대출 · 고정지출 회차에 연결된 출금
        by_month = defaultdict(list)
        for tx in rows:
            if str(tx.pk) in taken or not on_day(tx, s.transfer_day) or not same_receiver(tx, s, own_to, labels):
                continue
            by_month[(tx.occurred_on.year, tx.occurred_on.month)].append(tx)
        for month, found in by_month.items():
            if month in saved_months or len(found) != 1 or found[0].pk not in new:
                continue  # 이번 달 저축은 이미 셈 · 여러 건이면 고르지 않음 · 예전 거래는 건드리지 않음
            tx = found[0]
            FinancialTransaction.objects.filter(pk=tx.pk, transaction_type__in=TYPES).update(transaction_type="saving")
            s.evidence = [*(s.evidence or []), str(tx.pk)]
            s.save(update_fields=["evidence"])
            taken.add(str(tx.pk))


def on_day(tx, transfer_day):
    """이체일 ±3일. 그 달에 없는 날은 말일 기준 (day_in_month)
    shortcut: 그 거래가 난 달의 이체일만 본다 — 1일 이체가 전달 말일에 나가는 경우는 못 맞춤, 실제로 나오면 앞뒤 달도 보기"""
    due = day_in_month(tx.occurred_on.year, tx.occurred_on.month, transfer_day)
    return abs(tx.occurred_on - due) <= timedelta(days=DAY_GAP)


def same_receiver(tx, saving, own_to, labels):
    if saving.to_account_id is not None:
        return tx.transaction_type == "own_transfer" and own_to.get(tx.pk) == saving.to_account_id
    # 받는 계좌 모름(연결 안 한 계좌): 근거 이체와 같은 적요. 남에게 보낸 이체(transfer)는 이 경우만 대상
    return normalize_merchant(tx.source_description or tx.merchant_name) in labels

"""거래 유형 판단 (pipeline.link_basics).

은행 거래는 출금 transfer · 입금 income 으로 저장되는데, 이 단계가 내 계좌 간 이체 · 카드값 · 대출 상환 후보를 나눈다.
친구 송금은 그대로 transfer 다 (11/3 결정: 기본은 소비 아님). 반대쪽 거래는 전에 저장된 것까지 찾는다 (은행마다 조회 시점이 달라서).
"""
from django.db.models import Exists, OuterRef

from .institutions import CARDS
from .models import FinancialTransaction as Tx, Loan, PaymentMatch, TransactionLink

Type = Tx.TransactionType
Relation = TransactionLink.Relation

# [임시] 기관 표 이름과 다르게 찍히는 적요. 실제 통장 적요를 더 보고 늘린다
CARD_ALIASES = {"0301": ("KB카드",), "0304": ("농협카드",), "0305": ("비씨카드",)}
CARD_WORDS = {
    word.upper(): code
    for code, info in CARDS.items() for word in (info["name"], *CARD_ALIASES.get(code, ()))
}
LINK_STATE = "suggested"  # [임시] transaction_link 상태 코드가 미정이라 '규칙이 정함'과 같은 값을 쓴다
LOAN_CANDIDATE = "loan_repayment_candidate"  # 거래 field_states: 대출 상환 후보. 유형은 실제 납부와 맞았을 때만 바꾼다


def squeeze(text):
    return (text or "").replace(" ", "").upper()


def card_issuer(description):
    """적요 → 카드사 코드 (없으면 None)"""
    text = squeeze(description)
    return next((code for word, code in CARD_WORDS.items() if word in text), None)


def link(user, source, target, relation, amount):
    TransactionLink.objects.get_or_create(
        from_transaction=source, to_transaction=target, relation_type=relation,
        defaults={"user": user, "matched_amount": amount, "confirmation_state": LINK_STATE},
    )


def link_own_transfers(user, txs):
    """[임시] 같은 날 · 같은 금액이면 내 계좌 간 이체로 본다. 자정을 넘긴 이체는 놓치고, 같은 날 우연히 같은 금액을 주고받으면 잘못 묶는다.
    입금이 늦게 와도 결과가 같도록 규칙이 정한 카드값도 다시 본다. 사용자가 확정했거나 납부에 붙은 거래는 바꾸지 않는다"""
    bank = Tx.objects.filter(user=user, source_kind="bank", is_canceled=False).exclude(
        confirmation_state=Tx.ConfirmationState.CONFIRMED,
    ).exclude(Exists(PaymentMatch.objects.filter(transaction=OuterRef("pk"))))
    paired = set()
    pending = bank.filter(pk__in=txs.values("pk"), transaction_type__in=[Type.TRANSFER, Type.INCOME])
    for tx in pending.order_by("occurred_on", "occurred_time", "pk"):
        if tx.pk in paired:
            continue
        is_out = tx.transaction_type == Type.TRANSFER
        other = bank.filter(
            occurred_on=tx.occurred_on, amount=tx.amount,
            transaction_type__in=[Type.INCOME] if is_out else [Type.TRANSFER, Type.CARD_PAYMENT],
        ).exclude(account_id=tx.account_id).order_by("occurred_time", "pk").first()
        if other is None:
            continue
        paired |= {tx.pk, other.pk}
        Tx.objects.filter(pk__in=[tx.pk, other.pk]).update(transaction_type=Type.OWN_TRANSFER)
        out, income = (tx, other) if is_out else (other, tx)
        link(user, out, income, Relation.OWN_TRANSFER, tx.amount)


def mark_by_description(user, txs):
    """남은 출금 중 적요에 카드사 이름 → card_payment, 상환 중인 내 대출의 기관 이름 → 대출 상환 후보 표시.
    후보는 transfer 그대로라 목록에 남아 사용자가 고칠 수 있다 (적요 '국민은행 홍길동'처럼 사람에게 보낸 돈일 수 있어서).
    [임시] 대출보다 거래를 먼저 불러오면 후보가 되지 않는다"""
    lenders = [squeeze(name) for name in Loan.objects.filter(
        user=user, status=Loan.Status.REPAYING, lender_name__isnull=False,
    ).values_list("lender_name", flat=True)]
    for tx in txs.filter(source_kind="bank", transaction_type=Type.TRANSFER, is_canceled=False):
        text = squeeze(tx.source_description)
        if card_issuer(text):
            Tx.objects.filter(pk=tx.pk).update(transaction_type=Type.CARD_PAYMENT)
        elif any(lender and lender in text for lender in lenders):
            Tx.objects.filter(pk=tx.pk).update(field_states={**tx.field_states, LOAN_CANDIDATE: True})


def link_cancellations(user, txs):
    """[임시] 취소가 따로 온 줄을 같은 카드 · 같은 승인번호의 원거래에 잇는다. 은행 거래는 취소 표시가 없어 보지 않는다.
    취소 줄이 먼저 저장될 수 있어서 새 거래의 승인번호마다 양쪽을 다 찾는다"""
    keys = txs.filter(source_kind="card", approval_no__isnull=False).values_list("card_id", "approval_no").distinct()
    for card_id, approval_no in keys:
        rows = Tx.objects.filter(user=user, card_id=card_id, approval_no=approval_no).order_by("-occurred_on", "pk")
        for cancel in rows.filter(is_canceled=True):
            original = rows.filter(is_canceled=False, occurred_on__lte=cancel.occurred_on).first()
            if original is not None:
                link(user, original, cancel, Relation.CANCELLATION, cancel.canceled_amount or cancel.amount)


def apply(user, txs):
    link_cancellations(user, txs)
    link_own_transfers(user, txs)
    mark_by_description(user, txs)

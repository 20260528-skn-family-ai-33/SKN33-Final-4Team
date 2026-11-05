"""거래 유형 판단 (pipeline.link_basics).

은행 거래는 출금 transfer · 입금 income 으로 저장되는데, 이 단계가 내 계좌 간 이체 · 카드값 · 대출 상환 후보를 나눈다.
친구 송금은 그대로 transfer 다 (11/3 결정: 기본은 소비 아님). 반대쪽 거래는 전에 저장된 것까지 찾는다 (은행마다 조회 시점이 달라서).
"""
from collections import defaultdict

from django.db.models import Exists, OuterRef, Q

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
    입금이 늦게 와도 결과가 같도록 규칙이 정한 카드값도 다시 본다. 사용자가 확정했거나 납부에 붙은 거래, 같은 구매로 이은 출금은 바꾸지 않는다"""
    bank = Tx.objects.filter(user=user, source_kind="bank", is_canceled=False).exclude(
        confirmation_state=Tx.ConfirmationState.CONFIRMED,
    ).exclude(Exists(PaymentMatch.objects.filter(transaction=OuterRef("pk")))).exclude(
        Exists(TransactionLink.objects.filter(relation_type=Relation.SAME_PURCHASE, to_transaction=OuterRef("pk"))),
    )
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
    """[임시] 취소가 따로 온 줄을 같은 카드 · 같은 승인번호의 원거래에 잇는다. 은행은 승인번호가 없어 link_bank_cancellations 가 본다.
    취소 줄이 먼저 저장될 수 있어서 새 거래의 승인번호마다 양쪽을 다 찾는다"""
    keys = txs.filter(source_kind="card", approval_no__isnull=False).values_list("card_id", "approval_no").distinct()
    for card_id, approval_no in keys:
        rows = Tx.objects.filter(user=user, card_id=card_id, approval_no=approval_no).order_by("-occurred_on", "pk")
        for cancel in rows.filter(is_canceled=True):
            original = rows.filter(is_canceled=False, occurred_on__lte=cancel.occurred_on).first()
            if original is not None:
                link(user, original, cancel, Relation.CANCELLATION, cancel.canceled_amount or cancel.amount)


def link_bank_cancellations(user):
    """[임시] 은행 취소 줄(음수 금액, 택시 선결제 환불 · 예약 취소)을 원거래에 잇고 원거래도 취소로 표시해 한 쌍을 0원으로 만든다.
    같은 계좌 · 방향 · 금액 · 적요에서 취소 줄보다 앞선 가장 가까운 거래가 원거래다. 뒤에 같은 금액으로 다시 낸 거래는 짝이 아니다.
    원거래가 나중에 저장될 수 있어서 아직 안 이은 취소 줄을 모두 본다. 부분 환불(금액이 다름)은 못 잇는다.
    가장 가까운 거래가 납부에 붙어 있으면 자동 취소를 보류한다. 건너뛰고 찾으면 더 오래된 정상 거래를 대신 취소하게 된다"""
    links = TransactionLink.objects.filter(relation_type=Relation.CANCELLATION)
    waiting = Tx.objects.filter(user=user, source_kind="bank", is_canceled=True).exclude(
        Exists(links.filter(Q(from_transaction=OuterRef("pk")) | Q(to_transaction=OuterRef("pk")))),
    )
    for cancel in waiting.order_by("occurred_on", "occurred_time", "pk"):
        same_day = Q(occurred_on=cancel.occurred_on)
        if cancel.occurred_time is not None:
            same_day &= Q(occurred_time__lte=cancel.occurred_time)
        original = Tx.objects.filter(
            Q(occurred_on__lt=cancel.occurred_on) | same_day,
            user=user, account_id=cancel.account_id, direction=cancel.direction, amount=cancel.amount,
            source_description=cancel.source_description, is_canceled=False,
        ).order_by("-occurred_on", "-occurred_time", "-pk").first()
        if original is None or PaymentMatch.objects.filter(transaction=original).exists():
            continue
        Tx.objects.filter(pk=original.pk).update(is_canceled=True, canceled_amount=original.amount)
        link(user, original, cancel, Relation.CANCELLATION, cancel.amount)


def link_same_purchases(user):
    """[임시] 체크카드 승인과 그 결제로 나간 계좌 출금을 같은 구매로 잇는다. 이어진 계좌 줄은 거래 목록에서 빠진다 (api.listed_transactions).
    소비는 카드 줄이 맡고 계좌 줄은 이체로 남아 있어서, 잇지 않으면 한 결제가 두 줄로 보인다.
    같은 날 · 같은 금액 · 같은 이름이 양쪽 다 한 건일 때만 잇는다. 금액 · 날짜만 같은 거래는 다른 결제일 수 있어서다.
    신용카드는 결제 때 출금이 없어 보지 않는다. CODEF 카드 종류는 카드사마다 달라(NH 는 '본인') 카드 이름의 '체크'도 본다.
    양쪽 저장 순서가 달라도 되게 이미 이은 거래까지 모두 다시 본다. 뒤늦게 같은 출금이 들어와 모호해지면 규칙이 이은 연결을 푼다.
    사용자가 확정했거나 납부에 붙은 출금은 바꾸지 않는다"""
    approvals = Tx.objects.filter(
        Q(card__name__contains="체크") | Q(card__card_type__contains="체크"),
        user=user, source_kind="card", transaction_type=Type.SPENDING, is_canceled=False,
    )
    withdrawals = Tx.objects.filter(
        user=user, source_kind="bank", direction="out", transaction_type=Type.TRANSFER, is_canceled=False,
    ).exclude(confirmation_state=Tx.ConfirmationState.CONFIRMED).exclude(
        Exists(PaymentMatch.objects.filter(transaction=OuterRef("pk"))),
    )
    cards, banks = defaultdict(list), defaultdict(list)
    for tx in approvals:
        cards[tx.occurred_on, tx.amount, squeeze(tx.merchant_name)].append(tx)
    for tx in withdrawals:
        banks[tx.occurred_on, tx.amount, squeeze(tx.source_description)].append(tx)
    for key, found in cards.items():
        if key[2] and len(found) == 1 and len(banks[key]) == 1:
            link(user, found[0], banks[key][0], Relation.SAME_PURCHASE, found[0].amount)
        else:
            TransactionLink.objects.filter(
                relation_type=Relation.SAME_PURCHASE, confirmation_state=LINK_STATE,
                from_transaction__in=found, to_transaction__in=banks[key],
            ).delete()


def apply(user, txs):
    link_cancellations(user, txs)
    link_bank_cancellations(user)
    link_same_purchases(user)
    link_own_transfers(user, txs)
    mark_by_description(user, txs)

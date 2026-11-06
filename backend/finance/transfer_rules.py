"""거래 유형 판단 (pipeline.link_basics).

은행 거래는 출금 transfer · 입금 income 으로 저장되는데, 이 단계가 내 계좌 간 이체 추정 · 카드값 · 대출 상환 후보를 나눈다.
친구 송금은 그대로 transfer 다 (11/3 결정: 기본은 소비 아님).
"""
from collections import defaultdict
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
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
CARD_CANDIDATE = "card_payment_candidate"    # 거래 field_states: 적요의 카드사 코드. 유형은 청구서에 연결될 때만 card_payment (R11, 11/06)
# 거래 field_states: 내 계좌 이체 판단 — suggested(이름이 같아 추정) · confirmed(사용자가 맞다고 함) · rejected(아니라고 함)
OWN_REVIEW = "own_transfer_review"
OWN_GAP = timedelta(minutes=5)  # 내 계좌 이체 자동 분류: 출금 · 입금 시각 차이, 5분 포함 (11/5 결정, 자정 넘김 포함)
# 거래 field_states: 늦게 온 거래로 자동 연결이 모호해졌지만 저축 · 납부 근거라 풀지 않은 연결 (출금 쪽, 거래 상세에서 정정)
OWN_RECHECK = "own_transfer_recheck"


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


def same_name(text):
    """앞뒤 · 가운데 공백(탭 · 전각 포함)을 지우고 대소문자를 맞춘 이름"""
    return "".join((text or "").split()).upper()


def nickname(user):
    return same_name(get_user_model().objects.filter(pk=getattr(user, "pk", user)).values_list("nickname", flat=True).first())


def open_bank_rows(user):
    """규칙이 바꿔도 되는 은행 거래: 취소 · 사용자 확정 · 납부 · 같은 구매 · 이미 이은 내 계좌 이체가 아님"""
    return Tx.objects.filter(user=user, source_kind="bank", is_canceled=False).exclude(
        confirmation_state=Tx.ConfirmationState.CONFIRMED,
    ).exclude(Exists(PaymentMatch.objects.filter(transaction=OuterRef("pk")))).exclude(Exists(TransactionLink.objects.filter(
        Q(from_transaction=OuterRef("pk")) | Q(to_transaction=OuterRef("pk")),
        relation_type__in=[Relation.SAME_PURCHASE, Relation.OWN_TRANSFER],
    )))


def link_own_transfers(user, txs):
    """[규칙 추정] 양쪽 계좌 거래가 다 수집된 내 계좌 이체를 자동 분류한다 (R11, 11/5). 확실한 식별이 아니라서 auto 로 따로 남기고
    사용자가 거래 상세에서 [맞아요] · [연결 해제]할 수 있다 (api._apply_own_transfer).
    조건: 같은 회원의 다른 계좌 · 출금 ↔ 입금 · 같은 금액 · 시각 차이 5분 이내(시각이 없으면 안 함) · 양쪽 적요가 모두 가입 이름 ·
    양쪽에서 맞는 상대가 각각 하나뿐. 상대 거래의 유형과 상관없이 세어서 후보가 여럿이면 잇지 않는다.
    관리 대상 선택과 상관없이 저장된(실제로 수집된) 거래끼리 본다. 상대가 아직 없으면 suggest_own_transfers 가 이름만 보고 추정한다.
    이름만 추정된 출금(suggested)은 나중에 상대 입금이 들어오면 auto 로 올린다. 사용자 판단(confirmed · rejected)은 건드리지 않는다.
    새 거래가 기존 자동 연결의 후보가 되면 그 연결부터 다시 본다 (recheck_own_transfers) — 한꺼번에 받든 나눠 받든 결과가 같게"""
    name = nickname(user)
    base = Tx.objects.filter(user=user, source_kind="bank", is_canceled=False, occurred_time__isnull=False)

    def at(tx):
        return datetime.combine(tx.occurred_on, tx.occurred_time)

    def matches(tx):
        rows = base.filter(
            amount=tx.amount, direction="in" if tx.direction == "out" else "out",
            occurred_on__range=(tx.occurred_on - timedelta(days=1), tx.occurred_on + timedelta(days=1)),
        ).exclude(account_id=tx.account_id)
        return [o for o in rows if abs(at(o) - at(tx)) <= OWN_GAP
                and same_name(o.source_description) == name == same_name(tx.source_description)]

    def free(tx):
        review = (tx.field_states or {}).get(OWN_REVIEW)
        if tx.direction == "out":
            return tx.transaction_type == Type.TRANSFER and review in (None, "suggested")
        return tx.transaction_type == Type.INCOME and review is None

    if not name:
        return
    new = list(base.filter(pk__in=txs.values("pk")).order_by("occurred_on", "occurred_time", "pk"))
    recheck_own_transfers(user, {tx.pk for tx in new} | {o.pk for tx in new for o in matches(tx)}, matches, name)
    open_ids = open_bank_rows(user).values("pk")
    for tx in new:
        tx.refresh_from_db()
        if not open_bank_rows(user).filter(pk=tx.pk).exists():
            continue
        found = matches(tx)
        if not free(tx) or len(found) != 1 or len(matches(found[0])) != 1:
            continue
        with transaction.atomic():
            pair = list(Tx.objects.select_for_update().filter(pk__in=[tx.pk, found[0].pk]).filter(pk__in=open_ids).order_by("pk"))
            if len(pair) != 2 or not all(free(t) for t in pair):
                continue  # 잠그는 사이 바뀜
            if Tx.objects.filter(pk__in=[t.pk for t in pair], salary_decision__state="confirmed").exists():
                continue  # 사용자가 급여로 확인한 입금 (R12) — 잠근 뒤 다시 봐서 급여 확인과 겹쳐도 한쪽만
            for t in pair:
                Tx.objects.filter(pk=t.pk).update(
                    transaction_type=Type.OWN_TRANSFER, field_states={**t.field_states, OWN_REVIEW: "auto"},
                )
            out, income = sorted(pair, key=lambda t: t.direction != "out")
            link(user, out, income, Relation.OWN_TRANSFER, out.amount)


def recheck_own_transfers(user, touched_ids, matches, name):
    """새 거래가 후보로 끼어든 기존 자동 연결(auto)을 다시 본다. 아직 양쪽 다 유일하면 둔다.
    모호해졌으면 연결을 지우고 출금은 transfer(이름이 같으면 suggested), 입금은 income 으로 되돌린다 — 다른 후보에 갈아 붙이지 않는다.
    사용자가 확인한 연결(confirmed)은 그대로. 저축 · 납부 · 고정지출 근거가 된 거래는 풀지 않고 출금에 OWN_RECHECK 를 남겨
    거래 상세에서 다시 확인하게 한다 (저축으로 바뀐 거래도 상세의 [아니에요]로 저축 근거에서 뺄 수 있음)"""
    from budgets.candidates import linked_evidence  # budgets.candidates 가 이 파일을 읽어서 함수 안에서 부른다

    links = TransactionLink.objects.filter(
        Q(from_transaction__in=touched_ids) | Q(to_transaction__in=touched_ids),
        user=user, relation_type=Relation.OWN_TRANSFER, confirmation_state=LINK_STATE,
    ).select_related("from_transaction", "to_transaction")
    used = None
    for row in links:
        out, income = row.from_transaction, row.to_transaction
        if (out.field_states or {}).get(OWN_REVIEW) != "auto":
            continue  # 사용자 판단
        if (not out.is_canceled and not income.is_canceled and out.occurred_time and income.occurred_time
                and len(matches(out)) == 1 and len(matches(income)) == 1):
            continue
        used = linked_evidence(user) if used is None else used
        with transaction.atomic():
            pair = list(Tx.objects.select_for_update().filter(pk__in=[out.pk, income.pk]).order_by("pk"))
            out, income = sorted(pair, key=lambda t: t.direction != "out")
            protected = any(
                t.transaction_type != Type.OWN_TRANSFER or str(t.pk) in used
                or PaymentMatch.objects.filter(transaction=t).exists() for t in pair
            )
            if protected:
                Tx.objects.filter(pk=out.pk).update(field_states={**out.field_states, OWN_RECHECK: True})
                continue
            out_states = {**out.field_states}
            if same_name(out.source_description) == name:
                out_states[OWN_REVIEW] = "suggested"
            else:
                out_states.pop(OWN_REVIEW, None)
            in_states = {k: v for k, v in income.field_states.items() if k != OWN_REVIEW}
            Tx.objects.filter(pk=out.pk).update(transaction_type=Type.TRANSFER, field_states=out_states)
            Tx.objects.filter(pk=income.pk).update(transaction_type=Type.INCOME, field_states=in_states)
            row.delete()


def suggest_own_transfers(user, txs):
    """은행 출금의 적요가 가입 이름(nickname)과 통째로 같으면 '내 계좌 이체 추정'으로 표시만 한다 (R11, 11/5).
    유형은 transfer 그대로 두고 상대 계좌 · 입금 거래는 만들지 않는다. '김나은상점'처럼 일부만 같거나 이름이 비면 아니다.
    CODEF 은행 거래에는 적요 1~4칸뿐이라 상대 계좌 · 송금 식별값이 없다. 양쪽 거래가 다 있으면 link_own_transfers 가 먼저 본다.
    이미 판단이 있는 거래(추정 · 확인 · 거절)는 건드리지 않아서 재수집 · 재처리 · 이름 변경에도 사용자 판단이 남는다.
    납부 · 같은 구매에 붙었거나 사용자가 확정한 출금은 보지 않는다"""
    name = nickname(user)
    if not name:
        return
    rows = open_bank_rows(user).filter(pk__in=txs.values("pk"), direction="out", transaction_type=Type.TRANSFER)
    for tx in rows:
        # 카드값 후보는 보지 않는다 (예전에 card_payment 로 바뀌어 여기 오지 않던 것과 같게)
        if OWN_REVIEW not in tx.field_states and CARD_CANDIDATE not in tx.field_states and same_name(tx.source_description) == name:
            Tx.objects.filter(pk=tx.pk).update(field_states={**tx.field_states, OWN_REVIEW: "suggested"})


def mark_by_description(user, txs):
    """남은 출금 중 적요에 카드사 이름 → 카드값 후보, 상환 중인 내 대출의 기관 이름 → 대출 상환 후보 표시.
    후보는 transfer 그대로라 목록에 남아 사용자가 고칠 수 있다 (적요 '국민은행 홍길동' · '신한카드 홍길동'처럼 사람에게 보낸 돈일 수 있어서).
    카드값 후보는 청구서에 자동 연결되거나(card_payments.match) 사용자가 고르면 card_payment 가 된다 (R11, 11/06 — 예전엔 이름만으로 바로 바꿔 목록에서 숨김).
    [임시] 대출보다 거래를 먼저 불러오면 후보가 되지 않는다"""
    lenders = [squeeze(name) for name in Loan.objects.filter(
        user=user, status=Loan.Status.REPAYING, lender_name__isnull=False,
    ).values_list("lender_name", flat=True)]
    for tx in txs.filter(source_kind="bank", transaction_type=Type.TRANSFER, is_canceled=False):
        text = squeeze(tx.source_description)
        if card_issuer(text):
            Tx.objects.filter(pk=tx.pk).update(field_states={**tx.field_states, CARD_CANDIDATE: card_issuer(text)})
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
    suggest_own_transfers(user, txs)  # 카드값 · 대출 후보로 바뀌지 않은 이체만

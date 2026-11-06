"""카드 청구서 납부 거래 확인 (docs/api/cards.md '카드 청구서 납부 거래 확인', 11/06)

  GET    /api/finance/card-bills/<billId>/payments/            청구액 · 확인된 납부액 · 남은 금액 + 연결한 출금 + 고를 수 있는 출금 (?from=&to=)
  POST   /api/finance/card-bills/<billId>/payments/            출금 여러 건 연결 { transactionIds: [...], confirmReclassify? }
  DELETE /api/finance/card-bills/<billId>/payments/<txId>/     연결 취소 (이 연결이 바꾼 분류만 되돌리고, 이 청구서에 자동으로 다시 붙지 않게)

저장된 거래만 본다 (CODEF 호출 없음). 청구 총액은 바꾸지 않는다. 대출과 달리 '다 냈어요'가 없다 — 상태는 금액으로만 (card_payments.refresh_card_due).
고를 수 있는 출금 · 빼앗지 않는 거래는 대출 납부 확인과 같다 (api_loan_payments.selectable · blocked). 기본 기간은 선결제를 찾게 결제일 −35일부터.
잠금 순서: 회원 → 납부 예정 → 거래
"""
from datetime import date

from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from budgets.api import account_json, lock_user
from config.idempotency import idempotent

from .api_cards import require_card_consent
from .api_loan_payments import (LIMIT, AlreadyLinked, blocked, net_amount, other_feature_ids, reclassify_json,
                                search_range, selectable)
from .card_deduction import bill_total_mismatch
from .card_payments import (CARD_PAYMENT, CHECK_ANSWERS, SKIP_AUTO, USER_CHECK, adopted, bill_due, bill_due_on,
                            bill_payment, bill_status, paying_accounts, refresh_card_due, skip_key)
from .display import CARD_ISSUERS
from .loan_payments import CLASS_FIELDS, record_link, snapshot
from .models import CardBill, FinancialTransaction as Tx, PaymentDue, PaymentMatch
from .transfer_rules import card_issuer

BEFORE_DAYS = 35   # 기본 후보: 결제일 35일 전 ~ 7일 뒤 (선결제 · 분할 납부)
MAX_IDS = 20


class BillClosed(APIException):
    status_code = 409
    default_code = "not_ready"
    default_detail = "이 청구서에는 지금 납부를 연결할 수 없어요. 카드 관리에서 청구서를 다시 불러온 뒤 확인해 주세요."


class LinkSerializer(serializers.Serializer):
    transaction_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1, max_length=MAX_IDS)
    confirm_reclassify = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({name: ["이 칸은 보낼 수 없어요."] for name in sorted(unknown)})
        attrs["transaction_ids"] = sorted(set(attrs["transaction_ids"]))  # 같은 거래를 두 번 보내도 한 번
        return attrs


def locked_bill(user, bill_id):
    """남의 것 · 없는 청구서 · 카드별 명세서는 404. 예정이 있으면 잠근다 (없으면 None)"""
    require_card_consent(user)
    bill = get_object_or_404(CardBill.objects.select_related("connection").filter(
        user=user, scope_type=CardBill.ScopeType.ISSUER), pk=bill_id)
    if bill.connection and bill.connection.user_id != user.pk:
        raise Http404
    return bill, PaymentDue.objects.select_for_update().filter(user=user, bill=bill).first()


def record_of(tx):
    """거래의 카드 청구서 연결 기록 (분류를 바꾸기 전 값). 한 출금이 같은 날 명세서 여러 장을 냈으면 예정마다가 아니라 거래에 하나다"""
    record = (tx.field_states or {}).get(CARD_PAYMENT)
    return record if isinstance(record, dict) else None


def other_links(tx, due):
    return PaymentMatch.objects.filter(transaction=tx).exclude(due=due).exists()


def cancel_effect(tx, due):
    """연결 취소하면 분류가 어떻게 되는지: 이 연결이 분류를 바꿨고 그 뒤 그대로면 restore, 아니면 keep (자동 연결 · 이미 카드값).
    다른 납부 연결이 남아 있으면 keep — 그 출금은 아직 카드값이다. 마지막 연결을 풀 때 되돌린다 (취소 순서와 상관없이 같게)"""
    record = record_of(tx)
    if (record and not other_links(tx, due) and record.get("before") != record.get("after")
            and snapshot(tx) == record.get("after")):
        before = record["before"]
        return {"kind": "restore", "to_type": before["transaction_type"],
                "to_category": before["confirmed_category_id"] if before["transaction_type"] == "spending" else None}
    return {"kind": "keep", "to_type": tx.transaction_type,
            "to_category": tx.confirmed_category_id if tx.transaction_type == "spending" else None}


def release(tx, due):
    effect = cancel_effect(tx, due)
    if effect["kind"] == "restore":
        for name in CLASS_FIELDS:
            setattr(tx, name, record_of(tx)["before"][name])
    states = dict(tx.field_states or {})
    if record_of(tx) and not other_links(tx, due):
        states.pop(CARD_PAYMENT)  # 남은 연결이 있으면 기록을 두어 마지막 취소 때 되돌린다
    states[SKIP_AUTO] = sorted(set(states.get(SKIP_AUTO, [])) | {skip_key(due)})
    tx.field_states = states
    tx.save(update_fields=[*CLASS_FIELDS, "field_states", "updated_at"])
    return {"transaction_id": str(tx.pk), "classification": "restored" if effect["kind"] == "restore" else "kept",
            "transaction_type": tx.transaction_type,
            "category": tx.confirmed_category_id if tx.transaction_type == Tx.TransactionType.SPENDING else None}


def tx_json(tx, bill, accounts):
    issuer = card_issuer(tx.source_description)
    return {
        "transaction_id": str(tx.pk),
        "date": tx.occurred_on.isoformat(),
        "amount": net_amount(tx),
        "account": account_json(tx.account),
        "description": tx.source_description,
        "hints": {
            "issuer_in_description": issuer == bill.issuer_code,
            "other_issuer": issuer is not None and issuer != bill.issuer_code,
            "paying_account": tx.account_id in accounts,
            "near_due_date": bill_due_on(bill) is not None and abs((tx.occurred_on - bill_due_on(bill)).days) <= 3,
        },
        "reclassify": reclassify_json(tx, Tx.TransactionType.CARD_PAYMENT),
    }


def state_json(user, bill, due, today, start=None, end=None):
    paid, left, state, broken = bill_payment(bill, due, today)
    review = None
    if state == "needs_review":
        review = "payment_canceled" if broken else "due_date_unknown" if bill_due_on(bill) is None else "payment_not_found"
    accounts = paying_accounts(user, bill.issuer_code, [bill])
    matches = due.matches.select_related("transaction__account__connection").order_by("matched_at", "pk") if due else []
    body = {
        "bill": {
            "bill_id": str(bill.pk), "issuer_code": bill.issuer_code, "issuer_name": CARD_ISSUERS.get(bill.issuer_code),
            "due_date": bill_due_on(bill).isoformat() if bill_due_on(bill) else None,
            "total_amount": bill.total_amount,   # 조회된 청구 총액 (납부해도 줄이지 않음)
            "paid_amount": paid,                 # 확인된(유효한) 납부액
            "remaining_amount": left,            # max(0, 청구액 − 납부액)
            "payment_state": state,
            "review_reason": review,             # needs_review 이유: due_date_unknown('결제일 확인 필요') · payment_not_found · payment_canceled
            "can_link": adopted(user, bill) and (due is None or due.obligation_state == PaymentDue.ObligationState.ACTIVE),
        },
        "linked": [{
            **tx_json(m.transaction, bill, accounts), "matched_amount": m.matched_amount,
            "source": (record_of(m.transaction) or {}).get("source") or "rule",
            "cancel_effect": cancel_effect(m.transaction, due),
        } for m in matches],
    }
    if start is not None:
        excluded = other_feature_ids(user)
        rows = (selectable(user, today).filter(occurred_on__range=(start, end))
                .exclude(pk__in=PaymentMatch.objects.filter(user=user).values("transaction"))
                .select_related("account__connection"))
        items = [tx_json(tx, bill, accounts) for tx in rows if str(tx.pk) not in excluded and net_amount(tx) > 0]
        # 적요에 이 카드사 → 결제 계좌 → 결제일 근처 → 날짜 차이 순 (다른 카드사 적요도 빼지 않고 뒤로)
        due_on = bill_due_on(bill)
        items.sort(key=lambda i: (
            not i["hints"]["issuer_in_description"], i["hints"]["other_issuer"], not i["hints"]["paying_account"],
            abs((date.fromisoformat(i["date"]) - due_on).days), i["transaction_id"],
        ))
        body.update({"search_from": start.isoformat(), "search_to": end.isoformat(), "items": items[:LIMIT]})
    return body


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def card_bill_payments(request, bill_id):
    if request.method == "POST":
        return link_payments(request, bill_id)
    today = timezone.localdate()
    with transaction.atomic():
        lock_user(request.user)
        bill, due = locked_bill(request.user, bill_id)
        if bill_due_on(bill) is None:
            return Response(state_json(request.user, bill, due, today))  # 결제일을 모르면 후보 기간을 정할 수 없다
        start, end = search_range(request, bill_due_on(bill), today, BEFORE_DAYS)
        return Response(state_json(request.user, bill, due, today, start, end))


@idempotent()
@transaction.atomic
def link_payments(request, bill_id):
    today = timezone.localdate()
    lock_user(request.user)
    bill, due = locked_bill(request.user, bill_id)
    data = LinkSerializer(data=request.data)
    data.is_valid(raise_exception=True)
    values = data.validated_data
    if not adopted(request.user, bill) or (due is not None and due.obligation_state != PaymentDue.ObligationState.ACTIVE):
        raise BillClosed()
    invalid = serializers.ValidationError({"transaction_ids": ["고를 수 없는 거래가 있어요."]})
    txs = list(selectable(request.user, today).select_for_update(of=("self",))
               .filter(pk__in=values["transaction_ids"]).select_related("account").order_by("pk"))
    if len(txs) != len(values["transaction_ids"]):
        raise invalid
    for tx in txs:
        if PaymentMatch.objects.filter(transaction=tx).exclude(due=due).exists():  # due 가 None 이면 연결 하나라도 있으면
            raise AlreadyLinked()
        current = due is not None and PaymentMatch.objects.filter(transaction=tx, due=due).exists()
        if not current and blocked(request.user, tx):
            raise invalid
        change = reclassify_json(tx, Tx.TransactionType.CARD_PAYMENT)
        if change and change["needs_confirm"] and not values["confirm_reclassify"]:
            raise serializers.ValidationError({"confirm_reclassify": ["분류가 카드값으로 바뀌는 거래가 있어요. 바뀌는 내용을 확인해 주세요."]})
    due = due or bill_due(bill, txs[0])
    for tx in txs:
        record_link(tx, due, None, "user", record_key=CARD_PAYMENT, skip_key=skip_key(due),
                    transaction_type=Tx.TransactionType.CARD_PAYMENT, confirmation_state="confirmed",
                    classification_source="user")
        PaymentMatch.objects.get_or_create(due=due, transaction=tx, defaults={
            "user": request.user, "matched_amount": net_amount(tx),  # 청구액보다 많아도 자르지 않는다
        })
    refresh_card_due(due, today)
    return Response(state_json(request.user, bill, due, today))


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def card_bill_payment(request, bill_id, transaction_id):
    today = timezone.localdate()
    with transaction.atomic():
        lock_user(request.user)
        bill, due = locked_bill(request.user, bill_id)
        if request.data:
            raise serializers.ValidationError({name: ["이 칸은 보낼 수 없어요."] for name in sorted(request.data)})
        tx = get_object_or_404(Tx.objects.select_for_update().filter(user=request.user), pk=transaction_id)
        match = PaymentMatch.objects.filter(due=due, transaction=tx).first() if due else None
        if match is None:  # 이미 풀린 연결 (재전송) → 지금 상태 그대로
            return Response({**state_json(request.user, bill, due, today), "released": None})
        match.delete()
        released = release(tx, due)
        refresh_card_due(due, today)
        return Response({**state_json(request.user, bill, due, today), "released": released})


# ---------- 납부 확인 (첫 예산: 이 청구액 중 앞으로 얼마를 더 내야 하나) ----------
#   PUT    /api/finance/card-bills/<billId>/payment-check/   { answer: unpaid|paid|partial|later, remainingAmount? (partial 만) }
#   DELETE 같은 주소                                         확인 취소 (출금 근거는 그대로)
# 거래를 고르지 않는 사용자 확인이다: 가짜 출금 · PaymentMatch 를 만들지 않고 청구 총액도 바꾸지 않는다.
# 청구서 field_states[USER_CHECK] 에 답 · 그때 청구액 · 시각을 남긴다 (재조회가 지우지 않음, 청구액이 바뀌면 재확인)

class CheckSerializer(serializers.Serializer):
    answer = serializers.ChoiceField(choices=CHECK_ANSWERS)
    remaining_amount = serializers.IntegerField(required=False)

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({name: ["이 칸은 보낼 수 없어요."] for name in sorted(unknown)})
        raw = self.initial_data.get("remaining_amount")
        if raw is not None and type(raw) is not int:
            raise serializers.ValidationError({"remaining_amount": ["원 단위 정수로 보내 주세요."]})
        if attrs["answer"] == "partial":
            if raw is None:
                raise serializers.ValidationError({"remaining_amount": ["앞으로 더 낼 금액을 입력해 주세요."]})
        elif "remaining_amount" in self.initial_data:
            raise serializers.ValidationError({"remaining_amount": ["일부 냈어요일 때만 보내요."]})
        return attrs


def checked_bill(request, bill_id):
    lock_user(request.user)
    bill, due = locked_bill(request.user, bill_id)
    return CardBill.objects.select_for_update().get(pk=bill.pk), due  # 재조회 저장(_save_bills)과 같은 행 잠금


@api_view(["PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def card_bill_check(request, bill_id):
    today = timezone.localdate()
    with transaction.atomic():
        bill, due = checked_bill(request, bill_id)
        states = dict(bill.field_states or {})
        if request.method == "DELETE":
            if request.data:
                raise serializers.ValidationError({name: ["이 칸은 보낼 수 없어요."] for name in sorted(request.data)})
            if states.pop(USER_CHECK, None) is not None:
                bill.field_states = states
                bill.save(update_fields=["field_states"])
            return Response(bill_status(bill, due, today))
        data = CheckSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        if bill_total_mismatch(bill):  # 원래 청구액을 모르면 '앞으로 낼 금액'의 기준도 없다
            raise BillClosed("카드사 청구 총액과 상세 합계가 달라 지금은 납부 금액을 확인할 수 없어요.")
        if bill.total_amount <= 0:
            raise BillClosed("청구액이 0원이라 확인할 금액이 없어요.")
        left = values.get("remaining_amount")
        if values["answer"] == "partial" and not 0 < left < bill.total_amount:
            raise serializers.ValidationError({"remaining_amount": [f"1원 이상 {bill.total_amount - 1:,}원 이하로 입력해 주세요."]})
        record = {"answer": values["answer"], "remaining_amount": left, "bill_total": bill.total_amount}
        old = states.get(USER_CHECK) or {}
        if {k: old.get(k) for k in record} != record:  # 같은 답을 다시 보내면 시각도 그대로
            states[USER_CHECK] = {**record, "confirmed_at": timezone.now().isoformat()}
            bill.field_states = states
            bill.save(update_fields=["field_states"])
        return Response(bill_status(bill, due, today))


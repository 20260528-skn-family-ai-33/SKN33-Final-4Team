"""대출 납부 거래 확인 (docs/api/loans.md '12. 대출 납부 거래 확인', 11/06)

  GET    /api/finance/loan-dues/<dueId>/payments/            회차 상태 + 연결된 거래 + 고를 수 있는 출금 후보 (?from=&to= 로 기간 넓히기)
  POST   /api/finance/loan-dues/<dueId>/payments/            출금 연결 · 정정 { transactionId, settlement: full|partial, confirmReclassify? }
  DELETE /api/finance/loan-dues/<dueId>/payments/<txId>/     연결 취소 (이 연결이 바꾼 분류만 되돌림. 원래 분류를 모르면
                                                             { restoreType: transfer|spending, restoreCategory? } 로 고른 분류와 함께)

저장된 거래만 본다 (CODEF 호출 없음). 금액 차이 · 기관 이름은 참고 표시만 하고 자동 확정에 쓰지 않는다.
'다 냈어요'는 이 회차만 완료다 — 대출 상태(repaid) · 월 상환액 · 승인 예산은 바꾸지 않는다.
잠금 순서: 회원 → 대출 → 회차 → 거래 (홈 · 대출 수정과 같음)
"""
from datetime import date, timedelta

from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from budgets.api import account_json, id_set, lock_user, refresh_due_state
from budgets.models import Category
from config.idempotency import idempotent

from .api import cancel_info
from .api_connect import ConsentRequired
from .api_loans import has_loan_consent, owned_loans
from .institutions import BANK, name_of
from .loan_payments import LOAN_PAYMENT, near_payment_day, record_link, release_link, release_plan, settlement_of
from .models import (FinancialTransaction as Tx, Origin, PaymentDue, PaymentMatch, RecurringExpense,
                     RecurringSaving, TransactionLink)
from .transfer_rules import OWN_RECHECK, OWN_REVIEW, squeeze

DEFAULT_DAYS = 7        # 기본 후보: 납부일 ±7일
MAX_DAYS = 92           # 직접 넓혀 찾을 수 있는 기간 (약 3달)
LIMIT = 50
OTHER_FEATURE_TYPES = ["own_transfer", "saving", "income"]   # 다른 기능의 거래라 고를 수 없음
NO_CONFIRM_TYPES = ("transfer", "loan_repayment")             # 이 유형에서 대출 상환으로 바꿀 때는 따로 묻지 않음


class AlreadyLinked(APIException):
    status_code = 409
    default_code = "not_ready"
    default_detail = "이미 다른 납부에 연결된 거래예요. 그 납부에서 연결을 먼저 풀어 주세요."


class ReleaseSerializer(serializers.Serializer):
    """연결 취소 본문 (선택). 원래 분류를 모르는 거래(cancelEffect.kind = choose)만 쓴다"""
    restore_type = serializers.ChoiceField(choices=["transfer", "spending"], required=False)
    restore_category = serializers.CharField(required=False, allow_null=True)

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({name: ["이 칸은 보낼 수 없어요."] for name in sorted(unknown)})
        kind, code = attrs.get("restore_type"), attrs.get("restore_category")
        if kind == "spending" and (not code or code == "UNCLASSIFIED"
                                   or not Category.objects.filter(category_code=code, is_active=True).exists()):
            raise serializers.ValidationError({"restore_category": ["소비 카테고리를 골라 주세요."]})
        if kind != "spending" and code is not None:
            raise serializers.ValidationError({"restore_category": ["소비로 바꿀 때만 카테고리를 보내요."]})
        if kind is None and "restore_category" in attrs:
            raise serializers.ValidationError({"restore_type": ["분류 유형을 골라 주세요."]})
        return attrs

    def chosen(self):
        """사용자가 고른 분류 → 거래 칸 값 (이체는 거래 목록 규칙처럼 카테고리 '확인 필요')"""
        kind = self.validated_data.get("restore_type")
        if kind == "transfer":
            return {"transaction_type": "transfer", "confirmed_category_id": "UNCLASSIFIED",
                    "confirmation_state": "needs_review", "classification_source": None}
        if kind == "spending":
            return {"transaction_type": "spending", "confirmed_category_id": self.validated_data["restore_category"],
                    "confirmation_state": "confirmed", "classification_source": "user"}
        return None


class LinkSerializer(serializers.Serializer):
    transaction_id = serializers.UUIDField()
    settlement = serializers.ChoiceField(choices=["full", "partial"])
    confirm_reclassify = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({name: ["이 칸은 보낼 수 없어요."] for name in sorted(unknown)})
        return attrs


def net_amount(tx):
    return tx.amount - cancel_info(tx)["amount"]


def locked_due(user, due_id):
    """회원 잠금 뒤 대출 → 회차 순으로 잠근다. 남의 것 · 없는 회차 · 취소된 회차는 404"""
    due = get_object_or_404(PaymentDue.objects.filter(user=user, obligation_type="loan", obligation_state="active"),
                            pk=due_id)
    loan = owned_loans(user).select_for_update(of=("self",)).filter(pk=due.loan_id).first()
    if loan is None:
        raise Http404
    if loan.origin != Origin.MANUAL and not has_loan_consent(user):
        raise ConsentRequired("기관에서 조회한 대출의 납부를 확인하려면 대출 조회 동의가 필요해요.")
    due = get_object_or_404(PaymentDue.objects.select_for_update().filter(
        user=user, loan=loan, obligation_type="loan", obligation_state="active"), pk=due_id)
    return loan, due


def other_feature_ids(user):
    """고정지출 · 정기 저축의 근거 거래와 내 계좌 이체 · 카드 승인(같은 구매)으로 묶인 거래 (빼앗지 않음, 그 연결도 건드리지 않음)"""
    ids = set()
    for model in (RecurringExpense, RecurringSaving):
        for evidence in model.objects.filter(user=user).values_list("evidence", flat=True):
            ids |= id_set(evidence)
    linked = TransactionLink.objects.filter(user=user, relation_type__in=[
        TransactionLink.Relation.OWN_TRANSFER, TransactionLink.Relation.SAME_PURCHASE,
    ])
    ids |= {str(pk) for pk in linked.values_list("from_transaction_id", flat=True)}
    ids |= {str(pk) for pk in linked.values_list("to_transaction_id", flat=True)}
    return ids


def selectable(user, today):
    """고를 수 있는 출금: 내 계좌 · 출금 · 오늘까지 · 취소 아님 · 다른 납부에 안 붙음 · 다른 기능 근거 아님"""
    return (Tx.objects.filter(
        user=user, source_kind="bank", direction="out", is_canceled=False, occurred_on__lte=today,
        account__user=user, account__connection__user=user,
    ).exclude(transaction_type__in=OTHER_FEATURE_TYPES)
     # 키가 없는 행에서 NOT(NULL) 로 빠지지 않게 has_key 와 함께 (내 계좌 이체로 보거나 확인 중인 출금은 R11 것)
     .exclude(Q(field_states__has_key=OWN_REVIEW, **{f"field_states__{OWN_REVIEW}__in": ["auto", "suggested", "confirmed"]}))
     .exclude(Q(field_states__has_key=OWN_RECHECK, **{f"field_states__{OWN_RECHECK}": True})))


def blocked(user, tx):
    return str(tx.pk) in other_feature_ids(user) or net_amount(tx) <= 0


def reclassify_json(tx):
    """대출 상환으로 바꾸면 달라지는 분류. 이미 대출 상환이면 null"""
    if tx.transaction_type == "loan_repayment":
        return None
    return {
        "from_type": tx.transaction_type,
        "from_category": tx.confirmed_category_id if tx.transaction_type == "spending" else None,
        "needs_confirm": tx.transaction_type not in NO_CONFIRM_TYPES or tx.confirmation_state == "confirmed",
    }


def remembered(user, loan):
    """전에 사용자가 이 대출 납부로 확인한 출금의 계좌 · 적요 (추천 순서에만 씀)"""
    rows = Tx.objects.filter(payment_matches__due__loan=loan, user=user).only("account_id", "source_description", "field_states")
    picked = [t for t in rows if ((t.field_states or {}).get(LOAN_PAYMENT) or {}).get("source") == "user"]
    return {t.account_id for t in picked}, {squeeze(t.source_description) for t in picked if t.source_description}


def tx_json(tx, due, loan, memory=None):
    accounts, labels = memory or (set(), set())
    lender = squeeze(name_of(BANK, loan.lender_name)) if loan.lender_name else ""
    amount = net_amount(tx)
    settlement, source = settlement_of(tx, due)
    return {
        "transaction_id": str(tx.pk),
        "date": tx.occurred_on.isoformat(),
        "amount": amount,
        "account": account_json(tx.account),
        "description": tx.source_description,
        "amount_difference": amount - due.confirmed_amount,   # 실제 − 예정 (참고)
        "hints": {
            "linked_account": loan.linked_account_id is not None and tx.account_id == loan.linked_account_id,
            "near_due_date": abs((tx.occurred_on - due.due_date).days) <= 3
                             or (loan.payment_day is not None and near_payment_day(loan, tx.occurred_on)),
            "lender_in_description": bool(lender) and lender in squeeze(tx.source_description),
            "same_as_before": tx.account_id in accounts or squeeze(tx.source_description) in labels,
        },
        "reclassify": reclassify_json(tx),
        "settlement": settlement,
        "source": source,
    }


def cancel_effect(tx, due):
    """연결 취소하면 분류가 어떻게 되는지 (연결한 출금만). choose 면 화면이 분류를 받아 DELETE 본문으로 보낸다"""
    kind, before = release_plan(tx, due)
    effect = {"kind": kind, "to_type": None, "to_category": None}
    if kind == "restore":
        effect["to_type"] = before["transaction_type"]
        effect["to_category"] = before["confirmed_category_id"] if before["transaction_type"] == "spending" else None
    elif kind == "keep":
        effect["to_type"] = tx.transaction_type
        effect["to_category"] = tx.confirmed_category_id if tx.transaction_type == "spending" else None
    return effect


def search_range(request, due, today):
    end_default = min(due.due_date + timedelta(days=DEFAULT_DAYS), today)
    start_default = min(due.due_date - timedelta(days=DEFAULT_DAYS), end_default - timedelta(days=DEFAULT_DAYS))
    errors, values = {}, {}
    for name, default in (("from", start_default), ("to", end_default)):
        raw = request.query_params.get(name)
        try:
            values[name] = date.fromisoformat(raw) if raw else default
        except ValueError:
            errors[name] = ["YYYY-MM-DD 형식으로 보내 주세요."]
    if not errors and values["from"] > values["to"]:
        errors["from"] = ["시작일이 끝일보다 늦어요."]
    if not errors and (values["to"] - values["from"]).days > MAX_DAYS:
        errors["to"] = [f"한 번에 {MAX_DAYS}일까지 찾을 수 있어요."]
    if errors:
        raise serializers.ValidationError(errors)
    return values["from"], values["to"]


def state_json(user, loan, due, today, start=None, end=None):
    memory = remembered(user, loan)
    linked = [m.transaction for m in due.matches.select_related("transaction__account__connection").order_by("matched_at", "pk")]
    body = {
        "due": {
            "due_id": str(due.pk), "loan_id": str(loan.pk),
            "name": " ".join(v for v in (loan.lender_name, loan.product_name) if v) or "대출 상환",
            "due_date": due.due_date.isoformat(),
            "expected_amount": due.confirmed_amount,  # 예정액 (월 상환액 기준, 바꾸지 않음)
            "paid_amount": due.paid_amount,           # 연결한 출금 합계
            "payment_state": due.payment_state,
            "linked_account": account_json(loan.linked_account),
        },
        "linked": [{**tx_json(tx, due, loan, memory), "cancel_effect": cancel_effect(tx, due)} for tx in linked],
    }
    if start is not None:
        excluded = other_feature_ids(user)
        rows = (selectable(user, today).filter(occurred_on__range=(start, end))
                .exclude(pk__in=PaymentMatch.objects.filter(user=user).values("transaction"))
                .select_related("account__connection"))
        items = [tx_json(tx, due, loan, memory) for tx in rows if str(tx.pk) not in excluded and net_amount(tx) > 0]
        # 연결 계좌 · 전에 확인한 계좌/적요 → 납부일 근처 → 날짜 차이 → 금액 차이 순 (금액이 달라도 빼지 않음)
        items.sort(key=lambda i: (
            not (i["hints"]["linked_account"] or i["hints"]["same_as_before"]), not i["hints"]["near_due_date"],
            abs((date.fromisoformat(i["date"]) - due.due_date).days), abs(i["amount_difference"]), i["transaction_id"],
        ))
        body.update({"search_from": start.isoformat(), "search_to": end.isoformat(), "items": items[:LIMIT]})
    return body


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def loan_due_payments(request, due_id):
    if request.method == "POST":
        return link_payment(request, due_id)
    today = timezone.localdate()
    with transaction.atomic():
        lock_user(request.user)
        loan, due = locked_due(request.user, due_id)
        start, end = search_range(request, due, today)
        return Response(state_json(request.user, loan, due, today, start, end))


@idempotent()
@transaction.atomic
def link_payment(request, due_id):
    today = timezone.localdate()
    lock_user(request.user)
    loan, due = locked_due(request.user, due_id)
    data = LinkSerializer(data=request.data)
    data.is_valid(raise_exception=True)
    values = data.validated_data
    invalid = serializers.ValidationError({"transaction_id": ["고를 수 없는 거래예요."]})
    tx = selectable(request.user, today).select_for_update(of=("self",)).filter(pk=values["transaction_id"]).first()
    if tx is None:
        raise invalid
    elsewhere = PaymentMatch.objects.filter(transaction=tx).exclude(due=due)
    if elsewhere.exists():
        raise AlreadyLinked()
    current = PaymentMatch.objects.filter(transaction=tx, due=due).first()
    if current is None and blocked(request.user, tx):
        raise invalid
    change = reclassify_json(tx)
    if change and change["needs_confirm"] and not values["confirm_reclassify"]:
        raise serializers.ValidationError({"confirm_reclassify": ["분류가 대출 상환으로 바뀌어요. 바뀌는 내용을 확인해 주세요."]})

    settlement = values["settlement"]
    if settlement == "full":
        # '다 냈어요'는 회차에 하나만: 앞서 '다 냈어요'로 고른 다른 출금은 '일부'로 남긴다
        others = Tx.objects.select_for_update().filter(payment_matches__due=due).exclude(pk=tx.pk).order_by("pk")
        for other in others:
            if settlement_of(other, due)[0] == "full":
                record = dict(other.field_states[LOAN_PAYMENT], settlement="partial")
                other.field_states = {**other.field_states, LOAN_PAYMENT: record}
                other.save(update_fields=["field_states", "updated_at"])
    record_link(tx, due, settlement, "user", transaction_type="loan_repayment",
                confirmation_state="confirmed", classification_source="user")
    if current is None:
        PaymentMatch.objects.create(user=request.user, due=due, transaction=tx, matched_amount=net_amount(tx))
    refresh_due_state(due, today)
    return Response(state_json(request.user, loan, due, today))


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def loan_due_payment(request, due_id, transaction_id):
    today = timezone.localdate()
    with transaction.atomic():
        lock_user(request.user)
        loan, due = locked_due(request.user, due_id)
        tx = get_object_or_404(Tx.objects.select_for_update().filter(user=request.user), pk=transaction_id)
        body = ReleaseSerializer(data=request.data if request.data else {})
        body.is_valid(raise_exception=True)
        match = PaymentMatch.objects.filter(due=due, transaction=tx).first()
        if match is None:  # 이미 풀린 연결 (재전송) → 아무것도 바꾸지 않고 지금 상태 그대로
            return Response({**state_json(request.user, loan, due, today), "released": None})
        kind, _ = release_plan(tx, due)
        chosen = body.chosen() if kind == "choose" else None  # 원래 분류 기록이 있으면 보낸 값은 쓰지 않는다
        if kind == "choose" and chosen is None:
            # 원래 분류를 모르는 거래: 고르기 전에는 연결을 풀지 않는다 (풀면 대출 상환인 채로 화면에서 사라짐)
            raise serializers.ValidationError({"restore_type": ["원래 분류를 알 수 없어요. 이 거래를 이체 또는 소비 중 무엇으로 둘지 골라 주세요."]})
        match.delete()
        released = release_link(tx, due, chosen)
        refresh_due_state(due, today)
        return Response({**state_json(request.user, loan, due, today), "released": released})

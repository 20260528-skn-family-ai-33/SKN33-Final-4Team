"""저장된 카드 후보·선택·청구서 API (docs/api/cards.md). 외부 조회는 하지 않는다."""
import uuid
from datetime import datetime

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from users.models import Consent

from .api_connect import PURPOSE, ConsentRequired
from .display import CARD_ISSUERS, account_display, card_display
from .inputs import body_dict, reject_unknown_fields
from .models import Card, CardBill, Connection


def require_card_consent(user):
    if not Consent.objects.filter(
        user=user, purpose=PURPOSE, product_scope=Consent.ProductScope.CARD,
        revoked_at__isnull=True,
    ).exists():
        raise ConsentRequired("카드 조회 동의가 필요해요.")


def local_iso(value):
    if not isinstance(value, datetime) or timezone.is_naive(value):
        return None
    return timezone.localtime(value).isoformat()


def candidate_cards(user):
    # 연결 없는 카드의 노출 정책은 미정. connected/relogin이 확인된 카드만 후보다.
    return Card.objects.filter(
        user=user, connection__user=user,
        connection__business_type=Connection.BusinessType.CARD,
        connection__status__in=[Connection.Status.CONNECTED, Connection.Status.RELOGIN],
    ).select_related("connection").order_by("card_id")


def candidate_item(card):
    return {
        **card_display(card),
        "card_type": card.card_type,
        "card_status": card.card_status,
        "is_selected": card.is_selected,
        "fetched_at": local_iso(card.fetched_at),
    }


def selected_card_ids(data):
    reject_unknown_fields(data, {"selected_card_ids"})
    raw = data.get("selected_card_ids")
    if not isinstance(raw, list):
        raise ValidationError({"selected_card_ids": ["카드 ID 배열을 보내 주세요."]})
    picked = set()
    for value in raw:
        try:
            if not isinstance(value, str):
                raise ValueError
            card_id = uuid.UUID(value)
        except ValueError:
            raise ValidationError({"selected_card_ids": ["선택할 수 없는 카드예요."]})
        # 계좌 선택 API와 동일하게 중복 ID는 입력 오류로 처리한다.
        if card_id in picked:
            raise ValidationError({"selected_card_ids": ["선택할 수 없는 카드예요."]})
        picked.add(card_id)
    return picked


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
def connect_cards(request):
    if request.method == "GET":
        require_card_consent(request.user)
        return Response({"items": [candidate_item(card) for card in candidate_cards(request.user)]})

    picked = selected_card_ids(body_dict(request))
    with transaction.atomic():
        # 같은 회원의 전체 선택 교체를 직렬화한다. 응답도 이 잠금 안에서 구성한다.
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        list(Connection.objects.select_for_update().filter(user=request.user).order_by("pk"))
        list(Card.objects.select_for_update().filter(user=request.user).order_by("pk"))
        allowed = set(candidate_cards(request.user).values_list("card_id", flat=True))
        if not picked.issubset(allowed):
            raise ValidationError({"selected_card_ids": ["선택할 수 없는 카드예요."]})
        own = Card.objects.filter(user=request.user)
        own.filter(is_selected=True).exclude(pk__in=picked).update(is_selected=False)
        own.filter(is_selected=False, pk__in=picked).update(is_selected=True)
        items = [candidate_item(card) for card in candidate_cards(request.user)]
    return Response({"items": items})


def owned_card_display(card, user_id):
    if card is None:
        return None
    if card.user_id != user_id or (card.connection and card.connection.user_id != user_id):
        raise Http404
    # 연결 없음은 연결됨으로 추정하지 않는다. 공통 표시 객체를 만들 근거가 없다.
    return card_display(card) if card.connection else None


def bill_item(row, user_id):
    if row.user_id != user_id:
        raise Http404
    return {
        "bill_item_id": row.pk,
        "item_type": row.item_type,
        "merchant_name": row.merchant_name,
        "card": owned_card_display(row.card, user_id),
        "purchased_on": row.purchased_on,
        "original_purchase_amount": row.original_purchase_amount,
        "billed_amount": row.billed_amount,
        "installment_months": row.installment_months,
        "installment_round": row.installment_round,
        "fetched_at": local_iso(row.fetched_at),
    }


def included_cards(bill, shown_card, items):
    if bill.scope_type == CardBill.ScopeType.CARD and shown_card is not None:
        return [shown_card]
    # 일부 상세로 전체 포함 카드 목록을 추정하지 않는다.
    if bill.items_state != CardBill.ItemsState.COMPLETE or not items:
        return None
    if any(item["card"] is None for item in items):
        return None
    cards = {item["card"]["card_id"]: item["card"] for item in items}
    return [cards[key] for key in sorted(cards)]


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def card_bill(request, bill_id):
    require_card_consent(request.user)
    bill = get_object_or_404(
        CardBill.objects.select_related(
            "connection", "card__connection", "payment_account__connection",
        ), pk=bill_id, user=request.user,
    )
    if bill.connection and bill.connection.user_id != request.user.pk:
        raise Http404
    account = bill.payment_account
    if account and (account.user_id != request.user.pk or account.connection.user_id != request.user.pk):
        raise Http404
    shown_card = owned_card_display(bill.card, request.user.pk)
    rows = list(bill.items.select_related("card__connection").order_by("purchased_on", "bill_item_id"))
    items = [bill_item(row, request.user.pk) for row in rows]
    # complete만 정상 확인된 0건을 뜻한다. 나머지 빈 상세는 미확보 null.
    details = items if items or bill.items_state == CardBill.ItemsState.COMPLETE else None
    return Response({
        "bill_id": bill.pk,
        "issuer_code": bill.issuer_code,
        "issuer_name": CARD_ISSUERS.get(bill.issuer_code),
        "scope_type": bill.scope_type,
        "billing_month": bill.billing_month.strftime("%Y-%m"),
        "total_amount": bill.total_amount,
        "confirmation_status": bill.confirmation_state,
        "fetched_at": local_iso(bill.fetched_at),
        "payment_due_date": bill.payment_due_date,
        "withdrawal_due_date": bill.withdrawal_due_date,
        "payment_account": account_display(account),
        "card": shown_card if bill.scope_type != CardBill.ScopeType.ISSUER else None,
        "cards": included_cards(bill, shown_card, items),
        "items_state": bill.items_state,
        "items": details,
    })

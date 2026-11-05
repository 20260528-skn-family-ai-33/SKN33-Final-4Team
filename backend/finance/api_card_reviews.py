"""금액이 확인된 카드 미매칭 거래 조회·거래별 결제 카드 확인."""
import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .api_cards import candidate_cards, require_card_consent, local_iso
from .card_review import save_transaction, promote_coverage
from .display import card_display, CARD_ISSUERS
from .inputs import body_dict, reject_unknown_fields
from .models import CardApprovalReview, Connection
from .pipeline import process_new_transactions


def available_cards(user, issuer):
    return candidate_cards(user).filter(
        issuer_code=issuer, origin="provider", connection__organization_code=issuer,
    )


def review_item(row, cards):
    payload = row.payload
    return {
        "review_id": row.pk, "issuer_code": row.issuer_code,
        "issuer_name": CARD_ISSUERS.get(row.issuer_code),
        "occurred_on": payload["occurred_on"], "occurred_time": payload.get("occurred_time"),
        "merchant_name": payload.get("merchant_name"), "amount": payload["amount"],
        "is_canceled": payload["is_canceled"], "canceled_amount": payload["canceled_amount"],
        "can_resolve": row.data_valid, "fetched_at": local_iso(row.fetched_at),
        "candidates": [{**card_display(card), "is_selected": card.is_selected,
                        "name_matches": bool(row.card_name and row.card_name == card.name)} for card in cards],
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def card_reviews(request):
    require_card_consent(request.user)
    # 연결 해제한 카드사의 미확인 거래는 다시 연결한 후 확인한다.
    issuers = candidate_cards(request.user).values_list("issuer_code", flat=True)
    rows = CardApprovalReview.objects.filter(
        user=request.user, issuer_code__in=issuers, transaction__isnull=True,
    ).order_by("-fetched_at", "pk")
    cards = list(candidate_cards(request.user).filter(origin="provider"))
    return Response({"items": [review_item(row, [
        card for card in cards if card.issuer_code == row.issuer_code
        and card.connection.organization_code == row.issuer_code
    ]) for row in rows]})


@api_view(["PUT"])
@permission_classes([IsAuthenticated])
def resolve_card_review(request, review_id):
    data = body_dict(request)
    reject_unknown_fields(data, {"card_id"})
    try:
        card_id = uuid.UUID(data["card_id"]) if isinstance(data.get("card_id"), str) else None
    except ValueError:
        card_id = None
    if card_id is None:
        raise ValidationError({"card_id": ["선택할 수 없는 카드예요."]})
    with transaction.atomic():
        get_user_model().objects.select_for_update(no_key=True).get(pk=request.user.pk)
        require_card_consent(request.user)
        list(Connection.objects.select_for_update().filter(user=request.user).order_by("pk"))
        row = get_object_or_404(CardApprovalReview, user=request.user, pk=review_id)
        card = available_cards(request.user, row.issuer_code).filter(pk=card_id).first()
        if card is None:
            raise ValidationError({"card_id": ["선택할 수 없는 카드예요."]})
        if row.resolved_card_id and row.resolved_card_id != card.pk:
            return Response({"error": {"code": "card_review_conflict", "message": "이미 다른 카드로 확인한 거래예요. 목록을 다시 확인해 주세요."}}, status=409)
        if not row.data_valid:
            raise ValidationError({"non_field_errors": ["금액이나 취소 정보를 확인할 수 없어요. 카드 내역을 다시 불러와 주세요."]})
        if row.transaction_id is None:
            tx, created = save_transaction(request.user, card, row.identity, row.payload)
            row.resolved_card, row.transaction, row.resolved_at = card, tx, timezone.now()
            row.save(update_fields=["resolved_card", "transaction", "resolved_at"])
            if created:
                process_new_transactions(request.user, [tx.pk])
        promote_coverage(request.user, row.issuer_code)
        return Response({"review_id": row.pk, "card_id": card.pk, "transaction_id": row.transaction_id, "status": "resolved"})

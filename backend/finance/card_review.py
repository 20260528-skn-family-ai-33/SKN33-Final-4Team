"""카드 식별 보완. 호출자는 회원 잠금을 잡는다. 확인 결과는 승인 한 건에만 적용한다."""
import json
from collections import Counter
from datetime import date, time

from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from .codef_cards import approval_source_key, card_matches
from .models import CardApprovalCoverage, CardApprovalReview, FinancialTransaction, SyncRun


def transaction_defaults(user, payload):
    data = dict(payload)
    if isinstance(data["occurred_on"], str):
        data["occurred_on"] = date.fromisoformat(data["occurred_on"])
    if isinstance(data.get("occurred_time"), str):
        data["occurred_time"] = time.fromisoformat(data["occurred_time"])
    return {
        **data, "user": user, "source_kind": "card", "direction": "out", "transaction_type": "spending",
        "confirmation_state": "needs_review", "confirmed_category_id": "UNCLASSIFIED",
    }


def save_transaction(user, card, identity, payload, existing=None):
    if existing is None:
        tx, created = FinancialTransaction.objects.get_or_create(
            card=card, source_key=approval_source_key(card.source_card_key, identity),
            defaults=transaction_defaults(user, payload),
        )
    else:
        tx, created = existing, False
    if not created and (tx.is_canceled, tx.canceled_amount) != (payload["is_canceled"], payload["canceled_amount"]):
        tx.is_canceled, tx.canceled_amount = payload["is_canceled"], payload["canceled_amount"]
        tx.save(update_fields=["is_canceled", "canceled_amount", "updated_at"])
    return tx, created


def save_approvals(user, cards, card_rows, rows, run, fetched_at):
    new_ids, existing_count, reviews, unknown = [], 0, [], False
    occurrences = Counter()
    for raw in rows:
        row = dict(raw)
        shown, invalid, identity = row.pop("card_number"), row.pop("unknown_reason"), row.pop("identity")
        card_name = row.pop("card_name", None)
        # 다른 카드에 동일 승인 속성이 있어도 조회 순서에 따라 사용자 확인이 옮겨가지 않게 한다.
        # 번호는 해시에만 넣으며, 동일 행 회차는 전체 응답이 아닌 원천 번호별로 센다.
        occurrence_key = (shown, *identity[:-1])
        occurrences[occurrence_key] += 1
        key = approval_source_key(
            f"{run.connection.organization_code}:{shown}", (*identity[:-1], occurrences[occurrence_key]),
        )
        review = CardApprovalReview.objects.filter(
            user=user, issuer_code=run.connection.organization_code, source_key=key,
        ).select_related("transaction", "resolved_card").first()
        matched = [card for card, data in zip(cards, card_rows) if card_matches(data["number"], shown)]
        if invalid:
            unknown = True
            # 이전 정상 값으로 확인하지 못하게 한다. 원문·불확실한 금액은 저장하지 않는다.
            if review:
                review.data_valid = False
                review.started_at = run.started_at
                review.save(update_fields=["data_valid", "started_at"])
                reviews.append(review)
            continue
        if review is None and len(matched) != 1:
            review = CardApprovalReview(
                user=user, issuer_code=run.connection.organization_code, source_key=key,
                identity=identity,
            )
        if review is not None:
            review.payload = json.loads(json.dumps(row, cls=DjangoJSONEncoder))
            review.card_name = card_name
            review.data_valid = True
            review.started_at, review.fetched_at = run.started_at, fetched_at
            review.save()
            reviews.append(review)
            if review.resolved_card_id is None:
                continue
            # 재조회에서도 이 승인 한 건에 대한 사용자의 확인을 유지한다.
            card = review.resolved_card
            tx, created = save_transaction(user, card, identity, row, review.transaction)
            if review.transaction_id != tx.pk:
                review.transaction = tx
                review.save(update_fields=["transaction"])
        else:
            tx, created = save_transaction(user, matched[0], identity, row)
        if created:
            new_ids.append(tx.pk)
        else:
            existing_count += 1
    return new_ids, existing_count, reviews, unknown


def promote_coverage(user, issuer_code):
    """모든 확인이 끝나고 금액 오류가 없는 조회만 원래 실제 조회 기간으로 승격한다."""
    batches = CardApprovalCoverage.objects.filter(
        run__user=user, run__connection__organization_code=issuer_code,
        promoted_at=None, has_unknown_amount=False, run__status="success",
    ).select_related("run")
    for batch in batches:
        if batch.reviews.filter(transaction__isnull=True).exists() or batch.reviews.filter(data_valid=False).exists():
            continue
        run = batch.run
        for card in batch.cards.all():
            SyncRun.objects.create(
                user=user, connection=run.connection, card=card, product_code="card_approval_list",
                status="success", business_code="CF-00000", requested_from=run.requested_from,
                requested_to=run.requested_to, covered_from=batch.covered_from, covered_to=batch.covered_to,
                started_at=run.started_at, completed_at=run.completed_at,
            )
        batch.promoted_at = timezone.now()
        batch.save(update_fields=["promoted_at"])
        run.covered_from, run.covered_to = batch.covered_from, batch.covered_to
        run.save(update_fields=["covered_from", "covered_to"])


def approval_blocker(user, issuer_code):
    reviews = CardApprovalReview.objects.filter(user=user, issuer_code=issuer_code)
    if reviews.filter(data_valid=True, transaction__isnull=True).exists():
        return "card_assignment_required"
    if reviews.filter(data_valid=False).exists():
        return "spending_history_incomplete"
    latest = CardApprovalCoverage.objects.filter(
        run__user=user, run__connection__organization_code=issuer_code,
    ).order_by("-run__started_at").first()
    if latest and latest.has_unknown_amount:
        return "spending_history_incomplete"
    return None

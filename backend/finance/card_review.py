"""카드 식별 보완. 호출자는 회원 잠금을 잡는다. 확인 결과는 승인 한 건에만 적용한다."""
import json
from collections import Counter, defaultdict
from datetime import date, time

from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from .codef_cards import approval_source_key, card_matches
from .identifiers import identifier_key
from .models import Card, CardApprovalCoverage, CardApprovalReview, FinancialTransaction, SyncRun


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
    if not created:
        # 순액 계산 필드와 기관 근거를 같은 UPDATE로 저장한다. 금액이 이미 갱신된
        # 예전 데이터도 재조회 때 남아 있던 cancelStatus를 복구한다. 사용자 분류는 유지한다.
        detail = tx.source_detail if isinstance(tx.source_detail, dict) else {}
        latest = payload.get("source_detail") or {}
        updates = {"is_canceled": payload["is_canceled"], "canceled_amount": payload["canceled_amount"],
                   "source_detail": {**detail, **latest}}
        if not tx.merchant_category and payload.get("merchant_category"):
            updates["merchant_category"] = payload["merchant_category"]  # 업종을 받기 전에 저장한 거래
        changed = [key for key, value in updates.items() if getattr(tx, key) != value]
        if changed:
            for key in changed:
                setattr(tx, key, updates[key])
            tx.save(update_fields=[*changed, "updated_at"])
    return tx, created


def saved_approvals(user, card, identity):
    """재발급 전 카드가 보유 목록에 없어도 같은 카드사의 기존 승인을 찾는다."""
    owners = Card.objects.filter(user=user, issuer_code=card.issuer_code, source_card_key__isnull=False)
    keys = [approval_source_key(item.source_card_key, identity) for item in owners]
    return list(FinancialTransaction.objects.filter(user=user, card__in=owners, source_key__in=keys)[:2])


def review_signature(identity):
    """번호 표기·응답 순서와 무관한 승인 속성. 승인번호가 없으면 표기 간 자동 연결은 하지 않는다."""
    if len(identity) != 9 or not identity[2]:
        return None
    return tuple(value.isoformat() if isinstance(value, date) else value for value in identity[:-1])


def save_approvals(user, cards, card_rows, rows, run, fetched_at):
    def by_number(shown):
        return [card for card, data in zip(cards, card_rows) if card_matches(data["number"], shown)]

    used = {found[0].pk for found in map(by_number, (row["card_number"] for row in rows)) if len(found) == 1}

    def reissued_card(row):
        # main의 재발급 추정: 발급일을 모두 알고, 거래 이후 발급된 유일한 카드가 이번 응답에서 쓰였을 때만.
        if any(data["issued_on"] is None for data in card_rows):
            return []
        later = [card for card, data in zip(cards, card_rows) if row["occurred_on"] < data["issued_on"]]
        return later if len(later) == 1 and later[0].pk in used else []

    # 같은 속성의 다른 카드 거래나 반복 전표를 합치지 않도록 이번 응답과 기존 요청 양쪽의 유일성을 확인한다.
    counts = Counter(review_signature(row["identity"]) for row in rows)
    previous = defaultdict(list)
    for item in CardApprovalReview.objects.filter(
        user=user, issuer_code=run.connection.organization_code,
    ).select_related("transaction", "resolved_card"):
        previous[review_signature(item.identity)].append(item)

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
            identifier_key("card_shown", f"{run.connection.organization_code}:{shown}"),
            (*identity[:-1], occurrences[occurrence_key]),
        )
        review = CardApprovalReview.objects.filter(
            user=user, issuer_code=run.connection.organization_code, source_key=key,
        ).select_related("transaction", "resolved_card").first()
        signature = review_signature(identity)
        if review is None and signature is not None and counts[signature] == 1:
            candidates = previous[signature]
            if len(candidates) > 1 or (candidates and cards and len(saved_approvals(user, cards[0], identity)) > 1):
                unknown = True
                continue
            if candidates:
                review = candidates[0]
                # 요청 ID와 coverage 연결은 유지하고 표기별 조회 키만 최신으로 바꾼다.
                review.source_key = key
        matched = by_number(shown)
        if invalid:
            unknown = True
            # 이전 정상 값으로 확인하지 못하게 한다. 원문·불확실한 금액은 저장하지 않는다.
            if review:
                review.data_valid = False
                review.started_at = run.started_at
                review.save(update_fields=["source_key", "data_valid", "started_at"])
                reviews.append(review)
            continue
        saved = []
        # 이미 확인을 요청했거나 사용자가 고른 거래는 새 발급일 정보로 자동 변경하지 않는다.
        if review is None and not matched:
            matched = reissued_card(row)
            if matched:
                saved = saved_approvals(user, matched[0], identity)
                if len(saved) > 1:
                    # 기존 승인도 하나로 특정할 수 없으면 중복 생성과 조회 완료 승격을 모두 보류한다.
                    unknown = True
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
            existing = review.transaction
            if existing is None:
                existing = FinancialTransaction.objects.filter(
                    user=user, card=card, source_key=approval_source_key(card.source_card_key, identity),
                ).first()
                if existing and CardApprovalReview.objects.filter(transaction=existing).exclude(pk=review.pk).exists():
                    unknown = True
                    continue
            tx, created = save_transaction(user, card, identity, row, existing)
            if review.transaction_id != tx.pk:
                review.transaction = tx
                review.save(update_fields=["transaction"])
        else:
            tx, created = save_transaction(user, matched[0], identity, row, saved[0] if saved else None)
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

"""카드 식별 보완. 호출자는 회원 잠금을 잡는다. 확인 결과는 승인 한 건에만 적용한다."""
import json
from collections import Counter, defaultdict
from datetime import date, time

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Q
from django.utils import timezone

from .card_foreign import WonFinder
from .codef_cards import FOREIGN_PENDING, PARTIAL, approval_source_key, card_matches
from .models import Card, CardApprovalCoverage, CardApprovalReview, FinancialTransaction, SyncRun, TransactionLink
from .transfer_rules import LINK_STATE, link

# 금액을 몰라 저장하지 못한 외화 승인 표시. 승인 한 건당 한 줄이고 requested_from 이 사용일이다.
# 조회 기록이 아니라서 status 를 success 로 두지 않는다 (조회 범위 계산에 들어가지 않게)
FOREIGN_PENDING_RUN = "card_foreign_pending"


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


def save_transaction(user, card, identity, payload, existing=None, card_amount=True):
    """card_amount: payload 금액이 카드사가 준 원화 금액인지 (False 면 card_foreign 이 청구서 · 출금에서 찾은 금액이거나 저장된 금액)"""
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
        states = tx.field_states if isinstance(tx.field_states, dict) else {}
        if card_amount and "amount" in states and payload["amount"] is not None:
            # 11/10 PR #98 검수: 청구서 · 출금으로 채운 외화 승인에 카드사 원화 금액이 오면 그 금액으로 바꾼다.
            # 다른 카드 거래와 같은 기준(카드사 금액)이고, 채운 근거 표시는 지운다
            updates["amount"] = payload["amount"]
            updates["field_states"] = {key: value for key, value in states.items() if key != "amount"}
        changed = [key for key, value in updates.items() if getattr(tx, key) != value]
        if changed:
            for key in changed:
                setattr(tx, key, updates[key])
            tx.save(update_fields=[*changed, "updated_at"])
        if "amount" in changed:
            # 금액이 달라진 출금과는 같은 구매라는 근거가 없다 → 규칙이 이은 연결을 풀어 출금을 다시 보인다
            for row in TransactionLink.objects.filter(
                Q(from_transaction=tx) | Q(to_transaction=tx),
                relation_type=TransactionLink.Relation.SAME_PURCHASE, confirmation_state=LINK_STATE,
            ).select_related("from_transaction", "to_transaction"):
                other = row.to_transaction if row.from_transaction_id == tx.pk else row.from_transaction
                if other.amount != tx.amount:
                    row.delete()
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

    def foreign_card(row):
        """원화 없는 외화 승인의 카드 · 이미 저장된 거래. 카드는 원화 승인과 같은 방식(번호 → 재발급 추정)으로 찾는다"""
        found = by_number(row["card_number"]) or reissued_card(row)
        if len(found) != 1:
            return None, None
        saved = saved_approvals(user, found[0], row["identity"])
        return (found[0], saved[0]) if len(saved) == 1 else ((None, None) if saved else (found[0], None))

    new_ids, existing_count, reviews, unknown, foreign, filled = [], 0, [], False, [], 0
    finder = WonFinder(user, run.connection.organization_code, rows, foreign_card)
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
        if invalid is None and row["amount"] == 0:
            # 11/09: 0원 승인(카드 확인용 0달러 승인 등)은 쓴 돈이 없어 저장 · 카드 확인 · 분류하지 않는다.
            # 실데이터에서 같은 날 같은 가게의 실제 결제 옆에 0원 줄이 따로 보여 분류 확인만 늘렸다
            continue
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
        won = None
        if invalid == FOREIGN_PENDING and review is None:
            # 11/09: 같은 결제의 청구서 항목 · 체크카드 계좌 출금에 원화 금액이 있으면 그 금액으로 저장한다 (card_foreign)
            won = finder.won(identity)
            if row["source_detail"].get("cancelStatus") == PARTIAL:
                won = None   # 부분취소 금액이 외화일 수 있어 원화 순액을 모른다 → 저장된 거래를 바꾸지 않는다
            if won is None:
                if row["is_canceled"]:
                    continue   # 취소 · 거절이고 저장된 거래도 없으면 쓴 돈이 없어 알릴 것이 없다 (PR #98 리뷰)
                # 다시 조회해도 풀리지 않아 온보딩이 막히지 않게 조회 완료로 둔다. 금액을 만들지 않고 저장도 하지 않는다.
                # 청구서에 든 이 결제는 짝 거래가 없어 선차감에서 빼지 않으므로 카드값이 적게 잡히지 않는다 (cycle_overlap)
                foreign.append(row["occurred_on"])
                continue
            invalid = None
            # 취소 금액(resCancelAmount)도 외화로 올 수 있어 쓰지 않는다. 취소 · 거절이면 원화 전액 취소
            row["amount"], row["canceled_amount"] = won.amount, won.amount if row["is_canceled"] else None
            if not isinstance(won, FinancialTransaction):
                row["field_states"] = {"amount": {"source": won.sources}}
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
            tx, created = save_transaction(user, card, identity, row, existing, card_amount=won is None)
            if review.transaction_id != tx.pk:
                review.transaction = tx
                review.save(update_fields=["transaction"])
        else:
            tx, created = save_transaction(user, matched[0], identity, row, saved[0] if saved else None,
                                           card_amount=won is None)
        if created and won is not None and not isinstance(won, FinancialTransaction):
            filled += 1
            if won.withdrawal is not None:
                # 소비는 카드 줄이 맡고 계좌 줄은 목록에서 뺀다. 이름이 잘려 달라도 잇는다 (link_same_purchases 는 이름이 같아야 함)
                link(user, tx, won.withdrawal, TransactionLink.Relation.SAME_PURCHASE, tx.amount)
        if created:
            new_ids.append(tx.pk)
        else:
            existing_count += 1
    return new_ids, existing_count, reviews, unknown, foreign, filled


def record_foreign_pending(user, connection, run, covered, days, fetched_at):
    """이번 조회 범위의 표시를 새 결과로 바꾼다. 범위가 겹치는 재조회로 같은 승인이 두 번 세이지 않고, 원화 금액이 오면 표시가 사라진다"""
    SyncRun.objects.filter(
        user=user, connection__organization_code=connection.organization_code, product_code=FOREIGN_PENDING_RUN,
        requested_from__gte=covered[0], requested_from__lte=covered[1],
    ).delete()
    SyncRun.objects.bulk_create(SyncRun(
        user=user, connection=connection, product_code=FOREIGN_PENDING_RUN, status="skipped",
        requested_from=day, requested_to=day, started_at=run.started_at, completed_at=fetched_at,
    ) for day in days)


def foreign_pending_count(user, issuer_code, start, end):
    """start ~ end 에 쓴 외화 승인 중 금액을 몰라 저장하지 못한 건수"""
    return SyncRun.objects.filter(
        user=user, connection__organization_code=issuer_code, product_code=FOREIGN_PENDING_RUN,
        requested_from__gte=start, requested_from__lte=end,
    ).count()


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

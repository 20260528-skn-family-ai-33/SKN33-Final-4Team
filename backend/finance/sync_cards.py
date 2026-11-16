"""카드사의 보유카드 · 청구서 · 승인내역을 저장한다 (대출 sync_loans 와 같은 방식).

- 다시 불러오기(api_sync.card_syncer)가 부른다. 약속한 모양은 docs/api/finance-connect.md 11번.
- 첫 청구서 선차감(budgets/protect.py P-12)이 아래 조회 기록으로 '자료가 다 들어왔는지'를 판단한다 (10/28 나은):
  · 청구서: product_code=card_bill_list, 카드마다 · 달마다, 그 달 1일 ~ 말일(이번 달은 조회일까지). 다음 달 청구서는 기록 없음
  · 승인내역: product_code=card_approval_list, 카드마다, 카드사가 실제로 조회해 준 기간
  · 카드만 특정 못 한 승인은 별도 보관하고 사용자 확인 후 실제 조회 범위를 승격한다.
  · 금액·취소 정보가 불확실한 조회는 카드 확인만으로 승격하지 않는다.
- 저장 뒤 청구서의 납부 예정을 맞추고 카드값 출금을 다시 맞춘다 (card_payments.refresh_bill_dues, R04).
"""
import re

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from config import tasks
from users.models import Consent

from . import card_payments, codef
from .codef_cards import MONTH, CardDataError, month_range
from .identifiers import LegacyIdentifierKey, legacy_identifier_key
from .models import Card, CardBill, CardBillItem, CardApprovalCoverage, Connection, FinancialTransaction, Origin, SyncRun
from .card_review import promote_coverage, record_foreign_pending, save_approvals
from .pipeline import RETRY_STATE_KEY, process_new_transactions, retry_failed_transactions

CARD_RUN = "card_list"                     # 카드별 성공 기록 (sync_loans 의 loan_transaction_list 와 같은 쓰임)
BILL_RUN = "card_bill_list"                # budgets.protect.CARD_BILL_SYNC
APPROVAL_RUN = "card_approval_list"        # budgets.protect.CARD_SPENDING_SYNC
BILL_STARTED_AT = "codefStartedAt"         # card_bill.field_states: 이 값을 저장한 조회의 시작 시각
LIST_LOAD_RUN = "card_list_load"           # 첫 연결 때 보유카드만 먼저 받은 조회 (11/09, sync_card_list)


class CardConsentRequired(PermissionError):
    code = "consent_required"


class CardConnectionGone(ValueError):
    """지정한 연결이 이미 해제됨 → 새 연결을 만들지 않고 멈춤 (sync.BankConnectionGone 과 같음)"""
    code = "connection_changed"


class CardConnectionChanged(ValueError):
    code = "connection_changed"


def _check_consent(user, connection):
    # sync_loans._check_consent 와 같은 기준 (회원 전체 동의 또는 이 연결 동의)
    if not Consent.objects.filter(
        Q(connection__isnull=True) | Q(connection=connection),
        user=user, purpose="finance_inquiry", product_scope="card", revoked_at__isnull=True,
        granted_at__lte=timezone.now(),
    ).exists():
        raise CardConsentRequired("카드 조회 동의가 필요해요.")


def _reusable(model, user, connection, org):
    # 같은 회원 · 카드사의 이전(해제된) 연결 행을 옮겨 ID · 선택 · 최초 연결 시각 · 납부 연결을 보존한다.
    return model.objects.select_for_update(of=("self",)).filter(
        Q(connection=connection) | Q(connection__disconnected_at__isnull=False),
        user=user, connection__user=user, connection__business_type="card",
        connection__organization_code=org,
    )


def _save_cards(user, connection, org, cards, run, fetched_at):
    """→ (카드 목록 (응답 순서, 저장된 Card), 건수)"""
    counts, saved = {"created": 0, "updated": 0, "skipped": 0}, []
    for data in cards:
        try:
            card = _reusable(Card, user, connection, org).get(source_card_key=data["source_card_key"])
        except Card.DoesNotExist:
            if Card.objects.filter(user=user, source_card_key=legacy_identifier_key(data["number"])).exists():
                # 승인 거래 키가 카드 키에 묶여 있어 키만 바꾸면 거래가 두 번 저장된다 → 멈추고 동의 철회 후 다시 연결 (PR #102 리뷰)
                raise LegacyIdentifierKey() from None
            card = Card(user=user, connection=connection, source_card_key=data["source_card_key"],
                        origin=Origin.PROVIDER)
        except Card.MultipleObjectsReturned:
            raise CardDataError("ambiguous_card_identity") from None
        saved.append(card)
        if not card._state.adding:
            if card.origin != Origin.PROVIDER:
                raise CardDataError("card_origin_conflict")
            # 더 늦게 시작한 조회가 이미 저장했으면 덮지 않는다 (시작 시각끼리 비교, sync_loans 와 같음)
            last_started_at = card.sync_runs.filter(
                user_id=card.user_id, product_code=CARD_RUN, status="success",
            ).order_by("-started_at").values_list("started_at", flat=True).first()
            if last_started_at and last_started_at > run.started_at:
                counts["skipped"] += 1
                continue
        card.connection, card.issuer_code = connection, org
        for field in ("masked_number", "name", "card_type", "card_status"):
            setattr(card, field, data[field])
        card.fetched_at = fetched_at
        if card._state.adding:
            card.save(force_insert=True)   # 최초 연결 시각은 Card.save 가 처음 만들 때 한 번만 기록
            counts["created"] += 1
        else:
            card.save(update_fields=[
                "connection", "issuer_code",
                "masked_number", "name", "card_type", "card_status", "fetched_at",
            ])
            counts["updated"] += 1
        SyncRun.objects.create(
            user=user, connection=connection, card=card, product_code=CARD_RUN, status="success",
            business_code=codef.SUCCESS, started_at=run.started_at, completed_at=fetched_at,
        )
    return saved, counts


def _save_items(user, bill, items, fetched_at):
    """상세 목록을 원천 항목 키로 맞춘다. 이번 응답에 없는 항목은 지운다 (상세를 못 받았으면 부르지 않음)"""
    existing = {row.source_item_key: row for row in bill.items.select_for_update()}
    for data in items:
        row = existing.pop(data["source_item_key"], None) or CardBillItem(
            user=user, bill=bill, source_item_key=data["source_item_key"],
        )
        for field, value in data.items():
            setattr(row, field, value)
        row.fetched_at = fetched_at
        row.save()
    CardBillItem.objects.filter(pk__in=[row.pk for row in existing.values()]).delete()


def _save_bills(user, connection, org, bills, run, fetched_at):
    counts = {"created": 0, "updated": 0, "skipped": 0}
    for data in bills:
        try:
            bill = _reusable(CardBill, user, connection, org).get(
                issuer_code=org, statement_key=data["statement_key"],
            )
        except CardBill.DoesNotExist:
            bill = CardBill(user=user, connection=connection, issuer_code=org,
                            statement_key=data["statement_key"], source_type=Origin.PROVIDER)
        except CardBill.MultipleObjectsReturned:
            raise CardDataError("ambiguous_bill_identity") from None
        states = dict(bill.field_states or {})
        if not bill._state.adding:
            if bill.source_type != Origin.PROVIDER:
                raise CardDataError("bill_origin_conflict")
            saved = states.get(BILL_STARTED_AT)
            if saved and saved > run.started_at.isoformat():
                counts["skipped"] += 1
                continue
        states[BILL_STARTED_AT] = run.started_at.isoformat()
        states["codefBillType"] = data["bill_type"]   # 선차감: 같은 날 명세서 중 카드론 명세서 빼기 (card_deduction.bill_kind)
        bill.connection = connection
        # 명세서 단위 청구라 카드사 통합. 카드별로 나누지 않고 선차감도 카드사 단위로 계산한다 (10/29 나은)
        bill.card, bill.scope_type = None, CardBill.ScopeType.ISSUER
        # 확정 / 예상 구분은 CODEF 가 주지 않아 비워 둔다 (cards.md Z)
        for field in ("billing_month", "total_amount", "payment_due_date", "withdrawal_due_date"):
            setattr(bill, field, data[field])
        if data["items"] is not None or bill._state.adding:
            bill.items_state = data["items_state"]   # 이번에 상세를 못 받았으면 지난번 상세와 상태를 그대로 둔다
        bill.field_states, bill.fetched_at = states, fetched_at
        counts["created" if bill._state.adding else "updated"] += 1
        bill.save()
        if data["items"] is not None:
            _save_items(user, bill, data["items"], fetched_at)
    return counts


def sync_card_list(user, connected_id, org, connection_id, card_no=None, card_password=None):
    """보유카드만 먼저 저장한다 (11/09 첫 연결). 카드 선택 화면이 저장된 카드만 읽어서 연결 응답 전에 받아 둔다.
    청구서 · 승인내역은 뒤 작업(sync_card)이 받는다 — 업종까지 받으면 1분을 넘겨 온보딩이 멈춰 보였다.
    저장 순서 · 확인은 sync_card 와 같다 (외부 조회는 트랜잭션 밖, 저장 직전 연결 상태 · 동의 · 조회 권한 확인)"""
    connection = Connection.objects.filter(
        pk=connection_id, user=user, business_type="card", organization_code=org,
    ).first()
    if connection is None or connection.disconnected_at is not None:
        raise CardConnectionGone("연결이 해제되어 불러오지 않았어요.")
    _check_consent(user, connection)
    run = tasks.start_run(user, None, connection=connection, product_code=LIST_LOAD_RUN)
    try:
        card_rows = codef.list_cards(connected_id, org, card_no, card_password)
        fetched_at = timezone.now()
        with transaction.atomic():
            get_user_model().objects.select_for_update(no_key=True).get(pk=user.pk)
            connection = Connection.objects.select_for_update().get(pk=connection.pk, user=user)
            if connection.disconnected_at is not None or connection.status != "connected":
                raise CardConnectionChanged("조회 중 연결 상태가 바뀌어 저장하지 않았어요.")
            _check_consent(user, connection)
            tasks.claim_run(run, None)
            _cards, counts = _save_cards(user, connection, org, card_rows, run, fetched_at)
            run.status, run.business_code, run.completed_at = "success", codef.SUCCESS, timezone.now()
            run.save(update_fields=["status", "business_code", "completed_at"])
        return {"card_count": len(card_rows), "created_card_count": counts["created"]}
    except Exception as exc:
        business_code = None
        if isinstance(exc, codef.CodefError) and re.fullmatch(r"CF-[0-9]{5}", str(exc.code)):
            business_code = exc.code
        error_code = getattr(exc, "code", None) if isinstance(
            exc, (CardDataError, CardConsentRequired, CardConnectionChanged, tasks.WorkExpired, LegacyIdentifierKey)) else None
        tasks.finish_run(run.pk, status="failed", business_code=business_code,
                         error_code=error_code or ("provider_error" if isinstance(exc, codef.CodefError) else "card_sync_failed"))
        raise


def sync_card(user, connected_id, org, start, end, bill_months, card_no=None, card_password=None, connection_id=None,
              task_id=None):
    """신뢰된 서버 호출 전용. 보유카드 · 청구서(bill_months 의 달마다) · 승인내역(start ~ end)을 조회한 뒤
    한 트랜잭션으로 저장한다.

    - connection_id: 이 연결에만 저장한다. 해제됐으면 새 연결을 만들지 않고 CardConnectionGone.
      없으면 이 카드사의 연결 중인 연결을 쓴다 (개발용).
    - 카드 조회 동의는 외부 조회 전 · 저장 트랜잭션 안에서 확인한다 (철회되면 CardConsentRequired = PermissionError)
    - card_no · card_password 는 CODEF 요청에만 싣는다 (저장 · 로그 · 예외 메시지 금지)
    - task_id: 이 조회를 시킨 작업. 저장 직전 작업 · 조회 기록의 유효 기간을 본다 (config/tasks.py claim_run)
    - CODEF 실패는 codef.CodefError 를 그대로 올린다
    """
    months = list(bill_months or ())
    if not connected_id or start > end:
        raise ValueError("연결 ID와 조회 기간을 확인해 주세요.")
    if not months or any(not isinstance(month, str) or not MONTH.fullmatch(month) for month in months):
        raise ValueError("청구년월은 YYYYMM 이어야 해요.")
    if connection_id is None:
        connection = Connection.objects.get(
            user=user, business_type="card", organization_code=org,
            disconnected_at=None, status="connected",
        )
    else:
        connection = Connection.objects.filter(
            pk=connection_id, user=user, business_type="card", organization_code=org,
        ).first()
        if connection is None or connection.disconnected_at is not None:
            raise CardConnectionGone("연결이 해제되어 불러오지 않았어요.")
    _check_consent(user, connection)
    run = tasks.start_run(user, task_id, connection=connection, product_code="card_sync",
                          requested_from=start, requested_to=end)
    try:
        # 외부 조회를 모두 마친 뒤 저장 트랜잭션을 연다.
        card_rows = codef.list_cards(connected_id, org, card_no, card_password)
        bills = [
            bill for month in months
            for bill in codef.list_card_bills(connected_id, org, month, card_no, card_password)
        ]
        approvals, covered = codef.list_card_approvals(connected_id, org, start, end, card_no, card_password)
        fetched_at = timezone.now()
        with transaction.atomic():
            # main 의 저장 · 동의 변경과 같은 순서: 회원 → 연결 → 카드 → 청구서 → 거래
            get_user_model().objects.select_for_update(no_key=True).get(pk=user.pk)
            connection = Connection.objects.select_for_update().get(pk=connection.pk, user=user)
            if connection.disconnected_at is not None or connection.status != "connected":
                raise CardConnectionChanged("조회 중 연결 상태가 바뀌어 저장하지 않았어요.")
            _check_consent(user, connection)
            tasks.claim_run(run, task_id)
            if SyncRun.objects.filter(
                user=user, connection=connection, product_code="card_sync", status="success",
                started_at__gt=run.started_at,
            ).exists():
                raise CardConnectionChanged("더 최근 조회가 저장되어 이전 응답은 반영하지 않았어요.")
            cards, card_counts = _save_cards(user, connection, org, card_rows, run, fetched_at)
            bill_counts = _save_bills(user, connection, org, bills, run, fetched_at)

            # 조회 기간 밖의 실패도 재시도하되 이번에 생긴 거래는 제외한다 (sync_bank 와 같음)
            retry_ids = list(FinancialTransaction.objects.filter(
                user=user, source_kind="card", card__connection=connection,
                field_states__has_key=RETRY_STATE_KEY,
            ).values_list("pk", flat=True))
            new_ids, existing_count, reviews, unknown, foreign, filled = save_approvals(
                user, cards, card_rows, approvals, run, fetched_at,
            )
            record_foreign_pending(user, connection, run, covered, foreign, fetched_at)
            retry_failed_transactions(user, retry_ids)
            process_new_transactions(user, new_ids)
            # 청구서 → 납부 예정 (R04). 승인 거래가 새로 없어도 돈다. 저장 트랜잭션 · 회원 잠금 안
            card_payments.refresh_bill_dues(user, org)

            for card in cards:
                for month in months:
                    bill_from, bill_to = month_range(month, end)
                    if bill_from > bill_to:
                        continue  # 다음 달 청구서(결제 예정)는 받기만 하고, 아직 오지 않은 날을 조회했다고 남기지 않는다
                    SyncRun.objects.create(
                        user=user, connection=connection, card=card, product_code=BILL_RUN, status="success",
                        business_code=codef.SUCCESS, requested_from=bill_from, requested_to=bill_to,
                        covered_from=bill_from, covered_to=bill_to, started_at=run.started_at, completed_at=fetched_at,
                    )
            run.status, run.business_code, run.completed_at = "success", codef.SUCCESS, timezone.now()
            run.save(update_fields=["status", "business_code", "completed_at"])
            coverage = CardApprovalCoverage.objects.create(
                run=run, covered_from=covered[0], covered_to=covered[1], has_unknown_amount=unknown,
            )
            coverage.cards.set(cards)
            coverage.reviews.set(reviews)
            promote_coverage(user, org)
            coverage.refresh_from_db(fields=["promoted_at"])
            complete = coverage.promoted_at is not None
        return {
            "card_count": len(card_rows), "created_card_count": card_counts["created"],
            "updated_card_count": card_counts["updated"], "skipped_card_count": card_counts["skipped"],
            "bill_count": len(bills), "created_bill_count": bill_counts["created"],
            "updated_bill_count": bill_counts["updated"], "skipped_bill_count": bill_counts["skipped"],
            "new_transaction_count": len(new_ids), "existing_transaction_count": existing_count,
            "approvals_complete": complete, "foreign_pending_count": len(foreign), "foreign_filled_count": filled,
            "card_review_count": sum(review.transaction_id is None and review.data_valid for review in reviews),
        }
    except Exception as exc:
        # 기관 오류의 message/extra 에는 개인정보가 섞일 수 있어 저장하지 않는다.
        business_code = None
        if isinstance(exc, codef.CodefError) and re.fullmatch(r"CF-[0-9]{5}", str(exc.code)):
            business_code = exc.code
        if isinstance(exc, (CardDataError, CardConsentRequired, CardConnectionChanged, tasks.WorkExpired, LegacyIdentifierKey)):
            error_code = exc.code
        elif isinstance(exc, codef.CodefError):
            error_code = "provider_error"
        else:
            error_code = "card_sync_failed"
        tasks.finish_run(run.pk, status="failed", business_code=business_code, error_code=error_code)
        raise

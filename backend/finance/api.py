"""
finance 앱 API — 거래 (docs/api/transactions.md)
- 16. GET   /api/finance/transactions/       거래 목록
- 17. GET   /api/finance/transactions/<id>/  거래 한 건 보기
- 18. PATCH /api/finance/transactions/<id>/  거래 분류 수정
"""
from datetime import date

from django.db.models import Exists, F, OuterRef, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from budgets.models import Category

from . import mock
from .display import account_display, card_display
from .inputs import body_dict, reject_unknown_fields
from .models import FinancialTransaction, SourceKind, TransactionLink

Tx = FinancialTransaction
UNCLASSIFIED = "UNCLASSIFIED"
# 목록에 나오는 거래: 소비 · 입금(월급 등) · 남에게 보낸 이체 (10/22 V①)
# 내 계좌끼리 이체 · 저축 · 대출 상환 · 카드값은 넣지 않는다
LIST_TYPES = (Tx.TransactionType.SPENDING, Tx.TransactionType.INCOME, Tx.TransactionType.TRANSFER)
PERIOD_ERROR = "기간을 다시 골라 주세요."


# ---------- 목록에 나오는 거래 고르기 (17번 '한 건 보기'도 같은 기준을 쓴다) ----------

def listed_transactions(user):
    """이 회원의 거래 중 목록에 나오는 것만.
    체크카드 결제처럼 카드 승인 + 계좌 출금이 같은 구매(SAME_PURCHASE)로 이어져 있으면
    계좌 출금 쪽은 빼서 한 번만 센다 (10/22 B)"""
    same_purchase = TransactionLink.objects.filter(
        Q(from_transaction=OuterRef("pk")) | Q(to_transaction=OuterRef("pk")),
        relation_type=TransactionLink.Relation.SAME_PURCHASE,
    )
    return (
        Tx.objects.filter(user=user, transaction_type__in=LIST_TYPES)
        .annotate(in_same_purchase=Exists(same_purchase))
        .exclude(source_kind=SourceKind.BANK, in_same_purchase=True)
        # 화면 한 줄에 필요한 계좌 · 카드 · 카테고리 · 고정지출 정보를 미리 같이 가져온다 (거래마다 DB를 또 부르지 않게)
        .select_related("account__connection", "card__connection", "confirmed_category")
        .prefetch_related("allocations", "payment_matches__due")
    )


# ---------- 거래 1건 → 응답 한 줄 ----------

def cancel_info(tx):
    """취소 정보 {state: none · partial · full, amount: 취소된 금액}"""
    canceled = tx.canceled_amount if tx.canceled_amount is not None else (tx.amount if tx.is_canceled else 0)
    canceled = min(canceled, tx.amount)
    if canceled <= 0:
        return {"state": "none", "amount": 0}
    return {"state": "full" if canceled >= tx.amount else "partial", "amount": canceled}


def fixed_expense_id(tx):
    """이 거래가 납부로 연결된 고정지출 ID (★8 · 13). 없으면 None"""
    for match in tx.payment_matches.all():
        if match.due.obligation_type == "recurring":
            return match.due.recurring_id
    return None


def transaction_item(tx):
    """목록의 한 줄. 입금 · 이체는 카테고리 · 분류 상태 · 돈의 출처가 모두 None (V①)"""
    is_spending = tx.transaction_type == Tx.TransactionType.SPENDING
    cancel = cancel_info(tx)
    category = tx.confirmed_category if is_spending else None
    fixed_id = fixed_expense_id(tx) if is_spending else None

    if not is_spending:
        classify_state = funding = None
    else:
        # 확인 필요 카테고리면 상태도 항상 needs_review
        classify_state = "needs_review" if category.category_code == UNCLASSIFIED else tx.confirmation_state
        is_fixed = fixed_id is not None or any(a.funding_bucket == "fixed" for a in tx.allocations.all())
        funding = "fixed" if is_fixed else "living"

    return {
        "transaction_id": tx.transaction_id,
        "occurred_on": tx.occurred_on,
        "occurred_time": tx.occurred_time.strftime("%H:%M") if tx.occurred_time else None,
        "direction": tx.direction,
        "transaction_type": tx.transaction_type,
        # 표시 이름: 카드 = 가맹점명, 계좌 = 적요
        "merchant_name": (
            (tx.merchant_name or tx.source_description) if tx.source_kind == SourceKind.CARD
            else (tx.source_description or tx.merchant_name)
        ) or "",
        "amount": tx.amount,
        "cancel": cancel,
        "net_amount": tx.amount - cancel["amount"],
        "category": category.category_code if category else None,
        "category_name": category.display_name if category else None,
        "classify_state": classify_state,
        # 카드 거래는 카드만, 계좌 거래는 계좌만 채운다
        "payment_card": card_display(tx.card) if tx.source_kind == SourceKind.CARD else None,
        "payment_account": account_display(tx.account) if tx.source_kind == SourceKind.BANK else None,
        "funding": funding,
        "fixed_expense_id": fixed_id,
    }


# ---------- 16. 거래 목록 ----------

def _parse_day(value):
    """'2026-10-01' → date. 안 보냈으면 None, 형식이 틀리면 400"""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValidationError({"from": [PERIOD_ERROR]})


@api_view(["GET"])
@permission_classes([IsAuthenticated])  # 로그인 안 하면 403 not_authenticated
def transactions(request):
    """16. GET /api/finance/transactions/?category=FOOD&from=2026-10-01&to=2026-10-31
    - 기간: 달력월 기준. from 없으면 이번 달 1일, to 없으면 오늘
      단 category=UNCLASSIFIED에 기간을 안 보내면 전체 기간 (H-03, 홈 '확인할 거래 수'와 같은 숫자)
    - 정렬: 최신순 (거래일 → 거래 시각, 시각이 없으면 같은 날의 맨 뒤)"""
    params = request.query_params
    category = params.get("category") or None
    if category and not Category.objects.filter(category_code=category).exists():
        raise ValidationError({"category": ["모르는 카테고리예요."]})

    period_sent = bool(params.get("from") or params.get("to"))
    start, end = _parse_day(params.get("from")), _parse_day(params.get("to"))
    today = timezone.localdate()
    if start is None and category != UNCLASSIFIED:
        start = today.replace(day=1)
    end = end or today
    if start and start > end:
        raise ValidationError({"from": [PERIOD_ERROR]})
    basis = "custom" if period_sent else ("all" if category == UNCLASSIFIED else "month")

    rows = listed_transactions(request.user).filter(occurred_on__lte=end)
    if start:
        rows = rows.filter(occurred_on__gte=start)
    if category:
        # 카테고리를 걸면 소비 거래만 (입금 · 이체는 카테고리가 없음)
        rows = rows.filter(transaction_type=Tx.TransactionType.SPENDING, confirmed_category_id=category)
    rows = rows.order_by("-occurred_on", F("occurred_time").desc(nulls_last=True), "-created_at")

    items = [transaction_item(tx) for tx in rows]
    spending = [item for item in items if item["transaction_type"] == Tx.TransactionType.SPENDING]
    if start is None:
        # 전체 기간: 시작일 = 가장 오래된 거래일 (없으면 None)
        start = min((item["occurred_on"] for item in items), default=None)

    return Response({
        "period": {"from": start, "to": end, "basis": basis},
        "summary": {
            # 이번 응답의 소비 거래 소계 (입금 · 이체 제외). 홈 · 예산의 사용액과는 다른 값
            "spent_total": sum(item["net_amount"] for item in spending),
            "needs_review_count": sum(1 for item in spending if item["category"] == UNCLASSIFIED),
        },
        "items": items,
    })


# ---------- 17 · 18. 거래 한 건 보기 · 분류 수정 ----------

CATEGORY_ERROR = "카테고리를 다시 골라 주세요."
INCOME_ERROR = "이 거래는 카테고리를 고를 수 없어요."
FIXED_TRANSFER_ERROR = "고정지출에 연결된 거래예요. 고정지출 연결을 먼저 풀어 주세요."
ORIGINAL_TYPE = "original_transaction_type"  # field_states 에 남기는 '원래 이체였음' 표시 (10/24 AA⑥)


def suggestion_info(tx):
    """서비스가 제안한 분류 {candidates, reason}. 사용자가 확정했거나 소비가 아니면 None
    후보의 확신도 숫자는 화면에 보내지 않는다 (10/22 V)"""
    if tx.transaction_type != Tx.TransactionType.SPENDING or tx.confirmation_state == Tx.ConfirmationState.CONFIRMED:
        return None
    codes = [c.get("category") for c in (tx.suggestion_candidates or []) if c.get("category")]
    if not codes and tx.suggested_category_id:
        codes = [tx.suggested_category_id]
    names = dict(Category.objects.filter(category_code__in=codes).values_list("category_code", "display_name"))
    return {
        "candidates": [{"category": code, "category_name": names[code]} for code in codes if code in names],
        "reason": tx.suggestion_reason,
    }


def transaction_detail_body(tx):
    """거래 한 건 = 목록 한 줄 + 상세 칸"""
    return {
        **transaction_item(tx),
        # 할부 개월: 카드 거래만 (일시불 0). 계좌 거래는 None
        "installment_months": tx.installment_months if tx.source_kind == SourceKind.CARD else None,
        "suggestion": suggestion_info(tx),
        "classified_by": tx.classification_source,
        "updated_at": tx.updated_at,
    }


def is_fixed_linked(tx):
    """고정지출에 연결됐거나 고정비로 충당된 거래인지"""
    return fixed_expense_id(tx) is not None or any(a.funding_bucket == "fixed" for a in tx.allocations.all())


def _apply_category(tx, code):
    """18번 저장 규칙. 고칠 칸 목록을 돌려준다 (틀리면 400)"""
    states = dict(tx.field_states or {})
    if code is None:
        # null = '원래 이체였던 거래'를 다시 이체로 돌리기 (10/22 W). 그 밖의 거래는 400
        if tx.transaction_type == Tx.TransactionType.TRANSFER:
            return []  # 이미 이체 — 같은 결과로 성공
        if states.get(ORIGINAL_TYPE) != Tx.TransactionType.TRANSFER:
            raise ValidationError({"category": [CATEGORY_ERROR]})
        if is_fixed_linked(tx):
            raise ValidationError({"category": [FIXED_TRANSFER_ERROR]})  # 10/24 AA⑤
        states.pop(ORIGINAL_TYPE)
        tx.transaction_type = Tx.TransactionType.TRANSFER
        # 이체는 카테고리가 없지만 DB 칸은 비울 수 없어서 '확인 필요'로 둔다 (목록 응답에서는 None)
        tx.confirmed_category_id = UNCLASSIFIED
        tx.confirmation_state = Tx.ConfirmationState.NEEDS_REVIEW
        tx.classification_source = None
        tx.field_states = states
        return ["transaction_type", "confirmed_category", "confirmation_state", "classification_source", "field_states"]

    # 고를 수 있는 카테고리 = 10개 + 기타 (UNCLASSIFIED · 숨긴 카테고리는 안 됨)
    if (not isinstance(code, str) or code == UNCLASSIFIED
            or not Category.objects.filter(category_code=code, is_active=True).exists()):
        raise ValidationError({"category": [CATEGORY_ERROR]})
    changed = ["confirmed_category", "confirmation_state", "classification_source"]
    if tx.transaction_type == Tx.TransactionType.TRANSFER:
        # 이체를 소비로 바꾸기 (10/22 W). 원래 이체였다는 표시를 남긴다 (AA⑥)
        states[ORIGINAL_TYPE] = Tx.TransactionType.TRANSFER
        tx.transaction_type = Tx.TransactionType.SPENDING
        tx.field_states = states
        changed += ["transaction_type", "field_states"]
    tx.confirmed_category_id = code
    tx.confirmation_state = Tx.ConfirmationState.CONFIRMED
    # 사용자 확정 → 이후 같은 가맹점의 새 거래는 이 카테고리를 먼저 쓴다 (merchant_rules.remembered_category)
    tx.classification_source = Tx.ClassificationSource.USER
    return changed


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def transaction_detail(request, transaction_id):
    """17. GET  /api/finance/transactions/<id>/ — 거래 한 건 보기
    18. PATCH /api/finance/transactions/<id>/ — 거래 분류 수정 {"category": "FOOD" | null}
    없는 거래 · 남의 거래 · 목록에 안 나오는 거래(같은 구매로 빠진 계좌 출금, 카드값 등)는 모두 404 (★3)"""
    tx = get_object_or_404(listed_transactions(request.user), pk=transaction_id)
    if request.method == "GET":
        return Response(transaction_detail_body(tx))

    data = body_dict(request)
    reject_unknown_fields(data, {"category"})
    if "category" not in data:
        raise ValidationError({"category": [CATEGORY_ERROR]})
    if tx.transaction_type == Tx.TransactionType.INCOME:
        raise ValidationError({"category": [INCOME_ERROR]})  # 10/24 AA⑦

    changed = _apply_category(tx, data["category"])
    if changed:
        tx.save(update_fields=[*changed, "updated_at"])
    # 바뀐 카테고리 이름 등을 새로 읽어서 17번과 같은 모양으로 돌려준다
    tx = get_object_or_404(listed_transactions(request.user), pk=transaction_id)
    return Response(transaction_detail_body(tx))


# ---------- 옛 목업 도구 ----------
# config/openapi.py(나은)의 '현재 서버 확인' 문서가 아직 아래 두 개를 가져다 쓴다.
# 그 문서를 실제 응답 기준으로 바꾸면 함께 지운다. 화면(TransactionsPage.jsx)은 이 PR에서 새 모양으로 바꾼다.
PENDING = "확인 필요"
NOT_SPENDING = ("income", "transfer")


def net_amount(tx):
    if tx.get("cancel") == "full":
        return 0
    return tx["amount"] - tx.get("cancel_amount", 0)


def filter_days(selected):
    def keep(tx):
        if selected == "전체":
            return True
        if selected == PENDING:
            return tx["kind"] == "pending"
        return tx.get("cat") == selected

    days = []
    for day in mock.TX_DAYS:
        items = [{**tx, "net": net_amount(tx)} for tx in day["items"] if keep(tx)]
        if items:
            total = sum(tx["net"] for tx in items if tx["kind"] not in NOT_SPENDING)
            days.append({"date": day["date"], "items": items, "total": total})
    return days

"""
finance 앱 API — 거래 (docs/api/transactions.md)
- 16. GET   /api/finance/transactions/       거래 목록
- 17. GET   /api/finance/transactions/<id>/  거래 한 건 보기
- 18. PATCH /api/finance/transactions/<id>/  거래 분류 수정 · 내 계좌 이체 확인
"""
import uuid
from datetime import date

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q, UUIDField
from django.db.models.expressions import RawSQL
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from budgets.models import Category
from budgets.salary import salary_reviews

from . import classifier
from .display import account_display, card_display
from .inputs import body_dict, reject_unknown_fields
from .merchant_rules import FUTURE_KEY, same_store_pending
from .models import BankAccount, Card, FinancialTransaction, RecurringExpense, RecurringSaving, SourceKind, TransactionLink
from .spending import spending_summary
from .transfer_rules import OWN_RECHECK, OWN_REVIEW

Tx = FinancialTransaction
UNCLASSIFIED = "UNCLASSIFIED"
# 목록에 나오는 거래: 소비 · 입금(월급 등) · 남에게 보낸 이체 (10/22 V①)
# 내 계좌끼리 이체 · 저축 · 대출 상환 · 카드값은 넣지 않는다. 단 이 기능이 자동 분류하거나 사용자가 확인한 내 계좌 이체(출금 쪽)는
# 그 뒤 저축으로 바뀌었어도 되돌릴 수 있게 남긴다 (R11)
OWN_TYPES = (Tx.TransactionType.OWN_TRANSFER, Tx.TransactionType.SAVING)
LIST_TYPES = (Tx.TransactionType.SPENDING, Tx.TransactionType.INCOME, Tx.TransactionType.TRANSFER)
PERIOD_ERROR = "기간을 다시 골라 주세요."
# 목록 거래 유형 필터 (11/9). transfer = 남에게 보낸 이체 + 목록에 나오는 내 계좌 이체 줄(OWN_TYPES)
TYPE_FILTERS = {
    "spending": Q(transaction_type=Tx.TransactionType.SPENDING),
    "income": Q(transaction_type=Tx.TransactionType.INCOME),
    "transfer": Q(transaction_type__in=(Tx.TransactionType.TRANSFER, *OWN_TYPES)),
}


# ---------- 목록에 나오는 거래 고르기 (17번 '한 건 보기'도 같은 기준을 쓴다) ----------

def with_card_fixed_id(rows):
    """회원·거래 범위 쿼리에 카드 근거 연결을 붙인다. 추가 쿼리 없이 목록·선차감이 같은 판정을 쓴다."""
    # 카드 고정지출의 근거 거래(evidence)인 카드 결제 → 그 고정지출 (11/4). 카드는 납부 예정이 없어서 evidence 로 본다
    #   확정 · 중지만 (계좌 쪽 납부 짝처럼 중지해도 낸 기록은 고정비로 남음, 후보 · 무시는 아님)
    #   ID 는 budgets.api.id_set 처럼 대소문자 · 대시 · 중괄호 · urn:uuid: 를 떼고 비교
    re, tx = RecurringExpense._meta, Tx._meta
    card_fixed = RawSQL(f"""
        SELECT r.{re.pk.column} FROM {re.db_table} r
        WHERE r.user_id = {tx.db_table}.user_id AND r.status IN ('confirmed', 'stopped')
          AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(
                          CASE WHEN jsonb_typeof(r.evidence) = 'array' THEN r.evidence ELSE '[]'::jsonb END) AS e(v)
                      WHERE regexp_replace(lower(e.v), 'urn:|uuid:|[{{}}-]', '', 'g')
                            = replace({tx.db_table}.{tx.pk.column}::text, '-', ''))
        ORDER BY r.{re.pk.column} LIMIT 1""", [], output_field=UUIDField())
    return rows.annotate(card_fixed_id=card_fixed)


def listed_transactions(user):
    """이 회원의 거래 중 목록에 나오는 것만.
    체크카드 결제처럼 카드 승인 + 계좌 출금이 같은 구매(SAME_PURCHASE)로 이어져 있으면
    계좌 출금 쪽은 빼서 한 번만 센다 (10/22 B)"""
    same_purchase = TransactionLink.objects.filter(
        Q(from_transaction=OuterRef("pk")) | Q(to_transaction=OuterRef("pk")),
        relation_type=TransactionLink.Relation.SAME_PURCHASE,
    )
    return (
        with_card_fixed_id(Tx.objects.annotate(legacy_own=Exists(legacy_own_links())).filter(
            Q(transaction_type__in=LIST_TYPES)
            # 내 계좌 이체는 출금 쪽 한 줄만 (규칙이 이은 입금은 출금에서 함께 되돌린다)
            | Q(transaction_type__in=OWN_TYPES, direction=Tx.Direction.OUT,
                **{f"field_states__{OWN_REVIEW}__in": ["auto", "confirmed"]})
            | Q(LEGACY_OWN, legacy_own=True),
            user=user,
        ))
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


def has_payment_remaining(tx):
    """전액 취소는 납부가 아니며, 부분 취소는 잔액이 있으면 납부로 유지한다."""
    return tx.amount - cancel_info(tx)["amount"] > 0


def fixed_expense_id(tx):
    """이 거래가 납부로 연결된 고정지출 ID (★8 · 13). 카드 결제는 카드 고정지출의 근거 거래면 그 고정지출. 없으면 None"""
    for match in tx.payment_matches.all():
        if match.due.obligation_type == "recurring":
            return match.due.recurring_id
    if tx.source_kind == SourceKind.CARD:
        return getattr(tx, "card_fixed_id", None)  # with_card_fixed_id가 붙인 값
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


# 11/5(R11) 전 규칙이 판단 표시 없이 own_transfer 로 바꾸고 입금과 이은 은행 출금. DB 값은 그대로 두고 자동 분류(auto)로 읽어
# 목록에 보이고 [맞아요] · [연결 해제]할 수 있게 한다 (11/06). 저축으로 바뀐 것 · 연결 없는 것은 여기 넣지 않는다 (own_transfer_legacy_check 로 점검)
LEGACY_OWN = ~Q(field_states__has_key=OWN_REVIEW) & Q(
    transaction_type=Tx.TransactionType.OWN_TRANSFER, direction=Tx.Direction.OUT, source_kind=SourceKind.BANK)


def legacy_own_links():
    return TransactionLink.objects.filter(from_transaction=OuterRef("pk"), relation_type=TransactionLink.Relation.OWN_TRANSFER)


def is_legacy_own(tx):
    return (OWN_REVIEW not in (tx.field_states or {}) and tx.transaction_type == Tx.TransactionType.OWN_TRANSFER
            and tx.direction == Tx.Direction.OUT and tx.source_kind == SourceKind.BANK
            and TransactionLink.objects.filter(from_transaction=tx, relation_type=TransactionLink.Relation.OWN_TRANSFER).exists())


def own_transfer_review(tx):
    """내 계좌 이체 판단 suggested · auto · confirmed · rejected (이체 · 내 계좌 이체 · 저축 출금만, 없으면 None).
    예전 규칙의 표시 없는 자동 연결(is_legacy_own)은 auto. field_states 원값은 내보내지 않는다"""
    if tx.transaction_type not in (Tx.TransactionType.TRANSFER, *OWN_TYPES):
        return None
    return (tx.field_states or {}).get(OWN_REVIEW) or ("auto" if is_legacy_own(tx) else None)


def own_transfer_fields(tx):
    """목록 · 상세에 붙이는 내 계좌 이체 칸 (R11). recheck = 늦게 온 거래로 자동 판단이 모호해져 다시 확인할 것"""
    review = own_transfer_review(tx)
    return {"own_transfer_review": review,
            "own_transfer_recheck": review == "auto" and bool((tx.field_states or {}).get(OWN_RECHECK))}


def salary_fields(tx, reviews):
    """isSalary = 인정된 급여 (R12) · salaryReview = confirmed(사용자 확인) | auto(자동 인정) | null"""
    return {"is_salary": tx.pk in reviews, "salary_review": reviews.get(tx.pk)}


# ---------- 16. 거래 목록 ----------

def _parse_day(value):
    """'2026-10-01' → date. 안 보냈으면 None, 형식이 틀리면 400"""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValidationError({"from": [PERIOD_ERROR]})


def _list_param(params, name):
    """'A,B' → ['A', 'B'] (빈 값 · 중복 제외, 순서 유지)"""
    return list(dict.fromkeys(v.strip() for v in (params.get(name) or "").split(",") if v.strip()))


def _owned_ids(user, model, ids, field):
    """내 계좌 · 카드 ID만. 형식이 틀리거나 · 없거나 · 남의 것이면 400 (본문 ID 와 같은 규칙: 없음 · 남의 것 같은 문구)"""
    if not ids:
        return []
    try:
        ids = [uuid.UUID(v) for v in ids]
    except ValueError:
        ids = None
    if ids is None or model.objects.filter(user=user, pk__in=ids).count() != len(ids):
        raise ValidationError({field: ["선택할 수 없는 계좌 · 카드예요."]})
    return ids


@api_view(["GET"])
@permission_classes([IsAuthenticated])  # 로그인 안 하면 403 not_authenticated
def transactions(request):
    """16. GET /api/finance/transactions/?category=FOOD,CULTURE_LEISURE&accountId=…&cardId=…&transactionType=income&from=2026-10-01&to=2026-10-31
    - 기간: 달력월 기준. from 없으면 이번 달 1일, to 없으면 오늘
      단 category=UNCLASSIFIED 하나만 보내고 기간을 안 보내면 전체 기간 (H-03, 홈 '확인할 거래 수'와 같은 숫자)
    - category · accountId · cardId · transactionType 은 쉼표로 여러 개 (11/3 · 11/9). 한 조건 안에서는 그중 하나, 조건끼리는 모두 만족
    - 정렬: 최신순 (거래일 → 거래 시각, 시각이 없으면 같은 날의 맨 뒤)"""
    params = request.query_params
    codes = _list_param(params, "category")
    if Category.objects.filter(category_code__in=codes).count() != len(codes):
        raise ValidationError({"category": ["모르는 카테고리예요."]})
    category = UNCLASSIFIED if codes == [UNCLASSIFIED] else None  # 전체 기간 규칙은 확인 필요만 골랐을 때만
    account_ids = _owned_ids(request.user, BankAccount, _list_param(params, "accountId"), "account_id")
    card_ids = _owned_ids(request.user, Card, _list_param(params, "cardId"), "card_id")
    types = _list_param(params, "transactionType")
    if any(t not in TYPE_FILTERS for t in types):
        raise ValidationError({"transaction_type": ["모르는 거래 유형이에요."]})

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
    if codes:
        # 카테고리를 걸면 소비 거래만 (입금 · 이체는 카테고리가 없음)
        rows = rows.filter(transaction_type=Tx.TransactionType.SPENDING, confirmed_category_id__in=codes)
    if account_ids or card_ids:
        rows = rows.filter(Q(account_id__in=account_ids) | Q(card_id__in=card_ids))
    if types:
        rows = rows.filter(Q(*(TYPE_FILTERS[t] for t in types), _connector=Q.OR))
    rows = rows.order_by("-occurred_on", F("occurred_time").desc(nulls_last=True), "-created_at")

    salary = salary_reviews(request.user)
    items = [{**transaction_item(tx), **salary_fields(tx, salary), **own_transfer_fields(tx)} for tx in rows]
    summary = spending_summary(items)
    if start is None:
        # 전체 기간: 시작일 = 가장 오래된 거래일 (없으면 None)
        start = min((item["occurred_on"] for item in items), default=None)

    return Response({
        "period": {"from": start, "to": end, "basis": basis},
        "summary": {
            # 이번 응답의 소비 거래 소계 (입금 · 이체 제외). 홈 · 예산의 사용액과는 다른 값
            "spent_total": summary["spent_total"],
            "needs_review_count": summary["needs_review_count"],
        },
        "items": items,
    })


# ---------- 17 · 18. 거래 한 건 보기 · 분류 수정 ----------

CATEGORY_ERROR = "카테고리를 다시 골라 주세요."
INCOME_ERROR = "이 거래는 카테고리를 고를 수 없어요."
FIXED_TRANSFER_ERROR = "고정지출에 연결된 거래예요. 고정지출 연결을 먼저 풀어 주세요."
ORIGINAL_TYPE = "original_transaction_type"  # field_states 에 남기는 '원래 이체였음' 표시 (10/24 AA⑥)
APPLY_TO_FUTURE_ERROR = "true 또는 false 로 보내 주세요."
OWN_TRANSFER_ERROR = "내 계좌 이체로 확인할 수 없는 거래예요."
OWN_TRANSFER_ONLY_ERROR = "내 계좌 이체 확인은 다른 칸과 같이 보낼 수 없어요."
OWN_TRANSFER_FIRST_ERROR = "내 계좌 이체 확인을 먼저 취소해 주세요."
OWN_TRANSFER_FIXED_ERROR = "고정지출 근거로 쓰인 거래예요. 고정지출 화면에서 먼저 정리해 주세요."


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


def can_apply_to_future(tx):
    """'앞으로도 적용' 체크박스를 보여 줄 거래 (10/30): 소비 · 원래 이체 아님(받는 사람은 기억 안 함, W) · 페이 충전 아님
    다품목도 사용자가 직접 고르면 기억한다 (11/06 결정)"""
    return (tx.transaction_type == Tx.TransactionType.SPENDING and bool(tx.merchant_name)
            and (tx.field_states or {}).get(ORIGINAL_TYPE) != Tx.TransactionType.TRANSFER
            and not classifier.is_pay_charge(tx.merchant_name))


def transaction_detail_body(tx):
    """거래 한 건 = 목록 한 줄 + 상세 칸"""
    return {
        **transaction_item(tx),
        **salary_fields(tx, salary_reviews(tx.user_id)),
        # 할부 개월: 카드 거래만 (일시불 0). 계좌 거래는 None
        "installment_months": tx.installment_months if tx.source_kind == SourceKind.CARD else None,
        "suggestion": suggestion_info(tx),
        "classified_by": tx.classification_source,
        "can_apply_to_future": can_apply_to_future(tx),
        # 원래 이체였는지 (화면의 [이체로 되돌리기] 표시용, 11/03). field_states 원값은 내보내지 않는다 (AA⑥)
        "was_transfer": (tx.field_states or {}).get(ORIGINAL_TYPE) == Tx.TransactionType.TRANSFER,
        **own_transfer_fields(tx),
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
    # 사용자 확정. "앞으로도 적용"이면 이후 같은 가맹점의 새 거래가 이 카테고리를 먼저 쓴다 (아래 view, merchant_rules)
    tx.classification_source = Tx.ClassificationSource.USER
    return changed


def _apply_own_transfer(tx, is_mine):
    """내 계좌 이체 확인 (R11). 이 기능이 판단한 은행 출금만 바꾼다. 고칠 칸 목록을 돌려준다 (틀리면 400)
    - true  [맞아요]: 추정(suggested) · 거절(rejected)한 transfer → 이 출금만 own_transfer. 자동 분류(auto)는 연결 · 유형 그대로 확인만
    - false [아니에요] · 확인 취소 · 연결 해제: → transfer. 규칙이 이은 입금이 있으면 연결을 지우고 입금으로 되돌린다.
      저축으로 바뀐 거래면 그 저축의 근거 이체에서도 뺀다 (저축 자체 · 다른 근거는 그대로)
    판단 표시가 남아 있어도 지금 유형이 소비 등이면(사용자가 분류함) true · false 모두 400 — 오래된 화면의 요청이 분류를 덮지 않게.
    회원 → 이 출금 행이 잠긴 상태에서 다시 읽은 값으로 판단한다. 상대 입금 · 저축 행은 여기서 잠근다"""
    T = Tx.TransactionType
    states = dict(tx.field_states or {})
    review = own_transfer_review(tx)  # 예전 규칙의 표시 없는 자동 연결도 auto 로 (DB 표시는 사용자가 고를 때 처음 남긴다)
    as_transfer = (tx.transaction_type == T.TRANSFER and review in ("suggested", "rejected")
                   and tx.confirmation_state != Tx.ConfirmationState.CONFIRMED)
    as_own = tx.transaction_type in OWN_TYPES and review in ("auto", "confirmed")
    if is_mine and as_own and review == "confirmed" or not is_mine and as_transfer and review == "rejected":
        return []  # 이미 원하는 상태 — 같은 결과로 성공
    allowed = (
        tx.source_kind == SourceKind.BANK and tx.direction == Tx.Direction.OUT and not tx.is_canceled
        and (as_transfer or as_own)
        # 카드값 · 대출 · 고정지출 회차에 붙었거나 고정비로 충당된 출금은 그 연결이 먼저다
        and not tx.payment_matches.all() and not tx.allocations.all()
    )
    if not allowed:
        raise ValidationError({"own_transfer": [OWN_TRANSFER_ERROR]})
    states.pop(OWN_RECHECK, None)
    states[OWN_REVIEW] = "confirmed" if is_mine else "rejected"
    if is_mine:
        if tx.transaction_type == T.TRANSFER:
            tx.transaction_type = T.OWN_TRANSFER
    else:
        if tx.transaction_type == T.SAVING:
            _remove_saving_evidence(tx)
        _unlink_own_transfer(tx)
        tx.transaction_type = T.TRANSFER
    tx.confirmation_state = Tx.ConfirmationState.CONFIRMED if is_mine else Tx.ConfirmationState.NEEDS_REVIEW
    tx.field_states = states
    return ["transaction_type", "confirmation_state", "field_states"]


def _remove_saving_evidence(tx):
    """저축으로 바뀐 출금을 정기 저축 근거에서 뺀다. 고정지출 근거이기도 하면 거기서 먼저 정리해야 한다 (400)"""
    from budgets.api import id_set  # budgets.api 가 finance 를 읽어서 함수 안에서 부른다

    pk = str(tx.pk)
    if any(pk in id_set(ids) for ids in RecurringExpense.objects.filter(user=tx.user_id).values_list("evidence", flat=True)):
        raise ValidationError({"own_transfer": [OWN_TRANSFER_FIXED_ERROR]})
    for saving in RecurringSaving.objects.select_for_update().filter(user=tx.user_id).order_by("pk"):
        kept = [v for v in (saving.evidence or []) if id_set([v]) != {pk}]
        if len(kept) != len(saving.evidence or []):
            saving.evidence = kept
            saving.save(update_fields=["evidence"])


def _unlink_own_transfer(out):
    """규칙이 이은 내 계좌 이체 연결을 지우고 상대 입금을 income 으로 되돌린다. 입금에도 거절 표시를 남겨 규칙이 다시 잇지 않게 한다.
    입금이 그사이 다른 유형(저축 등)이 됐으면 유형은 두고 연결 · 표시만 정리한다"""
    links = TransactionLink.objects.filter(from_transaction=out, relation_type=TransactionLink.Relation.OWN_TRANSFER)
    for income in Tx.objects.select_for_update().filter(pk__in=links.values("to_transaction"), user_id=out.user_id).order_by("pk"):
        income.field_states = {**(income.field_states or {}), OWN_REVIEW: "rejected"}
        if income.transaction_type == Tx.TransactionType.OWN_TRANSFER:
            income.transaction_type = Tx.TransactionType.INCOME
        income.save(update_fields=["transaction_type", "field_states", "updated_at"])
    links.delete()


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def transaction_detail(request, transaction_id):
    """17. GET  /api/finance/transactions/<id>/ — 거래 한 건 보기
    18. PATCH /api/finance/transactions/<id>/ — 거래 분류 수정 {"category": "FOOD" | null, "applyToFuture": true}
        또는 내 계좌 이체 확인 {"ownTransfer": true | false} (R11, 다른 칸과 같이 보낼 수 없음)
    없는 거래 · 남의 거래 · 목록에 안 나오는 거래(같은 구매로 빠진 계좌 출금, 카드값 등)는 모두 404 (★3)"""
    if request.method == "GET":
        tx = get_object_or_404(listed_transactions(request.user), pk=transaction_id)
        return Response(transaction_detail_body(tx))

    data = body_dict(request)
    reject_unknown_fields(data, {"category", "apply_to_future", "own_transfer"})
    own = "own_transfer" in data
    if own and (len(data) != 1 or not isinstance(data["own_transfer"], bool)):
        raise ValidationError({"own_transfer": [OWN_TRANSFER_ONLY_ERROR if len(data) != 1 else APPLY_TO_FUTURE_ERROR]})
    if not own and "category" not in data:
        raise ValidationError({"category": [CATEGORY_ERROR]})
    apply_to_future = data.get("apply_to_future", False)
    if not isinstance(apply_to_future, bool):
        raise ValidationError({"apply_to_future": [APPLY_TO_FUTURE_ERROR]})

    # 조회 → 검사 → 저장을 한 트랜잭션에서 하고, 그동안 이 거래 행을 잠근다.
    # 같은 거래에 '카테고리 변경'과 '이체로 되돌리기'가 동시에 와도 하나씩 차례로 처리되어
    # transfer + 카테고리 확정 같은 섞인 상태가 남지 않는다 (PR #41 리뷰).
    with transaction.atomic():
        if own or apply_to_future:
            # 잠금 순서: 회원 → 거래 (pipeline · budgets.api.lock_user 와 같음). 저장 뒤 처리와 차례로 돈다
            get_user_model().objects.select_for_update(no_key=True).filter(pk=request.user.pk).first()
        # 1) 잠금: 조인 없이 거래 행만 잠근다 (outer join 쪽은 FOR UPDATE 불가)
        row = Tx.objects.select_for_update().filter(user=request.user, pk=transaction_id)
        if not list(row.values_list("pk", flat=True)):
            raise Http404  # 없는 거래 · 남의 거래
        # 2) 잠근 뒤 최신 상태를 다시 읽는다. 목록에 안 나오는 거래면 404
        tx = get_object_or_404(listed_transactions(request.user), pk=transaction_id)
        if own:
            changed = _apply_own_transfer(tx, data["own_transfer"])
        else:
            if tx.transaction_type == Tx.TransactionType.INCOME:
                raise ValidationError({"category": [INCOME_ERROR]})  # 10/24 AA⑦
            if tx.transaction_type in OWN_TYPES:
                raise ValidationError({"category": [OWN_TRANSFER_FIRST_ERROR]})
            changed = _apply_category(tx, data["category"])
            # '앞으로도 적용': 고를 수 있는 거래만 남기고, 아니면 지운다 (merchant_rules.remembered_category 가 읽음)
            states = dict(tx.field_states or {})
            if data["category"] is not None and apply_to_future and can_apply_to_future(tx):
                states[FUTURE_KEY] = True
            else:
                states.pop(FUTURE_KEY, None)
            if states != (tx.field_states or {}):
                tx.field_states = states
                changed = [*dict.fromkeys([*changed, "field_states"])]
        if changed:
            tx.save(update_fields=[*changed, "updated_at"])
        # '앞으로도 적용'이면 같은 가게의 다른 '확인 필요' 거래도 같은 카테고리로 (11/11)
        if not own and FUTURE_KEY in (tx.field_states or {}):
            for other in same_store_pending(tx):
                classifier.apply_remembered(other, data["category"])
                other.save(update_fields=[*classifier.FIELDS, "updated_at"])
    # 바뀐 카테고리 이름 등을 새로 읽어서 17번과 같은 모양으로 돌려준다
    tx = get_object_or_404(listed_transactions(request.user), pk=transaction_id)
    return Response(transaction_detail_body(tx))

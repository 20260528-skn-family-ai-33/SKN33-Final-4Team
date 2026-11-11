"""
가맹점별 사용자 분류 기억 (MVP: 별도 테이블 없이 거래 내역에서 찾는다)

거래 상세에서 사용자가 카테고리를 고치면, 그 거래는 classification_source = USER_SOURCE 로 저장된다.
"앞으로도 적용"(PATCH applyToFuture)을 고르면 그 거래의 field_states 에 FUTURE_KEY 가 남는다 (10/30).
새 거래가 들어오면 같은 회원·같은 가맹점의 '사용자가 확정한 가장 최근 거래'를 찾는다.
- 그 거래가 "앞으로도 적용"이면 그 카테고리를 자동으로 쓴다 (remembered_category)
- 아니면 자동으로 쓰지 않고, 거래 분류가 '확인 필요' 후보로만 보여 줄 수 있다 (last_user_choice)

이 방법이 느리거나(거래가 아주 많아질 때) 규칙을 따로 관리해야 하면 merchant_rule 테이블을 추가한다.
"""
import re

from .models import FinancialTransaction

# classification_source 코드는 아직 미정 (docs/DB_MODELS.md 5번). 정해지면 이 값만 맞춘다
USER_SOURCE = "user"
FUTURE_KEY = "apply_to_future"
SEARCHED_MULTI_ITEM = "multi_item"  # field_states["classifier"] 안: 5단계 검색 업종이 다품목. 같은 가게의 다음 거래도 다품목으로 처리


def normalize_merchant(name):
    """가맹점 이름 비교용: 앞뒤 공백 제거 + 연속 공백 하나로. 빈 값이면 None"""
    if not name:
        return None
    return " ".join(name.split()) or None


# 매달 바뀌는 연월 표시 (11/11 PR #106 리뷰). 전화번호 · 계약 번호처럼 받는 곳을 가르는 숫자는 남긴다
# 숫자 연월은 적요 맨 앞 · 맨 끝에 따로 떨어진 4자리 YYMM · 6자리 YYYYMM만. [임시] 4자리는 20~29년(2x)만 — '카페 1204' 같은 번호는 남긴다
_MM = r"(?:0[1-9]|1[0-2])"
_YM = rf"(?:20\d{{2}}|2\d){_MM}"
YEAR_MONTH = re.compile(
    rf"^{_YM}(?!\d)"                                  # 맨 앞 — 2606국민건강, 202606국민건강
    rf"|(?<!\d){_YM}$"                                # 맨 끝 — 국민건강 2606
    r"|(?<!\d)(?:\d{4}|\d{2})년\s*(?:0?[1-9]|1[0-2])월"  # 2026년 11월
    r"|(?<!\d)(?:0?[1-9]|1[0-2])월"                    # 관리비 11월
)


def compare_label(name):
    """반복 출금의 받는 곳 비교용 이름: 연월 표시를 빼고 공백 정리. 빈 값이면 None
    고정지출 · 저축 후보 감지(budgets/candidates.py)와 확정 뒤 다음 달 출금 연결(budgets/api.py remembered_withdrawal ·
    due_match · saving_match)이 같은 함수를 써야 감지된 묶음이 다음 달에도 이어진다.
    연월을 빼고 남는 글자가 없으면(적요가 '2606'뿐) 원래 이름을 쓴다"""
    text = normalize_merchant(name)
    if text is None:
        return None
    return normalize_merchant(YEAR_MONTH.sub(" ", text)) or text


def store_type(value):
    """카드사가 준 구체적인 업종. 전자상거래는 실제 가게 업종이 아니라서 쓰지 않는다 (11/06 NH: 피시방·치킨집도 전자상거래PG)"""
    industry = (value or "").strip()
    return None if not industry or industry.startswith("전자상거래") else industry


def last_user_choice(user, merchant_name, industry=None):
    """같은 가맹점에 사용자가 마지막으로 고른 (카테고리 코드, 앞으로도 적용 여부). 없으면 (None, False)"""
    merchant = normalize_merchant(merchant_name)
    if merchant is None:
        return None, False
    row = (
        FinancialTransaction.objects
        .filter(user=user, merchant_name=merchant, classification_source=USER_SOURCE)
        .order_by("-occurred_on", "-updated_at")
        .values_list("confirmed_category_id", "field_states", "merchant_category")
        .first()
    )
    if row is None:
        return None, False
    # 이름이 같아도 카드사 업종이 분명히 다르면 다른 가게다. 이번 거래의 업종을 쓴다
    past = store_type(row[2])
    return row[0], bool((row[1] or {}).get(FUTURE_KEY)) and not (past and industry and past != industry)


def seen_multi_item(user, merchant_name):
    """같은 회원·같은 가맹점의 거래 중 검색으로 다품목이 확인된 거래의 (쉼표로 이은 업종, 근거가 충돌했는지). 없으면 (None, False)"""
    merchant = normalize_merchant(merchant_name)
    rows = [] if merchant is None else list(FinancialTransaction.objects.filter(
        user=user, merchant_name=merchant, **{f"field_states__classifier__{SEARCHED_MULTI_ITEM}": True},
    ).values_list("field_states__classifier__industry", "field_states__classifier__name_conflict",
                  "field_states__classifier__evidence_conflict"))
    if not rows:
        return None, False
    return ", ".join(dict.fromkeys(row[0] for row in rows if row[0])), any(row[1] or row[2] for row in rows)


def remembered_category(user, merchant_name):
    """같은 가맹점의 새 거래에 자동으로 쓸 카테고리 코드. 마지막 선택이 "앞으로도 적용"일 때만, 아니면 None"""
    code, future = last_user_choice(user, merchant_name)
    return code if future else None


def same_store_pending(tx):
    """'앞으로도 적용'을 고른 거래와 같은 가게의 다른 '확인 필요' 소비 거래 (11/11)
    카드사 업종이 분명히 다르면 다른 가게로 보고 뺀다 (last_user_choice 와 같은 기준)"""
    merchant = normalize_merchant(tx.merchant_name)
    if merchant is None:
        return []
    rows = (
        FinancialTransaction.objects.select_for_update()
        .filter(user_id=tx.user_id, merchant_name=merchant,
                transaction_type=FinancialTransaction.TransactionType.SPENDING,
                confirmation_state=FinancialTransaction.ConfirmationState.NEEDS_REVIEW)
        .exclude(pk=tx.pk)
    )
    mine = store_type(tx.merchant_category)
    return [row for row in rows
            if not (mine and store_type(row.merchant_category) and store_type(row.merchant_category) != mine)]

"""
가맹점별 사용자 분류 기억 (MVP: 별도 테이블 없이 거래 내역에서 찾는다)

거래 상세에서 사용자가 카테고리를 고치면, 그 거래는 classification_source = USER_SOURCE 로 저장된다.
"앞으로도 적용"(PATCH applyToFuture)을 고르면 그 거래의 field_states 에 FUTURE_KEY 가 남는다 (10/30).
새 거래가 들어오면 같은 회원·같은 가맹점의 '사용자가 확정한 가장 최근 거래'를 찾는다.
- 그 거래가 "앞으로도 적용"이면 그 카테고리를 자동으로 쓴다 (remembered_category)
- 아니면 자동으로 쓰지 않고, 거래 분류가 '확인 필요' 후보로만 보여 줄 수 있다 (last_user_choice)

이 방법이 느리거나(거래가 아주 많아질 때) 규칙을 따로 관리해야 하면 merchant_rule 테이블을 추가한다.
"""
from .models import FinancialTransaction

# classification_source 코드는 아직 미정 (docs/DB_MODELS.md 5번). 정해지면 이 값만 맞춘다
USER_SOURCE = "user"
FUTURE_KEY = "apply_to_future"


def normalize_merchant(name):
    """가맹점 이름 비교용: 앞뒤 공백 제거 + 연속 공백 하나로. 빈 값이면 None"""
    if not name:
        return None
    return " ".join(name.split()) or None


def last_user_choice(user, merchant_name):
    """같은 가맹점에 사용자가 마지막으로 고른 (카테고리 코드, 앞으로도 적용 여부). 없으면 (None, False)"""
    merchant = normalize_merchant(merchant_name)
    if merchant is None:
        return None, False
    row = (
        FinancialTransaction.objects
        .filter(user=user, merchant_name=merchant, classification_source=USER_SOURCE)
        .order_by("-occurred_on", "-updated_at")
        .values_list("confirmed_category_id", "field_states")
        .first()
    )
    if row is None:
        return None, False
    return row[0], bool((row[1] or {}).get(FUTURE_KEY))


def remembered_category(user, merchant_name):
    """같은 가맹점의 새 거래에 자동으로 쓸 카테고리 코드. 마지막 선택이 "앞으로도 적용"일 때만, 아니면 None"""
    code, future = last_user_choice(user, merchant_name)
    return code if future else None

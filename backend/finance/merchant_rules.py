"""
가맹점별 사용자 분류 기억 (MVP: 별도 테이블 없이 거래 내역에서 찾는다)

거래 상세에서 사용자가 카테고리를 고치면, 그 거래는 classification_source = USER_SOURCE 로 저장된다.
새 거래가 들어오면 같은 회원·같은 가맹점의 '사용자가 확정한 가장 최근 거래'를 찾아 그 카테고리를 먼저 쓴다.
→ ML 분류보다 먼저 호출한다. 결과가 없으면 원래대로 ML·검색으로 분류한다.

이 방법이 느리거나(거래가 아주 많아질 때) 규칙을 따로 관리해야 하면 merchant_rule 테이블을 추가한다.
"""
from .models import FinancialTransaction

# classification_source 코드는 아직 미정 (docs/DB_MODELS.md 5번). 정해지면 이 값만 맞춘다
USER_SOURCE = "user"


def normalize_merchant(name):
    """가맹점 이름 비교용: 앞뒤 공백 제거 + 연속 공백 하나로. 빈 값이면 None"""
    if not name:
        return None
    return " ".join(name.split()) or None


def remembered_category(user, merchant_name):
    """같은 가맹점에 사용자가 마지막으로 고른 카테고리 코드. 없으면 None"""
    merchant = normalize_merchant(merchant_name)
    if merchant is None:
        return None
    return (
        FinancialTransaction.objects
        .filter(user=user, merchant_name=merchant, classification_source=USER_SOURCE)
        .order_by("-occurred_on", "-updated_at")
        .values_list("confirmed_category_id", flat=True)
        .first()
    )

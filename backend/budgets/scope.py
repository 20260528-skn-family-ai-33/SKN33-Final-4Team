"""생활비 예산 범위 — 배분 · 사용액에서 뺄 카테고리를 한곳에 둔다 (다른 모듈을 import하지 않는다: 순환 방지)

11/3 확정: 주거·통신 · 여행·숙박은 새 생활비 배분(추천 · 최소 금액 · 자물쇠)과 생활비 사용액 · 남은 생활비 · 하루 금액에서 뺀다.
카테고리 자체 · 거래 목록 · 전체 소비 합계에는 그대로 남고, 그 지출은 '예산 밖 지출'로 안내만 한다.
"""

OUT_OF_BUDGET = ("HOUSING_TELECOM", "TRAVEL_LODGING")


def outside_spent(summary):
    """finance.spending.spending_summary 결과 → 예산 밖 카테고리별 지출 {code: 원} (쓴 곳만)"""
    return {code: summary["by_category"][code] for code in OUT_OF_BUDGET if summary["by_category"].get(code)}


def living_spent(summary):
    """생활비 사용액 = 생활비 쪽 소비 합계 − 예산 밖 지출"""
    return summary["spent_total"] - sum(outside_spent(summary).values())

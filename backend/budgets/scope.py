"""생활비 배분·사용액에서 제외하는 카테고리 (11/3 확정)."""

OUT_OF_BUDGET = ("HOUSING_TELECOM", "TRAVEL_LODGING")


def outside_spent(summary):
    """소비 집계 → 예산 밖 카테고리별 금액 (쓴 곳만)."""
    return {code: summary["by_category"][code] for code in OUT_OF_BUDGET if summary["by_category"].get(code)}


def living_spent(summary):
    """고정비를 제외한 소비 집계에서 예산 밖 지출을 뺀다."""
    return summary["spent_total"] - sum(outside_spent(summary).values())

"""거래 목록과 예산이 공유하는 소비 집계. 입력은 transaction_item으로 정규화한 거래다."""
from collections import defaultdict


def spending_summary(items, *, living_only=False):
    spent = count = 0
    categories = defaultdict(int)
    for item in items:
        if item["transaction_type"] != "spending":
            continue
        count += item["category"] == "UNCLASSIFIED"  # 확인 필요 건수는 고정비 소비도 포함한다.
        if living_only and item["funding"] == "fixed":
            continue
        spent += item["net_amount"]  # 같은 구매 제외 · 취소 차감은 거래 목록의 정규화 결과 그대로
        categories[item["category"]] += item["net_amount"]
    return {"spent_total": spent, "needs_review_count": count, "by_category": dict(categories)}

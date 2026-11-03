"""소비 분석용 승인 예산 읽기 — 읽기 전용 (주기 생성 · 상태 갱신 · select_for_update 없음)

budgets.overview.budget_data 는 validated 를 먼저 고르고 주기를 준비하므로 소비 분석에 쓰지 않는다 (동작은 그대로 둔다).
여기는 기준 날짜가 들어 있는 DB 주기의 approved 예산만 읽는다. validated · draft · 프로필 생활비 목표로 대신하지 않는다.
"""
from django.db import DatabaseError

from budgets.models import Budget, BudgetCycle, BudgetItem

# 예산 범위 밖 카테고리 목록은 budgets.scope 한 곳에 둔다 (11/3 확정 — 11/2 2차의 '사용액에 포함'을 바꿈).
# 카테고리 자체 · 소비 조회(12개)는 그대로, 예산 줄 · 예산 선택지 · 생활비 사용액 · 남은 돈 · 하루 금액에서 뺀다.
# 그 지출은 outside_spent 로 '예산 밖 지출' 안내에만 쓴다.
from budgets.scope import OUT_OF_BUDGET  # noqa: F401 (dialog · explain · tools 가 여기서 가져간다)


def approved_budget(user, on_date):
    """→ {"state": "approved" | "no_cycle" | "no_approved" | "error", ...}
    approved 일 때만 cycle · budget · 금액 칸이 있다. 금액은 BudgetItem 그대로 (원)"""
    try:
        cycles = list(BudgetCycle.objects.filter(user=user, starts_on__lte=on_date, ends_before__gt=on_date)
                      .order_by("-starts_on").values("cycle_id", "starts_on", "ends_before")[:2])
        if len(cycles) > 1:  # 겹친 주기는 어느 쪽인지 모른다 — 고르지 않는다
            return {"state": "error", "reason": "overlapping_cycles"}
        if not cycles:
            return {"state": "no_cycle"}
        cycle = cycles[0]
        budget = (Budget.objects.filter(user=user, cycle_id=cycle["cycle_id"], status=Budget.Status.APPROVED)
                  .values("budget_id", "version_no", "approved_at", "funding_amount").first())
        if budget is None:
            return {"state": "no_approved", "cycle": cycle}
        items = list(BudgetItem.objects.filter(user=user, budget_id=budget["budget_id"])
                     .values_list("bucket", "category_id", "allocated_amount"))
    except DatabaseError:
        return {"state": "error", "reason": "db_error"}

    buckets = {}
    for bucket, _, amount in items:
        buckets[bucket] = buckets.get(bucket, 0) + amount
    funding = budget.pop("funding_amount")
    living = {code: amount for bucket, code, amount in items if bucket == "living" and code not in OUT_OF_BUDGET}
    old_scope = sum(amount for bucket, code, amount in items if bucket == "living" and code in OUT_OF_BUDGET)
    return {
        "state": "approved", "cycle": cycle, "budget": budget,
        "living": living,  # 카테고리별 잔액용 — 예산 범위 밖 카테고리 줄은 없다
        # 생활비 총액 = 승인 때 저장한 생활비 줄 전체 (홈 · 예산 화면과 같은 값, N-F1). 옛 기준 예산의 두 카테고리 배정도 그대로 둔다
        "living_total": buckets.get("living", 0),
        "out_of_budget_allocated": old_scope,  # 0 보다 크면 이전 기준(11/3 전)으로 승인한 예산 → 재추천 안내
        "buckets": buckets,  # living · fixed · savings · reserve · cushion 합계
        "funding": funding,
        "spare": None if funding is None else funding - sum(buckets.values()),  # 여윳돈은 저장하지 않는다
    }

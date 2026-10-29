"""소비 분석용 승인 예산 읽기 — 읽기 전용 (주기 생성 · 상태 갱신 · select_for_update 없음)

budgets.overview.budget_data 는 validated 를 먼저 고르고 주기를 준비하므로 소비 분석에 쓰지 않는다 (동작은 그대로 둔다).
여기는 기준 날짜가 들어 있는 DB 주기의 approved 예산만 읽는다. validated · draft · 프로필 생활비 목표로 대신하지 않는다.
"""
from django.db import DatabaseError

from budgets.models import Budget, BudgetCycle, BudgetItem


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
    return {
        "state": "approved", "cycle": cycle, "budget": budget,
        "living": {code: amount for bucket, code, amount in items if bucket == "living"},
        "living_total": buckets.get("living", 0),
        "buckets": buckets,  # living · fixed · savings · reserve · cushion 합계
        "funding": funding,
        "spare": None if funding is None else funding - sum(buckets.values()),  # 여윳돈은 저장하지 않는다
    }

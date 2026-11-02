"""소비 분석용 승인 예산 읽기 — 읽기 전용 (주기 생성 · 상태 갱신 · select_for_update 없음)

budgets.overview.budget_data 는 validated 를 먼저 고르고 주기를 준비하므로 소비 분석에 쓰지 않는다 (동작은 그대로 둔다).
여기는 기준 날짜가 들어 있는 DB 주기의 approved 예산만 읽는다. validated · draft · 프로필 생활비 목표로 대신하지 않는다.
"""
from django.db import DatabaseError

from budgets.models import Budget, BudgetCycle, BudgetItem

# 예산을 짜는 범위 밖 카테고리 (팀 결정 11/2, 임시 — 예산 담당 확인 후 바뀔 수 있음 · MEMO 22-9 · 23-17).
# 카테고리 자체 · 소비 조회(12개)는 그대로, 예산 줄(생활비 예산 합계 · 카테고리별 잔액) · 예산 선택지에서만 뺀다.
# 실제 지출은 생활비 사용액 · 남은 돈 · 하루 금액에 넣는다 (11/2 2차 — 처음엔 사용액에서도 뺐다, MEMO 23-19).
# 이유: ML 초안 추천액이 사실상 0(천 원 수준)이고 주거·통신 지출은 대부분 고정지출로 잡힌다.
# shortcut: 팀 예산 코드(budgets)에는 아직 정의가 없어 여기 한 곳에 둔다, 팀이 같은 목록을 정하면 그것을 가져다 쓴다
OUT_OF_BUDGET = ("HOUSING_TELECOM", "TRAVEL_LODGING")


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
    return {
        "state": "approved", "cycle": cycle, "budget": budget,
        "living": living,
        # 예산 범위 밖 카테고리의 생활비 배정은 생활비 예산에서 뺀다 (더미 예산은 그 배정을 기타로 옮겨 총액을 맞췄다 — 23-17)
        "living_total": sum(living.values()),
        "buckets": buckets,  # living · fixed · savings · reserve · cushion 합계
        "funding": funding,
        "spare": None if funding is None else funding - sum(buckets.values()),  # 여윳돈은 저장하지 않는다
    }

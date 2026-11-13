"""쿠션 확보액과 현재 설정으로 재적립 계획을 계산한다."""
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Sum
from django.utils import timezone

from users.models import FinancialProfile
from .models import CushionEntry
from .salary import due_income


def cushion_data(user):
    profile = due_income(FinancialProfile.objects.filter(user=user).first(), timezone.localdate())  # 다음 주기 실수령액 전환일이 지났으면 새 금액 (11/12)
    income = profile.monthly_take_home_income if profile else None
    monthly = profile.cushion_monthly if profile else None
    if income is not None and income <= 0:
        income = None
    # 저장된 cushion_target 이 오래돼도 지금 목표를 다시 계산한다: 직접 정한 금액(11/13) 또는 현재 월 실수령액 × 3
    from .protect import DEFAULT_GOAL_MULTIPLE, cushion_goal
    custom = profile.cushion_goal_amount is not None if profile else False
    goal = cushion_goal(profile, income)
    default_goal = income * DEFAULT_GOAL_MULTIPLE if income is not None else None
    now = CushionEntry.objects.filter(user=user).aggregate(total=Sum("amount"))["total"] or 0
    remain = max(goal - now, 0) if goal is not None else None
    pct = bar = months = None
    if goal is not None:
        percent = Decimal(now) * 100 / Decimal(goal)
        pct = int(percent.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        bar = float(min(Decimal(100), max(Decimal(0), percent)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP,
        ))
    if remain is None:
        eta = "급여를 먼저 입력해 주세요."
    elif remain == 0:
        months, eta = 0, "목표 달성"
    elif monthly is None:
        eta = "월 쿠션 적립금을 먼저 입력해 주세요."
    elif monthly == 0:
        eta = "월 적립금이 0원이라 계산할 수 없어요."
    else:
        months = (remain + monthly - 1) // monthly
        eta = f"{months}회 · 약 {months}개월"
    return {
        "missing": [key for key, value in (("takeHomeIncome", income), ("cushionMonthly", monthly))
                    if value is None],
        "cushion": {
            "now": now, "goal": goal, "goal_label": "직접 정한 금액" if custom else f"월급의 {DEFAULT_GOAL_MULTIPLE}배",
            "goal_custom": custom, "default_goal": default_goal, "bar": bar, "pct": pct,
            "monthly": monthly, "use_rule": None, "remain": remain, "eta": eta, "eta_months": months,
        },
    }


# ---------------- R27 보호 중인 쿠션: 예산 승인 때 쌓기 (11/13) ----------------
# 예산을 승인할 때 그 주기의 쿠션 배정액(BudgetItem bucket=cushion 합계)을 쿠션 기록(CushionEntry)에 반영한다.
# 같은 주기에 다시 승인(조정 · 분배 실패 복구 포함)하면 이미 반영한 금액과의 차액만 남긴다 (90만 + 30만 → 120만, 같은 주기 20만으로 → 110만).
# 추천 · 초안은 쌓지 않는다. 실제 이체 완료를 뜻하지 않는 '예산에서 보호한 기록'이다 (docs/reviews 통합 검수 R27)

def _cycle_recorded(user_id, cycle_id):
    return CushionEntry.objects.filter(user_id=user_id, cycle_id=cycle_id,
                                       entry_type="allocation").aggregate(total=Sum("amount"))["total"] or 0


def record_approved_cushion(budget):
    """승인 직후(같은 트랜잭션 · 회원 잠금 안)에 부른다. 같은 예산을 두 번 불러도 한 번만 반영한다 (request_key)"""
    from .models import BudgetItem

    key = f"budget-approve:{budget.pk}"
    if CushionEntry.objects.filter(user_id=budget.user_id, request_key=key).exists():
        return None
    planned = BudgetItem.objects.filter(budget=budget, bucket="cushion").aggregate(total=Sum("allocated_amount"))["total"] or 0
    recorded = _cycle_recorded(budget.user_id, budget.cycle_id)
    diff = planned - recorded
    if diff == 0:
        return None
    return CushionEntry.objects.create(
        user_id=budget.user_id, cycle_id=budget.cycle_id, budget=budget, entry_type="allocation", amount=diff,
        request_key=key, occurred_at=budget.approved_at or timezone.now(),
        note="예산 승인" if recorded == 0 else "같은 주기 예산 다시 승인 (차액)",
    )


def record_protect_cushion(user, budget, monthly):
    """지킬 돈 설정에서 월 쿠션 적립금을 바꿔 저장했을 때 (이번 주기에 승인한 예산이 있을 때만, 회원 잠금 안).
    이번 주기 보호 쿠션을 새 금액에 맞춰 차액만 남긴다. 생활비를 안 바꾼 저장은 다시 승인하지 않으므로 여기서 반영한다.
    생활비도 바꿔 새 예산을 승인하면 record_approved_cushion 이 그 예산의 쿠션 배정액에 다시 맞춘다"""
    from uuid import uuid4

    if budget is None or monthly is None:
        return None
    diff = monthly - _cycle_recorded(user.pk, budget.cycle_id)
    if diff == 0:
        return None
    return CushionEntry.objects.create(
        user=user, cycle_id=budget.cycle_id, budget=budget, entry_type="allocation", amount=diff,
        request_key=f"protect-save:{uuid4()}", occurred_at=timezone.now(), note="지킬 돈 설정에서 월 쿠션 적립금 변경 (차액)",
    )

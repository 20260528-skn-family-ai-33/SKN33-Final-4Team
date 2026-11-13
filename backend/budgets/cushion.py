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
# 추천 · 초안은 쌓지 않는다. 실제 이체 완료를 뜻하지 않는 '예산에서 보호한 기록'이라 종류는 planned(계획상 보호)다.
# 실제로 옮긴 적립(allocation)과 나눠 두어, 예산 조정이 '이미 적립한 돈'(allocation)만 줄이지 못하게 하는 계산과 섞이지 않는다 (검수 P2)
# 승인 경로(일반 승인 · 대화형 조정 · 분배 실패 복구)가 모두 Budget 을 status=approved 로 save 하므로
# signals.py 의 post_save 에서 한 곳으로 부른다 (검수 P1 — 조정 · 복구 승인도 반영)

def _cycle_recorded(user_id, cycle_id):
    return CushionEntry.objects.filter(user_id=user_id, cycle_id=cycle_id,
                                       entry_type="planned").aggregate(total=Sum("amount"))["total"] or 0


def _planned(budget):
    from .models import BudgetItem
    return BudgetItem.objects.filter(budget=budget, bucket="cushion").aggregate(total=Sum("allocated_amount"))["total"] or 0


def record_approved_cushion(budget):
    """승인 직후(같은 트랜잭션 · 회원 잠금 안)에 부른다. 같은 예산을 두 번 불러도 한 번만 반영한다 (request_key)"""
    key = f"budget-approve:{budget.pk}"
    if CushionEntry.objects.filter(user_id=budget.user_id, request_key=key).exists():
        return None
    recorded = _cycle_recorded(budget.user_id, budget.cycle_id)
    diff = _planned(budget) - recorded
    if diff == 0:
        return None
    return CushionEntry.objects.create(
        user_id=budget.user_id, cycle_id=budget.cycle_id, budget=budget, entry_type="planned", amount=diff,
        request_key=key, occurred_at=budget.approved_at or timezone.now(),
        note="예산 승인" if recorded == 0 else "같은 주기 예산 다시 승인 (차액)",
    )


def update_approved_split(user, budget, saving, cushion):
    """검수 P1 (11/13): 지킬 돈 설정에서 생활비는 그대로 두고 저축 · 월 쿠션 적립금만 바꿔 저장했을 때.
    이번 주기 승인 예산의 저축 · 쿠션 줄만 새 금액으로 바꾼다. 생활비 카테고리 배분은 그대로, 여윳돈은 '배분 대상 − 항목 합계'라 함께 맞춰진다.
    다시 추천(LLM)은 하지 않는다. 사용자가 저장으로 정한 금액이고 계산은 여기(서버)에서만 한다.
    만들어 둔 조정안 · 초안은 지킬 돈 저장(save_protect)이 이미 만료한다.
    반환: True = 반영(또는 이미 같음) · False = 반영할 수 없음(줄 모양이 다르거나 합계가 배분 대상을 넘음 → 화면이 다시 추천으로 보낸다)"""
    from .models import BudgetItem

    items = list(BudgetItem.objects.select_for_update().filter(budget=budget))
    saving_rows = [row for row in items if row.bucket == "savings"]
    cushion_rows = [row for row in items if row.bucket == "cushion"]
    if len(saving_rows) != 1 or len(cushion_rows) != 1 or saving is None or cushion is None:
        return False
    saving_row, cushion_row = saving_rows[0], cushion_rows[0]
    before = {"saving_floor": saving_row.allocated_amount, "cushion_monthly": cushion_row.allocated_amount}
    if before == {"saving_floor": saving, "cushion_monthly": cushion}:
        return True
    total = sum(row.allocated_amount for row in items) - saving_row.allocated_amount - cushion_row.allocated_amount + saving + cushion
    if budget.funding_amount is None or total > budget.funding_amount:
        return False
    saving_row.allocated_amount, cushion_row.allocated_amount = saving, cushion
    BudgetItem.objects.bulk_update([saving_row, cushion_row], ["allocated_amount"])
    summary = dict(budget.validation_summary or {})
    summary["protect_updates"] = [*summary.get("protect_updates", []), {
        "at": timezone.now().isoformat(), "before": before, "after": {"saving_floor": saving, "cushion_monthly": cushion},
    }]
    budget.validation_summary = summary
    budget.save(update_fields=["validation_summary"])
    diff = cushion - _cycle_recorded(user.pk, budget.cycle_id)
    if diff:
        from uuid import uuid4
        CushionEntry.objects.create(
            user=user, cycle_id=budget.cycle_id, budget=budget, entry_type="planned", amount=diff,
            request_key=f"protect-save:{uuid4()}", occurred_at=timezone.now(), note="지킬 돈 설정에서 월 쿠션 적립금 변경 (차액)",
        )
    return True

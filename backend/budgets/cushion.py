"""쿠션 확보액과 현재 설정으로 재적립 계획을 계산한다."""
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Sum

from users.models import FinancialProfile
from .models import CushionEntry


def cushion_data(user):
    profile = FinancialProfile.objects.filter(user=user).first()
    income = profile.monthly_take_home_income if profile else None
    monthly = profile.cushion_monthly if profile else None
    if income is not None and income <= 0:
        income = None
    # 저장된 목표가 오래돼도 현재 월 실수령액의 3배를 사용한다.
    goal = income * 3 if income is not None else None
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
            "now": now, "goal": goal, "goal_label": "월급의 3배", "bar": bar, "pct": pct,
            "monthly": monthly, "use_rule": None, "remain": remain, "eta": eta, "eta_months": months,
        },
    }

"""지킬 돈 계산 (docs/api/budgets.md P-01 ~ P-11). 저장은 api.py에서 한다."""
from django.db.models import Sum

from finance.models import Loan, RecurringExpense, RecurringSaving


RATES = {
    "alone": {"living": 55, "saving": 30, "spare": 5, "cushion": 10},
    "family": {"living": 45, "saving": 40, "spare": 5, "cushion": 10},
}
UNIT = 10_000


# ---------------- 순수 계산 (DB를 읽지 않음) ----------------

def blocked(available, savings_min):
    if available <= 0:
        return {"reason": "fixed_over_income", "shortfall": -available}
    if available - savings_min <= 0:
        return {"reason": "savings_over_available", "shortfall": savings_min - available}
    return None


def cushion_required(balance_total, take_home_income):
    return balance_total is None or take_home_income is None or balance_total < take_home_income * 3


def round_amount(numerator, denominator):
    """1만원 단위 반올림. 부동소수점 · Python의 짝수 반올림을 쓰지 않는다."""
    return (numerator + denominator * (UNIT // 2)) // (denominator * UNIT) * UNIT


def floor_amount(numerator, denominator):
    return numerator // (denominator * UNIT) * UNIT


def fill_cushion(living, saving, cushion, spare, savings_min):
    """P-08: 쿠션 0원을 막되 여윳돈 · 생활비에서 일부라도 마련하면 저축은 유지한다."""
    if cushion == 0:
        taken = min(UNIT, spare)
        spare -= taken
        cushion += taken
        taken = min(UNIT - cushion, living)
        living -= taken
        cushion += taken
        if cushion == 0:
            taken = min(UNIT, saving - savings_min)
            saving -= taken
            cushion += taken
    return living, saving, cushion, spare


def recommend(available, housing, savings_min, *, cushion_required=True):
    """저장 가능한 금액을 추천한다. blocked인 입력은 호출 전에 걸러야 한다.

    반환 키는 Python 이름이며 API renderer가 camelCase로 바꾼다.
    """
    if savings_min < 0 or blocked(available, savings_min):
        raise ValueError("배분 기준 금액은 저축 최소 금액보다 커야 해요.")
    rates = RATES[housing]
    ratio_saving = round_amount(available * rates["saving"], 100)
    rebalanced = savings_min > ratio_saving
    rest = available - savings_min if rebalanced else None
    if rebalanced:
        saving = savings_min
        denominator = rates["living"] + rates["spare"] + rates["cushion"]
        living = floor_amount(rest * rates["living"], denominator)
        cushion = floor_amount(rest * rates["cushion"], denominator)
    else:
        saving = ratio_saving
        living = round_amount(available * rates["living"], 100)
        cushion = floor_amount(available * rates["cushion"], 100)

    # P-07: 반올림으로 넘친 만큼 생활비에서 뺀 뒤 자투리를 여윳돈으로.
    living = max(0, living - max(0, living + saving + cushion - available))
    spare = available - living - saving - cushion
    if cushion_required:
        living, saving, cushion, spare = fill_cushion(living, saving, cushion, spare, savings_min)

    return {
        "living_budget": living,
        "saving_floor": saving,
        "cushion_monthly": cushion,
        "spare": spare,
        "rebalanced": rebalanced,
        "rest_amount": rest,
        "ratio_saving_floor": ratio_saving,
    }


# ---------------- DB 조회 (저장하지 않음) ----------------

def card_reserved_amount(user, cycle):
    # TODO 세진: 첫 청구서 선차감(결정 Q · cards.md C-17)
    # + 이번 회차 할부금. 카드 고정지출 금액은 fixed_total 에 이미 있으므로 여기서 빼서 두 번 안 빠지게
    return 0


def fixed_breakdown(user, cycle):
    # 결제 수단에 상관없이 확정 고정지출을 모두 포함한다 (P-04).
    categories = dict(RecurringExpense.objects.filter(
        user=user, status="confirmed",
    ).values("category_id").annotate(total=Sum("amount")).values_list("category_id", "total"))
    loans = Loan.objects.filter(user=user, status="repaying").aggregate(total=Sum("monthly_payment"))["total"] or 0
    return {"categories": categories, "loan": loans, "card_reserved": card_reserved_amount(user, cycle)}


def fixed_total(user, cycle):
    fixed = fixed_breakdown(user, cycle)
    return sum(fixed["categories"].values()) + fixed["loan"] + fixed["card_reserved"]


def savings_min(user):
    return RecurringSaving.objects.filter(user=user, status="confirmed").aggregate(total=Sum("amount"))["total"] or 0

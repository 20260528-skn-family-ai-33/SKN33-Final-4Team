"""지킬 돈 계산 (docs/api/budgets.md P-01 ~ P-11). 저장은 api.py에서 한다."""
import logging
from datetime import timedelta

from django.db.models import Q, Sum
from django.utils import timezone

from finance.models import Loan, RecurringExpense, RecurringSaving, SyncRun

logger = logging.getLogger(__name__)


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

# 카드 수집기가 SyncRun에 남길 조회 종류 (finance.sync 카드 쪽이 생기면 같은 값을 쓴다)
CARD_BILL_SYNC = "card_bill_list"          # 청구서 조회
CARD_SPENDING_SYNC = "card_approval_list"  # 승인 · 취소 내역 조회


def covered_until(runs, start):
    """성공한 조회 기간(covered_from ~ covered_to)을 start부터 빈틈없이 이어 붙인 마지막 날. start를 못 덮으면 None"""
    if start is None:
        return None
    reach = start - timedelta(days=1)
    for run_from, run_to in sorted(runs):
        if run_from > reach + timedelta(days=1):
            break
        reach = max(reach, run_to)
    return reach if reach >= start else None


def covered(runs, start, end):
    reach = covered_until(runs, start)
    return reach is not None and reach >= end


def history_until(card, product_code, start):
    """서버가 남긴 조회 기록으로만 판단한다 (화면 입력 · 거래 건수로 대신하지 않음, cards.md 선차감 함수 계약)"""
    runs = SyncRun.objects.filter(
        card=card, product_code=product_code, status="success",
        covered_from__isnull=False, covered_to__isnull=False,
    ).values_list("covered_from", "covered_to")
    return covered_until(list(runs), start)


def selected_cards(user):
    """선차감 대상: 카드 동의가 있고, 연결된(connected · relogin) 카드 중 관리 대상으로 고른 카드"""
    from finance.api_cards import ConsentRequired, candidate_cards, require_card_consent

    try:
        require_card_consent(user)
    except ConsentRequired:
        return []  # 카드 동의를 안 했으면 카드 청구서가 없다
    return list(candidate_cards(user).filter(is_selected=True))


def fixed_linked_amount(user, card, start, end):
    """이번 주기 그 카드 소비 중 고정지출 납부로 연결된 금액 (취소 차감). '그 카드로 쓴 돈'에서 되돌려 두 번 빼지 않게 쓴다"""
    from finance.api import cancel_info, fixed_expense_id
    from finance.models import FinancialTransaction

    rows = FinancialTransaction.objects.filter(
        user=user, card=card, source_kind="card", direction="out", transaction_type="spending",
        occurred_on__gte=start, occurred_on__lte=end,
    ).prefetch_related("payment_matches__due")
    return sum(tx.amount - cancel_info(tx)["amount"] for tx in rows if fixed_expense_id(tx) is not None)


def card_fixed_by_card(user, cards, cycle, today):
    """카드별로 선차감에서 뺄 확정 고정지출 합계. 고정지출 하나는 카드 한 장에서만 뺀다 (리뷰 10/28).

    어느 카드에서 뺄지: ① 이번 주기에 실제로 결제돼 납부로 연결된 카드(먼저 결제된 것) ② 없으면 고정지출에 적어 둔 결제 카드.
    적어 둔 카드와 실제 결제 카드가 달라도 한 번만 빠진다. 대상 카드(관리 대상) 밖이면 빼지 않는다.
    """
    from finance.api import fixed_expense_id
    from finance.models import FinancialTransaction

    card_ids = {card.pk for card in cards}
    if not card_ids:
        return {}
    end = min(today, cycle.ends_before - timedelta(days=1))
    owner = {}
    paid = FinancialTransaction.objects.filter(
        user=user, card_id__in=card_ids, source_kind="card", direction="out", transaction_type="spending",
        occurred_on__gte=cycle.starts_on, occurred_on__lte=end,
    ).prefetch_related("payment_matches__due").order_by("occurred_on", "pk")
    for tx in paid:
        expense_id = fixed_expense_id(tx)
        if expense_id is not None:
            owner.setdefault(expense_id, tx.card_id)
    expenses = RecurringExpense.objects.filter(user=user, status="confirmed").filter(
        Q(pk__in=list(owner)) | Q(payment_card_id__in=card_ids),
    ).values_list("pk", "payment_card_id", "amount")
    totals = {}
    for expense_id, payment_card_id, amount in expenses:
        card_id = owner.get(expense_id, payment_card_id)
        totals[card_id] = totals.get(card_id, 0) + amount
    return totals


def card_deduction(user, card, cycle, today, card_fixed=0):
    """카드 한 장의 첫 청구서 선차감. (금액, 보류 이유) — 금액이 None이면 보류"""
    from django.http import Http404
    from finance.card_deduction import get_card_first_bill_deduction
    from finance.models import CardBill

    connected_on = timezone.localdate(card.first_connected_at) if card.first_connected_at else None
    bill_until = history_until(card, CARD_BILL_SYNC, connected_on)
    spend_until = history_until(card, CARD_SPENDING_SYNC, cycle.starts_on)
    # 기준일 = 청구서 · 거래를 마지막으로 다 불러온 날 (오늘 조회가 없다고 매일 막지 않는다).
    # 그 뒤에 쓴 돈은 빼지 않으므로 선차감이 조금 클 수는 있어도 작아지지는 않는다.
    as_of = min(today, cycle.ends_before - timedelta(days=1))
    bill_complete = bill_until is not None and bill_until >= cycle.starts_on
    if bill_complete:
        as_of = min(as_of, bill_until)
    if spend_until is not None:
        as_of = min(as_of, spend_until)
    as_of = max(as_of, cycle.starts_on)
    if connected_on is not None and connected_on <= today and as_of < connected_on:
        # 이번 주기에 연결했는데 카드 거래를 연결일까지 못 불러옴 → 기준일이 연결 전으로 가면 '연결 전'으로 0원이 되므로 보류
        return None, "spending_history_incomplete"
    try:
        result = get_card_first_bill_deduction(
            user, card.pk, cycle, as_of=as_of,
            bill_history_complete=bill_complete, spending_history_complete=spend_until is not None,
        )
        if result.amount is None:
            return None, result.reason
        if result.cycle_card_spending is None:
            return result.amount, None  # 첫 청구서가 이번 주기 밖 · 연결 전 → 0원
        total = CardBill.objects.get(pk=result.bill_id, user=user).total_amount
    except (Http404, CardBill.DoesNotExist, ValueError, TypeError) as error:
        # 남의 연결이 섞이거나 금액이 음수인 이상한 자료 → 화면 전체를 에러로 만들지 않고 이 카드만 보류.
        # 코드 버그도 여기로 올 수 있어 로그는 남긴다 (예외 원문 · 금액은 남기지 않음)
        logger.warning("카드 선차감 계산 보류 card=%s error=%s", card.pk, type(error).__name__)
        return None, "card_data_invalid"
    # P-04: 확정 고정지출은 결제 수단과 상관없이 고정비에 이미 들어 있으므로, 이 카드에 배정된 고정지출(card_fixed_by_card)은 선차감에서 뺀다.
    # 이번 주기에 이미 결제돼 고정지출에 연결된 카드 거래는 '그 카드로 쓴 돈'에서 되돌려 두 번 빼지 않는다.
    end = min(as_of, cycle.ends_before - timedelta(days=1))
    other_spending = result.cycle_card_spending - fixed_linked_amount(user, card, cycle.starts_on, end)
    # 카드별로 0원 아래로는 안 내리므로, 배정된 카드의 남은 금액보다 고정지출이 크면 그 차이는 다른 카드에서 빼지 않는다
    # (선차감이 조금 커지는 쪽 = 예산이 모자라지 않는 쪽).
    return max(0, total - other_spending - card_fixed), None


def card_reserved(user, cycle, as_of=None):
    """첫 카드 청구서 선차감(결정 Q · cards.md C-17) 합계.

    관리 대상 카드마다 세진의 get_card_first_bill_deduction으로 계산한다.
    한 장이라도 계산 보류면 amount=None (0원으로 치지 않음) 이고 pending에 카드와 이유를 담는다.
    TODO 세진: 이번 회차 할부금 (cards.md C-17 · 366줄)
    """
    today = as_of or timezone.localdate()
    total, pending = 0, []
    cards = selected_cards(user)
    card_fixed = card_fixed_by_card(user, cards, cycle, today)
    for card in cards:
        amount, reason = card_deduction(user, card, cycle, today, card_fixed.get(card.pk, 0))
        if amount is None:
            pending.append({"card_id": str(card.pk), "reason": reason})
        else:
            total += amount
    return {"amount": None if pending else total, "pending": pending}


def fixed_breakdown(user, cycle, *, card=True):
    """고정지출 내역. card=False면 카드 선차감은 계산하지 않는다 (승인 때 초안에 저장한 값을 쓰는 경우)"""
    # 결제 수단에 상관없이 확정 고정지출을 모두 포함한다 (P-04).
    categories = dict(RecurringExpense.objects.filter(
        user=user, status="confirmed",
    ).values("category_id").annotate(total=Sum("amount")).values_list("category_id", "total"))
    loans = Loan.objects.filter(user=user, status="repaying").aggregate(total=Sum("monthly_payment"))["total"] or 0
    reserved = card_reserved(user, cycle) if card else {"amount": None, "pending": []}
    return {
        "categories": categories, "loan": loans,
        "card_ids": sorted(str(c.pk) for c in selected_cards(user)),
        "card_reserved": reserved["amount"], "card_pending": reserved["pending"],
    }


def fixed_base(fixed):
    """카드 선차감을 뺀 고정지출 합계 (확정 고정지출 + 대출)"""
    return sum(fixed["categories"].values()) + fixed["loan"]


def fixed_sum(fixed):
    """카드 선차감을 아직 모르면 None (고정지출 합계도 모름)"""
    if fixed["card_reserved"] is None:
        return None
    return fixed_base(fixed) + fixed["card_reserved"]


def fixed_total(user, cycle):
    return fixed_sum(fixed_breakdown(user, cycle))


def savings_min(user):
    return RecurringSaving.objects.filter(user=user, status="confirmed").aggregate(total=Sum("amount"))["total"] or 0

"""지킬 돈 계산 (docs/api/budgets.md P-01 ~ P-11). 저장은 api.py에서 한다."""
import logging
from datetime import timedelta

from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from finance.card_deduction import bill_kind
from finance.models import CardBill, CardBillItem, Loan, RecurringExpense, RecurringSaving, SyncRun

from .models import BudgetCycle

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


def history_until(cards, product_code, start):
    """서버가 남긴 조회 기록으로만 판단한다 (화면 입력 · 거래 건수로 대신하지 않음, cards.md 선차감 함수 계약).
    청구서 · 승인내역은 카드사 단위로 한 번에 불러오므로 그 카드사 카드들의 기록을 이어 붙인다"""
    runs = SyncRun.objects.filter(
        card__in=cards, product_code=product_code, status="success",
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


def provider_cards(user):
    """카드사에서 불러온 카드 전체 (관리 대상 아닌 카드 · 해제된 연결 포함). 카드사 단위로 묶을 때 쓴다"""
    from finance.models import Card, Origin

    return list(Card.objects.filter(user=user, origin=Origin.PROVIDER).exclude(issuer_code=None).order_by("card_id"))


def fixed_linked_amount(user, cards, start, end):
    """이번 주기 그 카드들 소비 중 고정지출 납부로 연결된 금액 (취소 차감). '그 카드로 쓴 돈'에서 되돌려 두 번 빼지 않게 쓴다"""
    from finance.api import cancel_info, fixed_expense_id, with_card_fixed_id
    from finance.models import FinancialTransaction

    rows = FinancialTransaction.objects.filter(
        user=user, card__in=cards, source_kind="card", direction="out", transaction_type="spending",
        occurred_on__gte=start, occurred_on__lte=end,
    ).prefetch_related("payment_matches__due")
    return sum(tx.amount - cancel_info(tx)["amount"] for tx in with_card_fixed_id(rows) if fixed_expense_id(tx) is not None)


def card_fixed_by_card(user, cards, cycle, today):
    """카드별로 선차감에서 뺄 확정 고정지출 합계. 고정지출 하나는 카드 한 장에서만 뺀다 (리뷰 10/28).

    어느 카드에서 뺄지: ① 이번 주기에 실제로 결제돼 납부로 연결된 카드(먼저 결제된 것) ② 없으면 고정지출에 적어 둔 결제 카드.
    적어 둔 카드와 실제 결제 카드가 달라도 한 번만 빠진다. 넘겨준 카드 밖이면 빼지 않는다.
    """
    from finance.api import fixed_expense_id, has_payment_remaining, with_card_fixed_id
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
    for tx in with_card_fixed_id(paid):
        if not has_payment_remaining(tx):
            continue
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


def installment_rounds(user, issuer_code, cycle, connected_on, first_bill_ids):
    """이번 회차 할부금 (cards.md C-17): 이번 주기에 내는 청구서(첫 청구서 · 카드론 명세서 제외)의 할부 중 예산에 아직 안 잡힌 구매.

    할부는 긁을 때 전액이 소비로 잡힌다. 이번 주기나 연결 뒤 승인 예산이 있던 주기에 산 것은 회차를 또 떼면 두 번 빠지므로 뺀다.
    그 밖의 구매(연결 전 · 연결 뒤 첫 예산 주기 전)와 구매일을 모르는 할부는 뗀다.
    None = 상세를 다 못 받았거나 회차 금액을 몰라 판단할 수 없음 (보류, 0원으로 치지 않는다)
    [임시] 아직 안 온 청구서는 0원으로 친다
    [임시] 카드 고정지출로 등록한 할부는 이름으로 가려내지 않아 고정비와 함께 잡힐 수 있다 (예산이 모자라지 않는 쪽)
    """
    bills = CardBill.objects.filter(user=user, issuer_code=issuer_code).annotate(
        due_on=Coalesce("withdrawal_due_date", "payment_due_date"),
    ).filter(due_on__gte=max(cycle.starts_on, connected_on), due_on__lt=cycle.ends_before).exclude(pk__in=first_bill_ids)
    bills = [bill for bill in bills if bill_kind(bill) != "loan"]
    if any(bill.items_state != CardBill.ItemsState.COMPLETE for bill in bills):
        return None
    counted = [(cycle.starts_on, cycle.ends_before)] + [
        (max(start, connected_on), end) for start, end in BudgetCycle.objects.filter(
            user=user, budgets__status="approved",
        ).values_list("starts_on", "ends_before")
    ]
    items = CardBillItem.objects.filter(bill__in=bills, item_type=CardBillItem.ItemType.INSTALLMENT)
    amounts = [
        billed for billed, bought in items.values_list("billed_amount", "purchased_on")
        if bought is None or not any(start <= bought < end for start, end in counted)
    ]
    return None if None in amounts else sum(amounts)


def first_budget_cycle(user, connected_at, cycle):
    """이 카드사의 최초 예산 적용 주기인지 (11/06). 카드사 첫 연결(connected_at) 뒤에 승인된 예산이 이번 주기보다 앞선 주기에
    있으면 그 주기에 이미 선차감을 반영했으므로 아니다 — 저장된 승인 기록(approved_at · 주기)으로만 판단해 조회할 때마다 바뀌지 않는다.
    승인 예산이 없던 동안은 계속 '최초'이고, 나중에 연결한 카드사는 그 연결 뒤 첫 승인 예산 주기가 최초가 된다"""
    from .models import Budget

    return not Budget.objects.filter(
        user=user, approved_at__isnull=False, approved_at__gte=connected_at, cycle__starts_on__lt=cycle.starts_on,
    ).exists()


def issuer_deduction(user, issuer_code, managed, issuer_cards, cycle, today):
    """카드사 한 곳의 첫 청구서 선차감 + 이번 회차 할부금 (10/29 나은: 카드사 단위). (금액, 이유) — 금액이 None이면 보류(이유 문자열),
    금액이 있으면 이유는 안내 — 문자열(no_bill_in_cycle 등) 또는 안내 목록(중복 확인 못 함 · 상세 일부)

    managed: 그 카드사 관리 대상 카드 (쓴 돈을 뺄 카드) · issuer_cards: 그 카드사 카드 전체 (첫 연결일 · 조회 기록)
    """
    from django.http import Http404
    from finance.card_deduction import get_issuer_first_bill_deduction

    from finance.card_review import approval_blocker

    blocker = approval_blocker(user, issuer_code)
    if blocker:
        return None, blocker
    connected = [card.first_connected_at for card in issuer_cards]
    connected_on = timezone.localdate(min(connected)) if connected and None not in connected else None
    # 11/06: 대상 청구서 = 이번 주기에 결제일이 든 청구서라 조회 기록도 연결일이 아니라 주기 시작일부터 이어져야 한다
    bill_until = history_until(issuer_cards, CARD_BILL_SYNC, cycle.starts_on)
    spend_until = history_until(issuer_cards, CARD_SPENDING_SYNC, cycle.starts_on)
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
        result = get_issuer_first_bill_deduction(
            user, issuer_code, cycle, card_ids=[card.pk for card in managed], as_of=as_of,
            bill_history_complete=bill_complete, spending_history_complete=spend_until is not None,
            first_cycle=connected_on is None or first_budget_cycle(user, min(connected), cycle),
        )
        if result.amount is None:
            if result.reason == "first_bill_unavailable":
                if spend_until is None:
                    return None, "spending_history_incomplete"
                if bill_until is not None and bill_until >= cycle.ends_before - timedelta(days=1):
                    return 0, "no_bill_in_cycle"
                # 정상 조회했지만 아직 청구서가 없음: 선차감 없이 시작하되 미반영 사유를 예산 snapshot에 남긴다.
                # 같은 주기 재추천에서는 first_budget_cycle이 계속 True이므로 새 청구서를 기존 방식으로 반영한다.
                return 0, "first_bill_unavailable"
            return None, result.reason
        rounds = installment_rounds(user, issuer_code, cycle, connected_on, result.bill_ids)
        if rounds is None:
            return None, "installment_amount_unknown"
        if result.cycle_card_spending is None:
            # 연결 전 주기 · 이미 반영한 카드사 · 이번 주기 청구서 없음 → 할부금만. 이유는 보류가 아니라 안내(card_notes)로
            return result.amount + rounds, result.reason
    except (Http404, ValueError, TypeError) as error:
        # 남의 연결이 섞이거나 금액이 음수인 이상한 자료 → 화면 전체를 에러로 만들지 않고 이 카드사만 보류.
        # 코드 버그도 여기로 올 수 있어 로그는 남긴다 (예외 원문 · 금액은 남기지 않음)
        logger.warning("카드 선차감 계산 보류 issuer=%s error=%s", issuer_code, type(error).__name__)
        return None, "card_data_invalid"
    # 11/06: 중복 차감은 같은 지출이라는 근거가 있는 금액만 (card_deduction.cycle_overlap — 이번 주기 소비 전체 · 카드 고정비 합계를
    # 일괄로 빼지 않음). 예산 반영액은 '앞으로 낼 금액'으로 줄이지 않는다 (이번 월급으로 이미 낸 카드값도 이번 예산에서 쓴 돈).
    # 이번 주기 전에 낸 금액(연결된 출금 근거)만 이미 빠져 있다. 확인 못 한 중복은 빼지 않고 안내로 남긴다
    notes = []
    if result.uncertain_count:
        notes.append({"reason": "card_overlap_uncertain", "amount": result.uncertain_amount, "count": result.uncertain_count})
    if result.items_incomplete:
        notes.append({"reason": "bill_items_incomplete", "amount": None, "count": None})  # 빠진 항목의 건수 · 금액은 모른다
    return result.amount + rounds, notes or None


def card_reserved(user, cycle, as_of=None):
    """첫 카드 청구서 선차감(결정 Q · cards.md C-17)과 이번 회차 할부금 합계.

    관리 대상 카드가 있는 카드사마다 세진의 get_issuer_first_bill_deduction으로 계산한다 (10/29 나은: 카드사 단위).
    한 곳이라도 계산 보류면 amount=None (0원으로 치지 않음) 이고 pending에 그 카드사의 관리 대상 카드와 이유를 담는다.
    """
    from finance.card_review import foreign_pending_count

    today = as_of or timezone.localdate()
    total, pending, notes = 0, [], []
    managed = selected_cards(user)
    if not managed:
        return {"amount": 0, "pending": []}
    cards = provider_cards(user)
    by_issuer = {}
    for card in managed:
        by_issuer.setdefault(card.issuer_code, []).append(card)
    for issuer_code in sorted(by_issuer, key=lambda code: code or ""):
        group = by_issuer[issuer_code]
        if not issuer_code:
            amount, reason = None, "card_data_invalid"  # 카드사를 모르는 카드
        else:
            amount, reason = issuer_deduction(
                user, issuer_code, group, [card for card in cards if card.issuer_code == issuer_code], cycle, today,
            )
        if amount is None:
            pending.extend({"card_id": str(card.pk), "reason": reason} for card in group)
        else:
            total += amount
            if isinstance(reason, list):  # 카드사 하나에 한 번 (금액을 카드마다 겹쳐 보이지 않게)
                notes.extend({"card_id": str(group[0].pk), "issuer_code": issuer_code, **note} for note in reason)
            elif reason:  # 금액은 정해졌고 알릴 것만 있음 (no_bill_in_cycle · first_budget_applied · before_first_connection)
                notes.extend({"card_id": str(card.pk), "reason": reason} for card in group)
            # 금액을 몰라 소비에 넣지 못한 이번 주기 외화 결제. 금액을 만들지 않고 건수만 알린다
            foreign = foreign_pending_count(user, issuer_code, cycle.starts_on, min(today, cycle.ends_before - timedelta(days=1)))
            if foreign:
                notes.append({"card_id": str(group[0].pk), "issuer_code": issuer_code,
                              "reason": "foreign_amount_pending", "amount": None, "count": foreign})
    result = {"amount": None if pending else total, "pending": pending}
    return {**result, "notes": notes} if notes else result  # 안내가 있을 때만 (내부 값)


def unreflected_card_ids(notes):
    """금액이 같아도 청구서 미반영 대상이 바뀌면 승인 전 추천을 갱신해야 한다."""
    return {note["card_id"] for note in notes if note.get("reason") == "first_bill_unavailable"}


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
        "card_reserved": reserved["amount"], "card_pending": reserved["pending"], "card_notes": reserved.get("notes", []),
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

from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID


def calculate_first_bill_deduction(
    *,
    is_first_bill: bool | None,
    total_amount: int | None,
    cycle_card_spending: int | None,
) -> int | None:
    """첫 청구서 총액에서 이번 주기 카드 순사용액을 뺀다(최소 0원).

    이후 청구서는 0원, 자료 미확인은 None. None을 0원으로 합산하지 않는다.
    total_amount는 미납액이 아닌 카드사 첫 청구서 총액이다.
    """
    if is_first_bill is not None and type(is_first_bill) is not bool:
        raise TypeError("is_first_bill은 bool 또는 None이어야 합니다.")
    if is_first_bill is None:
        return None
    if not is_first_bill:
        return 0

    for name, value in (
        ("total_amount", total_amount),
        ("cycle_card_spending", cycle_card_spending),
    ):
        if value is not None:
            # bool은 금액으로 받지 않는다.
            if type(value) is not int:
                raise TypeError(f"{name}는 원 단위 정수 또는 None이어야 합니다.")
            if value < 0:
                raise ValueError(f"{name}는 0원 이상이어야 합니다.")

    if total_amount is None or cycle_card_spending is None:
        return None
    return max(0, total_amount - cycle_card_spending)


# 같은 날 내는 명세서 구분(resBillType). CODEF 공통 코드표가 없어 숫자 코드는 해석하지 않는다 (10/29 데모 현대카드: 빈 값)
LOAN_BILL_WORDS = ("카드론", "장기카드대출")
USAGE_BILL_WORDS = ("이용", "사용")


def bill_kind(bill):
    """usage 카드 사용 명세서 · loan 카드론 명세서 · blank 빈 값 · unknown 모르는 값"""
    value = ((bill.field_states or {}).get("codefBillType") or "").strip()
    if not value:
        return "blank"
    if any(word in value for word in LOAN_BILL_WORDS):
        return "loan"
    if any(word in value for word in USAGE_BILL_WORDS):
        return "usage"
    return "unknown"


def usage_statements(bills):
    """카드론 명세서는 뺀다. 한 장이면 빈 값·모르는 값도 포함하고, 여러 장이면 사용 명세서만 합친다."""
    kinds = [bill_kind(bill) for bill in bills]
    if len(bills) == 1:
        return ([] if kinds[0] == "loan" else bills), None
    if "unknown" in kinds:
        return None, "bill_type_unknown"
    if "blank" in kinds and len(bills) > 1:
        return None, "first_bill_ambiguous"
    return [bill for bill, kind in zip(bills, kinds) if kind != "loan"], None


@dataclass(frozen=True)
class IssuerDeduction:
    issuer_code: str
    amount: int | None
    bill_ids: tuple[UUID, ...] = ()
    total_amount: int | None = None
    cycle_card_spending: int | None = None   # 11/06: 청구서에 든 이번 주기 사용 중 예산에 이미 잡힌 것(확인된 중복)만
    reason: str | None = None
    uncertain_amount: int = 0                # 중복인지 확인 못 해 빼지 않은 금액 (건별 금액을 아는 것만)
    uncertain_count: int = 0
    items_incomplete: bool = False           # 상세가 일부만 와서 빠진 항목은 알 수 없음 (건수 · 금액 모름)
    prepaid_before_cycle: int = 0            # 연결된 출금 근거상 이번 주기 시작 전에 낸 금액 (이전 월급)


COUNTED_ITEM_TYPES = ("one_time", "installment")   # 구매 항목. 수수료 · 이자 · 조정은 이번 주기 소비에 없으므로 중복이 아님


def bill_total_mismatch(bill):
    """상세의 이번 회차 금액이 모두 있는데 합계가 총액보다 크면 총액이 '원래 청구액'인지 알 수 없다 (11/06: 결제일 당일 조회에서
    총액 0원 · 상세 합계 511,515원 사례 — 납부 반영인지 미확인). 0원이나 상세 합계로 고르지 않는다"""
    billed = list(bill.items.values_list("billed_amount", flat=True))
    return bool(billed) and None not in billed and sum(billed) > bill.total_amount


def cycle_overlap(user, issuer_code, statements, start, end, managed_ids):
    """청구서 상세 중 이번 주기 예산에 이미 잡힌 지출 (11/06). 같은 지출이라는 근거가 있는 것만 중복으로 뺀다.
    - 대상: 구매일이 이번 주기 시작 ~ end(거래를 불러온 날) 인 일시불 · 할부 항목. 그 전 구매는 첫 예산 전 소비라 빼지 않는다.
    - 근거: 같은 카드사 카드 승인 거래와 구매일 · 원 구매 금액이 같고, 그 묶음(같은 날 · 같은 금액)의 항목 수와 거래 수가 같을 때만.
      거래가 이번 주기 예산에 잡혀 있어야 한다: 관리 카드 소비 또는 고정지출 근거로 연결된 결제(결제 카드와 상관없이 고정비에 있음).
      뺄 금액 = 이번 회차 청구액, 취소가 있으면 거래 순액까지. 할부는 그 회차만.
    - 확인 못 함(짝 없음 · 후보 수가 다름 · 일부만 예산에 잡힘 · 구매일 모름 · 모르는 종류): 빼지 않고 uncertain 으로 센다.
      상세가 일부만 왔으면 빠진 항목은 알 수 없어 items_incomplete 만 표시한다 (건수 · 금액을 만들지 않음)"""
    from .api import cancel_info, fixed_expense_id, with_card_fixed_id
    from .models import CardBillItem, FinancialTransaction

    confirmed = uncertain = count = 0
    groups = {}
    for item in CardBillItem.objects.filter(bill__in=statements):
        if item.item_type not in (*COUNTED_ITEM_TYPES, "unknown"):
            continue
        if item.purchased_on is not None and not start <= item.purchased_on <= end:
            continue  # 이번 주기 전(첫 예산 전 소비) · 아직 불러오지 않은 날의 구매는 이번 주기 사용액에 없다
        if item.purchased_on is None or item.item_type == "unknown" or item.billed_amount is None or \
                (item.original_purchase_amount or item.billed_amount) is None:
            if item.billed_amount:
                uncertain, count = uncertain + max(0, item.billed_amount), count + 1
            continue
        groups.setdefault((item.purchased_on, item.original_purchase_amount or item.billed_amount), []).append(item)
    if groups:
        rows = FinancialTransaction.objects.filter(
            user=user, source_kind="card", direction="out", card__issuer_code=issuer_code,
            occurred_on__in={day for day, _ in groups},
        ).prefetch_related("payment_matches__due")
        txs = {}
        for tx in with_card_fixed_id(rows):
            txs.setdefault((tx.occurred_on, tx.amount), []).append(tx)
    for key, items in groups.items():
        found = txs.get(key, [])
        counted = [tx for tx in found if tx.transaction_type == "spending"
                   and (tx.card_id in managed_ids or fixed_expense_id(tx) is not None)]
        nets = {tx.amount - cancel_info(tx)["amount"] for tx in found}
        if len(found) == len(items) and len(counted) == len(found) and len(nets) == 1:
            net = nets.pop()
            confirmed += sum(min(max(0, item.billed_amount), max(0, net)) for item in items)
        elif found and not counted:
            continue  # 짝이 모두 예산에 안 잡힌 거래(관리 대상 아닌 카드 소비) → 중복 아님, 그대로 떼어 둔다
        else:
            uncertain += sum(max(0, item.billed_amount) for item in items)
            count += len(items)
    return confirmed, uncertain, count


def prepaid_before(statements, start):
    """연결된 출금 중 이번 주기 시작 전에 낸 금액 (이전 월급으로 냄 → 이번 월급에서 다시 떼지 않음). 사용자 확인은 날짜가 없어 쓰지 않는다"""
    from .api import cancel_info
    from .models import PaymentMatch

    return sum(max(0, min(m.matched_amount, m.transaction.amount - cancel_info(m.transaction)["amount"]))
               for m in PaymentMatch.objects.filter(due__bill__in=statements, transaction__occurred_on__lt=start)
               .select_related("transaction"))


def get_issuer_first_bill_deduction(
    user, issuer_code, cycle, *, card_ids, bill_history_complete: bool,
    spending_history_complete: bool, as_of: date | None = None, first_cycle: bool = True,
) -> IssuerDeduction:
    """저장 자료로 카드사 첫 청구서의 주기 선차감액을 계산한다 (10/29 나은: 카드사 단위).

    - 대상 청구서 (11/06 기획 기준): 최초 예산 적용 주기에 결제일(출금 예정일, 없으면 결제 예정일)이 starts_on 이상 ·
      ends_before 미만인 카드사 통합 카드 사용 명세서 전부. 결제일이 카드 연결일보다 앞서도 포함한다(서비스 사용 전 카드 소비 중
      이번 주기에 갚을 돈). 주기 밖 청구서는 가져오지 않는다. 정정으로 대체된 예전 청구서(R04)는 뺀다.
      날짜마다 카드론 명세서는 빼고, 같은 날 한 장이면 빈 값·모르는 값도 포함하며, 여러 장이면 사용 명세서만 합친다.
    - first_cycle=False(이 카드사가 이미 승인 예산에 반영된 뒤의 주기, budgets.protect.issuer_deduction 이 판단) → 0원.
      이번 주기 대상 청구서가 없으면 first_bill_unavailable. issuer_deduction이 조회 범위를 확인해
      미반영 안내와 함께 선차감 없이 시작하도록 한다. 주기 끝까지 조회됐으면 no_bill_in_cycle이다.
    - 쓴 돈: card_ids(그 카드사 관리 대상 카드)로 이번 주기에 쓴 돈만 뺀다. 관리 대상 아닌 카드 몫은 같이 뗀다.
    서버가 청구 이력(주기 시작~기준일)과 거래·취소(주기 시작~기준일)의 수집 완료 여부를 전달한다.
    거래 범위는 주기 마지막 날까지만 포함한다. amount=None은 계산 보류이며 reason을 확인한다. DB·승인 예산은 변경하지 않는다.
    """
    from django.http import Http404
    from django.utils import timezone

    from .api_cards import require_card_consent
    from .models import Card, CardBill, Origin

    if type(bill_history_complete) is not bool or type(spending_history_complete) is not bool:
        raise TypeError("조회 범위 확인 값은 bool이어야 합니다.")
    if cycle.user_id != user.pk:
        raise Http404
    if cycle.starts_on >= cycle.ends_before:
        raise ValueError("급여 주기 시작일은 종료 경계일보다 빨라야 합니다.")
    as_of = timezone.localdate() if as_of is None else as_of
    if type(as_of) is not date:
        raise TypeError("as_of는 date여야 합니다.")
    if as_of < cycle.starts_on:
        raise ValueError("조회 기준일은 급여 주기 시작일 이후여야 합니다.")

    require_card_consent(user)

    def check_owner(obj):
        if obj is not None and obj.user_id != user.pk:
            raise Http404

    cards = list(Card.objects.filter(user=user, issuer_code=issuer_code, origin=Origin.PROVIDER)
                 .select_related("connection", "payment_account__connection")) if issuer_code else []
    for card in cards:
        check_owner(card.connection)
        if card.connection and card.connection.business_type != "card":
            raise Http404
        check_owner(card.payment_account)
        if card.payment_account:
            check_owner(card.payment_account.connection)
    card_ids = set(card_ids)
    if not card_ids or not card_ids <= {card.pk for card in cards}:
        raise Http404  # 남의 카드 · 다른 카드사 카드

    def pending(reason, bills=()):
        return IssuerDeduction(issuer_code, None, tuple(bill.pk for bill in bills), reason=reason)

    if any(card.first_connected_at is None for card in cards):
        return pending("first_connection_unknown")  # 가장 먼저 연결된 날을 확인할 수 없음
    connected_on = min(timezone.localdate(card.first_connected_at) for card in cards)
    if connected_on >= cycle.ends_before or connected_on > as_of:
        return IssuerDeduction(issuer_code, 0, reason="before_first_connection")
    if not bill_history_complete:
        return pending("bill_history_incomplete")

    from .card_payments import card_bills_and_dues, replaced_bill_ids  # card_payments 가 이 파일을 불러서 여기서

    replaced = replaced_bill_ids(*card_bills_and_dues(user))
    bills = CardBill.objects.filter(user=user, issuer_code=issuer_code).select_related(
        "connection", "payment_account__connection",
    )
    by_due = {}
    for bill in bills:
        check_owner(bill.connection)
        check_owner(bill.payment_account)
        if bill.payment_account:
            check_owner(bill.payment_account.connection)
        due = bill.withdrawal_due_date or bill.payment_due_date
        if due is not None and not cycle.starts_on <= due < cycle.ends_before or bill.pk in replaced:
            continue  # 이번 주기 밖 청구서 · 정정으로 대체된 청구서는 보지 않는다 (연결일과는 비교하지 않음)
        if bill.connection and (
            bill.connection.business_type != "card"
            or bill.connection.organization_code != bill.issuer_code
        ):
            return pending("bill_scope_unknown", [bill])
        if due is None:
            return pending("bill_due_date_unknown", [bill])
        # 카드 한 장 · 범위 모름 청구서는 카드사 총액이 아니다
        if bill.scope_type != CardBill.ScopeType.ISSUER:
            return pending("bill_scope_unknown", [bill])
        by_due.setdefault(due, []).append(bill)
    if not first_cycle:
        # 이미 반영한 카드사: 이번 주기 청구서는 할부 회차만 본다 (bill_ids 비움 = installment_rounds 가 제외하지 않음,
        # 같은 날 명세서 구분 판단도 필요 없음 — 예전처럼 첫 청구서 밖 청구서로 막지 않는다)
        return IssuerDeduction(issuer_code, 0, reason="first_budget_applied")
    first = []
    for due in sorted(by_due):
        statements, reason = usage_statements(by_due[due])
        if reason:
            return pending(reason, by_due[due])
        first.extend(statements)
    bill_ids = tuple(sorted(bill.pk for bill in first))
    if not first:
        return pending("first_bill_unavailable")  # 이번 주기 청구서를 못 찾음 (정상 빈 조회인지는 조회 범위로 호출 쪽이 판단)
    if not spending_history_complete:
        return pending("spending_history_incomplete", first)
    if any(bill.total_amount < 0 for bill in first):
        return pending("bill_amount_invalid", first)
    if any(bill_total_mismatch(bill) for bill in first):
        return pending("bill_total_mismatch", first)  # 원래 청구액을 확정할 수 없음 → 사용자 확인 전까지 보류
    total = sum(bill.total_amount for bill in first)

    # 11/06: 이번 주기 카드 소비 전체가 아니라, 이 청구서에 든 것 중 이번 주기 예산에 이미 잡힌 것만 뺀다 (cycle_overlap)
    end = min(as_of, cycle.ends_before - timedelta(days=1))
    overlap, uncertain, count = cycle_overlap(user, issuer_code, first, cycle.starts_on, end, card_ids)
    prepaid = prepaid_before(first, cycle.starts_on)
    amount = calculate_first_bill_deduction(
        is_first_bill=True, total_amount=max(0, total - prepaid), cycle_card_spending=overlap,
    )
    return IssuerDeduction(
        issuer_code, amount, bill_ids, total, overlap, uncertain_amount=uncertain, uncertain_count=count,
        items_incomplete=any(bill.items_state != CardBill.ItemsState.COMPLETE and not bill_total_mismatch(bill) for bill in first),
        prepaid_before_cycle=prepaid,
    )

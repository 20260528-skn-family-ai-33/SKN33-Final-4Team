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
    """같은 날 내는 명세서 → (카드 사용 명세서 목록, 보류 이유). 카드론 명세서는 뺀다.
    빈 값은 그날 명세서가 한 장일 때만 사용 명세서로 본다 (10/29 나은)"""
    kinds = [bill_kind(bill) for bill in bills]
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
    cycle_card_spending: int | None = None
    reason: str | None = None


def get_issuer_first_bill_deduction(
    user, issuer_code, cycle, *, card_ids, bill_history_complete: bool,
    spending_history_complete: bool, as_of: date | None = None,
) -> IssuerDeduction:
    """저장 자료로 카드사 첫 청구서의 주기 선차감액을 계산한다 (10/29 나은: 카드사 단위).

    - 첫 청구서: 그 카드사 카드 중 가장 먼저 연결된 날(first_connected_at) 이후 처음 내는 카드 사용 명세서.
      같은 날 여러 장이면 합치고 카드론 명세서는 뺀다. 카드사에 한 번뿐이라 나중에 고른 카드로 다시 생기지 않는다.
    - 쓴 돈: card_ids(그 카드사 관리 대상 카드)로 이번 주기에 쓴 돈만 뺀다. 관리 대상 아닌 카드 몫은 같이 뗀다.
    서버가 청구 이력(최초 연결~기준일)과 거래·취소(주기 시작~기준일)의 수집 완료 여부를 전달한다.
    거래 범위는 주기 마지막 날까지만 포함한다. amount=None은 계산 보류이며 reason을 확인한다. DB·승인 예산은 변경하지 않는다.
    """
    from django.http import Http404
    from django.utils import timezone

    from .api import cancel_info
    from .api_cards import require_card_consent
    from .models import Card, CardBill, FinancialTransaction, Origin

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
        if due is not None and due < connected_on:
            continue  # 연결 전 청구서 제외
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
    first = None
    for due in sorted(by_due):
        statements, reason = usage_statements(by_due[due])
        if reason:
            return pending(reason, by_due[due])
        if statements:
            first = statements
            break
    if first is None:
        return pending("first_bill_unavailable")
    bill_ids = tuple(sorted(bill.pk for bill in first))
    if not cycle.starts_on <= due < cycle.ends_before:
        return IssuerDeduction(issuer_code, 0, bill_ids, reason="first_bill_outside_cycle")
    if not spending_history_complete:
        return pending("spending_history_incomplete", first)
    if any(bill.total_amount < 0 for bill in first):
        return pending("bill_amount_invalid", first)
    total = sum(bill.total_amount for bill in first)

    end = min(as_of, cycle.ends_before - timedelta(days=1))
    transactions = FinancialTransaction.objects.filter(
        card_id__in=card_ids, source_kind="card", direction="out", transaction_type="spending",
        occurred_on__gte=cycle.starts_on, occurred_on__lte=end,
    ).only("user_id", "amount", "canceled_amount", "is_canceled")
    spending = 0
    for tx in transactions:
        check_owner(tx)
        # 기존 거래 API와 동일하게 취소액을 뺀다.
        spending += tx.amount - cancel_info(tx)["amount"]
    amount = calculate_first_bill_deduction(
        is_first_bill=True, total_amount=total, cycle_card_spending=spending,
    )
    return IssuerDeduction(issuer_code, amount, bill_ids, total, spending)

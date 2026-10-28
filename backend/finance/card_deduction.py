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
    total_amount는 미납액이 아닌 카드별 청구 총액이다.
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


@dataclass(frozen=True)
class CardDeduction:
    card_id: UUID
    amount: int | None
    bill_id: UUID | None = None
    cycle_card_spending: int | None = None
    reason: str | None = None


def get_card_first_bill_deduction(
    user, card_id, cycle, *, bill_history_complete: bool,
    spending_history_complete: bool, as_of: date | None = None,
) -> CardDeduction:
    """저장 자료로 카드별 최초 연결 후 첫 청구서의 주기 선차감액을 계산한다.

    서버가 청구 이력(최초 연결~기준일)과 거래·취소(주기 시작~기준일)의
    수집 완료 여부를 전달한다. 거래 범위는 주기 마지막 날까지만 포함한다.
    amount=None은 계산 보류이며 reason을 확인한다. DB·승인 예산은 변경하지 않는다.
    """
    from django.http import Http404
    from django.db.models import Q
    from django.shortcuts import get_object_or_404
    from django.utils import timezone

    from .api import cancel_info
    from .api_cards import require_card_consent
    from .models import Card, CardBill, FinancialTransaction

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
    card = get_object_or_404(Card.objects.select_related("connection", "payment_account__connection"), pk=card_id, user=user)

    def check_owner(obj):
        if obj is not None and obj.user_id != user.pk:
            raise Http404

    check_owner(card.connection)
    if card.connection and card.connection.business_type != "card":
        raise Http404
    check_owner(card.payment_account)
    if card.payment_account:
        check_owner(card.payment_account.connection)

    def pending(reason, bill=None):
        return CardDeduction(card.pk, None, bill.pk if bill else None, reason=reason)

    if card.first_connected_at is None:
        return pending("first_connection_unknown")
    connected_on = timezone.localdate(card.first_connected_at)
    if connected_on >= cycle.ends_before or connected_on > as_of:
        return CardDeduction(card.pk, 0, reason="before_first_connection")
    if not bill_history_complete:
        return pending("bill_history_incomplete")

    # 카드사 통합 청구는 임의로 나누지 않는다.
    scope = Q(card=card)
    if card.issuer_code:
        scope |= Q(card__isnull=True, issuer_code=card.issuer_code)
    bills = CardBill.objects.filter(scope, user=user).select_related("connection", "payment_account__connection")
    candidates = []
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
            return pending("bill_scope_unknown", bill)
        if due is None:
            return pending("bill_due_date_unknown", bill)
        if bill.scope_type != CardBill.ScopeType.CARD or bill.card_id != card.pk:
            return pending("bill_scope_unknown", bill)
        if card.issuer_code and bill.issuer_code != card.issuer_code:
            return pending("bill_scope_unknown", bill)
        candidates.append((due, bill))
    if not candidates:
        return pending("first_bill_unavailable")
    candidates.sort(key=lambda item: item[0])
    due, first = candidates[0]
    if len(candidates) > 1 and candidates[1][0] == due:
        return pending("first_bill_ambiguous")  # 같은 날짜면 선택 보류
    if not cycle.starts_on <= due < cycle.ends_before:
        return CardDeduction(card.pk, 0, first.pk, reason="first_bill_outside_cycle")
    if not spending_history_complete:
        return pending("spending_history_incomplete", first)
    if first.total_amount < 0:
        return pending("bill_amount_invalid", first)

    end = min(as_of, cycle.ends_before - timedelta(days=1))
    transactions = FinancialTransaction.objects.filter(
        card=card, source_kind="card", direction="out", transaction_type="spending",
        occurred_on__gte=cycle.starts_on, occurred_on__lte=end,
    ).only("user_id", "amount", "canceled_amount", "is_canceled")
    spending = 0
    for tx in transactions:
        check_owner(tx)
        # 기존 거래 API와 동일하게 취소액을 뺀다.
        spending += tx.amount - cancel_info(tx)["amount"]
    amount = calculate_first_bill_deduction(
        is_first_bill=True, total_amount=first.total_amount, cycle_card_spending=spending,
    )
    return CardDeduction(card.pk, amount, first.pk, spending)

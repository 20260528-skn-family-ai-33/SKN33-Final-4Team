"""고정지출 확인 화면의 카드 청구서. 청구서마다 '앞으로 낼 금액' 확인 상태(card_payments.bill_status)를 준다 (11/06).
amountAfterFixed(청구액 − 카드 고정비)는 예전 참고값 그대로 — 남은 금액도 예산 선차감액도 아니다.
"""
from django.http import Http404

from finance.card_deduction import bill_kind, usage_statements
from finance.card_payments import bill_status
from finance.display import CARD_ISSUERS
from finance.models import CardBill, PaymentDue
from users.models import FinancialProfile

from .models import BudgetCycle
from .protect import card_fixed_by_card, provider_cards, selected_cards
from .salary import salary_cycle_dates


def fixed_bill_preview(user, cycle, today):
    # 동의가 유효하고 연결된 관리 대상 카드가 있는 카드사만 표시한다.
    managed = selected_cards(user)
    if not managed:
        return []
    if cycle is None or not cycle.starts_on <= today < cycle.ends_before:
        # 온보딩에는 주기가 없거나, 홈 조회 전에는 종료된 주기가 active로 남을 수 있다.
        # 표시용 기간만 계산하고 예산·납부 예정은 생성하지 않는다.
        cycle = None  # 급여 설정도 없으면 이전·미래 주기를 대신 표시하지 않는다.
        profile = FinancialProfile.objects.filter(user=user).first()
        if profile and profile.payday:
            start, end, _ = salary_cycle_dates(profile, today)
            cycle = BudgetCycle(user=user, starts_on=start, ends_before=end)
    cards = provider_cards(user)
    fixed = card_fixed_by_card(user, cards, cycle, today) if cycle else {}
    issuers = sorted({card.issuer_code or "" for card in managed})
    rows = []
    for issuer in issuers:
        row = {
            "issuer_code": issuer,
            "issuer_name": CARD_ISSUERS.get(issuer) or "카드사 확인 필요",
            "confirmed_fixed_amount": sum(fixed.get(card.pk, 0) for card in cards if card.issuer_code == issuer),
            "total_amount": None,
            "amount_after_fixed": None,
            "is_estimated": None,
            "reason": None,
            "bills": [],  # 청구서별 납부 확인 상태 (totalAmount 를 알 때만)
        }
        rows.append(row)
        if cycle is None or not issuer:
            row["reason"] = "cycle_unavailable" if cycle is None else "bill_scope_unknown"
            continue
        groups = {}
        for bill in CardBill.objects.filter(user=user, issuer_code=issuer).select_related("connection"):
            if bill.connection and bill.connection.user_id != user.pk:
                raise Http404
            due = bill.withdrawal_due_date or bill.payment_due_date
            if bill_kind(bill) == "loan" and due is None:
                continue
            if due is None:
                row["reason"] = "bill_due_date_unknown"
                break
            if not cycle.starts_on <= due < cycle.ends_before:
                continue
            if bill.scope_type != CardBill.ScopeType.ISSUER or (bill.connection and (
                bill.connection.business_type != "card" or bill.connection.organization_code != issuer
            )):
                row["reason"] = "bill_scope_unknown"
                break
            groups.setdefault(due, []).append(bill)
        if row["reason"]:
            continue
        statements = []
        for due in sorted(groups):
            group, reason = usage_statements(groups[due])
            if reason:
                row["reason"] = reason
                break
            statements.extend(group)
        if row["reason"]:
            continue
        if not statements:
            row["reason"] = "bill_unavailable"
            continue
        if any(bill.total_amount < 0 for bill in statements):
            row["reason"] = "bill_amount_invalid"
            continue
        total = sum(bill.total_amount for bill in statements)
        states = {bill.confirmation_state for bill in statements}
        row.update(
            total_amount=total,
            amount_after_fixed=max(0, total - row["confirmed_fixed_amount"]),
            # 카드사가 예상 청구라고 준 것만 true. 확정 여부를 모르면(null · unknown) 예상이라고 하지 않고 null
            is_estimated=True if CardBill.ConfirmationStatus.ESTIMATED in states
            else False if states == {CardBill.ConfirmationStatus.CONFIRMED} else None,
        )
        dues = {due.bill_id: due for due in PaymentDue.objects.filter(user=user, bill__in=statements)}
        row["bills"] = [bill_status(bill, dues.get(bill.pk), today) for bill in statements]
    return rows

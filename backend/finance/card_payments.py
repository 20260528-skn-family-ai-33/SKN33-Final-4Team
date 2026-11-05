"""카드대금 출금 ↔ 카드 청구서 매칭 (pipeline.match_card_payments).

10/28 결정: 청구서는 카드사 단위 통합이고 카드론 명세서는 뺀다. 같은 날 사용 명세서가 여러 장이면 합친 금액 한 번으로 낸다.
청구서를 저장하면 출금 전에도 납부 예정을 만들어 홈 '꼭 나갈 돈'에 보인다 (sync_bill_dues, R04). 출금을 찾으면 그 납부 예정에 붙인다.
"""
import logging
from datetime import timedelta

from django.db import transaction

from django.db.models import Exists, OuterRef
from django.db.models.functions import Coalesce
from django.utils import timezone

from .card_deduction import usage_statements
from .models import Card, CardBill, FinancialTransaction as Tx, PaymentDue, PaymentMatch
from .transfer_rules import card_issuer

WINDOW = timedelta(days=3)
logger = logging.getLogger(__name__)


def due_key(bill):
    """카드 청구서 납부 예정의 중복 방지 키. 예정이 정정된 청구서로 옮겨 가면 키도 같이 바꾼다 (예전 청구서가 다시 와도 겹치지 않게)"""
    return f"card_bill:{bill.pk}"


def bill_due_on(bill):
    """납부 예정일 = 출금 예정일, 없으면 결제 예정일 (11/5 나은: 카드는 결제일에 출금된다. 매칭 · 선차감과 같은 기준). 둘 다 없으면 None"""
    return bill.withdrawal_due_date or bill.payment_due_date


def unpaid_bills(user, issuer_code, day):
    """출금일(없으면 결제일)이 ±3일 안인 카드사 통합 청구서를 날짜별로. 이미 납부가 붙은 청구서와 낼 돈이 없는 0원 명세서는 뺀다.
    [임시] 청구 월은 따로 비교하지 않는다: 달이 다른 청구서는 날짜에서 걸러진다"""
    bills = CardBill.objects.filter(
        user=user, issuer_code=issuer_code, scope_type=CardBill.ScopeType.ISSUER, total_amount__gt=0,
    ).annotate(
        due_on=Coalesce("withdrawal_due_date", "payment_due_date"),
        paid=Exists(PaymentMatch.objects.filter(due__bill=OuterRef("pk"))),
    ).filter(due_on__range=(day - WINDOW, day + WINDOW), paid=False).exclude(pk__in=replaced_bill_ids(*card_bills_and_dues(user)))
    by_due = {}
    for bill in bills:
        by_due.setdefault(bill.due_on, []).append(bill)
    return by_due


def paying_accounts(user, issuer_code, bills):
    """[임시] 청구서의 결제 계좌, 없으면 그 카드사 카드의 결제 계좌. 수집기가 아직 못 채워 모르면 빈 집합 = 계좌는 보지 않는다"""
    accounts = {bill.payment_account_id for bill in bills} - {None}
    return accounts or set(Card.objects.filter(
        user=user, issuer_code=issuer_code, payment_account__isnull=False,
    ).values_list("payment_account_id", flat=True))


def find_statements(user, tx):
    """이 출금이 낸 명세서들. [임시] 총액이 정확히 같을 때만 (분할 납부는 안 붙는다), 날짜가 가까운 쪽 먼저"""
    issuer_code = card_issuer(tx.source_description)
    if issuer_code is None:
        return []
    by_due = unpaid_bills(user, issuer_code, tx.occurred_on)
    for due_on in sorted(by_due, key=lambda day: abs((day - tx.occurred_on).days)):
        statements, reason = usage_statements(by_due[due_on])
        if reason or not statements or sum(bill.total_amount for bill in statements) != tx.amount:
            continue
        accounts = paying_accounts(user, issuer_code, statements)
        if not accounts or tx.account_id in accounts:
            return statements
    return []


def bill_due(bill, tx):
    """청구서의 납부 예정. 청구서 저장 때 만든 것(sync_bill_dues)이 있으면 그대로 쓰고, 계좌를 모르던 예정엔 실제 출금 계좌를 채운다"""
    due, created = PaymentDue.objects.get_or_create(bill=bill, defaults={
        "user": bill.user, "obligation_type": PaymentDue.ObligationType.CARD_BILL,
        "occurrence_key": due_key(bill), "due_date": bill.due_on, "confirmed_amount": bill.total_amount,
        "payment_account": tx.account, "source_type": bill.source_type,
        "confirmation_state": bill.confirmation_state or "unknown",
    })
    if not created and due.payment_account_id is None:
        due.payment_account = tx.account
        due.save(update_fields=["payment_account"])
    return due


def statement_groups(bills):
    """같은 카드사 · 청구 월 · 명세서 구분 묶음 → (그 달 마지막 조회 응답에 있던 청구서, 없던 청구서).
    응답에 있었는지는 청구서를 저장한 조회의 시작 시각(codefStartedAt)이 그 달의 가장 늦은 값인지로 본다. 시각이 없는 청구서는 보지 않는다"""
    def stamp(bill):
        return (bill.field_states or {}).get("codefStartedAt")

    latest, groups = {}, {}
    for bill in bills:
        if stamp(bill):
            month = (bill.issuer_code, bill.billing_month)
            latest[month] = max(latest.get(month, ""), stamp(bill))
            groups.setdefault((*month, (bill.field_states or {}).get("codefBillType") or ""), []).append(bill)
    for (issuer, month, _), group in groups.items():
        present = [b for b in group if stamp(b) == latest[(issuer, month)]]
        yield present, [b for b in group if b not in present]


def follow_date_corrections(bills, dues):
    """결제일 정정 → 같은 납부 예정을 새 청구서로 옮긴다 (R04 보완). CODEF 청구서엔 고유 번호가 없어 결제일이 키에 들어가서,
    날짜가 바뀌면 새 청구서 행이 생긴다. 날짜가 가깝다고 합치지 않고 아래가 모두 맞을 때만 정정으로 본다:
    같은 카드사 · 청구 월 · 명세서 구분에서, 그 달 마지막 조회 응답에 예정이 있던 예전 청구서는 없고 새 청구서 하나만 있으며,
    새 청구서에 아직 납부 예정이 없다(한 응답에 같이 온 적이 있으면 그때 따로 예정이 생겼으므로 다른 청구서로 본다).
    후보가 여럿이면 어느 것이 정정인지 몰라 옮기지 않는다. 정정이 되돌려지면 같은 규칙으로 다시 옮겨 온다. 납부 기록 · 예정 ID 는 그대로,
    중복 방지 키(occurrence_key)는 옮긴 청구서 기준으로 바꾼다 — 예전 청구서가 나중에 다시 오면 그 청구서에 새 예정을 만들 수 있게"""
    for present, absent in statement_groups(bills):
        gone = [b for b in absent if b.pk in dues]
        if len(present) != 1 or len(gone) != 1 or present[0].pk in dues:
            continue
        due = dues.pop(gone[0].pk)
        due.bill, due.occurrence_key = present[0], due_key(present[0])  # ID · 납부 기록은 그대로, 키만 새 청구서 기준
        due.save(update_fields=["bill", "occurrence_key"])
        dues[present[0].pk] = due


def replaced_bill_ids(bills, due_bill_ids):
    """정정으로 대체된 예전 청구서: 마지막 응답에 없고 예정도 없는데, 같은 묶음에서 응답에 있던 청구서가 하나뿐이고 그 청구서가 예정을 가짐.
    이런 청구서엔 예정을 새로 만들지 않고 출금도 붙이지 않는다 (같은 돈을 두 번 '낼 돈'으로 보이지 않게)"""
    replaced = set()
    for present, absent in statement_groups(bills):
        if len(present) == 1 and present[0].pk in due_bill_ids:
            replaced |= {b.pk for b in absent if b.pk not in due_bill_ids}
    return replaced


def card_bills_and_dues(user):
    bills = list(CardBill.objects.filter(user=user, scope_type=CardBill.ScopeType.ISSUER))
    return bills, set(PaymentDue.objects.filter(user=user, bill__isnull=False).values_list("bill_id", flat=True))


def sync_bill_dues(user, since, today, issuer_code=None):
    """저장된 카드사 통합 청구서 → 납부 예정 (R04). 청구서 저장(sync_cards)과 홈(home_data)이 회원 잠금 안에서 부른다. 외부 조회 없음.
    - 청구서 채택은 출금 매칭(find_statements)과 같다: 같은 납부일끼리 usage_statements (카드론 빼기 · 여러 장이면 사용 명세서만 ·
      판단 못 하면 만들지 않음). 청구서 한 장 = 납부 예정 하나 (카드별로 나누지 않음). 0원 · 음수 청구서는 만들지 않는다.
    - 새로 만드는 건 납부일이 since(홈의 지난 주기 시작일) 이후인 것만. since 가 None(급여일 없음)이면 만들지 않고 고치기만 한다.
    - 이미 있는 예정은 ID · 납부 연결을 두고 금액 · 날짜 · 확인 상태만 저장된 청구서로 맞춘 뒤 납부 상태를 기존 규칙으로 다시 계산한다.
      더 늦게 시작한 조회의 값은 _save_bills 가 덮지 않으므로 오래된 응답이 예정을 되돌리지 않는다.
    - 정정으로 낼 돈이 없어진(0원 이하) 청구서의 예정은 취소(obligation canceled)한다. 더는 채택되지 않는 청구서는 납부 기록이 없을 때만.
      납부 기록은 지우지 않는다. 이번 응답에 없다는 이유만으로는 바꾸지 않는다 (결제일 정정은 follow_date_corrections)
    - 결제 계좌는 청구서 계좌, 없으면 그 카드사 카드의 결제 계좌가 하나로 정해질 때만. 모르면 비운다"""
    bills = CardBill.objects.filter(user=user, scope_type=CardBill.ScopeType.ISSUER)
    if issuer_code:
        bills = bills.filter(issuer_code=issuer_code)
    bills = list(bills)
    dues = {due.bill_id: due for due in PaymentDue.objects.select_for_update().filter(user=user, bill__in=bills)}
    follow_date_corrections(bills, dues)
    replaced = replaced_bill_ids(bills, set(dues))  # 정정으로 대체된 예전 청구서 — 예정을 만들지 않고 같은 날 명세서 묶음에서도 뺀다
    groups = {}
    for bill in bills:
        if bill_due_on(bill) is not None and bill.pk not in replaced:
            groups.setdefault((bill.issuer_code, bill_due_on(bill)), []).append(bill)
    for (issuer, due_on), group in groups.items():
        statements, reason = usage_statements(group)
        adopted = set() if reason else {bill.pk for bill in statements}
        for bill in group:
            due = dues.get(bill.pk)
            valid = bill.pk in adopted and bill.total_amount > 0
            if due is None:
                if valid and since is not None and due_on >= since:
                    accounts = paying_accounts(user, issuer, [bill])
                    due = PaymentDue.objects.create(
                        user=user, obligation_type=PaymentDue.ObligationType.CARD_BILL, bill=bill,
                        occurrence_key=due_key(bill), due_date=due_on, confirmed_amount=bill.total_amount,
                        payment_account_id=next(iter(accounts)) if len(accounts) == 1 else None,
                        source_type=bill.source_type, confirmation_state=bill.confirmation_state or "unknown",
                    )
                    refresh_state(due, today)
                continue
            if valid:
                due.due_date, due.confirmed_amount = due_on, bill.total_amount
                due.source_type, due.confirmation_state = bill.source_type, bill.confirmation_state or "unknown"
                due.obligation_state, due.canceled_at = PaymentDue.ObligationState.ACTIVE, None
                due.save(update_fields=["due_date", "confirmed_amount", "source_type", "confirmation_state",
                                        "obligation_state", "canceled_at"])
                refresh_state(due, today)
            # 낼 돈이 없어졌으면(0원 이하) 납부 기록이 있어도 취소 — 기록은 남기고 예전 청구액을 낼 돈으로 보이지 않는다.
            # 더는 채택되지 않는(판단 보류) 청구서는 납부 기록이 없을 때만 취소한다
            elif due.obligation_state == PaymentDue.ObligationState.ACTIVE and (
                    bill.total_amount <= 0 or not due.matches.exists()):
                due.obligation_state, due.canceled_at = PaymentDue.ObligationState.CANCELED, timezone.now()
                due.save(update_fields=["obligation_state", "canceled_at"])


def refresh_state(due, today):
    """납부 연결이 있으면 기존 계산(refresh_due_state: 90% 이상 paid · 일부 partial). 없으면 홈과 같게
    출금일 +3일까지 unpaid('출금 확인 중'), 그 뒤 needs_review (budgets.home.prepare_dues)"""
    from budgets.api import refresh_due_state  # budgets.api 가 finance 를 불러서 여기서 불러온다
    from budgets.home import REVIEW_AFTER_DAYS

    if due.matches.exists():
        refresh_due_state(due, today)
        return
    due.paid_amount = 0
    due.payment_state = "needs_review" if due.due_date < today - timedelta(days=REVIEW_AFTER_DAYS) else "unpaid"
    due.save(update_fields=["paid_amount", "payment_state"])


def bill_due_window(user, today):
    """홈 '꼭 나갈 돈'과 같은 범위의 시작일(지난 급여 주기 시작일). 급여일이 없으면 None"""
    from budgets.salary import current_salary_profile, salary_cycle_dates

    profile = current_salary_profile(user, today)
    return salary_cycle_dates(profile, today)[2] if profile and profile.payday else None


def refresh_bill_dues(user, issuer_code=None, today=None):
    """카드 청구서 저장 뒤 · 홈을 열 때: 납부 예정 맞추기 → 이미 받은 카드값 출금 다시 맞추기 (새 승인 거래가 없어도).
    실패해도 청구서 저장 · 홈은 남기고 다음에 다시 돈다 (pipeline.rematch_payments 와 같음)"""
    today = today or timezone.localdate()
    try:
        with transaction.atomic():
            sync_bill_dues(user, bill_due_window(user, today), today, issuer_code)
            match(user)
    except Exception:
        logger.error("카드 청구서 납부 예정 맞추기 실패")  # 원문 · traceback 에 금융 정보가 섞일 수 있어 남기지 않는다


def match(user):
    """아직 안 붙은 카드값 출금을 모두 본다: 은행을 먼저 연결하면 청구서가 출금보다 늦게 들어오기 때문"""
    from budgets.api import refresh_due_state  # budgets.api 가 finance 를 불러서 여기서 불러온다

    today = timezone.localdate()
    payments = Tx.objects.filter(
        user=user, source_kind="bank", transaction_type=Tx.TransactionType.CARD_PAYMENT, is_canceled=False,
    ).exclude(pk__in=PaymentMatch.objects.filter(user=user).values("transaction")).select_related("account")
    for tx in payments.order_by("occurred_on", "pk"):
        for bill in find_statements(user, tx):
            due = bill_due(bill, tx)
            PaymentMatch.objects.get_or_create(due=due, transaction=tx, defaults={
                "user": user, "matched_amount": bill.total_amount,
            })
            refresh_due_state(due, today)

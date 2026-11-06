"""카드대금 출금 ↔ 카드 청구서 매칭 (pipeline.match_card_payments).

10/28 결정: 청구서는 카드사 단위 통합이고 카드론 명세서는 뺀다. 같은 날 사용 명세서가 여러 장이면 합친 금액 한 번으로 낸다.
청구서를 저장하면 출금 전에도 납부 예정을 만들어 홈 '꼭 나갈 돈'에 보인다 (sync_bill_dues, R04). 출금을 찾으면 그 납부 예정에 붙인다.

분할 납부 (11/06): 청구액과 납부액은 따로 둔다 — 청구 총액(confirmed_amount)은 줄이지 않고, 남은 금액 = 청구액 − 유효한 납부액(최소 0).
  자동 연결은 출금 한 건씩만 본다(여러 출금 조합으로 합계를 맞춰 보지 않음): 적요의 카드사 · 출금일 ±3일 · 결제 계좌(알 때)가 맞고
  금액이 그 청구서의 '남은 금액'과 정확히 같으며, 맞는 청구서가 하나 · 그 청구서에 맞는 출금도 하나일 때만.
  나머지(일부 금액 · 선결제처럼 날짜가 먼 출금 · 후보가 여럿)는 사용자가 고른다 (api_card_payments.py).
  납부 상태는 금액으로만 본다: 청구액 이상이면 paid, 일부면 partial (공통 90% 규칙을 쓰지 않음, refresh_card_due)
"""
import logging
from datetime import timedelta

from django.db import transaction

from collections import Counter

from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from .card_deduction import bill_total_mismatch, usage_statements
from .models import Card, CardBill, FinancialTransaction as Tx, PaymentDue, PaymentMatch
from .loan_payments import record_link
from .transfer_rules import CARD_CANDIDATE, card_issuer

WINDOW = timedelta(days=3)
CARD_PAYMENT = "card_bill_payment"   # 거래 field_states: 카드 청구서 연결 기록 (loan_payments.record_link 와 같은 모양, 연결 취소 때 분류 되돌리기)
SKIP_AUTO = "skip_auto_match"   # budgets.api.SKIP_AUTO 와 같은 칸. 카드는 skip_key(due) 를 넣는다
logger = logging.getLogger(__name__)


def due_key(bill):
    """카드 청구서 납부 예정의 중복 방지 키. 예정이 정정된 청구서로 옮겨 가면 키도 같이 바꾼다 (예전 청구서가 다시 와도 겹치지 않게)"""
    return f"card_bill:{bill.pk}"


def bill_due_on(bill):
    """납부 예정일 = 출금 예정일, 없으면 결제 예정일 (11/5 나은: 카드는 결제일에 출금된다. 매칭 · 선차감과 같은 기준). 둘 다 없으면 None"""
    return bill.withdrawal_due_date or bill.payment_due_date


def skip_key(due):
    """사용자가 연결을 푼 납부 예정 표시. 결제일 정정으로 occurrence_key 가 바뀌어도 남도록 예정 ID 로 둔다"""
    return f"card_due:{due.pk}"


def valid_paid(due):
    """(유효한 납부액, 근거가 깨졌는지). 취소된 출금은 빼고 한 줄은 출금 순액보다 많이 인정하지 않는다.
    PaymentMatch 기록(matched_amount)은 자르지 않고 그대로 둔다 — 청구액보다 많이 낸 것도 그대로 보인다"""
    from .api import cancel_info

    paid, broken = 0, False
    for row in due.matches.select_related("transaction"):
        net = row.transaction.amount - cancel_info(row.transaction)["amount"]
        broken |= net < row.matched_amount
        paid += max(0, min(row.matched_amount, net))
    return paid, broken


def card_due_state(amount, paid, broken, due_date, today):
    """카드 청구서 납부 상태: 연결한 출금이 취소됨 → needs_review, 청구액 이상 → paid, 일부 → partial,
    연결 없음 → 출금일 +3일까지 unpaid('출금 확인 중'), 그 뒤 needs_review (안 냈다고 단정하지 않음).
    결제일을 모르면(청구서에 출금 · 결제 예정일이 없음) 연결 없을 때 needs_review ('결제일 확인 필요')"""
    from budgets.home import REVIEW_AFTER_DAYS

    if broken:
        return "needs_review"
    if paid > 0:
        return "paid" if paid >= amount else "partial"
    if due_date is None:
        return "needs_review"
    return "needs_review" if due_date < today - timedelta(days=REVIEW_AFTER_DAYS) else "unpaid"


def refresh_card_due(due, today):
    """budgets.api.refresh_due_state 가 카드 청구서 예정에 부른다"""
    paid, broken = valid_paid(due)
    due.paid_amount = paid
    due.payment_state = card_due_state(due.confirmed_amount, paid, broken, due.due_date, today)
    due.matched_at = max((m.matched_at for m in due.matches.all()), default=None)
    due.save(update_fields=["paid_amount", "payment_state", "matched_at"])


def remaining(bill):
    """청구액 − 유효한 납부액 (최소 0). 예정이 없으면 청구액 그대로"""
    due = PaymentDue.objects.filter(bill=bill).first()
    return max(0, bill.total_amount - (valid_paid(due)[0] if due else 0))


def bill_payment(bill, due, today):
    """청구서 한 장 → (확인된 납부액, 남은 금액, 상태, 근거가 깨졌는지). 청구액이 0원 이하면 낼 돈이 없어 상태 None.
    예정이 없으면 연결된 납부가 없는 것일 뿐 안 낸 것으로 보지 않는다 (출금일 +3일 뒤 needs_review)"""
    paid, broken = valid_paid(due) if due else (0, False)
    if bill.total_amount <= 0:
        return paid, 0, None, broken
    state = card_due_state(bill.total_amount, paid, broken, bill_due_on(bill), today)
    return paid, max(0, bill.total_amount - paid), state, broken


USER_CHECK = "userPaymentCheck"   # card_bill.field_states: 사용자가 답한 납부 확인 (재조회 _save_bills 는 이 키를 건드리지 않는다)
CHECK_ANSWERS = ("unpaid", "paid", "partial", "later")


def user_check(bill):
    check = (bill.field_states or {}).get(USER_CHECK)
    return check if isinstance(check, dict) and check.get("answer") in CHECK_ANSWERS else None


def bill_status(bill, due, today):
    """첫 예산용 '이 청구액 중 앞으로 얼마를 더 내야 하나' (11/06). 청구 총액 · 납부 기록은 바꾸지 않는다.
    - paid_by_transactions: 연결된 계좌 출금(근거)이 청구액 이상 → 앞으로 0원
    - user_confirmed: 사용자 확인 (아직 안 냄 = 청구액 · 전부 = 0원 · 일부 = 입력한 금액). 출금 연결액과 더하지 않는다
    - recheck: 사용자 확인 뒤 청구액이 바뀜(bill_changed) · 확인보다 출금이 더 나감(transactions_conflict) · 연결 출금 취소 → 다시 확인
    - unchecked: 확인 없음 · '나중에 확인' (금액 null — 0원도 확정 미납액도 아님)
    - no_charge: 청구액 0원 이하 (상세 합계가 더 크면 total_mismatch — 원래 청구액을 모름)
    needs_check = 결제일이 지났는데 앞으로 낼 금액을 모름(또는 재확인) — 결제일 전이면 청구액 그대로 낼 예정으로 본다"""
    total, due_on = bill.total_amount, bill_due_on(bill)
    paid, broken = valid_paid(due) if due else (0, False)
    check = user_check(bill)
    status, left, recheck = "unchecked", None, None

    if bill_total_mismatch(bill):
        # 총액이 상세 합계보다 작음(예: 결제일 당일 조회 총액 0원 · 상세 511,515원) → 원래 청구액 · 남은 금액 모두 모름.
        # 0원이라고 '낼 금액 없음'으로 보지 않는다 (11/06, 원래 청구액 확인 방식은 별도 결정)
        status, recheck = "total_mismatch", "bill_total_mismatch"
    elif total <= 0:
        status, left = "no_charge", 0
    elif paid >= total and not broken:
        status, left = "paid_by_transactions", 0
    elif check and check["answer"] != "later":
        user_left = {"unpaid": total, "paid": 0}.get(check["answer"], check.get("remaining_amount"))
        if check.get("bill_total") != total:
            status, recheck = "recheck", "bill_changed"
        elif broken or user_left > total - paid:
            status, recheck = "recheck", "transactions_conflict"
        else:
            status, left = "user_confirmed", user_left
    return {
        "bill_id": str(bill.pk), "issuer_code": bill.issuer_code,
        "billing_month": bill.billing_month.strftime("%Y-%m") if bill.billing_month else None,
        "due_date": due_on.isoformat() if due_on else None, "total_amount": total,
        "status": status, "remaining_amount": left, "transaction_paid_amount": paid, "recheck_reason": recheck,
        "user_check": {"answer": check["answer"], "remaining_amount": check.get("remaining_amount"),
                       "confirmed_at": check.get("confirmed_at")} if check else None,
        "needs_check": status == "recheck" or (status == "unchecked" and due_on is not None and due_on <= today),
    }


def adopted(user, bill):
    """사용자가 납부를 연결할 수 있는 청구서: 낼 돈이 있고, 납부일을 알고, 정정으로 대체되지 않았고,
    같은 납부일 명세서 묶음에서 채택된 것 (sync_bill_dues 와 같은 기준)"""
    due_on = bill_due_on(bill)
    if bill.total_amount <= 0 or due_on is None or bill.scope_type != CardBill.ScopeType.ISSUER:
        return False
    bills, due_ids = card_bills_and_dues(user)
    replaced = replaced_bill_ids(bills, due_ids)
    group = [b for b in bills if b.issuer_code == bill.issuer_code and bill_due_on(b) == due_on and b.pk not in replaced]
    statements, reason = usage_statements(group)
    return bill.pk not in replaced and not reason and bill.pk in {b.pk for b in statements}


def unpaid_bills(user, issuer_code, day):
    """출금일(없으면 결제일)이 ±3일 안인 카드사 통합 청구서를 날짜별로. 낼 돈이 없는 0원 명세서 · 정정으로 대체된 청구서는 뺀다.
    일부 납부한 청구서도 남긴다 (남은 금액은 find_statements 가 본다).
    [임시] 청구 월은 따로 비교하지 않는다: 달이 다른 청구서는 날짜에서 걸러진다"""
    bills = CardBill.objects.filter(
        user=user, issuer_code=issuer_code, scope_type=CardBill.ScopeType.ISSUER, total_amount__gt=0,
    ).annotate(
        due_on=Coalesce("withdrawal_due_date", "payment_due_date"),
    ).filter(due_on__range=(day - WINDOW, day + WINDOW)).exclude(pk__in=replaced_bill_ids(*card_bills_and_dues(user)))
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
    """이 출금이 낸 명세서들 [(청구서, 인정 금액)]. 같은 납부일 명세서 묶음의 남은 금액 합이 출금액과 정확히 같을 때만.
    맞는 묶음이 둘 이상이면 고르지 않는다(가까운 날짜를 고르지 않음 → 사용자 확인). 사용자가 이 예정에서 푼 출금은 다시 붙이지 않는다"""
    issuer_code = card_issuer(tx.source_description)
    if issuer_code is None:
        return []
    skipped = set((tx.field_states or {}).get(SKIP_AUTO, []))
    found = []
    for group in unpaid_bills(user, issuer_code, tx.occurred_on).values():
        statements, reason = usage_statements(group)
        if reason or not statements:
            continue
        shares = [(bill, remaining(bill)) for bill in statements]
        shares = [(bill, amount) for bill, amount in shares if amount > 0]
        if not shares or sum(amount for _, amount in shares) != tx.amount:
            continue
        if any(skip_key(due) in skipped for due in PaymentDue.objects.filter(bill__in=[bill for bill, _ in shares])):
            continue
        accounts = paying_accounts(user, issuer_code, statements)
        if not accounts or tx.account_id in accounts:
            found.append(shares)
    return found[0] if len(found) == 1 else []


def bill_due(bill, tx):
    """청구서의 납부 예정. 청구서 저장 때 만든 것(sync_bill_dues)이 있으면 그대로 쓰고, 계좌를 모르던 예정엔 실제 출금 계좌를 채운다"""
    due, created = PaymentDue.objects.get_or_create(bill=bill, defaults={
        "user": bill.user, "obligation_type": PaymentDue.ObligationType.CARD_BILL,
        "occurrence_key": due_key(bill), "due_date": bill_due_on(bill), "confirmed_amount": bill.total_amount,
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
    - 정정으로 낼 돈이 없거나(0원 이하) 납부일을 모르게 된 청구서의 예정은 취소(obligation canceled)한다.
      날짜가 다시 제공되면 같은 예정을 최신 금액으로 되살린다. 더는 채택되지 않는 청구서는 납부 기록이 없을 때만.
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
        if bill.pk not in replaced:
            groups.setdefault((bill.issuer_code, bill_due_on(bill)), []).append(bill)
    for (issuer, due_on), group in groups.items():
        statements, reason = usage_statements(group)
        adopted = set() if reason else {bill.pk for bill in statements}
        for bill in group:
            due = dues.get(bill.pk)
            valid = due_on is not None and bill.pk in adopted and bill.total_amount > 0
            if due is None:
                if valid and since is not None and due_on >= since:
                    accounts = paying_accounts(user, issuer, [bill])
                    due = PaymentDue.objects.create(
                        user=user, obligation_type=PaymentDue.ObligationType.CARD_BILL, bill=bill,
                        occurrence_key=due_key(bill), due_date=due_on, confirmed_amount=bill.total_amount,
                        payment_account_id=next(iter(accounts)) if len(accounts) == 1 else None,
                        source_type=bill.source_type, confirmation_state=bill.confirmation_state or "unknown",
                    )
                    refresh_card_due(due, today)
                continue
            if valid:
                due.due_date, due.confirmed_amount = due_on, bill.total_amount
                due.source_type, due.confirmation_state = bill.source_type, bill.confirmation_state or "unknown"
                due.obligation_state, due.canceled_at = PaymentDue.ObligationState.ACTIVE, None
                due.save(update_fields=["due_date", "confirmed_amount", "source_type", "confirmation_state",
                                        "obligation_state", "canceled_at"])
                refresh_card_due(due, today)
            # 0원 이하 · 납부일 미제공 정정은 납부 기록이 있어도 예정을 취소한다.
            # 이전 금액 · 날짜는 취소된 예정의 이력으로만 남겨 홈에 표시하지 않고, 납부 기록은 보존한다.
            # 더는 채택되지 않는(판단 보류) 청구서는 납부 기록이 없을 때만 취소한다
            elif due.obligation_state == PaymentDue.ObligationState.ACTIVE and (
                    bill.total_amount <= 0 or due_on is None or not due.matches.exists()):
                due.obligation_state, due.canceled_at = PaymentDue.ObligationState.CANCELED, timezone.now()
                due.save(update_fields=["obligation_state", "canceled_at"])




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
    """아직 안 붙은 카드값 출금을 모두 본다: 은행을 먼저 연결하면 청구서가 출금보다 늦게 들어오기 때문.
    한 청구서 묶음에 맞는 출금이 여럿이면(같은 금액 두 번 등) 하나를 고르지 않는다 → 사용자 확인"""
    today = timezone.localdate()
    # 카드값(예전 규칙 · 사용자 분류) 또는 사용자가 확정하지 않은 카드값 후보 이체 (R11: 이름만으로는 card_payment 로 바꾸지 않음)
    payments = Tx.objects.filter(
        Q(transaction_type=Tx.TransactionType.CARD_PAYMENT)
        | Q(transaction_type=Tx.TransactionType.TRANSFER, field_states__has_key=CARD_CANDIDATE,
            confirmation_state__in=["needs_review", "suggested"]),
        user=user, source_kind="bank", direction="out", is_canceled=False,
    ).exclude(pk__in=PaymentMatch.objects.filter(user=user).values("transaction")).select_related("account")
    pairs = [(tx, shares) for tx in payments.order_by("occurred_on", "pk") if (shares := find_statements(user, tx))]
    per_group = Counter(tuple(bill.pk for bill, _ in shares) for _, shares in pairs)
    for tx, shares in pairs:
        if per_group[tuple(bill.pk for bill, _ in shares)] != 1:
            continue
        for i, (bill, amount) in enumerate(shares):
            due = bill_due(bill, tx)
            if i == 0 and tx.transaction_type != Tx.TransactionType.CARD_PAYMENT:
                # 후보 이체 → 카드값. 바꾼 기록을 남겨 연결을 풀면 이체로 돌아간다 (같은 날 명세서 여러 장이면 첫 장에만)
                record_link(tx, due, None, "rule", record_key=CARD_PAYMENT, skip_key=skip_key(due),
                            transaction_type=Tx.TransactionType.CARD_PAYMENT)
            PaymentMatch.objects.get_or_create(due=due, transaction=tx, defaults={"user": user, "matched_amount": amount})
            refresh_card_due(due, today)

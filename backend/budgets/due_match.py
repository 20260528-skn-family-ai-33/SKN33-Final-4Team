"""고정지출 결제 연결 (⑥, 11/4) — 홈을 열 때와 거래 저장 뒤 처리(finance/pipeline.py)가 같이 쓴다. 회원 잠금 안에서 부른다.

  계좌 고정지출  지난 · 이번 주기 회차(PaymentDue)를 만들고 맞는 출금을 연결 (match_current_due · remembered_withdrawal 그대로)
  카드 고정지출  근거 거래가 있으면 지난 · 이번 주기마다 기억한 가맹점 · 같은 카드 · 금액 ±10% 결제 1건을 고정비로 연결
                 (납부 예정은 만들지 않음. 근거 거래가 없으면 목록의 evidenceSuggestion 으로 묻는 흐름 그대로)
"""
from datetime import timedelta

from django.db.models import F, Q

from finance.merchant_rules import normalize_merchant
from finance.models import FinancialTransaction, PaymentDue, PaymentMatch, RecurringExpense

from .api import (card_linked_in, claimable, day_in_cycle, ensure_current_due, link_evidence, match_current_due,
                  matching_payments, refresh_due_state, remembered_evidence)


def sync_payment_dues(user, today):
    """거래 저장 뒤 처리용: 홈과 같은 지난 · 이번 주기를 구해 match_dues. 급여일이 없으면(온보딩 전) 아무것도 안 함"""
    from .home import ensure_cycle, ensure_previous_cycle
    from .salary import current_salary_profile, salary_cycle_dates

    profile = current_salary_profile(user, today)
    if not (profile and profile.payday):
        return
    start, next_payday, previous_start = salary_cycle_dates(profile, today)
    cycle = ensure_cycle(user, start, next_payday)
    match_dues(user, cycle, today, ensure_previous_cycle(user, previous_start, start))


def match_dues(user, cycle, today, previous_cycle):
    """확정 고정지출을 pk 순으로 잠그고(고정지출 수정 · 중지와 같은 순서) 계좌 · 카드별로 연결"""
    expenses = RecurringExpense.objects.select_for_update().filter(
        Q(payment_card__isnull=True, payment_day__isnull=False) | Q(payment_card__isnull=False),
        user=user, status="confirmed",
    ).order_by("pk")
    for expense in expenses:
        if expense.payment_card_id is None:
            match_account_dues(user, expense, cycle, today, previous_cycle)
        else:
            match_card_fixed(expense, (previous_cycle, cycle), today)
    refresh_partial_dues(user, cycle, previous_cycle, today)


def refresh_partial_dues(user, cycle, previous_cycle, today):
    """90% 규칙(11/4) 전에 partial 로 저장된 고정지출 회차 다시 계산 — 연결이 있어 match_current_due 가 다시 보지 않음
    홈 mustPay 와 같은 기간(지난 주기 시작 ~ 다음 월급날 전). 카드 청구서 · 대출 회차는 건드리지 않음
    잠금 순서: 고정지출(위 match_dues) → 회차(pk 순)"""
    since = (previous_cycle or cycle).starts_on
    dues = PaymentDue.objects.select_for_update().filter(
        user=user, obligation_type="recurring", obligation_state="active", payment_state="partial",
        due_date__gte=since, due_date__lt=cycle.ends_before, pk__in=PaymentMatch.objects.values("due"),
        paid_amount__gte=(F("confirmed_amount") * 9 + 9) / 10,  # 90% 를 넘어 paid 가 될 회차만 (진짜 partial 은 그대로 둠)
    ).order_by("pk")
    for due in dues:
        refresh_due_state(due, today)


def match_account_dues(user, expense, cycle, today, previous_cycle):
    """지난 · 이번 주기 회차 만들기 + 맞는 출금 연결 (홈 prepare_dues 에서 옮김, 동작 같음)

    출금일이 이미 지난 회차는
      - 등록하기 전 출금일이 지난 주기면 만들지 않는다 (11/9, home.md)
      - 등록하기 전 출금일이 이번 주기면 fixed.md 규칙: 맞는 출금이 있을 때만 "냄"으로 만든다
      - 등록한 뒤 출금일이면 home.md 규칙: 출금이 없어도 만들어 미납을 알린다 (앱을 한동안 안 연 경우)
    지난 주기 회차는 한 달을 건너뛰고 접속해서 아직 없을 때만 새로 만든다 (취소 · 납부 기록이 있으면 그대로 둠)
    """
    from .due_periods import monthly_due, occurrence_cycles
    from .home import registered_on

    periods = [(part, original is previous_cycle) for original in (previous_cycle, cycle)
               for part in occurrence_cycles(user, original)]
    for c, is_previous in periods:
        due_date = day_in_cycle(c, expense.payment_day)
        if due_date is None:
            continue
        if getattr(c, "monthly_occurrence", False):
            existing = monthly_due(PaymentDue.objects.filter(user=user, recurring=expense), due_date)
        else:
            existing = PaymentDue.objects.filter(
                user=user, occurrence_key=f"recurring:{expense.pk}:{c.starts_on.isoformat()}",
            ).first()
        if is_previous and existing is not None:
            match_current_due(expense, c, today)  # 이미 있는 지난 회차는 늦게 들어온 출금(±3일)만 맞춰 봄
            continue
        if is_previous and due_date < registered_on(expense):
            continue  # 11/9: 등록 전 지난 주기 출금일은 회차를 만들지 않음 — 이미 나간 돈이 '꼭 나갈 돈 · 출금 확인 필요'로 하나 더 보이던 문제
        create_past = registered_on(expense) <= due_date
        ensure_current_due(expense, c, today, create_past=create_past)
        match_current_due(expense, c, today)  # 회차를 만든 뒤 들어온 출금도 맞춰 봄 (±3일, 기억한 출금 1건이면 자동 연결)


def merchant_label(tx):
    return normalize_merchant(tx.merchant_name or tx.source_description)


def match_card_fixed(expense, cycles, today):
    """카드 고정지출 → 주기마다 기억한 가맹점 결제 1건을 고정비로 (C-12). 납부 예정은 만들지 않는다
    주기에 이미 연결된 결제가 있으면 건너뜀 · 맞는 결제가 여러 건이면 자동으로 고르지 않음"""
    labels = {merchant_label(t) for t in remembered_evidence(expense)} - {None}  # 비운 가게는 기억에서 빠짐
    if not labels:
        return  # 기억한 가맹점이 없으면 evidenceSuggestion('이 결제가 맞나요?')으로 묻는다
    for cycle in cycles:
        if cycle is None or card_linked_in(expense, cycle):
            continue  # 이 주기는 이미 연결됨 (주기마다 1건)
        end = cycle.ends_before - timedelta(days=1)
        # 금액 ±10% · 같은 카드 · 다른 고정지출 · 납부 짝에 안 쓰인 결제 (matching_payments). 미래 날짜 거래는 아직 안 쓴 돈
        found = [t for t in matching_payments(expense, cycle.starts_on, cycle.starts_on, min(end, today))
                 if merchant_label(t) in labels]
        if len(found) != 1:
            continue
        tx = FinancialTransaction.objects.select_for_update().get(pk=found[0].pk)  # 잠근 뒤 다시 확인
        if claimable(tx, expense):
            link_evidence(expense, tx, cycle, today, source="rule")


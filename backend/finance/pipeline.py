import logging

from django.contrib.auth import get_user_model
from django.db import DatabaseError, transaction
from django.utils import timezone

from budgets import due_match

from . import card_payments, classifier, loan_payments, transfer_rules
from .models import FinancialTransaction

logger = logging.getLogger(__name__)
RETRY_STATE_KEY = "after_save_pending"


# 세진: 취소 연결, 내 계좌 이체·카드값·대출 상환 후보 판단, finance/transfer_rules.py (SAME_PURCHASE 연결은 아직 없음)
def link_basics(user, txs):
    transfer_rules.apply(user, txs)


# 세진: 소비 카테고리 분류 (사용자 기억 → 규칙 → ML → 검색·Jev), finance/classifier.py
def classify_spending(user, txs):
    classifier.classify(user, txs)


# 세진: 카드값 출금과 카드 청구서 매칭, finance/card_payments.py
def match_card_payments(user, txs):
    card_payments.match(user)


# 세진: 대출 상환 출금과 대출 매칭, finance/loan_payments.py
def match_loan_repayments(user, txs):
    loan_payments.match(user)


# 나은: 내 계좌 이체와 확정 정기 저축 매칭, saving 전환
def match_savings(user, txs):
    pass


# 나은: 계좌 고정지출 납부 예정 연결(출금일 ±3일 · 기억한 출금) + 카드 고정지출 고정비 자동 연결(기억한 가맹점 · 같은 카드 · ±10%)
#   홈을 열 때와 같은 함수 (budgets/due_match.py). 받은 거래만이 아니라 이 회원의 지난 · 이번 주기를 다시 맞춰 본다
def match_payment_dues(user, txs):
    due_match.sync_payment_dues(user, timezone.localdate())


# 나은: 고정지출·저축 후보 감지
def detect_candidates(user, txs):
    pass


AFTER_SAVE_STEPS = [
    ("link_basics", link_basics),
    ("classify_spending", classify_spending),
    ("match_card_payments", match_card_payments),
    ("match_loan_repayments", match_loan_repayments),
    ("match_savings", match_savings),
    ("match_payment_dues", match_payment_dues),
    ("detect_candidates", detect_candidates),
]


def process_new_transactions(user, transaction_ids):
    _process_transactions(user, transaction_ids, retry_only=False)


def retry_failed_transactions(user, transaction_ids):
    _process_transactions(user, transaction_ids, retry_only=True)


def rematch_payments(user):
    """새 거래 없이 청구서 · 대출 · 납부 회차만 늦게 생기면 파이프라인이 돌지 않아서 홈을 열 때 다시 맞춘다.
    실패해도 홈은 열리고 다음에 다시 돈다"""
    try:
        with transaction.atomic():
            card_payments.match(user)
            loan_payments.match(user)
    except Exception as exc:
        # DB 오류 원문·traceback에는 거래 내용이 포함될 수 있다.
        logger.error("납부 다시 맞추기 실패 (%s)", "database_error" if isinstance(exc, DatabaseError) else "step_error")


def _save_pending(txs, pending):
    changed = []
    for tx in txs.all():
        # 단계가 바꾼 다른 근거 값은 그대로 둔다.
        states = dict(tx.field_states)
        if pending[tx.pk]:
            states[RETRY_STATE_KEY] = dict(pending[tx.pk])
        else:
            states.pop(RETRY_STATE_KEY, None)
        if states != tx.field_states:
            tx.field_states = states
            changed.append(tx)
    if changed:
        FinancialTransaction.objects.bulk_update(changed, ["field_states"], batch_size=500)


def _process_transactions(user, transaction_ids, *, retry_only):
    if not transaction_ids:
        return

    txs = FinancialTransaction.objects.filter(user=user, pk__in=transaction_ids)
    with transaction.atomic():
        # 잠금 순서: 회원 먼저 (budgets.api.lock_user 와 같음) → 거래(ID 순).
        # 홈 · 고정지출 수정과 한 회원에 하나씩 돌아서, 거래를 잠그는 순서가 달라도 서로 기다리다 멈추지 않는다
        get_user_model().objects.select_for_update(no_key=True).filter(pk=getattr(user, "pk", user)).first()
        # 같은 거래의 처리·재시도 기록이 서로 덮어쓰이지 않게 잠근다.
        pending = {}
        for tx in txs.select_for_update().order_by("pk"):
            pending[tx.pk] = dict(tx.field_states.get(RETRY_STATE_KEY, {}))
            if not retry_only:
                pending[tx.pk].update({name: "pending" for name, _ in AFTER_SAVE_STEPS})

        failed_ids = set()
        for index, (name, step) in enumerate(AFTER_SAVE_STEPS):
            ids = [pk for pk, states in pending.items() if name in states]
            if not ids:
                continue
            step_txs = txs.filter(pk__in=ids)
            try:
                with transaction.atomic():
                    step(user, step_txs.all())
            except Exception as exc:
                code = "database_error" if isinstance(exc, DatabaseError) else "step_error"
                # DB 오류 원문·traceback에는 거래 내용이 포함될 수 있다.
                logger.error("거래 저장 뒤 처리 단계 실패: %s (%s)", name, code)
                failed_ids.update(ids)
                for pk in ids:
                    pending[pk][name] = code
                    for later_name, _ in AFTER_SAVE_STEPS[index + 1:]:
                        pending[pk].setdefault(later_name, "upstream_failed")
                        if pending[pk][later_name] == "pending":
                            pending[pk][later_name] = "upstream_failed"
            else:
                for pk in ids:
                    # 앞 단계가 실패했다면 뒤 단계도 다음번에 다시 확인한다.
                    if pk not in failed_ids:
                        pending[pk].pop(name, None)
            _save_pending(step_txs, pending)

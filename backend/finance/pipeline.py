import logging

from django.db import transaction

from .models import FinancialTransaction

logger = logging.getLogger(__name__)


# 혜리: 취소·SAME_PURCHASE 연결, 내 계좌 이체·카드값·입금 판단
def link_basics(user, txs):
    pass


# 혜리: 소비 카테고리 분류 (규칙 → 사전 → ML → LLM)
def classify_spending(user, txs):
    pass


# 세진: 카드값 출금과 카드 청구서 매칭
def match_card_payments(user, txs):
    pass


# 세진: 대출 상환 출금과 대출 매칭
def match_loan_repayments(user, txs):
    pass


# 나은: 내 계좌 이체와 확정 정기 저축 매칭, saving 전환
def match_savings(user, txs):
    pass


# 나은: 출금과 고정지출 납부 예정 매칭(±3일), 카드 '맞나요?' 후보
def match_payment_dues(user, txs):
    pass


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
    if not transaction_ids:
        return

    txs = FinancialTransaction.objects.filter(user=user, pk__in=transaction_ids)
    for name, step in AFTER_SAVE_STEPS:
        try:
            with transaction.atomic():
                # 앞 단계의 변경·롤백을 반영하도록 새로 조회한다.
                step(user, txs.all())
        except Exception:
            logger.exception("거래 저장 뒤 처리 단계 실패: %s", name)

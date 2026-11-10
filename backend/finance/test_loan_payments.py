"""대출 상환 매칭 테스트 (pipeline.match_loan_repayments → finance/loan_payments.py).
페르소나 데이터에 대출 거래가 없어서 가짜 거래로 확인한다."""
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from budgets.home import home_data
from budgets.models import BudgetCycle
from users.models import FinancialProfile

from . import pipeline
from .models import BankAccount, Connection, FinancialTransaction as Tx, Loan, PaymentDue, PaymentMatch, TransactionLink

TODAY = date(2026, 11, 12)


class LoanPaymentTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="loan-payment@example.invalid")
        bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        self.account = BankAccount.objects.create(user=self.user, connection=bank, source_account_key="pay")
        self.other_account = BankAccount.objects.create(user=self.user, connection=bank, source_account_key="other")
        self.loan = Loan.objects.create(
            user=self.user, origin="manual", lender_name="한국장학재단", linked_account=self.account,
            monthly_payment=150_000, payment_day=10,
        )
        clock = patch("django.utils.timezone.localdate", return_value=TODAY)
        clock.start()
        self.addCleanup(clock.stop)

    def due(self, day=date(2026, 11, 10), loan=None):
        return PaymentDue.objects.create(
            user=self.user, obligation_type="loan", loan=loan or self.loan, occurrence_key=f"loan:{day}",
            due_date=day, confirmed_amount=150_000, source_type="manual", confirmation_state="confirmed",
        )

    def repayment(self, amount=150_000, day=date(2026, 11, 10), desc="한국장학재단", account=None,
                  transaction_type="transfer", confirmation_state="needs_review"):
        return Tx.objects.create(
            user=self.user, source_kind="bank", account=account or self.account, occurred_on=day, amount=amount,
            direction="out", source_description=desc, transaction_type=transaction_type,
            confirmed_category_id="UNCLASSIFIED", confirmation_state=confirmation_state,
        )

    def run_step(self):
        pipeline.match_loan_repayments(self.user, Tx.objects.none())

    def test_marks_due_paid(self):
        due, tx = self.due(), self.repayment(day=date(2026, 11, 12))
        self.run_step()
        due.refresh_from_db()
        self.assertEqual((due.payment_state, due.paid_amount), ("paid", 150_000))
        self.assertEqual(due.matches.get().transaction, tx)
        tx.refresh_from_db()
        self.assertEqual(tx.transaction_type, "loan_repayment")

    def test_links_loan_side_row_when_it_exists(self):
        self.due()
        tx = self.repayment()
        loan_row = Tx.objects.create(
            user=self.user, source_kind="loan", loan=self.loan, occurred_on=date(2026, 11, 10), amount=150_000,
            direction="out", transaction_type="loan_repayment",
            confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review",
        )
        self.run_step()
        link = TransactionLink.objects.get(relation_type="LOAN_REPAYMENT")
        self.assertEqual((link.from_transaction, link.to_transaction, link.matched_amount), (tx, loan_row, 150_000))

    def test_no_match(self):
        self.due()
        self.repayment(amount=149_999)                          # 월 상환액이 다름
        self.repayment(day=date(2026, 11, 14))                  # 납부일 +4일
        self.repayment(account=self.other_account)              # 연결 계좌가 아님
        self.repayment(desc="웰컴저축은행")                      # 다른 기관
        self.run_step()
        self.assertFalse(PaymentMatch.objects.exists())
        self.assertFalse(Tx.objects.exclude(transaction_type="transfer").exists())  # 안 맞은 후보는 목록에 남는다

    def test_candidate_without_due_stays_transfer(self):
        tx = self.repayment()
        self.run_step()
        tx.refresh_from_db()
        self.assertEqual(tx.transaction_type, "transfer")

    def test_user_confirmed_withdrawal_is_left_alone(self):
        self.due()
        tx = self.repayment(confirmation_state="confirmed")
        self.run_step()
        tx.refresh_from_db()
        self.assertEqual(tx.transaction_type, "transfer")
        self.assertFalse(PaymentMatch.objects.exists())

    def test_loan_details_filled_after_withdrawal_match_on_next_run(self):
        Loan.objects.filter(pk=self.loan.pk).update(monthly_payment=None, payment_day=None)
        self.due()
        tx = self.repayment()
        self.run_step()
        self.assertFalse(PaymentMatch.objects.exists())
        Loan.objects.filter(pk=self.loan.pk).update(monthly_payment=150_000, payment_day=10)
        self.run_step()
        self.assertEqual(PaymentMatch.objects.get().transaction, tx)

    def test_home_matches_when_due_is_created_after_withdrawal(self):
        tx = self.repayment()
        self.run_step()
        self.assertFalse(PaymentDue.objects.exists())   # 아직 급여 주기가 없어 회차가 없다
        FinancialProfile.objects.create(user=self.user, housing_type="family", monthly_take_home_income=1_500_000, payday=25)
        body = home_data(self.user, TODAY)
        tx.refresh_from_db()
        self.assertEqual(tx.transaction_type, "loan_repayment")
        self.assertEqual(PaymentDue.objects.get(loan=self.loan).payment_state, "paid")
        self.assertEqual(body["must_pay"], [])

    def test_same_terms_loans_are_left_for_user(self):
        """한 출금에 맞는 대출이 여럿이면 첫 번째를 고르지 않는다 → '납부 거래 확인'에서 사용자가 고른다 (11/06)"""
        twin = Loan.objects.create(
            user=self.user, origin="manual", lender_name="한국장학재단", linked_account=self.account,
            monthly_payment=150_000, payment_day=10,
        )
        self.due()
        self.due(date(2026, 11, 11), loan=twin)
        self.repayment()
        self.repayment(day=date(2026, 11, 11))
        self.run_step()
        self.assertFalse(PaymentMatch.objects.exists())
        self.assertFalse(Tx.objects.filter(transaction_type="loan_repayment").exists())

    def test_card_company_loan_repayment_is_matched(self):
        Loan.objects.filter(pk=self.loan.pk).update(lender_name="현대카드")
        self.due()
        tx = self.repayment(desc="현대카드", transaction_type="card_payment")
        self.run_step()
        tx.refresh_from_db()
        self.assertEqual(tx.transaction_type, "loan_repayment")

    def test_bank_code_lender_uses_institution_name(self):
        Loan.objects.filter(pk=self.loan.pk).update(lender_name="0003")
        self.due()
        self.repayment(desc="기업은행 대출이자")
        self.run_step()
        self.assertEqual(PaymentMatch.objects.count(), 1)

    def test_blank_lender_matches_nothing(self):
        Loan.objects.filter(pk=self.loan.pk).update(lender_name="")
        self.due()
        self.repayment(desc="다른 기관")
        self.run_step()
        self.assertFalse(PaymentMatch.objects.exists())

    def test_syncs_each_loan_once_per_run(self):
        self.repayment(day=date(2026, 9, 10))
        self.repayment(day=date(2026, 10, 10))
        with patch("finance.api_loans.sync_loan_due") as sync:
            self.run_step()
        sync.assert_called_once()

    def test_missing_linked_account_is_not_checked(self):
        Loan.objects.filter(pk=self.loan.pk).update(linked_account=None)
        self.due()
        self.repayment(account=self.other_account)
        self.run_step()
        self.assertEqual(PaymentMatch.objects.count(), 1)

    def test_payment_day_window_crosses_month_end(self):
        Loan.objects.filter(pk=self.loan.pk).update(payment_day=31)
        self.due(date(2026, 11, 30))
        self.repayment(day=date(2026, 12, 1))
        self.run_step()
        self.assertEqual(PaymentMatch.objects.count(), 1)

    def test_creates_this_cycles_due_when_missing(self):
        BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25), status="active")
        self.repayment()
        self.run_step()
        due = PaymentDue.objects.get(loan=self.loan)
        self.assertEqual((due.due_date, due.payment_state), (date(2026, 11, 10), "paid"))

    def test_two_withdrawals_for_one_due_are_left_for_user(self):
        """한 회차에 맞는 출금이 여럿이면 자동 연결하지 않는다 (11/06)"""
        self.due()
        self.repayment(), self.repayment(day=date(2026, 11, 11))
        self.run_step()
        self.run_step()
        self.assertFalse(PaymentMatch.objects.exists())

    def test_second_withdrawal_does_not_pay_twice(self):
        self.due()
        self.repayment()
        self.run_step()
        self.repayment(day=date(2026, 11, 11))  # 나중에 들어온 같은 금액 출금
        self.run_step()
        self.assertEqual(PaymentMatch.objects.count(), 1)

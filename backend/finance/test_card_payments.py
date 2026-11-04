"""카드대금 매칭 테스트 (pipeline.match_card_payments → finance/card_payments.py)."""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import pipeline
from .card_payments import match
from .models import (BankAccount, Card, CardBill, Connection, FinancialTransaction as Tx, PaymentDue, PaymentMatch)


class CardPaymentTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="card-payment@example.invalid")
        bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        self.account = BankAccount.objects.create(user=self.user, connection=bank, source_account_key="pay")
        self.other_account = BankAccount.objects.create(user=self.user, connection=bank, source_account_key="other")
        self.connection = Connection.objects.create(user=self.user, business_type="card", organization_code="0306", status="connected")
        self.card = Card.objects.create(user=self.user, connection=self.connection, origin="provider", issuer_code="0306")

    def bill(self, amount=420_000, due=date(2026, 11, 5), key="b1", bill_type="", **fields):
        values = dict(
            user=self.user, connection=self.connection, issuer_code="0306", statement_key=key,
            scope_type="issuer", billing_month=date(2026, 11, 1), total_amount=amount,
            withdrawal_due_date=due, source_type="provider", field_states={"codefBillType": bill_type},
        )
        return CardBill.objects.create(**{**values, **fields})

    def payment(self, amount=420_000, day=date(2026, 11, 5), desc="신한카드", account=None, **fields):
        return Tx.objects.create(
            user=self.user, source_kind="bank", account=account or self.account, occurred_on=day, amount=amount,
            direction="out", source_description=desc, transaction_type="card_payment",
            confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review", **fields,
        )

    def run_step(self, *txs):
        pipeline.match_card_payments(self.user, Tx.objects.filter(pk__in=[tx.pk for tx in txs]))

    def assert_paid(self, bill, tx):
        due = PaymentDue.objects.get(bill=bill)
        self.assertEqual((due.payment_state, due.paid_amount, due.obligation_type), ("paid", bill.total_amount, "card_bill"))
        self.assertEqual(due.matches.get().transaction, tx)

    def test_matches_bill_and_marks_due_paid(self):
        bill, tx = self.bill(), self.payment()
        self.run_step(tx)
        self.assert_paid(bill, tx)

    def test_uses_existing_due(self):
        bill = self.bill()
        due = PaymentDue.objects.create(
            user=self.user, obligation_type="card_bill", bill=bill, occurrence_key="existing",
            due_date=bill.withdrawal_due_date, confirmed_amount=bill.total_amount,
            source_type="provider", confirmation_state="confirmed",
        )
        tx = self.payment()
        self.run_step(tx)
        self.assert_paid(bill, tx)
        self.assertEqual(PaymentDue.objects.get(bill=bill).pk, due.pk)

    def test_date_window_is_three_days(self):
        bill = self.bill()
        self.run_step(self.payment(day=date(2026, 11, 9)))
        self.assertFalse(PaymentMatch.objects.exists())
        tx = self.payment(day=date(2026, 11, 8))
        self.run_step(tx)
        self.assert_paid(bill, tx)

    def test_falls_back_to_payment_due_date(self):
        bill = self.bill(due=None, payment_due_date=date(2026, 11, 5))
        tx = self.payment()
        self.run_step(tx)
        self.assert_paid(bill, tx)
        self.assertEqual(PaymentDue.objects.get(bill=bill).due_date, date(2026, 11, 5))

    def test_no_match(self):
        self.bill()
        self.bill(key="month", due=date(2026, 10, 5), billing_month=date(2026, 10, 1), amount=1)  # 다른 달
        self.bill(key="date", due=None)                                                             # 날짜 모름
        cases = [
            self.payment(amount=419_999),                  # 총액이 다름
            self.payment(desc="현대카드"),                  # 다른 카드사
            self.payment(desc="이하늘"),                    # 적요에 카드사가 없음
            self.payment(is_canceled=True),
        ]
        self.run_step(*cases)
        self.assertFalse(PaymentMatch.objects.exists())

    def test_card_scope_bill_is_not_matched(self):
        self.bill(scope_type="card")
        self.run_step(self.payment())
        self.assertFalse(PaymentMatch.objects.exists())

    def test_bill_payment_account_must_match_when_known(self):
        bill = self.bill(payment_account=self.account)
        self.run_step(self.payment(account=self.other_account))
        self.assertFalse(PaymentMatch.objects.exists())
        tx = self.payment()
        self.run_step(tx)
        self.assert_paid(bill, tx)

    def test_card_payment_account_is_used_when_bill_has_none(self):
        Card.objects.filter(pk=self.card.pk).update(payment_account=self.account)
        self.bill()
        self.run_step(self.payment(account=self.other_account))
        self.assertFalse(PaymentMatch.objects.exists())

    def test_card_loan_statement_is_left_out(self):
        usage, loan = self.bill(key="use", bill_type="이용대금"), self.bill(key="loan", amount=90_000, bill_type="카드론")
        wrong = self.payment(amount=90_000)
        self.run_step(wrong)
        self.assertFalse(PaymentMatch.objects.exists())
        tx = self.payment()
        self.run_step(tx)
        self.assert_paid(usage, tx)
        self.assertFalse(PaymentDue.objects.filter(bill=loan).exists())

    def test_same_day_usage_statements_are_paid_together(self):
        first, second = self.bill(key="u1", amount=300_000, bill_type="이용대금"), self.bill(key="u2", amount=120_000, bill_type="사용대금")
        tx = self.payment()
        self.run_step(tx)
        for bill in (first, second):
            self.assertEqual(PaymentDue.objects.get(bill=bill).payment_state, "paid")
        self.assertEqual(sum(m.matched_amount for m in PaymentMatch.objects.filter(transaction=tx)), 420_000)

    def test_rerun_and_second_withdrawal_do_not_pay_twice(self):
        bill = self.bill()
        tx, again = self.payment(), self.payment(day=date(2026, 11, 6))
        self.run_step(tx, again)
        self.run_step(tx, again)
        self.assertEqual(PaymentMatch.objects.count(), 1)
        self.assertEqual(PaymentDue.objects.get(bill=bill).paid_amount, 420_000)

    def test_bill_saved_after_withdrawal_is_matched_by_later_run(self):
        tx = self.payment()
        self.run_step(tx)
        bill = self.bill()
        match(self.user)
        self.assert_paid(bill, tx)

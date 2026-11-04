"""거래 유형 판단 테스트 (pipeline.link_basics → finance/transfer_rules.py)."""
from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from . import pipeline
from .models import (BankAccount, Card, Connection, FinancialTransaction as Tx, Loan, PaymentDue, PaymentMatch,
                     RecurringExpense, TransactionLink)
from .tests import URL
from .transfer_rules import LOAN_CANDIDATE


class Fixture(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="rules@example.invalid")
        bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        self.a = BankAccount.objects.create(user=self.user, connection=bank, source_account_key="a")
        self.b = BankAccount.objects.create(user=self.user, connection=bank, source_account_key="b")
        self.card = Card.objects.create(user=self.user, origin="provider", issuer_code="0306")

    def bank(self, account, direction, amount=100_000, desc="이체", day=date(2026, 11, 3), user=None, **fields):
        return Tx.objects.create(
            user=user or self.user, source_kind="bank", account=account, occurred_on=day, amount=amount,
            direction=direction, source_description=desc,
            transaction_type="transfer" if direction == "out" else "income",
            confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review", **fields,
        )

    def approval(self, approval_no, day=date(2026, 11, 1), **fields):
        return Tx.objects.create(
            user=self.user, source_kind="card", card=self.card, occurred_on=day, amount=30_000, direction="out",
            approval_no=approval_no, transaction_type="spending",
            confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review", **fields,
        )

    def run_on(self, *txs):
        pipeline.link_basics(self.user, Tx.objects.filter(pk__in=[tx.pk for tx in txs]))
        for tx in txs:
            tx.refresh_from_db()


class OwnTransferTest(Fixture):
    def assert_pair(self, out, income):
        self.assertEqual((out.transaction_type, income.transaction_type), ("own_transfer", "own_transfer"))
        link = TransactionLink.objects.get(from_transaction=out, to_transaction=income)
        self.assertEqual((link.relation_type, link.matched_amount), ("OWN_TRANSFER", out.amount))

    def test_same_day_same_amount_between_my_accounts(self):
        out, income = self.bank(self.a, "out"), self.bank(self.b, "in")
        self.run_on(out, income)
        self.assert_pair(out, income)
        self.assertEqual(TransactionLink.objects.count(), 1)  # 쌍 하나에 연결 하나 (역방향 없음)

    def test_finds_the_other_side_saved_by_an_earlier_sync(self):
        old_in, new_out = self.bank(self.b, "in"), self.bank(self.a, "out")
        self.run_on(new_out)
        old_in.refresh_from_db()
        self.assert_pair(new_out, old_in)

    def test_each_transaction_pairs_once(self):
        first, second = self.bank(self.a, "out", occurred_time=time(9)), self.bank(self.a, "out", occurred_time=time(10))
        income = self.bank(self.b, "in")
        self.run_on(first, second, income)
        self.assert_pair(first, income)
        self.assertEqual(second.transaction_type, "transfer")

    def test_not_a_pair(self):
        other_user = get_user_model().objects.create_user(email="rules-other@example.invalid")
        other_bank = Connection.objects.create(user=other_user, business_type="bank", organization_code="0004", status="connected")
        other_account = BankAccount.objects.create(user=other_user, connection=other_bank, source_account_key="x")
        self.bank(self.a, "in")                                  # 같은 계좌
        self.bank(self.b, "in", day=date(2026, 11, 4))           # 다른 날
        self.bank(self.b, "in", amount=99_999)                   # 다른 금액
        self.bank(self.b, "in", is_canceled=True)                # 취소된 입금
        self.bank(other_account, "in", user=other_user)          # 남의 계좌
        out = self.bank(self.a, "out")
        self.run_on(out)
        self.assertEqual(out.transaction_type, "transfer")
        self.assertFalse(TransactionLink.objects.exists())

    def test_user_converted_transfer_is_left_alone(self):
        spent = self.bank(self.a, "out")
        Tx.objects.filter(pk=spent.pk).update(transaction_type="spending")
        income = self.bank(self.b, "in")
        self.run_on(income)
        self.assertEqual(income.transaction_type, "income")


class DescriptionRuleTest(Fixture):
    def test_card_issuer_name_is_card_payment(self):
        for desc in ("신한카드", "KB카드출금", "현대 카드", "bc카드 대금", "비씨카드"):
            with self.subTest(desc=desc):
                tx = self.bank(self.a, "out", desc=desc)
                self.run_on(tx)
                self.assertEqual(tx.transaction_type, "card_payment")

    def test_lender_name_marks_candidate_only_for_my_active_loans(self):
        Loan.objects.create(user=self.user, origin="manual", lender_name="한국장학재단")
        Loan.objects.create(user=self.user, origin="manual", lender_name="웰컴저축은행", status="repaid")
        repayment, repaid = self.bank(self.a, "out", desc="한국장학재단 상환"), self.bank(self.a, "out", desc="웰컴저축은행")
        self.run_on(repayment, repaid)
        self.assertEqual((repayment.transaction_type, repaid.transaction_type), ("transfer", "transfer"))
        self.assertIn(LOAN_CANDIDATE, repayment.field_states)
        self.assertNotIn(LOAN_CANDIDATE, repaid.field_states)

    def test_candidate_stays_listed_and_editable_until_really_matched(self):
        Loan.objects.create(user=self.user, origin="manual", lender_name="국민은행")
        tx = self.bank(self.a, "out", desc="국민은행 홍길동", day=timezone.localdate())
        self.run_on(tx)
        self.client.force_login(self.user)
        self.assertIn(str(tx.pk), [item["transactionId"] for item in self.client.get(URL).json()["items"]])
        self.assertEqual(self.client.get(f"{URL}{tx.pk}/").status_code, 200)
        fixed = self.client.patch(f"{URL}{tx.pk}/", data='{"category": "FOOD"}', content_type="application/json")
        self.assertEqual(fixed.status_code, 200)

    def test_friend_transfer_and_deposits_stay(self):
        friend, deposit = self.bank(self.a, "out", desc="이하늘"), self.bank(self.a, "in", desc="신한카드 캐시백", amount=500)
        self.run_on(friend, deposit)
        self.assertEqual(friend.transaction_type, "transfer")
        self.assertEqual(deposit.transaction_type, "income")

    def test_pair_wins_over_description(self):
        out, income = self.bank(self.a, "out", desc="신한카드"), self.bank(self.b, "in")
        self.run_on(out, income)
        self.assertEqual(out.transaction_type, "own_transfer")


class CollectionOrderTest(Fixture):
    """같은 거래를 한 번에 받든 나눠 받든 결과가 같아야 한다"""

    def collect(self, desc, amount, order):
        out, income = self.bank(self.a, "out", amount=amount, desc=desc), self.bank(self.b, "in", amount=amount)
        for group in {"together": [[out, income]], "out_first": [[out], [income]], "in_first": [[income], [out]]}[order]:
            self.run_on(*group)
        out.refresh_from_db()
        income.refresh_from_db()
        links = TransactionLink.objects.filter(from_transaction=out, to_transaction=income, relation_type="OWN_TRANSFER")
        return out.transaction_type, income.transaction_type, links.count()

    def test_pair_is_found_in_every_order(self):
        Loan.objects.create(user=self.user, origin="manual", lender_name="한국장학재단")
        amount = 100_000
        for desc in ("신한카드", "한국장학재단", "이체"):
            for order in ("together", "out_first", "in_first"):
                with self.subTest(desc=desc, order=order):
                    amount += 1
                    self.assertEqual(self.collect(desc, amount, order), ("own_transfer", "own_transfer", 1))

    def test_late_deposit_does_not_touch_settled_withdrawals(self):
        paid = self.bank(self.a, "out", desc="신한카드")
        self.run_on(paid)
        due = PaymentDue.objects.create(
            user=self.user, obligation_type="manual", occurrence_key="paid", due_date=date(2026, 11, 3),
            confirmed_amount=100_000, source_type="manual", confirmation_state="confirmed",
        )
        PaymentMatch.objects.create(user=self.user, due=due, transaction=paid, matched_amount=100_000)
        confirmed = self.bank(self.a, "out", desc="신한카드", amount=200_000)
        self.run_on(confirmed)
        Tx.objects.filter(pk=confirmed.pk).update(confirmation_state="confirmed")
        late = self.bank(self.b, "in"), self.bank(self.b, "in", amount=200_000)
        self.run_on(*late)
        paid.refresh_from_db()
        confirmed.refresh_from_db()
        self.assertEqual([tx.transaction_type for tx in (paid, confirmed, *late)],
                         ["card_payment", "card_payment", "income", "income"])
        self.assertFalse(TransactionLink.objects.exists())


class CancellationTest(Fixture):
    def test_links_cancel_row_to_original_by_approval_number(self):
        original = self.approval("A1")
        cancel = self.approval("A1", day=date(2026, 11, 2), is_canceled=True, canceled_amount=30_000)
        self.run_on(cancel)
        link = TransactionLink.objects.get(relation_type="CANCELLATION")
        self.assertEqual((link.from_transaction, link.to_transaction, link.matched_amount), (original, cancel, 30_000))

    def test_original_saved_after_cancel_row_is_linked_once(self):
        cancel = self.approval("A1", day=date(2026, 11, 2), is_canceled=True, canceled_amount=30_000)
        self.run_on(cancel)
        self.assertFalse(TransactionLink.objects.exists())
        original = self.approval("A1")
        self.run_on(original)
        self.run_on(original, cancel)
        link = TransactionLink.objects.get(relation_type="CANCELLATION")
        self.assertEqual((link.from_transaction, link.to_transaction, link.matched_amount), (original, cancel, 30_000))

    def test_rerun_keeps_one_link(self):
        self.approval("A1")
        cancel = self.approval("A1", is_canceled=True)
        self.run_on(cancel)
        self.run_on(cancel)
        self.assertEqual(TransactionLink.objects.count(), 1)

    def cancel_row(self, amount=12_000, desc="택시", when=time(20, 0, 1), **fields):
        return self.bank(self.a, "out", amount, desc, occurred_time=when, is_canceled=True, canceled_amount=amount, **fields)

    def test_bank_cancel_row_cancels_its_original(self):
        # 선결제 뒤 환불이 음수 출금으로 옴. 한 쌍이 0원이 되고 뒤의 실제 결제는 남는다
        original = self.bank(self.a, "out", 12_000, "택시", occurred_time=time(20, 0, 0))
        cancel = self.cancel_row()
        real = self.bank(self.a, "out", 9_500, "택시", occurred_time=time(20, 5, 0))
        self.run_on(original, cancel, real)
        link = TransactionLink.objects.get(relation_type="CANCELLATION")
        self.assertEqual((link.from_transaction, link.to_transaction, link.matched_amount), (original, cancel, 12_000))
        self.assertEqual((original.is_canceled, original.canceled_amount), (True, 12_000))
        self.assertFalse(real.is_canceled)

    def test_bank_cancel_picks_nearest_earlier_not_later_rebooking(self):
        # 결제 → 취소 → 같은 금액 재결제: 취소 뒤의 재결제는 짝이 아니다
        first = self.bank(self.a, "out", 50_000, "예약", occurred_time=time(9, 0, 0))
        cancel = self.cancel_row(50_000, "예약", time(9, 30, 0))
        again = self.bank(self.a, "out", 50_000, "예약", occurred_time=time(9, 30, 30))
        self.run_on(first, cancel, again)
        self.assertEqual((first.is_canceled, again.is_canceled), (True, False))

    def test_bank_cancel_finds_original_in_the_same_second_or_saved_later(self):
        cancel = self.cancel_row(7_000, when=time(18, 0, 0))
        self.run_on(cancel)
        self.assertFalse(TransactionLink.objects.exists())
        original = self.bank(self.a, "out", 7_000, "택시", occurred_time=time(18, 0, 0))
        self.run_on(original)
        self.run_on(original)
        self.assertEqual(TransactionLink.objects.filter(relation_type="CANCELLATION").count(), 1)
        self.assertTrue(original.is_canceled)

    def test_bank_cancel_needs_same_account_description_amount(self):
        others = [self.bank(self.b, "out", 12_000, "택시", occurred_time=time(1, 0)),
                  self.bank(self.a, "out", 12_000, "편의점", occurred_time=time(1, 0)),
                  self.bank(self.a, "out", 9_000, "택시", occurred_time=time(1, 0))]
        self.run_on(*others, self.cancel_row())
        self.assertFalse(TransactionLink.objects.filter(relation_type="CANCELLATION").exists())
        self.assertFalse(any(tx.is_canceled for tx in others))

    def mark_paid(self, tx):
        due = PaymentDue.objects.create(user=self.user, obligation_type="recurring", occurrence_key="k", due_date=date(2026, 11, 3),
                                        confirmed_amount=12_000, paid_amount=12_000, payment_state="paid", obligation_state="active",
                                        recurring=RecurringExpense.objects.create(user=self.user, name="x", amount=12_000, category_id="FOOD",
                                                                                  status="confirmed", source_type="manual"))
        return PaymentMatch.objects.create(user=self.user, due=due, transaction=tx, matched_amount=12_000)

    def test_paid_due_withdrawal_is_not_canceled(self):
        original = self.bank(self.a, "out", 12_000, "택시", occurred_time=time(1, 0))
        self.mark_paid(original)
        self.run_on(original, self.cancel_row())
        self.assertFalse(original.is_canceled)

    def test_bank_cancel_holds_instead_of_canceling_an_older_payment(self):
        # 어제 정상 결제 → 오늘 납부에 붙은 결제 → 오늘 취소: 어제 결제를 대신 취소하지 않는다
        yesterday = self.bank(self.a, "out", 12_000, "택시", day=date(2026, 11, 2), occurred_time=time(20, 0, 0))
        today = self.bank(self.a, "out", 12_000, "택시", occurred_time=time(20, 0, 0))
        paid = self.mark_paid(today)
        cancel = self.cancel_row()
        self.run_on(yesterday, today, cancel)
        self.assertEqual((yesterday.is_canceled, today.is_canceled), (False, False))
        self.assertFalse(TransactionLink.objects.filter(relation_type="CANCELLATION").exists())
        # 납부 연결이 풀리면 다음 처리 때 오늘 결제에 이어진다
        paid.delete()
        self.run_on(yesterday, today, cancel)
        self.assertEqual((yesterday.is_canceled, today.is_canceled), (False, True))

    def test_no_link_without_separate_original(self):
        in_place = self.approval("A1", is_canceled=True)         # 카드사가 원거래 줄을 취소로 바꿔 준 경우
        no_number = self.approval(None, is_canceled=True)
        self.approval("A2")
        self.run_on(in_place, no_number)
        self.assertFalse(TransactionLink.objects.exists())

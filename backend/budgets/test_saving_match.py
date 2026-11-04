"""확정 저축 이체 → saving (⑤, budgets/saving_match.py) 테스트. 실행: backend 폴더에서 python manage.py test budgets.test_saving_match
TODAY 10/21 · 저축 이체일 2일 · 8월 이체가 근거(확정할 때 saving)"""
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from finance.api import listed_transactions
from finance.models import BankAccount, Connection, FinancialTransaction, RecurringSaving, TransactionLink
from finance.pipeline import process_new_transactions

from .saving_match import match_savings

TODAY = date(2026, 10, 21)


class SavingMatchTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="save@example.com", password="Example123!", nickname="김나은")
        bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        acc = lambda key, kind: BankAccount.objects.create(  # noqa: E731
            user=self.user, connection=bank, source_account_key=key, name=key, account_type=kind, is_selected=True)
        self.a, self.b, self.c = acc("월급통장", "demand"), acc("생활통장", "demand"), acc("첫목돈적금", "deposit")

    def out(self, on, amount=900_000, kind="own_transfer", label="적금이체", account=None):
        return FinancialTransaction.objects.create(
            user=self.user, source_kind="bank", account=account or self.a, occurred_on=on, amount=amount, direction="out",
            transaction_type=kind, source_description=label, confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review")

    def own(self, on, to=None, **fields):
        tx = self.out(on, **fields)
        income = FinancialTransaction.objects.create(
            user=self.user, source_kind="bank", account=to or self.c, occurred_on=on, amount=tx.amount, direction="in",
            transaction_type="own_transfer", confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review")
        TransactionLink.objects.create(user=self.user, from_transaction=tx, to_transaction=income, relation_type="OWN_TRANSFER",
                                       matched_amount=tx.amount, confirmation_state="confirmed")
        return tx

    def saving(self, evidence, to=None, status="confirmed"):
        FinancialTransaction.objects.filter(pk=evidence.pk).update(transaction_type="saving")  # 확정할 때 근거는 saving (S-06)
        return RecurringSaving.objects.create(user=self.user, name="첫목돈적금", amount=900_000, transfer_day=2,
                                              from_account=self.a, to_account=to, status=status, source_type="detected",
                                              evidence=[str(evidence.pk)])

    def match(self, *txs):
        match_savings(self.user, [t.pk for t in txs], TODAY)

    def kind(self, tx):
        return FinancialTransaction.objects.get(pk=tx.pk).transaction_type

    # ---- 맞는 경우 ----
    def test_linked_deposit_next_month_becomes_saving(self):
        s = self.saving(self.own(date(2026, 8, 2)), to=self.c)
        tx = self.own(date(2026, 9, 3))
        self.match(tx)
        self.assertEqual(self.kind(tx), "saving")
        self.assertFalse(listed_transactions(self.user).filter(pk=tx.pk).exists())
        s.refresh_from_db()
        self.assertIn(str(tx.pk), s.evidence)

    def test_unlinked_account_same_memo_transfer_becomes_saving(self):
        self.saving(self.out(date(2026, 8, 2), kind="transfer", label="청년적금 납입"))
        tx = self.out(date(2026, 9, 2), kind="transfer", label="청년적금 납입")
        self.assertTrue(listed_transactions(self.user).filter(pk=tx.pk).exists())
        self.match(tx)
        self.assertEqual(self.kind(tx), "saving")
        self.assertFalse(listed_transactions(self.user).filter(pk=tx.pk).exists())  # 거래 목록에서 빠짐

    def test_next_month_uses_newly_added_evidence(self):
        self.saving(self.out(date(2026, 8, 2), kind="transfer", label="청년적금 납입"))
        sep, oct_ = (self.out(date(2026, m, 2), kind="transfer", label="청년적금 납입") for m in (9, 10))
        self.match(sep)
        self.match(oct_)
        self.assertEqual((self.kind(sep), self.kind(oct_)), ("saving", "saving"))

    # ---- 그대로 두는 경우 ----
    def test_mismatches_stay(self):
        self.saving(self.own(date(2026, 8, 2)), to=self.c)
        cases = {
            "금액 다름": self.own(date(2026, 9, 2), amount=800_000),
            "날짜 ±3일 밖": self.own(date(2026, 9, 9)),
            "보내는 계좌 다름": self.own(date(2026, 9, 2), account=self.b),
            "받는 계좌 다름": self.own(date(2026, 10, 2), to=self.b),
        }
        self.match(*cases.values())
        for name, tx in cases.items():
            with self.subTest(name):
                self.assertEqual(self.kind(tx), "own_transfer")

    def test_two_in_same_month_both_stay(self):
        self.saving(self.own(date(2026, 8, 2)), to=self.c)
        first, second = self.own(date(2026, 9, 1)), self.own(date(2026, 9, 3))
        self.match(first, second)
        self.assertEqual((self.kind(first), self.kind(second)), ("own_transfer", "own_transfer"))

    def test_transfer_to_other_person_stays(self):
        self.saving(self.out(date(2026, 8, 2), kind="transfer", label="청년적금 납입"))
        tx = self.out(date(2026, 9, 2), kind="transfer", label="홍길동")
        self.match(tx)
        self.assertEqual(self.kind(tx), "transfer")

    def test_transfer_needs_unlinked_saving(self):
        self.saving(self.own(date(2026, 8, 2)), to=self.c)       # 받는 계좌를 아는 저축은 내 계좌 이체 짝만
        tx = self.out(date(2026, 9, 2), kind="transfer", label="적금이체")
        self.match(tx)
        self.assertEqual(self.kind(tx), "transfer")

    def test_stopped_and_ignored_are_not_matched(self):
        for status in ("stopped", "ignored", "suggested"):
            with self.subTest(status=status):
                RecurringSaving.objects.filter(user=self.user).delete()
                self.saving(self.own(date(2026, 8, 2)), to=self.c, status=status)
                tx = self.own(date(2026, 9, 2))
                self.match(tx)
                self.assertEqual(self.kind(tx), "own_transfer")

    def test_pipeline_twice_same_result(self):
        s = self.saving(self.own(date(2026, 8, 2)), to=self.c)
        tx = self.own(date(2026, 9, 2))
        with patch("django.utils.timezone.localdate", return_value=TODAY):
            process_new_transactions(self.user, [tx.pk])
            process_new_transactions(self.user, [tx.pk])
        s.refresh_from_db()
        self.assertEqual((self.kind(tx), s.evidence.count(str(tx.pk)), len(s.evidence)), ("saving", 1, 2))

"""R08 은행 거래 키: 입출금 방향을 키에 넣고, 방향 없는 예전 키(구키)로 저장된 거래는 같은 방향일 때만 재사용 (합성 응답만)
구키는 수정 전 알고리즘으로 만든 고정값 (test_bank_response 의 R07 기준값과 같음) — 현재 키 함수로 만들지 않는다
실행: backend 폴더에서 python manage.py test finance.test_bank_tx_key
"""
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from . import codef
from .models import BankAccount, FinancialTransaction, PaymentDue, PaymentMatch, RecurringExpense
from .sync import sync_bank
from .test_bank_response import NoCodefNetwork, account, tx

OUT = tx(resAccountOut="10000", resAccountIn="0")
IN = tx(resAccountOut="0", resAccountIn="10000")       # 날짜 · 시각 · 잔액 · 설명이 OUT 과 같고 방향만 다른 합성 입력
CANCEL = tx(resAccountTrTime="123001", resAccountOut="-10000", resAccountIn="0", resAfterTranBalance="100000")
ZERO = {"resAccountTrDate": "20261022", "resAccountTrTime": "100000", "resAccountOut": "5000", "resAccountIn": "",
        "resAfterTranBalance": "0", "resAccountDesc1": "이체"}
NO_BALANCE = {k: v for k, v in ZERO.items() if k != "resAfterTranBalance"}
LEGACY = {  # 수정 전 normalize_transaction 이 만든 키
    "pair": "362a8530846e0c7f1ff7998c55e2aa4ba6d0e6c0fad3f3ac739afccec7076a8c",   # OUT · IN 모두
    "cancel": "22e47f9098f44dcd7b4465d6121299841cac6520048e86873af5ba909120d94c",
    "zero": "160b4439ded713e770f7240256383d62be0037a93eff7a0440203c80801e197b",   # ZERO · NO_BALANCE 모두
}


class KeyTest(NoCodefNetwork, SimpleTestCase):
    def test_direction_only_difference_gets_different_keys(self):
        out, inn = codef.normalize_transaction(OUT), codef.normalize_transaction(IN)
        self.assertNotEqual(out["source_key"], inn["source_key"])
        self.assertEqual((out["source_key"], inn["source_key"]),
                         (f"bank:v2:out:{LEGACY['pair']}", f"bank:v2:in:{LEGACY['pair']}"))

    def test_same_row_same_key_and_other_fields_unchanged(self):
        self.assertEqual(codef.normalize_transaction(dict(OUT)), codef.normalize_transaction(dict(OUT)))
        r = codef.normalize_transaction(CANCEL)
        self.assertEqual(r["source_key"], f"bank:v2:out:{LEGACY['cancel']}")
        self.assertEqual((r["amount"], r["direction"], r["is_canceled"], r["canceled_amount"]), (10000, "out", True, 10000))
        zero, missing = codef.normalize_transaction(ZERO), codef.normalize_transaction(NO_BALANCE)
        self.assertEqual(zero["source_key"], missing["source_key"])  # 잔액 미제공 · 0원의 기존 키 정책 그대로
        self.assertEqual(zero["source_key"], f"bank:v2:out:{LEGACY['zero']}")
        self.assertEqual((zero["balance_after"], missing["balance_after"]), (0, None))
        self.assertLessEqual(len(zero["source_key"]), FinancialTransaction._meta.get_field("source_key").max_length)


class KeySyncTest(NoCodefNetwork, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email="bank-key@example.com")
        cls.other = get_user_model().objects.create_user(email="bank-key-other@example.com")

    def setUp(self):
        self.start, self.end = date(2026, 10, 1), date(2026, 10, 27)
        self.history = []
        patcher = patch("finance.codef._request", side_effect=lambda path, params: (
            {"resDepositTrust": [account()]} if path == codef.PATH_ACCOUNT_LIST else {"resTrHistoryList": self.history}))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.process = patch("finance.sync.process_new_transactions").start()
        self.addCleanup(patch.stopall)

    def sync(self, *rows, user=None):
        self.history = [dict(r) for r in rows]
        return sync_bank(user or self.user, "cid", "0088", self.start, self.end)

    def legacy(self, key, direction, user=None, **fields):
        """수정 전 동기화가 남긴 것 같은 거래 (구키) + 사용자가 확정한 분류"""
        user = user or self.user
        if not BankAccount.objects.filter(user=user).exists():
            self.sync(user=user)  # 계좌만 만든다
        return FinancialTransaction.objects.create(**{
            "user": user, "account": BankAccount.objects.get(user=user), "source_kind": "bank", "source_key": key,
            "occurred_on": date(2026, 10, 20), "amount": 10000, "direction": direction,
            "transaction_type": "spending" if direction == "out" else "income",
            "confirmed_category_id": "FOOD" if direction == "out" else "UNCLASSIFIED", "confirmation_state": "confirmed",
            "classification_source": "user", **fields})

    def keys(self, user=None):
        return sorted(FinancialTransaction.objects.filter(user=user or self.user).values_list("direction", "source_key"))

    def test_new_pair_saved_twice_and_stays_two(self):
        self.assertEqual(self.sync(OUT, IN)["new_transaction_count"], 2)
        self.assertEqual(self.sync(OUT, IN), {"account_count": 1, "new_transaction_count": 0, "existing_transaction_count": 2})
        self.assertEqual(self.keys(), [("in", f"bank:v2:in:{LEGACY['pair']}"), ("out", f"bank:v2:out:{LEGACY['pair']}")])

    def test_legacy_row_is_reused_with_id_state_and_links(self):
        old = self.legacy(LEGACY["pair"], "out")
        recurring = RecurringExpense.objects.create(
            user=self.user, payment_account=old.account, name="시험 고정지출", amount=10000,
            category_id="HOUSING_TELECOM", status="confirmed", source_type="manual", evidence=[str(old.pk)])
        due = PaymentDue.objects.create(
            user=self.user, recurring=recurring, payment_account=old.account, obligation_type="recurring",
            occurrence_key="r08", due_date=self.end, confirmed_amount=10000, source_type="manual",
            confirmation_state="confirmed", paid_amount=10000, payment_state="paid")
        match = PaymentMatch.objects.create(user=self.user, due=due, transaction=old, matched_amount=10000)
        self.process.reset_mock()

        result = self.sync(OUT)

        self.assertEqual(result, {"account_count": 1, "new_transaction_count": 0, "existing_transaction_count": 1})
        self.process.assert_called_once_with(self.user, [])  # 키만 옮긴 거래는 신규 후처리를 다시 하지 않는다
        tx_ = FinancialTransaction.objects.get(user=self.user)
        self.assertEqual(tx_.pk, old.pk)
        self.assertEqual(tx_.source_key, f"bank:v2:out:{LEGACY['pair']}")
        self.assertEqual((tx_.transaction_type, tx_.confirmed_category_id, tx_.confirmation_state, tx_.classification_source),
                         ("spending", "FOOD", "confirmed", "user"))
        self.assertEqual(PaymentMatch.objects.get(pk=match.pk).transaction_id, old.pk)
        self.assertEqual(self.sync(OUT)["existing_transaction_count"], 1)  # 다시 해도 같은 행

    def test_opposite_direction_with_same_legacy_key_is_separate(self):
        for first, second, stored in (("out", "in", (OUT, IN)), ("out", "in", (IN, OUT)),
                                      ("in", "out", (OUT, IN)), ("in", "out", (IN, OUT))):
            with self.subTest(stored=first, order=[r["resAccountOut"] for r in stored]):
                FinancialTransaction.objects.all().delete()
                old = self.legacy(LEGACY["pair"], first)
                only_new = IN if first == "out" else OUT
                result = self.sync(only_new)  # 반대 방향만 먼저 온다
                self.assertEqual(result["new_transaction_count"], 1)
                old.refresh_from_db()
                self.assertEqual((old.direction, old.source_key), (first, LEGACY["pair"]))  # 바꾸지 않고 옮기지 않는다
                result = self.sync(*stored)  # 두 방향이 같이 다시 온다 (순서 무관)
                self.assertEqual((result["new_transaction_count"], result["existing_transaction_count"]), (0, 2))
                self.assertEqual(FinancialTransaction.objects.filter(user=self.user).count(), 2)
                self.assertEqual(FinancialTransaction.objects.get(pk=old.pk).source_key, f"bank:v2:{first}:{LEGACY['pair']}")
                self.assertEqual(self.keys(), [("in", f"bank:v2:in:{LEGACY['pair']}"), ("out", f"bank:v2:out:{LEGACY['pair']}")])

    def test_cancel_and_missing_balance_legacy_rows(self):
        cancel = self.legacy(LEGACY["cancel"], "out", is_canceled=True, canceled_amount=10000)
        zero = self.legacy(LEGACY["zero"], "out", amount=5000)
        self.assertEqual(self.sync(CANCEL, NO_BALANCE)["new_transaction_count"], 0)
        cancel.refresh_from_db()
        self.assertEqual((cancel.source_key, cancel.is_canceled, cancel.canceled_amount),
                         (f"bank:v2:out:{LEGACY['cancel']}", True, 10000))
        self.assertEqual(FinancialTransaction.objects.get(pk=zero.pk).source_key, f"bank:v2:out:{LEGACY['zero']}")
        self.assertEqual(FinancialTransaction.objects.filter(user=self.user).count(), 2)

    def test_other_users_and_accounts_legacy_rows_are_not_reused(self):
        theirs = self.legacy(LEGACY["pair"], "out", user=self.other)
        self.sync()  # 내 계좌만 만든다
        mine = BankAccount.objects.get(user=self.user)
        other_account = BankAccount.objects.create(user=self.user, connection=mine.connection, source_account_key="other")
        elsewhere = FinancialTransaction.objects.create(
            user=self.user, account=other_account, source_kind="bank", source_key=LEGACY["pair"],
            occurred_on=date(2026, 10, 20), amount=10000, direction="out", transaction_type="spending",
            confirmed_category_id="FOOD", confirmation_state="confirmed")
        self.assertEqual(self.sync(OUT)["new_transaction_count"], 1)
        theirs.refresh_from_db()
        self.assertEqual(theirs.source_key, LEGACY["pair"])
        self.assertEqual(FinancialTransaction.objects.get(pk=elsewhere.pk).source_key, LEGACY["pair"])
        self.assertEqual(FinancialTransaction.objects.get(account=mine).source_key, f"bank:v2:out:{LEGACY['pair']}")

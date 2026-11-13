"""대출 납부 거래 확인 API (finance/api_loan_payments.py) — 사용자가 출금을 골라 '다 냈어요' · '일부만 냈어요'로 확인"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections, transaction
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from budgets.api import refresh_due_state
from budgets.models import Budget
from finance.spending import spending_summary
from users.models import FinancialProfile

from . import pipeline
from .api import listed_transactions, transaction_item
from .api_loans import sync_loan_due
from .models import (BankAccount, Connection, FinancialTransaction as Tx, Loan, PaymentDue, PaymentMatch,
                     RecurringExpense)

# 실제 CODEF·LLM 호출 차단: 이 모듈 테스트가 도는 동안만 (setUpModule ~ tearDownModule)
_blocks = [
    patch("finance.codef._request", side_effect=AssertionError("CODEF 호출 금지")),
    patch("chat.llm.openai_rest.ResponsesClient.__init__", side_effect=AssertionError("LLM 호출 금지")),
]


def setUpModule():
    # 11/13: 모듈을 불러올 때 시작하면 전체 테스트(python manage.py test)에서 다른 앱(chat · policies)의
    # ResponsesClient 테스트까지 막혀 깨진다. 이 모듈 테스트가 돌 때만 막는다
    for block in _blocks:
        block.start()


def tearDownModule():
    for block in _blocks:
        block.stop()


TODAY = date(2026, 11, 12)


def url(due, tx=None):
    return f"/api/finance/loan-dues/{due.pk}/payments/" + (f"{tx.pk}/" if tx else "")


class Base:
    def setUp(self):
        clock = patch("django.utils.timezone.localdate", return_value=TODAY)
        self.today = clock.start()
        self.addCleanup(clock.stop)
        self.user = get_user_model().objects.create_user(email="loan-due-pay@example.invalid")
        FinancialProfile.objects.create(user=self.user, monthly_take_home_income=2_800_000, payday=25)
        bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        self.account = BankAccount.objects.create(user=self.user, connection=bank, source_account_key="pay", account_type="demand")
        self.other_account = BankAccount.objects.create(user=self.user, connection=bank, source_account_key="other", account_type="demand")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get("/api/home/").status_code, 200)  # 급여 주기 10/25~11/25
        self.loan = self.make_loan()
        self.due = PaymentDue.objects.get(loan=self.loan)

    def make_loan(self, **values):
        fields = {"lender_name": "한국장학재단", "linked_account": self.account, "monthly_payment": 300_000,
                  "payment_day": 10, **values}
        loan = Loan.objects.create(user=self.user, origin="manual", status="repaying", **fields)
        with transaction.atomic():
            sync_loan_due(loan)  # 대출 등록처럼 이번 주기 회차 (11/10)
        return loan

    def tx(self, amount=300_000, day=date(2026, 11, 10), desc="자동이체", account=None, user=None, **values):
        fields = {"transaction_type": "transfer", "confirmed_category_id": "UNCLASSIFIED",
                  "confirmation_state": "needs_review", **values}
        return Tx.objects.create(
            user=user or self.user, source_kind="bank", account=account or self.account, occurred_on=day,
            amount=amount, direction="out", source_description=desc, **fields,
        )

    def link(self, due, tx, settlement="full", **extra):
        return self.client.post(url(due), {"transactionId": str(tx.pk), "settlement": settlement, **extra}, format="json")

    def state(self, due=None):
        due = due or self.due
        due.refresh_from_db()
        return due.payment_state, due.paid_amount


class LoanDuePaymentTests(Base, TestCase):
    def test_full_with_different_amount_and_no_lender_name(self):
        tx = self.tx(amount=298_000, desc="자동이체 출금")
        body = self.client.get(url(self.due)).json()
        item = next(i for i in body["items"] if i["transactionId"] == str(tx.pk))
        self.assertEqual((item["amount"], item["amountDifference"], item["description"]), (298_000, -2_000, "자동이체 출금"))
        self.assertEqual(item["hints"]["lenderInDescription"], False)
        self.assertTrue(item["hints"]["linkedAccount"])
        self.assertEqual(body["due"]["expectedAmount"], 300_000)
        response = self.link(self.due, tx, "full")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["due"]["paymentState"], "paid")
        self.assertEqual(self.state(), ("paid", 298_000))
        self.loan.refresh_from_db()
        self.due.refresh_from_db()
        self.assertEqual((self.loan.status, self.loan.monthly_payment, self.due.confirmed_amount), ("repaying", 300_000, 300_000))
        tx.refresh_from_db()
        self.assertEqual((tx.transaction_type, tx.confirmation_state), ("loan_repayment", "confirmed"))
        home = self.client.get("/api/home/").json()
        self.assertNotIn(str(self.due.pk), [i["dueId"] for i in home["mustPay"]])
        self.assertEqual(self.state(), ("paid", 298_000))

    def test_partial_stays_partial_even_above_90_percent(self):
        tx = self.tx(amount=290_000)
        self.assertEqual(self.link(self.due, tx, "partial").status_code, 200)
        self.assertEqual(self.state(), ("partial", 290_000))
        refresh_due_state(self.due, TODAY)
        pipeline.match_loan_repayments(self.user, Tx.objects.none())
        pipeline.match_payment_dues(self.user, Tx.objects.none())
        home = self.client.get("/api/home/").json()
        self.assertEqual(self.state(), ("partial", 290_000))
        row = next(i for i in home["mustPay"] if i["dueId"] == str(self.due.pk))
        self.assertEqual((row["paymentState"], row["amount"]), ("partial", 10_000))

    def test_amount_difference_uses_remaining_after_partial(self):
        """11/9: 일부만 낸 뒤 다른 출금을 고르면 남은 금액(예정 − 이미 연결한 출금)과 비교한다. 연결한 출금은 자기 금액을 빼고 본다"""
        first = self.tx(amount=200_000)
        self.assertEqual(self.link(self.due, first, "partial").status_code, 200)
        second = self.tx(amount=100_000, day=date(2026, 11, 11))
        body = self.client.get(url(self.due)).json()
        item = next(i for i in body["items"] if i["transactionId"] == str(second.pk))
        self.assertEqual(item["amountDifference"], 0)                  # 남은 100,000 과 같음 (예정 300,000 기준이면 −200,000)
        self.assertEqual(body["linked"][0]["amountDifference"], -100_000)  # 연결한 출금은 예정 300,000 과 비교

    def test_split_payments_then_full_on_last(self):
        first, second, last = self.tx(100_000, date(2026, 11, 5)), self.tx(100_000, date(2026, 11, 8)), self.tx(95_000)
        self.link(self.due, first, "partial")
        self.link(self.due, second, "partial")
        self.assertEqual(self.state(), ("partial", 200_000))
        body = self.link(self.due, last, "full").json()
        self.assertEqual(self.state(), ("paid", 295_000))
        self.assertEqual(sorted(i["settlement"] for i in body["linked"]), ["full", "partial", "partial"])
        self.assertEqual(PaymentMatch.objects.filter(due=self.due).count(), 3)
        # '다 냈어요'를 다른 출금으로 옮기면 앞의 것은 '일부'로
        body = self.link(self.due, first, "full").json()
        self.assertEqual(sorted((i["transactionId"], i["settlement"]) for i in body["linked"]),
                         sorted([(str(first.pk), "full"), (str(second.pk), "partial"), (str(last.pk), "partial")]))
        self.assertEqual(self.state(), ("paid", 295_000))

    def test_correct_settlement_and_transaction(self):
        wrong, right = self.tx(300_000, date(2026, 11, 9)), self.tx(300_000, date(2026, 11, 11))
        self.link(self.due, wrong, "full")
        self.assertEqual(self.link(self.due, wrong, "partial").json()["due"]["paymentState"], "partial")
        self.assertEqual(self.client.delete(url(self.due, wrong)).status_code, 200)
        self.assertEqual(self.link(self.due, right, "full").status_code, 200)
        self.assertEqual(list(PaymentMatch.objects.filter(due=self.due).values_list("transaction", flat=True)), [right.pk])
        self.assertEqual(self.state(), ("paid", 300_000))

    def test_cancel_restores_spending_and_needs_confirm(self):
        tx = self.tx(amount=300_000, transaction_type="spending", confirmed_category_id="FOOD",
                     confirmation_state="confirmed", classification_source="user")
        spent = lambda: spending_summary(transaction_item(t) for t in listed_transactions(self.user))["spent_total"]
        before = spent()
        item = next(i for i in self.client.get(url(self.due)).json()["items"] if i["transactionId"] == str(tx.pk))
        self.assertEqual(item["reclassify"], {"fromType": "spending", "fromCategory": "FOOD", "needsConfirm": True})
        response = self.link(self.due, tx, "full")
        self.assertEqual(response.status_code, 400)
        self.assertIn("confirmReclassify", response.json()["error"]["fields"])
        self.assertFalse(PaymentMatch.objects.exists())
        self.assertEqual(self.link(self.due, tx, "full", confirmReclassify=True).status_code, 200)
        self.assertEqual(spent(), before - 300_000)  # 소비에서 빠지고 대출(월 상환액으로 따로 확보)로 — 두 번 세지 않음
        linked = self.client.get(url(self.due)).json()["linked"][0]
        self.assertEqual(linked["cancelEffect"], {"kind": "restore", "toType": "spending", "toCategory": "FOOD"})
        response = self.client.delete(url(self.due, tx))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["released"], {"transactionId": str(tx.pk), "classification": "restored",
                                                       "transactionType": "spending", "category": "FOOD"})
        tx.refresh_from_db()
        self.assertEqual((tx.transaction_type, tx.confirmed_category_id, tx.confirmation_state, tx.classification_source),
                         ("spending", "FOOD", "confirmed", "user"))
        self.assertEqual(spent(), before)
        self.assertEqual(self.state(), ("needs_review", 0))

    def test_cancel_keeps_later_user_change(self):
        tx = self.tx()
        self.link(self.due, tx, "full")
        Tx.objects.filter(pk=tx.pk).update(transaction_type="spending", confirmed_category_id="HOUSEHOLD")  # 그 뒤 사용자가 바꿈
        self.assertEqual(self.client.get(url(self.due)).json()["linked"][0]["cancelEffect"],
                         {"kind": "keep", "toType": "spending", "toCategory": "HOUSEHOLD"})
        # 원래 분류 기록이 있으면 보낸 분류는 쓰지 않는다
        response = self.client.delete(url(self.due, tx), {"restoreType": "transfer"}, format="json")
        self.assertEqual(response.json()["released"]["classification"], "kept")
        tx.refresh_from_db()
        self.assertEqual((tx.transaction_type, tx.confirmed_category_id), ("spending", "HOUSEHOLD"))

    def legacy_auto(self):
        """이번 변경 전 자동 연결: 유형만 대출 상환으로 바꾸고 원래 분류 기록이 없다"""
        tx = self.tx(desc="한국장학재단", transaction_type="loan_repayment")
        PaymentMatch.objects.create(user=self.user, due=self.due, transaction=tx, matched_amount=300_000)
        refresh_due_state(self.due, TODAY)
        return tx

    def row(self, tx):
        return Tx.objects.filter(pk=tx.pk).values().get()

    def test_legacy_auto_link_needs_user_choice_before_release(self):
        tx = self.legacy_auto()
        self.assertEqual(self.client.get(url(self.due)).json()["linked"][0]["cancelEffect"],
                         {"kind": "choose", "toType": None, "toCategory": None})
        before, due_before = self.row(tx), PaymentDue.objects.filter(pk=self.due.pk).values().get()
        for body in (None, {"restoreType": "spending"}, {"restoreType": "spending", "restoreCategory": "UNCLASSIFIED"},
                     {"restoreType": "transfer", "restoreCategory": "FOOD"}, {"restoreType": "income"}, {"other": 1}):
            response = self.client.delete(url(self.due, tx), body, format="json")
            self.assertEqual(response.status_code, 400, body)
            self.assertEqual(response.json()["error"]["code"], "invalid")
        # 실패한 정정은 아무것도 바꾸지 않는다 (연결 · 분류 · 회차 그대로)
        self.assertEqual(self.row(tx), before)
        self.assertEqual(PaymentDue.objects.filter(pk=self.due.pk).values().get(), due_before)
        self.assertTrue(PaymentMatch.objects.filter(due=self.due, transaction=tx).exists())

        spent = lambda: spending_summary(transaction_item(t) for t in listed_transactions(self.user))["spent_total"]
        response = self.client.delete(url(self.due, tx), {"restoreType": "spending", "restoreCategory": "FOOD"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["released"], {"transactionId": str(tx.pk), "classification": "chosen",
                                                       "transactionType": "spending", "category": "FOOD"})
        tx.refresh_from_db()
        self.assertEqual((tx.transaction_type, tx.confirmed_category_id, tx.confirmation_state, tx.classification_source),
                         ("spending", "FOOD", "confirmed", "user"))
        self.assertNotIn("original_transaction_type", tx.field_states)  # 원래 이체였다고 추측하지 않음
        self.assertIn(tx.pk, listed_transactions(self.user).values_list("pk", flat=True))  # 거래 화면에 다시 보임
        self.assertEqual(spent(), 300_000)
        after = self.row(tx)
        again = self.client.delete(url(self.due, tx), {"restoreType": "transfer"}, format="json")  # 재요청
        self.assertEqual((again.status_code, again.json()["released"]), (200, None))
        self.assertEqual(self.row(tx), after)
        pipeline.match_loan_repayments(self.user, Tx.objects.none())
        self.assertFalse(PaymentMatch.objects.exists())

    def test_legacy_auto_link_can_become_transfer(self):
        tx = self.legacy_auto()
        response = self.client.delete(url(self.due, tx), {"restoreType": "transfer"}, format="json")
        self.assertEqual(response.json()["released"]["classification"], "chosen")
        tx.refresh_from_db()
        self.assertEqual((tx.transaction_type, tx.confirmed_category_id, tx.confirmation_state), ("transfer", "UNCLASSIFIED", "needs_review"))
        self.assertEqual(self.state(), ("needs_review", 0))

    def test_confirmed_legacy_link_still_asks_instead_of_restoring_loan_type(self):
        """예전 자동 연결을 사용자가 '다 냈어요'로 확인해도 기록한 원래 분류가 대출 상환이라 되돌릴 값이 아니다"""
        tx = self.legacy_auto()
        self.link(self.due, tx, "full")
        self.assertEqual(self.client.get(url(self.due)).json()["linked"][0]["cancelEffect"]["kind"], "choose")
        self.assertEqual(self.client.delete(url(self.due, tx)).status_code, 400)
        self.assertEqual(self.state(), ("paid", 300_000))

    def test_same_purchase_withdrawal_is_protected(self):
        from .models import Card, TransactionLink
        card_conn = Connection.objects.create(user=self.user, business_type="card", organization_code="0301", status="connected")
        card = Card.objects.create(user=self.user, connection=card_conn, source_card_key="c1", origin="provider")
        approval = Tx.objects.create(user=self.user, source_kind="card", card=card, occurred_on=date(2026, 11, 10),
                                     amount=300_000, direction="out", transaction_type="spending",
                                     confirmed_category_id="FASHION_SHOPPING", confirmation_state="confirmed")
        withdrawal = self.tx(desc="체크카드 출금", transaction_type="spending", confirmed_category_id="FASHION_SHOPPING",
                             confirmation_state="confirmed")
        TransactionLink.objects.create(user=self.user, from_transaction=withdrawal, to_transaction=approval,
                                       relation_type="SAME_PURCHASE", matched_amount=300_000, confirmation_state="confirmed")
        spent = lambda: spending_summary(transaction_item(t) for t in listed_transactions(self.user))["spent_total"]
        before = (self.row(withdrawal), self.row(approval), list(TransactionLink.objects.values()), spent())
        self.assertNotIn(str(withdrawal.pk), [i["transactionId"] for i in self.client.get(url(self.due)).json()["items"]])
        response = self.link(self.due, withdrawal, "full", confirmReclassify=True)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["fields"], {"transactionId": ["고를 수 없는 거래예요."]})
        self.assertEqual((self.row(withdrawal), self.row(approval), list(TransactionLink.objects.values()), spent()), before)
        self.assertFalse(PaymentMatch.objects.exists())

    def test_repeat_correction_changes_nothing_more(self):
        tx = self.tx()
        self.link(self.due, tx, "partial")
        first = (self.row(tx), PaymentDue.objects.filter(pk=self.due.pk).values().get())
        self.link(self.due, tx, "partial")
        self.assertEqual((self.row(tx), PaymentDue.objects.filter(pk=self.due.pk).values().get()), first)

    def test_cancelled_link_is_not_auto_relinked(self):
        tx = self.tx(desc="한국장학재단")  # 기존 자동 연결 조건을 모두 만족
        pipeline.match_loan_repayments(self.user, Tx.objects.none())
        self.assertEqual(self.state(), ("paid", 300_000))
        auto = self.client.get(url(self.due)).json()["linked"][0]
        self.assertEqual((auto["settlement"], auto["source"]), (None, "rule"))
        self.client.delete(url(self.due, tx))
        tx.refresh_from_db()
        self.assertEqual(tx.transaction_type, "transfer")  # 자동 연결이 바꾼 유형도 되돌림
        pipeline.match_loan_repayments(self.user, Tx.objects.none())
        self.client.get("/api/home/")
        self.assertFalse(PaymentMatch.objects.exists())
        self.assertEqual(self.link(self.due, tx, "full").status_code, 200)  # 사용자는 다시 고를 수 있다

    def test_repeat_and_idempotent_requests(self):
        tx = self.tx()
        headers = {"HTTP_IDEMPOTENCY_KEY": "loan-pay-0001"}
        body = {"transactionId": str(tx.pk), "settlement": "full"}
        first = self.client.post(url(self.due), body, format="json", **headers)
        second = self.client.post(url(self.due), body, format="json", **headers)
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(first.json(), second.json())
        self.link(self.due, tx, "full")  # 번호 없이 같은 요청
        self.assertEqual(PaymentMatch.objects.count(), 1)
        self.assertEqual(self.client.delete(url(self.due, tx)).status_code, 200)
        self.assertEqual(self.client.delete(url(self.due, tx)).status_code, 200)  # 이미 풀림 → 그대로 성공
        self.assertFalse(PaymentMatch.objects.exists())

    def test_other_user_cannot_touch(self):
        other = get_user_model().objects.create_user(email="loan-due-other@example.invalid")
        bank = Connection.objects.create(user=other, business_type="bank", organization_code="0088", status="connected")
        account = BankAccount.objects.create(user=other, connection=bank, source_account_key="x", account_type="demand")
        their_tx = self.tx(user=other, account=account)
        mine = self.tx()
        response = self.link(self.due, their_tx)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["fields"], {"transactionId": ["고를 수 없는 거래예요."]})
        self.assertNotIn(str(their_tx.pk), [i["transactionId"] for i in self.client.get(url(self.due)).json()["items"]])
        stranger = APIClient()
        stranger.force_authenticate(other)
        self.assertEqual(stranger.get(url(self.due)).status_code, 404)
        self.assertEqual(stranger.post(url(self.due), {"transactionId": str(mine.pk), "settlement": "full"},
                                       format="json").status_code, 404)
        self.link(self.due, mine)
        self.assertEqual(stranger.delete(url(self.due, mine)).status_code, 404)
        self.assertEqual(PaymentMatch.objects.count(), 1)
        self.assertEqual(self.client.get("/api/finance/loan-dues/not-a-uuid/payments/").status_code, 404)

    def test_protected_transactions_are_not_taken(self):
        canceled = self.tx(is_canceled=True)
        own = self.tx(transaction_type="own_transfer")
        suspected = self.tx(field_states={"own_transfer_review": "suggested"})  # R11 확인 중인 출금
        evidence = self.tx(transaction_type="spending", confirmed_category_id="HOUSING_TELECOM")
        RecurringExpense.objects.create(user=self.user, name="월세", amount=300_000, payment_day=10, status="confirmed",
                                        source_type="manual", category_id="HOUSING_TELECOM", evidence=[str(evidence.pk)])
        other_loan = self.make_loan(lender_name="다른은행", payment_day=11)
        other_due = PaymentDue.objects.get(loan=other_loan)
        taken = self.tx(day=date(2026, 11, 11))
        self.assertEqual(self.link(other_due, taken, "full").status_code, 200)
        ids = [i["transactionId"] for i in self.client.get(url(self.due)).json()["items"]]
        for tx in (canceled, own, suspected, evidence, taken):
            self.assertNotIn(str(tx.pk), ids)
        for tx in (canceled, own, suspected, evidence):
            self.assertEqual(self.link(self.due, tx, confirmReclassify=True).status_code, 400)
        response = self.link(self.due, taken)
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "not_ready"))
        self.assertEqual(list(PaymentMatch.objects.values_list("due", flat=True)), [other_due.pk])

    def test_multiple_loans_for_one_withdrawal_are_not_auto_linked(self):
        twin = self.make_loan()
        self.tx(desc="한국장학재단")
        pipeline.match_loan_repayments(self.user, Tx.objects.none())
        self.assertFalse(PaymentMatch.objects.exists())
        self.assertEqual(len(self.client.get(url(PaymentDue.objects.get(loan=twin))).json()["items"]), 1)

    def test_wider_search_finds_other_day_and_account(self):
        far = self.tx(day=date(2026, 10, 26), account=self.other_account, amount=310_000)
        default = self.client.get(url(self.due)).json()
        self.assertEqual((default["searchFrom"], default["searchTo"]), ("2026-11-03", "2026-11-12"))
        self.assertNotIn(str(far.pk), [i["transactionId"] for i in default["items"]])
        wide = self.client.get(url(self.due) + "?from=2026-10-20&to=2026-11-12").json()
        self.assertIn(str(far.pk), [i["transactionId"] for i in wide["items"]])
        self.assertEqual(self.client.get(url(self.due) + "?from=2026-13-01").status_code, 400)
        self.assertEqual(self.client.get(url(self.due) + "?from=2026-01-01&to=2026-11-12").status_code, 400)

    def test_linked_account_and_previous_choice_rank_first(self):
        elsewhere = self.tx(day=date(2026, 11, 10), account=self.other_account)
        linked = self.tx(day=date(2026, 11, 6))
        items = self.client.get(url(self.due)).json()["items"]
        self.assertEqual(items[0]["transactionId"], str(linked.pk))
        self.assertIn(str(elsewhere.pk), [i["transactionId"] for i in items])

    def test_approved_budget_and_loan_amount_unchanged(self):
        from budgets.models import BudgetCycle
        cycle = BudgetCycle.objects.get(user=self.user, status="active")
        Budget.objects.create(user=self.user, cycle=cycle, version_no=1, status="approved", funding_amount=1_500_000)
        before = list(Budget.objects.values())
        tx = self.tx(amount=250_000)
        self.link(self.due, tx, "full")
        self.client.get("/api/home/")
        self.assertEqual(self.state(), ("paid", 250_000))  # 예정액의 90% 미만이어도 사용자가 다 냈다고 확인
        self.assertEqual(list(Budget.objects.values()), before)
        self.assertEqual(Loan.objects.get(pk=self.loan.pk).monthly_payment, 300_000)

    def test_recent_dues_in_loan_list(self):
        self.link(self.due, self.tx(), "full")
        item = self.client.get("/api/finance/loans/").json()["items"][0]
        self.assertEqual(item["recentDues"], [{"dueId": str(self.due.pk), "dueDate": "2026-11-10",
                                               "expectedAmount": 300_000, "paidAmount": 300_000, "paymentState": "paid"}])

    def test_unknown_body_field_rejected(self):
        tx = self.tx()
        response = self.link(self.due, tx, "full", extra=1)
        self.assertEqual(response.status_code, 400)
        response = self.link(self.due, tx, "all")
        self.assertIn("settlement", response.json()["error"]["fields"])


@skipUnless(connection.vendor == "postgresql", "PostgreSQL 동시 요청 검사")
class LoanDuePaymentConcurrencyTests(Base, TransactionTestCase):
    serialized_rollback = True

    def post(self, args):
        barrier, due, tx = args
        close_old_connections()
        try:
            client = APIClient()
            client.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
            barrier.wait(5)
            return client.post(url(due), {"transactionId": str(tx.pk), "settlement": "full"}, format="json").status_code
        finally:
            connections.close_all()

    def test_same_transaction_to_two_dues_only_one_wins(self):
        other_due = PaymentDue.objects.get(loan=self.make_loan(lender_name="다른은행"))
        tx = self.tx()
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = sorted(pool.map(self.post, [(barrier, self.due, tx), (barrier, other_due, tx)]))
        self.assertEqual(codes, [200, 409])
        self.assertEqual(PaymentMatch.objects.filter(transaction=tx).count(), 1)

    def test_same_request_twice_concurrently_links_once(self):
        tx = self.tx()
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(self.post, [(barrier, self.due, tx)] * 2))
        self.assertEqual(codes, [200, 200])
        self.assertEqual(PaymentMatch.objects.filter(transaction=tx).count(), 1)


class LoanDuePaymentContractTests(Base, TestCase):
    """실제 요청 · 응답 · 오류가 docs/openapi/parts/loans.json 계약과 맞는지"""

    def assert_matches(self, body, ref):
        from openapi_schema_validator import OAS30Validator, oas30_format_checker
        from config.openapi import build_contract
        contract = getattr(type(self), "contract", None) or build_contract()
        type(self).contract = contract
        validator = OAS30Validator({"$ref": f"#/components/schemas/{ref}", "components": contract["components"]},
                                   format_checker=oas30_format_checker)
        errors = [f"{list(e.path)}: {e.message}" for e in validator.iter_errors(body)]
        self.assertEqual(errors, [], ref)

    def test_real_payloads_match_contract(self):
        food = self.tx(amount=300_000, transaction_type="spending", confirmed_category_id="FOOD", confirmation_state="confirmed")
        plain = self.tx(amount=298_000)
        self.assert_matches(self.client.get(url(self.due)).json(), "LoanDuePaymentState")
        request = {"transactionId": str(food.pk), "settlement": "partial", "confirmReclassify": True}
        self.assert_matches(request, "LoanPaymentLinkRequest")
        self.assert_matches(self.client.post(url(self.due), request, format="json").json(), "LoanDuePaymentState")
        self.assert_matches(self.link(self.due, plain, "full").json(), "LoanDuePaymentState")
        self.assert_matches(self.client.get(url(self.due)).json(), "LoanDuePaymentState")
        released = self.client.delete(url(self.due, food)).json()
        self.assertEqual(released["released"]["classification"], "restored")
        self.assert_matches(released, "LoanDuePaymentState")
        self.assert_matches(self.client.delete(url(self.due, food)).json(), "LoanDuePaymentState")  # released: null
        for item in self.client.get("/api/finance/loans/").json()["items"]:
            self.assert_matches(item, "Loan")
        legacy = self.tx(amount=300_000, day=date(2026, 11, 9), transaction_type="loan_repayment")
        PaymentMatch.objects.create(user=self.user, due=self.due, transaction=legacy, matched_amount=300_000)
        self.assert_matches(self.client.get(url(self.due)).json(), "LoanDuePaymentState")
        body = {"restoreType": "spending", "restoreCategory": "FOOD"}
        self.assert_matches(body, "LoanPaymentReleaseRequest")
        self.assert_matches(self.client.delete(url(self.due, legacy), body, format="json").json(), "LoanDuePaymentState")

    def test_real_errors_match_contract(self):
        legacy = self.tx(transaction_type="loan_repayment")
        PaymentMatch.objects.create(user=self.user, due=self.due, transaction=legacy, matched_amount=300_000)
        cases = [
            (self.client.delete(url(self.due, legacy)), 400, "invalid", {"restoreType"}),
            (self.client.get(url(self.due) + "?from=2026-01-01&to=2026-11-12"), 400, "invalid", {"to"}),
            (self.link(self.due, self.tx(is_canceled=True)), 400, "invalid", {"transactionId"}),
            (self.client.get("/api/finance/loan-dues/00000000-0000-0000-0000-000000000000/payments/"), 404, "not_found", None),
        ]
        for response, status, code, fields in cases:
            with self.subTest(code=code, fields=fields):
                self.assertEqual(response.status_code, status)
                body = response.json()
                self.assert_matches(body, "Error")
                self.assertEqual(body["error"]["code"], code)
                self.assertEqual(set(body["error"].get("fields", {})) or None, fields)

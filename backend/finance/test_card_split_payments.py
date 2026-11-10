"""카드값 분할 납부 · 사용자 확인 (finance/card_payments.py · api_card_payments.py · budgets/fixed_bills.py, 11/06)

합성 데이터만 쓴다. 청구 50만 원 = 30만 + 20만 같은 숫자는 예시다.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections, transaction
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from budgets.models import BudgetCycle
from users.models import Consent, FinancialProfile

from . import card_payments
from .api import listed_transactions
from .models import (BankAccount, Card, CardBill, Connection, FinancialTransaction as Tx, Loan, PaymentDue,
                     PaymentMatch)
from .transfer_rules import CARD_CANDIDATE, apply as apply_rules

# 실제 CODEF·LLM 호출 차단: 모듈을 불러올 때(테스트 DB 생성·시드 전) 시작한다
_blocks = [
    patch("finance.codef._request", side_effect=AssertionError("CODEF 호출 금지")),
    patch("chat.llm.openai_rest.ResponsesClient.__init__", side_effect=AssertionError("LLM 호출 금지")),
]
for _block in _blocks:
    _block.start()


def tearDownModule():
    for block in _blocks:
        block.stop()


TODAY = date(2026, 11, 12)
DUE = date(2026, 11, 5)
SHINHAN = "0306"


def url(bill, tx=None):
    return f"/api/finance/card-bills/{bill.pk}/payments/" + (f"{tx.pk}/" if tx else "")


class Base:
    def setUp(self):
        clock = patch("django.utils.timezone.localdate", return_value=TODAY)
        clock.start()
        self.addCleanup(clock.stop)
        self.user = self.make_user("split-pay@example.invalid")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def make_user(self, email):
        user = get_user_model().objects.create_user(email=email)
        FinancialProfile.objects.create(user=user, monthly_take_home_income=2_800_000, payday=25)
        Consent.objects.create(user=user, purpose="finance_inquiry", product_scope="card",
                               document_version="v1", document_hash="test")
        bank = Connection.objects.create(user=user, business_type="bank", organization_code="0004", status="connected")
        user.account = BankAccount.objects.create(user=user, connection=bank, source_account_key=f"{email}-pay",
                                                  account_type="demand")
        user.card_conn = Connection.objects.create(user=user, business_type="card", organization_code=SHINHAN,
                                                   status="connected")
        Card.objects.create(user=user, connection=user.card_conn, origin="provider", issuer_code=SHINHAN, is_selected=True)
        return user

    def bill(self, amount=500_000, due=DUE, key="b1", user=None, **fields):
        user = user or self.user
        return CardBill.objects.create(**{**dict(
            user=user, connection=user.card_conn, issuer_code=SHINHAN, statement_key=key, scope_type="issuer",
            billing_month=due.replace(day=1), total_amount=amount, withdrawal_due_date=due, source_type="provider",
            confirmation_state="confirmed", field_states={"codefBillType": ""},
        ), **fields})

    def pay(self, amount, day=DUE, desc="신한카드", user=None, candidate=True, **fields):
        """은행 출금. 기본은 새 규칙처럼 이체 + 카드값 후보 표시"""
        user = user or self.user
        values = {"transaction_type": "transfer", "confirmed_category_id": "UNCLASSIFIED",
                  "confirmation_state": "needs_review",
                  "field_states": {CARD_CANDIDATE: SHINHAN} if candidate else {}, **fields}
        return Tx.objects.create(user=user, source_kind="bank", account=user.account, occurred_on=day, amount=amount,
                                 direction="out", source_description=desc, **values)

    def match(self):
        with transaction.atomic():
            card_payments.match(self.user)

    def link(self, bill, *txs, **extra):
        return self.client.post(url(bill), {"transactionIds": [str(t.pk) for t in txs], **extra}, format="json")

    def state(self, bill, query=""):
        response = self.client.get(url(bill) + query)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def amounts(self, bill):
        b = self.state(bill)["bill"]
        return b["totalAmount"], b["paidAmount"], b["remainingAmount"], b["paymentState"]


class AutoMatchTest(Base, TestCase):
    def test_single_full_payment_is_auto_linked_and_shown_as_card_payment(self):
        bill, tx = self.bill(), self.pay(500_000)
        self.match()
        tx.refresh_from_db()
        self.assertEqual(tx.transaction_type, "card_payment")
        self.assertEqual(self.amounts(bill), (500_000, 500_000, 0, "paid"))
        self.assertEqual(self.state(bill)["linked"][0]["source"], "rule")

    def test_split_payment_is_not_auto_linked_by_sum(self):
        # 30만 + 20만 = 청구액이어도 합계만 보고 붙이지 않는다 (bill 먼저 · 출금 먼저 모두)
        for first in ("bill", "tx"):
            with self.subTest(first=first):
                PaymentMatch.objects.all().delete()
                PaymentDue.objects.all().delete()
                CardBill.objects.all().delete()
                Tx.objects.all().delete()
                bill = self.bill() if first == "bill" else None
                a, b = self.pay(300_000, date(2026, 11, 4)), self.pay(200_000)
                self.match()
                bill = bill or self.bill()
                self.match()
                self.assertFalse(PaymentMatch.objects.exists())
                self.assertEqual(self.link(bill, b, a).status_code, 200)
                self.assertEqual(self.amounts(bill), (500_000, 500_000, 0, "paid"))
                bill.refresh_from_db()
                self.assertEqual(bill.total_amount, 500_000)  # 청구액은 그대로

    def test_after_partial_the_exact_remaining_withdrawal_is_auto_linked(self):
        bill, first = self.bill(), self.pay(300_000, date(2026, 11, 3))
        self.assertEqual(self.link(bill, first).status_code, 200)
        self.assertEqual(self.amounts(bill), (500_000, 300_000, 200_000, "partial"))
        self.pay(150_000)  # 남은 금액과 다름 → 붙이지 않음
        rest = self.pay(200_000, date(2026, 11, 6))
        self.match()
        self.assertEqual(self.amounts(bill), (500_000, 500_000, 0, "paid"))
        self.assertTrue(PaymentMatch.objects.filter(transaction=rest).exists())
        self.assertEqual(PaymentMatch.objects.count(), 2)

    def test_ninety_percent_is_partial_with_remaining(self):
        bill = self.bill()
        self.link(bill, self.pay(450_000, candidate=False))
        self.assertEqual(self.amounts(bill), (500_000, 450_000, 50_000, "partial"))
        due = PaymentDue.objects.get(bill=bill)
        self.assertEqual((due.payment_state, due.paid_amount, due.confirmed_amount), ("partial", 450_000, 500_000))
        must_pay = self.client.get("/api/home/").json()["mustPay"]
        self.assertIn(50_000, [i["amount"] for i in must_pay if i["type"] == "card_bill"])

    def test_overpayment_is_kept_and_remaining_is_zero(self):
        bill = self.bill()
        self.link(bill, self.pay(300_000, candidate=False), self.pay(250_000, candidate=False))
        self.assertEqual(self.amounts(bill), (500_000, 550_000, 0, "paid"))
        self.assertEqual(sum(PaymentMatch.objects.values_list("matched_amount", flat=True)), 550_000)  # 자르지 않음

    def test_ambiguous_candidates_are_left_for_user(self):
        self.bill()
        self.bill(key="b2", due=date(2026, 11, 7), billing_month=date(2026, 11, 1))
        self.pay(500_000, date(2026, 11, 6))           # 두 청구서 모두 ±3일 · 같은 금액
        self.pay(500_000, desc="현대카드")               # 다른 카드사
        self.match()
        self.assertFalse(PaymentMatch.objects.exists())

    def test_card_name_in_ordinary_transfer_stays_visible(self):
        # R11: '신한카드 홍길동'처럼 카드사 이름이 든 송금은 청구서가 없으면 이체로 남아 목록에 보인다
        friend = self.pay(37_000, desc="신한카드 홍길동", candidate=False)
        payment = self.pay(500_000, candidate=False)
        self.bill()
        apply_rules(self.user, Tx.objects.filter(pk__in=[friend.pk, payment.pk]))
        self.match()
        friend.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual((friend.transaction_type, friend.field_states.get(CARD_CANDIDATE)), ("transfer", SHINHAN))
        self.assertIn(friend, listed_transactions(self.user))
        self.assertEqual(payment.transaction_type, "card_payment")
        self.assertNotIn(payment, listed_transactions(self.user))

    def test_user_confirmed_candidate_is_not_taken(self):
        bill = self.bill()
        self.pay(500_000, confirmation_state="confirmed")
        self.match()
        self.assertFalse(PaymentMatch.objects.exists())
        self.assertEqual(self.amounts(bill)[3], "needs_review")  # 출금일 +3일 지남, 안 냈다고 단정하지 않음


class UserLinkTest(Base, TestCase):
    def test_prepayment_outside_window_is_found_and_linked(self):
        bill, early = self.bill(), self.pay(200_000, date(2026, 10, 12), desc="신한카드 선결제")
        rest = self.pay(300_000)
        self.match()
        self.assertFalse(PaymentMatch.objects.exists())
        ids = [i["transactionId"] for i in self.state(bill)["items"]]
        self.assertIn(str(early.pk), ids)
        self.assertEqual(self.link(bill, early, rest).status_code, 200)
        self.assertEqual(self.amounts(bill), (500_000, 500_000, 0, "paid"))

    def test_unlink_restores_and_is_not_relinked_until_user_picks_again(self):
        bill, tx = self.bill(), self.pay(500_000)
        self.match()
        response = self.client.delete(url(bill, tx))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["released"]["classification"], "restored")
        tx.refresh_from_db()
        self.assertEqual(tx.transaction_type, "transfer")  # 다시 목록에 보인다
        self.assertIn(tx, listed_transactions(self.user))
        self.match()
        self.assertFalse(PaymentMatch.objects.exists())
        self.assertEqual(self.client.delete(url(bill, tx)).json()["released"], None)  # 재전송
        self.assertEqual(self.link(bill, tx).status_code, 200)
        self.assertEqual(self.amounts(bill)[3], "paid")

    def test_confirmed_classification_needs_explicit_confirm_and_is_restored(self):
        bill = self.bill()
        tx = self.pay(500_000, candidate=False, transaction_type="spending", confirmed_category_id="FOOD",
                      confirmation_state="confirmed", classification_source="user")
        response = self.link(bill, tx)
        self.assertEqual(response.status_code, 400)
        self.assertIn("confirmReclassify", response.json()["error"]["fields"])
        self.assertFalse(PaymentMatch.objects.exists())
        self.assertEqual(self.link(bill, tx, confirmReclassify=True).status_code, 200)
        tx.refresh_from_db()
        self.assertEqual(tx.transaction_type, "card_payment")
        self.client.delete(url(bill, tx))
        tx.refresh_from_db()
        self.assertEqual((tx.transaction_type, tx.confirmed_category_id, tx.confirmation_state),
                         ("spending", "FOOD", "confirmed"))

    def test_correction_moves_withdrawal_to_right_bill(self):
        wrong, right = self.bill(), self.bill(key="b2", due=date(2026, 10, 5), billing_month=date(2026, 10, 1))
        tx = self.pay(500_000, date(2026, 10, 6))
        self.link(wrong, tx)
        self.assertEqual(self.link(right, tx).status_code, 409)  # 다른 청구서에 붙은 출금은 먼저 풀어야
        self.client.delete(url(wrong, tx))
        self.assertEqual(self.link(right, tx).status_code, 200)
        self.assertEqual((self.amounts(wrong)[1], self.amounts(right)[1]), (0, 500_000))

    def test_permissions_and_protected_transactions(self):
        bill = self.bill()
        other = self.make_user("split-other@example.invalid")
        theirs_bill, theirs_tx = self.bill(user=other, key="o1"), self.pay(500_000, user=other)
        self.assertEqual(self.client.get(url(theirs_bill)).status_code, 404)
        self.assertEqual(self.link(theirs_bill, self.pay(1_000)).status_code, 404)
        loan = Loan.objects.create(user=self.user, origin="manual", status="repaying", lender_name="한국장학재단",
                                   monthly_payment=300_000, payment_day=10)
        loan_due = PaymentDue.objects.create(user=self.user, obligation_type="loan", loan=loan, occurrence_key="l1",
                                             due_date=DUE, confirmed_amount=300_000, source_type="manual",
                                             confirmation_state="confirmed")
        loan_tx = self.pay(300_000, candidate=False, transaction_type="loan_repayment")
        PaymentMatch.objects.create(user=self.user, due=loan_due, transaction=loan_tx, matched_amount=300_000)
        cases = {
            "남의 거래": (theirs_tx, 400),
            "취소된 거래": (self.pay(500_000, is_canceled=True), 400),
            "내 계좌 이체": (self.pay(500_000, candidate=False, transaction_type="own_transfer"), 400),
            "대출 납부에 연결됨": (loan_tx, 409),
        }
        for name, (tx, status) in cases.items():
            with self.subTest(name):
                self.assertEqual(self.link(bill, tx).status_code, status)
        self.assertEqual(PaymentMatch.objects.filter(due__bill=bill).count(), 0)
        self.assertEqual(self.client.post(url(bill), {"transactionIds": [], "x": 1}, format="json").status_code, 400)
        Consent.objects.filter(user=self.user).update(revoked_at="2026-11-01T00:00:00+09:00")
        self.assertEqual(self.client.get(url(bill)).status_code, 403)

    def test_repeat_and_idempotent_requests_do_not_double_count(self):
        bill, tx = self.bill(), self.pay(300_000, candidate=False)
        body = {"transactionIds": [str(tx.pk), str(tx.pk)]}
        for _ in range(2):
            self.assertEqual(self.client.post(url(bill), body, format="json", HTTP_IDEMPOTENCY_KEY="same-key").status_code, 200)
        self.assertEqual(self.link(bill, tx).status_code, 200)  # 새 요청 번호로 다시 보내도
        self.assertEqual(PaymentMatch.objects.count(), 1)
        self.assertEqual(self.amounts(bill), (500_000, 300_000, 200_000, "partial"))

    def test_bill_correction_and_canceled_withdrawal(self):
        bill, tx = self.bill(), self.pay(500_000)
        self.match()
        CardBill.objects.filter(pk=bill.pk).update(total_amount=520_000)  # 카드사 청구 정정
        with transaction.atomic():
            card_payments.sync_bill_dues(self.user, date(2026, 10, 25), TODAY)
        self.assertEqual(self.amounts(bill), (520_000, 500_000, 20_000, "partial"))
        Tx.objects.filter(pk=tx.pk).update(is_canceled=True)  # 출금 취소
        with transaction.atomic():
            card_payments.sync_bill_dues(self.user, date(2026, 10, 25), TODAY)
        self.assertEqual(self.amounts(bill), (520_000, 0, 520_000, "needs_review"))
        self.assertEqual(PaymentMatch.objects.count(), 1)  # 기록은 지우지 않음


class EdgeCaseTest(Base, TestCase):
    def test_bill_without_due_date_returns_due_date_review(self):
        # 청구액은 있지만 출금 · 결제 예정일이 없고 연결도 없음 → 500 이 아니라 '결제일 확인 필요'
        bill = self.bill(due=DUE, withdrawal_due_date=None, payment_due_date=None)
        body = self.state(bill)
        self.assertEqual((body["bill"]["dueDate"], body["bill"]["paymentState"], body["bill"]["reviewReason"],
                          body["bill"]["canLink"], body["bill"]["remainingAmount"]),
                         (None, "needs_review", "due_date_unknown", False, 500_000))
        self.assertNotIn("items", body)
        self.assertEqual(self.link(bill, self.pay(500_000)).status_code, 409)  # 결제일을 모르면 새 연결은 막고

    def test_bill_losing_due_date_still_shows_existing_links(self):
        bill, tx = self.bill(), self.pay(500_000)
        self.match()
        CardBill.objects.filter(pk=bill.pk).update(withdrawal_due_date=None, payment_due_date=None)
        body = self.state(bill)
        self.assertEqual((body["bill"]["paidAmount"], body["bill"]["paymentState"], body["bill"]["reviewReason"]),
                         (500_000, "paid", None))
        self.assertEqual(body["linked"][0]["hints"]["nearDueDate"], False)
        self.assertEqual(self.client.delete(url(bill, tx)).status_code, 200)  # 연결 내역 정정은 된다

    def two_statement_payment(self):
        """같은 날 명세서 두 장(30만 + 20만)을 출금 한 건(50만, 카드값 후보 이체)이 자동으로 낸 경우"""
        a = self.bill(300_000, key="use", field_states={"codefBillType": "이용대금"})
        b = self.bill(200_000, key="use2", field_states={"codefBillType": "사용대금"})
        tx = self.pay(500_000)
        self.match()
        tx.refresh_from_db()
        self.assertEqual((tx.transaction_type, PaymentMatch.objects.filter(transaction=tx).count()), ("card_payment", 2))
        return a, b, tx

    def release_in_order(self, first_is_a):
        a, b, tx = self.two_statement_payment()
        first, last = (a, b) if first_is_a else (b, a)
        self.assertEqual(self.state(first)["linked"][0]["cancelEffect"]["kind"], "keep")  # 다른 연결이 남아 있음
        released = self.client.delete(url(first, tx)).json()["released"]
        tx.refresh_from_db()
        self.assertEqual((released["classification"], tx.transaction_type), ("kept", "card_payment"))
        self.assertNotIn(tx, listed_transactions(self.user))      # 남은 납부가 있는 동안 카드값 그대로
        self.assertEqual(self.amounts(last)[3], "paid")
        self.assertEqual(self.state(last)["linked"][0]["cancelEffect"]["kind"], "restore")
        released = self.client.delete(url(last, tx)).json()["released"]
        tx.refresh_from_db()
        self.assertEqual((released["classification"], tx.transaction_type), ("restored", "transfer"))
        self.assertIn(tx, listed_transactions(self.user))
        self.assertFalse(PaymentMatch.objects.filter(transaction=tx).exists())
        self.match()  # 두 청구서 모두에서 푼 출금은 다시 붙지 않음
        self.assertFalse(PaymentMatch.objects.filter(transaction=tx).exists())

    def test_release_a_then_b_keeps_class_until_last(self):
        self.release_in_order(first_is_a=True)

    def test_release_b_then_a_keeps_class_until_last(self):
        self.release_in_order(first_is_a=False)


class FixedPreviewTest(Base, TestCase):
    """온보딩 고정지출 화면의 청구서 확인 (11/06 단순화): 거래를 고르지 않고 '앞으로 낼 금액'을 답한다"""
    def setUp(self):
        super().setUp()
        BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25))

    def row(self):
        response = self.client.get("/api/budgets/fixed/")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["cardBills"][0]

    def bill_row(self):
        return self.row()["bills"][0]

    def answer(self, bill, answer, **extra):
        return self.client.put(f"/api/finance/card-bills/{bill.pk}/payment-check/", {"answer": answer, **extra}, format="json")

    def test_answers_without_picking_transactions(self):
        bill = self.bill()
        b = self.bill_row()
        self.assertEqual((b["status"], b["remainingAmount"], b["needsCheck"], b["billingMonth"], b["totalAmount"]),
                         ("unchecked", None, True, "2026-11", 500_000))   # 결제일 지남 · 모름 → 0원도 미납액도 아님
        for answer, extra, status, left in (("unpaid", {}, "user_confirmed", 500_000), ("paid", {}, "user_confirmed", 0),
                                            ("partial", {"remainingAmount": 200_000}, "user_confirmed", 200_000),
                                            ("later", {}, "unchecked", None)):
            with self.subTest(answer=answer):
                response = self.answer(bill, answer, **extra)
                self.assertEqual(response.status_code, 200, response.content)
                b = self.bill_row()  # 새로고침해도 유지
                self.assertEqual((b["status"], b["remainingAmount"], b["userCheck"]["answer"]), (status, left, answer))
        self.assertFalse(PaymentMatch.objects.exists())      # 가짜 납부 기록을 만들지 않음
        self.assertFalse(Tx.objects.exists())
        bill.refresh_from_db()
        self.assertEqual(bill.total_amount, 500_000)          # 청구 총액은 그대로

    def test_zero_won_and_auto_paid_need_no_question(self):
        zero = self.bill(0, key="zero", due=date(2026, 11, 20), billing_month=date(2026, 11, 1))
        self.assertEqual(self.answer(zero, "paid").status_code, 409)
        CardBill.objects.all().delete()
        bill = self.bill()
        self.pay(500_000)
        self.match()
        b = self.bill_row()
        self.assertEqual((b["status"], b["remainingAmount"], b["needsCheck"]), ("paid_by_transactions", 0, False))

    def test_user_check_and_withdrawal_are_not_added_together(self):
        bill = self.bill()
        self.link(bill, self.pay(300_000, candidate=False))
        self.answer(bill, "partial", remainingAmount=200_000)
        b = self.bill_row()
        self.assertEqual((b["status"], b["remainingAmount"], b["transactionPaidAmount"]), ("user_confirmed", 200_000, 300_000))
        self.answer(bill, "unpaid")     # 출금 30만 원이 있는데 '안 냈어요' → 조용히 덮지 않고 재확인
        b = self.bill_row()
        self.assertEqual((b["status"], b["recheckReason"], b["remainingAmount"]), ("recheck", "transactions_conflict", None))

    def test_resync_keeps_check_and_correction_asks_again(self):
        from .models import SyncRun
        from .sync_cards import _save_bills
        bill = self.bill()
        self.answer(bill, "paid")
        def resync(total):
            run = SyncRun.objects.create(user=self.user, connection=self.user.card_conn, product_code="card_sync", status="running")
            _save_bills(self.user, self.user.card_conn, SHINHAN, [{
                "statement_key": "b1", "bill_type": "", "billing_month": date(2026, 11, 1), "total_amount": total,
                "payment_due_date": None, "withdrawal_due_date": DUE, "items": None, "items_state": "unavailable"}], run, None)
        resync(500_000)
        self.assertEqual(self.bill_row()["status"], "user_confirmed")    # 재조회만으로 지우지 않음
        resync(520_000)
        b = self.bill_row()
        self.assertEqual((b["status"], b["recheckReason"], b["userCheck"]["answer"]), ("recheck", "bill_changed", "paid"))
        self.answer(bill, "paid")
        self.assertEqual(self.bill_row()["status"], "user_confirmed")

    def test_validation_cancel_and_other_records(self):
        from budgets.models import Budget
        bill = self.bill()
        other_issuer = self.bill(key="kb", issuer_code="0301")
        self.answer(other_issuer, "paid")
        cycle = BudgetCycle.objects.get(user=self.user)
        approved = Budget.objects.create(user=self.user, cycle=cycle, version_no=1, status="approved", input_snapshot={"cardReserved": 1})
        for body, field in (({"answer": "partial"}, "remainingAmount"), ({"answer": "partial", "remainingAmount": 500_000}, "remainingAmount"),
                            ({"answer": "partial", "remainingAmount": True}, "remainingAmount"),
                            ({"answer": "paid", "remainingAmount": 0}, "remainingAmount"), ({"answer": "maybe"}, "answer"),
                            ({"answer": "paid", "x": 1}, "x")):
            with self.subTest(body=body):
                response = self.client.put(f"/api/finance/card-bills/{bill.pk}/payment-check/", body, format="json")
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(field, response.json()["error"]["fields"])
        theirs = self.bill(user=self.make_user("check-other@example.invalid"), key="o1")
        self.assertEqual(self.answer(theirs, "paid").status_code, 404)
        self.answer(bill, "paid")
        response = self.client.delete(f"/api/finance/card-bills/{bill.pk}/payment-check/")
        self.assertEqual((response.status_code, response.json()["status"], response.json()["userCheck"]), (200, "unchecked", None))
        other_issuer.refresh_from_db()
        self.assertEqual(other_issuer.field_states["userPaymentCheck"]["answer"], "paid")   # 다른 카드사 확인은 그대로
        approved.refresh_from_db()
        self.assertEqual((approved.status, approved.input_snapshot), ("approved", {"cardReserved": 1}))

    def test_home_uses_user_check_without_calling_it_unpaid(self):
        bill = self.bill(due=date(2026, 11, 20), billing_month=date(2026, 11, 1))
        cards = lambda: [i for i in self.client.get("/api/home/").json()["mustPay"] if i["type"] == "card_bill"]
        self.assertEqual([(i["amount"], i["userChecked"]) for i in cards()], [(500_000, False)])
        self.answer(bill, "partial", remainingAmount=120_000)
        self.assertEqual([(i["amount"], i["userChecked"]) for i in cards()], [(120_000, True)])
        self.answer(bill, "paid")
        self.assertEqual(cards(), [])
        detail = self.client.get(f"/api/finance/card-bills/{bill.pk}/").json()
        self.assertEqual((detail["paymentCheck"]["status"], detail["paymentCheck"]["remainingAmount"]), ("user_confirmed", 0))


class ConcurrencyTest(Base, TransactionTestCase):
    serialized_rollback = True

    def post(self, args):
        barrier, bill, tx = args
        close_old_connections()
        try:
            client = APIClient()
            client.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
            barrier.wait(5)
            return client.post(url(bill), {"transactionIds": [str(tx.pk)]}, format="json").status_code
        finally:
            connections.close_all()

    def test_same_withdrawal_to_two_bills_only_one_wins(self):
        first, second = self.bill(), self.bill(key="b2", due=date(2026, 10, 5), billing_month=date(2026, 10, 1))
        tx = self.pay(300_000, candidate=False)
        barrier = Barrier(2)
        with ThreadPoolExecutor(2) as pool:
            codes = sorted(pool.map(self.post, [(barrier, first, tx), (barrier, second, tx)]))
        self.assertEqual(codes, [200, 409])
        self.assertEqual(PaymentMatch.objects.filter(transaction=tx).count(), 1)

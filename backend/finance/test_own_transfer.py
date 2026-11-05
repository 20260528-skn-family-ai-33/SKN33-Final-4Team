"""R11 내 계좌 이체 확인 API (PATCH /api/finance/transactions/<id>/ {"ownTransfer": true | false}).
실행: backend 폴더에서 python manage.py test finance.test_own_transfer"""
import json

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase

from budgets.candidates import is_saving_transfer
from budgets.saving_match import match_savings

from . import pipeline
from .models import (BankAccount, Connection, FinancialTransaction as Tx, PaymentDue, PaymentMatch, RecurringExpense,
                     RecurringSaving, TransactionLink)
from .test_concurrency import run_together
from .tests import URL, FinanceData
from .transfer_rules import OWN_REVIEW


class OwnTransferApiTest(FinanceData):
    def setUp(self):
        self.client.force_login(self.user)

    def out(self, review="suggested", **fields):
        return self.tx(
            transaction_type="transfer", confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review",
            source_description="김나은", occurred_on=self.today, field_states={OWN_REVIEW: review} if review else {}, **fields,
        )

    def patch(self, tx, body):
        return self.client.patch(f"{URL}{tx.pk}/", data=json.dumps(body), content_type="application/json")

    def state(self, tx):
        tx.refresh_from_db()
        return tx.transaction_type, tx.field_states.get(OWN_REVIEW)

    def listed(self):
        return {i["transactionId"]: i for i in self.client.get(URL).json()["items"]}

    def test_suggestion_is_shown_in_list_and_detail(self):
        tx = self.out()
        self.assertEqual(self.listed()[str(tx.pk)]["ownTransferReview"], "suggested")
        body = self.client.get(f"{URL}{tx.pk}/").json()
        self.assertEqual((body["ownTransferReview"], body["transactionType"]), ("suggested", "transfer"))
        self.assertNotIn("fieldStates", body)

    def test_confirm_changes_only_this_withdrawal_and_stays_findable(self):
        tx = self.out()
        other_income = self.tx(direction="in", transaction_type="income", confirmed_category_id="UNCLASSIFIED",
                               confirmation_state="needs_review", amount=10000, occurred_on=self.today)
        res = self.patch(tx, {"ownTransfer": True})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()["transactionType"], res.json()["ownTransferReview"]), ("own_transfer", "confirmed"))
        self.assertEqual(self.state(other_income), ("income", None))  # 같은 금액 입금으로 넓히지 않음
        self.assertFalse(TransactionLink.objects.exists())
        rows = self.client.get(URL).json()
        self.assertIn(str(tx.pk), {i["transactionId"] for i in rows["items"]})  # 다시 찾아 되돌릴 수 있음
        self.assertEqual(rows["summary"]["spentTotal"], 0)
        self.assertEqual(self.patch(tx, {"ownTransfer": True}).status_code, 200)  # 같은 요청 두 번 = 성공
        self.assertEqual(self.patch(tx, {"category": "FOOD"}).status_code, 400)  # 확인 취소가 먼저

    def test_reject_keeps_transfer_and_not_spending_and_can_change_mind(self):
        tx = self.out()
        res = self.patch(tx, {"ownTransfer": False})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.state(tx), ("transfer", "rejected"))
        self.assertEqual(self.client.get(URL).json()["summary"]["spentTotal"], 0)
        self.assertEqual(self.patch(tx, {"ownTransfer": False}).status_code, 200)
        self.assertEqual(self.patch(tx, {"ownTransfer": True}).status_code, 200)
        self.assertEqual(self.state(tx), ("own_transfer", "confirmed"))
        self.assertEqual(self.patch(tx, {"ownTransfer": False}).status_code, 200)  # 확인 취소
        self.assertEqual(self.state(tx), ("transfer", "rejected"))
        res = self.patch(tx, {"category": "FOOD"})  # 기존 [소비로 분류하기]는 그대로
        self.assertEqual((res.status_code, res.json()["transactionType"]), (200, "spending"))

    def test_decision_survives_pipeline_rerun(self):
        get_user_model().objects.filter(pk=self.user.pk).update(nickname="김나은")
        confirmed, rejected = self.out(), self.out()
        self.patch(confirmed, {"ownTransfer": True})
        self.patch(rejected, {"ownTransfer": False})
        pipeline.process_new_transactions(self.user, [confirmed.pk, rejected.pk])
        self.assertEqual((self.state(confirmed), self.state(rejected)),
                         (("own_transfer", "confirmed"), ("transfer", "rejected")))

    def test_auto_pair_can_be_confirmed_or_unlinked(self):
        conn = Connection.objects.create(user=self.user, business_type="bank", organization_code="0088", status="connected")
        other = BankAccount.objects.create(user=self.user, connection=conn, source_account_key="acc-2", account_type="demand")
        tx = self.out(review="auto")
        Tx.objects.filter(pk=tx.pk).update(transaction_type="own_transfer")
        income = self.tx(account=other, direction="in", transaction_type="own_transfer", confirmed_category_id="UNCLASSIFIED",
                         confirmation_state="needs_review", occurred_on=self.today)
        TransactionLink.objects.create(user=self.user, from_transaction=tx, to_transaction=income, relation_type="OWN_TRANSFER",
                                       matched_amount=10000, confirmation_state="suggested")
        listed = self.listed()
        self.assertEqual(listed[str(tx.pk)]["ownTransferReview"], "auto")
        self.assertNotIn(str(income.pk), listed)  # 한 번의 이체는 출금 한 줄로
        self.assertEqual(self.patch(tx, {"ownTransfer": True}).status_code, 200)
        self.assertEqual(self.state(tx), ("own_transfer", "confirmed"))
        self.assertTrue(TransactionLink.objects.exists())
        res = self.patch(tx, {"ownTransfer": False})  # 연결 해제
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((self.state(tx), self.state(income)), (("transfer", "rejected"), ("income", "rejected")))
        self.assertFalse(TransactionLink.objects.exists())

    def test_stale_own_transfer_request_does_not_override_user_spending(self):
        # 재현: 추정 거래 → category=FOOD → ownTransfer=true 가 200 으로 소비를 own_transfer 로 바꾸던 문제
        for review in ("suggested", "rejected", "confirmed", "auto"):
            with self.subTest(review=review):
                tx = self.out(review=review)
                Tx.objects.filter(pk=tx.pk).update(
                    transaction_type="spending", confirmed_category_id="FOOD", confirmation_state="confirmed",
                    classification_source="user", field_states={OWN_REVIEW: review, "original_transaction_type": "transfer"},
                )
                for value in (True, False):
                    res = self.patch(tx, {"ownTransfer": value})
                    self.assertEqual(res.status_code, 400, res.content)
                tx.refresh_from_db()
                self.assertEqual((tx.transaction_type, tx.confirmed_category_id, tx.confirmation_state),
                                 ("spending", "FOOD", "confirmed"))
        tx = self.out()
        self.assertEqual(self.patch(tx, {"category": "FOOD"}).status_code, 200)  # 실제 API 순서로도 같음
        self.assertEqual(self.patch(tx, {"ownTransfer": True}).status_code, 400)
        self.assertEqual(self.patch(tx, {"ownTransfer": False}).status_code, 400)
        self.assertEqual(self.state(tx), ("spending", "suggested"))

    def saving_pair(self, review="auto"):
        conn = Connection.objects.create(user=self.user, business_type="bank", organization_code="0088", status="connected")
        other = BankAccount.objects.create(user=self.user, connection=conn, source_account_key="acc-s", account_type="demand")
        tx = self.out(review=review)
        Tx.objects.filter(pk=tx.pk).update(transaction_type="saving")
        income = self.tx(account=other, direction="in", transaction_type="own_transfer", confirmed_category_id="UNCLASSIFIED",
                         confirmation_state="needs_review", occurred_on=self.today, field_states={OWN_REVIEW: review})
        TransactionLink.objects.create(user=self.user, from_transaction=tx, to_transaction=income, relation_type="OWN_TRANSFER",
                                       matched_amount=10000, confirmation_state="suggested")
        saving = RecurringSaving.objects.create(
            user=self.user, name="적금", amount=10000, status="confirmed", source_type="detected", transfer_day=self.today.day,
            from_account=self.account, to_account=other, evidence=["11111111-1111-1111-1111-111111111111", str(tx.pk).upper()],
        )
        return tx, income, saving

    def test_auto_judgement_turned_saving_stays_findable_and_correctable(self):
        tx, income, saving = self.saving_pair()
        item = self.listed()[str(tx.pk)]
        self.assertEqual((item["transactionType"], item["ownTransferReview"]), ("saving", "auto"))
        self.assertEqual(self.client.get(URL).json()["summary"]["spentTotal"], 0)
        self.assertEqual(self.patch(tx, {"category": "FOOD"}).status_code, 400)
        res = self.patch(tx, {"ownTransfer": True})  # [맞아요]: 저축 그대로 확인만
        self.assertEqual((res.status_code, res.json()["transactionType"], res.json()["ownTransferReview"]),
                         (200, "saving", "confirmed"))
        res = self.patch(tx, {"ownTransfer": False})  # 정정: 저축 근거에서 빼고 일반 이체로
        self.assertEqual(res.status_code, 200, res.content)
        saving.refresh_from_db()
        self.assertEqual((saving.status, saving.evidence), ("confirmed", ["11111111-1111-1111-1111-111111111111"]))
        self.assertEqual((self.state(tx), self.state(income)), (("transfer", "rejected"), ("income", "rejected")))
        self.assertFalse(TransactionLink.objects.exists())
        match_savings(self.user, [tx.pk], self.today)  # 재처리해도 저축으로 다시 넣지 않음
        self.assertEqual(self.state(tx), ("transfer", "rejected"))

    def test_saving_correction_is_blocked_while_used_by_fixed_expense(self):
        tx, _, saving = self.saving_pair()
        RecurringExpense.objects.create(user=self.user, name="x", amount=10000, status="suggested", source_type="detected",
                                        category_id="ETC", evidence=[str(tx.pk)])
        res = self.patch(tx, {"ownTransfer": False})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"]["fields"]["ownTransfer"], ["고정지출 근거로 쓰인 거래예요. 고정지출 화면에서 먼저 정리해 주세요."])
        saving.refresh_from_db()
        self.assertEqual(len(saving.evidence), 2)
        self.assertEqual(self.state(tx), ("saving", "auto"))

    def test_rejects_invalid_or_protected_requests(self):
        plain = self.out(review=None)
        income = self.tx(direction="in", transaction_type="income", confirmed_category_id="UNCLASSIFIED",
                         confirmation_state="needs_review", field_states={OWN_REVIEW: "suggested"}, occurred_on=self.today)
        canceled = self.out(is_canceled=True)
        paid = self.out()
        due = PaymentDue.objects.create(user=self.user, obligation_type="manual", occurrence_key="k", due_date=self.today,
                                        confirmed_amount=10000, source_type="manual", confirmation_state="confirmed")
        PaymentMatch.objects.create(user=self.user, due=due, transaction=paid, matched_amount=10000)
        for tx in (plain, income, canceled, paid):
            with self.subTest(tx=tx.field_states):
                res = self.patch(tx, {"ownTransfer": True})
                self.assertEqual(res.status_code, 400, res.content)
                self.assertIn("ownTransfer", res.json()["error"]["fields"])
        self.assertEqual(self.state(paid), ("transfer", "suggested"))
        tx = self.out()
        self.assertEqual(self.patch(tx, {"ownTransfer": "yes"}).status_code, 400)
        self.assertEqual(self.patch(tx, {"ownTransfer": True, "category": "FOOD"}).status_code, 400)
        theirs = self.tx(user=self.other, transaction_type="transfer", confirmed_category_id="UNCLASSIFIED",
                         confirmation_state="needs_review", field_states={OWN_REVIEW: "suggested"})
        self.assertEqual(self.patch(theirs, {"ownTransfer": True}).status_code, 404)
        self.assertEqual(self.state(tx), ("transfer", "suggested"))

    def test_rejected_name_is_not_reused_as_saving_basis(self):
        suggested, rejected = self.out(), self.out(review="rejected")
        self.assertTrue(is_saving_transfer(suggested, "김나은"))
        self.assertFalse(is_saving_transfer(rejected, "김나은"))
        Tx.objects.filter(pk=rejected.pk).update(source_description="김나은 적금")
        rejected.refresh_from_db()
        self.assertTrue(is_saving_transfer(rejected, "김나은"))  # 저축 단어 근거는 그대로


class OwnTransferConcurrencyTest(TransactionTestCase):
    serialized_rollback = True  # 카테고리 시드 되살리기 (test_concurrency 와 같음)

    def test_confirm_and_reject_at_once_leave_a_consistent_state(self):
        from django.test import Client
        user = get_user_model().objects.create_user(email="own@example.invalid", password="Example123!")
        conn = Connection.objects.create(user=user, business_type="bank", organization_code="0004", status="connected")
        account = BankAccount.objects.create(user=user, connection=conn, source_account_key="a", account_type="demand")
        tx = Tx.objects.create(
            user=user, source_kind="bank", account=account, occurred_on="2026-11-03", amount=10000, direction="out",
            transaction_type="transfer", confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review",
            source_description="김나은", field_states={OWN_REVIEW: "suggested"},
        )

        def send(value):
            client = Client()
            client.force_login(user)
            return client.patch(f"{URL}{tx.pk}/", data=json.dumps({"ownTransfer": value}),
                                content_type="application/json").status_code

        self.assertEqual(run_together(lambda: send(True), lambda: send(False)), [200, 200])
        tx.refresh_from_db()
        self.assertIn((tx.transaction_type, tx.field_states[OWN_REVIEW], tx.confirmation_state),
                      [("own_transfer", "confirmed", "confirmed"), ("transfer", "rejected", "needs_review")])

        # 사용자 분류와 이체 확인이 동시에 와도 한쪽만 이긴다 (소비가 own_transfer 로 덮이지 않음)
        Tx.objects.filter(pk=tx.pk).update(transaction_type="transfer", confirmation_state="needs_review",
                                           field_states={OWN_REVIEW: "suggested"})

        def categorize():
            client = Client()
            client.force_login(user)
            return client.patch(f"{URL}{tx.pk}/", data=json.dumps({"category": "FOOD"}),
                                content_type="application/json").status_code

        codes = run_together(categorize, lambda: send(True))
        tx.refresh_from_db()
        if tx.transaction_type == "spending":
            self.assertEqual((codes, tx.confirmed_category_id), ([200, 400], "FOOD"))
        else:
            self.assertEqual((codes, tx.transaction_type), ([400, 200], "own_transfer"))

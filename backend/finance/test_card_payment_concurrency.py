"""홈/새 거래 처리의 자동 납부 연결과 기존 거래 분류 PATCH의 실제 PostgreSQL 경합."""
import threading
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from users.models import FinancialProfile
from . import card_payments, pipeline
from .models import BankAccount, CardBill, Connection, FinancialTransaction as Tx, PaymentMatch
from .transfer_rules import CARD_CANDIDATE


class CardPaymentClassificationRaceTest(TransactionTestCase):
    serialized_rollback = True

    def setUp(self):
        self.user = get_user_model().objects.create_user(email="card-race@example.invalid")
        FinancialProfile.objects.create(user=self.user, payday=25, monthly_take_home_income=2500000)
        bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        self.account = BankAccount.objects.create(user=self.user, connection=bank, source_account_key="race", account_type="demand")
        self.tx = Tx.objects.create(user=self.user, account=self.account, source_kind="bank", amount=420000,
                                    occurred_on=date(2026, 11, 5), direction="out", source_description="신한카드",
                                    transaction_type="transfer", confirmed_category_id="UNCLASSIFIED",
                                    confirmation_state="needs_review", field_states={CARD_CANDIDATE: "0306"})
        CardBill.objects.create(user=self.user, issuer_code="0306", statement_key="race", scope_type="issuer",
                                billing_month=date(2026, 11, 1), total_amount=420000,
                                withdrawal_due_date=date(2026, 11, 5), source_type="provider", field_states={"codefBillType": ""})

    def api_client(self):
        client = APIClient()
        client.force_authenticate(self.user)
        return client

    def race(self, job):
        read, resume = threading.Event(), threading.Event()
        errors, codes = [], []
        find = card_payments.find_statements

        def stop_after_read(user, tx):
            shares = find(user, tx)
            if tx.pk == self.tx.pk and not read.is_set():
                read.set()
                if not resume.wait(10):
                    raise AssertionError("분류 PATCH 완료를 기다리다 시간 초과")
            return shares

        def worker():
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                job(codes)
            except BaseException as exc:
                errors.append(exc)
            finally:
                connection.close()

        with patch("django.utils.timezone.localdate", return_value=date(2026, 11, 6)), \
                patch.object(card_payments, "find_statements", side_effect=stop_after_read):
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(read.wait(10), "자동 연결이 기존 후보를 읽지 못함")
                response = self.api_client().patch(f"/api/finance/transactions/{self.tx.pk}/", {"category": "FOOD"}, format="json")
                self.assertEqual(response.status_code, 200)
            finally:
                resume.set()
                thread.join(15)
            self.assertFalse(thread.is_alive(), "잠금 교착/요청 미완료")
        if errors:
            raise errors[0]
        self.tx.refresh_from_db()
        self.assertEqual((self.tx.transaction_type, self.tx.confirmed_category_id, self.tx.confirmation_state),
                         ("spending", "FOOD", "confirmed"))
        self.assertNotIn(card_payments.CARD_PAYMENT, self.tx.field_states)
        self.assertFalse(PaymentMatch.objects.filter(transaction=self.tx).exists())
        return codes

    def test_home_get_does_not_overwrite_successful_category_patch(self):
        codes = self.race(lambda codes: codes.append(self.api_client().get("/api/home/").status_code))
        self.assertEqual(codes, [200])

    def test_new_transaction_processing_does_not_overwrite_existing_candidate_patch(self):
        new = Tx.objects.create(user=self.user, source_kind="manual", amount=1000, occurred_on=date(2026, 11, 6),
                                direction="out", transaction_type="spending", confirmed_category_id="FOOD", confirmation_state="confirmed")
        # 새 거래만 잠근 파이프라인이 기존 후보도 찾는 실제 경로. 외부 분류/다른 납부 단계는 이 경합과 무관하다.
        with patch.object(pipeline, "AFTER_SAVE_STEPS", [("match_card_payments", pipeline.match_card_payments)]):
            self.race(lambda codes: pipeline.process_new_transactions(self.user, [new.pk]))
        new.refresh_from_db()
        self.assertNotIn(pipeline.RETRY_STATE_KEY, new.field_states)

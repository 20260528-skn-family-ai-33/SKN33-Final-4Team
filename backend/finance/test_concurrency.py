"""동시 요청 · 입력 타입 회귀 테스트 (PR #41 리뷰). 실행: backend 폴더에서 python manage.py test finance

TransactionTestCase를 쓴다: 각 요청이 실제로 커밋되어야 다른 스레드(다른 DB 연결)에서 보이고,
행 잠금(select_for_update)이 PostgreSQL에서 실제로 동작하는지 확인할 수 있다.
"""
import json
import threading
import time
import uuid

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import Client, TestCase, TransactionTestCase

from users.models import Consent

from .models import BankAccount, Connection, FinancialTransaction

CONSENT_URL = "/api/finance/consents/"
BANK_ONLY = {"consents": [{"purpose": "finance_inquiry", "productScope": "bank", "documentVersion": "v1"}]}


def run_together(*jobs):
    """여러 함수를 스레드로 거의 동시에 실행하고 결과를 순서대로 돌려준다 (스레드마다 DB 연결을 닫는다)."""
    barrier = threading.Barrier(len(jobs))
    results = [None] * len(jobs)
    errors = []

    def worker(index, job):
        try:
            barrier.wait(timeout=10)
            results[index] = job()
        except Exception as exc:  # 스레드 예외를 테스트 실패로 넘긴다
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(i, job)) for i, job in enumerate(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    if errors:
        raise errors[0]
    return results


class ConcurrencyBase(TransactionTestCase):
    # TransactionTestCase는 테스트마다 표를 비우므로, 마이그레이션이 넣은 카테고리 초기 데이터를 되살린다
    serialized_rollback = True

    def setUp(self):
        self.user = get_user_model().objects.create_user(email="sejin@example.com", password="Example123!")
        conn = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        self.account = BankAccount.objects.create(
            user=self.user, connection=conn, source_account_key="acc-1", masked_number="****5678", account_type="demand",
        )

    def client_for_user(self):
        client = Client()
        client.force_login(self.user)
        return client


class TransactionPatchConcurrencyTest(ConcurrencyBase):
    """18. 소비로 바꾼 이체에 '카테고리 변경'과 '이체로 되돌리기'가 동시에 와도 섞인 상태가 남지 않아야 한다"""

    def make_converted_transfer(self):
        return FinancialTransaction.objects.create(
            user=self.user, source_kind="bank", account=self.account, occurred_on="2026-10-20", amount=30000,
            direction="out", transaction_type="spending", confirmed_category_id="FOOD", confirmation_state="confirmed",
            classification_source="user", field_states={"original_transaction_type": "transfer"},
            source_description="이하늘",
        )

    def patch(self, tx, body):
        def job():
            res = self.client_for_user().patch(f"/api/finance/transactions/{tx.transaction_id}/",
                                               data=json.dumps(body), content_type="application/json")
            return res.status_code
        return job

    def test_change_and_revert_at_once_leave_consistent_state(self):
        for _ in range(5):  # 경합을 여러 번 만들어 본다
            tx = self.make_converted_transfer()
            codes = run_together(self.patch(tx, {"category": "HOUSEHOLD"}), self.patch(tx, {"category": None}))
            tx.refresh_from_db()
            state = (tx.transaction_type, tx.confirmed_category_id, tx.confirmation_state)
            # 차례로 처리됐다면 둘 중 하나만 가능하다
            self.assertIn(state, {
                ("transfer", "UNCLASSIFIED", "needs_review"),   # 변경 → 되돌리기 순서
                ("spending", "HOUSEHOLD", "confirmed"),         # 되돌리기 → 변경(이체를 다시 소비로) 순서
            }, f"섞인 상태: {state}, 응답 {codes}")
            if tx.transaction_type == "spending":
                self.assertEqual(tx.field_states.get("original_transaction_type"), "transfer")

    def test_patch_waits_for_row_lock(self):
        tx = self.make_converted_transfer()
        locked, release, done = threading.Event(), threading.Event(), threading.Event()

        def hold_lock():
            with transaction.atomic():
                list(FinancialTransaction.objects.select_for_update().filter(pk=tx.pk).values_list("pk", flat=True))
                locked.set()
                release.wait(timeout=10)
            connection.close()

        def send_patch():
            status = self.patch(tx, {"category": "HOUSEHOLD"})()
            done.set()
            connection.close()
            return status

        holder = threading.Thread(target=hold_lock)
        holder.start()
        self.assertTrue(locked.wait(timeout=10))
        sender = threading.Thread(target=send_patch)
        sender.start()
        time.sleep(0.5)
        self.assertFalse(done.is_set(), "다른 트랜잭션이 잠근 동안 PATCH가 기다리지 않았습니다")
        release.set()
        holder.join(timeout=10)
        sender.join(timeout=10)
        self.assertTrue(done.is_set())
        tx.refresh_from_db()
        self.assertEqual(tx.confirmed_category_id, "HOUSEHOLD")


class ConsentConcurrencyTest(ConcurrencyBase):
    """6. 요청 번호가 다른 같은 동의가 동시에 와도 유효한 동의는 한 건만 남아야 한다"""

    def post(self):
        def job():
            res = self.client_for_user().post(CONSENT_URL, data=json.dumps(BANK_ONLY), content_type="application/json",
                                              HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex)
            return res.status_code, res.json()["items"][0]["consentId"]
        return job

    def test_same_consent_at_once_creates_one(self):
        results = run_together(self.post(), self.post(), self.post())
        self.assertEqual({status for status, _ in results}, {201})
        self.assertEqual(len({consent_id for _, consent_id in results}), 1, results)
        self.assertEqual(Consent.objects.filter(user=self.user, product_scope="bank", revoked_at__isnull=True).count(), 1)


class ConsentTypeTest(TestCase):
    """6. productScope · documentVersion · purpose에 글자가 아닌 값이 오면 500이 아니라 400"""

    def test_non_string_values_are_400(self):
        user = get_user_model().objects.create_user(email="sejin@example.com", password="Example123!")
        self.client.force_login(user)
        base = {"purpose": "finance_inquiry", "productScope": "bank", "documentVersion": "v1"}
        for field in ("productScope", "documentVersion", "purpose"):
            for bad in ([], {}, 1, None):
                with self.subTest(field=field, value=bad):
                    row = {**base, field: bad}
                    res = self.client.post(CONSENT_URL, data=json.dumps({"consents": [row]}), content_type="application/json")
                    self.assertEqual(res.status_code, 400)
                    body = res.json()["error"]
                    self.assertEqual(body["code"], "invalid")
                    self.assertIn(field, body["fields"])
        self.assertFalse(Consent.objects.exists())

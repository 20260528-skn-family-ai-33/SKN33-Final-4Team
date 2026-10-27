"""조회 시작 순서와 저장 완료 순서가 다른 실제 PostgreSQL 회귀 검사."""
import hashlib
import threading
import uuid
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from users.models import Consent

from . import codef
from .models import Connection, Loan, SyncRun
from .sync_loans import sync_bank_loans
from .test_loan_sync import END, NUMBER, START, account, detail


@skipUnless(connection.vendor == "postgresql", "실제 행 잠금은 PostgreSQL에서 검사")
class LoanSyncConcurrencyTest(TransactionTestCase):
    # 기존 금융 동시성 테스트와 같은 방식으로 마이그레이션 초기 데이터를 복원한다.
    serialized_rollback = True

    def setUp(self):
        self.user = get_user_model().objects.create_user(email=f"loan-race-{uuid.uuid4().hex}@example.invalid")
        self.bank = Connection.objects.create(
            user=self.user, business_type="bank", organization_code="0004", status="connected",
        )
        Consent.objects.create(user=self.user, purpose="finance_inquiry", product_scope="loan",
                               document_version="test", document_hash="test-only")

    def seed_loan(self):
        loan = Loan.objects.create(
            user=self.user, connection=self.bank, origin="provider", reported_balance=50000,
            source_loan_key=hashlib.sha256(NUMBER.encode()).hexdigest(),
            fetched_at=timezone.now() - timedelta(days=1),
        )
        SyncRun.objects.create(
            user=self.user, connection=self.bank, loan=loan, product_code="loan_transaction_list",
            status="success", started_at=timezone.now() - timedelta(days=1), completed_at=loan.fetched_at,
        )

    def race(self, first_to_finish):
        entered = {name: threading.Event() for name in ("A", "B")}
        release = {name: threading.Event() for name in ("A", "B")}
        results, errors = {}, {}

        def request(path, params):
            if path == codef.PATH_ACCOUNT_LIST:
                return {"resLoan": [account()]}
            name = params["connectedId"]
            entered[name].set()
            if not release[name].wait(timeout=15):
                raise TimeoutError("조회 완료 신호 대기 시간 초과")
            return {**detail(), "resLoanBalance": "100000" if name == "A" else "200000"}

        def worker(name):
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '10s'")
                    cursor.execute("SET lock_timeout = '8s'")
                results[name] = sync_bank_loans(self.user, name, "0004", START, END)
            except Exception as exc:
                errors[name] = type(exc).__name__
            finally:
                connection.close()

        threads = {name: threading.Thread(target=worker, args=(name,)) for name in ("A", "B")}
        started = []
        with patch("finance.codef._request", side_effect=request):
            try:
                for name in ("A", "B"):
                    threads[name].start()
                    started.append(threads[name])
                    self.assertTrue(entered[name].wait(timeout=10), errors)
                # 두 요청 모두 시작한 상태에서 지정한 순서로 저장까지 끝낸다.
                for name in (first_to_finish, "B" if first_to_finish == "A" else "A"):
                    release[name].set()
                    threads[name].join(timeout=12)
                    self.assertFalse(threads[name].is_alive())
            finally:
                for signal in release.values():
                    signal.set()
                for thread in started:
                    thread.join(timeout=12)
        self.assertEqual(errors, {})
        self.assertEqual(Loan.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Loan.objects.get(user=self.user).reported_balance, 200000)
        self.assertEqual(results["B"]["skipped_count"], 0)
        self.assertEqual(results["A"]["skipped_count"], int(first_to_finish == "B"))
        runs = list(SyncRun.objects.filter(user=self.user, product_code="loan_sync").order_by("started_at"))
        self.assertEqual(len(runs), 2)
        self.assertLess(runs[0].started_at, runs[1].started_at)
        self.assertTrue(all(run.status == "success" for run in runs))

    def test_new_loan_later_request_can_update_after_first_finishes(self):
        self.race("A")

    def test_existing_loan_later_request_can_update_after_first_finishes(self):
        self.seed_loan()
        self.race("A")

    def test_new_loan_older_request_finishing_last_is_skipped(self):
        self.race("B")

    def test_existing_loan_older_request_finishing_last_is_skipped(self):
        self.seed_loan()
        self.race("B")

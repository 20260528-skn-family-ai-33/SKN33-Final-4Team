"""은행 동기화 저장과 홈 자동 납부 연결의 실제 PostgreSQL 잠금 경합 회귀 테스트."""
import hashlib
import threading
import time
from datetime import date, datetime
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from budgets import home
from users.models import FinancialProfile

from . import codef
from .models import BankAccount, Connection, FinancialTransaction, PaymentDue, PaymentMatch, RecurringExpense, SyncRun
from .pipeline import RETRY_STATE_KEY
from .sync import sync_bank


@skipUnless(connection.vendor == "postgresql", "행 잠금은 PostgreSQL에서만 확인")
class BankSyncHomeConcurrencyTest(TransactionTestCase):
    serialized_rollback = True

    def setUp(self):
        self.today = date(2026, 10, 21)
        self.user = get_user_model().objects.create_user(email="sync-home@example.com")
        FinancialProfile.objects.create(user=self.user, payday=25, monthly_take_home_income=2_500_000)
        self.bank = Connection.objects.create(
            user=self.user, business_type="bank", organization_code="0088", status="connected",
        )
        self.account_data = {
            "account_no": "110123456789", "name": "입출금", "balance": 90000, "kind_code": "11",
        }
        self.account = BankAccount.objects.create(
            user=self.user, connection=self.bank, account_type="demand", is_selected=True, balance=100000,
            source_account_key=hashlib.sha256(self.account_data["account_no"].encode()).hexdigest(),
        )
        self.expense = RecurringExpense.objects.create(
            user=self.user, name="월세", amount=700000, payment_day=20, category_id="HOUSING_TELECOM",
            status="confirmed", source_type="manual", payment_account=self.account,
            confirmed_at=timezone.make_aware(datetime(2026, 9, 25)),
        )
        self.payment_row = self.row(700000)
        self.payment = FinancialTransaction.objects.create(
            user=self.user, account=self.account, source_kind="bank", transaction_type="transfer",
            confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review", **self.payment_row,
        )

    def row(self, amount):
        return codef.normalize_transaction({
            "resAccountTrDate": "20261020", "resAccountTrTime": "123000",
            "resAccountOut": str(amount), "resAccountIn": "0", "resAfterTranBalance": "90000",
            "resAccountDesc3": "시험 출금",
        })

    def race_home_and_sync(self, rows):
        home_locked, release_home, sync_ready, sync_done = (threading.Event() for _ in range(4))
        results, errors, pids = {}, {}, {}
        original_lock = home.lock_user

        def hold_home_lock(user):
            original_lock(user)
            home_locked.set()
            if not release_home.wait(timeout=10):
                raise TimeoutError("홈 잠금 해제 신호를 받지 못했습니다")

        def get_home():
            client = APIClient()
            client.force_authenticate(self.user)
            return client.get("/api/home/")

        def sync():
            sync_ready.set()
            return sync_bank(self.user, "test-connected-id", "0088", date(2026, 9, 25), self.today)

        def worker(name, action):
            try:
                with connection.cursor() as cursor:
                    # 회귀가 생겨도 테스트 스레드가 DB 잠금을 무한히 기다리지 않게 한다.
                    cursor.execute("SET statement_timeout = '5s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    pids[name] = cursor.fetchone()[0]
                results[name] = action()
            except Exception as exc:
                errors[name] = exc
            finally:
                connection.close()
                if name == "sync":
                    sync_done.set()

        threads = [threading.Thread(target=worker, args=("home", get_home)),
                   threading.Thread(target=worker, args=("sync", sync))]
        started = []
        with (
            patch.object(home, "lock_user", side_effect=hold_home_lock),
            patch("django.utils.timezone.localdate", return_value=self.today),
            patch("finance.codef.list_accounts", return_value=[self.account_data]),
            patch("finance.codef.list_transactions", return_value=rows),
        ):
            try:
                threads[0].start()
                started.append(threads[0])
                self.assertTrue(home_locked.wait(timeout=10), errors)
                threads[1].start()
                started.append(threads[1])
                self.assertTrue(sync_ready.wait(timeout=10), errors)

                # 시간 지연에 기대지 않고 실제로 홈의 회원 잠금을 기다리는 시점을 확인한다.
                blocked = False
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline and not sync_done.is_set():
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_blocking_pids(%s)", [pids["sync"]])
                        blocked = pids["home"] in cursor.fetchone()[0]
                    if blocked:
                        break
                    sync_done.wait(timeout=0.01)
                self.assertTrue(blocked, f"동기화가 홈의 회원 잠금을 기다리지 않았습니다: {errors}")

                # 회원을 기다리며 연결·계좌를 점유하면 안 된다. 별도 연결에서 즉시 잠글 수 있어야 한다.
                try:
                    with transaction.atomic():
                        Connection.objects.select_for_update(nowait=True).get(pk=self.bank.pk)
                        BankAccount.objects.select_for_update(nowait=True).get(pk=self.account.pk)
                except DatabaseError as exc:
                    errors["locks_before_user"] = exc
            finally:
                release_home.set()
                for thread in started:
                    thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in started), "요청 스레드가 종료되지 않았습니다")
        self.assertEqual(errors, {})
        self.assertEqual(results["home"].status_code, 200)
        self.assertEqual(SyncRun.objects.get().status, "success")
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, 90000)

        # 홈의 납부 연결과 동기화 후처리가 모두 커밋되고, 재조회해도 중복 연결이 없어야 한다.
        self.payment.refresh_from_db()
        self.assertTrue(self.payment.field_states.get("converted_by_fixed"))
        self.assertNotIn(RETRY_STATE_KEY, self.payment.field_states)
        due = PaymentDue.objects.get(recurring=self.expense, due_date=date(2026, 10, 20))
        self.assertEqual((due.payment_state, due.paid_amount), ("paid", 700000))
        self.assertEqual(list(due.matches.values_list("transaction_id", flat=True)), [self.payment.pk])
        home.home_data(self.user, self.today)
        self.assertEqual(PaymentMatch.objects.filter(transaction=self.payment).count(), 1)
        return results["sync"]

    def test_home_and_sync_with_new_transaction(self):
        result = self.race_home_and_sync([self.row(10000)])
        self.assertEqual(result, {"account_count": 1, "new_transaction_count": 1, "existing_transaction_count": 0})
        self.assertEqual(FinancialTransaction.objects.count(), 2)

    def test_home_and_sync_retry_without_new_transaction(self):
        self.payment.field_states = {RETRY_STATE_KEY: {"classify_spending": "step_error"}}
        self.payment.save(update_fields=["field_states"])
        result = self.race_home_and_sync([self.payment_row])
        self.assertEqual(result, {"account_count": 1, "new_transaction_count": 0, "existing_transaction_count": 1})
        self.assertEqual(FinancialTransaction.objects.count(), 1)

    def test_home_and_sync_without_fetched_transactions(self):
        result = self.race_home_and_sync([])
        self.assertEqual(result, {"account_count": 1, "new_transaction_count": 0, "existing_transaction_count": 0})
        self.assertEqual(FinancialTransaction.objects.count(), 1)

"""R06 은행 조회: 먼저 시작한 조회가 늦게 저장해도 최신 계좌 값을 덮지 않는다 (실제 PostgreSQL 행 잠금)
실행: backend 폴더에서 python manage.py test finance.test_bank_sync_concurrency
"""
import threading
import uuid
from datetime import date
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase

from . import codef
from .models import BankAccount, Connection, FinancialTransaction, SyncRun
from .sync import sync_bank

DAY = date(2026, 10, 27)
NUMBER = "110123456789"
BALANCE = {"A": 100000, "B": 900000}


def row(day, amount, description="시험 거래"):
    return codef.normalize_transaction({
        "resAccountTrDate": day, "resAccountTrTime": "123000", "resAccountOut": str(amount), "resAccountIn": "0",
        "resAfterTranBalance": "0", "resAccountDesc3": description,
    })


@skipUnless(connection.vendor == "postgresql", "실제 행 잠금은 PostgreSQL에서 검사")
class BankSyncConcurrencyTest(TransactionTestCase):
    serialized_rollback = True  # 마이그레이션 초기 카테고리 복원 (test_loan_sync_concurrency 와 같음)

    def setUp(self):
        self.user = get_user_model().objects.create_user(email=f"bank-race-{uuid.uuid4().hex}@example.invalid")
        Connection.objects.create(user=self.user, business_type="bank", organization_code="0088", status="connected")
        self.rows = {"A": [row("20261020", 10000)], "B": [row("20261020", 10000)]}
        self.failing = set()
        self.balance = dict(BALANCE)
        self.entered = {name: threading.Event() for name in ("A", "B")}
        self.release = {name: threading.Event() for name in ("A", "B")}
        # 모든 sync_bank 호출(시드 포함)이 대역을 거친다 — 실제 CODEF 를 부르지 않는다
        for target, fake in (("list_accounts", self.accounts), ("list_transactions", self.transactions),
                             ("_request", self.no_network)):
            patcher = patch(f"finance.codef.{target}", side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def no_network(self, *args):
        raise AssertionError("테스트에서 실제 CODEF 를 부르면 안 된다")

    def accounts(self, connected_id, org):
        if connected_id != "seed":
            self.entered[connected_id].set()
            if not self.release[connected_id].wait(timeout=15):
                raise TimeoutError("조회 완료 신호 대기 시간 초과")
            if connected_id in self.failing:
                raise codef.CodefError("CF-00000", "시험 실패")
        name = {"A": "옛 계좌명", "B": "새 계좌명"}.get(connected_id, "처음 계좌명")
        return [{"account_no": NUMBER, "name": name, "balance": self.balance.get(connected_id, 50000),
                 "kind_code": "11", "org": org}]

    def transactions(self, connected_id, org, number, start, end):
        return self.rows.get(connected_id, [])

    def seed_account(self):
        sync_bank(self.user, "seed", "0088", DAY, DAY)  # 대역이 "seed" 를 바로 돌려준다
        account = BankAccount.objects.get(user=self.user)
        account.alias, account.is_selected, account.budget_role = "내 별칭", True, "living"
        account.save(update_fields=["alias", "is_selected", "budget_role"])

    def race(self, first, between=None):
        """A 를 먼저 시작 · B 를 나중에 시작한 뒤(둘 다 외부 조회 대기 중) first 쪽부터 저장까지 끝낸다"""
        entered, release = self.entered, self.release
        results, errors = {}, {}

        def worker(name):
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '10s'")
                    cursor.execute("SET lock_timeout = '8s'")
                results[name] = sync_bank(self.user, name, "0088", DAY, DAY)
            except Exception as exc:
                errors[name] = type(exc).__name__
            finally:
                connection.close()

        threads = {name: threading.Thread(target=worker, args=(name,)) for name in ("A", "B")}
        started = []
        try:
            for name in ("A", "B"):  # A 의 SyncRun 이 만들어진 뒤에 B 를 시작한다 → started_at A < B
                threads[name].start()
                started.append(threads[name])
                self.assertTrue(entered[name].wait(timeout=10), errors)
            for index, name in enumerate((first, "B" if first == "A" else "A")):
                release[name].set()
                threads[name].join(timeout=12)
                self.assertFalse(threads[name].is_alive())
                if index == 0 and between:
                    between()
        finally:
            for signal in release.values():
                signal.set()
            for thread in started:
                thread.join(timeout=12)
        return results, errors

    def account(self):
        accounts = list(BankAccount.objects.filter(user=self.user))
        self.assertEqual(len(accounts), 1)  # 역순 저장에도 계좌가 중복 생성되지 않는다
        return accounts[0]

    def check_newest(self, results, errors):
        self.assertEqual(errors, {})
        account = self.account()
        self.assertEqual((account.balance, account.name, account.account_type), (900000, "새 계좌명", "demand"))
        self.assertEqual(account.field_states["codef_deposit_code"], "11")
        runs = list(SyncRun.objects.filter(user=self.user, product_code="bank_sync", status="success"))
        self.assertTrue(all((r.covered_from, r.covered_to) == (DAY, DAY) for r in runs))
        return account

    def test_existing_account_older_response_saved_last_keeps_newest(self):
        self.seed_account()
        seen = {}
        results, errors = self.race("B", between=lambda: seen.update(at=self.account().fetched_at))
        account = self.check_newest(results, errors)
        self.assertEqual(account.fetched_at, seen["at"])  # 반영하지 않은 늦은 A 가 fetched_at 을 올리지 않는다
        self.assertEqual((account.alias, account.is_selected, account.budget_role), ("내 별칭", True, "living"))
        self.assertEqual(results["A"]["account_count"], 1)

    def test_older_missing_balance_saved_last_keeps_newest(self):
        """R07 보완: 잔액 미제공(None) 응답이라도 먼저 시작해 늦게 온 것이면 최신 잔액 · fetched_at 을 덮지 않는다"""
        self.seed_account()
        self.balance["A"] = None
        seen = {}
        results, errors = self.race("B", between=lambda: seen.update(at=self.account().fetched_at))
        account = self.check_newest(results, errors)
        self.assertEqual(account.fetched_at, seen["at"])

    def test_new_account_older_response_saved_last_keeps_newest(self):
        self.check_newest(*self.race("B"))

    def test_normal_order_later_response_wins(self):
        self.seed_account()
        self.check_newest(*self.race("A"))

    def test_later_failed_request_does_not_block_older_success(self):
        self.seed_account()
        self.failing.add("B")
        results, errors = self.race("B")
        self.assertEqual(errors, {"B": "CodefError"})
        account = self.account()
        self.assertEqual((account.balance, account.name, account.account_type), (100000, "옛 계좌명", "demand"))

    def test_older_response_still_adds_its_own_transactions_without_overwriting(self):
        self.rows["A"] = [row("20261020", 10000), row("20261021", 3000, "A 에만 있는 거래")]

        def confirm():  # B 저장 뒤 사용자가 그 거래 분류를 확정
            FinancialTransaction.objects.filter(user=self.user).update(
                transaction_type="spending", confirmation_state="confirmed", confirmed_category_id="FOOD")

        results, errors = self.race("B", between=confirm)
        self.check_newest(results, errors)
        self.assertEqual((results["A"]["new_transaction_count"], results["A"]["existing_transaction_count"]), (1, 1))
        txs = FinancialTransaction.objects.filter(user=self.user)
        self.assertEqual(txs.count(), 2)  # 같은 거래는 중복 생성되지 않는다
        old = txs.exclude(source_description="A 에만 있는 거래").get()
        self.assertEqual((old.transaction_type, old.confirmation_state, old.confirmed_category_id),
                         ("spending", "confirmed", "FOOD"))
        self.assertEqual(txs.get(source_description="A 에만 있는 거래").account_id, self.account().pk)

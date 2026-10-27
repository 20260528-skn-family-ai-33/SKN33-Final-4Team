import hashlib
from copy import deepcopy
from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import serializers
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from . import codef
from .models import BankAccount, Connection, FinancialTransaction, SyncRun
from .sync import sync_bank


@override_settings(CODEF={"CONNECTED_ID": "test-connected-id"})
class BankSyncTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email="sync@example.com")
        cls.other = get_user_model().objects.create_user(email="other@example.com")

    def setUp(self):
        self.start, self.end = date(2026, 7, 29), date(2026, 10, 27)
        self.accounts = [
            {"account_no": "110123456789", "name": "입출금", "balance": 90000, "kind_code": "11", "org": "0088"},
            {"account_no": "220987654321", "name": "다른 계좌", "balance": 500000, "kind_code": "12", "org": "0088"},
        ]
        self.rows = [self.row(), self.row(income=True)]
        self.list_accounts = self.start_patch("finance.codef.list_accounts", return_value=self.accounts)
        self.list_transactions = self.start_patch("finance.codef.list_transactions", return_value=self.rows)
        self.pipeline = self.start_patch("finance.sync.process_new_transactions")

    def start_patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def row(self, income=False, day="20261020", description="시험 거래"):
        return codef.normalize_transaction({
            "resAccountTrDate": day, "resAccountTrTime": "123000",
            "resAccountOut": "0" if income else "10000",
            "resAccountIn": "20000" if income else "0",
            "resAfterTranBalance": "90000", "resAccountDesc3": description,
        })

    def sync(self, user=None):
        return sync_bank(user or self.user, "test-connected-id", "0088", self.start, self.end)

    def test_saves_accounts_transactions_and_success_run(self):
        before = timezone.now()
        result = self.sync()
        self.assertEqual(result, {"account_count": 2, "new_transaction_count": 2, "existing_transaction_count": 0})
        connection = Connection.objects.get()
        self.assertEqual((connection.user, connection.business_type, connection.organization_code, connection.status),
                         (self.user, "bank", "0088", "connected"))
        self.assertIsNone(connection.disconnected_at)
        account = BankAccount.objects.get(account_type="demand")
        self.assertEqual(account.user, self.user)
        self.assertEqual(account.connection, connection)
        self.assertEqual(account.source_account_key, hashlib.sha256(self.accounts[0]["account_no"].encode()).hexdigest())
        self.assertEqual(account.masked_number, codef.mask(self.accounts[0]["account_no"]))
        self.assertEqual(account.balance, 90000)
        self.assertGreaterEqual(account.fetched_at, before)
        self.assertEqual(account.field_states, {"codef_deposit_code": "11"})
        self.assertEqual(BankAccount.objects.get(account_type="other").field_states, {"codef_deposit_code": "12"})
        self.list_accounts.assert_called_once_with("test-connected-id", "0088")
        self.list_transactions.assert_called_once_with(
            "test-connected-id", "0088", self.accounts[0]["account_no"], self.start, self.end,
        )
        for row in self.rows:
            tx = FinancialTransaction.objects.get(source_key=row["source_key"])
            self.assertEqual((tx.user, tx.account, tx.source_kind), (self.user, account, "bank"))
            for field, value in row.items():
                self.assertEqual(getattr(tx, field), value)
            self.assertEqual(tx.transaction_type, "income" if row["direction"] == "in" else "transfer")
            self.assertEqual(tx.confirmation_state, "needs_review")
            self.assertEqual(tx.confirmed_category_id, "UNCLASSIFIED")
        run = SyncRun.objects.get()
        self.assertEqual((run.user, run.connection, run.status), (self.user, connection, "success"))
        self.assertEqual((run.requested_from, run.requested_to), (self.start, self.end))
        self.assertEqual((run.covered_from, run.covered_to), (self.start, self.end))
        self.assertGreaterEqual(run.completed_at, run.started_at)
        self.assertEqual(run.business_code, codef.SUCCESS)

    def test_repeated_sync_keeps_transactions_and_account_choices(self):
        self.sync()
        account = BankAccount.objects.get(account_type="demand")
        account.is_selected, account.budget_role = True, "savings"
        account.alias = "내 별칭"
        account.field_states["other_state"] = "유지"
        account.save()
        fetched_at = account.fetched_at
        tx = FinancialTransaction.objects.get(direction="out")
        tx.transaction_type, tx.confirmation_state, tx.confirmed_category_id = "spending", "confirmed", "FOOD"
        tx.save()
        self.accounts[0]["balance"] = 80000
        self.accounts[0]["name"] = "바뀐 계좌명"
        self.pipeline.reset_mock()

        result = self.sync()

        self.assertEqual(result, {"account_count": 2, "new_transaction_count": 0, "existing_transaction_count": 2})
        self.assertEqual(Connection.objects.count(), 1)
        self.assertEqual(BankAccount.objects.count(), 2)
        self.assertEqual(FinancialTransaction.objects.count(), 2)
        self.assertEqual(SyncRun.objects.filter(status="success").count(), 2)
        account.refresh_from_db()
        self.assertEqual((account.is_selected, account.budget_role, account.alias), (True, "savings", "내 별칭"))
        self.assertEqual(account.field_states, {"other_state": "유지", "codef_deposit_code": "11"})
        self.assertEqual((account.balance, account.name), (80000, "바뀐 계좌명"))
        self.assertGreater(account.fetched_at, fetched_at)
        tx.refresh_from_db()
        self.assertEqual((tx.transaction_type, tx.confirmation_state, tx.confirmed_category_id),
                         ("spending", "confirmed", "FOOD"))
        self.pipeline.assert_called_once_with(self.user, [])

    def test_pipeline_receives_only_new_ids_after_all_rows_are_saved(self):
        self.sync()
        first_ids = set(FinancialTransaction.objects.values_list("pk", flat=True))
        self.assertEqual(set(self.pipeline.call_args.args[1]), first_ids)
        self.rows.append(self.row(day="20261021"))
        self.pipeline.reset_mock()
        seen_ids = []

        def process(user, ids):
            seen_ids.extend(FinancialTransaction.objects.filter(user=user, pk__in=ids).values_list("pk", flat=True))

        self.pipeline.side_effect = process
        result = self.sync()

        new_ids = set(FinancialTransaction.objects.values_list("pk", flat=True)) - first_ids
        self.assertEqual(len(new_ids), 1)
        self.assertEqual(result["new_transaction_count"], 1)
        self.assertEqual(result["existing_transaction_count"], 2)
        self.pipeline.assert_called_once_with(self.user, list(new_ids))
        self.assertEqual(set(seen_ids), new_ids)

    def test_account_numbers_are_not_stored_even_in_names_and_descriptions(self):
        first_number, second_number = (account["account_no"] for account in self.accounts)
        self.accounts[0]["name"] = f"입출금 {first_number}"
        self.rows[:] = [self.row(description=f"이체 {first_number} {second_number}")]
        original_row = deepcopy(self.rows[0])

        with self.assertNoLogs("finance"):
            self.sync()

        for model in (get_user_model(), Connection, BankAccount, FinancialTransaction, SyncRun):
            stored = serializers.serialize("json", model.objects.all())
            self.assertNotIn(first_number, stored)
            self.assertNotIn(second_number, stored)
            self.assertNotIn("test-connected-id", stored)
        self.assertFalse(BankAccount.objects.filter(source_account_ref_ciphertext__isnull=False).exists())
        tx = FinancialTransaction.objects.get()
        self.assertEqual(tx.source_key, original_row["source_key"])
        self.assertEqual(tx.source_description, f"이체 {codef.mask(first_number)} {codef.mask(second_number)}")
        self.assertEqual(self.rows[0], original_row)

    def test_provider_failure_rolls_back_all_new_accounts_and_transactions(self):
        self.accounts[1]["kind_code"] = "11"
        self.list_transactions.side_effect = [self.rows, codef.CodefError("CF-12100", "시험 오류")]

        with self.assertRaises(codef.CodefError):
            self.sync()

        self.assertFalse(BankAccount.objects.exists())
        self.assertFalse(FinancialTransaction.objects.exists())
        self.pipeline.assert_not_called()
        run = SyncRun.objects.get()
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.error_code, "provider_error")
        self.assertEqual(run.business_code, "CF-12100")
        self.assertGreaterEqual(run.completed_at, run.started_at)
        self.assertIsNone(run.covered_from)
        self.assertIsNone(run.covered_to)
        self.assertEqual(run.connection.user, self.user)

    def test_database_failure_restores_existing_accounts_and_keeps_old_transactions(self):
        self.sync()
        old_accounts = list(BankAccount.objects.order_by("pk").values())
        old_transactions = list(FinancialTransaction.objects.order_by("pk").values())
        self.accounts[0]["balance"] = 1
        self.accounts[1]["kind_code"] = "11"
        bad_row = {**self.row(day="20261022"), "amount": -1}
        self.list_transactions.side_effect = [[self.row(day="20261021")], [bad_row]]
        self.pipeline.reset_mock()

        with self.assertRaises(IntegrityError):
            self.sync()

        self.assertEqual(list(BankAccount.objects.order_by("pk").values()), old_accounts)
        self.assertEqual(list(FinancialTransaction.objects.order_by("pk").values()), old_transactions)
        self.assertEqual(SyncRun.objects.filter(status="success").count(), 1)
        run = SyncRun.objects.get(status="failed")
        self.assertEqual(run.error_code, "sync_error")
        self.assertIsNone(run.business_code)
        self.assertIsNotNone(run.completed_at)
        self.pipeline.assert_not_called()

    def test_account_list_failure_is_recorded(self):
        self.list_accounts.side_effect = codef.CodefError("CF-99999", "시험 오류")
        with self.assertRaises(codef.CodefError):
            self.sync()
        self.assertEqual(SyncRun.objects.get().status, "failed")
        self.assertEqual(SyncRun.objects.get().business_code, "CF-99999")
        self.assertFalse(BankAccount.objects.exists())
        self.list_transactions.assert_not_called()
        self.pipeline.assert_not_called()

    def test_connections_and_accounts_are_separate_for_each_user(self):
        self.sync()
        self.sync(self.other)
        self.assertEqual(Connection.objects.count(), 2)
        self.assertEqual(BankAccount.objects.count(), 4)
        self.assertEqual(FinancialTransaction.objects.count(), 4)
        for tx in FinancialTransaction.objects.select_related("account__connection"):
            self.assertEqual(tx.user_id, tx.account.user_id)
            self.assertEqual(tx.user_id, tx.account.connection.user_id)

    def test_disconnected_connection_is_not_reused_but_relogin_is(self):
        old = Connection.objects.create(
            user=self.user, business_type="bank", organization_code="0088",
            status="disconnected", disconnected_at=timezone.now(),
        )
        self.sync()
        active = Connection.objects.get(disconnected_at=None)
        self.assertNotEqual(active.pk, old.pk)
        active.status = "relogin"
        active.save(update_fields=["status"])
        self.sync()
        self.assertEqual(Connection.objects.count(), 2)
        self.assertEqual(BankAccount.objects.count(), 2)
        self.assertEqual(set(SyncRun.objects.values_list("connection_id", flat=True)), {active.pk})
        old.refresh_from_db()
        self.assertEqual(old.status, "disconnected")

    def test_command_prints_only_counts_and_uses_default_period(self):
        stdout, stderr = StringIO(), StringIO()
        with patch("finance.management.commands.codef_sync.timezone.localdate", return_value=self.end):
            call_command("codef_sync", email=self.user.email, stdout=stdout, stderr=stderr)
        self.assertEqual(stdout.getvalue(), "계좌 2개 · 새 거래 2건 · 이미 있던 거래 0건\n")
        self.assertEqual(stderr.getvalue(), "")
        self.list_transactions.assert_called_once_with(
            "test-connected-id", "0088", self.accounts[0]["account_no"], self.end - timedelta(days=90), self.end,
        )

    def test_command_hides_provider_error_details(self):
        secret_message = f"{self.accounts[0]['account_no']} test-connected-id 시험 거래 설명"
        self.list_transactions.side_effect = codef.CodefError("CF-99999", secret_message)
        stdout, stderr = StringIO(), StringIO()
        with self.assertNoLogs("finance"), self.assertRaises(CommandError) as error:
            call_command("codef_sync", email=self.user.email, stdout=stdout, stderr=stderr)
        self.assertEqual(str(error.exception), "은행 동기화에 실패했어요. SyncRun 실행 기록을 확인해 주세요.")
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        self.assertIsNone(error.exception.__cause__)
        self.assertTrue(error.exception.__suppress_context__)
        stored = serializers.serialize("json", SyncRun.objects.all())
        self.assertNotIn(secret_message, stored)

    def test_invalid_command_input_does_not_call_codef(self):
        for options in ({"email": self.user.email, "days": 0}, {"email": "missing@example.com"}):
            with self.subTest(options=options), self.assertRaises(CommandError):
                call_command("codef_sync", **options)
        with override_settings(CODEF={"CONNECTED_ID": ""}), self.assertRaises(CommandError):
            call_command("codef_sync", email=self.user.email)
        self.list_accounts.assert_not_called()
        self.list_transactions.assert_not_called()
        self.assertFalse(SyncRun.objects.exists())


class BankSyncTransactionTest(TransactionTestCase):
    # 테스트 전체를 트랜잭션으로 감싸지 않고 초기 카테고리는 복원한다.
    serialized_rollback = True

    def test_codef_calls_run_outside_database_transaction(self):
        user = get_user_model().objects.create_user(email="sync-transaction@example.com")
        day = date(2026, 10, 27)
        accounts = [
            {"account_no": "110123456789", "name": "첫 계좌", "balance": 90000, "kind_code": "11"},
            {"account_no": "220987654321", "name": "둘째 계좌", "balance": 90000, "kind_code": "11"},
        ]
        row = codef.normalize_transaction({
            "resAccountTrDate": "20261027", "resAccountTrTime": "123000",
            "resAccountOut": "10000", "resAfterTranBalance": "90000", "resAccountDesc3": "시험 거래",
        })

        def list_accounts(*args):
            self.assertFalse(connection.in_atomic_block)
            return accounts

        def list_transactions(*args):
            self.assertFalse(connection.in_atomic_block)
            self.assertFalse(BankAccount.objects.exists())
            self.assertFalse(FinancialTransaction.objects.exists())
            return [row]

        def process(saved_user, ids):
            self.assertTrue(connection.in_atomic_block)
            self.assertEqual(saved_user, user)
            self.assertEqual(BankAccount.objects.count(), 2)
            self.assertEqual(set(ids), set(FinancialTransaction.objects.values_list("pk", flat=True)))
            self.assertEqual(SyncRun.objects.get().status, "running")

        with (
            patch("finance.codef.list_accounts", side_effect=list_accounts) as account_call,
            patch("finance.codef.list_transactions", side_effect=list_transactions) as transaction_call,
            patch("finance.sync.process_new_transactions", side_effect=process) as pipeline_call,
        ):
            result = sync_bank(user, "test-connected-id", "0088", day, day)

        account_call.assert_called_once()
        self.assertEqual(transaction_call.call_count, 2)
        pipeline_call.assert_called_once()
        self.assertFalse(connection.in_atomic_block)
        self.assertEqual(result["new_transaction_count"], 2)
        self.assertEqual(SyncRun.objects.get().status, "success")

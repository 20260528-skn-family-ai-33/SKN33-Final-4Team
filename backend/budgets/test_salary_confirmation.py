"""사용자 확인 당시 원거래와 현재 원거래 비교. 과거 확정을 현재 값으로 복원하지 않는다."""
from datetime import date
from unittest.mock import patch

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from finance.models import FinancialTransaction
from .models import BudgetCycle, SalaryDecision
from .salary import confirmation_snapshot, recognized, refresh_confirmed, salary_reviews, valid_confirmed_ids
from .test_salary_check import OCT, SEPT, SalaryCheckFixture


class SalaryConfirmationValidityTest(SalaryCheckFixture):
    def get_deposits(self, today=None):
        with patch("django.utils.timezone.localdate", return_value=today or self.today):
            response = self.client.get("/api/budgets/salary/deposits/")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_confirm_saves_exact_snapshot_and_unchanged_confirmation_survives(self):
        tx = self.learn(memo="  머니쿠션  ")
        decision = SalaryDecision.objects.get(transaction=tx)
        saved_at = decision.decided_at
        for name, value in confirmation_snapshot(tx).items():
            self.assertEqual(getattr(decision, name), value)
        self.get_deposits()
        self.assertEqual(salary_reviews(self.user), {tx.pk: "confirmed"})
        decision.refresh_from_db()
        self.assertEqual((decision.state, decision.is_pattern, decision.decided_at), ("confirmed", True, saved_at))

    def test_each_changed_field_invalidates_and_requires_explicit_reconfirmation(self):
        other = self.make_account("changed-account")
        changes = [{"amount": 50_000}, {"occurred_on": date(2026, 9, 26)},
                   {"source_description": " 머니쿠션 "}, {"account": other}]
        for changed in changes:
            with self.subTest(changed=changed):
                tx = self.learn()
                before = confirmation_snapshot(tx)
                self.period()  # 저장된 합계도 검증
                FinancialTransaction.objects.filter(pk=tx.pk).update(**changed)
                # 상태 갱신 전의 조회도 오래된 확정을 소비하지 않는다.
                self.assertFalse(valid_confirmed_ids(self.user).filter(transaction=tx).exists())
                self.assertEqual(recognized(self.user, SEPT), [])
                body = self.get_deposits()
                decision = SalaryDecision.objects.get(transaction=tx)
                self.assertEqual((decision.state, decision.is_pattern), ("recheck", False))
                self.assertIsNone(body["pattern"])
                self.assertIsNone(BudgetCycle.objects.get(user=self.user, starts_on=SEPT).confirmed_income)
                self.assertNotIn(tx.pk, salary_reviews(self.user))
                item = next(i for w in body["windows"] for i in w["items"] if i["transactionId"] == str(tx.pk))
                self.assertEqual((item["decision"], item["isCandidate"]), ("recheck", True))
                for name, value in before.items():
                    self.assertEqual(getattr(decision, name), value)
                tx.refresh_from_db()
                self.assertEqual(self.post([tx]).status_code, 200)
                decision.refresh_from_db()
                self.assertEqual(decision.state, "confirmed")
                for name, value in confirmation_snapshot(tx).items():
                    self.assertEqual(getattr(decision, name), value)
                self.assertEqual(salary_reviews(self.user), {tx.pk: "confirmed"})
                tx.delete()

    def test_restoring_original_values_does_not_restore_confirmation_or_pattern(self):
        tx = self.learn()
        FinancialTransaction.objects.filter(pk=tx.pk).update(amount=1)
        refresh_confirmed(self.user)
        FinancialTransaction.objects.filter(pk=tx.pk).update(amount=tx.amount)
        self.get_deposits()
        decision = SalaryDecision.objects.get(transaction=tx)
        self.assertEqual((decision.state, decision.is_pattern), ("recheck", False))
        self.assertNotIn(tx.pk, salary_reviews(self.user))

    def test_changed_pattern_cannot_automatically_recognize_another_deposit(self):
        tx = self.learn()
        FinancialTransaction.objects.filter(pk=tx.pk).update(amount=2_600_000)
        candidate = self.tx(OCT, amount=2_600_000)
        self.period(date(2026, 11, 21))
        self.assertEqual(self.decision(tx), ("recheck", SEPT))
        self.assertIsNone(self.decision(candidate))

    def test_null_description_is_a_valid_snapshot_but_empty_string_is_a_change(self):
        tx = self.tx(SEPT, memo=None)
        self.assertEqual(self.post([tx]).status_code, 200)
        self.assertEqual(salary_reviews(self.user), {tx.pk: "confirmed"})
        FinancialTransaction.objects.filter(pk=tx.pk).update(source_description="")
        self.assertEqual(salary_reviews(self.user), {})
        self.assertEqual(self.decision(tx), ("recheck", SEPT))

    def test_missing_snapshot_is_not_backfilled(self):
        tx = self.tx(SEPT)
        decision = SalaryDecision.objects.create(user=self.user, transaction=tx, state="confirmed", pay_cycle_on=SEPT, is_pattern=True)
        self.get_deposits()
        decision.refresh_from_db()
        self.assertEqual((decision.state, decision.is_pattern), ("recheck", False))
        for name in confirmation_snapshot(tx):
            self.assertIsNone(getattr(decision, name))

    def test_old_recheck_on_deselected_account_can_be_confirmed_again(self):
        tx = self.learn()
        self.account.is_selected = False
        self.account.save(update_fields=["is_selected"])
        FinancialTransaction.objects.filter(pk=tx.pk).update(amount=1_000_000)
        later = date(2026, 12, 21)
        body = self.get_deposits(later)
        group = next(w for w in body["windows"] if w["payCycleOn"] == SEPT.isoformat())
        self.assertEqual(group["items"][0]["decision"], "recheck")
        self.assertEqual(self.post([tx], today=later).status_code, 200)
        self.assertEqual(self.decision(tx), ("confirmed", SEPT))

    def test_corrected_date_outside_window_keeps_original_cycle_for_reconfirmation(self):
        tx = self.learn()
        FinancialTransaction.objects.filter(pk=tx.pk).update(occurred_on=date(2026, 8, 1))
        self.get_deposits()
        self.assertEqual(self.post([tx]).status_code, 200)
        self.assertEqual(self.decision(tx), ("confirmed", SEPT))

    def test_historical_recheck_shows_unchanged_split_salary_in_same_cycle(self):
        first, second = self.tx(SEPT, amount=1_500_000), self.tx(SEPT, amount=1_000_000)
        self.assertEqual(self.post([first, second]).status_code, 200)
        FinancialTransaction.objects.filter(pk=first.pk).update(amount=1_600_000)
        later = date(2026, 12, 21)
        body = self.get_deposits(later)
        group = next(w for w in body["windows"] if w["payCycleOn"] == SEPT.isoformat())
        self.assertEqual({i["transactionId"]: i["decision"] for i in group["items"]},
                         {str(first.pk): "recheck", str(second.pk): "confirmed"})
        self.assertEqual(self.post([first, second], today=later).status_code, 200)
        self.assertEqual(sum(tx.amount for tx in recognized(self.user, SEPT)), 2_600_000)

    def test_future_date_cannot_be_reconfirmed(self):
        tx = self.learn()
        FinancialTransaction.objects.filter(pk=tx.pk).update(occurred_on=date(2027, 1, 1))
        self.get_deposits()
        self.assertEqual(self.post([tx]).status_code, 400)
        self.assertEqual(self.decision(tx), ("recheck", SEPT))

    def test_new_transaction_processing_invalidates_existing_confirmation(self):
        from finance import pipeline
        tx = self.learn()
        FinancialTransaction.objects.filter(pk=tx.pk).update(source_description="다른 입금")
        new = self.tx(date(2026, 10, 20), amount=1000)
        with patch.object(pipeline, "AFTER_SAVE_STEPS", []):
            pipeline.process_new_transactions(self.user, [new.pk])
        self.assertEqual(self.decision(tx), ("recheck", SEPT))


class LegacyConfirmationMigrationTest(TransactionTestCase):
    serialized_rollback = True

    def test_upgrade_requires_recheck_without_fabricating_snapshots(self):
        old = [("budgets", "0007_salarydecision_recheck")]
        new = [("budgets", "0008_salarydecision_confirmation_snapshot")]
        executor = MigrationExecutor(connection)
        executor.migrate(old)
        try:
            apps = executor.loader.project_state(old).apps
            User = apps.get_model("users", "User")
            user = User.objects.create(email="legacy-snapshot@example.invalid")
            bank = apps.get_model("finance", "Connection").objects.create(
                user=user, business_type="bank", organization_code="0004", status="connected")
            account = apps.get_model("finance", "BankAccount").objects.create(
                user=user, connection=bank, source_account_key="legacy", account_type="demand")
            Tx = apps.get_model("finance", "FinancialTransaction")
            Decision = apps.get_model("budgets", "SalaryDecision")
            Cycle = apps.get_model("budgets", "BudgetCycle")
            tx = Tx.objects.create(user=user, account=account, source_kind="bank", amount=2500000, direction="in", occurred_on=SEPT,
                                   transaction_type="income", confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review")
            original = Decision.objects.create(user=user, transaction=tx, state="confirmed", pay_cycle_on=SEPT, is_pattern=True)
            Cycle.objects.create(user=user, starts_on=SEPT, ends_before=OCT, confirmed_income=2500000)
        finally:
            MigrationExecutor(connection).migrate(new)
        upgraded = SalaryDecision.objects.get(pk=original.pk)
        self.assertEqual((upgraded.state, upgraded.is_pattern, upgraded.decided_at), ("recheck", False, original.decided_at))
        self.assertIsNone(BudgetCycle.objects.get(user_id=user.pk).confirmed_income)
        for field in ("snapshot_amount", "snapshot_occurred_on", "snapshot_description", "snapshot_account_id"):
            self.assertIsNone(getattr(upgraded, field))

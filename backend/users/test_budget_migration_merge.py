"""Merge both users/0011 histories without renaming them or changing stored financial settings."""
from contextlib import contextmanager
from datetime import date

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from budgets.models import BudgetCategorySetting, BudgetCycle
from .models import CategoryMinimum, FinancialProfile, PrivacyDeletion

GOALS = '0011_financialprofile_budget_category_targets'
PRIVACY = '0011_privacy_deletion'
MERGE = '0012_merge_budget_targets_privacy'


class BudgetMigrationGraphTest(SimpleTestCase):
    def test_merge_is_the_only_users_leaf_and_has_no_schema_operations(self):
        loader = MigrationLoader(None)
        self.assertNotIn('users', loader.detect_conflicts())
        # 0012 뒤에 users 마이그레이션이 더 생겨도(11-11 0013 사는 지역) 갈래 없이 0012 를 지나는 한 줄이면 된다
        [leaf] = loader.graph.leaf_nodes('users')
        self.assertIn(('users', MERGE), loader.graph.forwards_plan(leaf))
        merge = loader.disk_migrations['users', MERGE]
        self.assertEqual(set(merge.dependencies), {('users', GOALS), ('users', PRIVACY)})
        self.assertEqual(merge.operations, [])


class BudgetMigrationDataTest(TransactionTestCase):
    serialized_rollback = True

    @contextmanager
    def before_merge(self, names):
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes()
        targets = [('users', name) for name in names]
        try:
            executor.migrate([('users', '0010_financialprofile_payday_change')])
            MigrationExecutor(connection).migrate(targets)
            executor = MigrationExecutor(connection)
            applied = executor.loader.applied_migrations
            for name in (GOALS, PRIVACY):
                self.assertEqual(('users', name) in applied, name in names)
            yield executor.loader.project_state(targets).apps
        finally:
            MigrationExecutor(connection).migrate(leaves)

    def seed_settings(self, apps, *, with_goals):
        user = apps.get_model('users', 'User').objects.create(email='synthetic-migration@example.invalid')
        values = {'monthly_take_home_income': 2500000, 'living_budget': 700000}
        goals = {'FOOD': 345678, 'ETC': 40000}
        if with_goals:
            values['budget_category_targets'] = goals
        apps.get_model('users', 'FinancialProfile').objects.create(user=user, **values)
        CategoryMinimum.objects.create(user_id=user.pk, category_id='FOOD', min_amount=77777)
        cycle = BudgetCycle.objects.create(user_id=user.pk, starts_on=date(2026, 10, 25),
            ends_before=date(2026, 11, 25), status='active')
        setting = BudgetCategorySetting.objects.create(user_id=user.pk, cycle=cycle, category_id='EDUCATION',
            locked=True, locked_amount=12345, change_count=2)
        return user.pk, setting.pk, goals if with_goals else {}

    def apply_merge(self):
        MigrationExecutor(connection).migrate([('users', MERGE)])

    def assert_settings(self, seeded):
        user_id, setting_id, goals = seeded
        # 0012 시점 표에는 뒤에 생긴 칸(region_code 등)이 없다 → 보는 칸만 읽는다
        profile = FinancialProfile.objects.filter(user_id=user_id).values(
            'monthly_take_home_income', 'living_budget', 'budget_category_targets').get()
        self.assertEqual((profile['monthly_take_home_income'], profile['living_budget']), (2500000, 700000))
        self.assertEqual(profile['budget_category_targets'], goals)
        self.assertEqual(CategoryMinimum.objects.get(user_id=user_id, category_id='FOOD').min_amount, 77777)
        setting = BudgetCategorySetting.objects.get(pk=setting_id)
        self.assertEqual((setting.locked, setting.locked_amount, setting.change_count), (True, 12345, 2))
        applied = MigrationRecorder(connection).applied_migrations()
        for name in (GOALS, PRIVACY, MERGE):
            self.assertIn(('users', name), applied)

    def test_goal_branch_already_applied_keeps_exact_goals_minimum_and_lock(self):
        with self.before_merge([GOALS]) as apps:
            seeded = self.seed_settings(apps, with_goals=True)
            self.apply_merge()
            self.assert_settings(seeded)
            self.assertEqual(PrivacyDeletion.objects.count(), 0)

    def test_privacy_branch_already_applied_keeps_pending_deletion_record(self):
        with self.before_merge([PRIVACY]) as apps:
            seeded = self.seed_settings(apps, with_goals=False)
            deletion = apps.get_model('users', 'PrivacyDeletion').objects.create(
                user_id=seeded[0], scope='bank', status='provider_pending', attempts=3)
            self.apply_merge()
            self.assert_settings(seeded)
            stored = PrivacyDeletion.objects.get(pk=deletion.pk)
            self.assertEqual((stored.user_id, stored.scope, stored.status, stored.attempts),
                             (seeded[0], 'bank', 'provider_pending', 3))

    def test_both_branches_applied_merge_adds_history_only_without_schema_changes(self):
        with self.before_merge([GOALS, PRIVACY]) as apps:
            seeded = self.seed_settings(apps, with_goals=True)
            deletion = apps.get_model('users', 'PrivacyDeletion').objects.create(user_id=seeded[0], scope='all')
            with CaptureQueriesContext(connection) as queries:
                self.apply_merge()
            self.assertFalse(any(any(word in query['sql'].upper() for word in ('ALTER TABLE', 'CREATE TABLE', 'DROP TABLE'))
                                 for query in queries))
            self.assert_settings(seeded)
            self.assertTrue(PrivacyDeletion.objects.filter(pk=deletion.pk).exists())

    def test_neither_branch_applied_adds_both_without_changing_existing_settings(self):
        with self.before_merge(['0010_financialprofile_payday_change']) as apps:
            seeded = self.seed_settings(apps, with_goals=False)
            self.apply_merge()
            self.assert_settings(seeded)

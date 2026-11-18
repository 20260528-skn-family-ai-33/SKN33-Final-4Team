"""승인 전 조정안 격리와 잠금 버전 계약. 일반 추천·승인 예산 동작도 함께 보존한다."""
from unittest.mock import patch

from django.test import TestCase

from chat.models import AsyncTask
from users.models import FinancialProfile

from . import test_budget_view as fixtures
from .api import release_locks, release_locks_without_budget, release_stale_locks
from .models import BudgetCategorySetting
from .overview import budget_data


class AdjustmentGuardTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.BudgetViewTest.setUpTestData.__func__(cls)

    setUp = fixtures.BudgetViewTest.setUp
    make_budget = fixtures.BudgetViewTest.make_budget
    lock = fixtures.BudgetViewTest.lock
    get = fixtures.BudgetViewTest.get

    def task(self, task_type="budget_adjust"):
        return AsyncTask.objects.create(
            user=self.user, task_type=task_type, request_key=f"guards-{task_type}-{AsyncTask.objects.count()}",
            input_hash="guard-input", status="succeeded",
        )

    def derived(self, base=None, *, task_only=False, status="validated", food=700_000):
        proposal = self.make_budget(status, food=food)
        if task_only:
            proposal.task = self.task()
            proposal.save(update_fields=["task"])
        else:
            proposal.base_budget = base
            proposal.save(update_fields=["base_budget"])
        return proposal

    def test_ordinary_taskless_draft_remains_visible_and_adjustment_task_is_hidden(self):
        ordinary = self.make_budget()
        self.derived(task_only=True)
        self.assertIsNone(ordinary.task_id)
        self.assertEqual(self.get()["budget"]["budgetId"], str(ordinary.pk))

    def test_unapproved_base_linked_proposal_does_not_replace_approved_budget(self):
        approved = self.make_budget("approved")
        proposal = self.derived(approved)
        self.assertEqual(self.get()["budget"]["budgetId"], str(approved.pk))
        self.assertEqual(budget_data(self.user, fixtures.TODAY, selected=proposal)["budget"]["budget_id"], str(approved.pk))
        approved.refresh_from_db()
        proposal.refresh_from_db()
        self.assertEqual((approved.status, proposal.status), ("approved", "validated"))

    def test_task_only_proposal_is_hidden_when_no_ordinary_budget_exists(self):
        self.derived(task_only=True)
        self.assertIsNone(self.get()["budget"])

    def test_non_adjustment_task_draft_preserves_existing_recommendation_selection(self):
        self.make_budget("approved")
        ordinary = self.make_budget()
        ordinary.task = self.task("budget_recommend")
        ordinary.save(update_fields=["task"])
        self.assertEqual(self.get()["budget"]["budgetId"], str(ordinary.pk))

    def test_bulk_release_changes_only_current_locks_and_preserves_change_count(self):
        food = BudgetCategorySetting.objects.create(
            user=self.user, cycle=self.cycle, category_id="FOOD", locked=True,
            locked_amount=300_000, change_count=2, revision_no=4,
        )
        unlocked = BudgetCategorySetting.objects.create(
            user=self.user, cycle=self.cycle, category_id="BEAUTY", revision_no=8,
        )
        outside = BudgetCategorySetting.objects.create(
            user=self.user, cycle=self.cycle, category_id="TRAVEL_LODGING", locked=True,
            locked_amount=100_000, revision_no=6,
        )
        self.assertEqual(release_locks(self.user, self.cycle), ["FOOD"])
        self.assertEqual(release_locks(self.user, self.cycle), [])
        for setting in (food, unlocked, outside):
            setting.refresh_from_db()
        self.assertEqual((food.locked, food.locked_amount, food.change_count, food.revision_no), (False, None, 2, 5))
        self.assertEqual((unlocked.locked, unlocked.revision_no), (False, 8))
        self.assertEqual((outside.locked, outside.locked_amount, outside.revision_no), (True, 100_000, 6))

    def test_both_automatic_release_paths_use_the_same_revision_update(self):
        setting = BudgetCategorySetting.objects.create(
            user=self.user, cycle=self.cycle, category_id="FOOD", locked=True,
            locked_amount=300_000, change_count=2, revision_no=4,
        )
        with patch("budgets.recommend.locks_block_recommendation", return_value=True):
            self.assertEqual(release_stale_locks(self.user, self.profile), ["FOOD"])
            setting.refresh_from_db()
            self.assertEqual((setting.change_count, setting.revision_no), (2, 5))
            setting.locked, setting.locked_amount = True, 300_000
            setting.save(update_fields=["locked", "locked_amount"])
            self.assertEqual(release_locks_without_budget(self.user, self.cycle, 100_000), ["FOOD"])
        setting.refresh_from_db()
        self.assertEqual((setting.change_count, setting.revision_no), (2, 6))

    def test_profile_category_targets_have_independent_defaults_and_round_trip(self):
        other = FinancialProfile.objects.create(user=self.other)
        self.assertEqual((self.profile.budget_category_targets, other.budget_category_targets), ({}, {}))
        self.profile.budget_category_targets["FOOD"] = 435_000
        self.profile.save(update_fields=["budget_category_targets"])
        other.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(other.budget_category_targets, {})
        self.assertEqual(self.profile.budget_category_targets, {"FOOD": 435_000})

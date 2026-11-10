"""예산 조정의 보류 저장·승인·소유권·만료. 합성 DB와 고정된 조회 스냅샷을 사용한다."""
from copy import deepcopy
from datetime import date
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import Http404
from django.test import TestCase
from django.utils import timezone

from chat.models import AsyncTask, Conversation
from users.models import FinancialProfile

from .adjustment_service import (
    AdjustmentAdapter, AdjustmentConflict, AdjustmentExpired, AdjustmentNotReady,
    approve_adjustment, proposal_card,
)
from .api import lock_user
from .models import Budget, BudgetCategorySetting, BudgetCycle, BudgetItem, ReservePlan
from .test_adjustment_core import allocation, context, envelope, item


TODAY = date(2026, 11, 5)


class AdjustmentServiceTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="adjust-service@example.com")
        self.other = get_user_model().objects.create_user(email="adjust-other@example.com")
        self.profile = FinancialProfile.objects.create(user=self.user, payday=25, living_budget=1000000,
            savings_floor=600000, cushion_monthly=200000, monthly_take_home_income=3000000)
        self.cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25))
        self.base = Budget.objects.create(user=self.user, cycle=self.cycle, version_no=1, status="approved",
            funding_amount=3000000, approved_at=timezone.now(), input_snapshot={"untouched": "source"})
        self.snapshot = context()
        self.snapshot.update(base_budget_id=str(self.base.pk), cycle_id=str(self.cycle.pk), spending_status="complete")
        BudgetItem.objects.bulk_create([
            BudgetItem(user=self.user, budget=self.base, bucket="living", category_id=code, allocated_amount=amount)
            for code, amount in self.snapshot["allocations"].items()
        ] + [BudgetItem(user=self.user, budget=self.base, bucket=bucket, allocated_amount=amount)
             for bucket, amount in (("fixed", 800000), ("savings", 600000), ("cushion", 200000))])
        self.talk = Conversation.objects.create(user=self.user)
        factory = patch("budgets.adjustment_service.get_adjustment_context", side_effect=lambda *a, **k: deepcopy(self.snapshot))
        self.context_reader = factory.start()
        self.addCleanup(factory.stop)
        configured = self.settings(BUDGET_ADJUST_ALLOCATOR=allocation, BUDGET_ADJUST_REFERENCE_PROVIDER=None)
        configured.enable()
        self.addCleanup(configured.disable)

    def adapter(self):
        return AdjustmentAdapter(self.user, self.talk, TODAY, str(self.base.pk), self.snapshot["mode"])

    def stage(self, request=None, **settings):
        adapter = self.adapter()
        baseline = adapter.get_context()
        result = adapter.simulate(baseline, request or envelope(item(amount=15000), sources=["SPARE"]))
        card = adapter.propose(baseline, result, scope=settings.pop("scope", "cycle"), **settings)
        return adapter, card

    def flush(self, adapter):
        with transaction.atomic():
            lock_user(self.user)
            adapter.flush(self.talk)

    def approve(self, proposal, *, keep=False, key="adjust-approval-key", mode=None):
        with transaction.atomic():
            lock_user(self.user)
            return approve_adjustment(self.user, proposal, keep_for_next_cycle=keep, key=key,
                expected_mode=mode or self.snapshot["mode"], today=TODAY)

    def test_stage_writes_nothing_and_flush_preserves_original_until_approval(self):
        adapter, card = self.stage()
        self.assertEqual(Budget.objects.count(), 1)
        self.assertEqual(AsyncTask.objects.count(), 0)
        self.assertEqual(adapter.task_status(card["task_ref"])["state"], "awaiting_approval")
        self.flush(adapter)
        proposal = Budget.objects.get(pk=card["proposal_id"])
        self.assertEqual(proposal.status, "validated")
        self.assertEqual(proposal.base_budget_id, self.base.pk)
        self.assertEqual(proposal.task.conversation_id, self.talk.pk)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, "approved")
        self.assertEqual(self.base.items.get(category_id="FOOD").allocated_amount, 400000)
        self.assertEqual(proposal.items.get(category_id="FOOD").allocated_amount, 415000)
        self.assertEqual(proposal.input_snapshot["untouched"], "source")

    def test_stale_flush_leaves_no_proposal_task_or_cancellation(self):
        adapter, card = self.stage()
        self.snapshot["spent"]["FOOD"] += 5000
        with self.assertRaises(AdjustmentExpired):
            self.flush(adapter)
        self.assertEqual(Budget.objects.count(), 1)
        self.assertFalse(AsyncTask.objects.exists())
        self.assertEqual(Budget.objects.get(pk=self.base.pk).status, "approved")

    def test_financial_context_consent_is_rechecked_even_before_a_proposal_exists(self):
        from rest_framework.exceptions import PermissionDenied
        adapter = self.adapter()
        adapter.get_context()
        with patch("users.privacy.require_bank_not_revoked", side_effect=PermissionDenied("consent revoked")):
            with self.assertRaises(PermissionDenied):
                self.flush(adapter)
        self.assertEqual(Budget.objects.count(), 1)
        self.assertFalse(AsyncTask.objects.exists())

    def test_configured_proportional_default_is_reused_for_preview_and_approval(self):
        from .adjustment_core.calculation import proportional_group_reduction
        path = "budgets.adjustment_core.calculation.proportional_group_reduction"
        with self.settings(BUDGET_ADJUST_ALLOCATOR=path):
            adapter, card = self.stage(envelope(item(amount=30000), sources=["LIVING"]))
            self.assertIs(adapter.allocator, proportional_group_reduction)
            self.flush(adapter)
            self.assertEqual(self.approve(card["proposal_id"])["state"], "saved")
        proposal = Budget.objects.get(pk=card["proposal_id"])
        self.assertEqual(proposal.items.get(category_id="FOOD").allocated_amount, 430000)
        self.assertEqual(sum(row["amount"] for row in card["transfers"]), 30000)

    def test_explicitly_disabled_group_policy_is_not_silently_replaced(self):
        with self.settings(BUDGET_ADJUST_ALLOCATOR=None):
            adapter = self.adapter()
            result = adapter.simulate(adapter.get_context(), envelope(item(amount=30000), sources=["LIVING"]))
        self.assertEqual(result["status"], "needs_policy")
        self.assertFalse(AsyncTask.objects.exists())

    def test_approval_persists_verified_amount_and_requested_goal_once(self):
        adapter, card = self.stage()
        self.flush(adapter)
        response = self.approve(card["proposal_id"], keep=True)
        self.assertEqual(response["state"], "saved")
        self.assertEqual(response["saved_ref"], card["proposal_id"])
        proposal = Budget.objects.get(pk=card["proposal_id"])
        self.assertEqual(proposal.status, "approved")
        self.assertIsNotNone(proposal.approved_at)
        self.assertEqual(proposal.items.get(category_id="FOOD").allocated_amount, 415000)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.budget_category_targets, {"FOOD": 415000})
        self.assertEqual(self.profile.savings_floor, 600000)
        self.assertEqual(self.approve(proposal, keep=True), response)
        with self.assertRaises(AdjustmentConflict):
            self.approve(proposal, keep=False, key="another-key")
        self.assertEqual(Budget.objects.filter(status="approved").count(), 1)

    def test_only_requested_category_persists_when_living_donors_are_automatically_reduced(self):
        path = "budgets.adjustment_core.calculation.proportional_group_reduction"
        with self.settings(BUDGET_ADJUST_ALLOCATOR=path):
            adapter, card = self.stage(envelope(item(amount=100000), sources=["LIVING"]))
            self.assertTrue(any(row["diff"] < 0 and not row["direct"] for row in card["changes"]))
            self.flush(adapter)
            self.approve(card["proposal_id"], keep=True)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.budget_category_targets, {"FOOD": 500000})
        proposal = Budget.objects.get(pk=card["proposal_id"])
        self.assertLess(proposal.items.get(category_id="FASHION_SHOPPING").allocated_amount, 200000)

    def test_approval_reuses_frozen_ml_without_calling_provider_under_user_lock(self):
        prediction = {"ml_reference": deepcopy(self.snapshot["ml_reference"]),
                      "ml_reference_meta": {"source": "compatibility", "model_id": "test-model", "model_checksum": "0" * 64}}
        provider = Mock(return_value=prediction)

        def read_context(*args, reference_provider=None, **kwargs):
            baseline = deepcopy(self.snapshot)
            if reference_provider is not None:
                baseline.update(reference_provider({"target": 1000000}))
            return baseline

        self.context_reader.side_effect = read_context
        with self.settings(BUDGET_ADJUST_REFERENCE_PROVIDER=provider):
            adapter, card = self.stage()
            self.assertEqual(provider.call_count, 1)
            self.flush(adapter)
            self.approve(card["proposal_id"])
            self.assertEqual(provider.call_count, 1)

    def test_question_turns_reuse_only_ml_reference_and_reread_financial_inputs(self):
        prediction = {"ml_reference": deepcopy(self.snapshot["ml_reference"]),
                      "ml_reference_meta": {"source": "compatibility", "model_id": "test-model", "model_checksum": "0" * 64}}
        provider = Mock(return_value=prediction)

        def read_context(*args, reference_provider=None, **kwargs):
            baseline = deepcopy(self.snapshot)
            if reference_provider is not None:
                baseline.update(reference_provider({"target": 1000000}))
            return baseline

        self.context_reader.side_effect = read_context
        with self.settings(BUDGET_ADJUST_REFERENCE_PROVIDER=provider):
            first = self.adapter()
            first.get_context()
            self.snapshot["spent"]["FOOD"] = 125000
            following = self.adapter()
            following.reference_context = deepcopy(first.reference_context)
            current = following.get_context()
            self.assertEqual(provider.call_count, 1)
            self.assertEqual(current["spent"]["FOOD"], 125000)
            self.assertEqual(current["ml_reference"], prediction["ml_reference"])

    def test_saved_reference_survives_later_budget_replacement(self):
        adapter, card = self.stage()
        self.flush(adapter)
        self.approve(card["proposal_id"])
        Budget.objects.filter(pk=card["proposal_id"]).update(status="superseded")
        checked = self.adapter().task_status(card["task_ref"])
        self.assertEqual(checked["state"], "saved")
        self.assertEqual(checked["saved_ref"], card["proposal_id"])

    def test_unchecking_ongoing_default_reports_actual_saved_setting(self):
        adapter, card = self.stage(scope="ongoing")
        self.assertTrue(card["keep_for_next_cycle"])
        self.flush(adapter)
        saved = self.approve(card["proposal_id"], keep=False)
        self.assertFalse(saved["keep_for_next_cycle"])
        self.assertEqual(saved["scope"], "cycle")
        self.assertEqual(saved["target_items"], [])
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.budget_category_targets, {})

    def test_expiry_is_persisted_when_caller_returns_error_inside_atomic(self):
        adapter, card = self.stage()
        self.flush(adapter)
        self.snapshot["spent"]["FOOD"] += 5000
        with transaction.atomic():
            lock_user(self.user)
            try:
                approve_adjustment(self.user, card["proposal_id"], keep_for_next_cycle=False,
                    key="expiry-key", expected_mode=self.snapshot["mode"], today=TODAY)
            except AdjustmentExpired:
                pass
            else:
                self.fail("stale proposal was approved")
        self.assertEqual(Budget.objects.get(pk=card["proposal_id"]).status, "expired")
        self.assertEqual(AsyncTask.objects.get(pk=card["task_ref"]).result["state"], "expired")
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, "approved")

    def test_cancel_is_deferred_and_does_not_expire_source_draft(self):
        self.base.status = "validated"
        self.base.approved_at = None
        self.base.save(update_fields=["status", "approved_at"])
        self.snapshot.update(mode="INITIAL_ADJUSTMENT", source_status="validated")
        adapter, card = self.stage()
        self.flush(adapter)
        canceler = self.adapter()
        canceler.cancel(card["task_ref"])
        self.assertEqual(Budget.objects.get(pk=card["proposal_id"]).status, "validated")
        self.flush(canceler)
        self.assertEqual(Budget.objects.get(pk=card["proposal_id"]).status, "rejected")
        self.assertEqual(Budget.objects.get(pk=self.base.pk).status, "validated")
        self.assertEqual(AsyncTask.objects.get(pk=card["task_ref"]).status, "canceled")

    def test_foreign_user_or_conversation_cannot_read_task_or_card(self):
        adapter, card = self.stage()
        self.flush(adapter)
        other_talk = Conversation.objects.create(user=self.other)
        with self.assertRaises(Http404):
            AdjustmentAdapter(self.other, other_talk).task_status(card["task_ref"])
        with self.assertRaises(Http404):
            proposal_card(self.other, card["proposal_id"])
        with self.assertRaises(Http404):
            AdjustmentAdapter(self.user, Conversation.objects.create(user=self.user)).task_status(card["task_ref"])

    def test_partial_requires_persisted_selection_before_approval(self):
        adapter, card = self.stage(envelope(item(amount=35555), sources=["SPARE"]))
        self.assertFalse(card["can_approve"])
        self.flush(adapter)
        self.assertFalse(proposal_card(self.user, card["proposal_id"], today=TODAY)["can_approve"])
        with self.assertRaises(AdjustmentNotReady):
            self.approve(card["proposal_id"])
        self.talk.slots = {"state": {"agent:budget": {"proposal": {"proposal_id": card["proposal_id"]}}}}
        self.talk.save(update_fields=["slots"])
        self.assertTrue(proposal_card(self.user, card["proposal_id"], today=TODAY)["can_approve"])
        saved = self.approve(card["proposal_id"])
        self.assertEqual(saved["allocations"]["FOOD"], 435000)
        self.assertEqual(saved["buckets"]["SPARE"], 365000)

    def test_fixed_and_reserve_rows_are_copied_without_recreating_consumption(self):
        reserve = ReservePlan.objects.create(user=self.user, name="준비금", target_amount=20000, confirmation_state="confirmed")
        BudgetItem.objects.create(user=self.user, budget=self.base, bucket="reserve", reserve=reserve, allocated_amount=20000)
        self.snapshot["fixed_amount"] += 20000
        self.snapshot["funding_amount"] += 20000
        self.base.funding_amount += 20000
        self.base.save(update_fields=["funding_amount"])
        adapter, card = self.stage()
        self.flush(adapter)
        proposal = Budget.objects.get(pk=card["proposal_id"])
        self.assertEqual(proposal.items.get(bucket="reserve", reserve=reserve).allocated_amount, 20000)
        self.assertEqual(proposal.items.get(bucket="fixed").allocated_amount, 800000)
        self.approve(proposal)
        self.assertEqual(proposal.items.filter(bucket="reserve").count(), 1)

    def test_unlock_happens_only_on_approval_with_atomic_revision_increment(self):
        setting = BudgetCategorySetting.objects.create(user=self.user, cycle=self.cycle, category_id="FOOD",
            locked=True, locked_amount=400000, revision_no=7)
        self.snapshot["locks"] = {"FOOD": 400000}
        self.snapshot["lock_revisions"] = {"FOOD": 7}
        adapter = self.adapter()
        original = adapter.get_context()
        calculation = deepcopy(original)
        calculation["locks"] = {}
        result = adapter.simulate(calculation, envelope(item(amount=15000), sources=["SPARE"]))
        card = adapter.propose(original, result, scope="cycle", unlock_categories=["FOOD"])
        self.flush(adapter)
        setting.refresh_from_db()
        self.assertTrue(setting.locked)
        self.approve(card["proposal_id"])
        setting.refresh_from_db()
        self.assertFalse(setting.locked)
        self.assertIsNone(setting.locked_amount)
        self.assertEqual(setting.revision_no, 8)

    def test_initial_and_monthly_approval_routes_cannot_be_exchanged(self):
        adapter, card = self.stage()
        self.flush(adapter)
        with self.assertRaises(AdjustmentNotReady):
            self.approve(card["proposal_id"], mode="INITIAL_ADJUSTMENT")
        self.assertEqual(Budget.objects.get(pk=card["proposal_id"]).status, "validated")

    def test_ongoing_goals_use_the_consented_unlock_when_checking_next_cycle_total(self):
        setting = BudgetCategorySetting.objects.create(user=self.user, cycle=self.cycle, category_id="FOOD",
            locked=True, locked_amount=400000, revision_no=7)
        self.snapshot.update(locks={"FOOD": 400000}, lock_revisions={"FOOD": 7},
                             category_targets=deepcopy(self.snapshot["allocations"]))
        self.profile.budget_category_targets = deepcopy(self.snapshot["category_targets"])
        self.profile.save(update_fields=["budget_category_targets"])
        adapter = self.adapter()
        original = adapter.get_context()
        calculation = deepcopy(original)
        calculation["locks"] = {}
        result = adapter.simulate(calculation, envelope(item(amount=100000, direction="down"), item("HOUSEHOLD", 100000)))
        card = adapter.propose(original, result, scope="cycle", unlock_categories=["FOOD"])
        self.flush(adapter)
        saved = self.approve(card["proposal_id"], keep=True)
        self.assertEqual(saved["state"], "saved")
        setting.refresh_from_db()
        self.assertFalse(setting.locked)
        self.assertEqual(setting.revision_no, 8)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.budget_category_targets["FOOD"], 300000)
        self.assertEqual(self.profile.budget_category_targets["HOUSEHOLD"], 150000)
        self.assertEqual(sum(self.profile.budget_category_targets.values()), 1000000)
        proposal = Budget.objects.get(pk=card["proposal_id"])
        self.assertEqual(proposal.input_snapshot["adjustment_context"]["locks"], {"FOOD": 400000})

    def test_new_approval_expires_all_other_same_cycle_drafts(self):
        old = Budget.objects.create(user=self.user, cycle=self.cycle, version_no=2, status="validated")
        adapter, card = self.stage()
        self.flush(adapter)
        self.approve(card["proposal_id"])
        old.refresh_from_db()
        self.base.refresh_from_db()
        self.assertEqual(old.status, "expired")
        self.assertEqual(self.base.status, "superseded")

    def test_kept_locked_goal_below_system_reference_does_not_block_another_change(self):
        self.snapshot["allocations"].update(TRANSPORT_CAR=50000, FOOD=450000)
        self.snapshot.update(locks={"TRANSPORT_CAR": 50000}, lock_revisions={"TRANSPORT_CAR": 1},
                             category_targets={"TRANSPORT_CAR": 50000})
        self.snapshot["system_floors"]["TRANSPORT_CAR"] = 100000
        for code in ("TRANSPORT_CAR", "FOOD"):
            self.base.items.filter(category_id=code).update(allocated_amount=self.snapshot["allocations"][code])
        setting = BudgetCategorySetting.objects.create(user=self.user, cycle=self.cycle,
            category_id="TRANSPORT_CAR", locked=True, locked_amount=50000, revision_no=1)
        self.profile.budget_category_targets = {"TRANSPORT_CAR": 50000}
        self.profile.save(update_fields=["budget_category_targets"])
        adapter, card = self.stage()
        self.flush(adapter)
        self.assertEqual(self.approve(card["proposal_id"], keep=True)["state"], "saved")
        self.profile.refresh_from_db()
        setting.refresh_from_db()
        self.assertEqual(self.profile.budget_category_targets, {"TRANSPORT_CAR": 50000, "FOOD": 465000})
        self.assertEqual((setting.locked, setting.locked_amount, setting.revision_no), (True, 50000, 1))
        proposal = Budget.objects.get(pk=card["proposal_id"])
        self.assertEqual(proposal.items.get(category_id="TRANSPORT_CAR").allocated_amount, 50000)
        self.assertEqual(proposal.input_snapshot["adjustment_context"]["system_floors"]["TRANSPORT_CAR"], 100000)

    def test_target_clear_is_approved_even_without_money_changes(self):
        self.profile.budget_category_targets = {"FOOD": 400000}
        self.profile.save(update_fields=["budget_category_targets"])
        self.snapshot["category_targets"] = {"FOOD": 400000}
        adapter, card = self.stage(envelope(), target_changes={"FOOD": None})
        self.flush(adapter)
        self.approve(card["proposal_id"])
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.budget_category_targets, {})
        self.assertEqual(Budget.objects.get(pk=card["proposal_id"]).items.get(category_id="FOOD").allocated_amount, 400000)

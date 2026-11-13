"""Authoritative read context: ownership, unchanged budgets, availability and model provenance."""
from datetime import date
import json
import uuid
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from chat.models import AsyncTask
from finance.models import BankAccount, Connection, FinancialTransaction, RecurringSaving
from users.models import CategoryMinimum, FinancialProfile

from .adjustment_context import AdjustmentContextError, adjustment_today, context_fingerprint, get_adjustment_context, _validated_reference
from .models import Budget, BudgetCategorySetting, BudgetCycle, BudgetItem, CushionEntry, SpendingAllocation
from .overview import LIVING_CODES
from .recommend import budget_input_snapshot, hash_snapshot


TODAY = date(2026, 11, 5)
AMOUNTS = {"FOOD": 400000, "TRANSPORT_CAR": 100000, "HOUSEHOLD": 50000, "FASHION_SHOPPING": 200000,
           "MEDICAL_HEALTH": 50000, "BEAUTY": 50000, "EDUCATION": 50000, "CULTURE_LEISURE": 50000, "ETC": 50000}


class AdjustmentDateTest(SimpleTestCase):
    def test_compatibility_does_not_relabel_fallback_ratios_as_actual_model(self):
        prediction = {'ratios': dict.fromkeys(set(LIVING_CODES)-{'ETC'}, .125),
                      'model_id': 'fixture-artifact', 'model_checksum': 'a'*64}
        for extra in ({'source': 'fallback'}, {'used_fallback': True}, {'source': 'synthetic'}):
            result = _validated_reference({'target': 1000000}, set(LIVING_CODES)-{'ETC'}, {**prediction, **extra})
            self.assertEqual(result['ml_reference_status'], 'unavailable')

    @override_settings(CHAT_TODAY="2026-10-29")
    def test_fixed_service_date_is_shared_with_approval_rechecks(self):
        self.assertEqual(adjustment_today(), date(2026, 10, 29))

    @override_settings(CHAT_TODAY=None)
    @patch("budgets.adjustment_context.timezone.localdate", return_value=TODAY)
    def test_normal_service_uses_local_date(self, localdate):
        self.assertEqual(adjustment_today(), TODAY)
        localdate.assert_called_once_with()

    @override_settings(CHAT_TODAY="2026-10-29")
    def test_explicit_date_overrides_fixture_date(self):
        self.assertEqual(adjustment_today(TODAY), TODAY)


class AdjustmentContextTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email="adjustment-context@example.com")
        cls.other = get_user_model().objects.create_user(email="other-adjustment-context@example.com")
        cls.profile = FinancialProfile.objects.create(user=cls.user, monthly_take_home_income=3000000, payday=25,
            living_budget=1000000, savings_floor=600000, cushion_monthly=200000, housing_type="family")
        cls.cycle = BudgetCycle.objects.create(user=cls.user, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25))

    def setUp(self):
        collection = patch("budgets.adjustment_context.coverage", return_value={"state": "complete", "targets": [], "undetermined": []})
        balance = patch("budgets.adjustment_context.balance_json", return_value={"total": 12000000, "unknown_count": 0,
            "account_count": 1, "relogin_count": 0, "as_of": None})
        self.coverage, self.balance = collection.start(), balance.start()
        self.addCleanup(collection.stop)
        self.addCleanup(balance.stop)

    def make_budget(self, *, status="approved", user=None, cycle=None, base=None, task=None, snapshot=None):
        user, cycle = user or self.user, cycle or self.cycle
        latest = Budget.objects.filter(cycle=cycle).order_by("-version_no").values_list("version_no", flat=True).first() or 0
        budget = Budget.objects.create(user=user, cycle=cycle, status=status, version_no=latest + 1,
            base_budget=base, task=task, funding_amount=3000000, input_snapshot=snapshot or {},
            approved_at=timezone.now() if status == "approved" else None)
        BudgetItem.objects.bulk_create([BudgetItem(user=user, budget=budget, bucket="living", category_id=code,
            allocated_amount=AMOUNTS[code]) for code in LIVING_CODES] + [
            BudgetItem(user=user, budget=budget, bucket=bucket, allocated_amount=amount)
            for bucket, amount in (("fixed", 800000), ("savings", 600000), ("cushion", 200000))])
        return budget

    def context(self, **kwargs):
        return get_adjustment_context(self.user, today=TODAY, **kwargs)

    def tx(self, amount, category="FOOD", **kwargs):
        return FinancialTransaction.objects.create(user=self.user, source_kind="manual", occurred_on=TODAY,
            amount=amount, direction="out", transaction_type=kwargs.pop("transaction_type", "spending"),
            confirmed_category_id=category, confirmation_state="confirmed", **kwargs)

    def test_reserve_anchor_uses_first_approved_budget_after_repeated_adjustments(self):
        from .adjustment_core.funding import available_adjustment, reserve_floor
        original = self.make_budget()
        original.status = 'superseded'
        original.save(update_fields=['status'])
        current = self.make_budget(base=original)
        current.items.filter(bucket='savings').update(allocated_amount=300000)
        current.items.filter(bucket='living', category_id='FOOD').update(allocated_amount=1000000)
        self.profile.living_budget = 1600000
        self.profile.savings_floor = 300000
        self.profile.save()
        found = self.context()
        self.assertEqual(found['reserve_anchors']['SAVINGS'], 600000)
        self.assertEqual(found['reserve_anchors']['SPARE'], 400000)
        self.assertEqual(reserve_floor(found, 'SAVINGS', 300000), 180000)
        self.assertEqual(reserve_floor(found, 'SPARE', 100000), 120000)
        self.assertEqual(available_adjustment(found, 'FOOD', 'up', sources=['SAVINGS'])['maximum'], 120000)

    def test_selects_approved_instead_of_newer_derived_or_ordinary_draft(self):
        approved = self.make_budget()
        self.make_budget(status="validated", base=approved)
        self.make_budget(status="validated")
        found = self.context()
        self.assertEqual(found["base_budget_id"], str(approved.pk))
        self.assertEqual(found["mode"], "NORMAL_ADJUSTMENT")
        self.assertEqual(found["allocations"], AMOUNTS)
        self.assertEqual(found["buckets"], {"SAVINGS": 600000, "CUSHION_MONTHLY": 200000, "SPARE": 400000})

    def test_policy_region_save_preserves_context_and_pending_approval(self):
        """정책의 지역 기억은 예산 제안의 금융 기준을 바꾸지 않는다."""
        from django.db import transaction
        from chat.models import Conversation
        from policies.profiles import remember
        from .adjustment_service import AdjustmentAdapter, approve_adjustment
        from .api import lock_user
        from .test_adjustment_core import envelope, item

        self.make_budget()
        talk = Conversation.objects.create(user=self.user)
        adapter = AdjustmentAdapter(self.user, conversation=talk, today=TODAY)
        before = adapter.get_context()
        result = adapter.simulate(before, envelope(item(amount=15000), sources=["SPARE"]))
        card = adapter.propose(before, result, scope="cycle")
        with transaction.atomic():
            lock_user(self.user)
            adapter.flush(talk)
            remember(self.user, "11110")
        self.assertEqual(context_fingerprint(self.context()), context_fingerprint(before))
        with transaction.atomic():
            lock_user(self.user)
            saved = approve_adjustment(self.user, card["proposal_id"], keep_for_next_cycle=False,
                key="policy-region-approval", expected_mode=before["mode"], today=TODAY)
        self.assertEqual(saved["state"], "saved")

    def test_policy_region_save_does_not_hide_financial_changes(self):
        from policies.profiles import remember
        from .adjustment_service import AdjustmentAdapter, AdjustmentExpired

        self.make_budget()
        adapter = AdjustmentAdapter(self.user, today=TODAY)
        before = adapter.get_context()
        remember(self.user, "11110")
        self.profile.living_budget += 10000
        self.profile.save()
        with self.assertRaises(AdjustmentExpired):
            adapter._fresh(before)

    def test_selects_taskless_initial_draft_and_rejects_derived_draft(self):
        draft = self.make_budget(status="validated")
        task = AsyncTask.objects.create(user=self.user, request_key="context-task", task_type="budget_adjust", input_hash="hash")
        self.make_budget(status="validated", task=task)
        found = self.context()
        self.assertEqual((found["base_budget_id"], found["mode"]), (str(draft.pk), "INITIAL_ADJUSTMENT"))
        with self.assertRaisesRegex(AdjustmentContextError, "invalid_basis"):
            self.context(base_budget_id=task.budgets.get().pk, mode="INITIAL_ADJUSTMENT")

    def test_explicit_basis_is_owner_cycle_and_status_scoped(self):
        self.make_budget()
        foreign_cycle = BudgetCycle.objects.create(user=self.other, starts_on=self.cycle.starts_on, ends_before=self.cycle.ends_before)
        foreign = self.make_budget(user=self.other, cycle=foreign_cycle)
        old_cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 9, 25), ends_before=date(2026, 10, 25), status="closed")
        old = self.make_budget(cycle=old_cycle)
        rejected = self.make_budget(status="expired")
        for budget_id in (foreign.pk, old.pk, rejected.pk, uuid.uuid4(), "not-a-uuid"):
            with self.subTest(budget_id=budget_id), self.assertRaisesRegex(AdjustmentContextError, "invalid_basis"):
                self.context(base_budget_id=budget_id)

    def test_read_context_is_json_safe_and_does_not_write_or_create_cycles(self):
        self.make_budget()
        with CaptureQueriesContext(connection) as queries:
            found = self.context()
        json.dumps(found, allow_nan=False)
        self.assertTrue(queries)
        self.assertTrue(all(row["sql"].lstrip().upper().startswith("SELECT") for row in queries), [row["sql"] for row in queries])
        self.assertEqual(found["context_hash"], context_fingerprint(found))
        self.assertEqual(BudgetCycle.objects.filter(user=self.user).count(), 1)

    @override_settings(CHAT_TODAY="2026-10-29")
    def test_fixed_date_recheck_keeps_the_proposed_context_fingerprint(self):
        self.make_budget()
        proposed = get_adjustment_context(self.user, today=date(2026, 10, 29))
        rechecked = get_adjustment_context(self.user)
        self.assertEqual(rechecked["today"], "2026-10-29")
        self.assertEqual(context_fingerprint(proposed), context_fingerprint(rechecked))

    def test_minimum_lock_revision_and_profile_change_expire_fingerprint(self):
        self.make_budget()
        first = self.context()
        CategoryMinimum.objects.create(user=self.user, category_id="FOOD", min_amount=200000)
        second = self.context()
        self.assertNotEqual(first["context_hash"], second["context_hash"])
        self.assertNotEqual(first["protection_hash"], second["protection_hash"])
        BudgetCategorySetting.objects.create(user=self.user, cycle=self.cycle, category_id="FOOD",
            locked=True, locked_amount=400000, revision_no=3)
        third = self.context()
        self.assertEqual(third["locks"], {"FOOD": 400000})
        self.assertEqual(third["lock_revisions"], {"FOOD": 3})
        self.assertNotEqual(second["context_hash"], third["context_hash"])
        FinancialProfile.objects.filter(user=self.user).update(savings_floor=650000)
        self.assertNotEqual(third["context_hash"], self.context()["context_hash"])

    def test_transport_reference_uses_current_vehicle_answer_and_invalidates_old_policy(self):
        self.make_budget(snapshot={"system_floors": {"TRANSPORT_CAR": 55000}})
        fingerprints = []
        for vehicle, expected in ((False, {"TRANSPORT_CAR": 60000}),
                                  (True, {"TRANSPORT_CAR": 100000}), (None, {})):
            with self.subTest(vehicle=vehicle):
                FinancialProfile.objects.filter(user=self.user).update(has_car=vehicle)
                found = self.context()
                self.assertEqual(found["system_floors"], expected)
                self.assertEqual(found["policy_version"], "budget-adjust-20261113-living-first-v5")
                self.assertEqual(found["transport_history_status"], "unavailable")
                fingerprints.append(found["context_hash"])
        self.assertEqual(len(set(fingerprints)), 3)

    def test_legacy_floors_and_current_low_spending_do_not_invent_verified_history(self):
        snapshot = {"adjustment_context": {"system_floors": {
            "TRANSPORT_CAR": 55000, "FOOD": 282900, "MEDICAL_HEALTH": 30000}}}
        budget = self.make_budget(snapshot=snapshot)
        FinancialProfile.objects.filter(user=self.user).update(has_car=False)
        self.tx(1000, "TRANSPORT_CAR")
        found = self.context()
        self.assertEqual(found["system_floors"], {"TRANSPORT_CAR": 60000})
        self.assertEqual(found["transport_history_status"], "unavailable")
        self.assertEqual(found["spent"]["TRANSPORT_CAR"], 1000)
        self.assertEqual(found["allocations"]["TRANSPORT_CAR"], 100000)
        budget.refresh_from_db()
        self.assertEqual(budget.input_snapshot, snapshot)

    def test_reference_is_available_for_existing_budget_without_floor_snapshot(self):
        budget = self.make_budget()
        FinancialProfile.objects.filter(user=self.user).update(has_car=False)
        found = self.context()
        self.assertEqual(found["system_floors"], {"TRANSPORT_CAR": 60000})
        budget.refresh_from_db()
        self.assertEqual(budget.input_snapshot, {})
        self.assertEqual(dict(budget.items.filter(bucket="living").values_list("category_id", "allocated_amount")), AMOUNTS)

    def test_lower_lock_records_ignored_reference_without_changing_inputs_or_erasing_floor(self):
        budget = self.make_budget(snapshot={"system_floors": {"TRANSPORT_CAR": 55000}})
        FinancialProfile.objects.filter(user=self.user).update(has_car=False)
        budget.items.filter(category_id="TRANSPORT_CAR").update(allocated_amount=37777)
        setting = BudgetCategorySetting.objects.create(user=self.user, cycle=self.cycle,
            category_id="TRANSPORT_CAR", locked=True, locked_amount=37777, revision_no=2)
        minimum = CategoryMinimum.objects.create(user=self.user, category_id="TRANSPORT_CAR", min_amount=20000)
        before = FinancialProfile.objects.filter(user=self.user).values().get()
        with CaptureQueriesContext(connection) as queries:
            found = self.context()
        self.assertTrue(all(row["sql"].lstrip().upper().startswith("SELECT") for row in queries))
        self.assertEqual(found["locks"], {"TRANSPORT_CAR": 37777})
        self.assertEqual(found["system_floors"], {"TRANSPORT_CAR": 60000})
        self.assertEqual(found["system_floor_ignored_locked"], [{"category": "TRANSPORT_CAR",
            "reference_amount": 60000, "locked_amount": 37777}])
        self.assertEqual(FinancialProfile.objects.filter(user=self.user).values().get(), before)
        setting.refresh_from_db()
        minimum.refresh_from_db()
        budget.refresh_from_db()
        self.assertEqual((setting.locked, setting.locked_amount, setting.revision_no), (True, 37777, 2))
        self.assertEqual(minimum.min_amount, 20000)
        self.assertEqual(budget.input_snapshot, {"system_floors": {"TRANSPORT_CAR": 55000}})
        self.assertEqual(budget.items.get(category_id="TRANSPORT_CAR").allocated_amount, 37777)

    def test_legacy_lock_without_amount_is_not_compared_or_replaced_with_reference(self):
        self.make_budget()
        FinancialProfile.objects.filter(user=self.user).update(has_car=False)
        setting = BudgetCategorySetting.objects.create(user=self.user, cycle=self.cycle,
            category_id="TRANSPORT_CAR", locked=True, locked_amount=None)
        with self.assertRaisesRegex(AdjustmentContextError, "invalid_budget_data"):
            self.context()
        setting.refresh_from_db()
        self.assertIsNone(setting.locked_amount)

    def test_unknown_collection_is_not_presented_as_zero_available(self):
        self.make_budget()
        self.coverage.return_value = {"state": "partial", "targets": [], "undetermined": []}
        found = self.context()
        self.assertEqual(found["spending_status"], "partial")
        self.assertEqual(found['bucket_status'], {'SAVINGS': 'ready', 'SPARE': 'unknown', 'CUSHION_MONTHLY': 'unknown'})
        self.assertEqual(found['bucket_available'], {'SAVINGS': 600000})

    def test_spare_uses_living_overrun_and_free_ledger_without_double_counting(self):
        budget = self.make_budget()
        tx = self.tx(1100000)
        SpendingAllocation.objects.create(user=self.user, cycle=self.cycle, budget=budget, transaction=tx,
            funding_bucket="free", category_id="FOOD", amount=100000)
        found = self.context()
        self.assertEqual(found["spent"]["FOOD"], 1100000)
        self.assertEqual(found["bucket_available"]["SPARE"], 300000)
        outside = self.tx(50000, "TRAVEL_LODGING")
        SpendingAllocation.objects.create(user=self.user, cycle=self.cycle, budget=budget, transaction=outside,
            funding_bucket="free", category_id="TRAVEL_LODGING", amount=50000)
        self.assertEqual(self.context()["bucket_available"]["SPARE"], 250000)

    def test_cycle_funding_does_not_reuse_profile_spare_and_protects_usage_and_reservations(self):
        budget = self.make_budget()
        budget.items.filter(bucket="living", category_id="FOOD").update(allocated_amount=500000)
        outside = self.tx(50000, "TRAVEL_LODGING")
        SpendingAllocation.objects.create(user=self.user, cycle=self.cycle, budget=budget, transaction=outside,
            funding_bucket="free", category_id="TRAVEL_LODGING", amount=50000)
        with patch("budgets.adjustment_diagnostics.reserved_spare", return_value=(20000, "synthetic-reserve")):
            found = self.context()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.living_budget, 1000000)
        self.assertEqual(sum(found["allocations"].values()), 1100000)
        self.assertEqual(found["buckets"]["SPARE"], 300000)
        self.assertEqual(found["bucket_available"]["SPARE"], 230000)

    def test_savings_budget_is_independent_of_executed_savings_and_transfers(self):
        self.make_budget()
        self.tx(150000, "ETC", transaction_type="saving")
        found = self.context()
        self.assertEqual(found["bucket_available"]["SAVINGS"], 600000)
        self.assertEqual(found["bucket_floors"]["SAVINGS"], 0)
        revision = found['bucket_revisions']['SAVINGS']
        self.tx(10000, "ETC", transaction_type="transfer")
        self.tx(20000, "ETC", transaction_type="own_transfer")
        found = self.context()
        self.assertEqual(found["bucket_status"]["SAVINGS"], "ready")
        self.assertEqual(found["bucket_available"]["SAVINGS"], 600000)
        self.assertEqual(found['bucket_revisions']['SAVINGS'], revision)
        from .adjustment_core.funding import simulate_funding
        from .test_adjustment_core import envelope, item
        result = simulate_funding(found, envelope(item('FOOD', 550000), sources=['SAVINGS']))
        self.assertEqual(result['status'], 'partial', result)
        self.assertEqual(result['buckets']['SAVINGS'], 180000)

    def test_registered_savings_minimum_does_not_require_transfer_schedule(self):
        self.make_budget()
        RecurringSaving.objects.create(user=self.user, name="정기 저축", amount=200000, status="confirmed", source_type="manual")
        found = self.context()
        self.assertEqual(found["bucket_floors"]["SAVINGS"], 200000)
        self.assertEqual(found["bucket_status"]["SAVINGS"], "ready")
        self.assertEqual(found['bucket_available']['SAVINGS'], 400000)

    def test_registered_minimum_is_not_increased_by_executed_savings(self):
        self.make_budget()
        bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        account = BankAccount.objects.create(user=self.user, connection=bank, source_account_key="context-saving", account_type="demand")
        RecurringSaving.objects.create(user=self.user, name="정기 저축", amount=200000, transfer_day=10,
            from_account=account, status="confirmed", source_type="manual")
        self.tx(150000, "ETC", transaction_type="saving")
        found = self.context()
        self.assertEqual(found["bucket_floors"]["SAVINGS"], 200000)
        self.assertEqual(found["bucket_available"]["SAVINGS"], 400000)

    def test_monthly_cushion_protects_cycle_allocations_and_does_not_read_accumulated_use_as_funds(self):
        budget = self.make_budget()
        CushionEntry.objects.create(user=self.user, cycle=self.cycle, budget=budget, entry_type="allocation",
            amount=150000, request_key="context-cushion", occurred_at=timezone.now())
        found = self.context()
        self.assertEqual(found["bucket_available"]["CUSHION_MONTHLY"], 50000)
        self.balance.return_value["total"] = 1000000
        CushionEntry.objects.all().delete()
        found = self.context()
        self.assertEqual(found["bucket_floors"]["CUSHION_MONTHLY"], 1)
        self.assertEqual(found["bucket_available"]["CUSHION_MONTHLY"], 199999)

    def test_missing_and_temporary_references_are_never_claimed_to_be_ml(self):
        budget = self.make_budget(snapshot={"engine_input": {"target": 950000}})
        found = self.context()
        self.assertEqual(found["ml_reference_status"], "unavailable")
        self.assertNotIn("ml_reference", found)
        for source in ("fallback", "temp-fallback", "Synthetic-test", None):
            with self.subTest(source=source):
                budget.input_snapshot.update(ml_reference={code: AMOUNTS[code] for code in LIVING_CODES if code != "ETC"},
                    ml_reference_meta={"source": source, "model_id": "temp", "model_checksum": "0" * 64})
                budget.save(update_fields=["input_snapshot"])
                self.assertEqual(self.context()["ml_reference_status"], "unavailable")

    def test_real_reference_is_frozen_in_derived_snapshot_and_provider_is_not_called(self):
        values = {code: AMOUNTS[code] for code in LIVING_CODES if code != "ETC"}
        meta = {"source": "model", "model_id": "approved-artifact", "model_checksum": "a" * 64}
        self.make_budget(snapshot={"adjustment_context": {"ml_reference": values, "ml_reference_meta": meta,
                                                         "engine_input": {"target": 950000}}})
        provider = Mock(side_effect=AssertionError("frozen ML must not be regenerated"))
        found = self.context(reference_provider=provider)
        self.assertEqual(found["ml_reference"], values)
        self.assertEqual(found["ml_reference_status"], "ready")
        provider.assert_not_called()

    def test_compatibility_provider_is_explicit_cached_and_failure_leaves_direct_sources_usable(self):
        self.make_budget(snapshot={"engine_input": {"target": 950000}})
        ratios = {code: 1 / 8 for code in LIVING_CODES if code != "ETC"}
        provider = Mock(return_value={"ratios": ratios, "model_id": "real-artifact", "model_checksum": "a" * 64})
        cache = {}
        found = self.context(reference_provider=provider, reference_cache=cache)
        self.assertEqual(found["ml_reference"]["FOOD"], 118750)
        self.assertEqual(self.context(reference_provider=provider, reference_cache=cache)["ml_reference"], found["ml_reference"])
        self.assertEqual(provider.call_count, 1)
        failed = self.context(reference_provider=Mock(side_effect=AssertionError("saved reference must be reused")))
        self.assertEqual(failed["ml_reference_status"], "ready")
        self.assertEqual(failed["bucket_status"]["SPARE"], "ready")

    def test_reference_backfill_is_persisted_once_and_never_uses_adjusted_target(self):
        initial = self.make_budget(snapshot={'engine_input': {'target': 950001}})
        ratios = {c: 1/8 for c in LIVING_CODES if c != 'ETC'}
        provider = Mock(return_value={'ratios': ratios, 'model_id': 'fixture-artifact', 'model_checksum': 'a'*64})
        first = self.context(reference_provider=provider)
        initial.refresh_from_db()
        self.assertTrue(initial.input_snapshot['ml_reference_backfill_attempted'])
        self.assertEqual(initial.input_snapshot['ml_reference']['FOOD'], 118751)
        initial.status = 'superseded'
        initial.save(update_fields=['status'])
        self.make_budget(base=initial, snapshot={'engine_input': {'target': 1500000}})
        found = self.context(reference_provider=provider)
        self.assertEqual(found['ml_reference'], first['ml_reference'])
        self.assertEqual(found['ml_reference_meta']['target'], 950001)
        self.assertEqual(provider.call_count, 1)

    def test_failed_backfill_is_not_retried_and_does_not_store_provider_error(self):
        initial = self.make_budget(snapshot={'engine_input': {'target': 950000}})
        provider = Mock(side_effect=OSError('private model path'))
        for _ in range(2):
            self.assertEqual(self.context(reference_provider=provider)['ml_reference_status'], 'unavailable')
        self.assertEqual(provider.call_count, 1)
        initial.refresh_from_db()
        self.assertNotIn('private', str(initial.input_snapshot))

    def test_approval_recheck_cannot_start_backfill_or_write_attempt_marker(self):
        initial = self.make_budget(snapshot={'engine_input': {'target': 950000}})
        provider = Mock(side_effect=AssertionError('approval must never invoke a model'))
        found = self.context(reference_provider=provider, allow_reference_backfill=False)
        self.assertEqual(found['ml_reference_status'], 'unavailable')
        provider.assert_not_called()
        initial.refresh_from_db()
        self.assertNotIn('ml_reference_backfill_attempted', initial.input_snapshot)

    def test_missing_initial_input_never_uses_derived_target_to_recreate_reference(self):
        initial = self.make_budget(status='superseded', snapshot={})
        self.make_budget(base=initial, snapshot={'engine_input': {'target': 1500000}})
        provider = Mock(side_effect=AssertionError('derived target must not be used'))
        self.assertEqual(self.context(reference_provider=provider)['ml_reference_status'], 'unavailable')
        provider.assert_not_called()

    def test_transport_summary_is_used_only_when_verified_and_lock_has_priority(self):
        ref = {'status': 'ready', 'months': ['2026-08', '2026-09', '2026-10'],
               'baseline_numerator': 90001, 'baseline_denominator': 3,
               'history_fingerprint': 'a'*64, 'version': 'one-off-category-mad10-dominance3-v1'}
        budget = self.make_budget(snapshot={'transport_history_reference': ref})
        self.profile.has_car = True
        self.profile.save(update_fields=['has_car'])
        self.assertEqual(self.context()['system_floors']['TRANSPORT_CAR'], 30000)
        BudgetCategorySetting.objects.create(user=self.user, cycle=self.cycle,
            category_id='TRANSPORT_CAR', locked=True, locked_amount=12345)
        found = self.context()
        self.assertEqual(found['locks']['TRANSPORT_CAR'], 12345)
        self.assertEqual(found['system_floor_ignored_locked'][0]['reference_amount'], 30000)
        for bad in ({'status': 'unavailable'}, {'baseline_denominator': 0}, {'baseline_numerator': True},
                    {'months': ['2026-08', '2026-10', '2026-11']}):
            budget.input_snapshot = {'transport_history_reference': {**ref, **bad}}
            budget.save(update_fields=['input_snapshot'])
            self.assertEqual(self.context()['transport_history_status'], 'unavailable')
            self.assertEqual(self.context()['system_floors']['TRANSPORT_CAR'], 100000)

    def test_initial_draft_input_change_is_expired_without_writing_status(self):
        draft = self.make_budget(status="validated")
        draft.input_hash = hash_snapshot(budget_input_snapshot(self.user, self.cycle, profile=self.profile, today=TODAY, card=False))
        draft.save(update_fields=["input_hash"])
        self.assertEqual(self.context()["base_budget_id"], str(draft.pk))
        CategoryMinimum.objects.create(user=self.user, category_id="FOOD", min_amount=100000)
        with self.assertRaisesRegex(AdjustmentContextError, "expired"):
            self.context(base_budget_id=draft.pk, mode="INITIAL_ADJUSTMENT")
        draft.refresh_from_db()
        self.assertEqual(draft.status, "validated")

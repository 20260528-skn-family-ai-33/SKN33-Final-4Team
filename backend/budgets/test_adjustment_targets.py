"""Next-cycle soft target inputs and temporary recommendation money units."""
from copy import deepcopy
from datetime import date

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from chat.models import AsyncTask
from users.models import FinancialProfile

from .models import BudgetCategorySetting, BudgetCycle
from .recommend import (ALLOCATION_CODES, EngineCheckFailed, SUMMARY, UNIT_POLICY,
                        budget_input_snapshot, check_result, hash_snapshot, recommend_category_budgets)


def inputs(**changes):
    return {"target": 1000000, "minimums": {}, "locks": {}, "category_targets": {}, **changes}


class SoftTargetAllocationTest(SimpleTestCase):
    def calculate(self, inp):
        saved = deepcopy(inp)
        result = recommend_category_budgets(inp)
        check_result(inp, result)
        self.assertEqual(inp, saved)
        if result["status"] == "proposed":
            self.assertEqual(sum(result["allocations"].values()) + result["unallocated"], inp["target"])
            self.assertTrue(all(amount % 5000 == 0 for code, amount in result["allocations"].items() if code not in inp["locks"]))
        return result

    def test_no_targets_preserves_existing_temporary_ratio_behavior(self):
        result = self.calculate(inputs())
        self.assertEqual(result["summary"], SUMMARY)
        self.assertEqual(result["unallocated"], 0)
        self.assertTrue(all(amount % 10000 == 0 for amount in result["allocations"].values()))

    def test_target_is_a_separate_start_and_not_a_minimum_or_lock(self):
        inp = inputs(category_targets={"FOOD": 500000})
        result = self.calculate(inp)
        self.assertEqual(result["allocations"]["FOOD"], 500000)
        self.assertEqual(inp["minimums"], {})
        self.assertEqual(inp["locks"], {})
        self.assertIn("출발 금액", result["summary"])
        self.assertEqual(result["unit_policy"], UNIT_POLICY)

    def test_five_thousand_target_is_honored_and_remaining_categories_share_existing_ratios(self):
        result = self.calculate(inputs(category_targets={"FOOD": 415000}))
        self.assertEqual(result["allocations"]["FOOD"], 415000)
        self.assertEqual(result["unallocated"], 0)

    def test_minimum_55001_is_raised_to_60000_without_modifying_the_saved_minimum(self):
        inp = inputs(target=100000, minimums={"FOOD": 55001})
        result = self.calculate(inp)
        self.assertEqual(result["allocations"]["FOOD"], 60000)
        self.assertEqual(inp["minimums"]["FOOD"], 55001)

    def test_rounded_protection_cannot_be_silently_funded_beyond_total(self):
        result = self.calculate(inputs(target=55001, minimums={"FOOD": 55001}))
        self.assertEqual(result["status"], "infeasible")
        self.assertEqual(result["conflicts"][0]["shortfall"], 4999)

    def test_sub_unit_residual_is_left_unallocated_for_spare(self):
        result = self.calculate(inputs(target=100123))
        self.assertEqual(result["unallocated"], 123)
        self.assertEqual(sum(result["allocations"].values()), 100000)

    def test_exact_locked_amount_is_preserved_and_other_budgets_have_valid_units(self):
        result = self.calculate(inputs(locks={"FOOD": 37777}))
        self.assertEqual(result["allocations"]["FOOD"], 37777)
        self.assertEqual(result["unallocated"], 2223)

    def test_target_below_minimum_requires_review_instead_of_turning_into_a_lock(self):
        result = self.calculate(inputs(minimums={"FOOD": 60000}, category_targets={"FOOD": 50000}))
        conflict = result["conflicts"][0]
        self.assertEqual(result["status"], "infeasible")
        self.assertEqual((conflict["type"], conflict["reason"]), ("target_conflict", "minimum_amount"))
        self.assertEqual((conflict["requested_amount"], conflict["possible_amount"], conflict["diff"]), (50000, 60000, 10000))

    def test_target_conflicting_with_true_lock_requires_review(self):
        result = self.calculate(inputs(locks={"FOOD": 500000}, category_targets={"FOOD": 400000}))
        conflict = result["conflicts"][0]
        self.assertEqual(conflict["reason"], "locked_amount")
        self.assertEqual((conflict["requested_amount"], conflict["possible_amount"]), (400000, 500000))
        matching = self.calculate(inputs(locks={"FOOD": 37777}, category_targets={"FOOD": 37777}))
        self.assertEqual(matching["allocations"]["FOOD"], 37777)

    def test_total_conflict_reports_aggregate_gap_without_inventing_a_cut_order(self):
        result = self.calculate(inputs(category_targets={"FOOD": 600000, "FASHION_SHOPPING": 600000}))
        conflict = result["conflicts"][0]
        self.assertEqual(conflict["reason"], "living_budget")
        self.assertEqual((conflict["requested_amount"], conflict["possible_amount"], conflict["diff"]), (1200000, 1000000, -200000))
        self.assertEqual(result["allocations"], {})

    def test_total_conflict_capacity_keeps_exact_lock_but_only_offers_legal_free_units(self):
        result = self.calculate(inputs(target=100000, locks={"FOOD": 37777}, category_targets={"BEAUTY": 65000}))
        conflict = result["conflicts"][0]
        self.assertEqual(conflict["reason"], "living_budget")
        self.assertEqual((conflict["requested_amount"], conflict["possible_amount"], conflict["diff"]), (65000, 60000, -5000))

    def test_non_grid_or_invalid_legacy_target_is_explicitly_reviewed(self):
        result = self.calculate(inputs(category_targets={"FOOD": 35555}))
        conflict = result["conflicts"][0]
        self.assertEqual((conflict["reason"], conflict["possible_amount"], conflict["diff"]), ("amount_unit", 35000, -555))
        for targets in ({"FOOD": True}, {"FOOD": "400000"}, {"TRAVEL_LODGING": 50000}, []):
            with self.subTest(targets=targets):
                result = self.calculate(inputs(category_targets=targets))
                self.assertEqual(result["conflicts"][0]["reason"], "invalid_target")

    def test_non_grid_target_with_fractional_minimum_only_suggests_a_legal_unit(self):
        result = self.calculate(inputs(minimums={"FOOD": 55001}, category_targets={"FOOD": 55555}))
        conflict = result["conflicts"][0]
        self.assertEqual(conflict["reason"], "minimum_amount")
        self.assertEqual(conflict["possible_amount"], 60000)
        self.assertEqual(conflict["possible_amount"] % 5000, 0)
        self.assertGreaterEqual(conflict["possible_amount"], 55001)

    def test_target_results_cannot_be_replaced_by_an_unapproved_start(self):
        inp = inputs(category_targets={"FOOD": 400000})
        result = self.calculate(inp)
        result["allocations"]["FOOD"] += 5000
        result["allocations"]["CULTURE_LEISURE"] -= 5000
        with self.assertRaisesRegex(EngineCheckFailed, "유지 목표"):
            check_result(inp, result)

    def test_existing_untagged_design_result_keeps_its_previous_validator_contract(self):
        inp = inputs()
        result = self.calculate(inp)
        result.pop("unit_policy")
        result["allocations"]["FOOD"] += 1
        result["allocations"]["TRANSPORT_CAR"] -= 1
        check_result(inp, result)


class TargetSnapshotTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email="next-cycle-target@example.com")
        cls.profile = FinancialProfile.objects.create(user=cls.user, payday=25, monthly_take_home_income=2000000,
            living_budget=1000000, savings_floor=500000, cushion_monthly=100000, housing_type="family")
        cls.cycle = BudgetCycle.objects.create(user=cls.user, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25))

    def snapshot(self):
        self.profile.refresh_from_db()
        return budget_input_snapshot(self.user, self.cycle, profile=self.profile, today=date(2026, 11, 5), card=False)

    def test_goal_is_in_next_cycle_engine_input_and_hash_changes_when_set_or_cleared(self):
        initial = self.snapshot()
        self.profile.budget_category_targets = {"FOOD": 400000}
        self.profile.save(update_fields=["budget_category_targets"])
        targeted = self.snapshot()
        self.assertEqual(targeted["category_targets"], {"FOOD": 400000})
        self.assertEqual(targeted["engine_input"]["category_targets"], {"FOOD": 400000})
        self.assertEqual(recommend_category_budgets(targeted["engine_input"])["allocations"]["FOOD"], 400000)
        self.assertNotEqual(hash_snapshot(initial), hash_snapshot(targeted))
        self.profile.budget_category_targets = {}
        self.profile.save(update_fields=["budget_category_targets"])
        cleared = self.snapshot()
        self.assertNotEqual(hash_snapshot(targeted), hash_snapshot(cleared))
        self.assertEqual(hash_snapshot(initial), hash_snapshot(cleared))

    def test_etc_target_is_reserved_upstream_without_a_minimum_or_lock_row(self):
        self.profile.budget_category_targets = {"ETC": 50000, "FOOD": 400000}
        self.profile.save(update_fields=["budget_category_targets"])
        snapshot = self.snapshot()
        result = recommend_category_budgets(snapshot["engine_input"])
        check_result(snapshot["engine_input"], result)
        self.assertEqual(snapshot["etc"], 50000)
        self.assertEqual(snapshot["engine_input"]["target"], 950000)
        self.assertEqual(result["allocations"]["FOOD"], 400000)
        self.assertEqual(sum(result["allocations"].values()) + result["unallocated"] + snapshot["etc"], 1000000)
        self.assertEqual(snapshot["minimums"], {})
        self.assertEqual(snapshot["locks"], {})

    def test_etc_target_conflict_has_explicit_review_data(self):
        self.profile.budget_category_targets = {"ETC": 1100000}
        self.profile.save(update_fields=["budget_category_targets"])
        snapshot = self.snapshot()
        result = recommend_category_budgets(snapshot["engine_input"])
        check_result(snapshot["engine_input"], result)
        self.assertEqual(result["status"], "infeasible")
        conflict = result["conflicts"][0]
        self.assertEqual((conflict["categories"], conflict["reason"]), (["ETC"], "living_budget"))
        self.assertEqual((conflict["requested_amount"], conflict["possible_amount"], conflict["diff"]), (1100000, 1000000, -100000))

    def test_etc_target_conflict_capacity_is_on_grid_even_when_living_has_residual(self):
        self.profile.living_budget = 1000123
        self.profile.budget_category_targets = {"ETC": 1005000}
        self.profile.save(update_fields=["living_budget", "budget_category_targets"])
        result = recommend_category_budgets(self.snapshot()["engine_input"])
        conflict = result["conflicts"][0]
        self.assertEqual(conflict["reason"], "living_budget")
        self.assertEqual((conflict["possible_amount"], conflict["diff"]), (1000000, -5000))

    def test_exact_etc_lock_overrides_no_target_and_remains_an_exact_exception(self):
        BudgetCategorySetting.objects.create(user=self.user, cycle=self.cycle, category_id="ETC", locked=True, locked_amount=37777)
        snapshot = self.snapshot()
        result = recommend_category_budgets(snapshot["engine_input"])
        check_result(snapshot["engine_input"], result)
        self.assertEqual(snapshot["etc"], 37777)
        self.assertEqual(result["unallocated"], 2223)
        self.assertTrue(set(result["allocations"]) == set(ALLOCATION_CODES))

    def test_existing_recommendation_save_keeps_residual_in_spare_and_budget_items_on_grid(self):
        from .api import save_recommended_budget
        self.profile.living_budget = 1000123
        self.profile.budget_category_targets = {"FOOD": 415000}
        self.profile.save(update_fields=["living_budget", "budget_category_targets"])
        snapshot = self.snapshot()
        result = recommend_category_budgets(snapshot["engine_input"])
        check_result(snapshot["engine_input"], result)
        self.assertEqual(result["unallocated"], 123)
        task = AsyncTask.objects.create(user=self.user, task_type="budget_recommend", request_key="target-recommend",
            input_hash=hash_snapshot(snapshot), status="running")
        expected_spare = 2000000 - 1000123 - 500000 - 100000
        budget = save_recommended_budget(task, self.cycle, snapshot, result, expected_spare)
        amounts = list(budget.items.values_list("allocated_amount", flat=True))
        self.assertTrue(all(amount % 5000 == 0 for amount in amounts))
        self.assertEqual(budget.items.get(bucket="living", category_id="FOOD").allocated_amount, 415000)
        self.assertEqual(budget.funding_amount - sum(amounts), expected_spare + 123)

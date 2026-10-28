"""추천 예산 계약 테스트: python manage.py test budgets.test_recommend"""
import copy
import json
import random
import uuid
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from chat.models import AsyncTask
from finance.models import Card, Loan, RecurringExpense, RecurringSaving
from users.models import CategoryMinimum, CategoryPriority, FinancialProfile, IdempotencyRecord

from .models import Budget, BudgetCategorySetting, BudgetCycle
from .recommend import (
    CATEGORY_CODES, FALLBACK_RATIOS, SUMMARY, EngineCheckFailed,
    budget_input_hash, check_result, hash_snapshot, recommend_category_budgets,
)


URL = "/api/budgets/recommend/"
TODAY = date(2026, 10, 28)


def engine_input(target=1_060_000, **fields):
    inp = dict(age=27, sex="female", has_vehicle=False, take_home_income=2_800_000,
               housing_fixed=600_000, target=target, priorities=[], minimums={}, locks={}, input_version=1)
    inp.update(fields)
    return inp


class CategoryRecommendationTest(SimpleTestCase):
    def test_fallback_amounts_and_total(self):
        inp = engine_input()
        out = recommend_category_budgets(inp)
        self.assertEqual(sum(FALLBACK_RATIOS.values()), 1)
        self.assertEqual(out["allocations"], {code: int(ratio * 1_060_000) for code, ratio in FALLBACK_RATIOS.items()})
        self.assertEqual(out["summary"], SUMMARY)
        self.assertEqual(out["reasons"], [])
        self.assertEqual(out["conflicts"], [])
        check_result(inp, out)

    def test_minimum_155000_is_not_rounded_down(self):
        for target in (1_000_000, 1_510_000):
            # 두 번째는 주거 비율 몫 약 156,698원: 내림하면 최소 155,000원을 깨는 경계.
            inp = engine_input(target, minimums={"HOUSING_TELECOM": 155_000})
            out = recommend_category_budgets(inp)
            self.assertGreaterEqual(out["allocations"]["HOUSING_TELECOM"], 155_000)
            self.assertEqual(sum(out["allocations"].values()), target)
            check_result(inp, out)

    def test_locks_are_exact_and_remainder_goes_to_largest_free_ratio(self):
        inp = engine_input(1_060_007, locks={"FOOD": 333_333})
        out = recommend_category_budgets(inp)
        self.assertEqual(out["allocations"]["FOOD"], 333_333)
        self.assertEqual(out["allocations"]["CULTURE_LEISURE"] % 10_000, 6_674)
        check_result(inp, out)

    def test_minimums_and_locks_over_target_are_infeasible(self):
        inp = engine_input(100_000, locks={"FOOD": 80_000}, minimums={"FOOD": 50_000, "BEAUTY": 30_000})
        out = recommend_category_budgets(inp)
        self.assertEqual(out["status"], "infeasible")
        self.assertEqual(out["conflicts"], [{"type": "minimums_over_living", "shortfall": 10_000}])
        check_result(inp, out)

    def test_lock_below_minimum(self):
        inp = engine_input(locks={"FOOD": 100_000}, minimums={"FOOD": 155_000})
        out = recommend_category_budgets(inp)
        self.assertEqual(out["status"], "infeasible")
        self.assertEqual(out["conflicts"], [{"type": "lock_below_minimum", "category": "FOOD",
                                            "locked_amount": 100_000, "min_amount": 155_000}])
        check_result(inp, out)

    def test_all_locked_mismatch_in_both_directions_and_exact_match(self):
        locks = dict.fromkeys(CATEGORY_CODES, 10_000)
        for target in (90_000, 110_000):
            inp = engine_input(target, locks=locks)
            out = recommend_category_budgets(inp)
            self.assertEqual(out["conflicts"], [{"type": "locks_mismatch_living", "difference": 100_000 - target}])
            check_result(inp, out)
        inp = engine_input(100_000, locks=locks)
        self.assertEqual(recommend_category_budgets(inp)["allocations"], locks)

    def test_varied_feasible_inputs_preserve_every_constraint(self):
        rng = random.Random(1028)
        for index in range(300):
            minimums = {code: rng.randrange(0, 200_000) for code in CATEGORY_CODES}
            locks = {code: minimums[code] + rng.randrange(0, 100_000) for code in CATEGORY_CODES[:index % 10]}
            needed = sum(locks.get(code, minimums[code]) for code in CATEGORY_CODES)
            inp = engine_input(needed + rng.randrange(0, 1_000_000), minimums=minimums, locks=locks)
            original = copy.deepcopy(inp)
            out = recommend_category_budgets(inp)
            self.assertEqual(out["status"], "proposed")
            check_result(inp, out)
            self.assertEqual(inp, original)
        for target in (0, 1, 9_999):
            inp = engine_input(target)
            check_result(inp, recommend_category_budgets(inp))

    def test_engine_validation_rejects_bad_results(self):
        inp = engine_input(minimums={"BEAUTY": 55_000}, locks={"FOOD": 330_000})
        good = recommend_category_budgets(inp)
        bad = [None, {}, {**good, "status": "unchanged"}, {**good, "reasons": None}]
        for code, amount in (("BEAUTY", -1), ("BEAUTY", True), ("BEAUTY", 55_000.0), ("FOOD", 330_001)):
            value = copy.deepcopy(good)
            value["allocations"][code] = amount
            bad.append(value)
        for code in ("BEAUTY", "FOOD"):
            value = copy.deepcopy(good)
            value["allocations"][code] -= 10_000
            value["allocations"]["CULTURE_LEISURE"] += 10_000
            bad.append(value)
        value = copy.deepcopy(good)
        del value["allocations"]["BEAUTY"]
        bad.append(value)
        value = copy.deepcopy(good)
        value["allocations"]["ETC"] = 0
        bad.append(value)
        bad.append({"status": "infeasible", "allocations": {}, "reasons": [], "summary": "부족",
                    "conflicts": [{"type": "minimums_over_living", "shortfall": 1}]})
        for value in bad:
            with self.subTest(value=value), self.assertRaises(EngineCheckFailed):
                check_result(inp, value)


class RecommendAPITest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email="recommend@example.com")
        cls.other = get_user_model().objects.create_user(email="other@example.com")
        cls.profile = FinancialProfile.objects.create(
            user=cls.user, housing_type="alone", monthly_take_home_income=2_800_000, payday=25,
            living_budget=1_120_000, savings_floor=610_000, cushion_monthly=200_000, cushion_target=8_400_000,
            birth_date=date(1998, 10, 29), gender="female", has_car=False,
        )
        cls.card = Card.objects.create(user=cls.user, origin="manual", name="카드")
        for amount, category, card in ((580_000, "HOUSING_TELECOM", None), (20_000, "HOUSING_TELECOM", None), (100_000, "FOOD", cls.card)):
            RecurringExpense.objects.create(user=cls.user, name="고정", amount=amount, category_id=category,
                                            status="confirmed", source_type="manual", payment_card=card)
        cls.loan = Loan.objects.create(user=cls.user, origin="manual", status="repaying", monthly_payment=70_000)
        cls.saving = RecurringSaving.objects.create(user=cls.user, name="적금", amount=300_000, status="confirmed", source_type="manual")
        cls.cycle = BudgetCycle.objects.create(user=cls.user, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25))
        CategoryPriority.objects.create(user=cls.user, category_id="FOOD", rank=1)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        clock = patch("budgets.api.timezone.localdate", return_value=TODAY)
        clock.start()
        self.addCleanup(clock.stop)

    def post(self, key=None, body=None):
        return self.client.post(URL, data=json.dumps({} if body is None else body), content_type="application/json",
                                HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()))

    def task(self, response):
        self.assertEqual(response.status_code, 202, response.content)
        task = AsyncTask.objects.get(pk=response.json()["task"]["taskId"])
        self.assertEqual(response.json()["task"]["status"], task.status)
        return task

    def minimum(self, code, amount):
        return CategoryMinimum.objects.create(user=self.user, category_id=code, min_amount=amount)

    def lock(self, code, amount):
        return BudgetCategorySetting.objects.create(user=self.user, cycle=self.cycle, category_id=code, locked=True, locked_amount=amount)

    def budget(self, status="draft", cycle=None, user=None, version=None):
        cycle = cycle or self.cycle
        return Budget.objects.create(user=user or self.user, cycle=cycle, status=status,
                                     version_no=version or cycle.budgets.count() + 1)

    def assert_not_ready(self, response):
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["error"]["code"], "not_ready")
        self.assertNotIn("fields", response.json()["error"])
        self.assertFalse(AsyncTask.objects.exists())
        self.assertFalse(IdempotencyRecord.objects.exists())

    def test_success_budget_items_spare_and_expiration(self):
        previous = [self.budget(status) for status in ("draft", "validated", "approved", "rejected")]
        old_cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 9, 25), ends_before=date(2026, 10, 25))
        old = self.budget(cycle=old_cycle)
        other_cycle = BudgetCycle.objects.create(user=self.other, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25))
        other = self.budget(cycle=other_cycle, user=self.other)
        self.minimum("BEAUTY", 155_000)
        self.lock("FOOD", 333_333)
        key = str(uuid.uuid4())
        task = self.task(self.post(key))
        self.assertEqual(task.status, "succeeded")
        self.assertEqual((task.task_type, task.request_key), ("budget_recommend", key))
        self.assertIsNotNone(task.started_at)
        self.assertIsNotNone(task.completed_at)
        self.assertIsNone(task.error_code)
        budget = Budget.objects.get(task=task)
        self.assertEqual((budget.status, budget.version_no, budget.funding_amount), ("validated", 5, 2_800_000))
        self.assertEqual((budget.engine_version, budget.policy_version), ("temp-fallback-1", "2026-10-27"))
        self.assertIsNotNone(budget.validated_at)
        self.assertEqual(task.input_hash, budget.input_hash)
        self.assertEqual(task.input_hash, budget_input_hash(self.user, self.cycle))
        self.assertEqual(task.result, {"outcome": "proposed", "budget_id": str(budget.pk), "summary": SUMMARY, "conflicts": []})
        living = dict(budget.items.filter(bucket="living").values_list("category_id", "allocated_amount"))
        self.assertEqual(set(living), set(CATEGORY_CODES) | {"ETC"})
        self.assertEqual(sum(living.values()), 1_120_000)
        self.assertEqual(living["FOOD"], 333_333)
        self.assertGreaterEqual(living["BEAUTY"], 155_000)
        self.assertEqual(living["ETC"], 0)
        fixed = budget.items.filter(bucket="fixed")
        self.assertEqual(fixed.get(category_id="HOUSING_TELECOM").allocated_amount, 600_000)
        self.assertEqual(fixed.get(category_id="FOOD").allocated_amount, 100_000)
        self.assertEqual(fixed.get(category_detail="loan").allocated_amount, 70_000)
        self.assertFalse(fixed.filter(category_detail="card_reserved").exists())
        self.assertEqual(budget.items.get(bucket="savings").allocated_amount, 610_000)
        self.assertEqual(budget.items.get(bucket="cushion").allocated_amount, 200_000)
        self.assertEqual(budget.funding_amount - budget.items.aggregate(total=Sum("allocated_amount"))["total"], 100_000)
        self.assertEqual(budget.items.exclude(user=self.user).count(), 0)
        for obj in previous + [old, other]:
            obj.refresh_from_db()
        self.assertEqual([obj.status for obj in previous], ["expired", "expired", "approved", "rejected"])
        self.assertEqual((old.status, other.status), ("draft", "draft"))
        inp = budget.input_snapshot["engine_input"]
        self.assertEqual((inp["age"], inp["sex"], inp["has_vehicle"], inp["housing_fixed"]), (27, "female", False, 600_000))
        self.assertEqual(inp["priorities"], [{"category": "FOOD", "rank": 1}])

    def test_card_reserved_line_and_spare_use_same_amount(self):
        with patch("budgets.protect.card_reserved_amount", return_value=30_000):
            task = self.task(self.post())
        budget = Budget.objects.get(task=task)
        self.assertEqual(budget.items.get(category_detail="card_reserved").allocated_amount, 30_000)
        self.assertEqual(budget.funding_amount - budget.items.aggregate(total=Sum("allocated_amount"))["total"], 70_000)

    def test_not_ready_missing(self):
        for field in ("housing_type", "monthly_take_home_income", "payday"):
            original = getattr(self.profile, field)
            setattr(self.profile, field, None)
            self.profile.save()
            self.assert_not_ready(self.post())
            setattr(self.profile, field, original)
            self.profile.save()

    def test_not_ready_blocked(self):
        for income in (700_000, 1_070_000):
            self.profile.monthly_take_home_income = income
            self.profile.save()
            self.assert_not_ready(self.post())

    def test_not_ready_unsaved(self):
        self.profile.living_budget = None
        self.profile.save()
        self.assert_not_ready(self.post())

    def test_not_ready_saved_values_no_longer_valid(self):
        for field, value in (("savings_floor", 299_999), ("living_budget", 2_030_000), ("cushion_monthly", 0)):
            original = getattr(self.profile, field)
            setattr(self.profile, field, value)
            self.profile.save()
            self.assert_not_ready(self.post())
            setattr(self.profile, field, original)
            self.profile.save()

    def test_pending_task_reuse(self):
        for status in ("queued", "running"):
            pending = AsyncTask.objects.create(user=self.user, request_key=str(uuid.uuid4()), task_type="budget_recommend",
                                               status=status, input_hash="pending")
            with patch("budgets.recommend.recommend_category_budgets") as engine:
                task = self.task(self.post())
            self.assertEqual(task.pk, pending.pk)
            engine.assert_not_called()
            self.assertFalse(Budget.objects.exists())
            pending.status = "succeeded"
            pending.save()

    def test_other_users_or_task_types_do_not_block_recommendation(self):
        for user, kind in ((self.other, "budget_recommend"), (self.user, "budget_adjust")):
            AsyncTask.objects.create(user=user, request_key=str(uuid.uuid4()), task_type=kind, status="running", input_hash="x")
        task = self.task(self.post())
        self.assertEqual(task.status, "succeeded")
        self.assertEqual(Budget.objects.count(), 1)

    def test_same_request_key_replays_even_after_inputs_change(self):
        key = str(uuid.uuid4())
        first = self.post(key)
        self.profile.living_budget = None
        self.profile.save()
        second = self.post(key)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(second.status_code, 202)
        self.assertEqual((AsyncTask.objects.count(), Budget.objects.count(), IdempotencyRecord.objects.count()), (1, 1, 1))
        conflict = self.post(key, {"extra": True})
        self.assertEqual((conflict.status_code, conflict.json()["error"]["code"]), (409, "idempotency_conflict"))

    def test_required_key_and_empty_body(self):
        response = self.client.post(URL, {}, format="json")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (400, "invalid"))
        for body in ([1], {"target": 1}):
            response = self.post(body=body)
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (400, "invalid"))
        self.assertFalse(AsyncTask.objects.exists())

    def test_etc_minimum_is_removed_from_target(self):
        self.minimum("ETC", 60_000)
        task = self.task(self.post())
        budget = Budget.objects.get(task=task)
        self.assertEqual(budget.input_snapshot["engine_input"]["target"], 1_060_000)
        self.assertEqual(budget.items.get(bucket="living", category_id="ETC").allocated_amount, 60_000)
        self.assertNotIn("ETC", budget.input_snapshot["engine_input"]["minimums"])

    def test_etc_lock_takes_precedence_over_minimum(self):
        self.minimum("ETC", 60_000)
        self.lock("ETC", 90_000)
        task = self.task(self.post())
        budget = Budget.objects.get(task=task)
        self.assertEqual(budget.input_snapshot["engine_input"]["target"], 1_030_000)
        self.assertEqual(budget.items.get(bucket="living", category_id="ETC").allocated_amount, 90_000)
        self.assertNotIn("ETC", budget.input_snapshot["engine_input"]["locks"])

    def test_etc_over_living_is_infeasible_without_expiring_draft(self):
        self.lock("ETC", 1_120_001)
        draft = self.budget()
        task = self.task(self.post())
        self.assertEqual(task.status, "succeeded")
        self.assertEqual(task.result["conflicts"], [{"type": "minimums_over_living", "shortfall": 1}])
        self.assertEqual(task.result["outcome"], "infeasible")
        self.assertIsNone(task.result["budget_id"])
        draft.refresh_from_db()
        self.assertEqual(draft.status, "draft")
        self.assertEqual(Budget.objects.count(), 1)

    def test_locks_below_minimum_include_etc(self):
        for code in ("FOOD", "ETC"):
            minimum = self.minimum(code, 155_000)
            lock = self.lock(code, 100_000)
            task = self.task(self.post())
            self.assertEqual(task.status, "succeeded")
            self.assertEqual(task.result["outcome"], "infeasible")
            self.assertEqual(task.result["conflicts"], [{"type": "lock_below_minimum", "category": code,
                                                        "locked_amount": 100_000, "min_amount": 155_000}])
            self.assertFalse(Budget.objects.exists())
            lock.delete()
            minimum.delete()

    def test_all_locks_mismatch_is_infeasible(self):
        for code in CATEGORY_CODES:
            self.lock(code, 100_000)
        task = self.task(self.post())
        self.assertEqual(task.status, "succeeded")
        self.assertEqual(task.result["conflicts"], [{"type": "locks_mismatch_living", "difference": -120_000}])
        self.assertFalse(Budget.objects.exists())

    def test_minimums_over_living_is_infeasible(self):
        self.minimum("FOOD", 1_120_001)
        task = self.task(self.post())
        self.assertEqual(task.result["outcome"], "infeasible")
        self.assertEqual(task.result["conflicts"], [{"type": "minimums_over_living", "shortfall": 1}])
        self.assertEqual(task.status, "succeeded")
        self.assertFalse(Budget.objects.exists())

    def test_invalid_engine_result_fails_task_without_budget_changes(self):
        draft = self.budget()
        bad = recommend_category_budgets(engine_input(1_120_000))
        bad["allocations"]["FOOD"] += 1
        with patch("budgets.recommend.recommend_category_budgets", return_value=bad):
            task = self.task(self.post())
        self.assertEqual((task.status, task.error_code), ("failed", "engine_check_failed"))
        self.assertIsNone(task.result)
        self.assertIsNotNone(task.completed_at)
        draft.refresh_from_db()
        self.assertEqual(draft.status, "draft")
        self.assertEqual(Budget.objects.count(), 1)

    def test_engine_cannot_mutate_inputs_to_evade_validation(self):
        self.minimum("FOOD", 700_000)
        def engine(inp):
            inp["minimums"] = {}
            return recommend_category_budgets(inp)
        with patch("budgets.recommend.recommend_category_budgets", side_effect=engine):
            task = self.task(self.post())
        self.assertEqual((task.status, task.error_code), ("failed", "engine_check_failed"))
        self.assertFalse(Budget.objects.exists())

    def test_line_total_mismatch_fails_task(self):
        with patch("budgets.recommend.fixed_breakdown", return_value={"categories": {"FOOD": 1}, "loan": 0, "card_reserved": 0}):
            task = self.task(self.post())
        self.assertEqual((task.status, task.error_code), ("failed", "engine_check_failed"))
        self.assertFalse(Budget.objects.exists())

    def test_partial_budget_write_is_rolled_back_but_task_remains(self):
        draft = self.budget()
        with patch("budgets.models.BudgetItem.objects.bulk_create", side_effect=EngineCheckFailed("항목 검증 실패")):
            task = self.task(self.post())
        self.assertEqual(task.status, "failed")
        self.assertEqual(Budget.objects.count(), 1)
        draft.refresh_from_db()
        self.assertEqual(draft.status, "draft")

    def test_hash_is_sorted_and_tracks_required_inputs(self):
        task = self.task(self.post())
        snapshot = Budget.objects.get(task=task).input_snapshot
        self.assertEqual(hash_snapshot(snapshot), hash_snapshot(dict(reversed(list(snapshot.items())))))
        for field in ("living_budget", "savings_floor", "cushion_monthly", "monthly_take_home_income"):
            original = getattr(self.profile, field)
            setattr(self.profile, field, original + 1)
            self.profile.save()
            self.assertNotEqual(budget_input_hash(self.user, self.cycle), task.input_hash)
            setattr(self.profile, field, original)
            self.profile.save()
        for obj, field in ((self.loan, "monthly_payment"), (self.saving, "amount")):
            original = getattr(obj, field)
            setattr(obj, field, original + 1)
            obj.save()
            self.assertNotEqual(budget_input_hash(self.user, self.cycle), task.input_hash)
            setattr(obj, field, original)
            obj.save()
        minimum = self.minimum("FOOD", 1)
        self.assertNotEqual(budget_input_hash(self.user, self.cycle), task.input_hash)
        minimum.delete()
        locked = self.lock("FOOD", 1)
        self.assertEqual(budget_input_hash(self.user, self.cycle), task.input_hash)
        locked.delete()
        self.assertEqual(budget_input_hash(self.user, self.cycle), task.input_hash)
        next_cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 11, 25), ends_before=date(2026, 12, 25))
        self.assertNotEqual(budget_input_hash(self.user, next_cycle), task.input_hash)

    def test_unauthenticated_is_403_before_request_key_check(self):
        self.client.force_authenticate(user=None)
        response = self.client.post(URL, {}, format="json")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (403, "not_authenticated"))
        self.assertFalse(AsyncTask.objects.exists())

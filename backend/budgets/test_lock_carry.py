"""자물쇠: 새 주기에 이어받기 · 생활비가 바뀌어 안 맞으면 이번 주기 자물쇠 풀기 (P-13, 10/28)."""
import json
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from finance.models import BankAccount, Connection, RecurringExpense
from users.models import CategoryMinimum, FinancialProfile

from .home import ensure_cycle
from .models import BudgetCategorySetting, BudgetCycle


TODAY = date(2026, 10, 28)
URL = "/api/budgets/protect/"


class LockCarryTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="lock-carry@example.com")
        # 본가 · 실수령 280만 · 고정 70만 → 배분 기준 210만, 저장한 생활비 100만
        self.profile = FinancialProfile.objects.create(
            user=self.user, housing_type="family", monthly_take_home_income=2_800_000, payday=25,
            living_budget=1_000_000, savings_floor=800_000, cushion_monthly=200_000, cushion_target=8_400_000,
        )
        RecurringExpense.objects.create(user=self.user, name="월세", amount=700_000, category_id="HOUSING_TELECOM",
                                        status="confirmed", source_type="manual")
        bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        BankAccount.objects.create(user=self.user, connection=bank, source_account_key="lock-carry", account_type="demand",
                                   currency="KRW", is_selected=True, balance=2_800_000)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        clock = patch("django.utils.timezone.localdate", return_value=TODAY)
        clock.start()
        self.addCleanup(clock.stop)

    def lock(self, cycle, code, amount, change_count=1, locked=True):
        return BudgetCategorySetting.objects.create(user=self.user, cycle=cycle, category_id=code, locked=locked,
                                                    locked_amount=amount if locked else None, change_count=change_count)

    def settings(self, cycle):
        return {s.category_id: (s.locked, s.locked_amount, s.change_count)
                for s in BudgetCategorySetting.objects.filter(user=self.user, cycle=cycle)}

    def put(self, living):
        response = self.client.put(URL, json.dumps({"livingBudget": living, "savingFloor": 800_000, "cushionMonthly": 200_000}),
                                   content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    # ---------- 새 주기에 이어받기 ----------

    def test_new_cycle_inherits_locks_and_restarts_change_count(self):
        previous = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 9, 25), ends_before=date(2026, 10, 25))
        self.lock(previous, "FOOD", 300_000, change_count=2)
        self.lock(previous, "BEAUTY", None, change_count=1, locked=False)  # 푼 카테고리는 안 가져옴
        cycle = ensure_cycle(self.user, date(2026, 10, 25), date(2026, 11, 25))
        self.assertEqual(self.settings(cycle), {"FOOD": (True, 300_000, 0)})
        ensure_cycle(self.user, date(2026, 10, 25), date(2026, 11, 25))  # 이미 있는 주기면 다시 복사하지 않음
        self.assertEqual(BudgetCategorySetting.objects.filter(cycle=cycle).count(), 1)

    def test_first_cycle_has_no_locks(self):
        cycle = ensure_cycle(self.user, date(2026, 10, 25), date(2026, 11, 25))
        self.assertEqual(self.settings(cycle), {})

    def test_new_month_via_api_keeps_locks_for_recommendation(self):
        previous = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 9, 25), ends_before=date(2026, 10, 25))
        self.lock(previous, "FOOD", 300_000)
        self.assertEqual(self.client.get(URL).status_code, 200)  # 이번 달 주기가 처음 만들어짐
        cycle = BudgetCycle.objects.get(user=self.user, starts_on=date(2026, 10, 25))
        self.assertEqual(self.settings(cycle), {"FOOD": (True, 300_000, 0)})

    # ---------- 생활비가 바뀌어 안 맞으면 풀기 ----------

    def make_cycle_with_locks(self):
        cycle = ensure_cycle(self.user, date(2026, 10, 25), date(2026, 11, 25))
        self.lock(cycle, "FOOD", 500_000)
        self.lock(cycle, "CULTURE_LEISURE", 400_000)  # 잠긴 금액 합계 90만
        return cycle

    def test_living_below_locks_releases_this_cycle_locks(self):
        cycle = self.make_cycle_with_locks()
        data = self.put(800_000)  # 90만 > 80만 → 추천이 막힘
        self.assertEqual(data["locksReleased"], ["FOOD", "CULTURE_LEISURE"])
        self.assertEqual(self.settings(cycle), {"FOOD": (False, None, 1), "CULTURE_LEISURE": (False, None, 1)})  # 횟수 그대로

    def test_living_change_that_still_fits_keeps_locks(self):
        cycle = self.make_cycle_with_locks()
        data = self.put(950_000)
        self.assertEqual(data["locksReleased"], [])
        self.assertEqual(self.settings(cycle)["FOOD"], (True, 500_000, 1))

    def test_same_living_keeps_locks_even_if_other_values_change(self):
        cycle = self.make_cycle_with_locks()
        CategoryMinimum.objects.create(user=self.user, category_id="FOOD", min_amount=600_000)  # 자물쇠 < 최소 (생활비 문제 아님)
        response = self.client.put(URL, json.dumps({"livingBudget": 1_000_000, "savingFloor": 700_000, "cushionMonthly": 200_000}),
                                   content_type="application/json")
        self.assertEqual(response.json()["locksReleased"], [])
        self.assertTrue(self.settings(cycle)["FOOD"][0])

    def test_minimums_alone_too_big_keeps_locks(self):
        cycle = ensure_cycle(self.user, date(2026, 10, 25), date(2026, 11, 25))
        self.lock(cycle, "FOOD", 100_000)
        CategoryMinimum.objects.create(user=self.user, category_id="HOUSEHOLD", min_amount=900_000)
        data = self.put(800_000)  # 최소 금액만으로도 90만 > 80만 → 자물쇠를 풀어도 소용없음
        self.assertEqual(data["locksReleased"], [])
        self.assertTrue(self.settings(cycle)["FOOD"][0])

    def test_get_has_empty_locks_released(self):
        self.assertEqual(self.client.get(URL).json()["locksReleased"], [])

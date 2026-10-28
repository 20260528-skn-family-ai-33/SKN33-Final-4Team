from datetime import date
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from finance.models import FinancialTransaction
from users.models import FinancialProfile
from .models import Budget, BudgetCycle, CushionEntry, SpendingAllocation


URL = "/api/budgets/cushion/"


class CushionAPITest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="cushion@example.com")
        self.other = get_user_model().objects.create_user(email="other-cushion@example.com")
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.client.get("/api/csrf/")
        self.client.credentials(HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value)

    def profile(self, **changes):
        values = {"monthly_take_home_income": 2_800_000, "cushion_monthly": 210_000, **changes}
        return FinancialProfile.objects.create(user=self.user, **values)

    def entry(self, amount, user=None, entry_type="adjustment", **fields):
        return CushionEntry.objects.create(
            user=user or self.user, amount=amount, entry_type=entry_type,
            request_key=str(uuid4()), occurred_at=timezone.now(), **fields,
        )

    def get(self, query=""):
        response = self.client.get(URL + query)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_document_example(self):
        self.profile()
        self.entry(300_000, entry_type="initial")
        self.assertEqual(self.get(), {
            "missing": [], "cushion": {
                "now": 300_000, "goal": 8_400_000, "goalLabel": "월급의 3배",
                "bar": 3.57, "pct": 4, "monthly": 210_000, "useRule": None,
                "remain": 8_100_000, "eta": "39회 · 약 39개월", "etaMonths": 39,
            },
        })

    def test_anonymous_is_rejected(self):
        response = APIClient().get(URL)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "not_authenticated")
        self.assertNotIn("fields", response.json()["error"])

    def test_get_needs_session_but_not_csrf_token(self):
        self.client.credentials()
        self.get()

    def test_percent_uses_half_up_rounding(self):
        self.profile(monthly_take_home_income=200, cushion_monthly=1)
        self.entry(15)
        data = self.get()["cushion"]
        self.assertEqual((data["bar"], data["pct"]), (2.5, 3))

    def test_missing_profile_does_not_create_data(self):
        data = self.get()
        self.assertEqual(data["missing"], ["takeHomeIncome", "cushionMonthly"])
        self.assertEqual(data["cushion"]["now"], 0)
        for key in ("goal", "bar", "pct", "monthly", "remain", "etaMonths", "useRule"):
            self.assertIsNone(data["cushion"][key])
        self.assertFalse(FinancialProfile.objects.filter(user=self.user).exists())
        self.assertFalse(CushionEntry.objects.exists())
        self.assertFalse(BudgetCycle.objects.exists())

    def test_missing_salary_keeps_known_balance_and_monthly(self):
        self.profile(monthly_take_home_income=None, cushion_target=8_400_000)
        self.entry(20_000)
        data = self.get()
        self.assertEqual(data["missing"], ["takeHomeIncome"])
        self.assertEqual(data["cushion"]["now"], 20_000)
        self.assertEqual(data["cushion"]["monthly"], 210_000)
        self.assertIsNone(data["cushion"]["goal"])

    def test_missing_monthly_is_not_defaulted_to_ten_percent(self):
        self.profile(cushion_monthly=None)
        data = self.get()
        self.assertEqual(data["missing"], ["cushionMonthly"])
        self.assertIsNone(data["cushion"]["monthly"])
        self.assertIsNone(data["cushion"]["etaMonths"])
        self.assertEqual(data["cushion"]["goal"], 8_400_000)

    def test_zero_monthly_has_no_estimate(self):
        self.profile(cushion_monthly=0)
        data = self.get()
        self.assertEqual(data["missing"], [])
        self.assertEqual(data["cushion"]["monthly"], 0)
        self.assertIsNone(data["cushion"]["etaMonths"])

    def test_own_ledger_only_even_with_user_query(self):
        self.profile()
        FinancialProfile.objects.create(user=self.other, monthly_take_home_income=9_000_000, cushion_monthly=1)
        self.entry(100_000)
        self.entry(9_000_000, user=self.other)
        data = self.get(f"?userId={self.other.pk}")["cushion"]
        self.assertEqual((data["now"], data["goal"], data["monthly"]), (100_000, 8_400_000, 210_000))

    def test_all_entry_types_and_closed_cycles_are_summed(self):
        self.profile()
        cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 9, 25),
                                           ends_before=date(2026, 10, 25), status="closed")
        budget = Budget.objects.create(user=self.user, cycle=cycle, version_no=1, status="superseded")
        tx = FinancialTransaction.objects.create(
            user=self.user, source_kind="manual", occurred_on=date(2026, 10, 1), amount=50_000,
            direction="out", transaction_type="spending", confirmation_state="confirmed",
            confirmed_category_id="FOOD",
        )
        allocation = SpendingAllocation.objects.create(
            user=self.user, transaction=tx, cycle=cycle, budget=budget,
            funding_bucket="cushion", category_id="FOOD", amount=50_000,
        )
        self.entry(300_000, entry_type="initial")
        self.entry(100_000, entry_type="allocation", cycle=cycle, budget=budget)
        used = self.entry(-50_000, entry_type="use", allocation=allocation)
        self.entry(10_000, entry_type="refund", allocation=allocation, reference_entry=used)
        self.entry(-20_000)
        self.assertEqual(self.get()["cushion"]["now"], 340_000)

    def test_empty_ledger_is_zero_and_read_does_not_accrue_monthly(self):
        self.profile()
        self.assertEqual(self.get()["cushion"]["now"], 0)
        self.assertEqual(self.get()["cushion"]["now"], 0)
        self.assertFalse(CushionEntry.objects.exists())

    def test_goal_tracks_current_income_without_rewriting_profile(self):
        profile = self.profile(cushion_target=123)
        before = FinancialProfile.objects.filter(pk=profile.pk).values().get()
        self.assertEqual(self.get()["cushion"]["goal"], 8_400_000)
        self.assertEqual(FinancialProfile.objects.filter(pk=profile.pk).values().get(), before)
        profile.monthly_take_home_income = 3_000_000
        profile.save()
        self.assertEqual(self.get()["cushion"]["goal"], 9_000_000)

    def test_goal_reached_and_exceeded(self):
        self.profile(monthly_take_home_income=100, cushion_monthly=0)
        self.entry(300)
        data = self.get()["cushion"]
        self.assertEqual((data["remain"], data["bar"], data["pct"], data["etaMonths"]), (0, 100, 100, 0))
        self.entry(150)
        data = self.get()["cushion"]
        self.assertEqual((data["now"], data["remain"], data["bar"], data["pct"], data["etaMonths"]),
                         (450, 0, 100, 150, 0))

    def test_month_estimate_rounds_up(self):
        self.profile(monthly_take_home_income=100, cushion_monthly=100)
        self.assertEqual(self.get()["cushion"]["etaMonths"], 3)
        self.entry(-1)
        self.assertEqual(self.get()["cushion"]["etaMonths"], 4)

    def test_negative_ledger_is_not_silently_clamped(self):
        self.profile(monthly_take_home_income=100)
        self.entry(-30)
        data = self.get()["cushion"]
        self.assertEqual((data["now"], data["remain"], data["bar"], data["pct"]), (-30, 330, 0, -10))

    def test_zero_income_has_no_goal_or_division_error(self):
        self.profile(monthly_take_home_income=0)
        data = self.get()
        self.assertEqual(data["missing"], ["takeHomeIncome"])
        self.assertIsNone(data["cushion"]["goal"])

    def test_unknown_use_policy_is_not_presented_as_permission(self):
        self.profile(cushion_use_policy="legacy", cushion_enabled=True)
        self.assertIsNone(self.get()["cushion"]["useRule"])

    def test_get_preserves_pending_salary_and_budgets(self):
        profile = self.profile(payday=25, scheduled_payday=10, payday_changes_on=date(2020, 11, 10),
                               payday_previous_start=date(2020, 10, 25), payday_revision=1)
        cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2020, 10, 25), ends_before=date(2020, 11, 10))
        budget = Budget.objects.create(user=self.user, cycle=cycle, version_no=1, status="validated")
        self.get()
        profile.refresh_from_db()
        budget.refresh_from_db()
        self.assertEqual((profile.payday, budget.status), (25, "validated"))
        self.assertEqual(BudgetCycle.objects.filter(user=self.user).count(), 1)

    def test_patch_and_other_writes_are_not_implemented(self):
        self.profile()
        for method in ("patch", "put", "post", "delete"):
            response = getattr(self.client, method)(URL, {"cushionMonthly": 1}, format="json")
            self.assertEqual(response.status_code, 405, response.content)
            self.assertEqual(response.json()["error"]["code"], "method_not_allowed")
        self.assertEqual(self.get()["cushion"]["monthly"], 210_000)

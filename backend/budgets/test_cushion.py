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
                "now": 300_000, "goal": 8_400_000, "goalLabel": "월급의 3배", "goalCustom": False, "defaultGoal": 8_400_000,
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

    def test_other_writes_are_not_implemented(self):
        self.profile()
        for method in ("put", "post", "delete"):
            response = getattr(self.client, method)(URL, {"cushionMonthly": 1}, format="json")
            self.assertEqual(response.status_code, 405, response.content)
            self.assertEqual(response.json()["error"]["code"], "method_not_allowed")
        self.assertEqual(self.get()["cushion"]["monthly"], 210_000)

    def test_patch_goal_amount_changes_goal_and_expires_drafts(self):
        """11/13: 목표 금액을 직접 정한다. 월 적립금은 여기서 못 바꾼다 (지킬 돈 설정)"""
        profile = self.profile()
        cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25))
        draft = Budget.objects.create(user=self.user, cycle=cycle, status="validated", version_no=1)
        response = self.client.patch(URL, {"goalAmount": 5_000_000}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()["cushion"]
        self.assertEqual((body["goal"], body["goalLabel"], body["goalCustom"], body["defaultGoal"], body["remain"]),
                         (5_000_000, "직접 정한 금액", True, 8_400_000, 5_000_000))
        profile.refresh_from_db()
        draft.refresh_from_db()
        self.assertEqual((profile.cushion_goal_amount, profile.cushion_target, draft.status), (5_000_000, 5_000_000, "expired"))
        self.assertEqual(self.client.patch(URL, {"goalAmount": 5_000_000}, format="json").status_code, 200)  # 같은 값도 성공
        # 직접 정한 목표는 월급이 바뀌어도 그대로
        profile.monthly_take_home_income = 3_000_000
        profile.save()
        self.assertEqual(self.get()["cushion"]["goal"], 5_000_000)
        for body in ({"goalAmount": 9_999}, {"goalAmount": 1_000_000_001}, {"goalAmount": "5000000"}, {"goalAmount": 5_000_000.5},
                     {"goalAmount": True}, {}, {"cushionMonthly": 1}, {"goalAmount": 5_000_000, "goal": 1}):
            with self.subTest(body=body):
                self.assertEqual(self.client.patch(URL, body, format="json").status_code, 400)

    def test_patch_null_goes_back_to_default(self):
        """null = 기본(월급의 3배)으로 되돌리기. 다시 월급을 따라간다"""
        profile = self.profile(cushion_goal_amount=5_000_000)
        response = self.client.patch(URL, {"goalAmount": None}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()["cushion"]
        self.assertEqual((body["goal"], body["goalLabel"], body["goalCustom"]), (8_400_000, "월급의 3배", False))
        profile.refresh_from_db()
        self.assertEqual((profile.cushion_goal_amount, profile.cushion_target), (None, 8_400_000))

    def test_custom_goal_without_income(self):
        """급여가 없어도 직접 정한 목표는 보여 준다 (기본 목표는 계산 못 함)"""
        self.profile(monthly_take_home_income=None, cushion_goal_amount=3_000_000)
        body = self.get()["cushion"]
        self.assertEqual((body["goal"], body["goalCustom"], body["defaultGoal"]), (3_000_000, True, None))

    def test_goal_amount_changes_required_cushion_on_protect(self):
        """쿠션 필수(P-08)도 지금 목표 기준: 잔액이 목표보다 적으면 필수. goal 을 안 넘기면 월급 × 3 (혜리 코드)"""
        from .protect import cushion_goal, cushion_required
        self.assertFalse(cushion_required(9_000_000, 2_800_000))               # 3배(840만)보다 많음
        self.assertTrue(cushion_required(9_000_000, 2_800_000, 10_000_000))    # 직접 정한 1,000만보다 적음
        self.assertTrue(cushion_required(9_000_000, None))                     # 목표를 모르면 필수
        self.assertEqual(cushion_goal(FinancialProfile(cushion_goal_amount=1_000_000), 2_800_000), 1_000_000)
        self.assertEqual(cushion_goal(FinancialProfile(), 2_800_000), 8_400_000)
        self.assertIsNone(cushion_goal(None, None))

    def test_patch_without_profile_is_not_ready(self):
        self.assertEqual(self.client.patch(URL, {"goalAmount": 5_000_000}, format="json").status_code, 409)


class ApprovedCushionLedgerTest(TestCase):
    """R27 (11/13): 예산을 승인하면 쿠션 배정액을 보호 중인 쿠션에 쌓고, 같은 주기는 차액만"""

    def setUp(self):
        from .models import BudgetItem
        self.BudgetItem = BudgetItem
        self.user = get_user_model().objects.create_user(email="ledger@example.com")
        FinancialProfile.objects.create(user=self.user, monthly_take_home_income=2_800_000, cushion_monthly=300_000)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def approved(self, cycle, amount, version):
        budget = Budget.objects.create(user=self.user, cycle=cycle, status="approved", version_no=version,
                                       funding_amount=2_800_000, approved_at=timezone.now())
        self.BudgetItem.objects.create(user=self.user, budget=budget, bucket="cushion", allocated_amount=amount)
        return budget

    def now(self):
        return self.client.get(URL).json()["cushion"]["now"]

    def test_approval_adds_once_and_same_cycle_reapproval_adds_only_the_difference(self):
        from .cushion import record_approved_cushion
        oct_ = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 9, 25), ends_before=date(2026, 10, 25), status="closed")
        nov = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25))
        first = self.approved(oct_, 900_000, 1)
        record_approved_cushion(first)
        record_approved_cushion(first)  # 재시도 · 중복 호출은 한 번만
        self.assertEqual(self.now(), 900_000)
        b1 = self.approved(nov, 300_000, 1)
        record_approved_cushion(b1)
        self.assertEqual(self.now(), 1_200_000)  # 90만 + 이번 주기 30만
        b1.status = "superseded"
        b1.save(update_fields=["status"])
        b2 = self.approved(nov, 200_000, 2)
        record_approved_cushion(b2)
        self.assertEqual(self.now(), 1_100_000)  # 같은 주기 20만으로 다시 승인 → 차액 −10만
        self.assertEqual(list(CushionEntry.objects.filter(cycle=nov).order_by("created_at").values_list("amount", flat=True)), [300_000, -100_000])

    def test_every_approval_path_records_planned_cushion_on_save(self):
        """검수 P1 (11/13): 일반 승인 · 대화형 조정 · 분배 실패 복구 모두 budget.status='approved' 로 save 하므로
        signals.py 가 같은 기록을 남긴다. 종류는 planned — 예산 조정이 줄이지 못하는 '이미 적립한 돈'(allocation)과 다르다 (검수 P2)"""
        nov = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25))
        budget = Budget.objects.create(user=self.user, cycle=nov, status="validated", version_no=1, funding_amount=2_800_000)
        self.BudgetItem.objects.create(user=self.user, budget=budget, bucket="cushion", allocated_amount=300_000)
        budget.status, budget.approved_at = "approved", timezone.now()
        budget.save(update_fields=["status", "approved_at"])
        budget.save(update_fields=["validation_summary"])  # 승인 뒤 다른 칸 저장은 다시 쌓지 않는다
        self.assertEqual(self.now(), 300_000)
        self.assertEqual(list(CushionEntry.objects.values_list("entry_type", "amount")), [("planned", 300_000)])
        self.assertFalse(CushionEntry.objects.filter(entry_type="allocation").exists())

    def test_zero_cushion_budget_records_nothing(self):
        from .cushion import record_approved_cushion
        cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25))
        self.assertIsNone(record_approved_cushion(self.approved(cycle, 0, 1)))
        self.assertFalse(CushionEntry.objects.exists())

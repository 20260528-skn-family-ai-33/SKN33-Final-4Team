"""지킬 돈 계약 테스트. 실행: python manage.py test budgets.test_protect"""
import json
import random
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from finance.models import BankAccount, Card, Connection, Loan, RecurringExpense, RecurringSaving
from users.models import FinancialProfile

from .models import Budget, BudgetCycle
from .protect import blocked, cushion_required, fill_cushion, fixed_total, recommend, savings_min


URL = "/api/budgets/protect/"
TODAY = date(2026, 10, 28)
MONEY_FIELDS = ("living_budget", "saving_floor", "cushion_monthly", "spare")


class ProtectCalculationTest(SimpleTestCase):
    def test_document_examples(self):
        cases = [
            (2_030_000, "family", 1_000_000, (770_000, 1_000_000, 170_000, 90_000), True, 1_030_000, 810_000),
            (2_030_000, "alone", 0, (1_120_000, 610_000, 200_000, 100_000), False, None, 610_000),
            (13_000, "family", 0, (0, 10_000, 3_000, 0), False, None, 10_000),
        ]
        for available, housing, minimum, amounts, rebalanced, rest, ratio in cases:
            with self.subTest(available=available, housing=housing):
                result = recommend(available, housing, minimum)
                self.assertEqual(tuple(result[key] for key in MONEY_FIELDS), amounts)
                self.assertEqual((result["rebalanced"], result["rest_amount"], result["ratio_saving_floor"]),
                                 (rebalanced, rest, ratio))

    def test_cushion_uses_spare_then_living_and_preserves_saving(self):
        self.assertEqual(fill_cushion(8_000, 10_000, 0, 4_000, 0), (2_000, 10_000, 10_000, 0))
        self.assertEqual(fill_cushion(0, 10_000, 0, 3_000, 0), (0, 10_000, 3_000, 0))
        self.assertEqual(fill_cushion(3_000, 10_000, 0, 0, 0), (0, 10_000, 3_000, 0))

    def test_cushion_uses_only_excess_saving_when_living_and_spare_are_zero(self):
        # 현재 비율 추천에선 나오지 않는 배분도 순수 보정 함수에서 검사한다.
        self.assertEqual(fill_cushion(0, 20_000, 0, 0, 5_000), (0, 10_000, 10_000, 0))
        self.assertEqual(fill_cushion(0, 13_000, 0, 0, 10_000), (0, 10_000, 3_000, 0))
        self.assertEqual(fill_cushion(0, 10_000, 0, 0, 10_000), (0, 10_000, 0, 0))

    def test_optional_cushion_and_half_up_rounding(self):
        result = recommend(13_000, "family", 0, cushion_required=False)
        self.assertEqual(tuple(result[key] for key in MONEY_FIELDS), (3_000, 10_000, 0, 0))
        # 25,000원은 Python round의 짝수 반올림(20,000원)이 아니라 30,000원.
        self.assertEqual(recommend(62_500, "family", 0)["ratio_saving_floor"], 30_000)

    def test_allocation_invariants(self):
        rng = random.Random(1028)
        amounts = list(range(1, 40_001, 97)) + [1, 9_999, 10_000, 12_499, 12_500, 13_000, 99_999, 100_000, 2_030_000]
        amounts += [rng.randrange(1, 1_000_000_000) for _ in range(500)]
        for available in amounts:
            for housing in ("alone", "family"):
                for minimum in (0, available // 3, available // 2, available - 1):
                    for required in (True, False):
                        with self.subTest(available=available, housing=housing, minimum=minimum, required=required):
                            result = recommend(available, housing, minimum, cushion_required=required)
                            values = [result[key] for key in MONEY_FIELDS]
                            self.assertEqual(sum(values), available)
                            self.assertTrue(all(type(value) is int and value >= 0 for value in values))
                            self.assertGreaterEqual(result["saving_floor"], minimum)
                            if required:
                                self.assertGreater(result["cushion_monthly"], 0)

    def test_blocked_boundaries_and_priority(self):
        for available, minimum, reason, shortfall in (
            (-10, 100, "fixed_over_income", 10), (0, 100, "fixed_over_income", 0),
            (50, 100, "savings_over_available", 50), (100, 100, "savings_over_available", 0),
        ):
            self.assertEqual(blocked(available, minimum), {"reason": reason, "shortfall": shortfall})
            with self.assertRaises(ValueError):
                recommend(available, "family", minimum)
        self.assertIsNone(blocked(101, 100))

    def test_cushion_threshold(self):
        self.assertTrue(cushion_required(None, 2_800_000))
        self.assertTrue(cushion_required(8_399_999, 2_800_000))
        self.assertFalse(cushion_required(8_400_000, 2_800_000))
        self.assertFalse(cushion_required(8_400_001, 2_800_000))


class ProtectAPITest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email="protect@example.com")
        cls.other = get_user_model().objects.create_user(email="other@example.com")
        cls.profile = FinancialProfile.objects.create(
            user=cls.user, housing_type="family", monthly_take_home_income=2_800_000, payday=25,
        )
        cls.expense = RecurringExpense.objects.create(
            user=cls.user, name="월세", amount=700_000, category_id="HOUSING_TELECOM",
            status="confirmed", source_type="manual", payment_day=None,
        )
        cls.loan = Loan.objects.create(user=cls.user, origin="manual", status="repaying", monthly_payment=70_000)
        cls.saving = RecurringSaving.objects.create(
            user=cls.user, name="적금", amount=1_000_000, status="confirmed", source_type="manual",
        )
        cls.bank = Connection.objects.create(
            user=cls.user, business_type="bank", organization_code="0004", status="connected",
        )
        cls.account = BankAccount.objects.create(
            user=cls.user, connection=cls.bank, source_account_key="protect", account_type="demand",
            currency="KRW", is_selected=True, balance=2_800_000,
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.clock = patch("budgets.api.timezone.localdate", return_value=TODAY)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def put(self, body=None):
        if body is None:
            body = {"livingBudget": 770_000, "savingFloor": 1_000_000, "cushionMonthly": 170_000}
        return self.client.put(URL, data=json.dumps(body), content_type="application/json")

    def make_budget(self, status="draft", user=None, starts_on=date(2026, 10, 25)):
        user = user or self.user
        cycle, _ = BudgetCycle.objects.get_or_create(
            user=user, starts_on=starts_on,
            defaults={"ends_before": date(2026, 11, 25), "expected_payday": date(2026, 11, 25)},
        )
        return Budget.objects.create(user=user, cycle=cycle, version_no=cycle.budgets.count() + 1, status=status)

    def assert_error(self, response, status, field=None):
        self.assertEqual(response.status_code, status, response.content)
        error = response.json()["error"]
        self.assertEqual(error["code"], "not_ready" if status == 409 else "invalid")
        if field:
            self.assertEqual(set(error["fields"]), {field})
        if status == 409:
            self.assertNotIn("fields", error)
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.living_budget)
        self.assertIsNone(self.profile.savings_floor)
        self.assertIsNone(self.profile.cushion_monthly)
        self.assertIsNone(self.profile.cushion_target)
        return error

    def test_get_document_example(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "missing": [], "housingType": "family",
            "rates": {"living": 45, "saving": 40, "spare": 5, "cushion": 10},
            "base": {"takeHomeIncome": 2_800_000, "fixedTotal": 770_000, "available": 2_030_000},
            "savingsMin": 1_000_000,
            "recommended": {"livingBudget": 770_000, "savingFloor": 1_000_000, "cushionMonthly": 170_000,
                            "spare": 90_000, "rebalanced": True, "restAmount": 1_030_000, "ratioSavingFloor": 810_000},
            "blocked": None, "saved": None,
            "cushion": {"required": True, "target": 8_400_000, "balanceTotal": 2_800_000},
            "cardPending": [],
        })
        cycle = BudgetCycle.objects.get(user=self.user, status="active")
        self.assertEqual((cycle.starts_on, cycle.ends_before), (date(2026, 10, 25), date(2026, 11, 25)))

    def test_each_missing_value_skips_fixed_calculation(self):
        for field, name in (("housing_type", "housingType"), ("monthly_take_home_income", "takeHomeIncome"), ("payday", "payday")):
            original = getattr(self.profile, field)
            setattr(self.profile, field, None)
            self.profile.save()
            with self.subTest(field=field), patch("budgets.protect.fixed_total") as fixed:
                response = self.client.get(URL)
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["missing"], [name])
                if field == "monthly_take_home_income":
                    self.assertIsNone(data["cushion"]["target"])
                self.assertEqual(data["savingsMin"], 1_000_000)
                for key in ("base", "recommended", "blocked"):
                    self.assertIsNone(data[key])
                self.assertEqual(data["rates"] is None, field == "housing_type")
                fixed.assert_not_called()
                self.assertFalse(BudgetCycle.objects.exists())
            setattr(self.profile, field, original)
            self.profile.save()

    def test_no_profile(self):
        self.profile.delete()
        data = self.client.get(URL).json()
        self.assertEqual(data["missing"], ["housingType", "takeHomeIncome", "payday"])
        self.assertIsNone(data["cushion"]["target"])
        self.assertIsNone(data["saved"])
        self.assertEqual(data["savingsMin"], 1_000_000)
        self.assertTrue(data["cushion"]["required"])
        response = self.put([])
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "not_ready"))
        self.assertFalse(FinancialProfile.objects.filter(user=self.user).exists())

    def test_get_blocked_cases_including_equal_boundaries(self):
        for income, reason, shortfall in (
            (700_000, "fixed_over_income", 70_000), (770_000, "fixed_over_income", 0),
            (1_700_000, "savings_over_available", 70_000), (1_770_000, "savings_over_available", 0),
        ):
            with self.subTest(income=income):
                self.profile.monthly_take_home_income = income
                self.profile.save()
                response = self.client.get(URL)
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["blocked"], {"reason": reason, "shortfall": shortfall})
                self.assertIsNone(data["recommended"])
                self.assertEqual(data["base"]["available"], income - 770_000)

    def test_totals_include_card_fixed_expenses_and_filter_user_status(self):
        card = Card.objects.create(user=self.user, origin="manual", name="신용카드")
        for fields in ({"status": "suggested"}, {"status": "stopped"}, {"status": "ignored"},
                       {"user": self.other}, {"payment_card": card}):
            values = dict(user=self.user, name="제외", amount=100_000, category_id="ETC", status="confirmed", source_type="manual")
            values.update(fields)
            RecurringExpense.objects.create(**values)
        for fields in ({"status": "repaid", "monthly_payment": 100_000}, {"monthly_payment": None},
                       {"user": self.other, "monthly_payment": 100_000}):
            values = dict(user=self.user, origin="manual", status="repaying")
            values.update(fields)
            Loan.objects.create(**values)
        for fields in ({"status": "suggested"}, {"status": "stopped"}, {"status": "ignored"}, {"user": self.other}):
            values = dict(user=self.user, name="제외", amount=100_000, status="confirmed", source_type="manual")
            values.update(fields)
            RecurringSaving.objects.create(**values)
        cycle = self.make_budget().cycle
        with patch("budgets.protect.card_reserved", return_value={"amount": 30_000, "pending": []}) as reserved:
            self.assertEqual(fixed_total(self.user, cycle), 900_000)
            reserved.assert_called_once_with(self.user, cycle)
        self.assertEqual(savings_min(self.user), 1_000_000)
        self.assertEqual(savings_min(get_user_model().objects.create_user(email="empty@example.com")), 0)

    def test_balance_matches_home_and_optional_cushion(self):
        from .home import balance_json
        for balance, required in ((None, True), (8_399_999, True), (8_400_000, False), (8_400_001, False)):
            self.account.balance = balance
            self.account.save()
            data = self.client.get(URL).json()
            self.assertEqual(data["cushion"]["balanceTotal"], balance_json(self.user)["total"])
            self.assertEqual(data["cushion"]["required"], required)
        response = self.put({"livingBudget": 1_000_000, "savingFloor": 1_000_000, "cushionMonthly": 0})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["saved"]["spare"], 30_000)

    def test_put_success_and_same_shape_as_get(self):
        response = self.put()
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data, self.client.get(URL).json())
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.living_budget, self.profile.savings_floor, self.profile.cushion_monthly,
                          self.profile.cushion_target), (770_000, 1_000_000, 170_000, 8_400_000))
        self.assertEqual(data["saved"], {
            "livingBudget": 770_000, "savingFloor": 1_000_000, "cushionMonthly": 170_000, "spare": 90_000,
            "updatedAt": timezone.localtime(self.profile.updated_at).isoformat(),
        })

    def test_put_step_1_missing_precedes_blocked_and_body_errors(self):
        self.profile.housing_type = None
        self.profile.monthly_take_home_income = 1
        self.profile.save()
        error = self.assert_error(self.put([]), 409)
        self.assertEqual(error["message"], "내 정보 · 급여를 먼저 입력해 주세요.")

    def test_put_step_2_blocked_precedes_body_errors(self):
        for income in (700_000, 1_700_000):
            self.profile.monthly_take_home_income = income
            self.profile.save()
            error = self.assert_error(self.put([]), 409)
            self.assertEqual(error["message"], "정기 저축이나 고정지출을 먼저 고쳐 주세요.")

    def test_put_step_3_shape_integer_required_nonnegative_unknown_fields(self):
        valid = {"livingBudget": 770_000, "savingFloor": 1_000_000, "cushionMonthly": 170_000}
        for body in ([], [1], "hello", 42):
            self.assert_error(self.put(body), 400, "nonFieldErrors")
        self.assert_error(self.client.put(URL, data="null", content_type="application/json"), 400, "nonFieldErrors")
        for field in valid:
            for value in (None, True, False, "10000", 1.5, 10_000.0, -1):
                with self.subTest(field=field, value=value):
                    self.assert_error(self.put({**valid, field: value}), 400, field)
            self.assert_error(self.put({key: value for key, value in valid.items() if key != field}), 400, field)
        self.assert_error(self.put({**valid, "spare": 90_000}), 400, "spare")
        # 모양 검사가 저축 하한 · 쿠션 검사보다 먼저다.
        self.assert_error(self.put({"livingBudget": "bad", "savingFloor": 0, "cushionMonthly": 0}), 400, "livingBudget")

    def test_put_step_4_minimum_precedes_cushion_and_total(self):
        error = self.assert_error(self.put({"livingBudget": 3_000_000, "savingFloor": 999_999, "cushionMonthly": 0}),
                                  400, "savingFloor")
        self.assertEqual(error["fields"]["savingFloor"], ["저축 확인에서 고른 1,000,000원보다 적게 정할 수 없어요."])

    def test_put_step_5_required_cushion_precedes_total(self):
        error = self.assert_error(self.put({"livingBudget": 3_000_000, "savingFloor": 1_000_000, "cushionMonthly": 0}),
                                  400, "cushionMonthly")
        self.assertEqual(error["fields"]["cushionMonthly"], ["잔액이 월급의 3배보다 적어서 쿠션을 꼭 적립해야 해요."])

    def test_put_step_6_total(self):
        error = self.assert_error(self.put({"livingBudget": 1_000_000, "savingFloor": 1_000_000, "cushionMonthly": 30_001}),
                                  400, "nonFieldErrors")
        self.assertEqual(error["fields"]["nonFieldErrors"], ["배분 가능한 금액보다 1원 많아요. 입력 금액을 줄여 주세요."])

    def test_recommendations_pass_put_including_small_amounts(self):
        self.expense.delete()
        self.loan.delete()
        self.saving.delete()
        for housing in ("family", "alone"):
            for available in (1, 9_999, 12_500, 13_000, 20_000, 99_999, 2_030_000):
                self.profile.housing_type = housing
                self.profile.monthly_take_home_income = available
                self.profile.save(update_fields=["housing_type", "monthly_take_home_income"])
                self.account.balance = None
                self.account.save()
                data = self.client.get(URL).json()
                body = {key: data["recommended"][key] for key in ("livingBudget", "savingFloor", "cushionMonthly")}
                response = self.put(body)
                self.assertEqual(response.status_code, 200, response.content)

    def test_identical_put_preserves_timestamp_and_drafts(self):
        first = self.put().json()
        draft = self.make_budget()
        validated = self.make_budget("validated")
        self.assertEqual(self.put().json(), first)
        for budget in (draft, validated):
            budget.refresh_from_db()
        self.assertEqual((draft.status, validated.status), ("draft", "validated"))

    def test_changed_put_expires_only_own_draft_and_validated_in_all_cycles(self):
        self.put()
        budgets = [self.make_budget(status) for status in ("draft", "validated", "approved", "rejected", "superseded", "expired")]
        previous = self.make_budget(starts_on=date(2026, 9, 25))
        other = self.make_budget(user=self.other)
        response = self.put({"livingBudget": 760_000, "savingFloor": 1_000_000, "cushionMonthly": 170_000})
        self.assertEqual(response.status_code, 200)
        for budget in budgets + [previous, other]:
            budget.refresh_from_db()
        self.assertEqual([b.status for b in budgets], ["expired", "expired", "approved", "rejected", "superseded", "expired"])
        self.assertEqual((previous.status, other.status), ("expired", "draft"))

    def test_target_change_alone_expires_draft(self):
        self.put()
        draft = self.make_budget()
        self.profile.monthly_take_home_income = 3_000_000
        self.profile.save(update_fields=["monthly_take_home_income"])
        self.assertEqual(self.put().status_code, 200)
        draft.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(draft.status, "expired")
        self.assertEqual(self.profile.cushion_target, 9_000_000)

    def test_validation_failure_preserves_draft(self):
        draft = self.make_budget()
        self.assert_error(self.put({}), 400)
        draft.refresh_from_db()
        self.assertEqual(draft.status, "draft")

    @override_settings(DEBUG=False)
    def test_expiration_failure_rolls_back_profile(self):
        draft = self.make_budget()
        with patch("budgets.api.Budget.objects.filter") as budgets:
            budgets.return_value.update.side_effect = RuntimeError("만료 저장 실패")
            with self.assertLogs("config.api", level="ERROR"):
                response = self.put()
            self.assertEqual(response.status_code, 500)
            self.assertEqual(response.json()["error"]["code"], "server_error")
        self.profile.refresh_from_db()
        draft.refresh_from_db()
        self.assertIsNone(self.profile.living_budget)
        self.assertIsNone(self.profile.cushion_target)
        self.assertEqual(draft.status, "draft")

    def test_anonymous_get_and_put_are_403(self):
        self.client.force_authenticate(user=None)
        for response in (self.client.get(URL), self.put()):
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["error"]["code"], "not_authenticated")
        self.assertFalse(BudgetCycle.objects.exists())

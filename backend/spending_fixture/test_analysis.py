"""소비 분석 기반(spending_analysis) 검증: 분석 기간 순수 함수 · 승인 예산 읽기 전용 조회 · 날짜 경계 시나리오
실행: python manage.py test spending_fixture --settings=spending_fixture.settings
기대값은 손으로 정한 상수다 (서비스 함수로 만들지 않음). 기본 414건은 그대로, 변형은 테스트 안에서만 만든다(롤백).
"""
from datetime import date, time
from unittest import mock

from django.db import OperationalError, connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from budgets.models import Budget, BudgetCycle, BudgetItem
from finance.models import BankAccount, Card, Connection, FinancialTransaction
from spending_analysis.budget import approved_budget
from spending_analysis.periods import Period, budget_period, previous_month_same_days, recent_months, this_month
from users.models import FinancialProfile, User

from . import seed
from .test_fixture import DESIGN, fixture_db_only, summary, user

d = date


@fixture_db_only
class PeriodTest(TestCase):
    def test_this_month(self):
        p = this_month(d(2026, 10, 29))
        self.assertEqual((p, p.last_day), (Period(d(2026, 10, 1), d(2026, 10, 30)), d(2026, 10, 29)))
        self.assertEqual(this_month(d(2026, 10, 1)), Period(d(2026, 10, 1), d(2026, 10, 2)))

    def test_previous_month_same_days(self):
        cases = {
            d(2026, 10, 29): (d(2026, 9, 1), d(2026, 9, 29)),
            d(2026, 10, 31): (d(2026, 9, 1), d(2026, 9, 30)),   # 9/31 없음 → 말일
            d(2026, 3, 31): (d(2026, 2, 1), d(2026, 2, 28)),
            d(2026, 3, 29): (d(2026, 2, 1), d(2026, 2, 28)),
            d(2028, 3, 31): (d(2028, 2, 1), d(2028, 2, 29)),    # 윤년
            d(2028, 3, 29): (d(2028, 2, 1), d(2028, 2, 29)),
            d(2026, 5, 31): (d(2026, 4, 1), d(2026, 4, 30)),
            d(2026, 1, 15): (d(2025, 12, 1), d(2025, 12, 15)),  # 해 넘김
        }
        for today, (start, last) in cases.items():
            with self.subTest(today=today):
                p = previous_month_same_days(today)
                self.assertEqual((p.start, p.last_day), (start, last))

    def test_recent_months_are_completed_months(self):
        self.assertEqual(recent_months(d(2026, 10, 29), 3), [
            Period(d(2026, 7, 1), d(2026, 8, 1)), Period(d(2026, 8, 1), d(2026, 9, 1)), Period(d(2026, 9, 1), d(2026, 10, 1))])
        self.assertEqual(recent_months(d(2026, 1, 10), 2), [
            Period(d(2025, 11, 1), d(2025, 12, 1)), Period(d(2025, 12, 1), d(2026, 1, 1))])
        self.assertEqual(recent_months(d(2028, 3, 31), 1), [Period(d(2028, 2, 1), d(2028, 3, 1))])

    def test_recent_months_rejects_instead_of_correcting(self):
        for bad in (0, 4, 6, -1, "3", 3.0, True, None):
            with self.subTest(months=bad), self.assertRaises(ValueError):
                recent_months(d(2026, 10, 29), bad)

    def test_budget_period_separates_cycle_and_spent_range(self):
        got = budget_period(d(2026, 10, 25), d(2026, 11, 25), d(2026, 10, 29))
        self.assertEqual(got["cycle"], Period(d(2026, 10, 25), d(2026, 11, 25)))
        self.assertEqual((got["spent"], got["spent"].last_day), (Period(d(2026, 10, 25), d(2026, 10, 30)), d(2026, 10, 29)))
        self.assertEqual(budget_period(d(2026, 10, 25), d(2026, 11, 25), d(2026, 10, 25))["spent"],
                         Period(d(2026, 10, 25), d(2026, 10, 26)))  # 급여일 당일
        self.assertEqual(budget_period(d(2026, 10, 25), d(2026, 11, 25), d(2026, 11, 24))["spent"].last_day,
                         d(2026, 11, 24))  # 다음 급여일 직전
        for outside in (d(2026, 10, 24), d(2026, 11, 25)):  # 시작 전날 · 종료 경계(미포함)
            with self.subTest(today=outside), self.assertRaises(ValueError):
                budget_period(d(2026, 10, 25), d(2026, 11, 25), outside)

    def test_reproducible_from_explicit_date(self):
        today = d(2026, 10, 29)
        first = (this_month(today), previous_month_same_days(today), recent_months(today, 3),
                 budget_period(d(2026, 10, 25), d(2026, 11, 25), today))
        class NoToday(date):
            @classmethod
            def today(cls):
                raise AssertionError("현재 날짜를 읽으면 안 된다")

        with mock.patch("spending_analysis.periods.date", NoToday):
            again = (this_month(today), previous_month_same_days(today), recent_months(today, 3),
                     budget_period(d(2026, 10, 25), d(2026, 11, 25), today))
        self.assertEqual(first, again)


@fixture_db_only
class ApprovedBudgetTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def test_a_reads_approved_v1_not_validated_v2(self):
        self.assertTrue(Budget.objects.filter(user=user("A"), status="validated", version_no=2).exists())
        got = approved_budget(user("A"), d(2026, 10, 29))
        self.assertEqual((got["state"], got["budget"]["version_no"]), ("approved", 1))
        self.assertEqual((got["cycle"]["starts_on"], got["cycle"]["ends_before"]), (d(2026, 10, 25), d(2026, 11, 25)))
        self.assertEqual((got["living"]["FOOD"], got["living_total"], got["spare"]), (350_000, 900_000, 200_000))
        self.assertEqual(got["buckets"], {"living": 900_000, "fixed": 700_000, "savings": 500_000, "cushion": 100_000})
        self.assertNotIn("UNCLASSIFIED", got["living"])
        p = budget_period(got["cycle"]["starts_on"], got["cycle"]["ends_before"], d(2026, 10, 29))
        self.assertEqual((p["spent"].start, p["spent"].last_day), (d(2026, 10, 25), d(2026, 10, 29)))

    def test_payday_boundaries(self):
        before, on = approved_budget(user("A"), d(2026, 10, 24)), approved_budget(user("A"), d(2026, 10, 25))
        self.assertEqual(before["cycle"]["starts_on"], d(2026, 9, 25))  # 급여일 전날은 지난 주기
        self.assertEqual(on["cycle"]["starts_on"], d(2026, 10, 25))
        self.assertEqual(approved_budget(user("A"), d(2026, 11, 25)), {"state": "no_cycle"})  # ends_before 미포함
        self.assertEqual(approved_budget(user("A"), d(2026, 6, 24)), {"state": "no_cycle"})

    def test_c_draft_is_no_approved_budget(self):
        c = user("C")
        self.assertEqual(FinancialProfile.objects.get(user=c).living_budget, 800_000)  # 목표는 있어도 대신 쓰지 않음
        got = approved_budget(c, d(2026, 10, 29))
        self.assertEqual(got["state"], "no_approved")
        self.assertEqual(set(got), {"state", "cycle"})

    def test_b_variant_reads_new_approved_version(self):
        before = approved_budget(user("B"), d(2026, 10, 29))
        self.assertEqual((before["budget"]["version_no"], before["living"]["FOOD"]), (1, 400_000))
        seed.variant_b_approved_v2(DESIGN)
        got = approved_budget(user("B"), d(2026, 10, 29))
        self.assertEqual((got["budget"]["version_no"], got["living"]["FOOD"], got["living"]["FASHION_SHOPPING"],
                          got["living_total"]), (2, 500_000, 100_000, 1_300_000))
        self.assertEqual(Budget.objects.get(user=user("B"), cycle__status="active", version_no=1).status, "superseded")
        self.assertEqual(approved_budget(user("A"), d(2026, 10, 29))["living"]["FOOD"], 350_000)

    def test_other_users_items_never_mix_in(self):
        a_budget = Budget.objects.get(user=user("A"), cycle__status="active", status="approved")
        BudgetItem.objects.create(user=user("B"), budget=a_budget, bucket="living", category_id="CULTURE_LEISURE",
                                  category_detail="stray", allocated_amount=9_999_000)
        got = approved_budget(user("A"), d(2026, 10, 29))
        self.assertEqual((got["living"]["CULTURE_LEISURE"], got["living_total"]), (100_000, 900_000))
        self.assertEqual(approved_budget(User.objects.create_user(email="nobody@example.test"), d(2026, 10, 29)),
                         {"state": "no_cycle"})

    def test_read_only(self):
        def state():
            return (sorted(Budget.objects.values_list("budget_id", "status", "version_no")),
                    sorted(BudgetCycle.objects.values_list("cycle_id", "starts_on", "ends_before", "status", "revision_no")),
                    BudgetItem.objects.count(), seed.db_summary(DESIGN))
        before = state()
        with CaptureQueriesContext(connection) as queries:
            for p in "ABC":
                for day in (d(2026, 10, 24), d(2026, 10, 29), d(2026, 12, 1)):
                    approved_budget(user(p), day)
        self.assertEqual(state(), before)
        sql = [q["sql"].upper() for q in queries]
        self.assertTrue(sql and all(s.startswith("SELECT") and "FOR UPDATE" not in s for s in sql))

    def test_db_error_is_its_own_state(self):
        with mock.patch.object(BudgetCycle.objects, "filter", side_effect=OperationalError("down")):
            self.assertEqual(approved_budget(user("A"), d(2026, 10, 29)), {"state": "error", "reason": "db_error"})

    def test_overlapping_cycles_are_not_guessed(self):
        BudgetCycle.objects.create(user=user("C"), starts_on=d(2026, 10, 22), ends_before=d(2026, 11, 22), status="closed")
        self.assertEqual(approved_budget(user("C"), d(2026, 10, 29)), {"state": "error", "reason": "overlapping_cycles"})


@fixture_db_only
class DateBoundaryScenarioTest(TestCase):
    """날짜 경계 시나리오(별도 사용자, 기본 414건과 무관). 날짜마다 서로 다른 2의 거듭제곱 금액이라
    합계만 보고 어느 날이 들어갔는지 알 수 있다"""

    AMOUNTS = {d(2026, 6, 30): 512_000, d(2026, 7, 1): 256_000, d(2026, 8, 31): 128_000, d(2026, 9, 29): 1_000,
               d(2026, 9, 30): 2_000, d(2026, 10, 1): 4_000, d(2026, 10, 24): 8_000, d(2026, 10, 25): 16_000,
               d(2026, 10, 29): 32_000, d(2026, 10, 30): 64_000}

    @classmethod
    def setUpTestData(cls):
        u = cls.user = User.objects.create_user(email="fixture-boundary@example.test")
        conn = Connection.objects.create(user=u, business_type="card", organization_code="FIXTURE_CARD", status="connected")
        bank_conn = Connection.objects.create(user=u, business_type="bank", organization_code="FIXTURE_BANK", status="connected")
        bank = BankAccount.objects.create(user=u, connection=bank_conn, source_account_key="fixture:boundary-bank")
        card = Card.objects.create(user=u, connection=conn, payment_account=bank, origin="provider",
                                   source_card_key="fixture:boundary-card")
        for day, amount in cls.AMOUNTS.items():
            FinancialTransaction.objects.create(
                user=u, source_kind="card", card=card, source_key=f"fixture:boundary:{day}", occurred_on=day,
                occurred_time=time(12), amount=amount, direction="out", transaction_type="spending",
                confirmed_category_id="FOOD", confirmation_state="confirmed")
        cycle = BudgetCycle.objects.create(user=u, starts_on=d(2026, 10, 25), ends_before=d(2026, 11, 25), status="active")
        Budget.objects.create(user=u, cycle=cycle, version_no=1, status="approved", funding_amount=0, engine_version=seed.ENGINE)

    def spent(self, period):
        return summary(self.user, period.start, period.end_before)["spent_total"]

    def test_periods_pick_exact_days(self):
        today = d(2026, 10, 29)
        self.assertEqual(self.spent(this_month(today)), 60_000)                 # 10/1 · 10/24 · 10/25 · 10/29
        self.assertEqual(self.spent(previous_month_same_days(today)), 1_000)    # 9/29 만, 9/30 제외
        self.assertEqual([self.spent(p) for p in recent_months(today, 3)], [256_000, 128_000, 3_000])  # 6/30 · 10월 제외
        got = approved_budget(self.user, today)
        p = budget_period(got["cycle"]["starts_on"], got["cycle"]["ends_before"], today)
        self.assertEqual(self.spent(p["spent"]), 48_000)    # 10/25 · 10/29 (10/24 · 10/30 제외)
        self.assertEqual(self.spent(p["cycle"]), 112_000)   # 주기 전체에는 10/30 이 들어간다 — 사용액 범위와 다름

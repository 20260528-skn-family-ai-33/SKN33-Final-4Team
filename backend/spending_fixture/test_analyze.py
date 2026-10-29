"""소비 분석 1~9번 내부 결과(spending_analysis.analyze) 검증
정답 = data/expected.json(독립 산술) + 손으로 정한 상수. 서비스 함수로 정답을 만들지 않는다.
기본 414건은 그대로, 변형(C 승인 추가 · B 승인 교체 · 비교 · 0원 · 동률)은 테스트 안에서만(롤백).
실행: python manage.py test spending_fixture --settings=spending_fixture.settings
"""
from datetime import date, datetime, time
from decimal import Decimal
from unittest import mock

from django.db import OperationalError, connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from budgets.models import Budget, BudgetCycle, BudgetItem
from finance.models import BankAccount, Card, Connection, FinancialTransaction, SyncRun
from spending_analysis.analyze import analyze
from spending_analysis.periods import Period
from spending_analysis.result import percent
from users.models import User

from . import seed
from .test_fixture import DESIGN, EXPECTED, fixture_db_only, user

d = date
TODAY, AS_OF = d(2026, 10, 29), seed.AS_OF
KEYS = ("total", "fixed_amount", "living_amount", "by_category", "unclassified_amount", "needs_review_count")


def run(u, kind, category=None, **extra):
    return analyze(u, {"type": kind, "category": category, **extra}, today=TODAY, as_of=AS_OF)


def base(fig):
    return {k: fig[k] for k in KEYS}


def pct(value):
    return Decimal(value)


@fixture_db_only
class PercentTest(SimpleTestCase):
    def test_half_up_two_places_and_exact(self):
        self.assertEqual(percent(1, 800), {"value": pct("0.13"), "exact": {"numerator": 100, "denominator": 800}})
        self.assertEqual(percent(-1, 800)["value"], pct("-0.13"))
        self.assertEqual(percent(1, 8)["value"], pct("12.50"))
        self.assertEqual(percent(2, 3)["value"], pct("66.67"))
        self.assertIsNone(percent(5, 0))


@fixture_db_only
class PersonaAnalysisTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def test_current_month_types_1_2_5(self):
        shares = {"A": pct("25.83"), "B": pct("27.51")}
        top = {"A": 700_000, "B": 650_000}
        for p in "AB":
            want = EXPECTED["personas"][p]["current_month_known"]
            with self.subTest(persona=p):
                one = run(user(p), 1)
                self.assertEqual(one["current"]["period"], Period(d(2026, 10, 1), d(2026, 10, 30)))
                self.assertEqual(base(one["current"]["confirmed"]), want)
                two = run(user(p), 2, "FOOD")["current"]["confirmed"]
                self.assertEqual(base(two), want)  # 카테고리 조회여도 total · fixed_amount 는 전체 기준
                self.assertEqual((two["category"]["amount"], two["category"]["share"]["value"]),
                                 (want["by_category"]["FOOD"], shares[p]))
                five = run(user(p), 5)["current"]["confirmed"]
                self.assertEqual(five["top_spending"], [{"category": "HOUSING_TELECOM", "amount": top[p]}])
        self.assertEqual(run(user("A"), 5)["current"]["confirmed"]["shares"]["HOUSING_TELECOM"]["value"], pct("46.36"))

    def test_budget_types_3_4(self):
        for p in "AB":
            want = EXPECTED["personas"][p]["budget"]
            with self.subTest(persona=p):
                four = run(user(p), 4)
                self.assertEqual(four["budget"]["version_no"], 1)
                self.assertEqual(four["confirmed"], {
                    "allocated": want["living_budget"], "spent": want["living_spent"],
                    "difference": want["living_budget"] - want["living_spent"], "remaining": want["living_remaining"],
                    "over": want["over"], "covered_by_spare": want["covered_by_spare"],
                    "spare_remaining": want["spare_remaining"], "shortfall": want["shortfall"]})
                three = run(user(p), 3, "FOOD")["confirmed"]
                diff = want["food_signed_difference"]
                self.assertEqual(three, {"allocated": want["food_budget"], "spent": want["food_spent"], "difference": diff,
                                         "remaining": max(diff, 0), "over": max(-diff, 0)})

    def test_a_approved_v1_and_month_vs_cycle_food(self):
        a = user("A")
        three = run(a, 3, "FOOD")
        self.assertEqual((three["budget"]["version_no"], three["budget"]["allocated"]), (1, 350_000))  # validated v2 아님
        self.assertEqual((three["cycle_period"], three["spent_period"]),
                         (Period(d(2026, 10, 25), d(2026, 11, 25)), Period(d(2026, 10, 25), d(2026, 10, 30))))
        self.assertEqual((run(a, 2, "FOOD")["current"]["confirmed"]["category"]["amount"], three["confirmed"]["spent"]),
                         (390_000, 60_000))

    def test_comparison_types_6_7(self):
        for p in "AB":
            e = EXPECTED["personas"][p]
            with self.subTest(persona=p):
                six = run(user(p), 6)
                self.assertEqual(six["previous"]["period"], Period(d(2026, 9, 1), d(2026, 9, 30)))
                self.assertEqual(base(six["previous"]["confirmed"]), e["previous_same_dates_known"])
                total = six["comparison"]["total"]
                self.assertEqual((total["available"], total["delta"], total["rate"]["exact"]),
                                 (True, e["comparison"]["total_delta"], e["comparison"]["total_rate_exact"]))
                food = run(user(p), 7, "FOOD")["comparison"]["category"]
                self.assertEqual((food["delta"], food["rate"]["exact"]),
                                 (e["comparison"]["food_delta"], e["comparison"]["food_rate_exact"]))
        rates = {p: (run(user(p), 6)["comparison"]["total"]["rate"]["value"],
                     run(user(p), 7, "FOOD")["comparison"]["category"]["rate"]["value"]) for p in "AB"}
        self.assertEqual(rates, {"A": (pct("2.86"), pct("12.07")), "B": (pct("23.38"), pct("55.17"))})

    def test_trend_types_8_9(self):
        for p in "AB":
            monthly = EXPECTED["personas"][p]["monthly_known_sums"]
            with self.subTest(persona=p):
                eight = run(user(p), 8, months_requested=6, months_applied=3)
                self.assertEqual([s["period"].start for s in eight["series"]], [d(2026, 7, 1), d(2026, 8, 1), d(2026, 9, 1)])
                self.assertEqual([s["confirmed"] for s in eight["series"]], [monthly[m]["total"] for m in "789"])
                self.assertEqual(eight["item"]["months_requested"], 6)  # 원래 요청값 그대로
                nine = run(user(p), 9, "FOOD", months_requested=None, months_applied=3)
                self.assertEqual([s["confirmed"] for s in nine["series"]], [monthly[m]["by_category"]["FOOD"] for m in "789"])

    def test_c_partial_history_and_no_approved(self):
        c, e = user("C"), EXPECTED["personas"]["C"]
        one = run(c, 1)["current"]
        self.assertEqual((one["coverage"]["state"], one["confirmed"]), ("partial", None))
        self.assertEqual(base(one["observed"]), e["current_month_known"])  # 관측 390,000 은 확정 총액이 아님
        eight = run(c, 8, months_applied=3)["series"]
        self.assertEqual([(s["state"], s["observed"], s["confirmed"]) for s in eight], [("none", 0, None)] * 3)
        four = run(c, 4)
        self.assertEqual((four["budget"], four["confirmed"], four["observed"]),
                         ({"state": "no_approved"}, None, {"spent": e["current_cycle_living_known"]["living_amount"]}))
        six = run(c, 6)["comparison"]["total"]
        self.assertEqual((six["available"], six["previous_incomplete"], six["current_incomplete"], six["delta"], six["rate"]),
                         (False, True, True, None, None))

    def test_c_approved_budget_alone_does_not_confirm_remaining(self):
        c = user("C")
        draft = Budget.objects.get(user=c)
        approved = Budget.objects.create(user=c, cycle=draft.cycle, version_no=2, status="approved",
                                         funding_amount=draft.funding_amount, engine_version=seed.ENGINE)
        BudgetItem.objects.bulk_create([BudgetItem(user=c, budget=approved, bucket=i.bucket, category_id=i.category_id,
                                                   allocated_amount=i.allocated_amount) for i in draft.items.all()])
        four = run(c, 4)
        self.assertEqual((four["budget"]["allocated"], four["observed"], four["coverage"]["state"], four["confirmed"]),
                         (800_000, {"spent": 290_000}, "partial", None))  # 510,000 잔액을 만들지 않는다
        three = run(c, 3, "FOOD")
        self.assertEqual((three["budget"]["allocated"], three["observed"]["spent"], three["confirmed"]), (300_000, 110_000, None))

    def test_b_duplicate_cancel_and_overspend(self):
        b = run(user("B"), 2, "FASHION_SHOPPING")["current"]["confirmed"]
        self.assertEqual(b["category"]["amount"], 300_000)  # 180,000 한 번 + 120,000 + 0
        self.assertEqual(run(user("B"), 4)["confirmed"]["over"], 62_000)

    def test_b_swapped_approved_budget(self):
        seed.variant_b_approved_v2(DESIGN)
        three = run(user("B"), 3, "FOOD")
        self.assertEqual((three["budget"]["version_no"], three["confirmed"]["allocated"], three["confirmed"]["difference"]),
                         (2, 500_000, -32_000))

    def test_user_isolation(self):
        FinancialTransaction.objects.filter(user=user("B")).update(amount=9_999_000, canceled_amount=None)
        self.assertEqual(base(run(user("A"), 1)["current"]["confirmed"]), EXPECTED["personas"]["A"]["current_month_known"])

    def test_read_only(self):
        before = seed.db_summary(DESIGN), sorted(Budget.objects.values_list("budget_id", "status"))
        with CaptureQueriesContext(connection) as queries:
            for p in "ABC":
                for kind, cat, extra in ((1, None, {}), (2, "FOOD", {}), (3, "FOOD", {}), (4, None, {}), (5, None, {}),
                                         (6, None, {}), (7, "FOOD", {}), (8, None, {"months_applied": 3}),
                                         (9, "FOOD", {"months_applied": 2})):
                    self.assertEqual(run(user(p), kind, cat, **extra)["state"], "ok")
        self.assertEqual((seed.db_summary(DESIGN), sorted(Budget.objects.values_list("budget_id", "status"))), before)
        sql = [q["sql"].upper() for q in queries]
        self.assertTrue(sql and all(s.startswith("SELECT") and "FOR UPDATE" not in s for s in sql))

    def test_db_errors_are_not_zero(self):
        with mock.patch("spending_analysis.analyze.listed_transactions", side_effect=OperationalError("down")):
            self.assertEqual(run(user("A"), 1)["state"], "error")
        with mock.patch.object(BudgetCycle.objects, "filter", side_effect=OperationalError("down")):
            got = run(user("A"), 4)
        self.assertEqual((got["state"], got["reason"]), ("error", "db_error"))
        self.assertNotIn("confirmed", got)
        with mock.patch.object(SyncRun.objects, "filter", side_effect=OperationalError("down")):
            self.assertEqual(run(user("A"), 8, months_applied=3)["state"], "error")

    def test_invalid_input_is_rejected_not_corrected(self):
        a = user("A")
        bad_items = [
            {"type": 0}, {"type": 10}, {"type": "1"}, {"type": True}, {"type": 2}, {"type": 1, "category": "FOOD"},
            {"type": 2, "category": "NOPE"}, {"type": 3, "category": "UNCLASSIFIED"}, {"type": 8},
            {"type": 8, "months_applied": 4}, {"type": 8, "months_applied": 0}, {"type": 9, "category": "FOOD", "months_applied": "3"},
            {"type": 1, "months_applied": 3}, {"type": 8, "months_applied": 3, "months_requested": "6"},
            {"type": 1, "extra": 1},
        ]
        for item in bad_items:
            with self.subTest(item=item), self.assertRaises(ValueError):
                analyze(a, item, today=TODAY, as_of=AS_OF)
        for today, as_of in ((datetime(2026, 10, 29, 12), AS_OF), (TODAY, datetime(2026, 10, 29, 23)),
                             (TODAY, datetime(2026, 10, 28, 23, tzinfo=seed.KST)), ("2026-10-29", AS_OF)):
            with self.subTest(today=today, as_of=as_of), self.assertRaises(ValueError):
                analyze(a, {"type": 1}, today=today, as_of=as_of)


@fixture_db_only
class AnalysisVariantTest(TestCase):
    """별도 사용자(입출금 계좌 1 + 카드 1). 조회 기록 · 거래는 테스트마다 만든다"""
    SEPT, OCT = (d(2026, 9, 1), d(2026, 9, 30)), (d(2026, 10, 1), d(2026, 10, 29))

    def setUp(self):
        u = self.u = User.objects.create_user(email="fixture-analyze@example.test")
        self.conn = {k: Connection.objects.create(user=u, business_type=k, organization_code=f"FIXTURE_{k.upper()}",
                                                  status="connected") for k in ("bank", "card")}
        self.account = BankAccount.objects.create(user=u, connection=self.conn["bank"], source_account_key="fixture:an-bank",
                                                  account_type="demand")
        self.card = Card.objects.create(user=u, connection=self.conn["card"], payment_account=self.account,
                                        origin="provider", source_card_key="fixture:an-card")

    def covered(self, span, card_to=None):
        for kind, product in (("bank", "bank_sync"), ("card", "card_approval_list")):
            SyncRun.objects.create(user=self.u, connection=self.conn[kind], card=self.card if kind == "card" else None,
                                   product_code=product, status="success", covered_from=span[0],
                                   covered_to=card_to if kind == "card" and card_to else span[1])

    def spend(self, day, amount, category="FOOD"):
        FinancialTransaction.objects.create(
            user=self.u, source_kind="card", card=self.card, source_key=f"fixture:an:{day}:{category}:{amount}",
            occurred_on=day, occurred_time=time(12), amount=amount, direction="out", transaction_type="spending",
            confirmed_category_id=category, confirmation_state="confirmed")

    def test_only_current_incomplete(self):
        self.covered(self.SEPT)
        self.covered(self.OCT, card_to=d(2026, 10, 28))
        self.spend(d(2026, 9, 5), 10_000)
        self.spend(d(2026, 10, 5), 20_000)
        got = run(self.u, 6)["comparison"]["total"]
        self.assertEqual((got["available"], got["previous_incomplete"], got["current_incomplete"], got["delta"]),
                         (False, False, True, None))

    def test_only_previous_incomplete(self):
        self.covered(self.SEPT, card_to=d(2026, 9, 20))
        self.covered(self.OCT)
        self.spend(d(2026, 10, 5), 20_000)
        got = run(self.u, 7, "FOOD")["comparison"]["category"]
        self.assertEqual((got["available"], got["previous_incomplete"], got["current_incomplete"], got["rate"]),
                         (False, True, False, None))

    def test_previous_zero_gives_delta_without_rate(self):
        self.covered(self.SEPT)
        self.covered(self.OCT)
        self.spend(d(2026, 10, 5), 15_000)
        got = run(self.u, 6)["comparison"]["total"]
        self.assertEqual((got["available"], got["delta"], got["rate"]), (True, 15_000, None))

    def test_real_zero_spending(self):
        self.covered(self.OCT)
        five = run(self.u, 5)["current"]["confirmed"]
        self.assertEqual((five["total"], five["top_spending"], five["shares"]), (0, None, {}))
        two = run(self.u, 2, "FOOD")["current"]["confirmed"]["category"]
        self.assertEqual((two["amount"], two["share"]), (0, None))

    def test_tie_for_top_excludes_unclassified(self):
        self.covered(self.OCT)
        self.spend(d(2026, 10, 3), 10_000, "TRANSPORT_CAR")
        self.spend(d(2026, 10, 4), 10_000, "FOOD")
        self.spend(d(2026, 10, 5), 30_000, "UNCLASSIFIED")
        five = run(self.u, 5)["current"]["confirmed"]
        self.assertEqual(five["top_spending"], [{"category": "FOOD", "amount": 10_000},
                                                {"category": "TRANSPORT_CAR", "amount": 10_000}])
        self.assertEqual({k: v["value"] for k, v in five["shares"].items()},
                         {"FOOD": pct("20.00"), "TRANSPORT_CAR": pct("20.00"), "UNCLASSIFIED": pct("60.00")})

    def test_none_with_observed_transactions(self):
        self.spend(d(2026, 10, 5), 12_000)
        cur = run(self.u, 1)["current"]
        self.assertEqual((cur["coverage"]["state"], cur["observed"]["total"], cur["confirmed"]), ("none", 12_000, None))

    def test_no_cycle(self):
        self.covered(self.OCT)
        got = run(self.u, 4)
        self.assertEqual((got["state"], got["budget"]), ("ok", {"state": "no_cycle"}))
        self.assertFalse({"confirmed", "observed", "spent_period"} & set(got))  # 주기가 없으면 사용액 범위도 없다

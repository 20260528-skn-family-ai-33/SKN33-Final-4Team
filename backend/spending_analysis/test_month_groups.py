"""R03 월별 묶기: 각 거래의 연 · 월로 집계 + 날짜 범위 + 월별을 검증 · 조회 · 답까지 (DB · 외부 LLM 없음)
실행: backend 폴더에서 python manage.py test spending_analysis.test_month_groups
"""
import json
from datetime import date, datetime, timedelta
from unittest import mock

from django.test import SimpleTestCase

from chat.router_core import MemoryStateStore

from . import answer, dialog, tools
from .adapter import run
from .analyze import KST, validate
from .periods import Period

d = date


def tx(on, amount, category="FOOD", merchant="가게", time="12:00"):
    return {"occurred_on": on, "occurred_time": time, "merchant_name": merchant, "category": category,
            "net_amount": amount, "transaction_type": "spending", "funding": "living"}


def groups(picked, group="month", measure="sum", top=None):
    q = {"group_by": group, "measure": measure, **({"top_n": top} if top else {})}
    span = Period(min(r["occurred_on"] for r in picked).replace(day=1), max(r["occurred_on"] for r in picked) + timedelta(1)) \
        if picked else None  # 여러 달에 걸친 기간 하나 (수정 전 코드는 모두 시작 월로 묶었다)
    return tools._groups([(span, r) for r in picked], q)


def brief(found):
    return [(g["key"], g["name"], g["sum"], g["count"]) for g in found]


class MonthGroupTest(SimpleTestCase):
    SEP_OCT = [tx(d(2026, 9, 10), 10_000), tx(d(2026, 10, 10), 20_000)]

    def test_each_transaction_goes_to_its_own_month(self):
        self.assertEqual(brief(groups(self.SEP_OCT)), [(9, "9월", 10_000, 1), (10, "10월", 20_000, 1)])

    def test_order_is_calendar_not_input(self):
        self.assertEqual(brief(groups(self.SEP_OCT[::-1])), brief(groups(self.SEP_OCT)))

    def test_year_boundary_december_then_january(self):
        picked = [tx(d(2027, 1, 5), 3_000), tx(d(2026, 12, 20), 5_000)]
        self.assertEqual(brief(groups(picked)), [(12, "12월", 5_000, 1), (1, "1월", 3_000, 1)])

    def test_same_month_of_different_years_stays_apart(self):
        # 볼 수 있는 범위(최근 3개월 + 이번 달)에선 생기지 않는 입력 — 내부 집계가 연도를 구분하는지만 본다
        picked = [tx(d(2026, 9, 1), 1_000), tx(d(2025, 9, 1), 2_000)]
        self.assertEqual(brief(groups(picked)), [(9, "9월", 2_000, 1), (9, "9월", 1_000, 1)])

    def test_week_across_months_splits_by_month(self):
        week = Period(d(2026, 9, 28), d(2026, 10, 5))  # 월 9/28 ~ 일 10/4
        picked = [(week, tx(d(2026, 10, 2), 2_000)), (week, tx(d(2026, 9, 29), 1_000))]
        found = tools._groups(picked, {"group_by": "month", "measure": "sum"})
        self.assertEqual(brief(found), [(9, "9월", 1_000, 1), (10, "10월", 2_000, 1)])

    def test_month_boundary_first_day_belongs_to_new_month(self):
        picked = [tx(d(2026, 10, 31), 1_000), tx(d(2026, 11, 1), 2_000)]
        self.assertEqual(brief(groups(picked)), [(10, "10월", 1_000, 1), (11, "11월", 2_000, 1)])

    def test_cancel_rules_and_empty(self):
        picked = [tx(d(2026, 9, 1), 10_000), tx(d(2026, 9, 2), 4_000),   # 부분 취소 반영된 순금액 4,000
                  tx(d(2026, 10, 1), 0)]                                  # 전액 취소 = 0원, 건수 0
        self.assertEqual(brief(groups(picked)), [(9, "9월", 14_000, 2), (10, "10월", 0, 0)])
        self.assertEqual(groups([]), [])

    def test_top_n_and_count_keep_ranking(self):
        picked = [tx(d(2026, 8, 1), 9_000), tx(d(2026, 9, 1), 1_000), tx(d(2026, 9, 2), 1_000), tx(d(2026, 10, 1), 5_000)]
        self.assertEqual(brief(groups(picked, top=2)), [(8, "8월", 9_000, 1), (10, "10월", 5_000, 1)])
        self.assertEqual(brief(groups(picked, measure="count", top=1)), [(9, "9월", 2_000, 2)])
        tie = [tx(d(2026, 9, 1), 1_000), tx(d(2026, 10, 1), 1_000)]  # 동률은 이름 순 (기존 규칙: '10월' < '9월')
        self.assertEqual([g["key"] for g in groups(tie, top=2)], [10, 9])

    def test_month_totals_match_overall(self):
        picked = [tx(d(2026, 8, 3), 7_000), tx(d(2026, 9, 9), 0), tx(d(2026, 10, 1), 2_500), tx(d(2026, 10, 2), 1_500)]
        found = groups(picked)
        self.assertEqual(sum(g["sum"] for g in found), sum(r["net_amount"] for r in picked))
        self.assertEqual(sum(g["count"] for g in found), sum(r["net_amount"] > 0 for r in picked))

    def test_other_groups_unchanged(self):
        picked = [tx(d(2026, 10, 5), 1_000, "TRANSPORT", "B", "21:00"), tx(d(2026, 10, 6), 3_000, "FOOD", "A", "08:00")]
        self.assertEqual([g["key"] for g in groups(picked, "category")], ["FOOD", "TRANSPORT"])
        self.assertEqual([g["key"] for g in groups(picked, "weekday")], [0, 1])  # 10/5 월 · 10/6 화
        self.assertEqual([g["name"] for g in groups(picked, "merchant")], ["A", "B"])
        self.assertEqual(len(groups(picked, "time_slot")), 2)


class MonthGroupCheckTest(SimpleTestCase):
    def item(self, period, **extra):
        return {"type": 10, "category": None, "query": {"period": period, "measure": "sum", "group_by": "month"}, **extra}

    def test_every_query_period_accepts_month_group(self):
        tools.check_query(self.item("range", date_from="09-01", date_to="10-31"))
        tools.check_query(self.item("recent", months_applied=3))

    def test_dialog_ready_month_group_passes_validate(self):
        """대화가 ready 로 만든 '기간 + 월별' 항목을 analyze.validate 도 받는다 (기간 상한 · 미래 제한은 그대로)"""
        today, as_of = d(2026, 11, 5), datetime(2026, 11, 5, 10, tzinfo=KST)
        cases = {"this_month": {}, "last_month": {}, "this_week": {}, "last_week": {}, "month": {"month": 9}}
        for period, extra in cases.items():
            with self.subTest(period):
                said = {"mine": True, "target": "spend", "period": period, "group_by": "month", **extra}
                decision = dialog.step(MemoryStateStore(), period, f"{period} 월별로", lambda s, u: json.dumps(said),
                                       today=today)
                self.assertEqual(decision.status, "ready")
                self.assertEqual(decision.items[0]["query"]["group_by"], "month")
                validate(decision.items[0], today, as_of)
        with self.assertRaises(ValueError):  # 볼 수 있는 범위 밖 달은 여전히 거절 (month_of → None)
            tools.query_periods(self.item("month", month=6), today)

    def test_range_policy_unchanged(self):
        today = d(2026, 11, 5)
        self.assertEqual(tools.query_periods(self.item("range", date_from="09-01", date_to="10-31"), today),
                         [Period(d(2026, 9, 1), d(2026, 11, 1))])  # 시작일 포함 · 끝 다음 날 미포함
        with self.assertRaises(ValueError):  # 볼 수 있는 범위(8/1~) 밖은 그대로 막힌다
            tools.query_periods(self.item("range", date_from="06-01", date_to="10-31"), today)

    def test_recent_months_periods_unchanged(self):
        today = d(2026, 11, 5)
        self.assertEqual([p.start for p in tools.query_periods(self.item("recent", months_applied=2), today)],
                         [d(2026, 9, 1), d(2026, 10, 1)])
        self.assertEqual(tools.query_periods(self.item("recent", months_applied=2, include_current=True), today),
                         [Period(d(2026, 10, 1), d(2026, 10, 6)), Period(d(2026, 11, 1), d(2026, 11, 6))])


DATA = [tx(d(2026, 12, 20), 5_000), tx(d(2026, 12, 9), 9_999),    # 12/9 는 범위 시작 전
        tx(d(2027, 1, 3), 2_000), tx(d(2027, 1, 4), 1_000)]


def fake_rows(user, period, where=None, stats=None, category=None):
    """rows 대역: DB 의 occurred_on__gte=start · __lt=end_before 와 같은 거르기"""
    return [r for r in DATA if period.start <= r["occurred_on"] < period.end_before]


class RangeMonthPathTest(SimpleTestCase):
    """대화 판단 → analyze.validate → tools.query → answer.compose (원천 거래 · coverage 만 대역)"""

    @mock.patch.object(tools, "coverage", lambda user, period: {"state": "complete"})
    @mock.patch.object(tools, "rows", fake_rows)
    def test_range_by_month_answers_per_month(self):
        today = d(2027, 1, 5)
        said = {"mine": True, "target": "spend", "period": "range", "date_from": "12-10", "date_to": "01-04",
                "period_said": "12월 10일부터 1월 4일까지", "group_by": "month"}
        decision = dialog.step(MemoryStateStore(), "t", "12월 10일부터 1월 4일까지 월별로", lambda s, u: json.dumps(said),
                               today=today)
        self.assertEqual(decision.status, "ready")
        self.assertEqual(decision.items[0]["query"], {"period": "range", "measure": "sum", "group_by": "month"})
        results = run(None, decision, today=today, as_of=datetime(2027, 1, 5, 10, tzinfo=KST))
        self.assertTrue(results[0]["ok"], results)
        found = results[0]["result"]["confirmed"]
        self.assertEqual(brief(found["groups"]), [(12, "12월", 5_000, 1), (1, "1월", 3_000, 2)])
        self.assertEqual((found["sum"], found["count"]), (8_000, 3))
        lines = answer.compose(results)["lines"]
        self.assertTrue(any("12월" in line and "5,000원" in line for line in lines), lines)
        self.assertTrue(any("1월" in line and "3,000원" in line for line in lines), lines)

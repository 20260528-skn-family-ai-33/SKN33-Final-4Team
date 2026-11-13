"""소비 추이 → 습관 → 앞으로 (story) — 독립 산술로 확인. 외부 호출 · DB 없음."""
from datetime import date

from unittest import mock

from django.test import SimpleTestCase

from . import story
from .periods import Period
from .snapshot import josa

TODAY = date(2026, 11, 10)
MONTHS = [Period(date(2026, m, 1), date(2026, m + 1, 1)) for m in (7, 8, 9, 10)]


def pay(day, amount, category="FOOD", *, month=11, activity=None, time="12:30"):
    return {"net_amount": amount, "category": category, "occurred_on": date(2026, month, day), "occurred_time": time,
            "funding": "living", "transaction_type": "spending", "activity": activity}


def month_rows(month, *, lunch=10000, cafe=4000, extra=None):
    """평일마다 점심 외식 + 카페, 한 달에 한 번 미용실 (주말 장보기)"""
    days = (date(2026, month + 1, 1) - date(2026, month, 1)).days if month < 12 else 31
    rows = []
    for d in range(1, days + 1):
        day = date(2026, month, d)
        if day.weekday() < 5:
            rows.append(pay(d, lunch, month=month, activity="dining"))
            rows.append(pay(d, cafe, month=month, activity="cafe", time="13:10"))
        elif day.weekday() == 5:
            rows.append(pay(d, 30000, month=month, activity="groceries", time="16:00"))
    rows.append(pay(18, 40000, "BEAUTY", month=month))
    rows.extend(extra or [])
    return rows


def budget(allocated, categories, cycle=Period(date(2026, 11, 1), date(2026, 12, 1))):
    return {"budget": {"state": "approved", "allocated": allocated}, "cycle_period": cycle,
            "categories": [{"category": c, "allocated": a} for c, a in categories.items()]}


class TrendTest(SimpleTestCase):
    def test_zigzag_is_flat_not_a_monthly_decline(self):
        self.assertEqual(story._direction([1060000, 954000, 1040000, 930000]), "flat")
        self.assertEqual(story._direction([800000, 880000, 960000, 1050000]), "up")
        self.assertEqual(story._direction([1050000, 960000, 880000, 800000]), "down")
        self.assertEqual(story._direction([900000, 910000]), "short")

    def test_trend_names_the_path_the_stable_core_and_occasional_swings(self):
        history = [r for m in (7, 8, 9, 10) for r in month_rows(m, extra=[pay(9, 90000 if m % 2 else 10000, "FASHION_SHOPPING", month=m)])]
        f = story.trend_finding(story.monthly(history, MONTHS))
        self.assertIn("7월", f["evidence"][0])
        self.assertIn("→ 10월", f["evidence"][0])
        self.assertIn("추세는 아니에요", f["evidence"][0])
        self.assertTrue(any("식비는 매달" in e and "거의 고정" in e for e in f["evidence"]))
        self.assertTrue(any("패션·쇼핑처럼 가끔 사는" in e for e in f["evidence"]))
        self.assertIn("꾸준했어요", f["conclusion"])

    def test_rising_category_is_called_out_when_total_is_flat(self):
        history = [r for i, m in enumerate((7, 8, 9, 10)) for r in month_rows(m, extra=[pay(9, 20000 + 30000 * i, "CULTURE_LEISURE", month=m)])
                   if not (r["category"] == "FOOD" and r["activity"] == "cafe" and i % 2)]
        f = story.trend_finding(story.monthly(history, MONTHS))
        self.assertTrue(any("문화·여가는 2만 원에서 11만 원으로 꾸준히 늘고" in e for e in f["evidence"]), f["evidence"])

    def test_two_months_say_how_last_month_differed_not_a_range(self):
        series = [{"label": "9월", "total": 536000, "categories": {"FOOD": 300000, "FASHION_SHOPPING": 20000}},
                  {"label": "10월", "total": 761000, "categories": {"FOOD": 330000, "FASHION_SHOPPING": 200000}}]
        f = story.trend_finding(series)
        self.assertEqual(f["conclusion"], "지난달(10월) 생활비는 76.1만 원으로 9월(53.6만 원)보다 22.5만 원 늘었어요.")
        self.assertTrue(any("패션·쇼핑(+18만 원)" in e for e in f["evidence"]), f["evidence"])
        flat = [{**series[0], "total": 750000}, series[1]]
        self.assertIn("9월(75만 원)과 비슷했어요", story.trend_finding(flat)["conclusion"])

    def test_months_before_any_spending_are_not_zero_months(self):
        series = story.monthly(month_rows(10), MONTHS)
        self.assertEqual([m["label"] for m in series], ["10월"])


class HabitTest(SimpleTestCase):
    def test_repeated_weekday_lunch_dining_and_cafe_with_share(self):
        history = [r for m in (7, 8, 9, 10) for r in month_rows(m)]
        series = story.monthly(history, MONTHS)
        f, stats = story.habit_finding(history, series, [], TODAY)
        self.assertEqual(set(stats), {"dining", "cafe", "groceries"})
        self.assertTrue(stats["dining"]["weekday_lunch"])
        self.assertIn("평일 점심 외식(월 2", f["evidence"][0])
        self.assertIn("매달 반복돼요", f["evidence"][0])
        self.assertIn("생활비의", f["conclusion"])
        self.assertIn("식비이고", f["conclusion"])

    def test_one_off_activity_is_not_a_habit(self):
        history = [pay(5, 15000, month=m, activity="delivery") for m in (7, 8, 9, 10)]
        f, stats = story.habit_finding(history, story.monthly(history, MONTHS), [], TODAY)
        self.assertIsNone(f)
        self.assertEqual(stats, {})


class ProjectionTest(SimpleTestCase):
    def test_occasional_purchase_bought_early_is_not_extrapolated(self):
        history = [r for m in (7, 8, 9, 10) for r in month_rows(m)]
        current = [r for r in month_rows(11) if r["occurred_on"] <= TODAY] + [pay(2, 40000, "BEAUTY")]
        current = [r for r in current if not (r["category"] == "BEAUTY" and r["occurred_on"].day == 18)]
        month_end, per, next_total = story.by_pattern(current, history, MONTHS, TODAY)
        self.assertEqual(per["BEAUTY"], 40000)  # 한 달에 한 번 사는 미용실을 남은 20일 동안 매일 쓰는 것처럼 늘리지 않는다
        food_spent = sum(r["net_amount"] for r in current if r["category"] == "FOOD")
        self.assertGreater(per["FOOD"], food_spent)  # 자주 쓰는 식비는 남은 날에도 이어진다
        self.assertAlmostEqual(month_end, sum(per.values()))
        self.assertGreater(next_total, 0)

    def test_backtest_uses_only_months_before_each_checked_month(self):
        history = [r for m in (7, 8, 9, 10) for r in month_rows(m)]
        checked, error = story.backtest(history, MONTHS, 10)
        self.assertEqual(checked, 3)  # 7월은 앞선 달이 없어 검증하지 않는다
        later = history + [pay(28, 900000, "HOUSEHOLD", month=10)]
        self.assertEqual(story.backtest(later, MONTHS[:3], 10), story.backtest(history, MONTHS[:3], 10))  # 10월 자료가 7~9월 검증을 바꾸지 않는다
        self.assertGreater(error, 0)

    def test_outlook_over_budget_names_the_first_category_and_next_month(self):
        history = [r for m in (7, 8, 9, 10) for r in month_rows(m)]
        current = [r for r in month_rows(11, lunch=16000) if r["occurred_on"] <= TODAY]
        projection = story.project(current, history, TODAY, MONTHS, True)
        self.assertEqual(projection["method"], "pattern")
        f, over = story.outlook_finding(projection, current, TODAY, budget(300000, {"FOOD": 250000, "BEAUTY": 50000}), {}, history, MONTHS)
        self.assertIn("넘을 거예요", f["conclusion"])
        self.assertIn("다음 달(12월)도 지금처럼 쓰면", f["evidence"][1])
        self.assertEqual(over["category"], "FOOD")
        self.assertIn("가장 먼저 넘을 항목은 식비예요", f["evidence"][2])

    def test_budget_on_a_different_cycle_is_not_compared_with_the_calendar_month(self):
        history = [r for m in (7, 8, 9, 10) for r in month_rows(m)]
        current = [r for r in month_rows(11) if r["occurred_on"] <= TODAY]
        projection = story.project(current, history, TODAY, MONTHS, True)
        f, over = story.outlook_finding(projection, current, TODAY, budget(300000, {"FOOD": 250000},
                                        cycle=Period(date(2026, 10, 25), date(2026, 11, 25))), {}, history, MONTHS)
        self.assertNotIn("예산", f["conclusion"])
        self.assertIsNone(over)

    def test_incomplete_collection_makes_no_projection(self):
        self.assertIsNone(story.project([pay(3, 10000)], [], TODAY, [], False))


class ActionsAndChartTest(SimpleTestCase):
    def test_actions_use_the_persons_average_and_compare_with_the_overrun(self):
        habits = {"dining": {"amount": 230000, "average": 10000, "weekday_lunch": True},
                  "cafe": {"amount": 90000, "average": 4000, "weekday_lunch": True}}
        lines = story.actions(habits, {"month_end": 1100000, "allocated": 1050000})
        self.assertEqual(lines[0], "점심 외식을 주 1회 도시락으로 바꾸면 한 달 약 4만 원 (평균 1만 원 × 4회) — 이번 달 초과분의 절반 정도예요.")
        self.assertIn("카페·간식을 주 2회 건너뛰면 한 달 약 3.2만 원", lines[1])

    def test_chart_separates_actual_current_and_next(self):
        series = [{"label": "9월", "total": 900000}, {"label": "10월", "total": 950000}]
        bars = story.chart(series, {"observed": 300000, "month_end": 980000}, {"next_label": "12월", "next_month": 990000, "allocated": 1000000},
                           TODAY)["bars"]
        self.assertEqual([b["kind"] for b in bars], ["actual", "actual", "current", "next"])
        self.assertEqual((bars[2]["actual"], bars[2]["projected"]), (300000, 680000))
        self.assertEqual((bars[3]["actual"], bars[3]["projected"]), (0, 990000))

    def test_amount_and_particles(self):
        self.assertEqual(story.man(1144000), "114만 원")
        self.assertEqual(story.man(977000), "97.7만 원")
        self.assertEqual(story.man(4400), "4,400원")
        self.assertEqual(josa("약 80%", "이가"), "약 80%가")
        self.assertEqual(josa("97.7만 원", "으로로"), "97.7만 원으로")
        self.assertEqual(story.ieyo("뷰티·미용"), "뷰티·미용이에요")
        self.assertEqual(story.ieyo("식비"), "식비예요")


class CalculationTest(SimpleTestCase):
    def test_short_summary_and_a_per_category_table(self):
        history = [r for m in (7, 8, 9, 10) for r in month_rows(m)]
        current = [r for r in month_rows(11) if r["occurred_on"] <= TODAY] + [pay(2, 120000, "FASHION_SHOPPING")]
        st = story.build(current, history, MONTHS, TODAY, complete=True, budget=None)
        out = story.calculation(st, TODAY)
        p = st["projection"]
        self.assertEqual(len(out["lead"]), 2)
        self.assertIn(f"남은 {p['left']}일", out["lead"][0])
        self.assertIn("31일", out["lead"][1])
        self.assertLessEqual(len(out["rows"]), 5)  # 화면에는 큰 항목 4개 + 그 밖
        rows = {r["name"]: r for r in out["rows"]}
        self.assertIn(f"×{p['left']}일", rows["식비"]["note"])
        self.assertEqual(rows["패션·쇼핑"]["note"], "이미 12만 씀")
        self.assertEqual(rows["뷰티·미용"]["note"], "평소 한 달 4만")
        self.assertTrue(any("패션·쇼핑을 평소보다" in l for l in out["lines"]), out["lines"])  # 다음 달이 더 적은 이유
        self.assertTrue(any(n.startswith("정확도:") for n in out["notes"]))  # 정확도 · 기준은 접힌 안내로

    def test_no_projection_explains_only_monthly_totals(self):
        st = {"projection": None, "series": [{"label": "9월", "total": 500000}, {"label": "10월", "total": 600000}]}
        out = story.calculation(st, TODAY)
        self.assertEqual(out["rows"], [])
        self.assertIn("9월 50만 원", out["notes"][0])
        self.assertIsNone(story.calculation({"projection": None, "series": []}, TODAY))


class MenuLayoutTest(SimpleTestCase):
    """네 메뉴(전체 · 습관 · 앞으로 · 지난달 비교)는 같은 섹션을 순서만 바꾸지 않고 각자 다른 내용을 보인다"""
    def test_each_menu_has_its_own_layout(self):
        from . import insight
        history = [r for m in (7, 8, 9, 10) for r in month_rows(m)]
        current = [r for r in month_rows(11) if r["occurred_on"] <= TODAY]
        st = story.build(current, history, MONTHS, TODAY, complete=True, budget=budget(1000000, {"FOOD": 800000, "BEAUTY": 50000}))
        by_id = {f["id"]: f for f in st["findings"]}
        summary = {"findings": [], "basis": {"comparison_available": True},
                   "categories": [{"name": "식비", "current": {"amount": 200000}, "previous": {"amount": 150000}, "delta": 50000}]}
        cause = {"kind": "change_total", "conclusion": "지난달 같은 기간보다 5만 원 더 썼어요.", "evidence": ["이번 달 200,000원, 지난달 같은 기간 150,000원이에요."]}
        layouts = {}
        with mock.patch.object(insight.benchmark, "for_user", return_value=([], [])), mock.patch.object(insight, "select", return_value=[cause]):
            for goal in ("overview", "habit", "forecast", "reason"):
                blocks, examples, _ = insight._story_blocks(None, summary, goal, TODAY, by_id, st)
                layouts[goal] = [b.get("title") or b["kind"] for b in blocks]
                self.assertNotIn(insight.ASK[goal], examples)  # 이어 볼 분석 = 지금 본 것 말고
        self.assertEqual(layouts["overview"][:2], ["핵심 진단", "월별 생활비"])
        self.assertIn("소비 습관", layouts["overview"])
        self.assertIn("매달 반복되는 소비", layouts["habit"])
        self.assertNotIn("월별 생활비", layouts["habit"])  # 습관 답에는 월별 막대 · 예상이 없다
        self.assertIn("항목별 이번 달 말 예상", layouts["forecast"])
        self.assertNotIn("소비 습관", layouts["forecast"])
        self.assertEqual(layouts["reason"][1], "항목별 변화 (지난달 같은 기간 대비)")
        self.assertNotIn("월별 생활비", layouts["reason"])
        self.assertEqual(len({tuple(v) for v in layouts.values()}), 4)

    def test_forecast_rows_compare_with_budget_or_usual(self):
        history = [r for m in (7, 8, 9, 10) for r in month_rows(m)]
        current = [r for r in month_rows(11, lunch=16000) if r["occurred_on"] <= TODAY]
        projection = story.project(current, history, TODAY, MONTHS, True)
        rows = {r["name"]: r for r in story.forecast_rows(projection, budget(300000, {"FOOD": 250000}), TODAY)}
        self.assertIn("예산 25만의", rows["식비"]["note"])
        self.assertEqual(rows["식비"]["direction"], "up")
        usual = {r["name"]: r for r in story.forecast_rows(projection, None, TODAY)}
        self.assertTrue(usual["식비"]["note"].startswith("평소("), usual["식비"])

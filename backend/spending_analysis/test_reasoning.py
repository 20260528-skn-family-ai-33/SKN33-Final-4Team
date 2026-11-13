"""독립 산술·합성 시나리오로 결론과 설명 장부를 검증한다. 외부 호출 없음."""
import random
from datetime import date, timedelta

from django.test import SimpleTestCase

from . import reasoning, trend
from .periods import Period

TODAY = date(2026, 11, 28)
PERIODS = (Period(date(2026, 11, 1), date(2026, 11, 29)), Period(date(2026, 10, 1), date(2026, 10, 29)))


def row(day, amount=10000, category="FOOD", activity="dining"):
    return dict(occurred_on=day, occurred_time="12:00", net_amount=amount, category=category,
                activity=activity, funding="living", transaction_type="spending")


def month(month, amount=10000, category="FOOD", activity="dining", days=28):
    return [row(date(2026, month, d), amount, category, activity) for d in range(1, days + 1)]


def analyze(now, before, *, periods=PERIODS, complete=True, history=None):
    if history is None:
        history, day = [], date(2026, 5, 1)
        while day < date(2026, 11, 1):
            history.append(row(day))
            day += timedelta(days=1)
        history = [r for r in history if not periods[1].start <= r["occurred_on"] < periods[1].end_before] + before
    return reasoning.analyze(now, before, history, periods, trend.history_months(periods[0].last_day) if history else [],
                             comparable=complete, window_complete=complete)


class ExplanationScenarioTest(SimpleTestCase):
    def test_large_payment_only_does_not_become_habit_change(self):
        result = analyze(month(11) + [row(TODAY, 400000)], month(10))
        f = result["findings"][0]
        self.assertEqual(f["kind"], "large_total")
        self.assertEqual((f["large_delta"], f["regular_delta"]), (400000, 0))
        self.assertEqual(f["persistence"]["state"], "usual")
        text = " ".join([f["conclusion"], *f["evidence"]])
        self.assertIn("달라지지 않았어요", text)
        self.assertNotIn("+0원", text)

    def test_more_consuming_dates_and_more_payments_per_date_are_distinct(self):
        before, now = month(10), month(11)
        dates = reasoning.decompose(now, before[::2])
        per_day = reasoning.decompose(now + now, before)
        self.assertEqual(dates["parts"]["payment_days"], 140000)
        self.assertEqual(dates["parts"]["payments_per_day"], 0)
        self.assertEqual(per_day["parts"]["payments_per_day"], 280000)
        self.assertEqual(per_day["parts"]["payment_days"], 0)
        self.assertEqual(per_day["parts"]["within_type_amount"], 0)

    def test_same_type_unit_amount_change(self):
        f = analyze(month(11, 12000), month(10))["findings"][0]
        self.assertEqual(f["kind"], "within_type_amount")
        self.assertEqual(f["decomposition"]["parts"]["within_type_amount"], 56000)
        self.assertEqual(f["decomposition"]["parts"]["type_mix"], 0)

    def test_type_mix_does_not_become_same_type_price_increase(self):
        before = [row(date(2026, 10, d), 20000 if d <= 7 else 5000, activity="dining" if d <= 7 else "cafe") for d in range(1, 29)]
        now = [row(date(2026, 11, d), 20000 if d <= 14 else 5000, activity="dining" if d <= 14 else "cafe") for d in range(1, 29)]
        f = analyze(now, before)["findings"][0]
        self.assertEqual(f["kind"], "type_mix")
        self.assertEqual(f["decomposition"]["parts"]["type_mix"], 105000)
        self.assertEqual(f["decomposition"]["parts"]["within_type_amount"], 0)
        text = " ".join([f["conclusion"], *f["evidence"]])
        self.assertNotIn("금액 분해:", text)
        self.assertIn("외식에 쓰는 비중이 커진", text)
        self.assertIn("외식 관련 지출은", text)

    def test_one_week_spike_reverts_but_three_week_rise_persists(self):
        before = month(10)
        spike = month(11)
        for r in spike[7:14]:
            r["net_amount"] = 20000
        f = analyze(spike, before)["findings"][0]
        self.assertEqual(f["persistence"]["state"], "reverted")
        rise = month(11)
        for i, r in enumerate(rise):
            r["net_amount"] = (10000, 12000, 14000, 16000)[i // 7]
        f = analyze(rise, before)["findings"][0]
        self.assertEqual(f["persistence"]["state"], "sustained")

    def test_opposing_categories_are_discovered_even_if_total_is_flat(self):
        before = month(10) + month(10, 20000, "FASHION_SHOPPING", None)
        now = month(11, 20000) + month(11, 10000, "FASHION_SHOPPING", None)
        result = analyze(now, before)
        self.assertEqual(result["total_delta"], 0)
        self.assertEqual(result["findings"][0]["kind"], "offset")
        self.assertEqual(len(result["candidates"]), 3)

    def test_equal_total_and_unit_price_can_hide_type_switch(self):
        before = month(10, activity="cafe")
        now = month(11, activity="dining")
        f = analyze(now, before)["findings"][0]
        self.assertEqual(f["kind"], "type_offset")
        self.assertEqual(f["delta"], 0)
        self.assertIsNone(f["decomposition"]["shares"])

    def test_weekday_opportunities_explain_difference_without_behavior_change(self):
        periods = (Period(date(2026, 11, 1), date(2026, 11, 30)), Period(date(2026, 10, 1), date(2026, 10, 30)))
        now = [row(date(2026, 11, d), 20000 if date(2026, 11, d).weekday() >= 5 else 10000) for d in range(1, 30)]
        before = [row(date(2026, 10, d), 20000 if date(2026, 10, d).weekday() >= 5 else 10000) for d in range(1, 30)]
        result = analyze(now, before, periods=periods)
        self.assertEqual(result['findings'], [])  # 요일 수 차이뿐이면 보여 줄 변화가 없다 ('변화 없음' 대체 문장도 만들지 않는다)
        f = result['candidates'][0]
        self.assertEqual(f["kind"], "calendar")
        self.assertEqual(f["calendar"], {"available": True, "calendar": 10000, "behavior": 0})

    def test_unchanged_and_personal_normal_variation_are_not_new_habits(self):
        result = analyze(month(11), month(10))
        self.assertEqual(result["findings"], [])
        self.assertFalse(reasoning.material(20000, [100000, 160000, 110000, 170000])["meaningful"])

    def test_partial_unknown_data_keeps_observations_without_claiming_change(self):
        now = month(11, days=4) + [row(TODAY, 172600, "UNCLASSIFIED", None)]
        result = analyze(now, [], complete=False, history=[])
        self.assertTrue(result["findings"])
        self.assertTrue(all(f.get("category") != "UNCLASSIFIED" for f in result["findings"]))
        self.assertFalse(result["findings"][0]["decomposition"]["available"])
        self.assertIn("외식 중심으로", result["findings"][0]["conclusion"])  # 일부 내역이어도 확인된 패턴은 보여 준다

    def test_unclassified_growth_does_not_become_no_change(self):
        result = analyze(month(11, 20000, "UNCLASSIFIED", None), month(10, 10000, "UNCLASSIFIED", None), history=[])
        f = result["findings"][0]
        self.assertEqual(f["kind"], "unclassified_change")
        self.assertEqual(f["delta"], 280000)
        self.assertIsNone(f["category"])
        self.assertTrue(f["decomposition"]["reconciled"])

    def test_ten_thousand_delivery_does_not_explain_eighty_thousand_increase(self):
        before = month(10, days=14) + month(10, activity="delivery", days=14)
        now = month(11, 15000, days=14) + month(11, activity="delivery", days=15)
        f = analyze(now, before)["findings"][0]
        self.assertEqual(f["delta"], 80000)
        self.assertNotEqual(f["main_type"], "delivery")

    def test_insufficient_history_limits_persistence_not_arithmetic(self):
        f = analyze(month(11, 12000), month(10), history=[])["findings"][0]
        self.assertTrue(f["decomposition"]["reconciled"])
        self.assertEqual(f["persistence"]["state"], "unknown")

    def test_tiny_persistent_changes_are_not_promoted_as_increases(self):
        history, day = [], date(2026, 5, 1)
        while day < date(2026, 11, 1):
            history += [row(day), row(day, 20, "MEDICAL_HEALTH", None)]
            day += timedelta(days=1)
        now = month(11) + [row(date(2026, 11, d), (20, 30, 40, 50)[(d - 1) // 7], "MEDICAL_HEALTH", None) for d in range(1, 29)]
        before = month(10) + month(10, 20, "MEDICAL_HEALTH", None)
        result = analyze(now, before, history=history)
        self.assertFalse([f for f in result["findings"] if f.get("category") == "MEDICAL_HEALTH"])
        medical = next(c for c in result["candidates"] if c['category'] == 'MEDICAL_HEALTH')
        self.assertNotIn('증가 신호', medical['conclusion'])
        self.assertNotIn('3주 연속', medical['outlook'])

    def test_changed_classification_share_limits_category_causality(self):
        before = month(10) + [row(date(2026, 10, 8), 100000, "UNCLASSIFIED", None)]
        now = month(11, 12000) + [row(TODAY, 44000, "UNCLASSIFIED", None)]
        result = analyze(now, before)
        self.assertTrue(result["classification_changed"])
        self.assertTrue(all(f.get("main_type") is None for f in result["candidates"]))
        self.assertTrue(any("미분류 비중" in t for f in result["candidates"] for t in f["limits"]))
        food = next(f for f in result['candidates'] if f.get('category') == 'FOOD')
        self.assertEqual(food['kind'], 'classification_limited')
        self.assertTrue(food['quiet'])  # 판단할 수 없는 결론은 사용자에게 보이지 않는다
        self.assertNotIn(food, result['findings'])
        self.assertNotIn('주된 이유', food['conclusion'])

    def test_a_few_thousand_won_unclassified_does_not_block_category_analysis(self):
        # 11/13 실제 답변: 편의점 5,000원 때문에 모든 카테고리가 '단정하기 어려워요'로 막혔다
        before = month(10) + [row(date(2026, 10, 8), 5000, "UNCLASSIFIED", None)]
        now = month(11, 12000) + [row(TODAY, 3000, "UNCLASSIFIED", None)]
        result = analyze(now, before)
        self.assertFalse(result["classification_changed"])
        self.assertEqual(result["findings"][0]["kind"], "within_type_amount")

    def test_unknown_industry_keeps_observed_time_pattern_without_activity_guess(self):
        now = month(11, activity=None, days=5)
        f = analyze(now, [], complete=False, history=[])["findings"][0]
        evidence = " ".join([f['conclusion'], *f["evidence"]])
        self.assertIn("점심", evidence)
        self.assertNotIn("외식", evidence)

    def test_recurrent_large_category_is_not_called_one_off(self):
        before = month(10) + [row(date(2026, 10, 8), 400000)]
        history, day = [], date(2026, 5, 1)
        while day < date(2026, 11, 1):
            history.append(row(day))
            if day.day == 8:
                history.append(row(day, 400000))
            day += timedelta(days=1)
        result = analyze(month(11) + [row(date(2026, 11, 8), 400000)], before, history=history)
        f = result["candidates"][0]
        self.assertEqual((f["large_delta"], f["regular_delta"]), (0, 0))
        self.assertTrue(any("6개 월" in t for t in f["evidence"]))

    def test_randomized_exact_reconciliation_and_offset_ratio_suppression(self):
        rng = random.Random(42)
        for _ in range(100):
            rows = [[row(date(2026, m, rng.randrange(1, 29)), rng.randrange(1000, 30000), activity=rng.choice(["cafe", "dining"]))
                     for _ in range(rng.randrange(5, 50))] for m in (11, 10)]
            report = reasoning.decompose(*rows)
            self.assertEqual(sum(report["parts"].values()), report["delta"])
            if report["offsets"] or abs(report["delta"]) < 10000:
                self.assertIsNone(report["shares"])

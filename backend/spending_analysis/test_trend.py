"""시간순 월말 검증, 후보 선택, 미래 자료 차단, 자료 부족 시 보류를 확인한다."""
from datetime import date, timedelta

from django.test import SimpleTestCase

from . import trend

TODAY = date(2026, 11, 21)


def payment(day, amount, category="FOOD", funding="living"):
    return {"occurred_on": day, "net_amount": amount, "category": category, "funding": funding}


def series(kind="flat", today=TODAY):
    months = trend.history_months(today)
    rows, day = [], months[0].start
    while day <= today:
        offset = (day - months[0].start).days
        amount = (5000 + offset * 100 if kind == "rise" else 30000 - offset * 100 if kind == "fall" else
                  1000 if kind == "unstable" and day.month >= 9 else 10000)
        if kind != "weekday" or day.weekday() < 5:
            rows.append(payment(day, amount))
        day += timedelta(days=1)
    start = today.replace(day=1)
    return [r for r in rows if r["occurred_on"] >= start], [r for r in rows if r["occurred_on"] < start], months


def evaluate(kind="flat"):
    now, past, months = series(kind)
    return trend.forecast(now, past, TODAY, months=months)


class TrendTest(SimpleTestCase):
    def test_stable_consumption_selects_the_simpler_validated_method(self):
        result = evaluate()
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["month_end"], 300000)
        selected = result["validation"]["selected"]
        self.assertEqual(selected["model"], "average")
        self.assertIsNone(selected["phi"])
        self.assertIsNone(selected["weights"])
        self.assertEqual(result["validation"]["test_mae"], 0)

    def test_weekday_pattern_is_selected_by_month_end_error(self):
        result = evaluate("weekday")
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["validation"]["selected"]["model"], "weekday")
        self.assertEqual(result["validation"]["selected"]["weights"], "equal")
        self.assertEqual(result["validation"]["test_mae"], 0)
        self.assertEqual(result["validation"]["baseline_mae"], 5000)
        remaining = [TODAY + timedelta(days=i) for i in range(1, 10)]
        self.assertEqual(result["remaining"], sum(10000 for day in remaining if day.weekday() < 5))

    def test_rising_consumption_selects_parameters_with_lower_tuning_mae(self):
        result = evaluate("rise")
        self.assertEqual(result["state"], "ready")
        report = result["validation"]
        self.assertEqual(report["candidate_count"], 74)
        self.assertEqual(report["selected"], {"model": "trend", "lookback_days": 28, "weights": "latest", "phi": 1.0})
        self.assertEqual(report["tuning_mae"], min(r["tuning_mae"] for r in report["comparisons"]))
        self.assertLess(report["test_mae"], report["baseline_mae"])
        self.assertLess(report["test_mae"], report["fixed_08_mae"])
        self.assertEqual(result["weekly_direction"], "stable")  # 최근 4주의 작은 증가는 변화 안내에서 제외

    def test_user_histories_can_select_different_lookbacks_and_weights(self):
        rising, falling = evaluate("rise"), evaluate("fall")
        self.assertNotEqual(rising["validation"]["selected"], falling["validation"]["selected"])
        self.assertEqual(falling["validation"]["selected"]["lookback_days"], 56)
        self.assertEqual(falling["validation"]["selected"]["weights"], "equal")
        self.assertEqual(falling["weekly_direction"], "decrease")

    def test_test_months_are_disjoint_and_later_than_tuning_months(self):
        report = evaluate("rise")["validation"]
        self.assertEqual(report["tuning_months"], ["2026-07", "2026-08"])
        self.assertEqual(report["test_months"], ["2026-09", "2026-10"])
        self.assertLess(max(report["tuning_months"]), min(report["test_months"]))
        for result in report["test_results"]:
            origin = date.fromisoformat(result["as_of"])
            end = (origin.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            self.assertEqual((end - origin).days, 9)
        self.assertLessEqual(abs(report["test_mae"] - sum(r["absolute_error"] for r in report["test_results"]) / 2), 1)

    def test_test_data_does_not_change_the_chosen_parameters(self):
        now, past, months = series("rise")
        original = trend._calibrate([*past, *now], TODAY, months)
        changed = [{**r, "net_amount": r["net_amount"] * 4 if r["occurred_on"].month in (9, 10) else r["net_amount"]} for r in past]
        after = trend._calibrate([*changed, *now], TODAY, months)
        self.assertEqual(after["selected"], original["selected"])
        self.assertEqual(after["tuning_mae"], original["tuning_mae"])
        self.assertNotEqual(after["test_mae"], original["test_mae"])

    def test_future_large_payment_changes_target_but_not_training_features(self):
        now, past, months = series()
        before = trend._folds([*past, *now], TODAY, months)[0]
        future = payment(before["origin"] + timedelta(days=1), 400000)
        after = trend._folds([*past, *now, future], TODAY, months)[0]
        self.assertEqual(after["weeks"], before["weeks"])
        self.assertEqual(after["observed"], before["observed"])
        self.assertEqual(after["actual"] - before["actual"], 400000)

    def test_failure_on_unseen_months_is_not_repaired_by_picking_the_test_winner(self):
        result = evaluate("unstable")
        self.assertEqual(result["state"], "unavailable")
        report = result["validation"]
        self.assertEqual(report["state"], "failed")
        self.assertEqual(report["selected"]["model"], "average")
        self.assertTrue(any(r["test_mae"] == 0 for r in report["comparisons"]))
        self.assertNotIn("month_end", result)
        self.assertIn("검증 평균 오차", result["reason"])

    def test_three_months_of_history_do_not_silently_use_a_default_coefficient(self):
        now, past, months = series()
        past = [r for r in past if r["occurred_on"] >= months[-3].start]
        result = trend.forecast(now, past, TODAY, months=months[-3:])
        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(result["validation"]["state"], "insufficient")
        self.assertNotIn("selected", result["validation"])
        self.assertNotIn("month_end", result)

    def test_missing_month_is_not_treated_as_zero_spending(self):
        now, past, months = series()
        result = trend.forecast(now, past, TODAY, months=[p for p in months if p.start.month != 8])
        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(result["validation"]["available_months"], 0)

    def test_large_payment_is_included_once_without_changing_future_repeat_spending(self):
        now, past, months = series()
        original = trend.forecast(now, past, TODAY, months=months)
        result = trend.forecast([*now, payment(TODAY, 400000)], past, TODAY, months=months)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["large_count"], 1)
        self.assertEqual(result["remaining"], original["remaining"])
        self.assertEqual(result["month_end"] - original["month_end"], 400000)

    def test_refund_is_in_observed_spending_once(self):
        now, past, months = series()
        original = trend.forecast(now, past, TODAY, months=months)
        result = trend.forecast([*now, payment(TODAY, -20000)], past, TODAY, months=months)
        self.assertEqual(result["remaining"], original["remaining"])
        self.assertEqual(result["month_end"], original["month_end"] - 20000)

    def test_only_large_payments_have_no_separate_repeat_forecast(self):
        result = trend.forecast([payment(TODAY, 400000, "HOUSEHOLD")], [], TODAY)
        self.assertEqual(result["state"], "large_only")
        self.assertNotIn("month_end", result)

    def test_uncollected_days_block_forecasts(self):
        now, past, months = series()
        result = trend.forecast(now, past, TODAY, complete=False, months=months)
        self.assertEqual(result["state"], "unavailable")
        self.assertIn("수집되지", result["reason"])

    def test_many_payments_on_one_day_are_not_a_repeated_pattern(self):
        result = trend.forecast([payment(TODAY, 1000) for _ in range(30)], [], TODAY)
        self.assertEqual(result["state"], "unavailable")
        self.assertNotIn("validation", result)

    def test_current_and_historical_fixed_payments_do_not_change_variable_forecast(self):
        now, past, months = series()
        original = trend.forecast(now, past, TODAY, months=months)
        fixed_history = payment(date(2026, 9, 1), 900000, funding="fixed")
        result = trend.forecast([*now, payment(TODAY, 300000, funding="fixed")], [*past, fixed_history], TODAY, months=months)
        self.assertEqual(result, original)
        self.assertEqual([w["amount"] for w in result["weeks"]], [70000] * 4)

    def test_month_end_is_not_reported_as_a_forecast(self):
        today = date(2026, 11, 30)
        now, past, months = series(today=today)
        result = trend.forecast(now, past, today, months=months)
        self.assertEqual(result["state"], "unavailable")
        self.assertIn("오늘이 월말", result["reason"])

    def test_input_order_does_not_change_selection(self):
        now, past, months = series("rise")
        result = trend.forecast(list(reversed(now)), list(reversed(past)), TODAY, months=months)
        self.assertEqual(result, evaluate("rise"))

    def test_baseline_only_uses_the_last_three_complete_months(self):
        now, past, months = series()
        grouped = trend.summarize([*now, payment(TODAY, 500000, "HOUSING_TELECOM", "fixed")],
                                  [*past, payment(date(2026, 9, 1), 500000, funding="fixed")], TODAY, months, complete=True)
        self.assertNotIn("HOUSING_TELECOM", grouped)
        result = grouped["FOOD"]
        self.assertEqual(result["baseline"]["periods"], ["2026-08", "2026-09", "2026-10"])
        self.assertEqual(result["baseline"]["totals"], [310000, 300000, 310000])
        self.assertEqual(result["baseline"]["amount"], 310000)

    def test_internal_history_window_handles_year_boundaries(self):
        months = trend.history_months(date(2027, 2, 12))
        self.assertEqual(months[0].start, date(2026, 8, 1))
        self.assertEqual(months[-1].end_before, date(2027, 2, 1))

"""소비 유형 v2 (local_workspace/reports/소비유형_판정방식_v2_20261116.md) — 두 축 판정을 고정한다."""
from copy import deepcopy

from django.test import SimpleTestCase

from budgets.protect import RATES
from . import personality
from .personality import assess, describe, habit_lines, percentile

PACE = {"kind": "budget_ok", "conclusion": "생활비는 예산 속도에 맞게 쓰고 있어요."}
ALONE = {"housing": "alone", "age": 28, "base": 2_500_000}


def history(**overrides):
    return {"state": "ready", "change_count": 0, "categories": [],
            "period": {"from": "2026-10-25", "to": "2026-11-25", "as_of": "2026-11-16"}, **overrides}


def months(*rows, total=1_000_000, largest=None, unclassified=0, habits=None):
    """rows: 달마다 {코드: 금액}. habits: 달마다 반복 활동 목록 (예: ["dining"])"""
    out = []
    for i, cats in enumerate(rows):
        out.append({"label": f"{8 + i}월", "total": total, "categories": dict(cats),
                    "largest": (largest or [{}] * len(rows))[i] or {code: amount // 10 for code, amount in cats.items()},
                    "unclassified": unclassified, "habit_kinds": (habits or [[]] * len(rows))[i]})
    return out


def summary(taste=(), findings=(), snapshot=(), coverage="complete"):
    return {"basis": {"coverage": coverage}, "snapshot": list(snapshot),
            "story": {"taste_months": list(taste), "findings": list(findings)}}


DINING = [["dining"]] * 3
HABIT_FINDING = {"id": "HABIT", "kind": "habit", "category": "FOOD", "conclusion": "식비에서는 평일 점심 외식 결제가 매달 반복돼요."}


class TasteAxisTest(SimpleTestCase):
    def test_unclassified_only_month_counts_toward_hold_threshold(self):
        taste = months(*[{"BEAUTY": 120000}] * 3)
        taste[-1].update(total=0, categories={}, largest={}, unclassified=2_000_000)
        result = assess(summary(taste), history(), ALONE)
        self.assertEqual((result["profile"], result["reason"]), (None, "too_unclassified"))
        self.assertEqual(result["traits"][0]["months"], ["8월", "9월", "10월"])

    def test_recent_window_keeps_zero_months_without_division_by_zero(self):
        taste = months(*[{"BEAUTY": 120000}] * 4)
        for m in taste[-2:]:
            m.update(total=0, categories={}, largest={}, unclassified=10000)
        result = assess(summary(taste), history(), ALONE)
        self.assertIsNone(result["profile"])
        self.assertIsNone(result["reason"])
        self.assertEqual(result["traits"][0]["months"], ["9월", "10월", "11월"])

    def test_completed_month_habit_decides_food_type_with_own_history_reason(self):
        s = summary(months({"FOOD": 450000}, {"FOOD": 430000}, {"FOOD": 470000}, habits=DINING), [HABIT_FINDING])
        before = deepcopy(s)
        result = describe(s, history(), ALONE)
        self.assertEqual((result["title"], result["mascot"]), ("맛있는 게 제일 좋아형", "food"))
        self.assertEqual(result["lines"][0], HABIT_FINDING["conclusion"])
        self.assertEqual(s, before)

    def test_this_month_alone_or_one_completed_month_never_decides_a_type(self):
        snapshot = [{"kind": "composition", "category": "FOOD", "conclusion": "식비에 집중됐어요.", "impact": 1}]
        for taste in ((), months({"FOOD": 600000}, habits=[["dining"]])):
            with self.subTest(n=len(taste)):
                result = assess(summary(taste, snapshot=snapshot), history(), ALONE)
                self.assertIsNone(result["profile"])
                self.assertEqual(result["reason"], "short_history")

    def test_categories_compare_by_percentile_so_food_does_not_always_win(self):
        # 식비 45%(실측 중앙값 근처) vs 뷰티 10%(상위 15% 안팎) → 뷰티가 더 도드라진다
        taste = months(*[{"FOOD": 450000, "BEAUTY": 100000}] * 3, habits=DINING)
        result = describe(summary(taste, [HABIT_FINDING]), history(), ALONE)
        self.assertEqual(result["mascot"], "beauty")
        self.assertIn("최근 완료된 3개월 생활비의 평균 10%가 뷰티·미용이에요.", result["lines"])
        self.assertFalse(any("상위" in line or "백분위" in line for line in result["lines"]))  # 백분위는 화면에 쓰지 않는다

    def test_share_comparison_is_only_for_young_single_households(self):
        plain = months(*[{"FOOD": 400000, "BEAUTY": 100000}] * 3)
        dining = months(*[{"FOOD": 400000, "BEAUTY": 100000}] * 3, habits=DINING)
        for context in ({**ALONE, "housing": "family"}, {**ALONE, "age": 38}, {**ALONE, "age": None}):
            with self.subTest(context=context):
                # 실측 분포 대상 밖이면 비중으로 판정하지 않고 반복 활동 습관만 본다
                self.assertIsNone(describe(summary(plain), history(), context))
                self.assertEqual(describe(summary(dining, [HABIT_FINDING]), history(), context)["mascot"], "food")

    def test_habit_must_repeat_in_the_recent_months_not_only_in_older_ones(self):
        # 5~8월 외식 반복(story 습관은 6개월 중 4개월로 인정) → 최근 3개월(8~10월)에는 1개월뿐이면 식비 유형이 아니다
        old_habit = months(*[{"FOOD": 400000}] * 3, habits=[["dining"], [], []])
        result = assess(summary(old_habit, [HABIT_FINDING]), history(), {**ALONE, "housing": "family"})
        self.assertIsNone(result["profile"])
        food = next(t for t in result["traits"] if t["code"] == "FOOD")
        self.assertEqual((food["status"], food["habit"]), ("not_met", False))
        # 최근 3개월 중 2개월이면 과반이라 인정
        recent = months(*[{"FOOD": 400000}] * 3, habits=[["dining"], [], ["cafe"]])
        self.assertEqual(describe(summary(recent, [HABIT_FINDING]), history(), {**ALONE, "housing": "family"})["mascot"], "food")

    def test_one_off_large_payment_month_is_left_out(self):
        spike = months({"FASHION_SHOPPING": 30000}, {"FASHION_SHOPPING": 400000}, {"FASHION_SHOPPING": 30000},
                       largest=[{"FASHION_SHOPPING": 20000}, {"FASHION_SHOPPING": 380000}, {"FASHION_SHOPPING": 20000}])
        result = assess(summary(spike), history(), ALONE)
        fashion = next(t for t in result["traits"] if t["code"] == "FASHION_SHOPPING")
        self.assertEqual(fashion["months"], ["8월", "10월"])
        self.assertIsNone(result["profile"])  # 3%대 두 달만 남으면 많이 쓰는 편이 아니다

    def test_regular_single_monthly_payment_is_not_a_one_off(self):
        # 매달 미용실 한 번(결제 1건 = 그 달 전부)은 일회성이 아니다
        taste = months(*[{"BEAUTY": 120000}] * 3, largest=[{"BEAUTY": 120000}] * 3)
        self.assertEqual(describe(summary(taste), history(), ALONE)["mascot"], "beauty")

    def test_amount_floor_and_unclassified_hold_back_the_taste_axis(self):
        small = months(*[{"BEAUTY": 20000}] * 3, total=150000)
        self.assertIsNone(describe(summary(small), history(), ALONE))  # 13%여도 배분 기준의 1%(2.5만) 미만
        cloudy = months(*[{"BEAUTY": 120000}] * 3, unclassified=500000)
        result = assess(summary(cloudy), history(), ALONE)
        self.assertEqual((result["profile"], result["reason"]), (None, "too_unclassified"))

    def test_necessity_and_travel_spending_never_become_a_type(self):
        for code in ("TRANSPORT_CAR", "MEDICAL_HEALTH", "HOUSEHOLD", "EDUCATION", "TRAVEL_LODGING"):
            with self.subTest(code=code):
                self.assertIsNone(describe(summary(months(*[{code: 600000}] * 3)), history(), ALONE))

    def test_every_taste_type_has_a_name_and_mascot_without_emoji(self):
        for code, (title, desc, mascot) in personality.TASTE_TYPES.items():
            self.assertTrue(desc and mascot)
            self.assertNotRegex(title, r"[^\w\s·]")
            self.assertIn(code, personality.SHARE_DIST)

    def test_percentile_is_monotonic_and_bounded(self):
        for code, grid in personality.SHARE_DIST.items():
            self.assertEqual(len(grid), 21)
            values = [percentile(code, s / 100) for s in range(0, 101)]
            self.assertEqual(values, sorted(values))
            self.assertEqual((values[0], values[-1]), (0.0, 1.0))
        self.assertAlmostEqual(percentile("FOOD", 0.5295), 0.75)


class ModelReferenceTest(SimpleTestCase):
    taste = months(*[{"FOOD": 450000, "BEAUTY": 100000}] * 3)

    def model(self, beauty):
        return {"amounts": {"BEAUTY": 50000, "FOOD": 400000}, "mae": {"BEAUTY": 54000, "FOOD": 179000},
                "version": "v13_model_c", "label": "10월", "actual": {"BEAUTY": beauty, "FOOD": 450000}}

    def test_model_c_adds_one_line_only_when_far_beyond_error(self):
        high = describe(summary(self.taste), history(), {**ALONE, "model": self.model(120000)})
        self.assertIn("10월 뷰티·미용은 비슷한 조건을 반영한 모델 예상(약 50,000원)보다 높은 편이에요.", high["lines"])
        # 본인 기록 근거가 이미 2줄(습관 + 비중)이어도 모델 문장은 빠지지 않는다
        food = months(*[{"FOOD": 600000}] * 3, habits=DINING)
        model = {**self.model(0), "amounts": {"FOOD": 300000}, "mae": {"FOOD": 179000}, "actual": {"FOOD": 600000}}
        both = describe(summary(food, [HABIT_FINDING]), history(), {**ALONE, "model": model})
        self.assertEqual(both["lines"][:3], [HABIT_FINDING["conclusion"], "최근 완료된 3개월 생활비의 평균 60%가 식비예요.",
                                             "10월 식비는 비슷한 조건을 반영한 모델 예상(약 300,000원)보다 높은 편이에요."])
        near = describe(summary(self.taste), history(), {**ALONE, "model": self.model(90000)})  # 차이 4만 < MAE 5.4만
        self.assertFalse(any("모델" in line for line in near["lines"]))
        self.assertFalse(any("비슷한 사람" in line for line in high["lines"]))

    def test_model_installation_never_changes_the_type(self):
        without = describe(summary(self.taste), history(), {**ALONE, "model": {"reason": "not_installed"}})
        with_model = describe(summary(self.taste), history(), {**ALONE, "model": self.model(200000)})
        self.assertEqual((without["title"], without["tags"]), (with_model["title"], with_model["tags"]))
        model_trait = next(t for t in without["traits"] if t["basis"] == "model_c")
        self.assertEqual((model_trait["status"], model_trait["reason"]), ("unavailable", "not_installed"))


class ManageAxisTest(SimpleTestCase):
    SAVER = {**ALONE, "savings_floor": 800_000, "saving_seen": True}

    def test_service_saving_rate_matches_budget_policy(self):
        self.assertEqual((RATES["alone"]["saving"], RATES["family"]["saving"]), (30, 40))

    def test_saver_needs_plan_at_or_above_recommended_and_a_confirmed_transfer(self):
        result = describe(summary(), history(change_count=1), self.SAVER)
        self.assertEqual((result["title"], result["mascot"]), ("차곡차곡 모으는형", "saver"))
        self.assertEqual(result["lines"][0], "정해 둔 월 저축 800,000원이 서비스 권장 저축액(750,000원) 이상이에요.")
        for context in ({**self.SAVER, "savings_floor": 700_000}, {**self.SAVER, "saving_seen": False},
                        {**self.SAVER, "base": None}, {**self.SAVER, "housing": None}):
            with self.subTest(context=context):
                self.assertIsNone(describe(summary(), history(change_count=1), context))
        family = describe(summary(), history(change_count=1), {**self.SAVER, "housing": "family", "savings_floor": 1_000_000})
        self.assertEqual(family["lines"][0], "정해 둔 월 저축 1,000,000원이 서비스 권장 저축액(1,000,000원) 이상이에요.")

    def test_taste_type_leads_and_management_becomes_tags(self):
        taste = months(*[{"FOOD": 450000, "BEAUTY": 100000}] * 3)
        changes = history(change_count=2, categories=[{"code": "FOOD", "increases": 2, "topups": 2, "increase_amount": 60000}])
        result = describe(summary(taste), changes, self.SAVER)
        self.assertEqual(result["mascot"], "beauty")
        self.assertEqual([t["code"] for t in result["tags"]], ["saver", "stretch"])
        self.assertIn("추가 증액 전에", result["action"])
        self.assertLessEqual(len(result["lines"]), 5)

    def test_repeated_top_ups_are_a_tag_not_the_type(self):
        changes = history(change_count=3, categories=[{"code": "FOOD", "increases": 3, "topups": 3, "increase_amount": 90000}])
        result = describe(summary(), changes, ALONE)
        self.assertEqual((result["mascot"], result["tags"]), ("basic", [{"code": "stretch", "label": "이번만 조금 더"}]))
        self.assertEqual(result["lines"][0], "완료된 달이 2개월 이상 쌓이면 소비 취향 유형을 정해요.")
        self.assertIn("10월 25일~11월 24일", result["lines"][1])

    def test_moves_or_one_increase_are_not_repeated_top_ups(self):
        for row in ({"code": "FOOD", "increases": 2, "topups": 0}, {"code": "FOOD", "increases": 1, "topups": 1}):
            with self.subTest(row=row):
                self.assertIsNone(describe(summary(), history(change_count=2, categories=[row]), ALONE))

    def test_planner_needs_unchanged_budget_pace_and_complete_collection(self):
        s = summary(snapshot=[PACE])
        self.assertEqual(describe(s, history(), ALONE)["title"], "계획 척척형")
        self.assertIsNone(describe(s, history(change_count=1), ALONE))
        self.assertIsNone(describe(s, {"state": "unavailable"}, ALONE))
        self.assertIsNone(describe(summary(snapshot=[PACE], coverage="partial"), history(), ALONE))
        fast = {**PACE, "conclusion": "생활비를 예산보다 조금 빠르게 쓰고 있어요."}
        self.assertIsNone(describe(summary(snapshot=[fast]), history(), ALONE))

    def test_category_scoped_question_gets_no_card(self):
        s = summary(months(*[{"BEAUTY": 120000}] * 3), snapshot=[PACE])
        self.assertIsNone(describe(s, history(), {**ALONE, "scoped": True}))


class HabitLinesTest(SimpleTestCase):
    def test_habit_lines_show_only_changed_directions_with_amounts(self):
        changes = history(change_count=2, categories=[{"code": "FOOD", "name": "식비", "increases": 2, "decreases": 0,
                                                      "increase_amount": 80000}], savings={"decreases": 1, "decrease_amount": 30000})
        lines = habit_lines(changes, None)
        self.assertEqual(lines[1], "식비: 2번 늘림(+80,000원)")
        self.assertIn("저축 계획을 1번 줄였어요(-30,000원)", lines[2])
        self.assertIn("지켜 보면", lines[-1])
        self.assertEqual(habit_lines(history(), None), ["이번 급여 주기(10월 25일~11월 24일)에는 승인한 예산을 한 번도 바꾸지 않았어요."])
        self.assertEqual(habit_lines({"state": "unavailable"}, None), [])

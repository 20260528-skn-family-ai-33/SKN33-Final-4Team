"""이번 달 내역만으로 하는 분석(snapshot)과 '판단 못 한 말 · 당연한 말을 본문에 내지 않는다' 규칙 (11/13).
외부 호출 · DB 없음. 금액은 모두 독립 산술로 확인한다."""
import re
from dataclasses import replace
from datetime import date, timedelta
from unittest import mock

from django.test import SimpleTestCase

from chat.llm.fake import FakeReading
from . import dialog, insight, snapshot
from .periods import Period
from .reading import SpendingReading

TODAY = date(2026, 11, 20)
START = date(2026, 11, 1)
# 사용자가 싫다고 한 문장들: 판단하지 못했다는 말 · 하지 않은 일 설명 · 발견 없는 당연한 말
BANNED = re.compile(r"어려워요|단정|판단하지|판단하기|근거가 부족|나눠 쓴 돈이 쌓이는|원래도 있던|보지 않았|강조하지 않았|확정하지 않았")


def pay(day, amount, category="FOOD", *, activity=None, time="12:30", month=11, funding="living"):
    return {"net_amount": amount, "category": category, "occurred_on": date(2026, month, day), "occurred_time": time,
            "funding": funding, "transaction_type": "spending", "activity": activity, "merchant_name": "비공개"}


def budget(rows, days_left=10, start=START, end=date(2026, 12, 1)):
    """tools.budget_left(..., breakdown=True) 모양. rows = [(코드, 예산, 쓴 돈)]"""
    categories = [{"category": c, "allocated": a, "spent": s, "difference": a - s, "remaining": max(a - s, 0), "over": max(s - a, 0)}
                  for c, a, s in rows]
    allocated, spent = sum(a for _, a, _ in rows), sum(s for _, _, s in rows)
    return {"budget": {"state": "approved"}, "cycle_period": Period(start, end), "days_left": days_left,
            "confirmed": {"allocated": allocated, "spent": spent, "difference": allocated - spent,
                          "remaining": max(allocated - spent, 0), "over": max(spent - allocated, 0)},
            "categories": categories}


class JosaTest(SimpleTestCase):
    def test_particles_follow_final_consonant(self):
        self.assertEqual(snapshot.josa("뷰티·미용", "은는"), "뷰티·미용은")
        self.assertEqual(snapshot.josa("식비", "은는"), "식비는")
        self.assertEqual(snapshot.josa("교통·차량", "을를"), "교통·차량을")
        self.assertEqual(snapshot.josa("문화·여가", "을를"), "문화·여가를")
        self.assertEqual(snapshot.short(15000), "1.5만 원")
        self.assertEqual(snapshot.short(8000), "8,000원")


class BudgetPaceTest(SimpleTestCase):
    def food_rows(self, n):
        return [pay(d, 10000) for d in range(1, n + 1)]

    def test_fast_category_with_remaining_per_day_and_slack_source(self):
        # 30일 주기 중 20일 (67%) · 식비 52만 원 중 47만 원(90%) · 교통은 10만 원 중 1만 원
        found = snapshot.budget_pace(budget([("FOOD", 520000, 470000), ("TRANSPORT_CAR", 100000, 10000)], days_left=10),
                                     TODAY, rows=self.food_rows(12))
        self.assertEqual(len(found), 1)
        f = found[0]
        self.assertEqual(f["kind"], "budget_fast")
        self.assertIn("주기의 67%가 지났는데 예산의 90%를 썼어요", f["conclusion"])
        self.assertIn("남은 10일 동안 쓸 수 있는 돈은 50,000원", f["evidence"][0])
        self.assertIn("교통·차량(남은 9만 원)", " ".join(f["evidence"]))
        self.assertIn("하루 5,000원 안으로", f["actions"][0])
        self.assertIn("교통·차량에서 옮기는", f["actions"][1])
        self.assertEqual(f["visual"]["kind"], "pace")
        self.assertEqual(f["visual"]["elapsed"], 67)

    def test_over_budget_is_reported_even_early(self):
        found = snapshot.budget_pace(budget([("BEAUTY", 50000, 65000)]), date(2026, 11, 3), rows=[pay(2, 65000, "BEAUTY")])
        self.assertEqual(found[0]["kind"], "budget_fast")
        self.assertIn("예산을 이미 1.5만 원 넘었어요", found[0]["conclusion"])
        self.assertIn("뷰티·미용은", found[0]["conclusion"])

    def test_early_cycle_or_once_a_month_purchase_is_not_called_fast(self):
        # 5일째 미용실 한 번(예산의 80%)은 '빨리 쓴다'가 아니라 한 달에 한 번 사는 것
        found = snapshot.budget_pace(budget([("BEAUTY", 50000, 40000), ("FOOD", 520000, 90000)]), date(2026, 11, 5),
                                     rows=[pay(3, 40000, "BEAUTY")])
        self.assertTrue(all(f["kind"] != "budget_fast" for f in found))
        found = snapshot.budget_pace(budget([("BEAUTY", 50000, 40000), ("FOOD", 520000, 300000)]), TODAY,
                                     rows=[pay(3, 40000, "BEAUTY")])
        self.assertTrue(all(f["category"] != "BEAUTY" for f in found))

    def test_on_pace_is_a_short_positive_statement_not_a_warning(self):
        found = snapshot.budget_pace(budget([("FOOD", 520000, 300000)]), TODAY, rows=self.food_rows(12))
        self.assertEqual(found[0]["kind"], "budget_ok")
        self.assertIn("예산 속도에 맞게", found[0]["conclusion"])

    def test_no_confirmed_budget_makes_nothing(self):
        self.assertEqual(snapshot.budget_pace({"budget": {"state": "no_approved"}, "confirmed": None}, TODAY), [])


class SnapshotPatternTest(SimpleTestCase):
    def test_large_payments_share_and_rest(self):
        rows = [pay(d, 10000) for d in range(1, 9)] + [pay(10, 120000, "FASHION_SHOPPING")]
        f = snapshot.large_share(rows)[0]
        self.assertIn("큰 결제 1건(12만 원)이 이번 달 소비의 60%예요", f["conclusion"])
        self.assertIn("나머지 소비는 80,000원", f["evidence"][0])

    def test_spread_spending_is_not_a_large_payment_story(self):
        self.assertEqual(snapshot.large_share([pay(d, 10000 + d) for d in range(1, 15)]), [])

    def test_frequent_cafe_with_grounded_action(self):
        rows = [pay(d, 4000, activity="cafe", time="13:00") for d in range(2, 20) if date(2026, 11, d).weekday() < 5]
        f = snapshot.frequent(rows, TODAY, START, date(2026, 12, 1))[0]
        self.assertIn("평일 점심 무렵 카페·간식", f["conclusion"])
        self.assertIn("한 번에 평균 4,000원", f["evidence"][0])
        self.assertIn("주 2번만 줄이면 남은 1주 동안 약 8,000원", f["actions"][0])

    def test_weekend_daily_average_uses_elapsed_days(self):
        rows = [pay(d, 50000) for d in range(1, 21) if date(2026, 11, d).weekday() >= 5] + \
               [pay(d, 10000) for d in range(1, 21) if date(2026, 11, d).weekday() < 5]
        f = snapshot.weekend(rows, START, TODAY)[0]
        self.assertIn("주말 하루에 평일의 5.0배", f["conclusion"])

    def test_even_week_is_not_a_weekend_finding(self):
        self.assertEqual(snapshot.weekend([pay(d, 10000) for d in range(1, 21)], START, TODAY), [])

    def test_composition_only_when_one_category_dominates(self):
        rows = [pay(d, 30000) for d in range(1, 11)] + [pay(3, 40000, "CULTURE_LEISURE"), pay(4, 10000, "BEAUTY")]
        self.assertIn("86%가 식비에 몰려", snapshot.composition(rows)[0]["conclusion"])
        self.assertEqual(snapshot.composition([pay(1, 30000), pay(2, 30000, "CULTURE_LEISURE")]), [])

    def test_change_total_names_categories_and_early_monthly_purchases(self):
        now = [pay(d, 10000) for d in range(1, 6)] + [pay(2, 40000, "BEAUTY"), pay(3, 30000, "HOUSEHOLD")]
        before = [pay(d, 10000, month=10) for d in range(1, 6)]
        full = before + [pay(18, 40000, "BEAUTY", month=10), pay(25, 30000, "HOUSEHOLD", month=10)]
        f = snapshot.change_total(now, before, full, {"meaningful": True})[0]
        self.assertIn("7만 원 더 썼어요", f["conclusion"])
        self.assertIn("뷰티·미용(+4만 원)", f["conclusion"])
        self.assertIn("평소보다 일찍 산 것", f["evidence"][0])
        self.assertEqual(snapshot.change_total(now, before, full, {"meaningful": False}), [])


class AnswerContractTest(SimpleTestCase):
    """요구사항 1 · 2 · 12: 어떤 자료 조건에서도 본문은 해석으로 시작하고, 판단 못 한 말은 하단으로만"""

    def setUp(self):
        self.current, self.previous = [], []
        mock.patch.object(insight.tools, "rows", side_effect=lambda _u, p, *_a, **_k:
                          [r for r in (self.current if p.start.month == 11 else self.previous)
                           if p.start <= r["occurred_on"] < p.end_before]).start()
        self.cover = mock.patch.object(insight, "coverage", return_value={"state": "complete"}).start()
        mock.patch.object(insight, "covered_through", return_value=None).start()
        mock.patch.object(insight.tools, "fixed_forecast", return_value={"paid": 0, "remaining": 0}).start()
        mock.patch.object(insight.tools, "budget_left", return_value=None).start()
        self.addCleanup(mock.patch.stopall)

    def ask(self, goal=None, complete=None):
        reading = SpendingReading(mine=True, target="overview", analysis_goal=goal)
        d = replace(dialog.decide(reading, "분석해줘", today=TODAY), interpretation=reading)
        reply, _ = insight.answer(object(), d, TODAY, complete or mock.Mock(side_effect=TimeoutError()))
        return reply

    def check(self, reply):
        body = " ".join(reply["lines"])
        self.assertFalse(BANNED.search(body), body)
        self.assertEqual(reply["view"][1]["title"], "핵심 진단")
        self.assertLessEqual(len(reply["notices"]), 7)
        return body

    def test_every_goal_on_partial_short_and_changed_data(self):
        scenarios = {
            "no_history_partial": ([pay(d, 9000, activity="dining") for d in range(2, 20)] + [pay(5, 150000, "FASHION_SHOPPING")], [], "partial"),
            "large_payment": ([pay(d, 10000) for d in range(1, 20)] + [pay(12, 400000, "TRAVEL_LODGING")],
                              [pay(d, 10000, month=10) for d in range(1, 20)], "complete"),
            "flat": ([pay(d, 10000) for d in range(1, 20)], [pay(d, 10000, month=10) for d in range(1, 20)], "complete"),
            "mix_changed": ([pay(d, 20000, activity="delivery", time="21:00") for d in range(1, 20)] + [pay(3, 30000, "CULTURE_LEISURE")],
                            [pay(d, 20000, activity="dining", month=10) for d in range(1, 20)] + [pay(3, 30000, "CULTURE_LEISURE", month=10)], "complete"),
            "unclassified": ([pay(d, 10000) for d in range(1, 20)] + [pay(4, 5000, "UNCLASSIFIED")],
                             [pay(d, 10000, month=10) for d in range(1, 20)], "complete"),
        }
        for name, (now, before, state) in scenarios.items():
            for goal in (None, "reason", "habit", "forecast"):
                with self.subTest(name=name, goal=goal):
                    self.current, self.previous = now, before
                    self.cover.return_value = {"state": state}
                    body = self.check(self.ask(goal))
                    self.assertTrue(body)

    def test_large_payment_is_the_answer_to_why_so_much(self):
        self.current = [pay(d, 10000) for d in range(1, 20)] + [pay(12, 400000, "TRAVEL_LODGING")]
        self.previous = [pay(d, 10000, month=10) for d in range(1, 20)]
        reply = self.ask("reason")
        lead = reply["view"][1]["lines"][1]
        self.assertIn("여행·숙박 큰 결제 1건(40만 원)", lead)
        self.assertIn("빼면 지난달과 거의 같아요", lead)

    def test_recent_days_not_loaded_are_explained_once_and_dates_follow_the_clip(self):
        self.current = [pay(d, 10000) for d in range(1, 6)]
        with mock.patch.object(insight, "covered_through", return_value=date(2026, 11, 5)):
            reply = self.ask()
        self.assertIn("11월 1일~5일", reply["view"][0]["title"])
        self.assertIn("11월 6일 이후 내역은 아직 불러오지 않아 11월 5일까지 기준", reply["notices"][0])

    def test_over_budget_outlook_offers_budget_adjust_and_follow_up_questions(self):
        from .test_story import month_rows
        self.current = [r for r in month_rows(11, lunch=25000) if r["occurred_on"] <= TODAY]
        self.previous = [r for m in (7, 8, 9, 10) for r in month_rows(m)]
        mock.patch.object(insight.tools, "rows", side_effect=lambda _u, p, *_a, **_k:
                          [r for r in (*self.previous, *self.current) if p.start <= r["occurred_on"] < p.end_before]).start()
        budget = {"budget": {"state": "approved", "allocated": 500000}, "cycle_period": Period(START, date(2026, 12, 1)),
                  "confirmed": None, "categories": [{"category": "FOOD", "allocated": 450000}, {"category": "BEAUTY", "allocated": 50000}]}
        with mock.patch.object(insight.tools, "budget_left", return_value=budget):
            reply = self.ask("forecast")
        self.check(reply)
        self.assertIn("넘을 거예요", reply["view"][1]["lines"][1])
        self.assertEqual(reply["suggest"], ["budget_adjust"])
        self.assertEqual(reply["over"]["category"], "FOOD")
        self.assertTrue(reply["examples"])
        self.assertTrue(any(b["kind"] == "months" for b in reply["view"]))

    def test_llm_narrative_path_also_passes_the_contract(self):
        self.current = [pay(d, 9000, activity="dining") for d in range(2, 20)] + [pay(5, 150000, "FASHION_SHOPPING")]
        self.check(self.ask("habit", FakeReading()))


class CoveredThroughTest(SimpleTestCase):
    def target(self, start, end_before):
        return {"covered": [Period(start, end_before)]}

    def test_recent_days_not_yet_loaded_clip_the_analysis_period(self):
        found = {"state": "partial", "undetermined": [], "targets": [self.target(START, date(2026, 11, 6)), self.target(START, date(2026, 11, 9))]}
        with mock.patch.object(insight, "coverage", return_value=found):
            self.assertEqual(insight.covered_through(object(), date(2026, 11, 13)), date(2026, 11, 5))

    def test_gap_from_the_first_day_or_manual_source_is_not_clipped(self):
        for found in ({"state": "partial", "undetermined": [], "targets": [self.target(date(2026, 11, 3), date(2026, 11, 9))]},
                      {"state": "partial", "undetermined": [{"kind": "card"}], "targets": [self.target(START, date(2026, 11, 9))]},
                      {"state": "complete", "targets": []}):
            with mock.patch.object(insight, "coverage", return_value=found):
                self.assertIsNone(insight.covered_through(object(), date(2026, 11, 13)))


class MenuAndCalculationTest(SimpleTestCase):
    """'소비 분석해줘' · '또 뭐 분석해 줄 수 있어?' → 분석 메뉴, '왜 이렇게 계산됐어?' → 계산 방법 (같은 분석을 다시 내보내지 않는다)"""
    def test_menu_phrases(self):
        for text in ("소비 분석해줘", "분석해줘", "또 뭐 분석해줄 수 있어?", "어떤 분석 할 수 있어?", "소비 분석으로 뭘 볼 수 있어?", "분석 기능 뭐 있어"):
            self.assertTrue(insight.wants_menu(text), text)
        for text in ("아무거나 분석해줘", "전체적으로 분석해줘", "식비에서 뭐가 늘었는지 분석해줘", "나 뭐에 돈 제일 많이 써?",
                     "이번 달 식비 분석해줘", "분석해서 뭐가 문제인지 알려줘", "", None):
            self.assertFalse(insight.wants_menu(text), text)

    def test_calculation_phrases(self):
        for text in ("왜 이렇게 계산된거야?", "어떻게 계산했어?", "86.6만 원은 어떻게 나온 거야?", "계산 방법 알려줘", "무슨 기준이야?"):
            self.assertTrue(insight.wants_calculation(text), text)
        for text in ("이번 달 생활비 왜 이렇게 많이 나왔지?", "왜 이렇게 많이 썼어", "이대로 쓰면 이번 달 얼마나 쓸까?", "식비 계산해줘"):
            self.assertFalse(insight.wants_calculation(text), text)

    def test_menu_reply_lists_options_with_a_question_each_and_reads_no_db(self):
        reply, reason = insight.answer(None, None, TODAY, None, question="소비 분석해줘")
        self.assertIsNone(reason)
        options = next(b for b in reply["view"] if b["kind"] == "options")["items"]
        self.assertEqual([o["title"] for o in options], [t for t, _, _ in insight.MENU])
        self.assertTrue(all(o["desc"] and o["ask"] for o in options))
        for o in options:  # 메뉴의 질문을 누르면 메뉴가 아니라 그 분석으로 간다
            self.assertFalse(insight.wants_menu(o["ask"]), o["ask"])

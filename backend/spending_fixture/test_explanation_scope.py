"""설명 범위·결론 통합 회귀: 더미 DB, 실제 조회·검사, 외부 LLM 호출 없음."""
import copy
import json
from unittest.mock import patch

from django.test import TestCase

from spending_analysis import claims as C, dialog, explain, tools
from spending_analysis.reading import SpendingReading

from . import seed
from .test_fixture import DESIGN, TODAY, fixture_db_only, user


@fixture_db_only
class ExplanationScopeIntegrationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def item(self, **kwargs):
        reading = SpendingReading(mine=True, explain=True, categories=("FOOD",), **kwargs)
        decision = dialog.decide(reading, "설명 요청", today=TODAY)
        self.assertEqual(decision.status, "ready")
        return decision.explain["items"][0]

    def test_direct_composition_queries_the_requested_dates(self):
        for period, expected in (("last_month", ("2026-09-01", "2026-09-30")),
                                 ("last_week", ("2026-10-19", "2026-10-25"))):
            with self.subTest(period=period):
                item = self.item(period=period, period_said=period, explain_kind="composition")
                ev = explain.evidence(user("A"), item, TODAY)
                self.assertEqual(ev["state"], "ok")
                self.assertEqual((str(ev["period"].start), str(ev["period"].last_day)), expected)
                out = explain.answer(user("A"), [item], today=TODAY)
                self.assertNotIn("이번 달", out["lines"][0])

    def test_fresh_comparison_passes_filters_to_both_queries(self):
        item = self.item(period="this_month", period_said="이번 달", explain_kind="change",
                         weekday=("weekend",), payment="card", include_fixed=True)
        with patch.object(tools, "spend_query", wraps=tools.spend_query) as read:
            ev = explain.evidence(user("A"), item, TODAY)
        self.assertEqual(ev["state"], "ok")
        self.assertEqual(read.call_count, 2)
        for call in read.call_args_list:
            self.assertEqual(call.kwargs["where"]["weekdays"], [5, 6])
            self.assertEqual(call.kwargs["where"]["payment"], "card")
            self.assertTrue(call.kwargs["where"]["include_fixed"])

    def test_recent_composition_keeps_disjoint_periods(self):
        item = self.item(period="recent", months=3, include_current=True, measure="sum", group_by="month",
                         explain_kind="composition")
        with patch.object(tools, "spend_query", wraps=tools.spend_query) as read:
            explain.evidence(user("A"), item, TODAY)
        self.assertEqual([(str(c.args[1].start), str(c.args[1].last_day)) for c in read.call_args_list],
                         [("2026-08-01", "2026-08-29"), ("2026-09-01", "2026-09-29"), ("2026-10-01", "2026-10-29")])

    def test_past_period_change_does_not_silently_compare_current_month(self):
        item = self.item(period="last_week", explain_kind="change")
        self.assertEqual(explain.evidence(user("A"), item, TODAY), {"state": "no_basis"})


@fixture_db_only
class HypothesisEvidenceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def setUp(self):
        self.item = {"type": 7, "category": "FOOD", "fresh": True}
        self.ev = explain.evidence(user("A"), self.item, TODAY)
        self.claims = C.first_claims(self.ev, self.item)

    def test_subset_hypothesis_never_uses_whole_scope_statistics(self):
        for axis, subject in (("count", "커피"), ("average", "점심"), ("average", "교통비")):
            with self.subTest(axis=axis, subject=subject):
                calls = []
                def complete(system, payload, timeout=None):
                    calls.append(system)
                    self.assertEqual(system, C.PLAN_PROMPT)  # 근거가 없으면 답 LLM을 부르지 않는다
                    return json.dumps({"queries": [], "reason": "대상 확인", "hypothesis": {"axis": axis, "subject": subject}})
                out = explain.answer(user("A"), [self.item], today=TODAY, plan=complete, question="가설 확인")
                self.assertEqual(len(calls), 1)
                self.assertEqual(out["trace"]["support"]["status"], "insufficient")
                self.assertIn("판단할 수 없어요", out["lines"][0])
                self.assertEqual([b["kind"] for b in out["view"]], ["text"])

    def test_same_metric_but_different_period_is_not_support(self):
        other = copy.deepcopy(next(c for c in self.claims if c["axis"] == "count"))
        other["conditions"]["basis"]["periods"] = [("2000-01-01", "2000-02-01")]
        self.assertEqual(C.support([self.claims[0], other], {"axis": "count", "subject": "식비"})["status"], "insufficient")

    def test_target_names_must_not_match_by_substring(self):
        lunch = next(c for c in self.claims if c["axis"] == "time_slot")
        self.assertEqual(lunch["subject"], "점심")
        self.assertFalse(C._matches(lunch, {"axis": "time_slot", "subject": "점"}))

    def test_whole_scope_average_is_only_an_observation_not_a_price_claim(self):
        supported = C.support(self.claims, {"axis": "average", "subject": "식비"})
        self.assertEqual(supported["status"], "observation")
        self.assertTrue(supported["claims"])
        self.assertIn("상품 가격이 올랐는지는 판단할 수 없어요", supported["limitation"])

    def test_unrelated_first_sentence_is_rejected(self):
        count = next(c["id"] for c in self.claims if c["axis"] == "count")
        raw = json.dumps({"sentences": [{"text": "{C1}", "claims": ["C1"]},
                                       {"text": "{" + count + "}", "claims": [count]}]})
        self.assertIn("0:hypothesis_first", C.check(raw, self.claims, {"axis": "count", "subject": None})[1])

    def test_invalid_plan_types_fail_closed(self):
        for hyp in ([], {"axis": "unknown", "subject": "점심"}, {"axis": "count", "subject": []}):
            self.assertIsNone(C._plan(json.dumps({"queries": [], "reason": "확인", "hypothesis": hyp})))

    def test_invalid_plan_does_not_become_an_answer_without_a_hypothesis(self):
        calls = []
        def complete(system, payload, timeout=None):
            calls.append(system)
            return json.dumps({"queries": [], "reason": "확인", "hypothesis": {"axis": "unknown"}})
        lines, trace = C.run(self.ev, self.item, "점심값이 비싸진 거야?", complete)
        self.assertIsNone(lines)
        self.assertEqual(trace["fallback"], "invalid_plan")
        self.assertEqual(calls, [C.PLAN_PROMPT])


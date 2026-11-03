"""설명 요청의 범위와 결론 회귀 검사. LLM·DB 없이 올바른 해석/잘못된 답을 주입한다."""
import json
from datetime import date

from django.test import SimpleTestCase

from . import claims, dialog
from .reading import SpendingReading


class ExplanationScopeTest(SimpleTestCase):
    def decide(self, **kwargs):
        return dialog.decide(SpendingReading(mine=True, explain=True, categories=("FOOD",), **kwargs),
                             "설명 요청", today=date(2026, 10, 29))

    def test_explicit_period_is_not_replaced_with_this_month(self):
        for period in ("last_month", "last_week", "this_week"):
            with self.subTest(period=period):
                out = self.decide(period=period, period_said=period, explain_kind="composition")
                self.assertEqual(out.status, "ready")
                self.assertEqual(out.explain["items"][0]["query"]["period"], period)

    def test_filters_survive_fresh_comparison(self):
        out = self.decide(period="this_month", weekday=("weekend",), payment="card",
                          include_fixed=True, explain_kind="change")
        item = out.explain["items"][0]
        self.assertEqual(item["type"], 7)
        self.assertEqual(item["weekdays"], [5, 6])
        self.assertEqual(item["payment"], "card")
        self.assertTrue(item["include_fixed"])

    def test_explanation_uses_normal_request_validation(self):
        for kwargs, status in (({"period_said": "작년"}, "ask"),
                               ({"compare": "other_period"}, "unsupported"),
                               ({"other_person": True}, "ask"),
                               ({"period": "last_month", "compare": "prev_month"}, "ask")):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.decide(**kwargs).status, status)

    def test_detail_target_is_not_silently_replaced_with_the_previous_category(self):
        reading = SpendingReading(mine=True, explain=True, categories=({"detail": "커피", "category": "FOOD"},),
                                   refers_to="previous")
        last = {"request": {"target": "spend", "period": "this_month", "compare": None, "months": None,
                            "categories": ["FOOD"]}, "items": []}
        out = dialog.decide(reading, "그중 커피는 왜 많이 나왔어?", last=last, today=date(2026, 10, 29))
        self.assertEqual((out.status, out.reason), ("ask", "detail_not_supported"))


class ClaimContractTest(SimpleTestCase):
    def test_malformed_claim_ids_are_rejected_without_crashing(self):
        known = [{"id": "C1", "axis": "scope", "kind": "change", "subject": "식비", "direction": "up", "text": "증가"}]
        for ids in ([{}], [None], [1]):
            raw = json.dumps({"sentences": [{"text": "{C1}", "claims": ids}]})
            self.assertIsNone(claims.check(raw, known)[0])

    def test_plan_keeps_the_subject_of_a_metric(self):
        for axis, subject in (("count", "커피"), ("average", "점심"), ("visit", "새로 생긴 가게")):
            plan = claims._plan(json.dumps({"queries": [], "reason": "확인", "hypothesis": {
                "axis": axis, "subject": subject}}))
            self.assertEqual(plan["hypothesis"]["subject"], subject)

    def test_declared_but_unrendered_claim_cannot_support_a_conclusion(self):
        known = [{"id": "C1", "axis": "scope", "subject": "식비", "kind": "change", "direction": "up", "rank": None,
                  "text": "식비는 늘었어요."},
                 {"id": "C2", "axis": "count", "subject": "결제 건수", "kind": "change", "direction": "down", "rank": None,
                  "text": "결제 건수는 줄었어요."}]
        raw = json.dumps({"sentences": [{"text": "{C1}", "claims": ["C1", "C2"]}]})
        self.assertIsNone(claims.check(raw, known, must=[["C2"]])[0])

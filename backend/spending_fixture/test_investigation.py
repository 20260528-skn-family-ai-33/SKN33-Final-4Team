"""대상·지표 계약과 목적 있는 조회. 금액 정답은 작은 거래 표를 손으로 계산한다."""
import json
from datetime import date
from unittest.mock import Mock
from django.test import SimpleTestCase, TestCase
from spending_analysis import claims as C, investigation as I, reading, tools, explain
from . import seed
from .test_fixture import DESIGN, TODAY, fixture_db_only, user


def hyp(metric, target, unavailable=None):
    return {"metric": metric, "target": target, "unavailable": unavailable}


class TargetContractTest(SimpleTestCase):
    def test_target_and_metric_are_separate_codes(self):
        self.assertEqual(I.internal(hyp("average", "slot:lunch"))["subject"], "점심")
        for bad in (hyp("average", "건당 금액"), hyp("average", "slot:lunch", "점심값"),
                    hyp("count", "unavailable"), hyp("average", "visit:new"), hyp("day_average", "scope")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                I.parse(bad)

    def test_reading_preserves_hypothesis_separately(self):
        parsed = reading.parse({"mine": True, "explain": True, "refers_to": "previous", "hypothesis": hyp("average", "slot:lunch")})
        self.assertEqual(parsed.categories, ())
        self.assertEqual(parsed.hypothesis, hyp("average", "slot:lunch"))
        with self.assertRaises(reading.ReadFailed):
            reading.parse({"mine": True, "hypothesis": hyp("count", "scope")})

    def test_current_category_is_scope_but_other_categories_stay_distinct(self):
        item = {"category": "FOOD"}
        self.assertIsNone(I.resolve(I.internal(hyp("amount", "category:FOOD")), item))
        self.assertEqual(I.resolve(I.internal(hyp("average", "category:FOOD")), item)["target"], "scope")
        self.assertEqual(I.resolve(I.internal(hyp("amount", "category:FOOD")), {"category": None})["target"], "category:FOOD")

    def test_schema_separates_unavailable_from_observable_targets(self):
        observable, unavailable, _ = I.SCHEMA["anyOf"]
        self.assertEqual(observable["properties"]["unavailable"]["type"], "null")
        self.assertNotIn("unavailable", observable["properties"]["target"]["enum"])
        self.assertEqual(unavailable["properties"]["target"]["enum"], ["unavailable"])
        delivery = next(t for t in I.catalog() if t["id"] == "online:배달앱")
        self.assertIn("배달음식", delivery["aliases"])

    def test_unknown_target_cannot_be_a_valid_plan(self):
        raw = json.dumps({"reason": "확인", "queries": [], "hypothesis": hyp("average", "평균")})
        self.assertIsNone(C._plan(raw))


@fixture_db_only
class InvestigationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def setUp(self):
        self.item = {"type": 7, "category": "FOOD", "fresh": True}
        self.ev = explain.evidence(user("A"), self.item, TODAY)

    def complete(self, hypothesis, queries=()):
        self.payloads = []
        def llm(system, payload, timeout=None):
            data = json.loads(payload)
            self.payloads.append(data)
            if system == C.PLAN_PROMPT:
                return json.dumps({"queries": list(queries), "reason": "필요한 비교", "hypothesis": hypothesis})
            ids = list(dict.fromkeys(g[0] for g in data["must"] if g))
            return json.dumps({"sentences": [{"text": "{"+cid+"}", "claims": [cid]} for cid in ids]})
        return llm

    def test_needed_average_is_queried_even_when_plan_says_no_extra_queries(self):
        target = hyp("average", "slot:lunch")
        lines, trace = C.run(self.ev, self.item, "점심 평균 질문", self.complete(target))
        self.assertIsNotNone(lines, trace)
        self.assertEqual(trace["support"]["status"], "observation")
        self.assertEqual([(q["target"], q["metric"]) for q in trace["queries"]], [("slot:lunch", "average")])
        self.assertEqual(trace["unused_queries"], [])
        self.assertIn("점심 건당 평균", lines[0])
        self.assertEqual(trace["cited"][0]["metric"], "average")

    def test_unavailable_product_does_not_trigger_irrelevant_queries(self):
        lines, trace = C.run(self.ev, self.item, "커피 질문", self.complete(hyp("count", "unavailable", "커피"),
                              [{"by": "time_slot", "focus": None}, {"by": "merchant", "focus": "배달"}]))
        self.assertEqual(trace["queries"], [])
        self.assertEqual(len(self.payloads), 1)
        self.assertIn("커피", lines[0])
        self.assertEqual(trace["target_resolution"], "unsupported_target")

    def test_existing_average_needs_no_more_queries(self):
        lines, trace = C.run(self.ev, self.item, "평균 질문", self.complete(hyp("average", "scope"), [{"by": "weekday", "focus": None}]))
        self.assertEqual(trace["queries"], [])
        self.assertEqual(trace["target_resolution"], "already_supported")
        self.assertIn("12,000원에서 15,000원", lines[0])

    def test_reading_hypothesis_has_priority_over_planner_reinterpretation(self):
        lines, trace = C.run(self.ev, self.item, "점심 질문", self.complete(hyp("average", "scope")),
                             hypothesis=hyp("average", "slot:lunch"))
        self.assertEqual(trace["hypothesis"]["target"], "slot:lunch")
        self.assertIn("점심 건당 평균", lines[0])

    def test_generic_amount_reading_can_be_refined_to_a_price_hypothesis(self):
        lines, trace = C.run(self.ev, self.item, "가격 부담 질문", self.complete(hyp("average", "scope")),
                             hypothesis=hyp("amount", "category:FOOD"))
        self.assertIsNone(self.payloads[0]["question_hypothesis"])
        self.assertEqual(trace["hypothesis"]["metric"], "average")
        self.assertIn("12,000원에서 15,000원", lines[0])
        self.assertIn("결제 건수", trace["next_check"])

    def test_general_answer_requires_total_even_when_key_factor_is_present(self):
        def omit_total(system, payload, timeout=None):
            if system == C.PLAN_PROMPT:
                return json.dumps({"queries": [], "reason": "기존 근거", "hypothesis": None})
            key = C.key_claims(C.first_claims(self.ev, self.item))[0]
            return json.dumps({"sentences": [{"text": "{"+key+"}", "claims": [key]}]})
        lines, trace = C.run(self.ev, self.item, "일반 변화 질문", omit_total)
        self.assertIsNone(lines)
        self.assertEqual(trace["fallback"], "rejected")
        self.assertIn("0:missing_must1", trace["check"])

    def test_absent_payments_have_a_search_limit_not_a_change_disclaimer(self):
        lines, trace = C.run(self.ev, self.item, "배달 결제 질문", self.complete(hyp("amount", "online:배달앱")))
        self.assertIn("두 기간 모두 결제가 없었어요", lines[0])
        self.assertIn("다른 이름", trace["support"]["limitation"])
        self.assertNotIn("변화예요", trace["support"]["limitation"])
        self.assertIn("다른 항목", trace["next_check"])

    def test_fewer_payments_suggests_average_in_same_scope(self):
        claim = {"id": "C1", "kind": "change", "direction": "down"}
        contract = {"status": "observation", "claims": ["C1"]}
        next_step = C.next_check([claim], I.internal(hyp("count", "scope")), contract)
        self.assertIn("같은 범위의 건당 평균", next_step)
        self.assertNotIn("다른 항목", next_step)

    def test_visit_offset_is_compact_and_total_is_preserved(self):
        ev = explain.evidence(user("B"), self.item, TODAY)
        lines, trace = C.run(ev, self.item, "일반 변화 질문", self.complete(None))
        self.assertEqual(trace["fallback"], None)
        self.assertIn("224,000원", lines[0])
        self.assertIn("280,000원", lines[1])
        self.assertIn("56,000원", lines[1])
        self.assertEqual(" ".join(lines).count("224,000원"), 1)
        self.assertIn("상쇄", lines[1])

    def test_never_expands_the_original_category_or_time_filter(self):
        for target, item in (("category:TRANSPORT_CAR", self.item),
                             ("slot:night", {**self.item, "time_slots": ["lunch"]}),
                             ("day:weekend", {**self.item, "weekdays": [0]}),
                             ("online:배달앱", {**self.item, "merchant": "다른가게"}),
                             ("online:배달앱", {**self.item, "merchant": "배민"})):
            claims, reason = C.target_claims(self.ev, item, I.internal(hyp("count", target)), 20)
            self.assertEqual((claims, reason), ([], "outside_scope"))

    def test_counts_and_averages_use_positive_net_payments_and_fixed_policy(self):
        def row(amount, time="12:00", fixed=False):
            return {"transaction_type": "spending", "category": "FOOD", "net_amount": amount,
                    "merchant_name": "가상가게", "funding": "fixed" if fixed else "living",
                    "occurred_time": time, "occurred_on": date(2026, 10, 3)}
        before = tools._detail([row(100), row(300), row(0), row(900, fixed=True)], "FOOD")
        after = tools._detail([row(600), row(100, "18:00")], "FOOD")
        ev = {**self.ev, "sides": [after, before]}
        for metric, expected in (("average", (200, 600)), ("count", (2, 1)), ("amount", (400, 600))):
            got, _ = C.target_claims(ev, self.item, I.internal(hyp(metric, "slot:lunch")), 20)
            self.assertEqual((got[0]["before"], got[0]["after"]), expected)
        before_fixed = tools._detail([row(100), row(300), row(900, fixed=True)], "FOOD", True)
        ev["sides"][1] = before_fixed
        got, _ = C.target_claims(ev, {**self.item, "include_fixed": True}, I.internal(hyp("count", "slot:lunch")), 20)
        self.assertEqual(got[0]["before"], 3)
        self.assertNotIn(C.FIXED, got[0]["text"])

    def test_missing_time_and_empty_average_are_not_zero(self):
        self.ev["sides"][0]["metric_groups"]["time_slot"][None] = {"count": 1, "amount": 100}
        got, reason = C.target_claims(self.ev, self.item, I.internal(hyp("average", "slot:lunch")), 20)
        self.assertEqual((got, reason), ([], "missing_time_or_merchant"))
        del self.ev["sides"][0]["metric_groups"]["time_slot"][None]
        self.ev["sides"][0]["metric_groups"]["time_slot"].pop("lunch")
        got, reason = C.target_claims(self.ev, self.item, I.internal(hyp("average", "slot:lunch")), 20)
        self.assertEqual((got, reason), ([], "empty_average"))

    def test_missing_time_does_not_reuse_a_partial_top_slot_claim(self):
        self.ev["sides"][0]["metric_groups"]["time_slot"][None] = {"count": 1, "amount": 100}
        lines, trace = C.run(self.ev, self.item, "점심 지출 질문", self.complete(hyp("amount", "slot:lunch")))
        self.assertEqual(trace["support"]["status"], "insufficient")
        self.assertEqual(trace["target_resolution"], "missing_time_or_merchant")
        self.assertIn("시간이나 가게 이름", lines[0])

    def test_new_metric_payload_has_no_amounts_or_merchant_names(self):
        C.run(self.ev, self.item, "점심 평균 질문", self.complete(hyp("average", "slot:lunch")))
        payload = json.dumps(self.payloads[-1], ensure_ascii=False)
        for secret in ("180000", "132000", "범카츠", "metric_groups", "before", "after"):
            self.assertNotIn(secret, payload)

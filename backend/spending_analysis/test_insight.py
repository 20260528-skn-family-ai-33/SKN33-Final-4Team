"""집계 범위·독립 제한·서술 근거 검증을 네트워크 없이 확인한다."""
import json
from dataclasses import replace
from datetime import date
from unittest import mock

from django.test import SimpleTestCase

from chat.llm.fake import FakeReading
from chat.llm.schemas import schema_for, role_schema
from . import dialog, insight, narrative, lifestyle
from .reading import SpendingReading, parse, ReadFailed

TODAY = date(2026, 11, 12)


def transaction(amount, category="FOOD", *, day=8, month=11, time="19:00", funding="living"):
    return {"net_amount": amount, "category": category, "occurred_on": date(2026, month, day),
            "occurred_time": time, "funding": funding, "transaction_type": "spending", "merchant_name": "비공개 가게"}


def decision(**extra):
    reading = SpendingReading(mine=True, target="overview", analysis_scope="living", **extra)
    return replace(dialog.decide(reading, "소비를 분석해줘", today=TODAY), interpretation=reading)


def projection(days=28):
    return {"state": "ready", "month_end": 29000, "remaining": 10000,
            "validation": {"selected": {"lookback_days": days, "model": "average"}, "test_months": ["2026-09", "2026-10"], "test_mae": 2000}}


class InsightTest(SimpleTestCase):
    def setUp(self):
        self.current = [transaction(3000), transaction(3000), transaction(12000, time=None), transaction(1000, "TRANSPORT_CAR"),
                        transaction(500000, "HOUSING_TELECOM", funding="fixed"), transaction(200000, "TRAVEL_LODGING"), transaction(10000, funding="fixed")]
        self.previous = [transaction(3000, month=10), transaction(3000, month=10), transaction(2000, "TRANSPORT_CAR", month=10)]
        self.rows = mock.patch.object(insight.tools, "rows", side_effect=lambda _u, p, *_a, **_k:
            self.current if p.start == TODAY.replace(day=1) else self.previous).start()
        self.cover = mock.patch.object(insight, "coverage", return_value={"state": "complete"}).start()
        self.fixed = mock.patch.object(insight.tools, "fixed_forecast", return_value={"paid": 510000, "remaining": 50000}).start()
        mock.patch.object(insight.reasoning, "persistence", return_value={"state": "usual", "weeks": [70000] * 4}).start()
        mock.patch.object(insight, "covered_through", return_value=None).start()  # 수집 범위 맞추기는 따로 시험한다
        self.addCleanup(mock.patch.stopall)

    def test_consistent_living_aggregates_no_identity_and_all_candidates_reviewed(self):
        summary, reason = insight.summarize(object(), decision(), TODAY)
        self.assertIsNone(reason)
        self.assertEqual(summary["current"]["amount"], 19000)
        self.assertEqual(summary["previous"]["amount"], 8000)
        self.assertTrue(summary["basis"]["fixed_excluded"])
        self.assertEqual({c["code"] for c in summary["categories"]}, {"FOOD", "TRANSPORT_CAR"})
        self.assertEqual({c["category"] for c in summary["candidates"]}, {"FOOD", "TRANSPORT_CAR"})
        self.assertNotIn("비공개 가게", json.dumps(summary, ensure_ascii=False))
        self.assertNotIn("occurred_on", json.dumps(summary))

    def test_general_analysis_is_variable_and_fixed_request_still_allows_analysis(self):
        reading = SpendingReading(mine=True, target="overview")
        d = replace(dialog.decide(reading, "아무거나 분석해줘", today=TODAY), interpretation=reading)
        summary, _ = insight.summarize(object(), d, TODAY)
        self.assertEqual(summary["current"]["amount"], 219000)
        self.assertEqual(summary["basis"]["scope"], "변동 소비")
        self.assertTrue(insight.eligible(decision(include_fixed=True)))

    def test_story_answer_is_computed_on_the_server_without_llm_prose(self):
        # 추이 → 습관 → 앞으로 답은 서버 계산 문장으로 만든다 (사용자 확정 구성, 11/13). 문장 LLM · 의미 대조 호출 없음
        complete = mock.Mock(wraps=FakeReading())
        reply, reason = insight.answer(object(), decision(), TODAY, complete, question="내 소비 습관 어때?")
        self.assertIsNone(reason)
        complete.assert_not_called()
        self.assertEqual(reply["view"][1]["title"], "핵심 진단")
        self.assertEqual(reply["view"][1]["tone"], "key")
        self.assertTrue(reply["examples"])  # 이어서 물을 만한 말

    def test_total_paid_fixed_is_added_once_even_for_living_scope(self):
        with mock.patch.object(insight.trend, "forecast", return_value=projection()), mock.patch.object(insight.trend, "summarize", return_value={}):
            summary, _ = insight.summarize(object(), decision(), TODAY)
        self.assertEqual(summary["current"]["amount"], 19000)
        total = summary["total_forecast"]
        self.assertEqual((total["actual"], total["month_end"]), (729000, 789000))

    def test_fixed_lookup_failure_only_blocks_total_forecast(self):
        self.fixed.side_effect = insight.tools.DbError()
        with mock.patch.object(insight.trend, "forecast", return_value=projection()), mock.patch.object(insight.trend, "summarize", return_value={}):
            summary, _ = insight.summarize(object(), decision(), TODAY)
        self.assertEqual(summary["overall_trend"]["state"], "ready")
        self.assertEqual(summary["total_forecast"]["state"], "unavailable")

    def test_validation_error_is_server_notice_and_never_llm_prose_input(self):
        complete = mock.Mock(wraps=FakeReading())
        with mock.patch.object(insight.trend, "forecast", return_value=projection()), mock.patch.object(insight.trend, "summarize", return_value={}):
            reply, _ = insight.answer(object(), decision(analysis_goal="forecast"), TODAY, complete)
        self.assertNotIn("오차", " ".join(reply["lines"]))
        self.assertTrue(any(n.startswith("예상 방식:") for n in reply["notices"]))  # 예상 방식 · 검증 결과는 하단에서 확인

    def test_failed_forecast_validation_keeps_reason_and_error_separate(self):
        text, notes = insight._outlook({"overall_trend": {"state": "unavailable",
            "reason": "월말 검증 평균 오차가 20,000원으로 기준선보다 나은 예측을 확인하지 못했어요."}})
        self.assertIsNone(text)  # 검증되지 않은 전망은 본문에 쓰지 않는다
        self.assertIn("예측이 충분히 맞지 않아", notes[0])
        self.assertNotIn("오차", notes[0])
        self.assertIn("20,000원", " ".join(notes))

    def test_partial_data_keeps_observations_without_change_claims(self):
        self.cover.return_value = {"state": "partial"}
        self.current = [{**transaction(10000, day=d, time="12:00"), "activity": "dining"} for d in (3, 5, 7, 9)]
        reply, _ = insight.answer(object(), decision(), TODAY, FakeReading())
        text = " ".join(reply["lines"])
        self.assertNotIn("지난달보다", text)
        self.assertIn("확인된", text)
        self.assertIn("일부 내역 기준", reply["view"][0]["title"])
        self.assertTrue(any("지난달 비교는 빼고" in n for n in reply["notices"]))
        self.assertTrue(any(n.startswith("월말 전망:") for n in reply["notices"]))
        self.assertFalse(any(v["kind"] == "trend" for v in reply["view"]))

    def test_missing_comparison_period_does_not_block_valid_forecast(self):
        self.cover.side_effect = [{"state": "complete"}, {"state": "partial"}, *[{"state": "complete"}] * 7]
        with mock.patch.object(insight.trend, "forecast", return_value=projection()), mock.patch.object(insight.trend, "summarize", return_value={}):
            summary, _ = insight.summarize(object(), decision(), TODAY)
        self.assertFalse(summary["basis"]["comparison_available"])
        self.assertEqual(summary["overall_trend"]["state"], "ready")

    def test_long_window_requires_its_own_collection_coverage(self):
        self.cover.side_effect = [*[{"state": "complete"}] * 9, {"state": "partial"}]
        with mock.patch.object(insight.trend, "forecast", return_value=projection(56)), mock.patch.object(insight.trend, "summarize", return_value={}):
            summary, _ = insight.summarize(object(), decision(), TODAY)
        self.assertNotIn("month_end", summary["overall_trend"])
        self.assertIn("56일", summary["overall_trend"]["reason"])

    def test_unconfirmed_last_week_spike_does_not_silently_extrapolate(self):
        with mock.patch.object(insight.trend, "forecast", return_value=projection()), \
             mock.patch.object(insight.trend, "summarize", return_value={}), \
             mock.patch.object(insight.reasoning, "persistence", return_value={"state": "mixed", "weeks": [10, 10, 10, 100], "baseline_range": [10, 10]}):
            summary, _ = insight.summarize(object(), decision(), TODAY)
        self.assertNotIn("month_end", summary["overall_trend"])
        self.assertIn("최근 한 주", summary["overall_trend"]["reason"])

    def test_return_to_usual_range_holds_model_that_still_projects_the_spike(self):
        with mock.patch.object(insight.trend, "forecast", return_value=projection()), \
             mock.patch.object(insight.trend, "summarize", return_value={}), \
             mock.patch.object(insight.trend, "_remaining", return_value=80000), \
             mock.patch.object(insight.reasoning, "persistence", return_value={"state": "reverted", "weeks": [70000, 140000, 70000, 70000], "baseline_range": [70000, 70000]}):
            summary, _ = insight.summarize(object(), decision(), TODAY)
        self.assertNotIn("month_end", summary["overall_trend"])
        self.assertIn("이전 범위로 돌아왔어요", summary["overall_trend"]["reason"])

    def test_unknown_only_data_has_no_fake_category_or_zero_spending(self):
        self.cover.return_value = {"state": "none"}
        self.current, self.previous = [transaction(172600, "UNCLASSIFIED")], []
        reply, _ = insight.answer(object(), decision(), TODAY, FakeReading())
        self.assertNotIn("UNCLASSIFIED", " ".join(reply["lines"]))
        self.assertTrue(any("172,600원" in n for n in reply["notices"]))
        self.assertNotIn("어려워요", " ".join(reply["lines"]))
        self.assertNotIn("0원 썼", " ".join(reply["lines"]))

    def test_filtered_scope_does_not_mix_all_fixed_expenses(self):
        summary, _ = insight.summarize(object(), decision(categories=("FOOD",), payment="card", weekday=("weekend",)), TODAY)
        self.assertNotIn("total_forecast", summary)
        self.fixed.assert_not_called()

    def test_schema_and_intent_goal_wiring(self):
        self.assertEqual(schema_for(narrative.PROMPT)[1], narrative.SCHEMA)
        self.assertEqual(schema_for(narrative.CHECK_PROMPT)[1], narrative.CHECK_SCHEMA)
        self.assertIn("analysis_goal", role_schema("spending")["properties"])
        with self.assertRaises(ReadFailed):
            parse({"mine": True, "analysis_goal": "invent"})
        reading = parse({"mine": True, "target": "overview", "explain": True, "analysis_goal": "reason",
                         "hypothesis": {"metric": "amount", "target": "scope", "unavailable": None}})
        self.assertIsNone(reading.hypothesis)
        specific = parse({"mine": True, "explain": True, "analysis_goal": "reason",
                          "hypothesis": {"metric": "count", "target": "scope", "unavailable": None}})
        self.assertIsNotNone(specific.hypothesis)

    def test_specific_industry_and_exact_platform_only(self):
        for category, industry, merchant, expected in (("FOOD", "한식", "가게", "dining"), ("FOOD", None, "배달의민족", "delivery"),
                ("FOOD", None, "배민식당", None), ("FOOD", "슈퍼마켓", "점심마트", None), ("MEDICAL_HEALTH", "한식", "가게", None)):
            self.assertEqual(lifestyle.identify(category, industry, merchant), expected)

    def test_habit_goal_can_describe_existing_pattern_without_inventing_change(self):
        self.current = [{**transaction(10000, day=d, time="12:00"), "activity": "dining"} for d in range(2, 7)]
        self.previous = [{**transaction(10000, month=10, day=d, time="12:00"), "activity": "dining"} for d in range(2, 7)]
        reply, _ = insight.answer(object(), decision(analysis_goal="habit"), TODAY, FakeReading(), question="내 소비 습관 어때?")
        self.assertIn("외식", " ".join(reply["lines"]))
        self.assertNotIn("늘어난", " ".join(reply["lines"]))
        self.assertNotIn("늘었", " ".join(reply["lines"]))

    def test_partial_unknown_industry_explains_spending_shape_not_payment_counts(self):
        self.cover.return_value = {'state': 'partial'}
        self.current = [transaction(amount, day=day, time=None) for day, amount in
                        zip((2, 3, 4, 5, 6, 7, 7), (15000, 16000, 15500, 17000, 16000, 16000, 16000))]
        self.current += [transaction(13300, 'TRANSPORT_CAR', day=d, time=None) for d in (2, 3, 4, 4)]
        self.current += [transaction(172600, 'UNCLASSIFIED', day=4)]
        for complete in (FakeReading(), mock.Mock(side_effect=TimeoutError())):
            reply, _ = insight.answer(object(), decision(analysis_goal='habit'), TODAY, complete)
            text = ' '.join(reply['lines'])
            # 고르게 나눠 쓴 식비는 발견이 아니다. 대신 이번 달 총액을 좌우한 큰 결제 한 건의 몫을 설명한다
            self.assertNotIn('나눠 쓴 돈이 쌓이는', text)
            self.assertIn('큰 결제 1건', text)
            self.assertIn('51%', text)
            self.assertNotIn('외식', text)
            self.assertNotIn('미분류', text)
            self.assertNotIn('UNCLASSIFIED', text)
            self.assertNotIn('지속 여부', text)
            self.assertNotIn('어려워요', text)
            self.assertTrue(any('분류 전 결제' in note for note in reply['notices']))
            self.assertFalse(any('자동으로 작성한 설명' in note for note in reply['notices']))

    def test_habit_keeps_explained_change_instead_of_replacing_it_with_repetition(self):
        self.current = [{**transaction(20000, day=d, time='12:00'), 'activity': 'dining'} for d in range(2, 10)]
        self.previous = [{**transaction(10000, month=10, day=d, time='12:00'), 'activity': 'dining'} for d in range(2, 10)]
        reply, _ = insight.answer(object(), decision(analysis_goal='habit'), TODAY, FakeReading())
        lead = reply['view'][1]['lines'][1:]
        self.assertTrue(any('한 번에 쓰는 금액이 커진 것' in line for line in lead), lead)  # 반복 방식 + 최근 달라진 점

    def test_call_failure_or_semantic_rejection_uses_computed_analysis(self):
        for complete in (mock.Mock(side_effect=TimeoutError()), lambda s, u, **kw: json.dumps({"supported": False, "answers_question": True, "no_repetition": True, "checked_findings": []}) if s == narrative.CHECK_PROMPT else FakeReading()(s, u)):
            reply, _ = insight.answer(object(), decision(), TODAY, complete)
            self.assertTrue(reply["lines"])
            # LLM 서술이 실패해도 사용자에게 내부 사정을 알리지 않고 계산된 분석을 그대로 보여 준다
            self.assertFalse(any("자동으로 작성한 설명" in n for n in reply["notices"]))


class NarrativeBoundaryTest(SimpleTestCase):
    def setUp(self):
        self.data = narrative.payload("왜?", [{"id": "F1", "kind": "payment_days", "category": "FOOD", "conclusion": "식비가 80,000원 늘었어요.",
            "evidence": ["결제일 수의 영향은 70,000원이에요."], "outlook": "지속 여부는 아직 확인하지 못했어요.", "limits": []}], "전망은 보류했어요.", "reason")

    def test_new_numeric_unknown_reference_and_motive_are_rejected(self):
        for text, ids in (("임의로 300만원 늘었어요", ["F1"]), ("스트레스 때문이에요", ["F1"]), ("달라졌어요", ["FAKE"])):
            raw = {"diagnosis": {"text": text, "findings": ids}, "evidence": [], "outlook": None}
            self.assertIsNone(narrative.draft(json.dumps(raw), self.data))

    def test_new_prose_can_link_validated_conclusion_and_metric_tokens(self):
        raw = {"diagnosis": {"text": "식비의 {{V1}}원 차이는 결제한 날짜가 달라진 영향으로 설명돼요.", "findings": ["F1"]},
               "evidence": [], "outlook": None}
        self.assertIsNotNone(narrative.draft(json.dumps(raw), self.data))
        raw["diagnosis"]["text"] = "예측은 미래 금액을 보장하지는 않아요."
        self.assertIsNotNone(narrative.draft(json.dumps(raw), self.data))

    def test_equal_amounts_in_different_findings_keep_distinct_references(self):
        data = narrative.payload("왜?", [{"id": "F1", "kind": "amount", "conclusion": "식비는 80,000원이에요.",
            "evidence": [], "outlook": "", "limits": []}], "월말 예상액은 80,000원이에요.", "reason")
        self.assertEqual(data["values"], {"V1": "80,000", "V2": "80,000"})
        raw = {"diagnosis": {"text": "월말 예상은 {{V1}}원이에요.", "findings": ["FORECAST"]},
               "evidence": [{"text": "식비는 {{V1}}원이에요.", "findings": ["F1"]}], "outlook": None}
        self.assertIsNone(narrative.draft(json.dumps(raw), data))
        raw["diagnosis"]["text"] = "월말 예상은 {{V2}}원이에요."
        self.assertIsNotNone(narrative.draft(json.dumps(raw), data))

    def test_spending_delta_cannot_be_relabelled_as_prediction_error(self):
        raw = {"diagnosis": {"text": "검증 기간의 예측 오차는 약 {{V1}}원이에요.", "findings": ["F1", "FORECAST"]},
               "evidence": [], "outlook": None}
        self.assertIsNone(narrative.draft(json.dumps(raw), self.data))

    def test_canonical_large_payment_proof_passes_without_unrelated_maximum(self):
        data = narrative.payload("이번 달 생활비 왜 늘었지?", [{"id": "LARGE_TOTAL", "kind": "large_total",
            "conclusion": "큰 결제의 영향이 크고 나머지 소비는 비슷해요.",
            "evidence": ["전체는 520,000원과 120,000원이에요. 큰 결제 차이는 400,000원이고 나머지 차이는 0원이에요."],
            "outlook": "", "limits": []}], None, "reason")
        complete = mock.Mock(wraps=FakeReading())
        self.assertIsNotNone(narrative.compose(data, complete))
        check_data = json.loads(complete.call_args_list[1].args[1])
        self.assertNotIn("summary", check_data["grounding"])
        self.assertEqual(check_data["grounding"]["findings"][0]["id"], "LARGE_TOTAL")

    def test_forecast_estimates_are_displayed_once_and_other_outlooks_are_kept(self):
        data = narrative.payload("얼마나 쓸까?", [{"id": "F1", "kind": "amount", "conclusion": "식비는 75,000원 늘었어요.",
            "evidence": [], "outlook": "", "limits": []}], "월말 변동 소비는 739,000원, 총지출은 1,514,000원이에요. 최근 28일을 반영했어요.", "forecast")
        raw = {"diagnosis": {"text": "월말 변동 소비는 {{V2}}원, 총지출은 {{V3}}원으로 예상돼요.", "findings": ["FORECAST"]},
               "evidence": [{"text": "식비는 {{V1}}원 늘었어요.", "findings": ["F1"]}],
               "outlook": {"text": "최근 {{V4}}일을 반영하면 변동 소비는 {{V2}}원, 총지출은 {{V3}}원이에요.", "findings": ["FORECAST"]}}

        def complete(system, user, **kwargs):
            return FakeReading()(system, user) if system == narrative.CHECK_PROMPT else json.dumps(raw)

        result = narrative.compose(data, complete)
        self.assertEqual(result[2], [])
        self.assertEqual(" ".join(sum(result, [])).count("739,000원"), 1)
        raw["outlook"]["text"] = "앞으로 새로운 큰 결제가 생기면 실제 금액은 달라질 수 있어요."
        self.assertTrue(narrative.compose(data, complete)[2])

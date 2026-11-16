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
        def suggest(_system, user, **_kwargs):
            choices = json.loads(user)["candidates"]
            return json.dumps({"questions": [{"id": c["id"], "text": c["question"]} for c in choices]}, ensure_ascii=False)
        complete = mock.Mock(side_effect=suggest)
        reply, reason = insight.answer(object(), decision(), TODAY, complete, question="내 소비 습관 어때?")
        self.assertIsNone(reason)
        complete.assert_called_once()
        self.assertEqual(complete.call_args.args[0], insight.followups.PROMPT)
        self.assertEqual(reply["view"][1]["title"], "핵심 진단")
        self.assertEqual(reply["view"][1]["tone"], "key")
        self.assertEqual(reply["examples"], [c["question"] for c in json.loads(complete.call_args.args[1])["candidates"]])

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
        self.assertTrue(any("지난달과의 총액 비교는 제외" in n for n in reply["notices"]))
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
        # 완료된 달이 2개월 미만이면 이번 달 진행분만으로 소비 유형을 정하지 않는다 (v2) — 핵심 진단이 첫 화면에 그대로
        self.assertEqual(reply['view'][1].get('tone'), 'key')
        self.assertFalse(any(b['kind'] == 'persona' for b in reply['view']))
        self.assertTrue(any('한 번에 쓰는 금액이 커진 것' in line for line in reply['lines']), reply['lines'])

    def test_habit_details_keep_original_blocks_and_approved_history(self):
        self.current = [{**transaction(20000, day=d, time='12:00'), 'activity': 'dining'} for d in range(2, 10)]
        summary, _ = insight.summarize(object(), decision(analysis_goal='habit'), TODAY)
        st = summary['story']
        with mock.patch.object(insight.benchmark, 'for_user', return_value=([], [])):
            original, _, _ = insight._story_blocks(None, summary, 'habit', TODAY, {f['id']: f for f in st['findings']}, st)
            history = {'state': 'ready', 'period': {'from': '2026-10-25', 'to': '2026-11-25'},
                       'change_count': 3, 'categories': [{'code': 'FOOD', 'name': '식비', 'increases': 3,
                       'decreases': 0, 'increase_amount': 90000}], 'savings': {'increases': 0, 'decreases': 1}}
            with mock.patch.object(insight.budget_habits, 'for_user', return_value=history):
                reply = insight._story_answer(mock.Mock(pk='user'), summary, 'habit', TODAY, '')
        persona = reply['view'][1]
        # 예산 반복 증액은 대표 유형이 아니라 태그 (v2). 취향 근거가 없으니 대표는 기본 + 구체적인 이유
        self.assertEqual((persona['kind'], persona['mascot']), ('persona', 'basic'))
        self.assertEqual(persona['tags'], [{'code': 'stretch', 'label': '이번만 조금 더'}])
        self.assertTrue(persona['desc'] and persona['reasons'] and persona['action'])
        self.assertTrue(any(t['code'] == 'FOOD' and t['status'] == 'unavailable' for t in persona['traits']))
        self.assertFalse(any('average' in t for t in persona['traits']))
        details = next(b['blocks'] for b in reply['view'] if b['kind'] == 'details')
        # 핵심 진단은 유형 카드 바로 아래(접지 않음), 나머지 기존 블록은 상세에 그대로
        self.assertEqual(reply['view'][2]['tone'], 'key')
        self.assertFalse(any(b.get('tone') == 'key' for b in details))
        for block in original:
            if block.get('tone') != 'key':
                self.assertIn(block, details)
        text = ' '.join(reply['lines'])
        self.assertIn('11월 24일', text)
        self.assertIn('실제 저축액이 아닌 예산 기준', text)
        self.assertNotIn('0번', text)  # 바뀌지 않은 쪽 횟수는 늘어놓지 않는다
        self.assertNotIn('11월 25일', text)

    def test_habit_details_only_add_graphs_related_to_the_interpretation(self):
        monthly = {'kind': 'months', 'title': '월별 생활비'}
        weekly = {'kind': 'trend', 'title': '반복 소비 추이'}
        pace = {'kind': 'pace', 'title': '예산 사용'}
        f = {'id': 'large', 'kind': 'large_share', 'section': '큰 결제', 'conclusion': '큰 결제가 영향을 줬어요.',
             'evidence': ['나머지 소비는 비슷해요.'], 'visual': pace}
        summary = {'story': {'chart': monthly, 'forecast_rows': [{'name': '식비'}]}, 'snapshot': [f],
                   'findings': [{'quiet': True, 'comparable': True, 'conclusion': '조용한 변화'}]}
        by_id = {'TREND': {'conclusion': '최근 추이', 'evidence': ['월별 근거']},
                 'FORECAST': {'conclusion': '월말 예상', 'evidence': ['예상 근거']}}
        original = [{'kind': 'text', 'lines': ['핵심 진단', '원래 분석']}]
        with mock.patch.object(insight, 'chart', return_value=weekly), mock.patch.object(insight, '_total_line', return_value=None):
            blocks = insight._habit_details(summary, original, TODAY, by_id)
        for block in [*original, pace]:
            self.assertIn(block, blocks)
        self.assertNotIn(monthly, blocks)
        self.assertNotIn(weekly, blocks)
        text = ' '.join(line for b in blocks if b['kind'] == 'text' for line in b['lines'])
        self.assertIn('예상 근거', text)
        self.assertNotIn('조용한 변화', text)

    def test_category_habit_does_not_use_another_categorys_budget_changes(self):
        reply = {'view': [{'kind': 'head', 'title': '식비 분석'},
                          {'kind': 'text', 'tone': 'key', 'lines': ['핵심 진단', '식비의 흐름']}],
                 'lines': ['식비의 흐름'], 'notices': []}
        with mock.patch.object(insight.budget_habits, 'for_user') as history:
            out = insight._habit_card(mock.Mock(pk='user'), {'basis': {'categories': ['FOOD']}}, reply, TODAY)
        history.assert_not_called()
        self.assertNotIn('이번만 조금 더형', str(out))
        # 근거가 없으면 유형(마스코트 카드)을 지어내지 않고 확인된 핵심 진단을 바로 보여 준다
        self.assertEqual(out['view'][1], reply['view'][1])
        self.assertNotIn('persona', str(out))
        self.assertNotIn('탐색 중', str(out))
        self.assertEqual(out['notices'], [])

    def test_habit_keeps_limits_of_findings_added_to_details(self):
        summary, _ = insight.summarize(object(), decision(analysis_goal='habit'), TODAY)
        limit = '가게 업종 정보가 없는 결제가 있어 세부 소비는 구분하지 못했어요.'
        summary['findings'] = [{'id': 'new', 'kind': 'change', 'section': '달라진 점', 'comparable': True,
                                'conclusion': '한 번에 쓰는 금액이 커졌어요.', 'evidence': [], 'limits': [limit]}]
        with mock.patch.object(insight.benchmark, 'for_user', return_value=([], [])):
            reply = insight._story_answer(None, summary, 'habit', TODAY, '')
        self.assertIn(limit, reply['notices'])

    def test_call_failure_or_semantic_rejection_uses_computed_analysis(self):
        for complete in (mock.Mock(side_effect=TimeoutError()), lambda s, u, **kw: json.dumps({"supported": False, "answers_question": True, "no_repetition": True, "checked_findings": []}) if s == narrative.CHECK_PROMPT else FakeReading()(s, u)):
            reply, _ = insight.answer(object(), decision(), TODAY, complete)
            self.assertTrue(reply["lines"])
            # LLM 서술이 실패해도 사용자에게 내부 사정을 알리지 않고 계산된 분석을 그대로 보여 준다
            self.assertFalse(any("자동으로 작성한 설명" in n for n in reply["notices"]))


class PersonaContextTest(SimpleTestCase):
    def test_saver_uses_the_same_fixed_deduction_as_protect(self):
        from budgets.models import Budget
        from budgets.protect import recommend
        from finance.models import FinancialTransaction
        user = mock.Mock(pk="synthetic-user")
        user.financial_profile = mock.Mock(birth_date=date(1998, 1, 1), housing_type="family", savings_floor=800000)
        budget = mock.Mock(funding_amount=3000000, cycle=mock.Mock(starts_on=date(2026, 11, 1)))
        with mock.patch.object(Budget.objects, "filter") as budgets, \
                mock.patch("budgets.protect.fixed_breakdown", return_value={
                    "categories": {"HOUSING_TELECOM": 700000}, "loan": 100000, "card_reserved": 200000}) as fixed, \
                mock.patch.object(FinancialTransaction.objects, "filter") as transfers:
            budgets.return_value.order_by.return_value.select_related.return_value.first.return_value = budget
            transfers.return_value.exists.return_value = True
            context = insight._persona_context(user, {}, TODAY)
        fixed.assert_called_once_with(user, budget.cycle)
        self.assertEqual(context["base"], 2000000)
        self.assertEqual(recommend(context["base"], "family", 0)["ratio_saving_floor"], 800000)
        result = insight.personality.assess({}, {"state": "unavailable"}, context)
        self.assertEqual(result["profile"]["mascot"], "saver")
        self.assertIn("권장 저축액(800,000원)", result["profile"]["lines"][0])

    def test_unknown_fixed_total_does_not_treat_income_as_available(self):
        from budgets.models import Budget
        from finance.models import FinancialTransaction
        user = mock.Mock(pk="synthetic-user")
        user.financial_profile = mock.Mock(birth_date=date(1998, 1, 1), housing_type="family", savings_floor=1200000)
        budget = mock.Mock(funding_amount=3000000, cycle=mock.Mock(starts_on=date(2026, 11, 1)))
        with mock.patch.object(Budget.objects, "filter") as budgets, \
                mock.patch("budgets.protect.fixed_total", return_value=None), \
                mock.patch.object(FinancialTransaction.objects, "filter") as transfers:
            budgets.return_value.order_by.return_value.select_related.return_value.first.return_value = budget
            transfers.return_value.exists.return_value = True
            context = insight._persona_context(user, {}, TODAY)
        self.assertNotIn("base", context)
        result = insight.personality.assess({}, {"state": "unavailable"}, context)
        self.assertIsNone(result["profile"])


class AnalysisPresentationTest(SimpleTestCase):
    def test_forecast_notices_follow_amount_even_without_monthly_graph(self):
        forecast = {"id": "FORECAST", "conclusion": "월말 예상은 42만 원이에요.", "evidence": []}
        for method in ("pattern", "validated"):
            for goal in ("overview", "habit", "forecast"):
                with self.subTest(method=method, goal=goal):
                    summary = self.summary()
                    summary["story"].update(
                        findings=[{"id": "TREND", "kind": "trend_flat", "conclusion": "생활비는 비슷했어요.", "evidence": []}, forecast],
                        projection={"method": method, "month_end": 420000, "observed": 300000,
                                    "checked": 3, "error": 50000,
                                    "validation": {"selected": {"model": "average", "lookback_days": 28},
                                                   "test_months": ["8월", "9월", "10월"], "test_mae": 50000}})
                    with mock.patch.object(insight.benchmark, "for_user", return_value=([], [])):
                        reply = insight._story_answer(None, summary, goal, TODAY, "")
                    self.assertIn(forecast["conclusion"], reply["lines"])
                    self.assertNotIn("months", [b["kind"] for b in reply["view"]])
                    self.assertTrue(any("예상 방식:" in n and "50,000원" in n for n in reply["notices"]))
                    self.assertIn("예상액에는 앞으로 새로 생길 큰 결제가 들어 있지 않아요.", reply["notices"])
                    if goal == "habit":
                        self.assertIn("월말 전망", str(next(b for b in reply["view"] if b["kind"] == "details")))

    def test_unshown_forecast_does_not_add_prediction_notices(self):
        summary = self.summary()
        summary["story"].update(
            findings=[{"id": "FORECAST", "conclusion": "월말 예상은 42만 원이에요.", "evidence": []}],
            projection={"method": "pattern", "checked": 3, "error": 50000})
        reply = insight._story_answer(None, summary, "reason", TODAY, "")
        self.assertFalse(any("예상 방식:" in n or "앞으로 새로 생길 큰 결제" in n for n in reply["notices"]))

    def summary(self):
        return {"basis": {"coverage": "partial", "comparison_available": False, "scope": "생활비",
                          "comparison_reason": "직접 등록한 카드의 조회 범위를 확인할 수 없어요.", "as_of": TODAY.isoformat()},
                "current": {"amount": 20000, "count": 2}, "previous": {"amount": 20000}, "total_material": {"meaningful": False},
                "snapshot": [], "findings": [], "unclassified_amount": [0, 0], "categories": [],
                "forecast_reason": "직접 등록한 카드의 조회 범위를 확인할 수 없어 월말 예상은 보류했어요.",
                "story": {"findings": [], "actions": [], "chart": None, "over": None, "slot": "과거 점심 시간대 소비는 식비 중심이에요."}}

    def test_slot_only_analysis_is_not_dropped_for_any_question(self):
        for goal in ("habit", "overview", "reason", "forecast"):
            with self.subTest(goal=goal), mock.patch.object(insight, "summarize", return_value=(self.summary(), None)), \
                    mock.patch.object(insight.benchmark, "for_user", return_value=([], [])):
                reply, _ = insight.answer(object(), decision(analysis_goal=goal), TODAY, None)
            front = reply["view"][1]
            lines = front.get("reasons", front.get("lines", []))
            self.assertTrue(lines and all(isinstance(line, str) and line for line in lines))
            self.assertNotIn("탐색 중", str(reply))
            self.assertNotIn("패턴을 읽기엔", str(reply))
            if goal != "forecast":
                self.assertIn("과거", " ".join(lines))
            else:
                self.assertTrue(any("월말 전망:" in note for note in reply["notices"]))

    def test_unavailable_forecast_does_not_repeat_monthly_graph_or_offer_increase_question(self):
        summary = self.summary()
        summary["story"].update(slot=None, chart={"kind": "months", "title": "월별 생활비", "basis": "지난 3개월"},
            findings=[{"id": "TREND", "conclusion": "지난 3개월 생활비는 비슷했어요.", "evidence": []}])
        with mock.patch.object(insight, "summarize", return_value=(summary, None)):
            reply, _ = insight.answer(object(), decision(analysis_goal="forecast"), TODAY, None)
        self.assertNotIn("지난 3개월 생활비는 비슷했어요.", reply["lines"])
        self.assertFalse(any(block["kind"] == "months" for block in reply["view"]))
        self.assertEqual(reply["examples"], [])
        self.assertTrue(any("조회 범위" in note for note in reply["notices"]))

    def test_already_shown_forecast_only_offers_its_new_calculation_detail(self):
        summary = self.summary()
        summary["story"].update(slot=None, projection={"month_end": 420000},
            findings=[{"id": "FORECAST", "conclusion": "월말 예상은 42만 원이에요.", "evidence": []}])
        reply = {"lines": ["월말 예상은 42만 원이에요."], "view": []}
        context = insight._followup_context(summary, "overview", reply, TODAY)
        candidates = context["candidates"]
        self.assertEqual([row["id"] for row in candidates], ["calculation"])
        self.assertEqual(context["answered"], ["forecast"])
        self.assertEqual(insight._followup_context(summary, "calculation", reply, TODAY)["candidates"], [])

    def test_shown_nested_comparison_is_remembered_but_unshown_findings_remain_available(self):
        summary = self.summary()
        summary["basis"]["comparison_available"] = True
        changes = [{"kind": "change", "comparable": True, "conclusion": line}
                   for line in ("식비가 늘었어요.", "교통비는 줄었어요.")]
        for shown in (changes[:1], changes):
            reply = {"lines": [], "view": [{"kind": "details", "blocks": [
                {"kind": "text", "lines": [f["conclusion"] for f in shown]}]}]}
            with mock.patch.object(insight, "select", return_value=changes):
                context = insight._followup_context(summary, "habit", reply, TODAY)
            self.assertEqual("reason" in context["answered"], len(shown) == len(changes))
            self.assertEqual("reason" in [c["id"] for c in context["candidates"]], len(shown) < len(changes))

    def test_unavailable_comparison_and_forecast_are_not_marked_as_answered(self):
        context = insight._followup_context(self.summary(), "overview", {"lines": [], "view": []}, TODAY)
        self.assertFalse({"reason", "forecast"} & set(context["answered"]))

    def test_forecast_input_is_not_offered_when_it_would_repeat_weekly_graph(self):
        summary = self.summary()
        weekly = {"kind": "trend", "title": "식비 추이"}
        summary["trends"] = {"FOOD": {"weekly_direction": "increase"}}
        summary["story"].update(slot=None, findings=[{"id": "FORECAST", "conclusion": "월말 예상", "evidence": []}])
        with mock.patch.object(insight, "chart", return_value=weekly):
            candidates = insight._followup_context(summary, "reason", {"lines": [], "view": [weekly]}, TODAY)["candidates"]
        self.assertNotIn("forecast", [row["id"] for row in candidates])

    def test_coverage_notice_distinguishes_unverified_dates_and_manual_records(self):
        from .periods import Period
        period = Period(date(2026, 11, 1), date(2026, 11, 13))
        missing = Period(date(2026, 11, 5), date(2026, 11, 8))
        notice = insight._coverage_note({"state": "partial", "targets": [{"missing": [missing]}]}, period)
        self.assertIn("11월 5일~7일", notice)
        self.assertIn("빠짐없이 불러왔는지 확인되지", notice)
        notice = insight._coverage_note({"state": "partial", "undetermined": [{"reason": "manual_card"}]}, period)
        self.assertIn("직접 등록", notice)
        self.assertNotIn("다 모이지", notice)
        self.assertNotIn("내역이 적", notice)

    def test_habit_input_does_not_repeat_category_share_or_budget_graph(self):
        for visual in ({"kind": "rows", "rows": [{"name": "식비", "ratio": 70}]}, {"kind": "pace"}):
            with self.subTest(visual=visual):
                summary = self.summary()
                summary["snapshot"] = [{"id": "SHARE", "kind": "composition", "section": "비중", "conclusion": "식비 중심이에요.",
                                        "evidence": [], "category": "FOOD", "visual": visual}]
                reply = {"lines": ["식비 중심이에요."], "view": [visual]}
                self.assertEqual(insight._followup_context(summary, "overview", reply, TODAY)["candidates"], [])

    def test_change_table_excludes_small_or_quiet_changes(self):
        summary = {"categories": [
            {"code": "FOOD", "name": "식비", "delta": 6000, "current": {"amount": 1006000}, "previous": {"amount": 1000000}},
            {"code": "FASHION_SHOPPING", "name": "패션·쇼핑", "delta": 100000, "current": {"amount": 200000}, "previous": {"amount": 100000}}],
            "candidates": [
                {"category": "FOOD", "comparable": True, "quiet": True, "material": {"meaningful": False}},
                {"category": "FASHION_SHOPPING", "comparable": True, "quiet": False, "material": {"meaningful": True}}]}
        self.assertEqual([row["name"] for row in insight._change_rows(summary)], ["패션·쇼핑"])

    def test_weekly_chart_shows_the_changing_category_instead_of_flat_total(self):
        summary = {"findings": [], "trends": {"FOOD": {"weekly_direction": "increase", "weeks": [{"amount": n} for n in (10000, 20000, 30000, 40000)]}},
                   "overall_trend": {"weekly_direction": "flat", "weeks": [{"amount": 50000}] * 4}}
        visual = insight.chart(summary, TODAY)
        self.assertEqual([s["code"] for s in visual["series"]], ["FOOD"])
        self.assertEqual([p["amount"] for p in visual["series"][0]["points"]], [10000, 20000, 30000, 40000])

    def test_forecast_alone_cannot_create_an_empty_habit_card(self):
        summary = self.summary()
        summary["story"].update(slot=None, findings=[{"id": "FORECAST", "conclusion": "월말 예상", "evidence": []}])
        with mock.patch.object(insight.benchmark, "for_user", return_value=([], [])):
            reply = insight._story_answer(object(), summary, "habit", TODAY, "")
        # 유형이 없으면 빈 유형 카드 대신 확인된 핵심 진단을 바로 보여 준다
        first = reply["view"][1]
        self.assertNotEqual(first["kind"], "persona")
        self.assertEqual(first.get("tone"), "key")
        self.assertTrue(all(isinstance(line, str) and line for line in first["lines"][1:]) and first["lines"][1:])

    def test_refund_in_another_category_does_not_erase_observed_food_timing(self):
        rows = [transaction(12000, day=d, time="12:00") for d in (2, 3)] + [transaction(-1000, "MEDICAL_HEALTH")]
        found = insight.snapshot.observation(rows)
        self.assertIn("점심 시간대 식비 중심", found[0]["conclusion"])
        self.assertIn("환불액을 제외", found[0]["limits"][0])

    def test_unclassified_payments_still_have_analyzable_timing(self):
        rows = [transaction(12000, "UNCLASSIFIED", day=d, time="12:00") for d in (2, 3)]
        finding = insight.snapshot.observation(rows)[0]
        self.assertIn("점심 시간대에 집중", finding["conclusion"])
        self.assertIn("100%", finding["evidence"][0])
        self.assertNotIn("식비", str(finding))
        self.assertNotIn("외식", str(finding))


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

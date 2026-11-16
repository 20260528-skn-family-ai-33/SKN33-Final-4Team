import copy
import json
from unittest import mock

from django.test import SimpleTestCase

from chat.llm.fake import FakeReading
from chat.llm.schemas import schema_for
from . import followups


class FollowupTest(SimpleTestCase):
    def setUp(self):
        self.reply = {"lines": ["식비는 주말에 집중됐어요."], "notices": ["지난달 비교 자료가 부족해요."],
                      "view": [{"kind": "text", "lines": ["생활 패턴", "식비는 주말에 집중됐어요."]}],
                      "examples": ["내 소비 습관 어때?", "월말 예상은 어떻게 계산했어?"],
                      "suggest": ["budget_adjust"], "over": {"category": "FOOD", "amount": 10000}}
        self.candidates = [{"id": "forecast", "question": "이대로 쓰면 이번 달 얼마나 쓸까?"},
                           {"id": "calculation", "question": "월말 예상은 어떻게 계산했어?"}]

    def apply(self, complete, *, candidates=None, question="내 소비 패턴 어때?"):
        return followups.apply(self.reply, question, complete,
                               candidates=self.candidates if candidates is None else candidates)

    def fallback(self):
        return {**self.reply, "examples": [row["question"] for row in self.candidates]}

    def test_topics_already_answered_in_this_conversation_are_not_offered_again(self):
        # 전체 분석 → 월말 계산 → 습관: 마지막 답에서 '월말 예상은 어떻게 계산했어?'를 다시 추천하지 않는다
        complete = mock.Mock(return_value=json.dumps({"questions": [
            {"id": "calculation", "text": "월말 예상은 어떻게 계산했어?"}]}, ensure_ascii=False))
        reply = followups.apply(self.reply, "내 소비 습관 어때?", complete, candidates=self.candidates, covered=("calculation",))
        self.assertEqual(reply["examples"], ["이대로 쓰면 이번 달 얼마나 쓸까?"])  # LLM 이 다시 고르면 기본 문구로
        none = followups.apply(self.reply, "내 소비 습관 어때?", complete, candidates=self.candidates,
                               covered=("calculation", "forecast"))
        self.assertEqual(none["examples"], [])

    def test_contextual_questions_replace_only_examples_with_one_bounded_call(self):
        original = copy.deepcopy(self.reply)
        questions = [{"id": "forecast", "text": "이번 달 말에는 얼마나 쓸까?"},
                     {"id": "calculation", "text": "월말 예상의 계산 근거를 알려줘"}]
        complete = mock.Mock(return_value=json.dumps({"questions": questions}, ensure_ascii=False))
        reply = self.apply(complete)
        self.assertEqual(reply, {**original, "examples": [row["text"] for row in questions]})
        self.assertEqual(self.reply, original)
        complete.assert_called_once()
        system, raw = complete.call_args.args
        self.assertEqual(system, followups.PROMPT)
        self.assertEqual(complete.call_args.kwargs, {"timeout": 5})
        data = json.loads(raw)
        self.assertIn("식비는 주말에 집중됐어요.", data["answer"])
        self.assertEqual(data["limits"], original["notices"])
        self.assertEqual(data["candidates"], self.candidates)
        self.assertEqual(set(data), {"question", "answer", "limits", "candidates"})

    def test_malformed_or_unusable_questions_keep_only_verified_fallback(self):
        invalid = [None, [], {}, {"questions": "식비는?"}, {"questions": [], "extra": True}]
        invalid += [{"questions": qs} for qs in [[None], ["월말 예상은 어떻게 계산했어?"],
                    [{"id": [], "text": "월말 전망은?"}], [{"id": "forecast"}]]]
        invalid += [{"questions": [{"id": "forecast", "text": text}]} for text in [
            None, " ", "a" * 51, "월말\n예상은?", "https://example.com", "월말에 10만원 쓸까?", "<script>",
            "내 소비 습관 어때?", "월말 식비 예상은?", "월말에 더 많이 쓰게 될까?", "월말 예산을 변경해줘"]]
        invalid += [{"questions": [{"id": "forecast", "text": "월말 전망은?"}] * n} for n in (2, 4)]
        for value in ["not json", *map(json.dumps, invalid)]:
            with self.subTest(value=value):
                complete = mock.Mock(return_value=value)
                self.assertEqual(self.apply(complete), self.fallback())

    def test_timeout_or_call_limit_does_not_discard_analysis_or_retry(self):
        for error in (TimeoutError(), RuntimeError("call_limit")):
            complete = mock.Mock(side_effect=error)
            self.assertEqual(self.apply(complete), self.fallback())
            complete.assert_called_once()

    def test_shown_topics_are_kept_even_if_generation_fails_and_calculation_stays_available(self):
        complete = mock.Mock(side_effect=TimeoutError())
        reply = followups.apply(self.reply, "내 소비 습관 어때?", complete,
                                candidates=self.candidates, answered=("forecast",))
        self.assertEqual(reply["examples"], [self.candidates[1]["question"]])
        self.assertEqual(reply["_answered_topics"], ["forecast"])
        self.assertEqual([row["id"] for row in json.loads(complete.call_args.args[1])["candidates"]], ["calculation"])

    def test_menu_without_followups_never_calls_the_model(self):
        complete = mock.Mock()
        reply = {"lines": ["어떤 분석을 볼까요?"], "examples": []}
        self.assertEqual(followups.apply(reply, "소비 분석해줘", complete), reply)
        self.assertEqual(self.apply(None), self.fallback())
        complete.assert_not_called()

    def test_stable_or_partial_spending_cannot_restore_unavailable_reason_or_forecast(self):
        candidates = [{"id": "habit", "question": "내 소비 습관 어때?"}]
        for key, text in (("reason", "이번 달 생활비 왜 이렇게 많이 나왔지?"),
                          ("forecast", "이번 달 말에 얼마나 쓸까?")):
            complete = mock.Mock(return_value=json.dumps({"questions": [{"id": key, "text": text}]}))
            reply = self.apply(complete, candidates=candidates, question="전체적으로 분석해줘")
            self.assertEqual(reply["examples"], [candidates[0]["question"]])

    def test_increase_premise_needs_explicit_evidence_and_never_implies_overspending(self):
        candidate = {"id": "reason", "question": "지난달과 달라진 점은 뭐야?"}
        text = "이번 달 생활비 왜 이렇게 많이 나왔지?"
        complete = mock.Mock(return_value=json.dumps({"questions": [{"id": "reason", "text": text}]}))
        self.assertEqual(self.apply(complete, candidates=[candidate])["examples"], [candidate["question"]])
        candidate["allow_increase_question"] = True
        self.assertEqual(self.apply(complete, candidates=[candidate])["examples"], [text])
        complete.return_value = json.dumps({"questions": [{"id": "reason", "text": "내가 왜 과소비했을까?"}]})
        self.assertEqual(self.apply(complete, candidates=[candidate])["examples"], [candidate["question"]])

    def test_no_new_content_means_no_buttons_even_on_llm_failure(self):
        complete = mock.Mock(side_effect=TimeoutError())
        self.assertEqual(self.apply(complete, candidates=[])["examples"], [])
        complete.assert_not_called()

    def test_rephrasing_cannot_add_direction_or_a_different_comparison_period(self):
        candidate = {"id": "reason", "question": "지난달과 달라진 소비는 뭐야?"}
        for text in ["소비가 커진 이유가 뭐야?", "생활비가 높아진 이유는?", "생활비가 비싸진 이유를 알려줘",
                     "생활비가 불어난 이유는?", "생활비가 낮아진 이유는?", "작년보다 달라진 소비는 뭐야?",
                     "지난해와 비교해 소비가 어때?", "지난 주와 소비를 비교해줘", "두 달 전과 소비가 어떻게 달라졌어?",
                     "십월과 비교한 소비 차이는?", "최근 몇 개월과 비교한 소비 차이는?"]:
            with self.subTest(text=text):
                complete = mock.Mock(return_value=json.dumps({"questions": [{"id": "reason", "text": text}]}))
                self.assertEqual(self.apply(complete, candidates=[candidate])["examples"], [candidate["question"]])

    def test_neutral_rephrasing_keeps_natural_wording_and_equivalent_month_scope(self):
        candidate = {"id": "reason", "question": "지난달과 달라진 소비는 뭐야?"}
        for text in ["지난 달과 비교하면 소비가 어떻게 달라졌어?", "이번 달 소비에서 달라진 점을 알려줘",
                     "전월 대비 소비 차이를 알려줘", "소비 변화의 이유를 알려줘"]:
            with self.subTest(text=text):
                complete = mock.Mock(return_value=json.dumps({"questions": [{"id": "reason", "text": text}]}))
                self.assertEqual(self.apply(complete, candidates=[candidate])["examples"], [text])

    def test_same_topic_is_removed_before_call_and_llm_can_choose_no_questions(self):
        complete = mock.Mock(return_value='{"questions": []}')
        reply = self.apply(complete, question="이대로 쓰면 월말에 얼마나 쓸까?")
        offered = json.loads(complete.call_args.args[1])["candidates"]
        self.assertEqual([row["id"] for row in offered], ["calculation"])
        self.assertEqual(reply["examples"], [])

    def test_live_schema_and_fake_adapter_share_the_contract(self):
        self.assertEqual(schema_for(followups.PROMPT), ("spending_followups", followups.SCHEMA))
        fake = FakeReading()
        self.assertEqual(self.apply(fake), self.fallback())
        self.assertEqual(fake.calls[-1]["role"], "fake_spending_followups")


class CoveredAcrossPathsTest(SimpleTestCase):
    """live 재현 (11/16 20시): 습관 → '지난달과 달라진 소비는 뭐야?'(분석 목표 없는 비교 조회로 읽혀도) → 월말 → 계산"""

    def turn(self, store, question, examples, analyzed=False, goal=None, answered=()):
        from types import SimpleNamespace
        from . import agent
        request = SimpleNamespace(analysis_goal=goal, explain=None)
        decision = SimpleNamespace(request=request, interpretation=request)
        result = {"status": "replied", "reply": {"lines": ["답"], "examples": examples, "_answered_topics": list(answered)}}
        reply = agent._covered(store, "c1", decision, question, result, analyzed)["reply"]
        self.assertNotIn("_answered_topics", reply)
        return reply["examples"]

    def test_topics_in_answer_remain_covered_after_an_intervening_question(self):
        from chat.router_core import MemoryStateStore
        store = MemoryStateStore()
        reason, forecast, calc = "지난달과 달라진 소비는 뭐야?", "이대로 쓰면 이번 달 얼마나 쓸까?", "월말 예상은 어떻게 계산했어?"
        self.assertEqual(self.turn(store, "내 소비 습관 어때?", [calc], analyzed=True, goal="habit",
                                   answered=("reason", "forecast")), [calc])
        self.assertEqual(self.turn(store, calc, [reason, forecast], analyzed=True), [])
        self.assertEqual(self.turn(MemoryStateStore(), calc, [reason], analyzed=True), [reason])

    def test_questions_answered_by_any_path_are_not_suggested_again(self):
        from chat.router_core import MemoryStateStore
        store = MemoryStateStore()
        reason, forecast, calc = "지난달과 달라진 소비는 뭐야?", "이대로 쓰면 이번 달 얼마나 쓸까?", "월말 예상은 어떻게 계산했어?"
        self.assertEqual(self.turn(store, "내 소비 습관 어때?", [calc], analyzed=True, goal="habit"), [calc])
        self.assertEqual(self.turn(store, reason, [calc]), [calc])  # 비교 조회 경로 — 문장으로 '달라진 점'을 남긴다
        self.assertEqual(self.turn(store, forecast, [reason, calc], analyzed=True, goal="forecast"), [calc])
        self.assertEqual(self.turn(store, calc, [reason], analyzed=True), [])
        from . import dialog
        self.assertEqual(dialog._state(store, "c1")["covered"], ["habit", "reason", "forecast", "calculation"])
        self.assertEqual(self.turn(MemoryStateStore(), forecast, [reason]), [reason])  # 새 대화는 처음부터

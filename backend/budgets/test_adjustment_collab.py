"""예산 조정의 조회 이관·재개 계약. 합성 자료와 주입한 포트만 사용한다."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock, patch

from budgets.adjustment_core.financial_conversation import handle
from budgets.adjustment_core.simulation import simulate
from budgets.adjustment_core.calculation import proportional_group_reduction
from budgets.test_adjustment_core import Store, item, reading
from budgets.test_financial_planner import financial_context


class AdjustmentCollabTest(unittest.TestCase):
    def setUp(self):
        self.context, self.store = financial_context(), Store()
        self.context["diagnostic_inputs"]["planned_spare"]["amount"] = 0
        self.complete, self.propose, self.cancel = Mock(), Mock(side_effect=self.card), Mock()
        self.cards = []

    def card(self, context, result, **settings):
        number = len(self.cards) + 1
        card = {**deepcopy(result), **settings, "proposal_id": f"proposal-{number}",
                "task_ref": f"task-{number}", "state": "awaiting_approval"}
        self.cards.append(card)
        return card

    def turn(self, text, *answers, hints=None):
        self.complete.reset_mock()
        self.complete.side_effect = [json.dumps(answer, ensure_ascii=False) for answer in answers]
        return handle({"conversation_id": "talk", "text": text, "hints": hints}, store=self.store,
                      complete=self.complete, get_context=lambda: deepcopy(self.context),
                      simulate=lambda inp, req: simulate(inp, req, allocator=proportional_group_reduction),
                      propose=self.propose, task_status=lambda ref: {"task_ref": ref, "state": "awaiting_approval"},
                      cancel=self.cancel, allocator=proportional_group_reduction)

    def preview(self):
        return self.turn("식비 5만 원 늘려줘", reading(requests=[item(amount=50000)], sources=["SPARE"]))["reply"]["proposal"]

    def assert_handoff(self, result, before, category="TRANSPORT_CAR"):
        self.assertEqual(result["status"], "not_mine")
        self.assertEqual(result["suggest"], {"to": "spending", "category": category})
        self.assertEqual(self.store.state, before)
        self.cancel.assert_not_called()

    def test_funding_question_button_uses_db_category_name_without_llm(self):
        self.turn("식비 5만 원 늘려줘", reading(requests=[item(amount=50000)]))
        before = deepcopy(self.store.state)
        self.assertEqual(before["pending"]["kind"], "comparison")
        self.assert_handoff(self.turn("교통은 얼마 남았어?", reading(mine=False, handoff="spending", handoff_category="TRANSPORT_CAR")), before)
        response = self.turn("교통·차량에서 가져오기", hints={"from": "spending", "category": "TRANSPORT_CAR"})
        self.complete.assert_not_called()
        proposal = response["reply"]["proposal"]
        self.assertEqual(proposal["allocations"]["TRANSPORT_CAR"], 60000)
        self.assertEqual(proposal["allocations"]["FOOD"], 440000)

    def test_preview_query_preserves_proposal_and_task(self):
        self.preview()
        before = deepcopy(self.store.state)
        self.assert_handoff(self.turn("교통은 얼마 남았어?", {
            "action": "not_mine", "category": "TRANSPORT_CAR", "amount": None,
            "amount_mode": None, "suggested_target": None}), before)
        self.assertEqual(len(self.cards), 1)

    def test_resume_without_text_redisplays_without_llm(self):
        first = self.preview()
        response = self.turn("")
        self.complete.assert_not_called()
        self.assertEqual(response["reply"]["proposal"]["proposal_id"], first["proposal_id"])
        self.assertEqual(len(self.cards), 1)

    def test_preview_button_preserves_goal_and_donor_floor(self):
        self.preview()
        response = self.turn("교통·차량에서 가져오기", hints={"from": "spending", "category": "TRANSPORT_CAR"})
        self.complete.assert_not_called()
        proposal = response["reply"]["proposal"]
        self.assertEqual(proposal["allocations"]["FOOD"], 450000)
        self.assertEqual(proposal["allocations"]["TRANSPORT_CAR"], 60000)  # 시스템 보호액 유지
        self.assertEqual(proposal["buckets"]["SPARE"], 390000)

    def test_pointing_feedback_receives_spending_hint(self):
        self.preview()
        response = self.turn("그럼 거기서 빼줘", {
            "action": "use_source", "category": "TRANSPORT_CAR", "amount": None,
            "amount_mode": None, "suggested_target": None}, hints={"from": "spending", "category": "TRANSPORT_CAR"})
        self.assertEqual(json.loads(self.complete.call_args.args[1])["hints"]["category"], "TRANSPORT_CAR")
        self.assertEqual(response["reply"]["proposal"]["allocations"]["FOOD"], 450000)

    def test_locked_donor_does_not_replace_preview(self):
        first = self.preview()
        self.context["locks"]["TRANSPORT_CAR"] = 100000
        response = self.turn("교통·차량에서 가져오기", hints={"from": "spending", "category": "TRANSPORT_CAR"})
        self.assertEqual(response["status"], "awaiting_input")
        self.assertEqual(response["reply"]["proposal"], first)
        self.assertEqual(len(self.cards), 1)

    def pending(self, kind, **extra):
        self.store.state = {"requests": [item(amount=50000)], "pending": {
            "kind": kind, "line": "확인해 주세요.", "choices": [], **extra}}

    def test_numeric_question_query_preserves_pending(self):
        self.pending("financial_data", field="planned")
        before = deepcopy(self.store.state)
        self.assert_handoff(self.turn("교통은 얼마 남았어?", {"value": None},
                            reading(mine=False, handoff="spending", handoff_category="TRANSPORT_CAR")), before)

    def test_one_off_question_query_preserves_candidates_and_pending(self):
        for parsed in ({"decisions": [{"id": "candidate", "decision": "unknown"}]}, {"decisions": []}, {}):
            with self.subTest(parsed=parsed):
                self.pending("one_off_review")
                self.store.state["review_candidates"] = [{"id": "candidate", "category": "FOOD", "amount": 50000}]
                before = deepcopy(self.store.state)
                self.assert_handoff(self.turn("교통은 얼마 남았어?", parsed,
                                    reading(mine=False, handoff="spending", handoff_category="TRANSPORT_CAR")), before)

    def test_one_off_answers_continue_planning_with_the_confirmed_decisions(self):
        for decision in ("one_off", "recurring", "unknown"):
            with self.subTest(decision=decision):
                self.pending("one_off_review")
                self.store.state["review_candidates"] = [{"id": "candidate", "category": "FOOD", "amount": 50000}]
                answers = [{"decisions": [{"id": "candidate", "decision": decision}]}]
                if decision == "unknown":
                    answers.append(reading())
                with patch("budgets.adjustment_core.financial_conversation.comparison_reply",
                           return_value={"status": "awaiting_input", "reply": {}}) as compare:
                    self.turn("일회성 확인 답변", *answers)
                state = compare.call_args.args[2]
                self.assertEqual(state["financial_overrides"]["history"]["review_decisions"], {"candidate": decision})
                self.assertNotIn("review_candidates", state)

    def test_comparison_consumption_reason_query_is_not_recommendation_reason(self):
        self.pending("financial_selection")
        self.store.state["financial_offer"] = {"answer": {"reasons": ["추천 근거"]}, "comparison": {"plans": []}}
        before = deepcopy(self.store.state)
        self.assert_handoff(self.turn("식비 왜 늘었어?", reading(mine=False, handoff="spending", handoff_category="FOOD")), before, "FOOD")

    def test_comparison_explanation_stays_with_budget(self):
        self.pending("financial_selection")
        self.store.state["financial_offer"] = {"answer": {"reasons": ["추천 근거"]}, "comparison": {"plans": []}}
        before = deepcopy(self.store.state)
        response = self.turn("왜 추천해?", reading(action="explain"))
        self.assertEqual(response["status"], "replied")
        self.assertEqual(response["reply"]["lines"], ["추천 근거"])
        self.assertEqual({k: v for k, v in self.store.state.items() if k != 'dialogue_history'}, before)
        self.assertEqual(self.store.state['dialogue_history'][-1]['user'], '왜 추천해?')

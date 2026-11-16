"""초과 카테고리 진입은 합성 서버 자료로 검증하며 기존 작업을 선택 없이 바꾸지 않는다."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock

from budgets.adjustment_core.financial_conversation import handle
from budgets.adjustment_core.funding import simulate_funding
from budgets.adjustment_core.calculation import proportional_group_reduction
from budgets.test_adjustment_core import Store, item, reading
from budgets.test_financial_planner import financial_context


class OverBudgetTest(unittest.TestCase):
    def setUp(self):
        self.context, self.store = financial_context(), Store()
        self.context["spent"]["FOOD"] = 412000
        self.context["diagnostic_inputs"]["planned_spare"]["amount"] = 0
        self.complete, self.propose = Mock(), Mock(side_effect=self.card)
        self.cancel = Mock(side_effect=self.cancel_task)
        self.tasks, self.cards = {}, []
        self.hints = {"reason": "over", "categories": ["FOOD"], "over_amount": 12000}

    def card(self, context, result, **settings):
        number = len(self.cards) + 1
        card = {**deepcopy(result), **settings, "proposal_id": f"proposal-{number}",
                "task_ref": f"task-{number}", "state": "awaiting_approval"}
        self.cards.append(card)
        self.tasks[card["task_ref"]] = {"state": "awaiting_approval"}
        return card

    def cancel_task(self, ref):
        self.tasks[ref]["state"] = "canceled"

    def turn(self, text="", *answers, hints=None, **extra):
        self.complete.reset_mock()
        self.complete.side_effect = [json.dumps(answer, ensure_ascii=False) for answer in answers]
        return handle({"conversation_id": "talk", "text": text, "hints": hints, **extra}, store=self.store,
                      complete=self.complete, get_context=lambda: deepcopy(self.context),
                      simulate=lambda inp, req: simulate_funding(inp, req, allocate_group_reduction=proportional_group_reduction),
                      propose=self.propose, task_status=lambda ref: deepcopy(self.tasks[ref]),
                      cancel=self.cancel, allocator=proportional_group_reduction)

    def preview(self):
        return self.turn("식비 5만 원 늘려줘", reading(requests=[item(amount=50000)], sources=["SPARE"]))["reply"]["proposal"]

    def test_starts_known_category_without_llm_or_assuming_requested_amount(self):
        before = deepcopy(self.context)
        response = self.turn(hints={**self.hints, "over_amount": 999999})
        self.complete.assert_not_called()
        self.propose.assert_not_called()
        self.assertEqual(self.context, before)
        self.assertEqual(self.store.state["over_budget"], {"category": "FOOD", "over_amount": 12000})
        self.assertEqual(self.store.state["requests"], [item(amount=None)])
        self.assertEqual(self.store.state["pending"]["kind"], "funding_scope")
        self.assertIn("12,000", str(response["reply"]["lines"]))
        self.assertNotIn("999,999", str(response))

    def test_amount_and_funding_follow_existing_flow_and_only_create_preview(self):
        self.turn(hints=self.hints)
        self.turn("spare")
        self.complete.assert_not_called()
        self.assertEqual(self.store.state["pending"]["kind"], "amount")
        response = self.turn("3만 원 늘려줘", reading(requests=[item(amount=30000)], reference="previous"))
        self.assertEqual(response["reply"]["proposal"]["allocations"]["FOOD"], 430000)
        self.assertEqual(self.context["allocations"]["FOOD"], 400000)

    def test_malformed_and_multiple_category_hints_never_modify_existing_state(self):
        self.preview()
        before = deepcopy(self.store.state)
        for changes in ({"categories": []}, {"categories": ["FOOD", "BEAUTY"]},
                        {"categories": "FOOD"}, {"categories": [None]}, {"categories": ["ETC"]},
                        {"categories": ["UNCLASSIFIED"]}, {"categories": ["unknown"]},
                        {"over_amount": None}, {"over_amount": True}, {"over_amount": 0},
                        {"over_amount": -1}, {"over_amount": 12.5},
                        {"from": "spending", "category": "BEAUTY"}, {"amount": 50000}):
            with self.subTest(changes=changes):
                response = self.turn(hints={**self.hints, **changes})
                self.assertEqual(response["error"], "invalid_over_handoff")
                self.assertEqual(self.store.state, before)
                self.complete.assert_not_called()
                self.cancel.assert_not_called()

    def test_zero_budget_and_resolved_overage_do_not_start(self):
        for allocated, spent in ((0, 12000), (400000, 400000), (400000, 0)):
            with self.subTest(allocated=allocated, spent=spent):
                self.context["allocations"]["FOOD"], self.context["spent"]["FOOD"] = allocated, spent
                self.assertEqual(self.turn(hints=self.hints)["status"], "unsupported")
                self.assertIsNone(self.store.state)
                self.complete.assert_not_called()

    def test_partial_spending_and_unapproved_budget_do_not_start(self):
        self.context["spending_status"] = "partial"
        self.assertEqual(self.turn(hints=self.hints)["error"], "spending_unverified")
        self.context.update(spending_status="complete", mode="INITIAL_ADJUSTMENT", source_status="validated")
        self.assertEqual(self.turn(hints=self.hints)["status"], "unsupported")
        self.assertIsNone(self.store.state)

    def test_locked_category_still_requires_unlock_consent(self):
        self.context["locks"]["FOOD"] = 400000
        self.turn(hints=self.hints)
        self.assertEqual(self.store.state["pending"]["kind"], "unlock")
        self.assertNotIn("unlock_categories", self.store.state)
        self.propose.assert_not_called()
        self.turn('unlock')
        self.assertEqual(self.store.state['pending']['kind'], 'amount')
        self.turn('3만 원 늘려줘', reading(requests=[item(amount=30000)], reference='previous'))
        self.assertEqual(self.store.state['pending']['kind'], 'comparison')
        self.assertIn('plan_living', dict(self.store.state['pending']['choices']))
        self.assertIn('FOOD', self.store.state['unlock_categories'])
        self.propose.assert_not_called()

    def test_new_entry_only_queues_choice_and_keep_restores_exact_original_state(self):
        self.preview()
        before = deepcopy(self.store.state)
        response = self.turn(hints=self.hints)
        queued = deepcopy(self.store.state)
        queued.pop("over_entry")
        self.assertEqual(queued, before)
        self.assertEqual({choice["value"] for choice in response["reply"]["choices"]}, {"over_keep", "over_restart"})
        self.cancel.assert_not_called()
        self.complete.assert_not_called()
        self.turn("over_keep", hints=self.hints)
        self.assertEqual(self.store.state, before)
        self.assertEqual(len(self.cards), 1)

    def test_keep_preserves_comparison_and_question(self):
        self.store.state = {"requests": [item(amount=50000)], "financial_offer": {"comparison": {"plans": []}},
                            "requested_view": "recommendation",
                            "pending": {"kind": "financial_selection", "line": "골라 주세요.", "choices": []}}
        before = deepcopy(self.store.state)
        self.turn(hints=self.hints)
        response = self.turn("over_keep")
        self.assertEqual(self.store.state, before)
        self.assertEqual(response["reply"]["comparison"], {"plans": []})
        self.complete.assert_not_called()

    def test_restart_cancels_old_task_only_after_choice_and_clears_old_goal(self):
        first = self.preview()
        self.store.state.update(refinement={"donors": {"BEAUTY": {"target": 30000}}}, excluded=["SAVINGS"], llm_call_count=8)
        self.turn(hints=self.hints)
        self.cancel.assert_not_called()
        response = self.turn("over_restart", hints=self.hints)
        self.cancel.assert_called_once_with(first["task_ref"])
        self.assertTrue(response["new_work"])
        self.assertEqual(self.tasks[first["task_ref"]]["state"], "canceled")
        self.assertEqual(self.store.state["requests"], [item(amount=None)])
        for key in ("proposal", "work_ref", "over_entry", "refinement", "llm_call_count"):
            self.assertNotIn(key, self.store.state)
        self.complete.assert_not_called()

    def test_cancel_failure_and_pending_cancellation_preserve_old_preview(self):
        first = self.preview()
        self.turn(hints=self.hints)
        before = deepcopy(self.store.state)
        self.cancel.side_effect = RuntimeError("test cancellation failure")
        self.assertEqual(self.turn("over_restart")["error"], "task_check_failed")
        self.assertEqual(self.store.state, before)
        self.cancel.side_effect = None
        self.assertEqual(self.turn("over_restart")["status"], "awaiting_input")
        self.assertEqual(self.store.state, before)
        self.tasks[first["task_ref"]]["state"] = "canceled"
        self.assertTrue(self.turn("over_restart")["new_work"])

    def test_saving_cannot_be_canceled_by_new_entry(self):
        first = self.preview()
        self.turn(hints=self.hints)
        before = deepcopy(self.store.state)
        self.tasks[first["task_ref"]]["state"] = "saving"
        response = self.turn("over_restart")
        self.assertEqual(response["status"], "awaiting_input")
        self.assertEqual(self.store.state, before)
        self.cancel.assert_not_called()

    def test_overage_is_rechecked_before_canceling_existing_preview(self):
        self.preview()
        self.turn(hints=self.hints)
        before = deepcopy(self.store.state)
        self.context["spent"]["FOOD"] = 400000
        self.assertEqual(self.turn("over_restart")["status"], "unsupported")
        self.assertEqual(self.store.state, before)
        self.cancel.assert_not_called()

    def test_duplicate_entry_and_other_hints_do_not_restart_work(self):
        self.turn(hints=self.hints)
        before = deepcopy(self.store.state)
        self.turn(hints=self.hints)
        self.assertEqual(self.store.state, before)
        self.complete.assert_not_called()
        self.turn("3만 원", reading(requests=[item(amount=30000)], reference="previous"),
                  hints={"from": "spending", "category": "BEAUTY"})
        self.assertEqual(self.store.state["requests"][0]["category"], "FOOD")

    def test_unselected_text_does_not_replace_existing_work(self):
        self.preview()
        self.turn(hints=self.hints)
        before = deepcopy(self.store.state)
        self.turn("3만 원 늘려줘")
        self.assertEqual(self.store.state, before)
        self.complete.assert_not_called()
        self.cancel.assert_not_called()

    def test_spending_return_resumes_overage_work_without_a_restart_question(self):
        self.turn(hints=self.hints)
        before = deepcopy(self.store.state)
        result = self.turn("교통은 얼마 남았어?", reading(mine=False, handoff="spending", handoff_category="TRANSPORT_CAR"))
        self.assertEqual(result["status"], "not_mine")
        self.assertEqual(self.store.state, before)
        self.turn("교통·차량에서 가져오기", hints={"from": "spending", "category": "TRANSPORT_CAR"})
        self.complete.assert_not_called()
        self.assertNotIn("over_entry", self.store.state)
        self.assertEqual(self.store.state["pending"]["kind"], "amount")
        self.assertEqual(self.store.state["requests"][0]["category"], "FOOD")
        self.assertEqual(self.store.state["sources"], ["TRANSPORT_CAR"])

    def test_finished_task_does_not_leave_an_unanswerable_restart_question(self):
        first = self.preview()
        self.turn(hints=self.hints)
        self.tasks[first["task_ref"]]["state"] = "expired"
        self.assertEqual(self.turn("over_restart")["status"], "ended")
        self.assertIsNone(self.store.state)
        self.cancel.assert_not_called()

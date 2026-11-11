"""초과 진입의 웹 연결부·저장 어댑터 계약. 외부 LLM 없이 합성 예산만 사용한다."""
from copy import deepcopy
import json
from unittest.mock import Mock

from django.db import transaction
from django.test import TestCase, override_settings

from budgets.adjustment_service import AdjustmentAdapter
from budgets.adjustment_core.calculation import proportional_group_reduction
from budgets.models import Budget
from budgets.test_adjustment_core import item, reading
from chat.budget_adjustment import handler
from chat.models import Conversation
from chat.router_core import MemoryStateStore
from chat import test_budget_adjustment as api_fixtures


@override_settings(CHAT_ENGINE="router", CHAT_LLM="fake", BUDGET_ADJUST_ALLOCATOR=proportional_group_reduction)
class OverBudgetServiceTest(TestCase):
    def setUp(self):
        api_fixtures.BudgetAdjustmentApiTest.setUp(self)
        self.ctx["spent"]["FOOD"] = 412000
        self.talk = Conversation.objects.create(user=self.user)
        self.store, self.complete = MemoryStateStore(), Mock(side_effect=AssertionError("no interpretation expected"))
        self.adapter = AdjustmentAdapter(self.user, conversation=self.talk)
        self.handle = handler(self.user, self.store, self.adapter, self.complete, "talk")
        self.request = {"mode": "start", "text": None, "hints": {
            "reason": "over", "categories": ["FOOD"], "over_amount": 999999}}

    def test_internal_handoff_starts_without_llm_and_exposes_funding_phase(self):
        result = self.handle(self.request)
        payload = result["reply"]["budget_adjustment"]
        self.assertEqual(payload["phase"], "funding_scope")
        self.assertFalse(payload["can_approve"])
        self.assertEqual(self.store.get_agent_state("talk", "budget")["over_budget"]["over_amount"], 12000)
        self.complete.assert_not_called()
        self.assertEqual(Budget.objects.count(), 1)

    def test_new_work_resets_call_count_only_after_explicit_selection(self):
        original = {"requests": [item(amount=50000)], "llm_call_count": 8}
        self.store.save_agent_state("talk", "budget", deepcopy(original))
        choice = self.handle(self.request)
        self.assertEqual(choice["reply"]["budget_adjustment"]["phase"], "over_entry")
        self.assertEqual(self.store.get_agent_state("talk", "budget")["llm_call_count"], 8)
        self.handle({"mode": "continue", "text": "over_restart"})
        state = self.store.get_agent_state("talk", "budget")
        self.assertEqual(state["llm_call_count"], 0)
        self.assertEqual(state["requests"], [item(amount=None)])
        self.complete.assert_not_called()

    def test_selected_amount_and_source_save_only_an_unapproved_preview(self):
        self.handle(self.request)
        self.handle({"mode": "continue", "text": "spare"})
        self.complete.assert_not_called()
        self.complete.side_effect = [json.dumps(reading(requests=[item(amount=30000)], reference="previous"))]
        response = self.handle({"mode": "continue", "text": "3만 원 늘려줘"})
        proposal = response["reply"]["proposal"]
        self.assertEqual(proposal["allocations"]["FOOD"], 430000)
        self.assertEqual(Budget.objects.count(), 1)
        with transaction.atomic():
            self.adapter.flush(self.talk)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, "approved")
        self.assertEqual(Budget.objects.get(pk=proposal["proposal_id"]).status, "validated")
        self.complete.assert_called_once()

    def test_public_message_api_does_not_accept_unvalidated_top_level_hints(self):
        response = self.client.post("/api/chat/messages/", {"conversationId": None, "choice": "budget",
                                   "hints": self.request["hints"]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Budget.objects.count(), 1)

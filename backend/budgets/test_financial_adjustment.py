"""Synthetic DB tests for consent boundaries, revisions and final approval replay."""
from copy import deepcopy
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings
from django.db import close_old_connections, connections, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from chat import test_budget_adjustment as api_fixtures
from chat.models import Conversation
from users.models import Consent
from .ai_consent import consent_state, has_consent
from .models import Budget, BudgetCategorySetting, BudgetCycle, BudgetItem
from .adjustment_core.financial_planner import synthetic_diagnostics
from .adjustment_core.calculation import proportional_group_reduction
from .test_adjustment_core import item, reading
from .test_adjustment_core import context, envelope


@override_settings(CHAT_ENGINE="router", CHAT_LLM="fake", CHAT_TODAY="2026-11-10",
                   BUDGET_ADJUST_ALLOCATOR=proportional_group_reduction)
class FinancialAdjustmentApiTest(TestCase):
    post, say = api_fixtures.BudgetAdjustmentApiTest.post, api_fixtures.BudgetAdjustmentApiTest.say

    def setUp(self):
        api_fixtures.BudgetAdjustmentApiTest.setUp(self)
        self.ctx.update(today="2026-11-10")
        self.ctx["diagnostic_inputs"] = synthetic_diagnostics(self.ctx["allocations"])
        self.ctx["diagnostic_inputs"]["planned_spare"]["amount"] = 0
        self.model = Mock(explains=False)
        self.reader = patch("chat.engine.reading", return_value=self.model)
        self.reader.start()
        self.addCleanup(self.reader.stop)

    def chat(self, cid=None, text=None, choice=None, answers=()):
        self.model.side_effect = [json.dumps(v, ensure_ascii=False) for v in answers]
        return self.say(cid, **({"text": text} if text else {"choice": choice}))

    def offer(self):
        start = self.chat(choice="budget")
        self.cid = start["conversationId"]
        reply = self.chat(self.cid, text="식비를 5만 원 늘리고 싶어. 비교해줘", answers=[
            reading(requests=[item(amount=50000)], action="compare", sources=["SPARE", "LIVING"]),
            {"plans": [{"funding": [{"source": "SPARE", "amount": 50000}]}], "recommendation_index": None, "reason_fact_ids": []},
            {"plans": [], "recommendation_index": 0, "reason_fact_ids": ["reserve", "plan_0.preserved"]}])
        return reply["messages"][-1]["content"]

    def select(self, content):
        reply = self.chat(self.cid, choice={"value": "financial_0", "turn": content["turn"]})
        return reply["messages"][-1]["content"]["budgetAdjustment"]["proposal"]

    def test_explicit_amount_ignores_empty_placeholder_in_new_and_persisted_state(self):
        from chat.engine import ConversationStore, KEY
        blank = dict.fromkeys(("category", "amount", "amount_mode", "direction"))
        for persisted in (False, True):
            with self.subTest(persisted=persisted):
                cid = self.chat(choice="budget")["conversationId"]
                if persisted:
                    talk = Conversation.objects.get(pk=cid)
                    store = ConversationStore.load(talk.slots)
                    state = store.get_agent_state(KEY, "budget")
                    state["requests"] = [deepcopy(blank)]
                    store.save_agent_state(KEY, "budget", state)
                    talk.slots = store.dump()
                    talk.save(update_fields=["slots"])
                response = self.chat(cid, text="여윳돈에서 가져와 식비 예산을 1만 원 더 늘려줘", answers=[{
                    "reading": reading(requests=[item(amount=10000), deepcopy(blank)], sources=["SPARE"], destinations=["FOOD"]),
                    "reply": "말씀하신 조건으로 확인할게요."}])
                content = response["messages"][-1]["content"]["budgetAdjustment"]
                self.assertTrue(content["canApprove"])
                self.assertEqual(content["proposal"]["allocations"]["FOOD"], 410000)
                self.assertEqual(content["proposal"]["buckets"]["SPARE"], 390000)
                stored = ConversationStore.load(Conversation.objects.get(pk=cid).slots).get_agent_state(KEY, "budget")
                self.assertEqual(len(stored["requests"]), 1)

    def test_discussion_keeps_conditions_until_recommendation_and_comparison_are_requested(self):
        start = self.chat(choice="budget")
        cid = start["conversationId"]
        first = self.chat(cid, text="식비가 부족해", answers=[{
            "reading": reading(requests=[item()], reference="previous"),
            "reply": "최근 식비에서 달라진 지출이 있었나요?"}])["messages"][-1]["content"]
        self.assertNotIn("proposal", first["budgetAdjustment"])
        self.assertNotIn("comparison", first["budgetAdjustment"])
        second = self.chat(cid, text="요즘 외식이 늘었어", answers=[{
            "reading": reading(reason="외식이 늘어남", reference="previous"),
            "reply": "외식이 늘어난 상황이군요. 조정할 때 꼭 유지할 예산이 있나요?"}])["messages"][-1]["content"]
        third = self.chat(cid, text="저축은 유지하고 여윳돈을 쓰고 싶어", answers=[{
            "reading": reading(sources=["SPARE"], excluded=["SAVINGS"], reference="previous"),
            "reply": "저축을 유지하고 여윳돈으로 보충하는 방향으로 볼게요."}])["messages"][-1]["content"]
        self.assertNotIn("proposal", third["budgetAdjustment"])
        self.assertNotIn("comparison", third["budgetAdjustment"])
        self.assertEqual(Budget.objects.count(), 1)
        for content in (first, second, third):
            self.assertTrue({"recommend", "compare"} <= {choice["value"] for choice in content["choices"]})
        offered = self.chat(cid, choice={"value": "recommend", "turn": third["turn"]}, answers=[
            {"plans": [{"funding": [{"source": "SPARE", "amount": 120000}]}], "recommendation_index": None, "reason_fact_ids": []},
            {"plans": [], "recommendation_index": 0, "reason_fact_ids": ["reserve", "plan_0.preserved"]}
        ])["messages"][-1]["content"]
        self.assertNotIn("comparison", offered["budgetAdjustment"])
        card = offered["budgetAdjustment"]["proposal"]
        self.assertEqual(card["allocations"]["FOOD"], 520000)
        self.assertEqual(card["buckets"]["SAVINGS"], 600000)
        self.assertEqual(Budget.objects.get(pk=card["proposalId"]).status, "validated")
        compared = self.chat(cid, choice={"value": "compare", "turn": offered["turn"]}, answers=[
            {"plans": [{"funding": [{"source": "SPARE", "amount": 120000}]}], "recommendation_index": None, "reason_fact_ids": []},
            {"plans": [], "recommendation_index": 0, "reason_fact_ids": ["reserve", "plan_0.preserved"]}
        ])["messages"][-1]["content"]
        self.assertIn("comparison", compared["budgetAdjustment"])
        self.assertEqual(Budget.objects.count(), 2)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, "approved")

    def test_compare_select_refine_and_approve_only_direct_goal(self):
        content = self.offer()
        self.assertEqual(Budget.objects.count(), 1)
        first = self.select(content)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, "approved")
        response = self.chat(self.cid, text="쇼핑 2만 원 더 줄여줘", answers=[
            {"action": "refine_more", "category": "FASHION_SHOPPING", "amount": 20000,
             "amount_mode": "by", "suggested_target": None}])
        second = response["messages"][-1]["content"]["budgetAdjustment"]["proposal"]
        self.assertEqual(second["allocations"]["FASHION_SHOPPING"], 180000)
        self.assertEqual(second["allocations"]["FOOD"], 450000)
        self.assertEqual(Budget.objects.get(pk=first["proposalId"]).status, "expired")
        self.assertEqual(self.post(f"/api/budgets/proposals/{first['proposalId']}/approve/", {}).status_code, 409)
        result = self.post(f"/api/budgets/proposals/{second['proposalId']}/approve/", {"keepForNextCycle": True})
        self.assertEqual(result.status_code, 200, result.content)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.budget_category_targets, {"FOOD": 450000})

    def test_free_text_recommendation_shows_one_preview_without_comparison(self):
        cid = self.chat(choice="budget")["conversationId"]
        response = self.chat(cid, text="식비를 5만 원 늘릴 추천안 좀 보여줘", answers=[
            {"reading": reading(requests=[item(amount=50000)], action="compare", sources=["SPARE"]), "reply": "원하는 조건으로 확인할게요."},
            {"plans": [{"funding": [{"source": "SPARE", "amount": 50000}]}], "recommendation_index": None, "reason_fact_ids": []},
            {"plans": [], "recommendation_index": 0, "reason_fact_ids": ["reserve", "plan_0.preserved"]}])
        result = response["messages"][-1]["content"]["budgetAdjustment"]
        self.assertNotIn("comparison", result)
        self.assertEqual(result["proposal"]["allocations"]["FOOD"], 450000)

    def test_first_new_request_after_initial_approval_uses_current_approved_budget(self):
        from chat.engine import ConversationStore, KEY
        self.base.status, self.base.approved_at = "validated", None
        self.base.save(update_fields=["status", "approved_at"])
        self.ctx.update(mode="INITIAL_ADJUSTMENT", source_status="validated")
        first = self.select(self.offer())
        result = self.post(f"/api/budgets/{first['proposalId']}/approve/", {})
        self.assertEqual(result.status_code, 200, result.content)
        self.ctx.update(base_budget_id=first["proposalId"], mode="NORMAL_ADJUSTMENT", source_status="approved",
                        allocations=deepcopy(first["allocations"]), buckets=deepcopy(first["buckets"]))
        self.ctx["bucket_available"]["SPARE"] = self.ctx["buckets"]["SPARE"]
        response = self.chat(self.cid, text="여윳돈에서 가져와 식비 예산을 1만 원 더 늘려줘", answers=[{
            "reading": reading(requests=[item(amount=10000)], sources=["SPARE"], reference="previous"),
            "reply": "말씀하신 조건으로 확인할게요."}])
        content = response["messages"][-1]["content"]["budgetAdjustment"]
        self.assertEqual(content["status"], "replied")
        second = content["proposal"]
        self.assertEqual(second["mode"], "NORMAL_ADJUSTMENT")
        self.assertEqual(second["allocations"]["FOOD"], 460000)
        self.assertEqual(second["buckets"]["SPARE"], 340000)
        self.assertNotEqual(second["taskRef"], first["taskRef"])
        self.assertEqual(str(Budget.objects.get(pk=second["proposalId"]).base_budget_id), first["proposalId"])
        state = ConversationStore.load(Conversation.objects.get(pk=self.cid).slots).get_agent_state(KEY, "budget")
        self.assertEqual(state["llm_call_count"], 1)
        self.assertEqual(state["reference_context"]["base_budget_id"], first["proposalId"])
        self.assertEqual(state["reference_context"]["ml_reference"], self.ctx["ml_reference"])
        self.assertEqual(Budget.objects.get(pk=first["proposalId"]).status, "approved")

    def test_changed_spending_expires_before_feedback_calls_model(self):
        first = self.select(self.offer())
        self.model.reset_mock()
        self.ctx["spent"]["FOOD"] = 130000
        response = self.chat(self.cid, text="쇼핑 더 줄여도 돼")
        self.assertEqual(response["messages"][-1]["content"]["budgetAdjustment"]["proposal"]["state"], "expired")
        self.model.assert_not_called()

    def test_unlock_consent_survives_comparison_and_revision_but_applies_only_on_approval(self):
        setting = BudgetCategorySetting.objects.create(user=self.user, cycle=self.base.cycle,
            category_id="FOOD", locked=True, locked_amount=400000, revision_no=2)
        self.ctx.update(locks={"FOOD": 400000}, lock_revisions={"FOOD": 2})
        self.cid = self.chat(choice="budget")["conversationId"]
        asked = self.chat(self.cid, text="식비 5만 원 늘려줘", answers=[
            reading(requests=[item(amount=50000)], sources=["SPARE", "LIVING"])])
        self.assertIn("잠겨", str(asked))
        offered = self.chat(self.cid, text="잠금 풀고 비교해줘", answers=[
            reading(unlock=True, action="compare"),
            {"plans": [{"funding": [{"source": "SPARE", "amount": 50000}]}], "recommendation_index": None, "reason_fact_ids": []},
            {"plans": [], "recommendation_index": 0, "reason_fact_ids": ["reserve", "plan_0.preserved"]}])
        first = self.select(offered["messages"][-1]["content"])
        revised = self.chat(self.cid, text="쇼핑 2만 원 더 줄여줘", answers=[
            {"action": "refine_more", "category": "FASHION_SHOPPING", "amount": 20000,
             "amount_mode": "by", "suggested_target": None}])
        card = revised["messages"][-1]["content"]["budgetAdjustment"]["proposal"]
        setting.refresh_from_db()
        self.assertTrue(setting.locked)
        self.assertEqual(setting.revision_no, 2)
        self.assertEqual(card["allocations"]["FOOD"], 450000)
        self.assertEqual(Budget.objects.get(pk=first["proposalId"]).status, "expired")
        result = self.post(f"/api/budgets/proposals/{card['proposalId']}/approve/", {})
        self.assertEqual(result.status_code, 200, result.content)
        setting.refresh_from_db()
        self.assertFalse(setting.locked)
        self.assertEqual(setting.revision_no, 3)

    def test_analysis_cannot_be_approved_after_source_availability_changes(self):
        card = self.select(self.offer())
        self.ctx["bucket_available"]["SPARE"] = 10000
        reply = self.post(f"/api/budgets/proposals/{card['proposalId']}/approve/", {})
        self.assertEqual(reply.status_code, 409)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, "approved")

    @unittest.skipUnless(os.environ.get("BUDGET_LIVE_SYNTHETIC_TEST") == "1", "Opt-in real LLM; synthetic fixture only")
    @override_settings(CHAT_LLM="live")
    def test_live_synthetic_comparison_feedback_and_final_approval(self):
        from chat.llm.live import LiveReading
        from .ai_consent import PURPOSES, document, document_hash
        for purpose in PURPOSES["adjustment"]:
            doc = document(purpose)
            Consent.objects.create(user=self.user, purpose=purpose, document_version=doc["version"], document_hash=document_hash(doc))
        self.reader.stop()
        live = LiveReading(10, None)
        self.enterContext(patch("chat.engine.reading", return_value=live))
        start = self.say(choice="budget")
        cid = start["conversationId"]
        first = self.say(cid, text="식비가 부족해")["messages"][-1]["content"]
        compared = self.say(cid, choice={"value": "compare", "turn": first["turn"]})["messages"][-1]["content"]
        comparison = compared["budgetAdjustment"]["comparison"]
        selected = comparison["recommendation"]["planId"]
        self.assertIsNotNone(selected)
        preview = self.say(cid, choice={"value": "financial_" + selected, "turn": compared["turn"]})["messages"][-1]["content"]["budgetAdjustment"]["proposal"]
        revision = self.say(cid, text="쇼핑은 더 줄여도 돼")["messages"][-1]["content"]["budgetAdjustment"]["proposal"]
        self.assertEqual(revision["allocations"]["FOOD"], preview["allocations"]["FOOD"])
        self.assertLess(revision["allocations"]["FASHION_SHOPPING"], preview["allocations"]["FASHION_SHOPPING"])
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, "approved")
        result = self.post(f"/api/budgets/proposals/{revision['proposalId']}/approve/", {"keepForNextCycle": True})
        self.assertEqual(result.status_code, 200, result.content)
        self.profile.refresh_from_db()
        self.assertEqual(set(self.profile.budget_category_targets), {"FOOD"})
        self.assertTrue(all(a["kind"] == "ok" for a in live.client.attempts))
        print("SYNTHETIC_SERVICE_LLM_CALLS", len(live.client.attempts))


@override_settings(CHAT_ENGINE="router", CHAT_LLM="live", BUDGET_DESIGN_ENABLED=True, BUDGET_DESIGN_EXTERNAL_ENABLED=True)
class BudgetConsentTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="budget-consent@example.test")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def grant(self, scope):
        path = "/api/budgets/ai-consent/" if scope == "design" else "/api/budgets/adjustment-ai-consent/"
        state = self.client.get(path).json()
        body = {"agreed": True, "healthAgreed": True,
                **{k: state[k] for k in ("documentVersion", "documentHash", "healthDocumentVersion", "healthDocumentHash")}}
        result = self.client.put(path, body, format="json")
        self.assertEqual(result.status_code, 200, result.content)
        return path, body

    def test_design_permission_allows_routing_but_not_adjustment(self):
        from chat.llm.fake import FakeReading
        from chat.router_core.reading import PROMPT
        self.grant("design")
        reader = Mock(wraps=FakeReading(), explains=False)
        with patch("chat.engine.reading", return_value=reader):
            response = self.client.post("/api/chat/messages/", {"conversationId": None, "text": "식비 예산 줄여줘"}, format="json")
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["error"]["code"], "budget_adjust_consent_required")
            self.assertEqual([call.args[0] for call in reader.call_args_list], [PROMPT])
            reader.reset_mock()
            denied = self.client.post("/api/chat/messages/", {"conversationId": None, "choice": "budget"}, format="json")
            self.assertEqual(denied.status_code, 403)
            reader.assert_not_called()
        self.assertTrue(has_consent(self.user, "design"))
        self.assertFalse(has_consent(self.user, "adjustment"))

    def test_new_spending_chat_works_without_adjustment_consent(self):
        from chat.llm.fake import FakeReading
        with patch("chat.engine.reading", return_value=FakeReading()):
            response = self.client.post("/api/chat/messages/", {"conversationId": None, "text": "이번 달 얼마 썼어?"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["route"]["intent"], "spending")

    def test_separate_explicit_checkboxes_hash_and_revocation(self):
        path, body = self.grant("adjustment")
        self.assertTrue(has_consent(self.user))
        invalid = self.client.put(path, {**body, "documentHash": "old"}, format="json")
        self.assertEqual(invalid.status_code, 409)
        self.assertEqual(invalid.json()["error"]["code"], "document_outdated")
        invalid = self.client.put(path, {**body, "healthAgreed": False}, format="json")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(self.client.delete(path, {"confirm": True}, format="json").status_code, 200)
        self.assertFalse(has_consent(self.user))
        self.assertEqual(Consent.objects.filter(user=self.user, revoked_at__isnull=False).count(), 2)

    def test_disabled_features_reject_grants_and_allow_old_consent_revocation(self):
        for scope, flags in (("design", {"BUDGET_DESIGN_ENABLED": False}),
                             ("design", {"BUDGET_DESIGN_EXTERNAL_ENABLED": False}),
                             ("adjustment", {"CHAT_LLM": "fake"})):
            with self.subTest(scope=scope, flags=flags):
                path, body = self.grant(scope)
                Consent.objects.filter(user=self.user, purpose=consent_state(self.user, scope)["purpose"]).update(document_hash="old")
                with override_settings(**flags):
                    state = self.client.get(path).json()
                    self.assertFalse(state["available"])
                    self.assertTrue(state["canRevoke"])
                    before = Consent.objects.count()
                    response = self.client.put(path, body, format="json")
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(response.json()["error"]["code"], "budget_ai_unavailable")
                    self.assertEqual(Consent.objects.count(), before)
                    response = self.client.delete(path, {"confirm": True}, format="json")
                    self.assertEqual(response.status_code, 200)
                    self.assertFalse(response.json()["canRevoke"])


@override_settings(CHAT_LLM="fake", BUDGET_ADJUST_ALLOCATOR=proportional_group_reduction)
class ConcurrentAdjustmentApprovalTest(TransactionTestCase):
    serialized_rollback = True
    def test_two_conversations_cannot_spend_the_same_spare_twice(self):
        from users.models import FinancialProfile
        from .adjustment_context import AdjustmentContextError
        from .adjustment_service import AdjustmentAdapter
        from .api import lock_user
        user = get_user_model().objects.create_user(email="parallel-adjustment@example.test")
        FinancialProfile.objects.create(user=user, payday=1, living_budget=1000000, savings_floor=600000,
                                        cushion_monthly=200000, monthly_take_home_income=3000000)
        today = timezone.localdate()
        cycle = BudgetCycle.objects.create(user=user, starts_on=today.replace(day=1), ends_before=today.replace(year=today.year + 1, month=1, day=1))
        base = Budget.objects.create(user=user, cycle=cycle, version_no=1, status="approved", funding_amount=3000000,
                                     approved_at=timezone.now())
        ctx = context()
        ctx.update(base_budget_id=str(base.pk), cycle_id=str(cycle.pk), spending_status="complete")
        BudgetItem.objects.bulk_create([BudgetItem(user=user, budget=base, bucket="living", category_id=c, allocated_amount=n)
                                       for c, n in ctx["allocations"].items()] + [
            BudgetItem(user=user, budget=base, bucket=b, allocated_amount=n) for b, n in (("fixed", 800000), ("savings", 600000), ("cushion", 200000))])
        def current(*args, **kwargs):
            if Budget.objects.get(pk=base.pk).status != "approved":
                raise AdjustmentContextError("expired")
            return deepcopy(ctx)
        barrier = Barrier(2)
        def approve(number, proposal):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(user)
                barrier.wait(timeout=10)
                reply = client.post(f"/api/budgets/proposals/{proposal}/approve/", {}, format="json", HTTP_IDEMPOTENCY_KEY=f"parallel-{number}")
                return reply.status_code
            finally:
                connections.close_all()
        with patch("budgets.adjustment_service.get_adjustment_context", side_effect=current), patch("budgets.overview.budget_data", return_value={}):
            proposals = []
            for _ in range(2):
                talk = Conversation.objects.create(user=user)
                adapter = AdjustmentAdapter(user, conversation=talk)
                inp = adapter.get_context()
                card = adapter.propose(inp, adapter.simulate(inp, envelope(item(amount=300000), sources=["SPARE"])), scope="cycle")
                with transaction.atomic():
                    lock_user(user)
                    adapter.flush(talk)
                proposals.append(card["proposal_id"])
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda pair: approve(*pair), enumerate(proposals)))
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(Budget.objects.filter(user=user, status="approved").count(), 1)
        self.assertEqual(Budget.objects.filter(user=user, status="expired").count(), 1)

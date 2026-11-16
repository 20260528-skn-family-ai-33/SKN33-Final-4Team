"""실제 메시지·조정안 API, DB 저장과 대화 경합. LLM/금융 자료는 고정 테스트 입력만 사용한다."""
from copy import deepcopy
from datetime import date
from unittest.mock import patch
import json
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from budgets.models import Budget, BudgetCycle, BudgetItem
from budgets.test_adjustment_core import allocation, context
from chat.llm.fake import FakeReading
from chat.models import AsyncTask, ChatMessage, Conversation
from users.models import FinancialProfile


@override_settings(CHAT_ENGINE="router", CHAT_LLM="fake", BUDGET_ADJUST_ALLOCATOR=allocation)
class BudgetAdjustmentApiTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="adjustment-api@example.test", password="test-password")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.profile = FinancialProfile.objects.create(user=self.user, monthly_take_home_income=3000000,
            payday=1, living_budget=1000000, savings_floor=600000, cushion_monthly=200000)
        today = timezone.localdate()
        cycle = BudgetCycle.objects.create(user=self.user, starts_on=today.replace(day=1),
                                          ends_before=today.replace(year=today.year + 1, month=1, day=1))
        self.base = Budget.objects.create(user=self.user, cycle=cycle, version_no=1, status="approved",
                                          funding_amount=3000000, approved_at=timezone.now())
        self.ctx = context()
        self.ctx.update(base_budget_id=str(self.base.pk), cycle_id=str(cycle.pk), spending_status="complete")
        BudgetItem.objects.bulk_create([
            BudgetItem(user=self.user, budget=self.base, bucket="living", category_id=code, allocated_amount=amount)
            for code, amount in self.ctx["allocations"].items()
        ] + [BudgetItem(user=self.user, budget=self.base, bucket=bucket, allocated_amount=amount)
             for bucket, amount in (("fixed", 800000), ("savings", 600000), ("cushion", 200000))])
        patcher = patch("budgets.adjustment_service.get_adjustment_context", side_effect=lambda *a, **k: deepcopy(self.ctx))
        self.context_read = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("budgets.overview.budget_data", return_value={"state": "ready"})
        self.budget_render = patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, path, body, key=None):
        return self.client.post(path, body, format="json", HTTP_IDEMPOTENCY_KEY=key or str(uuid4()))

    def say(self, conversation=None, **body):
        response = self.post("/api/chat/messages/", {"conversationId": conversation, **body})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def ready(self, *, entry=None):
        start = self.say(choice="budget", **({"budgetContext": entry} if entry else {}))
        cid = start["conversationId"]
        asked = self.say(cid, text="식비를 1만 5천 원 늘려줘")
        content = asked["messages"][-1]["content"]
        reply = self.say(cid, choice={"value": "plan_living", "turn": content["turn"]})
        return cid, reply["messages"][-1]["content"]

    def test_message_preview_approval_and_refresh_keep_same_verified_money(self):
        cid, content = self.ready()
        adjustment = content["budgetAdjustment"]
        self.assertTrue(adjustment["canApprove"])
        card = adjustment["proposal"]
        proposal = Budget.objects.get(pk=card["proposalId"])
        self.assertEqual(proposal.status, "validated")
        self.assertEqual(proposal.base_budget_id, self.base.pk)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, "approved")
        self.assertEqual(proposal.items.get(bucket="living", category_id="FOOD").allocated_amount, 415000)
        response = self.post(f"/api/budgets/proposals/{proposal.pk}/approve/", {"keepForNextCycle": True})
        self.assertEqual(response.status_code, 200, response.content)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.budget_category_targets, {"FOOD": 415000})
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, "superseded")
        restored = self.client.get(f"/api/chat/conversations/{cid}/").json()["messages"][-1]["content"]["budgetAdjustment"]
        self.assertEqual(restored["proposal"]["state"], "saved")
        self.assertFalse(restored["canApprove"])
        opposite = self.post(f"/api/budgets/proposals/{proposal.pk}/approve/", {"keepForNextCycle": False})
        self.assertEqual(opposite.status_code, 409)
        self.assertEqual(opposite.json()["error"]["code"], "idempotency_conflict")

    def test_initial_entry_uses_existing_approval_route_and_expires_original_after_success(self):
        self.base.status, self.base.approved_at = "validated", None
        self.base.save(update_fields=["status", "approved_at"])
        self.ctx.update(mode="INITIAL_ADJUSTMENT", source_status="validated")
        cid, content = self.ready(entry={"baseBudgetId": str(self.base.pk), "mode": "INITIAL_ADJUSTMENT"})
        proposal_id = content["budgetAdjustment"]["proposal"]["proposalId"]
        wrong = self.post(f"/api/budgets/proposals/{proposal_id}/approve/", {"keepForNextCycle": False})
        self.assertEqual(wrong.status_code, 409)
        with patch("budgets.api.current_salary_profile", side_effect=AssertionError("파생 조정안은 일반 급여일 활성화를 거치지 않는다")):
            response = self.post(f"/api/budgets/{proposal_id}/approve/", {"keepForNextCycle": False})
        self.assertEqual(response.status_code, 200, response.content)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, "expired")
        self.assertEqual(AsyncTask.objects.get(budgets__pk=proposal_id).conversation_id, Conversation.objects.get(pk=cid).pk)

    @override_settings(CHAT_TODAY="2026-10-29")
    def test_fixed_server_date_is_shared_by_chat_approval_and_budget_response(self):
        _, content = self.ready()
        proposal_id = content["budgetAdjustment"]["proposal"]["proposalId"]
        response = self.post(f"/api/budgets/proposals/{proposal_id}/approve/", {"keepForNextCycle": False})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.context_read.call_args.kwargs["today"], date(2026, 10, 29))
        self.assertEqual(self.budget_render.call_args.args[1], date(2026, 10, 29))

    def test_history_revalidates_only_the_latest_card(self):
        from budgets import adjustment_service
        cid, _ = self.ready()
        latest = ChatMessage.objects.filter(conversation_id=cid, role="bot").latest("created_at")
        older = ChatMessage.objects.create(user=self.user, conversation_id=cid, role="bot",
                                           content_type=latest.content_type, content=latest.content)
        ChatMessage.objects.filter(pk=older.pk).update(created_at=self.base.approved_at)
        with patch.object(adjustment_service, "proposal_card", wraps=adjustment_service.proposal_card) as check:
            response = self.client.get(f"/api/chat/conversations/{cid}/")
        self.assertEqual(response.status_code, 200, response.content)
        check.assert_called_once()
        cards = [m["content"]["budgetAdjustment"] for m in response.json()["messages"]
                 if m["content"].get("budgetAdjustment", {}).get("proposal")]
        self.assertEqual(len(cards), 2)
        self.assertFalse(cards[0]["canApprove"])
        self.assertFalse(cards[0]["proposal"]["canApprove"])
        self.assertTrue(cards[1]["canApprove"])

    def test_other_users_basis_is_not_disclosed(self):
        other = get_user_model().objects.create_user(email="other-adjust@example.test", password="test-password")
        self.client.force_authenticate(other)
        response = self.post("/api/chat/messages/", {"conversationId": None, "choice": "budget",
            "budgetContext": {"baseBudgetId": str(self.base.pk), "mode": "INITIAL_ADJUSTMENT"}})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Conversation.objects.count(), 0)

    OVER_HINTS = {"reason": "over", "categories": ["FOOD"], "overAmount": 12000}

    def over_entry(self, **hints):
        return {"baseBudgetId": str(self.base.pk), "mode": "NORMAL_ADJUSTMENT", "hints": {**self.OVER_HINTS, **hints}}

    def test_over_hints_from_budget_screen_start_the_over_adjustment(self):
        """예산 화면의 초과 카테고리 → 예산 조정이 질문 없이 재원 선택으로 (11/11). 화면이 보낸 금액은 참고값이고 저장하지 않는다"""
        from chat import budget_adjustment
        self.ctx["spent"].update(FOOD=412000, BEAUTY=60000)  # 식비 400,000원 → 12,000원 초과 · 뷰티 50,000원 → 10,000원 초과
        seen = []
        real = budget_adjustment.handle_expanded
        with patch("chat.budget_adjustment.handle_expanded", side_effect=lambda request, **ports: seen.append(request) or real(request, **ports)):
            start = self.say(choice="budget", budgetContext=self.over_entry(overAmount=999999))
            cid, content = start["conversationId"], start["messages"][-1]["content"]
            self.assertEqual(seen[0]["hints"], {"reason": "over", "categories": ["FOOD"], "over_amount": 999999})
            self.assertEqual((seen[0]["mode"], seen[0]["entry"], seen[0]["text"]), ("start", "button", None))
            self.assertEqual(start["messages"][0]["content"]["text"], "식비 예산 조정")
            self.assertIn("식비 예산을 12,000원 넘었어요", content["text"])  # 화면 금액(999,999)이 아니라 서버가 다시 계산한 초과액
            self.assertEqual((start["agent"], content["budgetAdjustment"]["phase"]), ("budget", "funding_scope"))
            self.assertNotIn("999999", json.dumps(Conversation.objects.get(pk=cid).slots, ensure_ascii=False))
        # 조정 중에 다른 초과 항목으로 들어오는 경우(이어가기 / 새 조정 질문)는 같은 대화 안에서 일어난다 —
        # 소비 분석 초과 답변의 [예산 조정하기]: chat.test_agent_collab.test_over_answer_suggestion_returns_with_the_over_category

    def test_over_hints_for_a_category_that_is_no_longer_over_do_not_start(self):
        """화면이 본 뒤 상태가 바뀌어 더는 초과가 아니면 조정을 시작하지 않는다 (서버가 다시 확인)"""
        start = self.say(choice="budget", budgetContext=self.over_entry())  # 고정 맥락의 식비는 120,000 / 400,000원
        content = start["messages"][-1]["content"]
        self.assertIn("현재 확인된 초과 항목이 아니에요", content["text"])

    def test_plain_budget_entry_sends_no_hints(self):
        from chat import budget_adjustment
        seen = []
        real = budget_adjustment.handle_expanded
        with patch("chat.budget_adjustment.handle_expanded", side_effect=lambda request, **ports: seen.append(request) or real(request, **ports)):
            start = self.say(choice="budget", budgetContext={"baseBudgetId": str(self.base.pk), "mode": "NORMAL_ADJUSTMENT"})
        self.assertEqual(seen[0]["hints"], {})
        self.assertEqual(start["messages"][0]["content"]["text"], "예산 조정")

    def test_bad_over_hints_are_rejected_before_anything_is_saved(self):
        bad = [
            None, [], "over", {},
            {**self.OVER_HINTS, "reason": "one_off"},
            {**self.OVER_HINTS, "categories": []},
            {**self.OVER_HINTS, "categories": ["FOOD", "BEAUTY"]},
            {**self.OVER_HINTS, "categories": ["HOUSING_TELECOM"]},   # 예산 밖 카테고리
            {**self.OVER_HINTS, "categories": ["ETC"]},               # 기타는 초과 항목 조정을 시작하지 않는다
            {**self.OVER_HINTS, "categories": ["NOPE"]},
            {**self.OVER_HINTS, "categories": "FOOD"},
            {**self.OVER_HINTS, "categories": [{"code": "FOOD"}]},
            {**self.OVER_HINTS, "overAmount": 0},
            {**self.OVER_HINTS, "overAmount": -1},
            {**self.OVER_HINTS, "overAmount": "12000"},
            {**self.OVER_HINTS, "overAmount": True},
            {**self.OVER_HINTS, "overAmount": 1.5},
            {**self.OVER_HINTS, "amount": 50000},                      # 금액 · 슬롯 직접 주입 금지
            {"reason": "over", "categories": ["FOOD"]},
        ]
        for hints in bad:
            with self.subTest(hints=hints):
                response = self.post("/api/chat/messages/", {"conversationId": None, "choice": "budget", "budgetContext": {
                    "baseBudgetId": str(self.base.pk), "mode": "NORMAL_ADJUSTMENT", "hints": hints}})
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("budgetContext", response.json()["error"]["fields"])
        self.assertEqual(Conversation.objects.count(), 0)

    def test_over_hints_need_the_budget_button_and_a_normal_basis(self):
        entry = self.over_entry()
        cases = [{"conversationId": None, "text": "식비 조정", "budgetContext": entry},                      # 문장과 함께는 안 됨
                 {"conversationId": None, "choice": "spending", "budgetContext": entry},                    # 다른 기능 버튼
                 {"conversationId": None, "choice": "budget", "budgetContext": {**entry, "mode": "INITIAL_ADJUSTMENT"}},  # 승인 전 초안
                 {"conversationId": None, "choice": "budget", "budgetContext": {                            # 분배 실패 복구에는 hints 없음
                     "mode": "DESIGN_RECOVERY", "sourceTaskId": str(uuid4()), "hints": entry["hints"]}}]
        for body in cases:
            with self.subTest(body=body):
                self.assertEqual(self.post("/api/chat/messages/", body).status_code, 400)
        other = get_user_model().objects.create_user(email="over-other@example.test", password="test-password")
        self.client.force_authenticate(other)  # 남의 기준 예산이면 hints 검사보다 404 가 먼저
        response = self.post("/api/chat/messages/", {"conversationId": None, "choice": "budget", "budgetContext": entry})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Conversation.objects.count(), 0)

    def test_expiry_is_persisted_without_applying_a_different_result(self):
        _, content = self.ready()
        proposal_id = content["budgetAdjustment"]["proposal"]["proposalId"]
        self.ctx["spent"]["FOOD"] += 1
        response = self.post(f"/api/budgets/proposals/{proposal_id}/approve/", {"keepForNextCycle": False})
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["error"]["code"], "expired")
        self.assertEqual(Budget.objects.get(pk=proposal_id).status, "expired")
        self.assertEqual(Budget.objects.get(pk=self.base.pk).status, "approved")

    def test_budget_prompt_is_registered_with_the_existing_live_schema_client(self):
        from budgets.adjustment_core.interpretation import PROMPT, SCHEMA
        from chat.llm.schemas import schema_for
        self.assertEqual(schema_for(PROMPT), ("budget_adjustment_reading", SCHEMA))

    def test_declined_query_preserves_every_budget_state_field(self):
        from chat import engine
        for preview in (False, True):
            with self.subTest(preview=preview):
                if preview:
                    cid, _ = self.ready()
                else:
                    cid = self.say(choice="budget")["conversationId"]
                    self.say(cid, text="식비 5만 원 늘려줘")
                before = deepcopy(engine.ConversationStore.load(Conversation.objects.get(pk=cid).slots)
                                  .get_agent_state(engine.KEY, "budget"))
                proposals = Budget.objects.count()
                self.say(cid, text="교통은 얼마 남았어?")
                after = engine.ConversationStore.load(Conversation.objects.get(pk=cid).slots)
                self.assertEqual(after.get_agent_state(engine.KEY, "budget"), before)
                self.assertEqual(Budget.objects.count(), proposals)

    def test_failed_interpretations_cannot_reset_the_per_task_llm_limit(self):
        from chat import engine
        reader = FakeReading()
        with patch.object(engine, "reading", return_value=reader):
            first = self.say(choice="budget")
            cid = first["conversationId"]
            for _ in range(11):
                self.say(cid, text="가짜 해석기에 없는 예산 문장")
        state = engine.ConversationStore.load(Conversation.objects.get(pk=cid).slots)
        self.assertEqual(state.get_agent_state(engine.KEY, "budget")["llm_call_count"], 10)
        self.assertEqual(reader.missed, 10)
        self.assertEqual(AsyncTask.objects.count(), 0)

    def test_first_failed_interpretation_preserves_frozen_reference_for_retry(self):
        from budgets.adjustment_core.financial_conversation import DIALOGUE_PROMPT
        from chat import engine

        class FailedBudgetReading(FakeReading):
            def __call__(self, system, text):
                if system == DIALOGUE_PROMPT:
                    raise RuntimeError("test interpretation failure")
                return super().__call__(system, text)

        with patch.object(engine, "reading", return_value=FailedBudgetReading()):
            first = self.say(text="식비 예산 줄여줘")
        self.assertTrue(first["route"]["fallback"])
        cid = first["conversationId"]
        state = engine.ConversationStore.load(Conversation.objects.get(pk=cid).slots)
        saved = state.get_agent_state(engine.KEY, "budget")
        self.assertEqual(saved["reference_context"]["ml_reference"], self.ctx["ml_reference"])
        self.assertEqual(saved["llm_call_count"], 1)
        self.say(cid, text="식비 예산 줄여줘")
        self.assertTrue(callable(self.context_read.call_args.kwargs["reference_provider"]))
        self.assertEqual(AsyncTask.objects.count(), 0)

    def test_conversation_conflict_cannot_leave_a_proposal_without_its_message(self):
        from chat import engine
        first = self.say(choice="budget")
        cid = first["conversationId"]
        asked = self.say(cid, text="식비를 1만 5천 원 늘려줘")["messages"][-1]["content"]
        original = engine.answer

        def another_turn_commits(*args, **kwargs):
            result = original(*args, **kwargs)
            talk = Conversation.objects.get(pk=cid)
            slots = deepcopy(talk.slots)
            slots["rev"] += 1
            Conversation.objects.filter(pk=cid).update(slots=slots)
            return result

        with patch.object(engine, "answer", side_effect=another_turn_commits):
            # 헤더 없는 요청도 대화 버전 충돌 전에 제안·작업을 저장하지 않아야 한다.
            response = self.client.post("/api/chat/messages/", {"conversationId": cid,
                "choice": {"value": "plan_living", "turn": asked["turn"]}}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "conversation_busy")
        self.assertEqual(Budget.objects.count(), 1)
        self.assertEqual(AsyncTask.objects.count(), 0)
        self.assertEqual(Conversation.objects.get(pk=cid).messages.count(), 4)

    def test_bank_permission_is_rechecked_before_staged_financial_writes(self):
        from rest_framework.exceptions import PermissionDenied
        first = self.say(choice="budget")
        cid = first["conversationId"]
        asked = self.say(cid, text="식비를 1만 5천 원 늘려줘")["messages"][-1]["content"]
        with patch("users.privacy.require_bank_not_revoked", side_effect=[None, PermissionDenied("금융 동의가 철회됐어요.")]):
            response = self.post("/api/chat/messages/", {"conversationId": cid,
                "choice": {"value": "plan_living", "turn": asked["turn"]}})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Budget.objects.count(), 1)
        self.assertEqual(AsyncTask.objects.count(), 0)

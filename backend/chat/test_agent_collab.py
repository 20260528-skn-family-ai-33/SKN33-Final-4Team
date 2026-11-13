"""예산 조정 ↔ 소비 분석 협업 (11/11): 조정 중 조회 질문은 소비 분석이 답하고, 조정 얘기가 다시 오면 그 조정안을 이어서 고친다.
실제 메시지 API · 실제 두 에이전트 · 가짜 LLM(정해 둔 문장). 예산 맥락은 고정 테스트 입력, 거래는 없다(쓴 돈 0원).
"""
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from budgets.models import Budget, BudgetCycle, BudgetItem
from budgets.test_adjustment_core import allocation, context
from chat import engine
from chat.llm.fake import FakeReading
from chat.models import Conversation
from chat.interpretation.legacy import RoleFakeLLM
from chat.router_core import MemoryStateStore, current_agent, run_turn
from chat.test_router_agents import PHASE_B_SAID, FakeAgent, FakeServer
from spending_analysis import agent as spending_agent, dialog
from spending_analysis.dialog import record_analysis, run_items
from users.models import FinancialProfile


SETTINGS = dict(CHAT_ENGINE="router", CHAT_LLM="fake", BUDGET_ADJUST_ALLOCATOR=allocation)


class CollabFixture:
    """고정 예산 + 실제 메시지 API 로 한 대화를 이어 가는 준비물 (실제 LLM 확인 도구 chat.live_collab_check 도 같이 쓴다)"""

    def setUp(self):
        self.user = get_user_model().objects.create_user(email="collab@example.test", password="test-password")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        FinancialProfile.objects.create(user=self.user, monthly_take_home_income=3000000, payday=1,
                                        living_budget=1000000, savings_floor=600000, cushion_monthly=200000)
        today = timezone.localdate()
        cycle = BudgetCycle.objects.create(user=self.user, starts_on=today.replace(day=1),
                                           ends_before=today.replace(year=today.year + 1, month=1, day=1))
        self.base = Budget.objects.create(user=self.user, cycle=cycle, version_no=1, status="approved",
                                          funding_amount=3000000, approved_at=timezone.now())
        self.ctx = context()
        # Keep this handoff scenario fully funded using an unranked category under priority protection.
        self.ctx['ml_reference']['BEAUTY'] = 20000
        self.ctx.update(base_budget_id=str(self.base.pk), cycle_id=str(cycle.pk), spending_status="complete")
        BudgetItem.objects.bulk_create([
            BudgetItem(user=self.user, budget=self.base, bucket="living", category_id=code, allocated_amount=amount)
            for code, amount in self.ctx["allocations"].items()
        ] + [BudgetItem(user=self.user, budget=self.base, bucket=bucket, allocated_amount=amount)
             for bucket, amount in (("fixed", 800000), ("savings", 600000), ("cushion", 200000))])
        for target, value in (("budgets.adjustment_service.get_adjustment_context", lambda *a, **k: deepcopy(self.ctx)),
                              ("budgets.overview.budget_data", lambda *a, **k: {"state": "ready"})):
            patcher = patch(target, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.llm = FakeReading()  # 호출 기록(calls)을 보려고 한 대화 동안 같은 가짜 해석기를 쓴다
        patcher = patch.object(engine, "reading", side_effect=lambda: self.llm)  # 실제 LLM 확인(live_collab_check)이 바꿔 끼운다
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cid = None

    def say(self, text=None, **body):
        if text is not None:
            body["text"] = text
        self.llm.calls.clear()
        response = self.client.post("/api/chat/messages/", {"conversationId": self.cid, **body}, format="json",
                                    HTTP_IDEMPOTENCY_KEY=str(uuid4()))
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.cid = data["conversationId"]
        return data, data["messages"][-1]["content"]

    def pick(self, content, value):
        return self.say(choice={"value": value, "turn": content["turn"]})

    def roles(self):
        return [call["role"] for call in self.llm.calls]

    def budget_state(self):
        store = engine.ConversationStore.load(Conversation.objects.get(pk=self.cid).slots)
        return store.get_agent_state(engine.KEY, "budget") or {}

    def proposal(self):
        """식비 5만 원 늘려줘 → 생활비 내에서 → 조정안 카드"""
        data, asked = self.say("식비 5만 원 늘려줘")
        self.assertEqual(data["agent"], "budget")
        data, card = self.pick(asked, "living")
        return card

    def changes(self, card):
        return {row["category"]: row["diff"] for row in card["budgetAdjustment"]["proposal"]["changes"] if row["diff"]}


@override_settings(**SETTINGS)
class AgentCollabTest(CollabFixture, TestCase):
    # ---- 목표 대화 ----

    def test_target_dialogue(self):
        card = self.proposal()
        first = self.changes(card)
        self.assertEqual(first["FOOD"], 50000)
        self.assertNotIn("TRANSPORT_CAR", first)  # 처음엔 교통에서 가져오지 않는다
        proposal_id = card["budgetAdjustment"]["proposal"]["proposalId"]

        # 조회 질문 → 예산 조정이 not_mine + 갈 곳 → 라우터 LLM 없이 소비 분석이 답한다
        before = self.budget_state()
        data, answer = self.say("교통은 얼마 남았어?")
        self.assertEqual((data["agent"], data["route"]["intent"]), ("spending", "spending"))
        self.assertEqual(self.roles(), ["budget_feedback", "fake"])  # 예산 조정 해석 1 + 소비 해석 1 (라우터 0)
        self.assertIn("100,000원", answer["text"])
        self.assertIn("지금 예산 기준이에요. 얼마까지 뺄 수 있는지는 예산 조정에서 확인할 수 있어요.", answer["notices"])
        self.assertIn({"value": "take_from", "label": "교통·차량에서 가져오기"}, answer["choices"])
        after = self.budget_state()
        self.assertEqual({k: v for k, v in after.items() if k != "llm_call_count"},
                         {k: v for k, v in before.items() if k != "llm_call_count"})  # 조정안 · 상태 그대로
        self.assertEqual(Budget.objects.get(pk=proposal_id).status, "validated")

        # 조정 얘기 → 이어서/새로 묻지 않고 그 조정안을 교통에서 가져오는 걸로 고친다
        data, revised = self.say("그럼 거기서 빼줘")
        self.assertEqual(data["agent"], "budget")
        self.assertEqual(self.roles(), ["spending", "router", "budget_feedback"])
        second = self.changes(revised)
        self.assertEqual(second["FOOD"], 50000)  # 증액 목표는 그대로
        self.assertEqual(second["TRANSPORT_CAR"], -45000)  # 직접 고른 교통도 시스템 보호액 55,000원을 남긴다.
        self.assertEqual(sum(second.values()), 0)
        self.assertTrue(revised["budgetAdjustment"]["canApprove"])
        self.assertNotEqual(revised["budgetAdjustment"]["proposal"]["proposalId"], proposal_id)

    def test_take_from_button_returns_without_any_llm_call(self):
        self.proposal()
        _, answer = self.say("교통은 얼마 남았어?")
        data, revised = self.pick(answer, "take_from")
        self.assertEqual(data["agent"], "budget")
        self.assertEqual(self.roles(), [])
        self.assertEqual(self.changes(revised)["TRANSPORT_CAR"], -45000)

    def test_budget_button_returns_to_the_kept_proposal_without_llm(self):
        card = self.proposal()
        self.say("교통은 얼마 남았어?")
        data, shown = self.say(choice="budget")  # [예산 조정] 기능 버튼 — 이어서/새로 묻지 않고 그 조정안을 다시 보여 준다
        self.assertEqual((data["agent"], self.roles()), ("budget", []))
        self.assertEqual(shown["budgetAdjustment"]["proposal"]["proposalId"], card["budgetAdjustment"]["proposal"]["proposalId"])
        self.assertTrue(shown["budgetAdjustment"]["canApprove"])

    def test_over_answer_suggestion_returns_with_the_over_category(self):
        """조정 중 조회 → 소비 분석의 초과 답변 → [예산 조정하기] → 예산 조정이 기존 조정안을 보존하고 초과 항목으로 새로 할지 묻는다 (11/11)"""
        from finance.models import Card, Connection, FinancialTransaction, SyncRun
        today = timezone.localdate()
        link = Connection.objects.create(user=self.user, business_type="card", organization_code="FIXTURE_CARD", status="connected")
        plastic = Card.objects.create(user=self.user, connection=link, origin="provider", source_card_key="collab-over")
        # 이번 주기 승인내역을 다 받았다는 기록이 있어야 소비 분석이 초과를 '확정'으로 답한다 (spending_analysis.coverage)
        SyncRun.objects.create(user=self.user, connection=link, card=plastic, product_code="card_approval_list", status="success",
                               covered_from=today.replace(day=1), covered_to=today)
        FinancialTransaction.objects.create(
            user=self.user, occurred_on=today, amount=130000, direction="out", transaction_type="spending",
            confirmed_category_id="TRANSPORT_CAR", confirmation_state="confirmed", source_kind="card", card=plastic)
        self.ctx["spent"]["TRANSPORT_CAR"] = 130000  # 교통 예산 100,000원 → 30,000원 초과 (예산 조정이 다시 확인하는 값)
        card = self.proposal()
        proposal_id = card["budgetAdjustment"]["proposal"]["proposalId"]
        data, answer = self.say("교통은 얼마 남았어?")
        self.assertEqual(data["agent"], "spending")
        self.assertIn("30,000원 초과했어요", answer["text"])
        self.assertIn({"value": "budget", "label": "예산 조정하기"}, answer["choices"])
        self.assertNotIn("over", answer)  # 라우터에만 알리고 화면 응답에는 넣지 않는다

        data, asked = self.pick(answer, "budget")
        self.assertEqual((data["agent"], self.roles()), ("budget", []))  # LLM 없이 넘어온다
        self.assertEqual(asked["budgetAdjustment"]["phase"], "over_entry")
        self.assertIn("교통·차량 초과 항목으로 새로 시작할까요?", asked["text"])
        self.assertEqual([c["label"] for c in asked["choices"]][:2], ["기존 조정 이어가기", "초과 항목으로 새 조정"])
        self.assertEqual(Budget.objects.get(pk=proposal_id).status, "validated")  # 기존 조정안은 그대로

        data, started = self.pick(asked, "over_restart")
        self.assertEqual((data["agent"], self.roles()), ("budget", []))
        self.assertIn("교통·차량 예산을 30,000원 넘었어요", started["text"])
        self.assertEqual(started["budgetAdjustment"]["phase"], "funding_scope")
        # 새 상담은 서버 작업이 아직 없어도 예산 조정이 계속 맡는다 — 이전 조정안은 취소로 기록 (PR #111 리뷰)
        store = engine.ConversationStore.load(Conversation.objects.get(pk=self.cid).slots)
        self.assertEqual(store.get_task(engine.KEY), {"task_ref": None, "phase": "started", "suspended": False})
        self.assertEqual((store.get_budget_result(engine.KEY) or {}).get("outcome"), "canceled")
        self.assertNotEqual(Budget.objects.get(pk=proposal_id).status, "validated")  # 이전 조정안은 더 이상 승인 대기가 아니다
        # 재원 선택 클릭이 라우터 해석(LLM)으로 빠지지 않고 예산 조정의 다음 질문으로 이어진다
        source = next(choice["value"] for choice in started["choices"] if "여윳돈" in choice["label"])
        data, asked = self.pick(started, source)
        self.assertEqual((data["agent"], self.roles()), ("budget", []))
        self.assertEqual(asked["budgetAdjustment"]["status"], "awaiting_input")
        self.assertIn("얼마", asked["text"])

    def test_pointing_question_uses_the_category_under_adjustment(self):
        self.proposal()
        data, answer = self.say("이건 얼마 남았어?")  # "이거" = 늘리려던 식비
        self.assertEqual(data["agent"], "spending")
        self.assertIn("식비", answer["text"])
        self.assertIn("식비 예산은 400,000원", answer["text"])  # 조회는 그대로 답한다
        # 늘리려는 항목 자체는 재원이 될 수 없다 — [식비에서 가져오기]를 제안하지 않는다 (누르면 예산 조정이 거절하던 버튼, PR #111 리뷰)
        self.assertNotIn("take_from", [choice["value"] for choice in answer["choices"]])
        data, other = self.say("교통은 얼마 남았어?")  # 다른 항목은 그대로 재원으로 제안한다
        self.assertIn({"value": "take_from", "label": "교통·차량에서 가져오기"}, other["choices"])

    # ---- 카드가 없을 때 · 비교 카드 ----

    def test_inquiry_during_question_keeps_the_question_and_uses_hint_as_source(self):
        _, asked = self.say("식비 5만 원 늘려줘")  # 어디서 조정할지 묻는 중 (카드 없음)
        before = self.budget_state()
        data, _ = self.say("교통은 얼마 남았어?")
        self.assertEqual(data["agent"], "spending")
        self.assertEqual(self.roles(), ["budget", "fake"])
        self.assertEqual({k: v for k, v in self.budget_state().items() if k != "llm_call_count"},
                         {k: v for k, v in before.items() if k != "llm_call_count"})
        data, card = self.say("그럼 거기서 빼줘")
        self.assertEqual(data["agent"], "budget")
        changes = self.changes(card)
        self.assertEqual((changes["FOOD"], changes["TRANSPORT_CAR"]), (25000, -25000))  # 교통에서 가능한 만큼만
        self.assertEqual(card['budgetAdjustment']['phase'], 'partial')
        self.assertFalse(card['budgetAdjustment']['canApprove'])
        self.assertEqual(set(changes), {'FOOD', 'TRANSPORT_CAR'})
        self.assertIn('partial_compare', [choice['value'] for choice in card['choices']])

    def test_not_mine_restores_the_comparison_card(self):
        from budgets.adjustment_core import financial_conversation as fc
        from chat.router_core import MemoryStateStore
        store = MemoryStateStore()
        state = {"requests": [{"category": "FOOD", "amount": 50000, "amount_mode": "by", "direction": "up"}],
                 "financial_offer": {"answer": {"plans": [{}], "recommended_index": 0}, "comparison": {}},
                 "pending": {"kind": "financial_selection", "line": "변경안을 볼 방법을 골라 주세요.",
                             "choices": [("financial_0", "1안 생활비")]}}
        store.save_agent_state("c", "budget", deepcopy(state))
        result = fc.handle({"conversation_id": "c", "text": "교통은 얼마 남았어?", "mode": "continue"},
                           store=store, complete=FakeReading(), get_context=lambda: deepcopy(self.ctx),
                           simulate=None, propose=None, task_status=None, cancel=None)
        self.assertEqual((result["status"], result["suggest"]), ("not_mine", {"to": "spending", "category": "TRANSPORT_CAR"}))
        self.assertEqual(store.get_agent_state("c", "budget"), state)  # 비교 카드 · 고르는 질문 그대로

    # ---- 기준: 바꾸기 · 바꿀 수 있는 범위는 예산 조정에 남는다 ----

    def test_range_question_stays_with_budget(self):
        self.proposal()
        data, answer = self.say("식비 얼마까지 늘릴 수 있어?")
        self.assertEqual(data["agent"], "budget")
        self.assertIn("최대", answer["text"])
        self.assertEqual(self.roles(), ['budget_feedback'])  # 표현을 해석한 뒤 서버가 가능액을 계산한다

    def test_spending_follow_ups_stay_with_spending_until_adjustment_talk(self):
        self.proposal()
        self.say("교통은 얼마 남았어?")
        data, answer = self.say("쇼핑은 얼마 남았어?")  # 소비 분석이 계속 답한다
        self.assertEqual(data["agent"], "spending")
        self.assertIn({"value": "take_from", "label": "패션·쇼핑에서 가져오기"}, answer["choices"])
        data, revised = self.say("그럼 거기서 빼줘")  # "거기" = 마지막에 본 쇼핑
        self.assertEqual(data["agent"], "budget")
        self.assertLess(self.changes(revised)["FASHION_SHOPPING"], 0)
        self.assertNotIn("TRANSPORT_CAR", self.changes(revised))


class SuggestRoutingTest(SimpleTestCase):
    """라우터 규칙만: 갈 곳(suggest)을 받으면 LLM 없이 넘기고, 넘겨 둔 조정은 묻지 않고 이어간다. 예산은 정해 둔 결과만 주는 대역"""

    def setUp(self):
        self.store, self.llm = MemoryStateStore(), RoleFakeLLM(PHASE_B_SAID)
        self.budget = FakeAgent(work_ref="T1")
        self.server = FakeServer({"T1": {"state": "awaiting_approval"}})

    def spending(self, request):
        self.spending_request = request
        result = spending_agent.handle(request, store=self.store, complete=self.llm, execute=lambda decision: None)
        if result["status"] == "replied" and result["decision"].items:  # 실행을 생략했으니 직전 조회만 직접 기록
            record_analysis(self.store, "talk", result["decision"], run_items(result["decision"].items, lambda one: {
                "evidence": "confirmed", "period": {"from": "2026-10-01", "to": "2026-10-29"}, "as_of": "t"}))
            dialog.publish(self.store, "talk")
        return result

    def turn(self, text=None, button=None):
        self.llm.calls.clear()
        return run_turn("talk", self.store, self.llm, text=text, button=button, server=self.server,
                        agents={"spending": self.spending, "budget": self.budget, "policy": None})

    def roles(self):
        return [call["role"] for call in self.llm.calls]

    def start_budget(self):
        self.turn("식비 예산 줄여줘")
        self.assertEqual(current_agent(self.store, "talk"), "budget")

    def test_suggest_skips_router_llm_and_lends_the_task(self):
        self.start_budget()
        self.budget.replies["식비 예산 얼마 남았어?"] = {"status": "not_mine", "suggest": {"to": "spending", "category": "FOOD"}}
        turn = self.turn("식비 예산 얼마 남았어?")
        self.assertEqual((turn["delivered"], self.roles()), (["budget", "spending"], ["spending"]))
        self.assertEqual(turn["decision"].notices, ("budget_task_kept",))
        self.assertEqual(self.store.get_task("talk")["lent"], {"category": "FOOD"})
        self.assertEqual(self.spending_request["context"]["suggested_category"], "FOOD")
        self.assertTrue(self.spending_request["context"]["budget_return"])

    def test_lent_task_resumes_without_asking_and_passes_the_seen_category(self):
        self.start_budget()
        self.budget.replies["식비 예산 얼마 남았어?"] = {"status": "not_mine", "suggest": {"to": "spending", "category": "FOOD"}}
        self.turn("식비 예산 얼마 남았어?")
        turn = self.turn("예산 좀 줄여줘")  # 소비 not_mine → 라우터 분류 → 넘겨 둔 조정 재개 (이어서/새로 질문 없음)
        handoff = turn["decision"].handoff
        self.assertEqual((turn["decision"].status, handoff["mode"]), ("ready", "resume"))
        self.assertEqual((handoff["hints"]["from"], handoff["hints"]["category"]), ("spending", "FOOD"))
        self.assertEqual(self.roles(), ["spending", "router"])
        self.assertNotIn("lent", self.store.get_task("talk"))
        self.assertEqual(current_agent(self.store, "talk"), "budget")

    def test_take_from_label_returns_without_llm(self):
        self.start_budget()
        self.budget.replies["식비 예산 얼마 남았어?"] = {"status": "not_mine", "suggest": {"to": "spending", "category": "FOOD"}}
        self.turn("식비 예산 얼마 남았어?")
        turn = self.turn("식비에서 가져오기")
        self.assertEqual((turn["decision"].handoff["mode"], turn["decision"].handoff["hints"]["category"]), ("resume", "FOOD"))
        self.assertEqual(self.roles(), [])

    def test_bad_suggest_falls_back_to_router_classification(self):
        self.start_budget()
        for suggest in ({"to": "budget"}, {"to": "nowhere"}, "spending", None):
            with self.subTest(suggest=suggest):
                self.budget.replies["식비 얼마 썼어?"] = {"status": "not_mine", "suggest": suggest}
                turn = self.turn("식비 얼마 썼어?")
                self.assertEqual((turn["delivered"], self.roles()), (["budget", "spending"], ["router", "spending"]))
                self.assertNotIn("lent", self.store.get_task("talk"))  # 기존 보류 규칙 그대로 (돌아올 때 이어서/새로 질문)
                self.store.save_task("talk", {**self.store.get_task("talk"), "suspended": False})

    def test_suspended_without_suggest_still_asks_resume_or_restart(self):
        self.start_budget()
        self.budget.replies["식비 얼마 썼어?"] = {"status": "not_mine"}
        self.turn("식비 얼마 썼어?")
        turn = self.turn("교통비 예산 늘려줘")
        self.assertEqual((turn["decision"].status, turn["decision"].asking), ("ask", "budget_task_choice"))

    def test_spending_declining_a_suggested_handoff_restores_the_budget_turn(self):
        self.start_budget()
        self.budget.replies["교통비 예산 늘려줘"] = {"status": "not_mine", "suggest": {"to": "spending", "category": None}}
        turn = self.turn("교통비 예산 늘려줘")  # 소비도 자기 일이 아니라고 함 → 목적지 확인 질문, 조정은 담당 그대로
        self.assertEqual((turn["decision"].status, turn["decision"].asking), ("ask", "intent"))
        task = self.store.get_task("talk")
        self.assertEqual((task["suspended"], task.get("lent")), (False, None))


class OverSuggestRoutingTest(SimpleTestCase):
    """라우터 규칙만 (11/11): 소비 분석 초과 답변의 [예산 조정하기] → 예산 조정에 초과 hints. 두 에이전트 모두 정해 둔 결과만 주는 대역"""
    OVER = {"category": "FOOD", "over_amount": 12000}
    HINTS = {"reason": "over", "categories": ["FOOD"], "over_amount": 12000}
    OVER_ANSWER = {"status": "replied", "over": OVER, "reply": {"lines": ["식비 예산을 넘었어요."], "suggest": ["budget_adjust"]}}
    PLAIN_ANSWER = {"status": "replied", "reply": {"lines": ["이번 달 소비예요."]}}

    def setUp(self):
        self.store, self.llm = MemoryStateStore(), RoleFakeLLM(PHASE_B_SAID)
        self.spending = FakeAgent({None: self.OVER_ANSWER})  # 소비 분석 버튼 → 초과 답변
        self.budget = FakeAgent(work_ref="T1")
        self.server = FakeServer({"T1": {"state": "awaiting_approval"}})

    def turn(self, text=None, button=None, **options):
        return run_turn("talk", self.store, self.llm, text=text, button=button, server=self.server,
                        agents={"spending": self.spending, "budget": self.budget, "policy": None}, **options)

    def lend(self):
        """조정 중 조회 질문 → 예산 조정이 소비 분석으로 넘김(넘겨 둔 조정) → 소비 분석의 초과 답변"""
        self.turn("식비 예산 줄여줘")
        self.assertEqual(current_agent(self.store, "talk"), "budget")
        question = "식비 예산 얼마 남았어?"
        self.budget.replies[question] = {"status": "not_mine", "suggest": {"to": "spending", "category": "FOOD"}}
        self.spending.replies[question] = self.OVER_ANSWER
        self.turn(question)
        self.assertEqual(self.store.get_task("talk")["lent"], {"category": None})  # 대역 답에는 조회 항목이 없어 본 카테고리는 비어 있다

    def test_suggestion_starts_budget_with_the_over_category(self):
        self.turn(button="spending")
        self.assertEqual(self.store.get_over("talk"), self.OVER)
        turn = self.turn(button="budget", suggested=True)
        handoff = turn["decision"].handoff
        self.assertEqual((turn["decision"].status, handoff["mode"], handoff["entry"], handoff["text"]), ("ready", "start", "button", None))
        self.assertEqual(handoff["hints"], self.HINTS)
        self.assertEqual(self.budget.calls[-1]["hints"], self.HINTS)  # 예산 조정 에이전트가 받은 요청
        self.assertIsNone(self.store.get_over("talk"))
        self.assertEqual(self.llm.calls, [])  # 라우터 · 에이전트 LLM 을 부르지 않는다

    def test_feature_button_without_a_suggestion_sends_no_hints(self):
        self.turn(button="spending")
        turn = self.turn(button="budget")  # 위쪽 [예산 조정] 기능 버튼
        self.assertEqual(turn["decision"].handoff["hints"], {})

    def test_over_is_remembered_for_one_turn_only(self):
        self.turn(button="spending")
        self.spending.replies[None] = self.PLAIN_ANSWER
        self.turn(button="spending")  # 초과가 아닌 답
        self.assertIsNone(self.store.get_over("talk"))
        self.assertEqual(self.turn(button="budget", suggested=True)["decision"].handoff["hints"], {})

    def test_any_other_turn_forgets_the_over(self):
        self.turn(button="spending")
        self.turn(button="policy")
        self.assertIsNone(self.store.get_over("talk"))
        self.assertEqual(self.turn(button="budget", suggested=True)["decision"].handoff["hints"], {})

    def test_malformed_over_from_the_agent_is_dropped(self):
        for over in ({"category": "NOPE", "over_amount": 1}, {"category": "FOOD", "over_amount": 0},
                     {"category": "FOOD", "over_amount": "12000"}, {"category": "FOOD"}, "FOOD", None):
            with self.subTest(over=over):
                self.spending.replies[None] = {**self.OVER_ANSWER, "over": over}
                self.turn(button="spending")
                self.assertIsNone(self.store.get_over("talk"))

    def test_explicit_screen_hints_win_over_the_remembered_answer(self):
        self.turn(button="spending")
        screen = {"reason": "over", "categories": ["BEAUTY"], "over_amount": 5000}
        self.assertEqual(self.turn(button="budget", hints=screen, suggested=True)["decision"].handoff["hints"], screen)

    def test_lent_task_resumes_with_over_hints_only(self):
        """넘겨 둔 조정이 있어도 묻지 않고 재개하되, 조회 복귀용 {"from": "spending"} 모양과 섞지 않는다"""
        self.lend()
        turn = self.turn(button="budget", suggested=True)
        handoff = turn["decision"].handoff
        self.assertEqual((turn["decision"].status, handoff["mode"], handoff["hints"]), ("ready", "resume", self.HINTS))
        task = self.store.get_task("talk")
        self.assertEqual((task["suspended"], task.get("lent")), (False, None))
        self.assertEqual(current_agent(self.store, "talk"), "budget")

    def test_lent_task_without_the_suggestion_keeps_the_seen_category_hint(self):
        self.lend()
        handoff = self.turn(button="budget")["decision"].handoff  # 기능 버튼: 기존 규칙 그대로
        self.assertEqual((handoff["mode"], handoff["hints"]), ("resume", {"from": "spending"}))

    def test_suspended_task_resumes_instead_of_the_router_question(self):
        """보류한 조정이 있을 때도 라우터는 이어서/새로를 묻지 않는다 — 예산 조정 에이전트가 한 번만 묻는다"""
        self.turn("식비 예산 줄여줘")
        question = "식비 얼마 썼어?"
        self.budget.replies[question] = {"status": "not_mine"}
        self.spending.replies[question] = self.OVER_ANSWER
        self.turn(question)
        task = self.store.get_task("talk")
        self.assertEqual((task["suspended"], task.get("lent")), (True, None))
        turn = self.turn(button="budget", suggested=True)
        self.assertEqual((turn["decision"].status, turn["decision"].handoff["mode"], turn["decision"].handoff["hints"]),
                         ("ready", "resume", self.HINTS))

    def test_failed_budget_start_keeps_the_over_for_a_retry(self):
        self.turn(button="spending")
        self.budget.replies[None] = {"status": "failed", "error": "call_failed"}
        self.turn(button="budget", suggested=True)
        self.assertEqual(self.store.get_over("talk"), self.OVER)
        self.assertIsNone(self.store.get_task("talk"))
        del self.budget.replies[None]
        self.assertEqual(self.turn(button="budget", suggested=True)["decision"].handoff["hints"], self.HINTS)


class NewConsultationTaskTest(SimpleTestCase):
    """예산 조정이 이전 작업을 끝내고 서버 작업이 없는 새 상담을 시작한 결과(new_work)의 라우터 기록 (PR #111 리뷰)"""
    OLD = {"task_ref": "T1", "phase": "awaiting_approval", "suspended": False}
    NEW = {"status": "awaiting_input", "work_ref": None, "new_work": True}

    def record(self, state, result=None):
        from chat.router_core import _budget_result
        store = MemoryStateStore()
        store.save_task("talk", dict(self.OLD))
        server = FakeServer({"T1": state} if state else {})
        return _budget_result(store, "talk", result or self.NEW, server), store

    def test_keeps_a_task_without_reference_once_the_old_one_is_confirmed_ended(self):
        outcome, store = self.record({"state": "canceled"})
        self.assertEqual(outcome, "in_progress")
        self.assertEqual(store.get_task("talk"), {"task_ref": None, "phase": "started", "suspended": False})
        self.assertEqual(current_agent(store, "talk"), "budget")
        self.assertEqual(store.get_budget_result("talk")["outcome"], "canceled")

    def test_changes_nothing_while_the_old_task_is_active_or_unconfirmed(self):
        for state, expected in (({"state": "awaiting_approval"}, "previous_task_active"), (None, "unknown")):
            with self.subTest(state=state):
                outcome, store = self.record(state)
                self.assertEqual(outcome, expected)
                self.assertEqual(store.get_task("talk"), self.OLD)
                self.assertIsNone(store.get_budget_result("talk"))

    def test_ordinary_results_still_go_through_the_server_check(self):
        outcome, store = self.record({"state": "canceled"}, {"status": "awaiting_input", "work_ref": None})
        self.assertEqual(outcome, "canceled")  # new_work 가 아니면 기존 규칙 그대로 — 끝난 작업을 해제한다
        self.assertIsNone(store.get_task("talk"))

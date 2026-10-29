"""메인 라우터 · 에이전트 연결 테스트 (단계 B: 담당 우선 · 역할별 해석 · not_mine 재분류 · 전달 2회 제한 · 버튼 · 실패 복구)
실행: backend 폴더에서 python manage.py test chat --settings=spending_fixture.settings

LLM 은 RoleFakeLLM(통합 형식 가짜 추출값 → 부른 역할 형식). 소비는 실제 소비 에이전트(분석 실행만 생략).
예산 · 청년 정책 에이전트는 연결 계약만 확인하는 대역 — 정해 둔 문장(버튼이면 None)에 정해 둔 결과만 돌려준다
(자기 분야 판별 · 조정 · 추천 내용은 흉내 내지 않는다).
청년 정책 분류는 가짜 분류 결과로만 확인한다 — 실제 자연어 분류는 검증하지 않았다.
"""
from functools import partial

from django.test import SimpleTestCase

from chat.interpretation.legacy import SAID, RoleFakeLLM
from spending_analysis import agent as spending_agent
from spending_analysis import dialog
from spending_analysis.dialog import record_analysis, run_items

from .router_core import MemoryStateStore, current_question, route, route_button, run_turn

PHASE_B_SAID = {
    **SAID,
    "3개월": {"intents": [], "followup": "answer", "months": 3},
    "교통비 예산 늘려줘": {"intents": ["budget_adjust"], "direction": "up", "categories": ["TRANSPORT_CAR"], "followup": "new"},
    "청년 정책 추천해줘": {"intents": ["policy"]},  # 가짜 분류 결과 (라우터 형식으로만 쓰인다)
    "잘 모르겠어": {"intents": ["unclear"]},
    "예산을 5만 원 줄여줘": {"intents": ["budget_adjust"], "direction": "down", "amount": 50000, "amount_mode": "by",
                       "followup": "new"},
}


class FakeAgent:
    """외부 에이전트 대역: 문장(버튼이면 None)별로 정해 둔 결과. 정해 두지 않은 입력은 awaiting_input + work_ref"""

    def __init__(self, replies=None, work_ref=None):
        self.replies, self.work_ref, self.calls = dict(replies or {}), work_ref, []

    def __call__(self, request):
        self.calls.append(request)
        result = self.replies.get(request["text"], {"status": "awaiting_input", "work_ref": self.work_ref})
        if isinstance(result, Exception):
            raise result
        return result


class FakeServer:
    def __init__(self, states=None):
        self.states = dict(states or {})

    def task_status(self, task_ref):
        return {"task_ref": task_ref, "checked_at": "2026-10-29T12:00:00+09:00", **self.states[task_ref]}


class AgentRoutingTest(SimpleTestCase):
    def setUp(self):
        self.store, self.llm = MemoryStateStore(), RoleFakeLLM(PHASE_B_SAID)
        self.budget, self.policy = FakeAgent(work_ref="T1"), FakeAgent(work_ref="P1")
        self.server = FakeServer({"T1": {"state": "awaiting_approval"}})
        self.spending_fail = False

    def spending(self, request):
        if self.spending_fail:
            return {"status": "failed", "error": "analysis_failed"}
        return spending_agent.handle(request, store=self.store, complete=self.using, execute=lambda decision: None)

    def done(self, turn, talk="talk-1"):
        """가짜 분석 기록 (실행을 생략했으므로 직접) + 소비 요약 다시 공개"""
        decision = turn["result"]["decision"]
        record_analysis(self.store, talk, decision, run_items(decision.items, lambda one: {
            "evidence": "confirmed", "period": {"from": "2026-10-01", "to": "2026-10-29"}, "as_of": "t"}))
        dialog.publish(self.store, talk)

    def turn(self, text=None, button=None, talk="talk-1", llm=None, **kw):
        llm = self.using = llm or self.llm
        agents = {"spending": self.spending, "budget": self.budget, "policy": self.policy}
        return run_turn(talk, self.store, llm, text=text, button=button, server=self.server, agents=agents, **kw)

    def roles(self):
        return [c["role"] for c in self.llm.calls]

    def state(self, talk="talk-1"):
        return self.store.export(talk)

    # ---- 담당 우선 · 소비 후속 질문 ----

    def test_follow_up_after_completed_analysis_goes_to_spending_only(self):
        self.done(self.turn("식비 얼마 썼어?"))
        self.assertEqual(self.roles(), ["router", "spending"])  # 담당 없음: 라우터 분류 → 소비가 자기 프롬프트로 해석
        turn = self.turn("그럼 교통비는?")  # 분석이 끝나도 담당 · 직전 조건은 남는다 → 소비만 해석
        self.assertEqual(turn["delivered"], ["spending"])
        self.assertEqual(turn["result"]["detail"]["items"], [{"type": 2, "category": "TRANSPORT_CAR"}])
        self.assertEqual(self.roles(), ["router", "spending", "spending"])

    def test_unclear_reference_spending_question_asks_scope_not_destination(self):
        """가리키는 대상만 불분명한 소비 질문: 소비가 맡아(mine=true · refers_to=unclear) 범위를 되묻는다 — not_mine 왕복 없음"""
        llm = RoleFakeLLM({**PHASE_B_SAID, "그쪽은 얼마 나갔어?": {"intents": ["spending"], "target": "spend", "refers_to": "unclear"}})
        turn = self.turn("그쪽은 얼마 나갔어?", llm=llm)
        decision = turn["result"]["decision"]
        self.assertEqual((turn["delivered"], decision.asking, decision.reason), (["spending"], "scope", "reference_unclear"))
        self.assertEqual([c["role"] for c in llm.calls], ["router", "spending"])

    def test_spending_receives_raw_text_and_minimal_context(self):
        self.turn("식비 얼마 썼어?")
        request = self.llm.calls[-1]
        self.assertEqual((request["text"], request["pending"], request["chosen"]), ("식비 얼마 썼어?", None, False))
        self.assertNotIn("budget_task", request)  # 소비 프롬프트에는 예산 작업 맥락을 보내지 않는다

    # ---- not_mine · 재분류 · 왕복 제한 ----

    def test_switch_during_question_keeps_spending_question(self):
        self.turn("최근 0개월 식비 알려줘")
        asked = current_question(self.store, "talk-1")
        self.assertEqual(asked["asking"], "months")
        turn = self.turn("식비 예산 줄여줘")  # 소비가 먼저 받고 not_mine → 라우터 재분류 → 예산 시작
        self.assertEqual((turn["delivered"], turn["decision"].handoff["mode"], turn["decision"].declined),
                         (["spending", "budget"], "start", ("spending",)))
        self.assertEqual(self.roles()[-2:], ["spending", "router"])  # 전환에는 라우터 분류 호출이 하나 더 든다
        self.assertEqual(self.store.get_agent_state("talk-1", "spending")["pending"], asked)  # 보류만, 삭제 안 함
        self.assertIsNone(current_question(self.store, "talk-1"))  # 지금 답할 질문은 아님 (담당이 예산)
        turn = self.turn(button="spending")  # 다시 소비로: 시작 안내, 보류된 질문은 그대로
        self.assertEqual((turn["result"]["status"], turn["result"]["reply"]["lines"], turn["decision"].notices),
                         ("awaiting_input", ["어떤 소비가 궁금하세요?"], ("budget_task_suspended",)))
        self.assertEqual(current_question(self.store, "talk-1"), asked)
        turn = self.turn("3개월")  # [임시 · 미정] 보류했던 개월 수 질문의 답으로 이어진다 (되묻기 규칙 그대로)
        self.assertEqual(turn["result"]["detail"]["items"],
                         [{"type": 9, "category": "FOOD", "months_requested": 3, "months_applied": 3}])

    def test_budget_request_during_spending_question_is_rerouted(self):
        self.turn("최근 0개월 식비 알려줘")
        asked = current_question(self.store, "talk-1")
        turn = self.turn("예산을 5만 원 줄여줘")  # 소비 되묻기 중이어도 명확한 다른 분야 요청 → not_mine → 재분류
        self.assertEqual((turn["delivered"], turn["decision"].handoff["mode"], turn["decision"].handoff["hints"]),
                         (["spending", "budget"], "start", {"direction": "down", "amount": 50000, "amount_mode": "by"}))
        self.assertEqual(self.store.get_agent_state("talk-1", "spending")["pending"], asked)  # 소비 질문은 보류만

    def test_own_domain_unsupported_is_not_sent_elsewhere(self):
        self.turn("식비 얼마 썼어?")
        calls = len(self.llm.calls)
        turn = self.turn("지난달 얼마 썼어?")
        self.assertEqual((turn["delivered"], turn["result"]["status"], turn["result"]["reason"]),
                         (["spending"], "unsupported", "last_month_only"))
        self.assertEqual((self.budget.calls, self.roles()[calls:]), ([], ["spending"]))

    def test_budget_in_progress_gets_raw_text_first(self):
        self.turn("식비 예산 줄여줘")
        calls = len(self.llm.calls)
        turn = self.turn("응")  # 예산 진행 중: 라우터 LLM 없이 원문 그대로 예산 에이전트에
        request = self.budget.calls[-1]
        self.assertEqual((request["mode"], request["text"], request["work_ref"], len(self.llm.calls)),
                         ("continue", "응", "T1", calls))
        self.assertEqual(turn["delivered"], ["budget"])

    def test_budget_not_mine_then_reroute(self):
        self.turn("식비 예산 줄여줘")
        calls = len(self.llm.calls)
        self.budget.replies["식비 얼마 썼어?"] = {"status": "not_mine"}
        turn = self.turn("식비 얼마 썼어?")
        self.assertEqual((turn["delivered"], turn["result"]["detail"]["items"], turn["decision"].notices),
                         (["budget", "spending"], [{"type": 2, "category": "FOOD"}], ("budget_task_suspended",)))
        self.assertEqual(self.roles()[calls:], ["router", "spending"])  # 재분류 1 + 소비 해석 1
        self.assertEqual(self.llm.calls[calls]["context"]["declined_by"], "budget")
        self.assertTrue(self.store.get_task("talk-1")["suspended"])

    def test_second_not_mine_turns_into_destination_question(self):
        self.turn("최근 0개월 식비 알려줘")
        before = self.state()
        self.budget.replies["식비 예산 줄여줘"] = {"status": "not_mine"}  # 새 담당도 거절
        turn = self.turn("식비 예산 줄여줘")
        self.assertEqual((turn["delivered"], turn["decision"].asking, turn["decision"].reason),
                         (["spending", "budget"], "intent", "destination_unclear"))
        after = self.state()
        question = after.pop(("question", "talk-1"))
        self.assertEqual((question["asking"], after), ("intent", before))  # 전환 전 상태 그대로 + 확인 질문만
        del self.budget.replies["식비 예산 줄여줘"]
        turn = self.turn("예산 조정")  # 확인 질문의 답은 라우터가 먼저 처리 (새 입력이라 다시 보낼 수 있다)
        self.assertEqual(turn["decision"].handoff["mode"], "start")

    def test_router_classified_agent_declining_asks_without_reclassifying(self):
        self.llm.said = {**PHASE_B_SAID, "애매한 말": {"intents": ["spending"]}}  # 라우터는 소비, 소비 해석은 not_mine
        original = self.llm.__class__.__call__

        def disagree(llm, system, user):
            text = original(llm, system, user)
            return '{"mine": false}' if llm.calls[-1]["role"] == "spending" else text
        self.llm.__class__ = type("Disagree", (RoleFakeLLM,), {"__call__": disagree})
        turn = self.turn("애매한 말")
        self.assertEqual((turn["delivered"], turn["decision"].reason), (["spending"], "destination_unclear"))
        self.assertEqual(self.roles(), ["router", "spending"])  # 라우터가 정한 곳이 거절 → 같은 분류를 다시 부르지 않는다

    def test_declined_agent_is_not_asked_again(self):
        self.turn("식비 예산 줄여줘")
        self.budget.replies["교통비 예산 늘려줘"] = {"status": "not_mine"}  # 거절했는데 라우터도 예산이라고 봄
        before = self.state()
        turn = self.turn("교통비 예산 늘려줘")
        self.assertEqual((turn["delivered"], turn["decision"].reason), (["budget"], "destination_unclear"))
        self.assertEqual(len(self.budget.calls), 2)  # 시작 1 + 이번 1 — 같은 입력을 다시 보내지 않았다
        after = self.state()
        after.pop(("question", "talk-1"))
        self.assertEqual(after, before)

    def test_router_question_is_answered_first(self):
        self.turn("식비 얼마 썼어?")  # 소비가 담당
        turn = self.turn("소비 분석하고 예산도 줄여줘")  # 소비가 not_mine → 라우터: 복합 → 무엇부터 질문
        self.assertEqual((turn["decision"].asking, turn["delivered"]), ("intent", ["spending"]))
        calls = len(self.llm.calls)
        turn = self.turn("소비 분석")  # 소비 담당이어도 라우터 질문의 답으로 먼저 처리
        self.assertEqual((turn["delivered"], self.roles()[calls:]), (["spending"], ["router", "spending"]))
        self.assertTrue(self.llm.calls[-1]["chosen"])  # 소비는 원래 문장에서 소비 부분만 해석
        self.assertEqual(turn["result"]["detail"]["items"], [{"type": 5, "category": None}])

    # ---- 실패 · 상태 보존 ----

    def test_classification_failure_keeps_state(self):
        self.turn("식비 예산 줄여줘")
        self.budget.replies["식비 얼마 썼어?"] = {"status": "not_mine"}
        before = self.state()
        turn = self.turn("식비 얼마 썼어?", llm=RoleFakeLLM(broken=True))
        self.assertEqual((turn["decision"].status, self.state()), ("failed", before))

    def test_agent_failure_is_not_not_mine_and_restores(self):
        self.turn("식비 얼마 썼어?")
        before = self.state()
        for reply in ({"status": "failed", "error": "x"}, RuntimeError("down"), {"status": "weird"}):
            with self.subTest(reply=reply):
                self.budget.replies["식비 예산 줄여줘"] = reply
                turn = self.turn("식비 예산 줄여줘")  # 새 담당 시작 실패 → 예산 작업이 생긴 것으로 남지 않음
                self.assertEqual((turn["result"]["status"], turn["delivered"]), ("failed", ["spending", "budget"]))
                self.assertEqual(self.state(), before)

    def test_spending_failure_restores_suspended_budget(self):
        self.turn("식비 예산 줄여줘")
        before = self.state()
        self.budget.replies["식비 얼마 썼어?"] = {"status": "not_mine"}
        self.spending_fail = True
        turn = self.turn("식비 얼마 썼어?")
        self.assertEqual((turn["result"]["status"], self.state()), ("failed", before))
        self.assertFalse(self.store.get_task("talk-1")["suspended"])

    def test_spending_read_failure_keeps_question(self):
        self.turn("최근 0개월 식비 알려줘")
        before = self.state()
        turn = self.turn("2개월", llm=RoleFakeLLM(broken=True))
        self.assertEqual((turn["result"]["status"], self.state()), ("failed", before))

    def test_budget_save_and_restart_rules_kept(self):
        self.turn("식비 예산 줄여줘")
        self.server.states["T1"] = {"state": "saved"}  # saved_ref 없으면 저장 성공으로 보지 않는다
        self.turn("응")
        self.assertIsNone(self.store.get_budget_result("talk-1"))
        self.budget.replies["식비 얼마 썼어?"] = {"status": "not_mine"}
        self.server.states["T1"] = {"state": "awaiting_approval"}
        self.turn("식비 얼마 썼어?")
        self.assertEqual(self.turn("교통비 예산 늘려줘")["decision"].asking, "budget_task_choice")
        self.server.states["T1"] = {"state": "canceled"}  # 에이전트가 취소를 서버에 반영했다
        self.budget.replies["교통비 예산 늘려줘"] = {"status": "awaiting_input", "work_ref": "T2"}  # 보관한 새 요청의 start
        self.server.states["T2"] = {"state": "proposing"}
        turn = self.turn("새로 할래")  # 취소 먼저 → 서버 canceled 확인 → 보관한 새 요청 start 한 번
        modes = [c["mode"] for c in self.budget.calls[-2:]]
        self.assertEqual((turn["decision"].handoff["mode"], modes), ("cancel", ["cancel", "start"]))
        self.assertEqual(self.store.get_budget_result("talk-1")["outcome"], "canceled")
        self.assertEqual(self.store.get_task("talk-1")["task_ref"], "T2")

    def test_budget_context_is_spending_summary_only(self):
        self.done(self.turn("식비 얼마 썼어?"))
        turn = self.turn("식비 예산 줄여줘")
        shared = turn["decision"].handoff["context"]["shared"]["spending"]
        self.assertEqual(set(shared), {"has_question", "previous", "items"})  # 소비가 공개한 요약만 (내부 칸 · 원문 없음)
        self.assertEqual(shared["previous"]["categories"], ["FOOD"])

    # ---- 버튼 ----

    def test_buttons_skip_router_llm(self):
        turn = self.turn(button="spending")
        self.assertEqual((turn["result"]["status"], turn["result"]["reply"]["lines"]), ("awaiting_input", ["어떤 소비가 궁금하세요?"]))
        turn = self.turn(button="budget")
        self.assertEqual((turn["decision"].handoff["mode"], turn["decision"].handoff["entry"], turn["decision"].handoff["text"]),
                         ("start", "button", None))
        self.assertEqual(self.llm.calls, [])  # 라우터 분류 호출 0 (에이전트 내부 호출 수는 각 에이전트 몫)
        self.assertEqual(self.store.get_task("talk-1")["task_ref"], "T1")  # 시작만, 승인 · 변경 없음
        self.assertEqual(self.turn(button="budget")["delivered"], ["budget"])  # 진행 중 담당이면 그 에이전트에 전달

    def test_budget_button_with_suspended_task_asks_like_text(self):
        self.turn("식비 예산 줄여줘")
        self.turn(button="spending")
        self.assertTrue(self.store.get_task("talk-1")["suspended"])
        turn = self.turn(button="budget")
        self.assertEqual((turn["decision"].asking, turn["delivered"]), ("budget_task_choice", []))
        self.assertEqual(self.store.get_task("talk-1")["task_ref"], "T1")  # 덮어쓰지 않음

    def test_button_during_router_question(self):
        self.turn("소비 분석하고 예산도 줄여줘")
        turn = self.turn(button="budget")  # 의도 선택 질문의 선택지와 같은 버튼 → 답
        self.assertEqual(turn["decision"].handoff["mode"], "start")
        self.turn(button="spending")
        self.turn("교통비 예산 늘려줘")  # 보류 작업 + 새 요청 → 라우터 질문
        turn = self.turn(button="policy")  # 그 질문의 답이 아닌 버튼 → 같은 질문 다시 (덮어쓰지 않음)
        self.assertEqual((turn["decision"].asking, turn["delivered"]), ("budget_task_choice", []))

    # ---- 청년 정책 (가짜 분류 결과 + 대역) ----

    def test_policy_from_fake_classification_and_button(self):
        turn = self.turn("청년 정책 추천해줘", input_id="in-1")  # 가짜 분류 결과 intents=["policy"]
        request = self.policy.calls[-1]
        self.assertEqual({k: request[k] for k in ("agent", "mode", "entry", "text", "input_id", "conversation_id")},
                         {"agent": "policy", "mode": "start", "entry": "text", "text": "청년 정책 추천해줘",
                          "input_id": "in-1", "conversation_id": "talk-1"})
        self.assertEqual((turn["delivered"], self.store.get_work("talk-1", "policy")),
                         (["policy"], {"work_ref": "P1", "suspended": False}))
        self.policy.replies["취소"] = {"status": "ended", "ended_reason": "canceled"}
        turn = self.turn("취소")  # 취소의 뜻은 정책 에이전트가 정한다 (라우터는 그대로 전달)
        self.assertEqual((self.policy.calls[-1]["mode"], self.policy.calls[-1]["text"], turn["result"]["status"]),
                         ("continue", "취소", "ended"))
        self.assertIsNone(self.store.get_work("talk-1", "policy"))
        self.turn(button="policy")
        self.assertEqual(self.policy.calls[-1]["entry"], "button")

    def test_policy_switch_keeps_work_and_undecided_restart_changes_nothing(self):
        self.turn(button="policy")
        self.policy.replies["식비 얼마 썼어?"] = {"status": "not_mine"}
        turn = self.turn("식비 얼마 썼어?")
        self.assertEqual((turn["delivered"], turn["decision"].notices), (["policy", "spending"], ("policy_work_suspended",)))
        self.assertEqual(self.store.get_work("talk-1", "policy"), {"work_ref": "P1", "suspended": True})
        before = self.state()
        for entry in ({"button": "policy"}, {"text": "청년 정책 추천해줘"}):
            with self.subTest(entry=entry):
                turn = self.turn(**entry)  # [미정] 보류된 정책 작업이 있을 때 다시 시작 — 정하지 않고 그대로
                self.assertEqual((turn["decision"].reason, self.policy.calls[-1]["text"]),
                                 ("policy_resume_undecided", "식비 얼마 썼어?"))
                after = self.state()
                after.pop(("active", "talk-1"), None), before.pop(("active", "talk-1"), None)
                self.assertEqual(after, before)

    # ---- input_id · 대화 분리 ----

    def test_input_id_given_or_generated(self):
        self.assertEqual(self.turn(button="budget", input_id="fixed")["input_id"], "fixed")
        self.assertEqual(self.budget.calls[-1]["input_id"], "fixed")
        generated = self.turn("응")["input_id"]
        self.assertEqual((len(generated), self.budget.calls[-1]["input_id"]), (32, generated))

    def test_conversations_are_separate(self):
        self.turn("식비 예산 줄여줘", talk="a")
        self.turn("최근 0개월 식비 알려줘", talk="b")
        self.assertEqual((self.store.get_task("b"), current_question(self.store, "a")), (None, None))
        self.assertEqual(current_question(self.store, "b")["asking"], "months")
        self.assertEqual(self.store.get_task("a")["task_ref"], "T1")

    def test_route_alone_returns_delivery_without_llm(self):
        """route() 만 쓰면 담당이 있을 때 전달 결정으로 끝난다 (판단 · 재분류는 run_turn · 에이전트 몫)"""
        self.turn("식비 예산 줄여줘")
        calls = len(self.llm.calls)
        decision = route("식비 얼마 썼어?", "talk-1", self.store, self.llm, self.server)
        self.assertEqual((decision.agent, decision.handoff["mode"], len(self.llm.calls)), ("budget", "continue", calls))
        self.assertEqual(route_button("spending", "talk-1", self.store).handoff["entry"], "button")

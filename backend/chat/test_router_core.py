"""메인 라우터 테스트 (분야 분류 · 라우터 질문 · 전환 · 예산 작업 보류 · 재개 · 취소 · 서버 확인)
실행: backend 폴더에서 python manage.py test chat --settings=spending_fixture.settings

단계 B: 소비 규칙 테스트는 spending_analysis/test_dialog.py 로 옮겼다. 여기는 라우터 · 전환 테스트만 남긴다.
기대값은 그대로이고 부르는 곳만 바뀌었다:
- self.say = run_turn(라우터 → 담당 에이전트) 결과를 분리 전 Decision 칸으로 본다(legacy.view)
- LLM 은 RoleFakeLLM: 통합 형식 가짜 추출값(legacy.SAID)을 부른 역할(라우터 · 소비) 형식으로 바꿔 답한다
- 소비는 실제 소비 에이전트(handle), 분석 실행만 가짜(확정 결과로 기록). 예산 조정은 연결 계약만 확인하는 대역:
  정해 둔 문장에는 not_mine, 그 밖에는 None(전달까지만 — 결과 기록은 테스트가 record_budget_task 로 직접)
- [계약 변경] 예산 전달의 소비 맥락: last_analysis → shared.spending (소비가 공개한 요약)
"""
from functools import partial

from django.test import SimpleTestCase

from chat.interpretation.legacy import RoleFakeLLM, item, view
from spending_analysis import agent as spending_agent
from spending_analysis import dialog
from spending_analysis.dialog import record_analysis, run_items

from .router_core import MemoryStateStore, current_question, pop_queued_start, record_budget_task, run_turn

PERIOD = {"from": "2026-10-01", "to": "2026-10-29"}
LEGACY_HANDOFF = ("task", "text", "hints", "mode", "context")
LEGACY_CONTEXT = ("conversation_id", "selected_from", "shared", "active_task", "task_state", "cancel_for")


def legacy(handoff):
    """분리 전 예산 전달 칸만 (공통 계약 칸 agent · entry · work_ref · switched_from · declined_by 는 따로 확인)"""
    out = {k: handoff[k] for k in LEGACY_HANDOFF if k in handoff}
    out["context"] = {k: v for k, v in handoff["context"].items() if k in LEGACY_CONTEXT}
    return out


def router_calls(llm):
    return [c for c in llm.calls if c["role"] == "router"]


def fake_execute(decision):
    """분석 실행 생략 (라우터 테스트는 소비 결정까지만). 분석 기록이 필요한 테스트는 Talk.done 으로 직접 남긴다"""
    return None


class FakeServer:
    """읽기 전용 서버 어댑터 대역. states[task_ref] = 서버 확인값, broken = 확인 실패"""

    def __init__(self, states=None, broken=False, current_approved=True):
        self.states, self.broken, self.calls = dict(states or {}), broken, []
        self.current_approved = current_approved  # 현재 적용 중인 승인 예산 존재(A) — 라우터는 이걸로 B 를 판단하지 않는다

    def task_status(self, task_ref):
        self.calls.append(task_ref)
        if self.broken:
            raise TimeoutError("서버 확인 실패")
        state = self.states[task_ref]
        return {"task_ref": task_ref, "checked_at": "2026-10-29T12:00:00+09:00", **state}


class Talk:
    """run_turn 으로 한 턴씩: 소비 = 실제 소비 에이전트, 예산 = 정해 둔 문장만 not_mine 인 대역 (그 밖에는 전달까지만)"""
    BUDGET_DECLINES = {"일단 이번 달 소비부터 보여줘"}

    def say(self, text, talk="talk-1", llm=None, server=None):
        llm = llm or self.llm
        agents = {"spending": partial(spending_agent.handle, store=self.store, complete=llm, execute=fake_execute),
                  "budget": lambda request: {"status": "not_mine"} if request["text"] in self.BUDGET_DECLINES else None}
        return view(run_turn(talk, self.store, llm, text=text, agents=agents,
                             server=server or getattr(self, "server", None)))

    def done(self, decision, talk="talk-1"):
        """가짜 분석 기록 (분리 전 테스트와 같음) + 소비 요약 다시 공개 (실제로는 소비 에이전트가 실행 뒤 한다)"""
        record_analysis(self.store, talk, decision, run_items(decision.items, lambda one: {
            "evidence": "confirmed", "period": PERIOD, "as_of": "2026-10-29T10:00:00+09:00", "facts": {"F1": 1}}))
        dialog.publish(self.store, talk)

    def ask_then(self, first, then):
        self.done(self.say(first))
        return self.say(then)


class RouterCoreTest(Talk, SimpleTestCase):
    def setUp(self):
        self.store, self.llm = MemoryStateStore(), RoleFakeLLM()

    def test_out_of_scope(self):
        decision = self.say("날씨 어때?")
        self.assertEqual((decision.status, decision.intent, decision.reason), ("unsupported", "unsupported", "out_of_scope"))

    def test_budget_adjust_handoff(self):
        decision = self.say("식비 예산 줄여줘")
        self.assertEqual((decision.status, decision.intent), ("ready", "budget_adjust"))
        self.assertEqual(legacy(decision.handoff), {
            "task": "budget_adjust", "text": "식비 예산 줄여줘",
            "mode": "start", "context": {"conversation_id": "talk-1", "selected_from": None,
                                         "shared": {"spending": None},  # [계약 변경] last_analysis → 소비 요약
                                         "active_task": None},
            "hints": {"categories": ["FOOD"], "direction": "down"},  # 말하지 않은 금액은 만들지 않음 (칸 생략)
        })
        # 단계 A 공통 계약 칸 (기존 칸과 따로 확인)
        self.assertEqual({k: decision.handoff[k] for k in ("agent", "entry", "work_ref")}, {"agent": "budget", "entry": "text",
                                                                                            "work_ref": None})
        self.assertEqual({k: decision.handoff["context"][k] for k in ("switched_from", "declined_by")},
                         {"switched_from": None, "declined_by": None})

    def test_budget_amount_hints_keep_what_was_said(self):
        # 금액 · by/to 는 보조값으로만 넘긴다. 조정 후 금액을 계산하거나 나누지 않는다
        # 대화마다 새 요청 (같은 대화에서 두 번째 예산 요청은 진행 중 작업에 대한 답으로 간다)
        by = self.say("식비 예산을 5만 원 줄여줘", talk="a").handoff["hints"]
        to = self.say("식비 예산을 5만 원으로 줄여줘", talk="b").handoff["hints"]
        self.assertEqual(by, {"categories": ["FOOD"], "direction": "down", "amount": 50000, "amount_mode": "by"})
        self.assertEqual(to, {"categories": ["FOOD"], "direction": "down", "amount": 50000, "amount_mode": "to"})

    def test_budget_adjust_is_never_asked_or_blocked_by_router(self):
        # by/to 불명확 · 모르는 카테고리 · UNCLASSIFIED · 여러 카테고리 — 되묻지 않고 원문 그대로 전달, 대기 상태 없음
        cases = {
            "식비 예산 5만 원 조정해줘": {"categories": ["FOOD"], "direction": "down", "amount": 50000},
            "데이트비 예산 5만 원 줄여줘": {"categories": [{"unknown": "데이트비"}], "direction": "down", "amount": 50000},
            "확인 필요 예산 5만 원만큼 줄여줘": {"categories": ["UNCLASSIFIED"], "direction": "down", "amount": 50000,
                                    "amount_mode": "by"},
            "식비랑 확인 필요 예산 5만 원만큼 줄여줘": {"categories": ["FOOD", "UNCLASSIFIED"], "direction": "down",
                                        "amount": 50000, "amount_mode": "by"},  # 카테고리를 빼거나 금액을 나누지 않음
            "기타 예산 줄여줘": {"categories": ["ETC"], "direction": "down"},
        }
        for text, hints in cases.items():
            with self.subTest(text):
                decision = self.say(text, talk=text)  # 사례마다 새 대화
                self.assertEqual((decision.status, decision.intent, decision.asking, decision.skipped),
                                 ("ready", "budget_adjust", None, ()))
                self.assertEqual((decision.handoff["mode"], decision.handoff["text"], decision.handoff["hints"]),
                                 ("start", text, hints))
                self.assertIsNone(current_question(self.store, text))

    def test_bad_hints_are_dropped_for_budget_adjust_only(self):
        for text in ["예산 조정 금액 형식 틀림", "예산 조정 카테고리 형식 틀림", "금액 없이 by/to"]:
            with self.subTest(text):
                decision = self.say(text, talk=text)  # 의도는 유효 → 보조값만 통째로 비우고 원문 전달
                self.assertEqual((decision.status, decision.handoff["text"], decision.handoff["hints"]), ("ready", text, {}))
        # 소비 분석이 섞이거나 소비 분석이면 같은 형식 오류도 실패 (검증 완화 안 함)
        self.assertEqual(self.say("복합 의도 카테고리 형식 틀림").status, "failed")
        self.assertEqual(self.say("모르는 카테고리 코드").status, "failed")

    def test_a_mixed_intent_then_choice(self):
        decision = self.say("소비 분석하고 예산도 줄여줘")
        self.assertEqual((decision.status, decision.intent, decision.asking, decision.options),
                         ("ask", None, "intent", ("spending", "budget_adjust")))
        self.assertEqual(current_question(self.store, "talk-1")["text"], "소비 분석하고 예산도 줄여줘")
        self.assertEqual(router_calls(self.llm)[-1]["pending"], None)

        decision = self.say("소비 분석")
        self.assertEqual(router_calls(self.llm)[-1]["pending"], {"asking": "intent", "options": ["spending", "budget_adjust"]})
        self.assertEqual((decision.status, decision.intent, decision.items, decision.handoff),
                         ("ready", "spending", (item(5),), None))
        self.assertIsNone(current_question(self.store, "talk-1"))

    def test_a2_mixed_intent_then_budget_choice(self):
        self.say("소비 분석하고 예산도 줄여줘")
        decision = self.say("예산 조정")
        self.assertEqual((decision.status, decision.intent, decision.items), ("ready", "budget_adjust", ()))
        self.assertEqual(legacy(decision.handoff), {  # 원문 전체 + 무엇을 골랐는지 (소비 분석은 실행하지 않음)
            "task": "budget_adjust", "text": "소비 분석하고 예산도 줄여줘", "hints": {"direction": "down"},
            "mode": "start", "context": {"conversation_id": "talk-1", "selected_from": ["spending", "budget_adjust"],
                                         "active_task": None,
                        "shared": {"spending": None}},  # [계약 변경] last_analysis → 소비 요약
        })
        self.assertIsNone(current_question(self.store, "talk-1"))

    def test_c_unclear_answer_keeps_state(self):
        self.say("소비 분석하고 예산도 줄여줘")
        saved = current_question(self.store, "talk-1")
        decision = self.say("응")
        self.assertEqual((decision.status, decision.asking), ("ask", "intent"))
        self.assertEqual(current_question(self.store, "talk-1"), saved)

    def test_d_cancel_clears_state(self):
        self.say("소비 분석하고 예산도 줄여줘")
        calls = len(self.llm.calls)
        self.assertEqual(self.say("취소").status, "cancelled")
        self.assertIsNone(current_question(self.store, "talk-1"))
        self.assertEqual(len(self.llm.calls), calls)  # 취소 말은 LLM 을 부르지 않는다

    def test_e_new_request_switches(self):
        self.say("소비 분석하고 예산도 줄여줘")
        decision = self.say("이제 됐고 예산 얼마 남았어?")
        self.assertEqual(decision.items, (item(4),))
        self.assertIsNone(current_question(self.store, "talk-1"))

    def test_conversations_do_not_mix(self):
        self.say("소비 분석하고 예산도 줄여줘", talk="talk-1")
        self.say("최근 0개월 식비 알려줘", talk="talk-2")
        self.assertEqual(current_question(self.store, "talk-1")["asking"], "intent")
        self.assertEqual(current_question(self.store, "talk-2")["asking"], "months")
        self.assertEqual(self.say("2개월", talk="talk-1").status, "ask")  # talk-1 은 개월 수를 묻지 않았다
        self.assertEqual(self.say("2개월", talk="talk-2").items[0]["months_applied"], 2)
        self.assertEqual(current_question(self.store, "talk-1")["asking"], "intent")

    def test_amount_mode_answer_alone_fails_for_router(self):
        # 라우터 해석: by/to 만 있는 답은 형식 오류 → 실패, 대기 상태 없음 (소비 되묻기 중 경우는 spending_analysis/test_dialog.py)
        self.assertEqual(self.say("5만 원만큼").status, "failed")
        self.assertIsNone(current_question(self.store, "talk-1"))

    def test_budget_adjust_after_analysis_is_handed_off_without_question(self):
        decision = self.ask_then("최근 2개월 식비 어때?", "예산 좀 줄여줘")
        self.assertEqual((decision.status, decision.intent, decision.asking), ("ready", "budget_adjust", None))
        # [계약 변경] 소비 맥락은 소비가 공개한 요약(shared.spending)으로만 넘긴다 (분리 전 last_analysis.request)
        self.assertEqual(decision.handoff["context"]["shared"]["spending"]["previous"]["categories"], ["FOOD"])
        self.assertEqual(decision.handoff["text"], "예산 좀 줄여줘")


class BudgetTaskTest(Talk, SimpleTestCase):
    """소비 분석 ↔ 예산 조정 에이전트 전환. 에이전트 · 서버는 대역"""

    def setUp(self):
        self.store, self.llm = MemoryStateStore(), RoleFakeLLM()
        self.server = FakeServer({"T1": {"state": "awaiting_approval"}})

    def agent(self, result, talk="talk-1", server=None):
        """가짜 예산 조정 에이전트가 답한 뒤 통합 계층이 부르는 기록"""
        return record_budget_task(self.store, talk, result, server or self.server)

    def start_task(self, talk="talk-1"):
        """조정 시작 → 에이전트가 서버 작업 T1 을 만들어 승인 대기"""
        self.say("식비 예산 줄여줘", talk=talk)
        self.assertEqual(self.agent({"task_ref": "T1", "content": "식비를 5만 원 줄이는 안이에요. 승인할까요?"}, talk=talk),
                         "in_progress")

    def test_spending_to_budget_start(self):
        self.done(self.say("최근 2개월 식비 어때?"))
        decision = self.say("식비 예산 줄여줘")
        self.assertEqual((decision.handoff["mode"], decision.handoff["context"]["active_task"]), ("start", None))
        self.assertIsNotNone(decision.handoff["context"]["shared"]["spending"])  # [계약 변경] last_analysis → 소비 요약
        self.assertEqual(self.store.get_task("talk-1"), {"task_ref": None, "phase": "started", "suspended": False})

    def test_short_answers_continue_to_agent_without_approval(self):
        self.start_task()
        for text in ["응", "그렇게 해줘"]:
            with self.subTest(text):
                decision = self.say(text)
                self.assertEqual((decision.status, decision.handoff["mode"], decision.handoff["text"]),
                                 ("ready", "continue", text))
                self.assertEqual(decision.handoff["context"]["active_task"], {"task_ref": "T1", "phase": "awaiting_approval"})
                self.assertEqual(self.store.get_task("talk-1")["phase"], "awaiting_approval")  # 라우터가 승인으로 바꾸지 않음
        self.assertIsNone(self.store.get_budget_result("talk-1"))

    def test_switch_to_spending_suspends_once(self):
        self.start_task()
        calls = len(self.server.calls)
        decision = self.say("일단 이번 달 소비부터 보여줘")
        self.assertEqual((decision.status, decision.items, decision.notices), ("ready", (item(5),), ("budget_task_suspended",)))
        self.assertEqual(self.store.get_task("talk-1"),
                         {"task_ref": "T1", "phase": "awaiting_approval", "suspended": True})  # 승인 · 삭제 없음
        self.assertEqual(self.say("최근 2개월 식비 어때?").notices, ())  # 보류 안내는 한 번만
        self.assertEqual(len(self.server.calls), calls)  # 전환에는 서버 확인도 필요 없음

    def test_switch_while_saving_is_not_treated_as_stopped(self):
        self.server.states["T1"] = {"state": "saving"}
        self.start_task()
        decision = self.say("일단 이번 달 소비부터 보여줘")
        self.assertEqual(decision.notices, ("budget_task_saving",))
        self.assertEqual(self.store.get_task("talk-1")["phase"], "saving")
        self.server.states["T1"] = {"state": "saved", "saved_ref": "B7v3"}  # 저장 결과는 별도 확인
        self.assertEqual(self.agent({}), "saved")
        self.assertEqual(self.store.get_budget_result("talk-1")["saved_ref"], "B7v3")

    def test_resume_checks_server_read_only(self):
        self.start_task()
        self.say("일단 이번 달 소비부터 보여줘")
        decision = self.say("아까 예산 조정 이어서 하자")
        self.assertEqual((decision.handoff["mode"], decision.handoff["context"]["task_state"]), ("resume", "awaiting_approval"))
        self.assertEqual(self.server.calls, ["T1", "T1"])  # 시작 기록 1 + 재개 확인 1 (승인 API 는 없음)
        self.assertFalse(self.store.get_task("talk-1")["suspended"])

    def test_resume_of_expired_task_records_result_and_hands_off(self):
        self.start_task()
        self.say("일단 이번 달 소비부터 보여줘")
        self.server.states["T1"] = {"state": "expired"}
        decision = self.say("아까 예산 조정 이어서 하자")
        self.assertEqual((decision.handoff["mode"], decision.handoff["context"]["task_state"]), ("resume", "expired"))
        self.assertIsNone(self.store.get_task("talk-1"))  # 안내는 에이전트가
        self.assertEqual(self.store.get_budget_result("talk-1")["outcome"], "expired")

    def test_resume_check_failure_keeps_state(self):
        self.start_task()
        self.say("일단 이번 달 소비부터 보여줘")
        task = self.store.get_task("talk-1")
        decision = self.say("아까 예산 조정 이어서 하자", server=FakeServer(broken=True))
        self.assertEqual((decision.status, decision.reason), ("failed", "task_check_failed"))
        self.assertEqual((self.store.get_task("talk-1"), self.store.get_budget_result("talk-1")), (task, None))

    def restart(self):
        """보류된 T1 이 있는데 새 조정 요청 → 새로 시작 선택"""
        self.start_task()
        self.say("일단 이번 달 소비부터 보여줘")
        decision = self.say("교통비 예산도 늘려줘")  # 재개인지 새 조정인지 모름 → 자동 resume 안 함
        self.assertEqual((decision.status, decision.asking, decision.options), ("ask", "budget_task_choice", ("resume", "restart")))
        self.assertEqual(self.store.get_task("talk-1")["task_ref"], "T1")  # 덮어쓰지 않음
        return self.say("새로 할래")

    def test_restart_sends_cancel_first_and_holds_new_request(self):
        decision = self.restart()
        self.assertEqual((decision.handoff["mode"], decision.handoff["text"], decision.handoff["context"]["cancel_for"]),
                         ("cancel", "새로 할래", "restart"))  # 취소 전달만, 새 start 없음
        self.assertEqual(decision.handoff["context"]["active_task"], {"task_ref": "T1", "phase": "awaiting_approval"})
        task = self.store.get_task("talk-1")
        self.assertEqual((task["task_ref"], task["next_start"]["text"]), ("T1", "교통비 예산도 늘려줘"))
        self.assertIsNone(current_question(self.store, "talk-1"))
        self.assertIsNone(pop_queued_start(self.store, "talk-1"))

    def test_restart_waits_while_cancel_pending_or_unchecked(self):
        self.restart()
        task = self.store.get_task("talk-1")
        self.assertEqual(self.agent({"content": "취소하고 있어요"}), "in_progress")  # 서버는 아직 승인 대기
        self.assertEqual(self.agent({}, server=FakeServer(broken=True)), "unknown")
        self.assertEqual((self.store.get_task("talk-1"), self.store.get_budget_result("talk-1")), (task, None))
        self.assertIsNone(pop_queued_start(self.store, "talk-1"))
        # 에이전트가 먼저 새 작업 T2 를 만들었다고 해도 T1 이 진행 중이면 바꾸지 않는다
        self.server.states["T2"] = {"state": "proposing"}
        self.assertEqual(self.agent({"task_ref": "T2"}), "previous_task_active")
        self.assertEqual(self.store.get_task("talk-1"), task)

    def test_restart_starts_once_after_cancel_confirmed(self):
        self.restart()
        self.server.states["T1"] = {"state": "canceled"}
        self.assertEqual(self.agent({"content": "취소했어요"}), "canceled")
        self.assertEqual(self.store.get_budget_result("talk-1")["outcome"], "canceled")
        decision = pop_queued_start(self.store, "talk-1")
        self.assertEqual((decision.handoff["mode"], decision.handoff["text"], decision.handoff["hints"]),
                         ("start", "교통비 예산도 늘려줘", {"categories": ["TRANSPORT_CAR"], "direction": "up"}))
        self.assertIsNone(decision.handoff["context"]["active_task"])
        self.assertEqual(self.store.get_task("talk-1"), {"task_ref": None, "phase": "started", "suspended": False})
        # 같은 완료 결과를 다시 받아도 새 작업을 건드리지 않고 start 도 다시 나오지 않는다
        self.assertIsNone(pop_queued_start(self.store, "talk-1"))
        self.assertEqual(self.agent({"task_ref": "T1"}), "canceled")
        self.assertEqual(self.store.get_task("talk-1"), {"task_ref": None, "phase": "started", "suspended": False})
        self.assertIsNone(pop_queued_start(self.store, "talk-1"))

    def test_restart_while_saving_keeps_real_outcome(self):
        self.server.states["T1"] = {"state": "saving"}
        self.restart()
        self.assertEqual(self.agent({}), "in_progress")  # 취소 요청만으로 저장이 멈췄다고 보지 않는다
        self.assertEqual(self.store.get_task("talk-1")["phase"], "saving")
        self.server.states["T1"] = {"state": "saved", "saved_ref": "B7v3"}
        self.assertEqual(self.agent({}), "saved")  # 취소로 바꿔 기록하지 않는다
        result = self.store.get_budget_result("talk-1")
        self.assertEqual((result["outcome"], result["unstarted_request"]["text"]), ("saved", "교통비 예산도 늘려줘"))
        self.assertIsNone(pop_queued_start(self.store, "talk-1"))  # 새 요청은 시작하지 않고 보관 (정책 미정)
        self.assertIsNone(self.store.get_task("talk-1"))

    def test_restart_without_server_task_waits_for_agent_end(self):
        self.say("식비 예산 줄여줘")  # 서버 작업 없이 에이전트가 묻는 중
        self.say("일단 이번 달 소비부터 보여줘")
        self.say("교통비 예산도 늘려줘")
        self.assertEqual(self.say("새로 할래").handoff["mode"], "cancel")
        self.assertIsNone(pop_queued_start(self.store, "talk-1"))
        self.assertEqual(self.agent({"ended": True}), "ended_without_task")
        self.assertIsNone(self.store.get_budget_result("talk-1"))  # 서버 작업 취소가 아니므로 결과 기록 없음
        self.assertEqual(pop_queued_start(self.store, "talk-1").handoff["mode"], "start")
        self.assertIsNone(pop_queued_start(self.store, "talk-1"))

    def test_resume_choice_check_failure_keeps_everything(self):
        self.done(self.say("최근 2개월 식비 어때?"))
        self.start_task()
        self.say("일단 이번 달 소비부터 보여줘")
        self.say("교통비 예산도 늘려줘")
        state = lambda: (current_question(self.store, "talk-1"), self.store.get_task("talk-1"),
                         self.store.get_budget_result("talk-1"), self.store.get_analysis("talk-1"))
        before = state()
        decision = self.say("이어서", server=FakeServer(broken=True))
        self.assertEqual((decision.status, decision.reason), ("failed", "task_check_failed"))
        self.assertEqual(state(), before)
        self.assertEqual(before[0]["asking"], "budget_task_choice")
        self.assertEqual(self.say("이어서").handoff["mode"], "resume")  # 서버가 돌아오면 같은 질문에 이어서 답할 수 있다
        self.assertIsNone(current_question(self.store, "talk-1"))

    def test_resume_request_check_failure_keeps_pending_question(self):
        self.start_task()
        self.say("일단 이번 달 소비부터 보여줘")
        self.say("최근 0개월 식비 알려줘")  # 라우터 질문 대기 중
        before = (current_question(self.store, "talk-1"), self.store.get_task("talk-1"), self.store.get_budget_result("talk-1"))
        resume = {"intents": ["budget_adjust"], "resume": True, "followup": "new"}
        llm = RoleFakeLLM(said={"아까 예산 조정 이어서 하자": resume})  # 역할별 형식으로 바꿔 답하는 대역
        self.assertEqual(self.say("아까 예산 조정 이어서 하자", llm=llm, server=FakeServer(broken=True)).status, "failed")
        self.assertEqual((current_question(self.store, "talk-1"), self.store.get_task("talk-1"), self.store.get_budget_result("talk-1")),
                         before)

    def test_resume_choice_from_question(self):
        self.start_task()
        self.say("일단 이번 달 소비부터 보여줘")
        self.say("교통비 예산도 늘려줘")
        self.assertEqual(self.say("이어서").handoff["mode"], "resume")

    def test_cancel_is_forwarded_not_recorded(self):
        self.start_task()
        decision = self.say("취소")
        self.assertEqual((decision.status, decision.handoff["mode"], decision.handoff["text"]), ("ready", "continue", "취소"))
        self.assertEqual(self.store.get_task("talk-1")["task_ref"], "T1")  # 라우터가 취소됐다고 기록하지 않음
        self.assertEqual(self.agent({"content": "취소했어요"}), "in_progress")  # 서버가 아직 진행 중이면 그대로
        self.server.states["T1"] = {"state": "canceled"}
        self.assertEqual(self.agent({"content": "취소했어요"}), "canceled")
        self.assertIsNone(self.store.get_task("talk-1"))

    def test_cancel_target_is_asked_when_ambiguous(self):
        self.start_task()
        self.say("일단 이번 달 소비부터 보여줘")
        self.say("최근 0개월 식비 알려줘")  # 라우터 질문(개월 수) + 보류된 조정 작업
        decision = self.say("취소")
        self.assertEqual((decision.asking, decision.options), ("cancel_target", ("question", "budget_task")))
        decision = self.say("예산 조정 취소")
        self.assertEqual((decision.handoff["mode"], decision.handoff["text"]), ("continue", "취소"))
        self.assertEqual(current_question(self.store, "talk-1")["asking"], "months")  # 라우터 질문은 그대로
        self.say("취소")
        self.assertEqual(self.say("질문 취소").reason, "cancel")
        self.assertIsNone(current_question(self.store, "talk-1"))
        self.assertEqual(self.store.get_task("talk-1")["task_ref"], "T1")

    def test_cancel_target_choice_in_cancel_wording_is_an_answer(self):
        """취소 대상을 물었을 때 "질문만 그만"처럼 취소 말투(followup=cancel)여도 선택지를 골랐으면 그 답으로 처리"""
        self.start_task()
        self.say("일단 이번 달 소비부터 보여줘")
        self.say("최근 0개월 식비 알려줘")
        self.say("취소")
        llm = RoleFakeLLM(said={"그 질문만 그만": {"intents": [], "followup": "cancel", "choice": "question"},
                                "그거 말고 다른 거": {"intents": [], "followup": "cancel", "choice": "spending"}})
        self.assertEqual(self.say("그거 말고 다른 거", llm=llm).asking, "cancel_target")  # 선택지에 없는 값 → 다시 묻는다
        self.assertEqual(self.say("그 질문만 그만", llm=llm).reason, "cancel")
        self.assertIsNone(current_question(self.store, "talk-1"))
        self.assertEqual(self.store.get_task("talk-1")["task_ref"], "T1")  # 조정 작업은 그대로

    def test_success_needs_this_tasks_saved_ref(self):
        self.start_task()
        # 현재 승인 예산이 있어도(A) 이 작업이 승인 대기면 성공 아님(B)
        self.assertTrue(self.server.current_approved)
        self.assertEqual(self.agent({"content": "예산을 바꿨어요!", "server": {"state": "saved"}}), "in_progress")
        self.assertIsNone(self.store.get_budget_result("talk-1"))
        # 서버가 saved 라고 해도 이 작업과 저장 결과를 잇는 saved_ref 가 없으면 믿지 않는다
        self.server.states["T1"] = {"state": "saved"}
        self.assertEqual(self.agent({}), "unknown")
        self.assertEqual(self.store.get_task("talk-1")["task_ref"], "T1")
        self.server.states["T1"] = {"state": "saved", "saved_ref": "B7v3"}
        self.assertEqual(self.agent({}), "saved")
        self.assertIsNone(self.store.get_task("talk-1"))
        self.assertEqual(self.store.get_budget_result("talk-1"),
                         {"outcome": "saved", "task_ref": "T1", "saved_ref": "B7v3", "checked_at": "2026-10-29T12:00:00+09:00"})

    def test_agent_ending_without_server_task(self):
        self.say("식비 예산 줄여줘")
        self.assertEqual(self.agent({"ended": True, "content": "알겠어요"}), "ended_without_task")
        self.assertEqual((self.store.get_task("talk-1"), self.store.get_budget_result("talk-1")), (None, None))

    def test_back_to_spending_after_saved_or_failed(self):
        for state, notices in [({"state": "saved", "saved_ref": "B7v3"}, ()), ({"state": "failed"}, ("budget_change_not_saved",))]:
            with self.subTest(state["state"]):
                self.store, self.server = MemoryStateStore(), FakeServer({"T1": state})
                self.say("식비 예산 줄여줘")
                self.agent({"task_ref": "T1"})
                decision = self.say("바뀐 예산으로 식비 남은 거 알려줘")
                self.assertEqual((decision.items, decision.notices), ((item(3, "FOOD"),), notices))
                self.assertEqual(decision.budget_basis["refetch_current_budget"], True)  # 최신 적용 예산은 다시 조회
                self.assertEqual(decision.budget_basis["last_change"]["outcome"], state["state"])

    def test_changed_budget_analysis_is_not_fixed_to_budget_left(self):
        decision = self.say("바뀐 예산으로 분석해줘")  # 분석 범위가 없음 → 범위만 묻는다
        self.assertEqual((decision.status, decision.asking, decision.reason), ("ask", "scope", "analysis_unclear"))
        decision = self.say("바뀐 예산으로 이번 달 소비 알려줘", talk="talk-2")  # 명시한 대로 5번, 3·4번으로 바꾸지 않음
        self.assertEqual((decision.items, decision.notices), ((item(5),), ("no_budget_change_record",)))

    def test_llm_failure_keeps_task_state(self):
        self.start_task()
        self.say("일단 이번 달 소비부터 보여줘")
        state = (self.store.get_task("talk-1"), current_question(self.store, talk := "talk-1"), self.store.get_budget_result(talk))
        for text, llm in [("아까 예산 조정 이어서 하자", RoleFakeLLM(broken=True)), ("재개 형식 틀림", None)]:
            with self.subTest(text):
                self.assertEqual(self.say(text, llm=llm).status, "failed")
                self.assertEqual((self.store.get_task(talk), current_question(self.store, talk), self.store.get_budget_result(talk)), state)
        self.assertEqual(self.agent({}, server=FakeServer(broken=True)), "unknown")  # 서버 확인 실패도 그대로
        self.assertEqual(self.store.get_task(talk), state[0])

    def test_tasks_do_not_mix_between_conversations(self):
        self.start_task(talk="talk-1")
        decision = self.say("응", talk="talk-2")  # talk-2 에는 조정 작업이 없다
        self.assertIsNone(decision.handoff)
        self.assertIsNone(self.store.get_task("talk-2"))
        self.assertEqual(self.store.get_task("talk-1")["task_ref"], "T1")

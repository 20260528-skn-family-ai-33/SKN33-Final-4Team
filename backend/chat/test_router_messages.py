"""run_turn 결과 → 안내 문구 (router_core.messages). LLM 은 RoleFakeLLM, 소비는 실제 에이전트(분석 실행만 생략),
예산 · 정책은 대역. 문구는 확정 · [임시] 문장 그대로 비교하고, 미정 정책 경로는 문구 없이 review 코드로 남는지 본다."""
from django.test import SimpleTestCase

from chat.interpretation.legacy import RoleFakeLLM
from spending_analysis import agent as spending_agent

from .router_core import MemoryStateStore, messages, run_turn
from .test_router_agents import PHASE_B_SAID, FakeAgent, FakeServer

MENU = [{"value": "spending", "label": "소비 분석"}, {"value": "budget", "label": "예산 조정"}]


class MessagesTest(SimpleTestCase):
    def setUp(self):
        self.store, self.llm = MemoryStateStore(), RoleFakeLLM(PHASE_B_SAID)
        # 담당 에이전트가 먼저 받으므로 소비 문장은 예산 · 정책 대역이 not_mine 으로 돌려보낸다
        mine_not = {t: {"status": "not_mine"} for t in ("최근 0개월 식비 알려줘", "이번 달 얼마 썼어?")}
        self.budget, self.policy = FakeAgent(mine_not, work_ref="T1"), FakeAgent(mine_not, work_ref="P1")
        self.server = FakeServer({"T1": {"state": "awaiting_approval"}})
        self.spending_fail = False

    def spending(self, request):
        if self.spending_fail:
            return {"status": "failed", "error": "analysis_failed"}
        return spending_agent.handle(request, store=self.store, complete=self.using, execute=lambda decision: None)

    def say(self, text=None, button=None, llm=None):
        self.using = llm or self.llm
        agents = {"spending": self.spending, "budget": self.budget, "policy": self.policy}
        return messages.render(run_turn("talk-1", self.store, self.using, text=text, button=button,
                                        server=self.server, agents=agents))

    def test_opening_reuses_chat_screen_text_and_menu(self):
        out = messages.opening()
        self.assertEqual((out["lines"], out["choices"]), (["안녕하세요! 무엇을 도와드릴까요?"], MENU))

    def test_spending_start_button_keeps_agent_reply(self):
        out = self.say(button="spending")
        self.assertEqual(out["lines"], ["어떤 소비가 궁금하세요?"])
        self.assertIn("이번 달 얼마 썼어?", out["examples"])

    def test_questions_are_stem_plus_reason_plus_choices(self):
        self.assertEqual(self.say("최근 0개월 식비 알려줘")["lines"], ["몇 개월을 볼까요? 최근 3개월까지 볼 수 있어요."])
        self.say("취소")
        out = self.say("커피값 얼마 썼어?")
        self.assertEqual(out["lines"], ["'커피값'을 어느 카테고리로 조회할까요?"])
        self.assertEqual(out["choices"][0], {"value": "FOOD", "label": "식비"})
        self.say("취소")
        out = self.say("최근 3개월 식비 예산 얼마나 남았어?")
        self.assertEqual(out["lines"], ["남은 예산은 현재 예산 기간으로만 볼 수 있어요.", "현재 예산 기간의 식비 남은 예산을 확인할까요?"])
        self.assertEqual(out["choices"], [{"value": True, "label": "네"}, {"value": False, "label": "아니요"}])
        self.assertEqual(self.say("취소")["lines"], ["방금 질문은 취소했어요."])
        self.assertEqual(self.say("아까 그거")["lines"],
                         ["어떤 분석을 가리키는지 확실하지 않아요.", "어떤 기간이나 카테고리를 볼지 알려 주세요."])

    def test_conflict_question_names_the_choices(self):
        out = self.say("이번 달 3개월 식비")
        self.assertEqual(out["lines"], ["말씀하신 조건이 서로 맞지 않아요.", "어느 기간의 식비를 볼까요?"])
        self.assertEqual(out["choices"], [{"value": "this_month", "label": "이번 달"}, {"value": "recent", "label": "최근 3개월"}])
        self.say("취소")
        out = self.say("전체랑 식비")
        self.assertEqual((out["lines"][1], [c["label"] for c in out["choices"]]), ("어느 범위를 볼까요?", ["전체", "식비"]))

    def test_alias_notice_once_then_reuse_with_repick_choice(self):
        self.say("커피값 얼마 썼어?")
        self.assertEqual(self.say("식비")["notices"], ["이번 대화에서는 '커피값'을 식비 전체로 조회할게요."])
        out = self.say("커피값 얼마 썼어?")
        self.assertEqual(out["notices"], ["'커피값'은 이번 대화에서 정한 대로 식비 전체로 조회했어요."])
        self.assertEqual(out["choices"], [{"value": "repick", "label": "'커피값' 카테고리 다시 고르기"}])
        self.assertEqual(self.say("'커피값' 카테고리 다시 고르기")["lines"], ["'커피값'을 어느 카테고리로 조회할까요?"])

    def test_router_questions_and_confirmed_chat_md_texts(self):
        out = self.say("소비 분석하고 예산도 줄여줘")
        self.assertEqual((out["lines"], out["choices"]), (["무엇부터 도와드릴까요?"], MENU))
        self.say("취소")
        self.assertEqual(self.say("날씨 어때?"), {"lines": ["머니쿠션은 소비 분석과 예산 조정을 도와드려요."],
                                               "choices": MENU, "notices": [], "review": []})
        self.assertEqual(self.say("잘 모르겠어"), {"lines": ["조금 더 자세히 적어 주세요."], "choices": MENU, "notices": [],
                                                 "review": []})
        self.assertEqual(self.say("식비 얼마 썼어?", llm=RoleFakeLLM(broken=True)),
                         {"lines": ["지금은 이해하지 못했어요, 아래에서 골라 주세요."], "choices": MENU, "notices": [], "review": []})

    def test_unsupported_adds_what_is_supported(self):
        self.assertEqual(self.say("지난달 얼마 썼어?")["lines"],
                         ["지난달 소비만 따로 보는 건 아직 지원하지 않아요.",
                          "지금은 이번 달 소비, 지난달 같은 기간 대비, 최근 3개월 추이를 볼 수 있어요."])

    def test_switch_notice_once_and_cancel_target_choices(self):
        self.say("식비 예산 줄여줘")  # 예산 대역: 진행 중 작업 T1
        out = self.say("최근 0개월 식비 알려줘")
        self.assertEqual(out["notices"], ["진행 중이던 예산 조정은 잠시 멈춰 뒀어요. 다시 이어갈 때 상태를 확인할게요."])
        self.assertEqual(self.say("2개월")["notices"], [])  # 보류 안내는 전환하는 답변에 한 번만
        self.say("최근 0개월 식비 알려줘")
        out = self.say("취소")
        self.assertEqual((out["lines"], out["choices"]), (["무엇을 취소할까요?"], [
            {"value": "question", "label": "방금 질문"}, {"value": "budget_task", "label": "예산 조정"}]))

    def test_agent_reply_is_passed_through_and_failure_has_generic_text(self):
        self.budget.replies["식비 예산 줄여줘"] = {"status": "awaiting_input", "work_ref": "T1",
                                              "reply": {"lines": ["예산 에이전트 문장"], "notices": []}}
        self.assertEqual(self.say("식비 예산 줄여줘")["lines"], ["예산 에이전트 문장"])  # 세부 대화는 에이전트 몫
        self.spending_fail = True
        self.assertEqual(self.say("이번 달 얼마 썼어?")["lines"], ["지금은 처리하지 못했어요. 잠시 후 다시 말씀해 주세요."])

    def test_three_kinds_of_cancel_are_worded_apart(self):
        """질문 취소 / 에이전트에 취소 요청 전달 / 서버가 확인한 취소 — 전달만으로 취소됐다고 말하지 않는다"""
        self.say("식비 예산 줄여줘")
        out = self.say("취소")  # 진행 중 예산 작업 + 질문 없음 → 원문 그대로 에이전트에 전달
        self.assertEqual(out["notices"], ["예산 조정 쪽에 취소 요청을 전달했어요. 실제로 취소됐는지는 확인한 뒤 알려드릴게요."])
        self.assertNotIn("방금 질문은 취소했어요.", out["lines"])
        turn = {"decision": run_turn("talk-1", self.store, self.llm, text="취소", agents={}, server=self.server)["decision"],
                "result": {"status": "replied", "reply": None}}
        self.assertIn("예산 조정 작업이 취소된 것을 확인했어요.", messages.render(turn, budget_outcome="canceled")["notices"])
        self.assertNotIn("예산 조정 작업이 취소된 것을 확인했어요.", messages.render(turn, budget_outcome="in_progress")["notices"])

    def test_saving_task_is_not_said_to_have_stopped(self):
        self.say("식비 예산 줄여줘")
        self.server.states["T1"] = {"state": "saving"}
        self.say("그렇게 해줘")  # 대역이 다시 답하면 서버 확인 → 저장 중
        out = self.say("이번 달 얼마 썼어?")
        self.assertEqual(out["notices"], ["예산 조정은 저장이 진행 중이에요. 결과는 확인한 뒤 알려드릴게요."])

    def test_undecided_policy_code_gets_no_text(self):
        self.say("청년 정책 추천해줘")
        out = self.say("이번 달 얼마 썼어?")
        self.assertEqual(out["notices"], ["진행 중이던 청년 정책 추천은 잠시 멈춰 뒀어요."])
        self.assertEqual(self.say(button="policy"), {"lines": [], "choices": [], "review": ["policy_resume_undecided"],
                                                     "notices": []})

    def test_every_decision_code_has_text_or_is_listed_undecided(self):
        """router_core · 소비 결정이 내는 질문 · 사유 코드 (코드 검색으로 모은 목록) — 새 코드가 생기면 여기서 걸린다"""
        asking = {"intent", "cancel_target", "budget_task_choice", "detail", "months", "category", "scope",
                  "explain_target", "budget_current", "period_choice", "category_choice"}
        unsupported = {"out_of_scope", "last_month_only", "category_compare", "no_budget_category",
                       "reference_not_available", "evidence_incomplete"}
        question_reasons = {"destination_unclear", "no_previous_analysis", "reference_unclear", "conflicting_conditions",
                            "analysis_unclear", "recent", "prev_month", "last_month"}
        notices = {"budget_task_suspended", "budget_task_saving", "policy_work_suspended", "budget_change_not_saved",
                   "no_budget_change_record"}
        self.assertLessEqual(asking, set(messages.ASK))
        self.assertLessEqual(unsupported, set(messages.UNSUPPORTED))
        self.assertLessEqual(question_reasons, set(messages.REASON))
        self.assertLessEqual(notices, set(messages.NOTICE))
        self.assertEqual(set(messages.CANCELLED), {"cancel", "declined"})
        self.assertEqual(set(messages.UNDECIDED), {"policy_resume_undecided", "policy_cancel_undecided"})

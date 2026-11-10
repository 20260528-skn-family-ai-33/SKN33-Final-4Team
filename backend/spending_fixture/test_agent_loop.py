"""소비 분석 추가 판단 1회(MEMO 31) — 실패 처리. 판단 LLM 은 정해 둔 응답을 주는 대역, 계산은 fixture DB
어떤 실패든: 재시도 · 다른 경로 없이 첫 조회 결과 그대로 + 못 확인한 요구 안내. 첫 조회를 다시 실행하지 않는다
실행: python manage.py test spending_fixture.test_agent_loop --settings=spending_fixture.settings
"""
import json
from unittest import mock

from django.test import SimpleTestCase, TestCase

from chat.llm import fake
from chat.llm.schemas import schema_for
from spending_analysis import adapter, agent_loop

from . import seed
from .test_agent_wiring import COMPARE, SPLIT, WHERE, Judge, Talk, answer, no_digits, reply_of
from .test_fixture import DESIGN, fixture_db_only

TIME_SLOT = agent_loop.UNMET["where_changed"].format(name="시간대")


def pick(tool, **args):
    return json.dumps({"action": "tool", "tool": tool, "args": args or None, "reason": "확인"})


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


@fixture_db_only
class JudgeFailureTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def said(self, persona, text, judge):
        with mock.patch.object(adapter, "run", wraps=adapter.run) as run:
            result = Talk(persona, judge=judge).say(text)
        self.assertEqual(run.call_count, 1)  # 실패해도 첫 조회는 한 번뿐 (다시 실행하지 않는다)
        self.assertLessEqual(len(judge.calls), 1)  # 판단은 최대 한 번 (재시도 없음)
        no_digits(self, judge)
        return result["reply"]

    def assert_kept(self, reply, persona="B", notice=TIME_SLOT):
        """첫 결과 카드 · 문장은 단독 비교 그대로 + 못 확인한 요구 안내 한 줄"""
        first = reply_of(persona, COMPARE)
        self.assertEqual((reply["view"], reply["lines"]), (first["view"], first["lines"]))
        self.assertEqual(reply["notices"], [*first["notices"], notice])

    def test_1_failures_keep_the_first_result(self):
        clock = Clock()
        cases = {"call_failed": Judge(TimeoutError("대기 한도")), "invalid_json": Judge("{깨진"),
                 "wrong_by": Judge(pick("where_changed", by="weekday")),  # 서버가 본 요구(시간대)와 다른 기준 — 실행하지 않는다
                 "not_allowed": Judge(pick("explain_change", ref="R1"))}
        for why, judge in cases.items():
            with self.subTest(why=why):
                reply = self.said("B", SPLIT, judge)
                self.assertEqual(reply["trace"]["agent"]["fallback"], why)
                self.assert_kept(reply)
        with mock.patch.object(agent_loop.time, "monotonic", clock):  # 응답은 왔지만 대기 한도를 넘김
            reply = self.said("B", SPLIT, Judge(clock=clock, spend=agent_loop.TIMEOUT + 1))
        self.assertEqual(reply["trace"]["agent"]["fallback"], "timeout")
        self.assert_kept(reply)

    def test_2_answer_with_a_remaining_need_is_not_done(self):
        """판단이 answer 를 골라도 서버가 본 요구(시간대로 나누기)는 남았다고 밝힌다 (끝낸 것처럼 답하지 않는다)"""
        reply = self.said("B", SPLIT, Judge(answer()))
        self.assertEqual((reply["trace"]["agent"]["status"], reply["trace"]["agent"]["unmet"]),
                         ("answered", [{"tool": "where_changed", "by": "time_slot"}]))
        self.assert_kept(reply)

    def test_3_tool_failure_says_what_was_not_checked(self):
        """도구 실패(카테고리로 좁힌 비교를 카테고리로 나누기) → 첫 결과 + 그 확인을 못 했다는 안내"""
        reply = self.said("B", WHERE, Judge(pick("where_changed", by="category")))
        trace = reply["trace"]["agent"]
        self.assertEqual((trace["fallback"], trace["tool_status"]), ("tool_unsupported", "unsupported"))
        self.assert_kept(reply, notice=agent_loop.UNMET["where_changed"].format(name="카테고리"))

    def test_4_unknown_need_failure_is_still_admitted(self):
        """multi_step 만(서버가 요구를 특정 못 함)인데 판단이 실패 → '나머지는 확인하지 못했어요'"""
        reply = self.said("B", WHERE, Judge(TimeoutError("대기 한도")))
        self.assert_kept(reply, notice=agent_loop.UNMET[None])

    def test_5_incomplete_data_skips_the_judge(self):
        """비교할 수 없는 자료(지우: 지난달 내역 부족) → 판단 · 추가 분석 없이 확인 가능한 결과 + 제한 안내"""
        judge = Judge()
        reply = self.said("C", SPLIT, judge)
        self.assertEqual(judge.calls, [])
        self.assertEqual(reply["trace"]["agent"]["allowed"], [])
        self.assert_kept(reply, persona="C")

    def test_5b_budget_first_admits_the_rest(self):
        """해석이 예산을 먼저 읽은 multi_step 질문 — 비교가 없어 판단하지 않지만, 나머지를 못 봤다고 밝힌다"""
        judge, text = Judge(), "식비 예산 남은 거랑 지난달보다 많이 쓰는지 알려줘"
        with mock.patch.dict(fake.SPENDING_SAID, {text: {"mine": True, "target": "budget_left", "categories": ["FOOD"], "multi_step": True}}):
            reply = self.said("A", text, judge)
        self.assertEqual(judge.calls, [])
        self.assertEqual(reply["notices"][-1], agent_loop.UNMET[None])
        self.assertEqual(reply["view"], reply_of("A", "식비 예산 얼마 남았어?")["view"])

    def test_6_one_call_with_the_full_timeout(self):
        judge = Judge()
        self.said("B", SPLIT, judge)
        self.assertEqual(judge.calls[0]["_timeout"], agent_loop.TIMEOUT)


class JudgeSchemaTest(SimpleTestCase):
    def test_prompt_has_schema(self):
        name, schema = schema_for(agent_loop.PROMPT)
        self.assertEqual(name, "spending_agent_judge")
        self.assertEqual(schema["properties"]["tool"]["enum"], ["where_changed", "budget_lookup", None])
        self.assertEqual(agent_loop._choice('{"action": "tool", "tool": "budget_lookup", "args": {"by": "weekday"}}',
                                            ["budget_lookup"])[:2], ("budget_lookup", {}))  # 예산 조회는 인자를 받지 않는다

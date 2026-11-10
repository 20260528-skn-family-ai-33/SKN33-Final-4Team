"""소비 분석 에이전트 루프 1일차(MEMO 29) — 도구 4개 · 루프 · 안전장치. 계획 LLM 은 정해 둔 순서대로 답하는 가짜, 계산은 fixture DB
실행: python manage.py test spending_fixture.test_agent_loop --settings=spending_fixture.settings
"""
import json
import re

from django.test import SimpleTestCase, TestCase

from chat.llm.schemas import schema_for
from spending_analysis import adapter, agent_loop, explain
from spending_analysis.answer import NAMES, compose

from . import seed
from .test_conversation import AS_OF, TODAY
from .test_fixture import DESIGN, fixture_db_only, user

QUESTION = "지난달보다 식비 왜 늘었어?"
FOOD_PREV = {"target": "spend", "compare": "prev_month", "categories": ["FOOD"]}


def tool(name, **args):
    return json.dumps({"action": "tool", "tool": name, "args": args, "reason": "필요한 조회"}, ensure_ascii=False)


ANSWER = json.dumps({"action": "answer", "tool": None, "args": None, "reason": "답할 수 있음"})


class Planner:
    """정해 둔 행동을 순서대로 돌려주는 계획 LLM 대역 — 받은 입력을 남긴다. 예외를 넣으면 그 차례에 던진다.
    루프 지시문이 아닌 호출(explain_change 안의 B)은 호출 오류로 → B 가 지금 템플릿 설명으로 답한다"""

    def __init__(self, *actions, clock=None, spend=0):
        self.actions, self.calls, self.other, self.clock, self.spend = list(actions), [], 0, clock, spend

    def __call__(self, system, user, timeout=None):
        if system != agent_loop.PROMPT:
            self.other += 1
            raise TimeoutError("B 대역 없음")
        self.calls.append({"user": json.loads(user), "timeout": timeout})
        if self.clock is not None:
            self.clock.now += self.spend
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


class LoopTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def loop(self, persona, planner, question=QUESTION, clock=None):
        out = agent_loop.run(question, user=user(persona), today=TODAY, as_of=AS_OF, complete=planner,
                             **({"clock": clock} if clock else {}))
        # 12번: LLM 이 받은 입력에는 id(R1 · R1.F1) · 순위 · 가게 순번을 빼면 숫자(금액 · 날짜)가 없다 — 질문 원문은 사용자가 쓴 말이라 뺀다
        for call in planner.calls:
            sent = json.dumps({k: v for k, v in call["user"].items() if k != "question"}, ensure_ascii=False)
            self.assertNotRegex(re.sub(r'R\d+(\.F\d+)?|"rank": \d+|가게 \d+', "", sent), r"[0-9]")
            self.assertNotIn("server", sent)
        return out


@fixture_db_only
class LoopAnswerTest(LoopTestBase):
    def test_1_compare_then_explain_stacks_cards_in_order(self):
        """민재 식비: 지난달 비교 → 이유 설명. 카드 숫자는 일반 경로(adapter.run → compose · explain.answer)와 같다"""
        planner = Planner(tool("spend_lookup", **FOOD_PREV), tool("explain_change", ref="R1"), ANSWER)
        out = self.loop("B", planner)
        self.assertEqual(out["status"], "answered")
        self.assertEqual([(s["tool"], s["status"]) for s in out["steps"]], [("spend_lookup", "ok"), ("explain_change", "ok")])
        first, second = out["results"]
        compare = compose(adapter.run(user("B"), first["server"]["decision"], today=TODAY, as_of=AS_OF))
        why = explain.answer(user("B"), first["server"]["records"], today=TODAY)
        self.assertEqual(out["reply"]["view"], compare["view"] + why["view"])
        self.assertEqual(out["reply"]["lines"][:len(compare["lines"])], compare["lines"])
        self.assertEqual(planner.other, 1)  # B 계획을 한 번 불렀고 (대역 오류) 템플릿 설명으로 답함
        # 두 번째 계획 호출은 첫 결과(방향 있음)를 받았다
        sent = planner.calls[1]["user"]["results"][0]
        self.assertEqual((sent["id"], sent["status"], sent["facts"][0]["id"], sent["facts"][0]["direction"]),
                         ("R1", "ok", "R1.F1", "up"))
        self.assertTrue(second["facts"])  # 설명 주장 (가게는 순번)

    def test_2_two_categories_have_directions(self):
        planner = Planner(tool("spend_lookup", target="spend", compare="prev_month", categories=["FOOD", "TRANSPORT_CAR"]), ANSWER)
        out = self.loop("A", planner)
        self.assertEqual(out["status"], "answered")
        changes = {(f["subject"], f["direction"], f["state"]) for f in out["results"][0]["facts"] if f["kind"] == "change"}
        self.assertEqual(changes, {(NAMES["FOOD"], "up", "confirmed"), (NAMES["TRANSPORT_CAR"], "same", "confirmed")})

    def test_3_budget_then_compare(self):
        planner = Planner(tool("budget_lookup", categories=["FOOD"]), tool("spend_lookup", **FOOD_PREV), ANSWER)
        out = self.loop("A", planner)
        self.assertEqual(out["status"], "answered")
        budget, compare = (o["server"]["reply"] for o in out["results"])
        self.assertEqual(out["reply"]["view"], budget["view"] + compare["view"])
        self.assertEqual(out["results"][0]["server"]["results"][0]["result"]["type"], 3)
        self.assertTrue(all(f["kind"] == "line" and "□" in f["preview"] for f in out["results"][0]["facts"]))

    def test_4_where_changed_by_time_slot(self):
        planner = Planner(tool("spend_lookup", **FOOD_PREV), tool("where_changed", ref="R1", by="time_slot"), ANSWER)
        out = self.loop("B", planner)
        self.assertEqual(out["status"], "answered")
        facts = out["results"][1]["facts"]
        self.assertEqual((facts[0]["axis"], facts[0]["subject"], facts[0]["rank"]), ("time_slot", "저녁", 1))
        self.assertEqual(out["reply"]["view"][-1]["kind"], "text")


@fixture_db_only
class LoopGuardTest(LoopTestBase):
    def test_5_at_most_three_tools(self):
        planner = Planner(*(tool("spend_lookup", target="spend", categories=[c])
                            for c in ("FOOD", "TRANSPORT_CAR", "HOUSEHOLD", "BEAUTY")))
        out = self.loop("A", planner)
        self.assertEqual((out["status"], len(out["steps"]), len(planner.calls)), ("answered", agent_loop.MAX_STEPS, 3))
        self.assertEqual(len(planner.actions), 1)  # 네 번째는 묻지도 실행하지도 않음

    def test_6_same_call_twice_is_duplicate(self):
        planner = Planner(tool("spend_lookup", **FOOD_PREV), tool("spend_lookup", **FOOD_PREV), ANSWER)
        out = self.loop("A", planner)
        self.assertEqual([s["status"] for s in out["steps"]], ["ok", "duplicate"])
        self.assertEqual(out["status"], "answered")

    def test_7_unavailable_month_asks(self):
        planner = Planner(tool("spend_lookup", target="spend", compare="month", compare_month=3, categories=["FOOD"]))
        out = self.loop("A", planner)
        self.assertEqual(out["status"], "ask")
        self.assertEqual(out["decision"].asking, "period_choice")
        self.assertIsNone(out["reply"])

    def test_8_unknown_arg_is_invalid_and_loop_goes_on(self):
        planner = Planner(tool("spend_lookup", cafe=True), tool("spend_lookup", target="spend", categories=["FOOD"]), ANSWER)
        out = self.loop("A", planner)
        self.assertEqual([s["status"] for s in out["steps"]], ["invalid", "ok"])
        self.assertEqual(planner.calls[1]["user"]["results"][0], {"id": "R1", "tool": "spend_lookup", "status": "invalid",
                                                                  "reason": "unknown_args"})
        self.assertEqual(out["status"], "answered")

    def test_9_explain_needs_a_comparison(self):
        planner = Planner(tool("spend_lookup", target="spend", categories=["FOOD"]), tool("explain_change", ref="R1"), ANSWER)
        out = self.loop("A", planner)
        self.assertEqual(out["steps"][1], {"tool": "explain_change", "args": {"ref": "R1"}, "status": "unsupported",
                                           "reason": "no_comparison"})
        self.assertEqual(out["status"], "answered")  # 이번 달 식비 결과만으로 답한다

    def test_10_planner_failures_fall_back(self):
        cases = {"call_failed": TimeoutError("대기 한도"), "invalid_json": "{깨진",
                 "unknown_tool": json.dumps({"action": "tool", "tool": "transfer", "args": {}, "reason": "x"})}
        for why, action in cases.items():
            with self.subTest(why=why):
                out = self.loop("A", Planner(action))
                self.assertEqual((out["status"], out["fallback"], out["steps"]), ("fallback", why, []))
        self.assertEqual(self.loop("A", Planner(ANSWER))["fallback"], "no_result")  # 결과 없이 answer

    def test_11_timeout_and_remaining_time(self):
        clock = Clock()
        planner = Planner(tool("spend_lookup", **FOOD_PREV), tool("explain_change", ref="R1"), ANSWER, clock=clock, spend=4)
        out = self.loop("A", planner, clock=clock)
        self.assertEqual([c["timeout"] for c in planner.calls], [agent_loop.TIMEOUT, agent_loop.TIMEOUT - 4, agent_loop.TIMEOUT - 8])
        self.assertEqual(out["status"], "answered")
        clock.now, planner = 0, Planner(tool("spend_lookup", **FOOD_PREV), clock=clock, spend=agent_loop.TIMEOUT + 1)
        out = self.loop("A", planner, clock=clock)
        self.assertEqual((out["status"], out["fallback"], out["steps"]), ("fallback", "timeout", []))


class SchemaTest(SimpleTestCase):
    def test_loop_prompt_has_schema(self):
        name, schema = schema_for(agent_loop.PROMPT)
        self.assertEqual(name, "spending_agent_step")
        self.assertEqual(set(schema["required"]), {"action", "tool", "args", "reason"})
        self.assertEqual(len(schema["properties"]["args"]["anyOf"]), 5)  # 도구 4개 + null

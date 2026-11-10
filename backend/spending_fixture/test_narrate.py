"""답변 조합 + 보충(11/10) — narrate(6 · 7번 지난달 비교) · supplement(보충 검사) · B 답 보충. 실제 LLM 호출 없음 (정해 둔 응답)
실행: python manage.py test spending_fixture.test_narrate --settings=spending_fixture.settings
"""
import json
import re
from functools import partial
from unittest import mock

from django.test import SimpleTestCase, TestCase

from chat.router_core import MemoryStateStore, run_turn
from spending_analysis import agent as spending_agent
from spending_analysis import claims as C
from spending_analysis import narrate, supplement

from . import seed
from .test_claims import _answer, _by, _ev
from .test_conversation import AS_OF, TODAY, FakeLLM
from .test_fixture import DESIGN, fixture_db_only, user

FOOD = "지난달보다 식비 더 썼어?"
TOTAL = "지난달보다 많이 썼어?"
NEXT = "어디에서 늘었는지 궁금하면 '왜 늘었어?'라고 물어봐 주세요."


def _said(*sentences, extra=None, reason=None):
    return json.dumps({"sentences": [{"text": t, "facts": re.findall(r"\{(F\d+)\}", t)} for t in sentences],
                       "extra": extra, "extra_reason": reason}, ensure_ascii=False)


class Narrator:
    """정해 둔 응답을 돌려주는 조합 LLM 대역 — 받은 입력을 남긴다. raise_ = 호출 오류"""

    def __init__(self, raw=None, raise_=False):
        self.raw, self.raise_, self.calls = raw, raise_, []

    def __call__(self, system, user, timeout=None):
        self.calls.append({"system": system, "user": json.loads(user), "timeout": timeout})
        if self.raise_:
            raise TimeoutError("대기 한도")
        return self.raw


def ask(persona, text, narrator=None):
    """한 질문 → reply (라우터 → 소비 분석 에이전트, 해석은 가짜)"""
    store, llm = MemoryStateStore(), FakeLLM()
    agent = partial(spending_agent.handle, store=store, complete=llm, user=user(persona), narrate_complete=narrator)
    turn = run_turn("talk-1", store, llm, text=text, today=TODAY, as_of=AS_OF, agents={"spending": agent})
    return turn["result"]["reply"]


class SupplementCheckTest(SimpleTestCase):
    """보충 검사: 숫자 · 금액 표현 · 길이 · 문장 수 · 금지 표현 · 이유 칸"""

    def test_passes(self):
        for text in (NEXT, "이번 달 식비는 원래 쓰던 흐름과 비교해 볼 수 있어요.", "원인은 거래 내역만으로 단정할 수 없어요."):
            with self.subTest(text=text):
                self.assertEqual(supplement.check(text, "next_step"), (text, []))
        self.assertEqual(supplement.check(None, None), (None, []))

    def test_rejects(self):
        cases = {
            "지난달보다 3만 원 더 썼어요.": "number",
            "지난달보다 삼만 원 더 썼어요.": "number",
            "식비가 두 배로 늘었어요.": "number",
            "12% 늘었어요.": "number",
            "{F1} 그래서 늘었어요.": "placeholder",
            "남는 돈은 투자해 보세요.": "banned",
            "배달 때문이에요.": "banned",
            "가" * (supplement.MAX_CHARS + 1): "too_long",
            "하나예요. 둘이에요. 셋이에요.": "too_many_sentences",
        }
        for text, why in cases.items():
            with self.subTest(text=text):
                out, reasons = supplement.check(text, "uncovered")
                self.assertIsNone(out)
                self.assertIn(why, reasons)
        self.assertEqual(supplement.check(NEXT, "advice"), (None, ["reason"]))
        self.assertEqual(supplement.check(None, "uncovered"), (None, ["reason_without_extra"]))


@fixture_db_only
class NarrateTest(TestCase):
    """윤서(A) 식비: 이번 달 390,000원 · 지난달 같은 기간 348,000원 · +42,000원 (05 기대값, PersonaAnswerTest 와 같은 숫자)"""

    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def test_facts_are_server_sentences_and_llm_gets_no_numbers(self):
        narrator = Narrator(_said("{F1}"))
        ask("A", FOOD, narrator)
        sent = narrator.calls[0]["user"]
        self.assertEqual(sent["question"], FOOD)
        self.assertEqual(sent["facts"], [
            {"id": "F1", "kind": "change", "subject": "식비", "direction": "up", "must": True,
             "preview": "식비 지출은 지난달보다 □원 증가했어요."},
            {"id": "F2", "kind": "this_month", "subject": "식비", "direction": None, "must": False,
             "preview": "이번 달(□월 □일~□일) 식비에 □원 썼어요."},
            {"id": "F3", "kind": "last_month", "subject": "식비", "direction": None, "must": False,
             "preview": "지난달 같은 기간(□월 □일~□일)에는 식비에 □원 썼어요."}])
        no_ids = [{k: v for k, v in f.items() if k != "id"} for f in sent["facts"]]
        self.assertNotRegex(json.dumps(no_ids, ensure_ascii=False), r"[0-9]")  # 금액 · 날짜를 보내지 않는다
        self.assertEqual(narrator.calls[0]["timeout"], narrate.TIMEOUT)

    def test_composed_answer_and_supplement_go_under_the_card(self):
        plain = ask("A", FOOD)
        reply = ask("A", FOOD, Narrator(_said("{F1}", "그리고 {F2}", extra=NEXT, reason="next_step")))
        self.assertEqual(reply["view"][:-1], plain["view"])  # 카드 숫자는 그대로
        self.assertEqual(reply["view"][-1], {"kind": "text", "lines": [
            "식비 지출은 지난달보다 42,000원 증가했어요.",
            "그리고 이번 달(10월 1일~29일) 식비에 390,000원 썼어요.", NEXT]})
        self.assertEqual((reply["lines"], reply["notices"]), (plain["lines"], plain["notices"]))  # 템플릿 답도 그대로
        trace = dict(reply["trace"]["narrate"])
        self.assertIsInstance(trace.pop("ms"), int)  # 조합 호출 시간 (개발용)
        self.assertEqual(trace, {"used": ["F1", "F2"], "rejected": [], "extra_reason": "next_step",
                                 "extra_dropped": [], "fallback": None})

    def test_total_compare_uses_total_sentences(self):
        reply = ask("A", TOTAL, Narrator(_said("{F1} {F3}", extra=NEXT, reason="next_step")))
        self.assertEqual(reply["view"][-1]["lines"], [
            "전체 소비는 지난달보다 42,000원 증가했어요. 지난달 같은 기간(9월 1일~29일) 소비는 1,468,000원이었어요.", NEXT])

    def test_bad_compositions_keep_the_current_card(self):
        plain = ask("A", FOOD)
        cases = {
            "missing_must": _said("{F2}"),  # 증감 문장(must)을 빼면 안 된다
            "free_text": _said("네, 많이 늘었어요 {F1}"),  # 연결 말 밖의 말
            "repeated": _said("{F1}", "그리고 {F1}"),
            "format": "{}",
        }
        for why, raw in cases.items():
            with self.subTest(why=why):
                reply = ask("A", FOOD, Narrator(raw))
                self.assertEqual(reply["view"], plain["view"])
                trace = reply["trace"]["narrate"]
                self.assertEqual(trace["fallback"], "rejected")
                self.assertTrue(any(why in r for r in trace["rejected"]), trace)

    def test_bad_supplement_is_dropped_alone(self):
        """여러 항목 카드엔 지난달 금액이 없다 — 지난달 금액 문장을 골랐으면 보충이 탈락해도 문장 칸은 붙는다"""
        reply = ask("A", "지난달보다 식비랑 주거비 더 썼어?", Narrator(_said("{F1}", "{F3}", "{F4}", extra="지난달보다 4만 원 넘게 더 썼어요.", reason="uncovered")))
        self.assertEqual(reply["view"][-1]["lines"], ["식비 지출은 지난달보다 42,000원 증가했어요.",
                                                      "지난달 같은 기간(9월 1일~29일)에는 식비에 348,000원 썼어요.",
                                                      "주거·통신 지출은 지난달과 같아요."])
        self.assertEqual(reply["trace"]["narrate"]["extra_dropped"], ["number"])

    def test_only_must_sentences_without_supplement_are_not_repeated(self):
        """증감 문장만 고르고 보충이 없으면(또는 보충이 탈락하면) 카드와 같은 말 — 문장 칸을 붙이지 않는다"""
        plain = ask("A", FOOD)
        # 항목 하나짜리 카드는 이번 달 · 지난달 금액도 보여 준다 — 그 문장을 같이 골라도 반복
        for raw in (_said("{F1}"), _said("{F2}", "그리고 {F1}"), _said("{F1}", "{F3}"),
                    _said("{F1}", extra="지난달보다 4만 원 넘게 더 썼어요.", reason="uncovered")):
            reply = ask("A", FOOD, Narrator(raw))
            self.assertEqual((reply["view"], reply["trace"]["narrate"]["fallback"]), (plain["view"], "same_as_card"))
        reply = ask("A", FOOD, Narrator(_said("{F1}", extra=NEXT, reason="next_step")))  # 보충이 있으면 붙인다
        self.assertEqual(reply["view"][-1]["lines"], ["식비 지출은 지난달보다 42,000원 증가했어요.", NEXT])
        multi = ask("A", "지난달보다 식비랑 주거비 더 썼어?")  # 여러 항목: 증감 문장만이면 반복, 지난달 금액을 골랐으면 새 정보
        reply = ask("A", "지난달보다 식비랑 주거비 더 썼어?", Narrator(_said("{F1}", "그리고 {F4}")))
        self.assertEqual((reply["view"], reply["trace"]["narrate"]["fallback"]), (multi["view"], "same_as_card"))

    def test_sentence_name_puts_the_filter_first(self):
        self.assertEqual(narrate.said_name({"type": 7, "category": "FOOD", "time_slots": ["evening"]}), "저녁 식비")
        self.assertEqual(narrate.said_name({"type": 7, "category": "FOOD", "weekdays": [5, 6], "time_slots": ["evening"]}), "주말 저녁 식비")
        self.assertEqual(narrate.said_name({"type": 6, "weekdays": [5, 6]}), "주말")
        self.assertEqual(narrate.said_name({"type": 7, "category": "FOOD"}), "식비")

    def test_call_failure_and_timeout_keep_the_current_card(self):
        plain = ask("A", FOOD)
        reply = ask("A", FOOD, Narrator(raise_=True))
        self.assertEqual((reply["view"], reply["trace"]["narrate"]), (plain["view"], {"fallback": "call_failed"}))
        ticks = iter([0.0, narrate.TIMEOUT + 1])
        results = [{"ok": True, "item": {}, "result": {"type": 7}}]
        with mock.patch.object(narrate, "facts", return_value=[]), mock.patch.object(narrate, "applies", return_value=True):
            same, trace = narrate.attach(plain, results, FOOD, Narrator(_said("{F1}")), clock=lambda: next(ticks))
        self.assertEqual((same, trace), (plain, {"fallback": "timeout"}))

    def test_other_analyses_and_no_llm_are_untouched(self):
        narrator = Narrator(_said("{F1}"))
        plain = ask("A", "식비 얼마 썼어?")
        self.assertEqual(ask("A", "식비 얼마 썼어?", narrator), plain)  # 6 · 7번이 아니면 부르지 않는다
        self.assertEqual(narrator.calls, [])
        self.assertNotIn("trace", ask("A", FOOD))  # 조합 LLM 이 없으면 지금 답 그대로

    def test_not_comparable_is_left_to_the_card(self):
        """지우(C)는 지난달 자료가 다 모이지 않았다 — 카드 맨 위 안내(lead)로 충분해 조합하지 않는다 (같은 안내가 두 번 보이지 않게)"""
        narrator = Narrator(_said("{F1}"))
        plain = ask("C", TOTAL)
        self.assertEqual(ask("C", TOTAL, narrator), plain)
        self.assertEqual(narrator.calls, [])
        self.assertTrue(plain["lead"][0].startswith("지난달 같은 기간의 내역이 충분하지 않아"))

    def test_label_before_placeholder_is_rejected(self):
        """두 카테고리 질문에서 '식비는 {F1}'처럼 이름을 붙이면 탈락 (11/10 실제 평가 N6) — 문장에 이름이 이미 있다"""
        reply = ask("A", "지난달보다 식비랑 주거비 더 썼어?", Narrator(_said("식비는 {F1}", "주거·통신은 {F4}")))
        self.assertEqual(reply["trace"]["narrate"]["rejected"], ["1:free_text", "2:free_text", "missing_must:F1,F4"])
        reply = ask("A", "지난달보다 식비랑 주거비 더 썼어?", Narrator(_said("{F1}", "그리고 {F4}", extra=NEXT, reason="next_step")))
        self.assertEqual(reply["view"][-1]["lines"], ["식비 지출은 지난달보다 42,000원 증가했어요.",
                                                      "그리고 주거·통신 지출은 지난달과 같아요.", NEXT])


@fixture_db_only
class ExplainPlanSupplementTest(TestCase):
    """B 답에도 같은 보충 규칙: 통과하면 서버 문장 뒤에, 숫자가 있으면 보충만 버린다"""

    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def run_b(self, extra, reason):
        item, ev = _ev("B", "FOOD")
        sid = _by(C.first_claims(ev, item), "average")["id"]

        def complete(system, user, timeout=None):
            if system == C.PLAN_PROMPT:
                return json.dumps({"queries": [], "reason": "충분하다", "hypothesis": {"axis": "average", "subject": None}})
            raw = json.loads(_answer((f"{{{sid}}}", [sid])))
            return json.dumps({**raw, "extra": extra, "extra_reason": reason}, ensure_ascii=False)
        lines, trace = C.run(ev, item, "왜 식비가 늘었어?", complete)
        return lines, trace, _by(C.first_claims(ev, item), "average")["text"]

    def test_supplement_follows_server_sentences(self):
        """가설 없는 일반 설명(서버 한계 문장 없음): 보충이 서버 문장 뒤에 붙는다"""
        item, ev = _ev("B", "FOOD")
        first = C.first_claims(ev, item)
        key = C.key_claims(first)[0]
        extra = "늘어난 결제가 이번 달에만 있었던 일인지는 다음 달 내역과 함께 보면 알 수 있어요."

        def complete(system, user, timeout=None):
            if system == C.PLAN_PROMPT:
                return json.dumps({"queries": [], "reason": "충분하다", "hypothesis": None})
            raw = json.loads(_answer((f"{{{first[0]['id']}}}", [first[0]["id"]]), (f"특히 {{{key}}}", [key])))
            return json.dumps({**raw, "extra": extra, "extra_reason": "uncovered"}, ensure_ascii=False)
        lines, trace = C.run(ev, item, "왜 식비가 늘었어?", complete)
        self.assertIsNone(trace["support"]["limitation"])
        self.assertEqual((lines[-1], trace["extra_reason"], trace["extra_dropped"]), (extra, "uncovered", []))

    def test_bad_supplement_is_dropped(self):
        lines, trace, text = self.run_b("건당 3천 원씩 올랐어요.", "next_step")
        self.assertEqual((lines, trace["extra_dropped"], trace["fallback"]), ([text], ["number"], None))

    def test_server_limitation_wins_over_llm_uncovered(self):
        """건당 평균 근거엔 서버 한계 문장이 붙는다 — 같은 말을 보충으로 되풀이하지 않는다 (11/10 실제 평가 B2)"""
        item, ev = _ev("A", "FOOD")
        sid = _by(C.first_claims(ev, item), "average")["id"]
        extra = "구매 품목이나 수량을 알 수 없어 가격이 올랐는지는 판단하기 어려워요."

        def complete(system, user, timeout=None):
            if system == C.PLAN_PROMPT:
                return json.dumps({"queries": [], "reason": "충분하다",
                                   "hypothesis": {"metric": "average", "target": "scope", "unavailable": None}})
            raw = json.loads(_answer((f"{{{sid}}}", [sid])))
            return json.dumps({**raw, "extra": extra, "extra_reason": "uncovered"}, ensure_ascii=False)
        lines, trace = C.run(ev, item, "건당 금액이 올라서야?", complete)
        self.assertTrue(trace["support"]["limitation"])
        self.assertEqual((lines, trace["extra_dropped"]), ([_by(C.first_claims(ev, item), "average")["text"]], ["server_limitation"]))

    def test_server_next_check_wins_over_llm_next_step(self):
        lines, trace, text = self.run_b(NEXT, "next_step")
        if trace["next_check"]:
            self.assertEqual((lines, trace["extra_dropped"]), ([text], ["server_next_check"]))
        else:
            self.assertEqual(lines, [text, NEXT])

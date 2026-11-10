"""지난달 비교 사실 표(narrate — 추가 판단 LLM 에 보낼 관찰 결과, MEMO 31) · supplement(B 답 보충 검사) · B 답 보충. 실제 LLM 호출 없음
실행: python manage.py test spending_fixture.test_narrate --settings=spending_fixture.settings
"""
import json
from functools import partial
from unittest import mock

from django.test import SimpleTestCase, TestCase

from chat.router_core import MemoryStateStore, run_turn
from spending_analysis import adapter
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


def results_of(persona, text):
    """한 질문(라우터 → 소비 분석, 해석은 가짜)의 조회 결과 (adapter.run 이 돌려준 것 그대로)"""
    store, llm, got, run = MemoryStateStore(), FakeLLM(), [], adapter.run
    agent = partial(spending_agent.handle, store=store, complete=llm, user=user(persona))
    with mock.patch.object(adapter, "run", side_effect=lambda *a, **k: got.append(run(*a, **k)) or got[-1]):
        run_turn("talk-1", store, llm, text=text, today=TODAY, as_of=AS_OF, agents={"spending": agent})
    return got[-1] if got else []


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
class NarrateFactsTest(TestCase):
    """윤서(A) 식비: 이번 달 390,000원 · 지난달 같은 기간 348,000원 · +42,000원 (05 기대값, PersonaAnswerTest 와 같은 숫자)"""

    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def test_facts_are_server_sentences_and_llm_gets_no_numbers(self):
        table = narrate.facts(results_of("A", FOOD))
        self.assertEqual(table[0]["text"], "식비 지출은 지난달보다 42,000원 증가했어요.")
        sent = narrate.for_llm(table)
        self.assertEqual(sent, [
            {"id": "F1", "kind": "change", "subject": "식비", "direction": "up", "must": True,
             "preview": "식비 지출은 지난달보다 □원 증가했어요."},
            {"id": "F2", "kind": "this_month", "subject": "식비", "direction": None, "must": False,
             "preview": "이번 달(□월 □일~□일) 식비에 □원 썼어요."},
            {"id": "F3", "kind": "last_month", "subject": "식비", "direction": None, "must": False,
             "preview": "지난달 같은 기간(□월 □일~□일)에는 식비에 □원 썼어요."}])
        no_ids = [{k: v for k, v in f.items() if k != "id"} for f in sent]
        self.assertNotRegex(json.dumps(no_ids, ensure_ascii=False), r"[0-9]")  # 금액 · 날짜를 보내지 않는다

    def test_total_compare_uses_total_sentences(self):
        table = narrate.facts(results_of("A", TOTAL))
        self.assertEqual([f["text"] for f in table][::2], ["전체 소비는 지난달보다 42,000원 증가했어요.",
                                                           "지난달 같은 기간(9월 1일~29일) 소비는 1,468,000원이었어요."])

    def test_only_comparable_compares_make_a_table(self):
        """6 · 7번이 아니거나 비교할 수 없으면(지우: 지난달 자료 부족) 표를 만들지 않는다 — 답 문장 한 줄씩으로"""
        self.assertTrue(narrate.applies(results_of("A", FOOD)))
        self.assertFalse(narrate.applies(results_of("A", "식비 얼마 썼어?")))
        self.assertFalse(narrate.applies(results_of("C", TOTAL)))

    def test_sentence_name_puts_the_filter_first(self):
        self.assertEqual(narrate.said_name({"type": 7, "category": "FOOD", "time_slots": ["evening"]}), "저녁 식비")
        self.assertEqual(narrate.said_name({"type": 7, "category": "FOOD", "weekdays": [5, 6], "time_slots": ["evening"]}), "주말 저녁 식비")
        self.assertEqual(narrate.said_name({"type": 6, "weekdays": [5, 6]}), "주말")
        self.assertEqual(narrate.said_name({"type": 7, "category": "FOOD"}), "식비")


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

"""달끼리 비교(11/10): "8월보다 식비 더 썼어?" · "9월이랑 8월 식비 비교해줘" — 기간 맞추기 · 달 이름 · 후속 질문 · 볼 수 없는 달
해석은 가짜(chat.llm.fake), 계산은 fixture DB 의 실제 analyze. 기준일 2026-10-29.
실행: python manage.py test spending_fixture.test_month_compare --settings=spending_fixture.settings
"""
import json
from datetime import date
from functools import partial

from django.test import SimpleTestCase, TestCase

from chat.llm.fake import FakeReading
from chat.router_core import MemoryStateStore, run_turn
from spending_analysis import agent as spending_agent
from spending_analysis import dialog
from spending_analysis.periods import Period, month_labels, month_pair
from spending_analysis.reading import SpendingReading

from . import seed
from .test_conversation import AS_OF, TODAY
from .test_fixture import DESIGN, fixture_db_only, summary, user

FOOD = ("FOOD",)


class Talk:
    def __init__(self, persona, narrator=None):
        self.store, self.user, self.narrator = MemoryStateStore(), user(persona), narrator

    def say(self, text):
        llm = FakeReading()
        agent = partial(spending_agent.handle, store=self.store, complete=llm, user=self.user, narrate_complete=self.narrator)
        turn = run_turn("talk-1", self.store, llm, text=text, today=TODAY, as_of=AS_OF, agents={"spending": agent})
        return turn["result"]


class MonthPairTest(SimpleTestCase):
    """기간 맞추기 (DB 없음)"""

    def test_in_progress_month_clips_the_other_to_the_same_day(self):
        current, previous, clipped = month_pair(date(2026, 10, 29), None, 8)
        self.assertEqual((current, previous, clipped),
                         (Period(date(2026, 10, 1), date(2026, 10, 30)), Period(date(2026, 8, 1), date(2026, 8, 30)), True))
        self.assertEqual(month_labels(date(2026, 10, 29), current, previous, clipped),
                         {"current": "이번 달", "previous": "8월", "previous_same": "8월 같은 기간"})
        # 3/31 vs 2월: 2월 말일까지
        self.assertEqual(month_pair(date(2026, 3, 31), None, 2)[1], Period(date(2026, 2, 1), date(2026, 3, 1)))

    def test_finished_months_use_whole_months_in_date_order(self):
        for pair in (month_pair(date(2026, 10, 29), 9, 8), month_pair(date(2026, 10, 29), 8, 9)):  # 말한 순서와 상관없이
            self.assertEqual(pair, (Period(date(2026, 9, 1), date(2026, 10, 1)), Period(date(2026, 8, 1), date(2026, 9, 1)), False))
        self.assertEqual(month_labels(date(2026, 10, 29), *pair), {"current": "9월", "previous": "8월", "previous_same": "8월"})

    def test_same_month_or_out_of_window_is_none(self):
        self.assertIsNone(month_pair(date(2026, 10, 29), None, 10))
        self.assertIsNone(month_pair(date(2026, 10, 29), 9, 9))
        self.assertIsNone(month_pair(date(2026, 10, 29), None, 3))  # 볼 수 있는 범위(7월~) 밖


class DecideTest(SimpleTestCase):
    """해석 → 조회 항목 (DB 없음)"""

    def decide(self, **said):
        return dialog.decide(SpendingReading(mine=True, target="spend", **said), "", today=TODAY)

    def test_items_carry_the_months(self):
        d = self.decide(compare="month", compare_month=8, categories=FOOD)
        self.assertEqual(d.items, ({"type": 7, "category": "FOOD", "compare_month": 8},))
        d = self.decide(compare="month", compare_month=9, period="month", month=8)  # 순서를 바꿔 말해도 나중 달이 보는 쪽
        self.assertEqual(d.items, ({"type": 6, "category": None, "compare_month": 8, "month": 9},))

    def test_this_month_vs_last_month_is_the_existing_compare(self):
        for said in ({"compare_month": 9}, {"compare_month": 9, "period": "month", "month": 10},
                     {"compare_month": 10, "period": "last_month"}):
            self.assertEqual(self.decide(compare="month", categories=FOOD, **said).items, ({"type": 7, "category": "FOOD"},))

    def test_said_period_does_not_trip_the_period_check(self):
        """기간 말(period_said)이 있어도 '맞춰 조회하지 못했어요'로 막지 않는다 (11/10 화면 확인: "이번달이랑 9월 식비 비교해줘")"""
        d = self.decide(compare="month", compare_month=9, period="this_month", period_said="이번달이랑 9월", categories=FOOD)
        self.assertEqual((d.status, d.items), ("ready", ({"type": 7, "category": "FOOD"},)))
        d = self.decide(compare="month", compare_month=8, period="this_month", period_said="이번달이랑 8월", categories=FOOD)
        self.assertEqual((d.status, d.items), ("ready", ({"type": 7, "category": "FOOD", "compare_month": 8},)))
        d = self.decide(compare="month", compare_month=8, period="month", month=9, period_said="9월이랑 8월")
        self.assertEqual((d.status, d.items), ("ready", ({"type": 6, "category": None, "compare_month": 8, "month": 9},)))

    def test_last_month_against_a_named_month(self):
        """지난달(9월) vs 8월 → 9월 전체 vs 8월 전체 (지난달을 이번 달로 바꾸지 않는다)"""
        d = self.decide(compare="month", compare_month=8, period="last_month", period_said="지난달이랑 8월달", categories=FOOD)
        self.assertEqual((d.status, d.items), ("ready", ({"type": 7, "category": "FOOD", "compare_month": 8, "month": 9},)))

    def test_month_period_without_a_number_means_this_month(self):
        """실제 해석 그대로(11/10 화면): "이번달이랑 9월달" → period=month · month=null · compare_month=9 — 막지 않고 이번 달로 본다"""
        d = self.decide(compare="month", compare_month=9, period="month", period_said="이번달이랑 9월달", categories=FOOD)
        self.assertEqual((d.status, d.items), ("ready", ({"type": 7, "category": "FOOD"},)))
        d = self.decide(compare="month", compare_month=8, period="month", period_said="이번달이랑 8월달", categories=FOOD)
        self.assertEqual((d.status, d.items), ("ready", ({"type": 7, "category": "FOOD", "compare_month": 8},)))

    def test_unavailable_or_meaningless_months_are_not_replaced(self):
        d = self.decide(compare="month", compare_month=3, categories=FOOD)
        self.assertEqual((d.status, d.asking, d.reason), ("ask", "period_choice", "period_unavailable"))
        self.assertEqual(self.decide(compare="month", compare_month=10).reason, "period_compare")  # 이번 달끼리
        self.assertEqual(self.decide(compare="month").reason, "period_compare")  # 달을 말하지 않음
        self.assertEqual(self.decide(compare="month", compare_month=8, measure="count").reason, "compare_query")


@fixture_db_only
class MonthCompareAnswerTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def food(self, start, end):
        return summary(user("A"), start, end)["by_category"]["FOOD"]

    def test_this_month_against_august_same_days(self):
        now, before = self.food(date(2026, 10, 1), date(2026, 10, 30)), self.food(date(2026, 8, 1), date(2026, 8, 30))
        reply = Talk("A").say("8월보다 식비 더 썼어?")["reply"]
        self.assertEqual(reply["lines"], ["이번 달(10월 1일~29일) 소비", "8월 같은 기간(8월 1일~29일)과 비교했어요."])
        head, total, rows = reply["view"]
        self.assertEqual(head["title"], "식비 · 8월 같은 기간과 비교")
        self.assertEqual((total["amount"], total["caption"]), (abs(now - before), "8월보다 더 쓴 금액" if now > before else "8월보다 덜 쓴 금액"))
        self.assertEqual([(r["name"], r["amount"], r["note"]) for r in rows["rows"]],
                         [("이번 달", now, "10월 1일~29일"), ("8월", before, "8월 1일~29일")])
        self.assertNotIn("지난달", json.dumps(reply, ensure_ascii=False))

    def test_finished_months_whole_month(self):
        """9월 360,000 · 8월 350,000 (최근 3개월 추이와 같은 숫자) → +10,000"""
        reply = Talk("A").say("9월이랑 8월 식비 비교해줘")["reply"]
        self.assertEqual(reply["lines"], ["9월(9월 1일~30일) 소비", "8월(8월 1일~31일)과 비교했어요."])
        self.assertEqual(reply["sections"], [{"title": "식비 · 360,000원", "lines": ["8월보다 10,000원 증가했어요. ↑ 2.86%"]}])
        head, total, rows = reply["view"]
        self.assertEqual((head["title"], total["caption"], total["amount"]), ("식비 · 8월과 비교", "8월보다 더 쓴 금액", 10_000))
        self.assertEqual([(r["name"], r["amount"]) for r in rows["rows"]], [("9월", 360_000), ("8월", 350_000)])

    def test_october_vs_september_is_the_same_answer_as_last_month_compare(self):
        self.assertEqual(Talk("A").say("10월이랑 9월 식비 비교해줘")["reply"], Talk("A").say("지난달보다 식비 더 썼어?")["reply"])

    def test_out_of_window_month_asks_instead_of_replacing(self):
        result = Talk("A").say("3월보다 식비 더 썼어?")
        self.assertEqual((result["status"], result["detail"]["asking"], result["detail"]["reason"]),
                         ("awaiting_input", "period_choice", "period_unavailable"))

    def test_restating_a_month_compare_while_asked_for_a_period(self):
        """기간을 되묻는 중("어느 기간을 볼까요?")에 달 비교를 다시 말하면 '이번 달' 선택지로 읽지 않고 그 비교를 한다 (11/10 화면 확인)"""
        for said, title in (("이번달이랑 9월달 식비 비교해줘", "식비 · 지난달 같은 기간과 비교"),
                            ("이번달이랑 8월달 식비 비교해줘", "식비 · 8월 같은 기간과 비교")):
            talk = Talk("A")
            self.assertEqual(talk.say("3월보다 식비 더 썼어?")["detail"]["asking"], "period_choice")
            self.assertEqual(talk.say(said)["reply"]["view"][0]["title"], title)
            self.assertEqual(Talk("A").say(said)["reply"]["view"][0]["title"], title)  # 새 대화에서도 같은 답

    def test_followups_keep_the_compared_months(self):
        talk = Talk("A")
        talk.say("9월이랑 8월 식비 비교해줘")
        reply = talk.say("그럼 교통비는?")["reply"]  # 범위만 바꾸면 같은 두 달
        self.assertEqual(reply["lines"], ["9월(9월 1일~30일) 소비", "8월(8월 1일~31일)과 비교했어요."])
        self.assertEqual(reply["view"][0]["title"], "교통·차량 · 8월과 비교")
        talk = Talk("A")
        talk.say("9월이랑 8월 식비 비교해줘")
        said = " ".join(talk.say("왜 늘었을까?")["reply"]["lines"])  # 설명도 그 두 달 이름으로
        self.assertIn("식비 지출은 8월보다 10,000원 늘었어요.", said)
        self.assertNotIn("지난달", said)

    def test_narrate_sentences_use_month_names(self):
        seen = []

        def narrator(system, user_, timeout=None):
            seen.extend(json.loads(user_)["facts"])
            return json.dumps({"sentences": [{"text": "{F1}", "facts": ["F1"]}, {"text": "{F3}", "facts": ["F3"]}],
                               "extra": "두 달 모두 한 달 전체를 비교했어요.", "extra_reason": "uncovered"}, ensure_ascii=False)
        reply = Talk("A", narrator).say("9월이랑 8월 식비 비교해줘")["reply"]
        self.assertEqual(reply["view"][-1]["lines"], ["식비 지출은 8월보다 10,000원 증가했어요.",
                                                      "8월(8월 1일~31일)에는 식비에 350,000원 썼어요.",
                                                      "두 달 모두 한 달 전체를 비교했어요."])
        self.assertEqual(seen[1]["preview"], "□월(□월 □일~□일) 식비에 □원 썼어요.")

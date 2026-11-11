"""소비 분석 추가 판단 연결(MEMO 31) — 라우터 → agent.handle → 첫 조회(기존 경로) → (필요할 때만) 판단 1회 → 추가 도구 최대 1회.
해석 · 판단 · B 는 가짜(chat.llm.fake), 계산은 fixture DB
실행: python manage.py test spending_fixture.test_agent_wiring --settings=spending_fixture.settings
"""
import json
import re
from functools import partial
from unittest import mock

from django.test import TestCase

from chat.llm import fake
from chat.llm.fake import FakeReading
from chat.router_core import MemoryStateStore, run_turn
from spending_analysis import adapter, agent_loop, claims, explain
from spending_analysis.answer import won
from spending_analysis.scope import DAY_NAMES
from spending_analysis import agent as spending_agent

from . import seed
from .test_conversation import AS_OF, TODAY
from .test_fixture import DESIGN, fixture_db_only, user

SPLIT = "지난달보다 식비 늘었는데 어느 시간대에서 많이 늘었어?"  # 비교 + 나누기 + 가설 없는 explain (실제 해석값)
WHERE = "지난달보다 식비 늘었으면 어디서 늘었는지도 봐줘"  # multi_step 만 (서버 요구 없음 — 판단이 고른다)
BUDGET = "식비 예산 얼마 남았고 지난달보다 많이 쓰고 있어?"
TWO = "식비랑 교통비 중에 지난달보다 뭐가 더 늘었어?"
WHY = "지난달보다 식비 늘었으면 왜 늘었는지도 알려줘"
COMPARE = "지난달보다 식비 더 썼어?"
EASY = ("이번 달 식비 얼마야?", COMPARE, "식비 예산 얼마 남았어?", "9월이랑 8월 식비 비교해줘")


def answer(**extra):
    return json.dumps({"action": "answer", "tool": None, "args": None, "reason": "충분", **extra})


class Judge:
    """판단 LLM 대역 — 받은 입력을 남긴다. raw 를 주면 그 응답(예외면 던짐), 없으면 가짜 판단(chat.llm.fake). clock 이면 호출마다 spend 초"""

    def __init__(self, raw=None, clock=None, spend=0):
        self.raw, self.calls, self.clock, self.spend = raw, [], clock, spend

    def __call__(self, system, user, timeout=None):
        assert system == agent_loop.PROMPT, "판단 대역에 다른 지시문"
        self.calls.append({**json.loads(user), "_timeout": timeout})
        if self.clock is not None:
            self.clock.now += self.spend
        if isinstance(self.raw, Exception):
            raise self.raw
        return self.raw if self.raw is not None else fake.fake_judge(user)


class Plan:
    """B 계획 · 답 LLM 대역 — 부른 지시문을 남긴다. fail 이면 호출 실패"""

    def __init__(self, fail=False):
        self.fake, self.fail, self.calls = FakeReading(), fail, []

    def __call__(self, system, user, timeout=None):
        assert system in (claims.PLAN_PROMPT, claims.ANSWER_PROMPT), "B 대역에 다른 지시문"
        self.calls.append(system)
        if self.fail:
            raise TimeoutError("B 대역: 실패")
        return self.fake(system, user, timeout=timeout)


class Talk:
    def __init__(self, persona, judge=None, plan=None):
        self.store, self.user, self.judge, self.plan, self.llm = MemoryStateStore(), user(persona), judge, plan, FakeReading()

    def say(self, text):
        agent = partial(spending_agent.handle, store=self.store, complete=self.llm, user=self.user,
                        agent_complete=self.judge, explain_plan=self.plan)
        return run_turn("talk-1", self.store, self.llm, text=text, today=TODAY, as_of=AS_OF, agents={"spending": agent})["result"]


def reply_of(persona, text, **kw):
    return Talk(persona, **kw).say(text)["reply"]


def no_digits(test, judge):
    """판단 LLM 이 받은 입력(질문 원문 · 대기 한도 제외)에는 숫자(금액 · 날짜 · 달)가 없다 — 결과 id · 가게 순번만"""
    for call in judge.calls:
        sent = json.dumps({k: v for k, v in call.items() if k not in ("question", "_timeout")}, ensure_ascii=False)
        test.assertNotRegex(re.sub(r"R\d+|가게 \d+", "", sent), r"[0-9]")


@fixture_db_only
class AgentWiringTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def test_1_easy_questions_do_not_call_the_judge(self):
        """단순 합계 · 단순 비교 · 남은 예산 · 달끼리 비교 → 스위치를 켜도 기존 답 그대로, 판단 0회"""
        judge = Judge()
        for text in EASY:
            with self.subTest(text=text):
                self.assertEqual(reply_of("A", text, judge=judge), reply_of("A", text))
        self.assertEqual(judge.calls, [])

    def test_2_split_runs_the_comparison_once_and_passes_the_need(self):
        """비교 + 나누기(+ 가설 없는 explain): 비교만 기존 경로로 한 번 → 판단에 비교 결과 + 남은 요구(time_slot) → 추가 도구 1회"""
        self.assertEqual(Talk("B").say(SPLIT)["reason"], "compare_query")  # 스위치 꺼짐 = 기존 미지원 안내
        judge = Judge()
        with mock.patch.object(adapter, "run", wraps=adapter.run) as run:
            result = Talk("B", judge=judge).say(SPLIT)
        self.assertEqual(run.call_count, 1)  # 첫 조회 한 번 (where_changed 는 그 기간 집계를 다시 묶는다)
        self.assertEqual([(i["type"], i["category"]) for i in run.call_args.args[1].items], [(7, "FOOD")])
        self.assertEqual(len(judge.calls), 1)
        sent = judge.calls[0]
        self.assertEqual((sent["needs"], sent["allowed"]), ([{"tool": "where_changed", "by": "time_slot"}], ["where_changed"]))
        self.assertEqual(sent["conditions"], {"target": "spend", "compare": "prev_month", "categories": ["FOOD"]})
        self.assertEqual(sent["results"][0]["status"], "ok")
        trace = result["reply"]["trace"]["agent"]
        self.assertEqual((trace["status"], trace["unmet"]), ("tool", []))
        view = result["reply"]["view"]
        self.assertEqual(view[:-2], reply_of("B", COMPARE)["view"])  # 앞 카드 = 단독 비교 카드 (숫자 그대로)
        rows, said = view[-2:]  # 나눠 본 결과 = 순위 줄(전 → 후 · 증감) + 한 줄 요약
        self.assertEqual(rows["title"], "시간대별 변화 (고정지출 제외)")
        self.assertEqual(rows["rows"][0], {"name": "저녁 시간대", "value": "70,000원 → 237,000원", "note": "+167,000원", "direction": "up"})
        self.assertEqual(said["lines"], ["저녁 시간대가 가장 많이 늘었어요. 전체 증가분의 절반 이상이에요."])
        no_digits(self, judge)

    def test_3_split_keeps_the_range_and_the_follow_up_record(self):
        """나눈 뒤 기록은 비교(나누기 뺀 요청) — '그럼 교통비는?'이 지난달 비교를 이어받는다.
        같은 대화에서 해석이 refers_to=previous 를 붙여도 비교 + 나누기를 직접 말했으면 나눈다"""
        judge = Judge()
        talk = Talk("B", judge=judge)
        talk.say(SPLIT)
        items = talk.say("그럼 교통비는?")["decision"].items
        self.assertEqual([(i["type"], i["category"]) for i in items], [(7, "TRANSPORT_CAR")])
        with mock.patch.dict(fake.SPENDING_SAID, {SPLIT: {**fake.SPENDING_SAID[SPLIT], "refers_to": "previous"}}):
            self.assertEqual(talk.say(SPLIT)["reply"]["trace"]["agent"]["status"], "tool")
        self.assertEqual(len(judge.calls), 2)

    def test_4_split_is_not_forced(self):
        """나누기 + 건수 같은 다른 계산은 나누지 않는다 (기존 미지원 안내 그대로, 판단 없음)"""
        judge, text = Judge(), "지난달보다 시간대별로 몇 번 썼어?"
        with mock.patch.dict(fake.SPENDING_SAID, {text: {
                "mine": True, "target": "spend", "compare": "prev_month", "group_by": "time_slot", "measure": "count"}}):
            self.assertEqual(Talk("A", judge=judge).say(text)["reason"], "compare_query")
        self.assertEqual(judge.calls, [])

    def test_5_multi_step_tools_inherit_the_first_result(self):
        """multi_step: 판단이 고른 도구 1회. 예산을 물었을 때만 budget_lookup 이 허용된다"""
        cases = {WHERE: ("B", ["where_changed"], "where_changed", 1), BUDGET: ("A", ["where_changed", "budget_lookup"], "budget_lookup", 2)}
        for text, (persona, allowed, tool, runs) in cases.items():
            with self.subTest(text=text):
                judge = Judge()
                with mock.patch.object(adapter, "run", wraps=adapter.run) as run:
                    result = Talk(persona, judge=judge).say(text)
                self.assertEqual((len(judge.calls), judge.calls[0]["allowed"]), (1, allowed))
                self.assertEqual(run.call_count, runs)  # 첫 조회 1 (+ 예산 1)
                trace = result["reply"]["trace"]["agent"]
                self.assertEqual((trace["choice"]["tool"], trace["status"]), (tool, "tool"))
                no_digits(self, judge)
        # 예산을 묻지 않은 질문엔 budget_lookup 이 없다 — 골라도 실행하지 않는다
        judge = Judge(json.dumps({"action": "tool", "tool": "budget_lookup", "args": None, "reason": "예산도"}))
        with mock.patch.object(adapter, "run", wraps=adapter.run) as run:
            trace = Talk("B", judge=judge).say(WHERE)["reply"]["trace"]["agent"]
        self.assertEqual((trace["fallback"], run.call_count), ("not_allowed", 1))

    def test_6_budget_lookup_does_not_replace_the_comparison_record(self):
        """추가 예산 조회는 기록하지 않는다 — 후속 질문은 첫 조회(지난달 비교)를 이어받는다. 예산 카드는 지금 예산 주기 문장 그대로"""
        talk = Talk("A", judge=Judge())
        reply = talk.say(BUDGET)["reply"]
        self.assertTrue(any("이번 예산 기간은" in line for line in reply["lines"]))
        self.assertEqual(reply["view"][:3], reply_of("A", COMPARE)["view"])
        items = talk.say("그럼 교통비는?")["decision"].items
        self.assertEqual([(i["type"], i["category"]) for i in items], [(7, "TRANSPORT_CAR")])

    def test_7_nothing_to_add_skips_the_call(self):
        """두 비교(나눌 비교를 못 고름) + 예산 안 물음 → 허용 도구가 없어 판단을 부르지 않는다"""
        judge = Judge()
        result = Talk("A", judge=judge).say(TWO)
        self.assertEqual(judge.calls, [])
        self.assertEqual(result["reply"]["trace"]["agent"]["status"], "answered")

    def test_8_ask_first_then_judge_after_the_answer(self):
        """모르는 말(데이트비)은 기존 경로가 먼저 카테고리를 되묻는다 — 판단은 답으로 조건이 확정된 뒤 (원래 요청의 multi_step)"""
        judge, text = Judge(), "지난달보다 데이트비 늘었는데 어느 시간대에서 늘었어?"
        said = {text: {"mine": True, "target": "spend", "compare": "prev_month", "categories": [{"unknown": "데이트비"}], "multi_step": True},
                "식비로 볼래": {"mine": True, "followup": "answer", "categories": ["FOOD"]}}
        with mock.patch.dict(fake.SPENDING_SAID, said), mock.patch.dict(fake.JUDGE_SAID, {text: ("where_changed", {"by": "time_slot"})}):
            talk = Talk("B", judge=judge)
            self.assertEqual((talk.say(text)["status"], judge.calls), ("awaiting_input", []))
            done = talk.say("식비로 볼래")
        self.assertEqual(done["status"], "replied")
        self.assertEqual(len(judge.calls), 1)
        self.assertEqual(judge.calls[0]["conditions"]["categories"], ["FOOD"])
        self.assertEqual(done["reply"]["trace"]["agent"]["status"], "tool")

    def test_9_general_why_has_no_judge_and_no_b(self):
        """일반 '왜 늘었어?' → 기존 증감 근거 + 짧은 서버 설명. 판단 · B 모두 0회. 증감(큰 금액 카드) + 결제 건수 · 건당 평균 비교"""
        judge, plan = Judge(), Plan()
        for texts in ([WHY], [COMPARE, "왜 식비가 늘었어?"]):
            with self.subTest(texts=texts):
                talk = Talk("B", judge=judge, plan=plan)
                for text in texts:
                    result = talk.say(text)
                self.assertEqual(result["status"], "replied")
                view = result["reply"]["view"]
                self.assertEqual([b["kind"] for b in view], ["head", "total", "rows", "text"])
                self.assertEqual([r["name"] for r in view[2]["rows"]], ["결제 건수", "건당 평균"])
                self.assertEqual(view[-1]["lines"], ["더 자주, 한 번에 더 많이 결제했어요."])  # 민재: 29 → 35건 · 14,000 → 18,000원
                said = " ".join(result["reply"]["lines"])
                self.assertTrue("결제 건수는 " in said and "건당 평균 결제액은 " in said, said)  # 글자만 보는 화면에도 두 값
                self.assertFalse(any(word in said for word in ("많이 늘어난 곳", "시간대", "줄이면")))
                self.assertIn("단정할 수는 없어요", result["reply"]["notices"][0])
                self.assertNotIn("suggest", result["reply"])
        self.assertEqual((judge.calls, plan.calls), ([], []))

    def test_10_hypothesis_goes_to_b_only(self):
        """명시적인 가설 → B 만 (판단 없음). B 가 실패하면 서버 설명으로 끝 (판단 · 다른 LLM 으로 우회하지 않음)"""
        for fail in (False, True):
            with self.subTest(fail=fail):
                judge, plan = Judge(), Plan(fail=fail)
                talk = Talk("B", judge=judge, plan=plan)
                talk.say(COMPARE)
                result = talk.say("배달 때문이야?")
                self.assertEqual(result["status"], "replied")
                self.assertEqual(judge.calls, [])
                self.assertEqual(plan.calls[0], claims.PLAN_PROMPT)
                if fail:
                    self.assertEqual(plan.calls, [claims.PLAN_PROMPT])  # 실패 뒤 재시도 · 답 LLM 없음
                    self.assertNotIn("배달", " ".join(result["reply"]["lines"]))  # 확인 못 한 가설을 확인한 것처럼 말하지 않는다

    def test_11_follow_ups_keep_context(self):
        """후속 질문은 판단 없이 기존 맥락 처리 그대로"""
        judge = Judge()
        talk = Talk("A", judge=judge)
        talk.say(COMPARE)
        items = talk.say("그럼 교통비는?")["decision"].items
        self.assertEqual([(i["type"], i["category"]) for i in items], [(7, "TRANSPORT_CAR")])
        self.assertEqual(judge.calls, [])


@fixture_db_only
class LiveReadingRegressionTest(TestCase):
    """11/10 실제 LLM 평가(verify_agent --live)에서 실패한 해석값 그대로 (chat.llm.fake.AGENT_SAID). 기대값은 평가 기준 그대로"""

    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def said(self, persona, *texts, judge=None):
        talk = Talk(persona, judge=judge or Judge())
        for text in texts:
            result = talk.say(text)
        return talk, result

    def test_a1_explain_with_grouping_is_split(self):
        """A1: explain=true(가설 없음) + 비교 + 시간대 → 비교 먼저 + 시간대 나누기 (기존엔 compare_query 미지원 안내)"""
        self.assertEqual(fake.SPENDING_SAID[SPLIT]["explain"], True)
        _, result = self.said("B", SPLIT)
        trace = result["reply"]["trace"]["agent"]
        self.assertEqual((trace["status"], trace["choice"]["tool"], trace["unmet"]), ("tool", "where_changed", []))
        self.assertIn("저녁", " ".join(result["reply"]["lines"]))

    def test_a1_with_a_hypothesis_goes_to_b(self):
        """가설 + 시간대 나누기('배달 때문에 저녁에 늘었어?' → hypothesis + group_by): B 가 실제로 돈다 (판단 단계는 안 부름).
        B 가 다루지 않는 시간대 나누기는 못 봤다고 안내. B 가 꺼져 있으면 기존 미지원 안내 그대로"""
        text = "배달 때문에 저녁에 늘었어?"
        reading = {"mine": True, **fake.SPENDING_SAID[SPLIT], "hypothesis": {"metric": "amount", "target": "online:배달앱", "unavailable": None}}
        with mock.patch.dict(fake.SPENDING_SAID, {text: reading}), mock.patch.dict(fake.PLAN_SAID, {text: fake.PLAN_SAID["배달 때문이야?"]}), \
                mock.patch.dict(fake.ANSWER_SAID, {text: fake.ANSWER_SAID["배달 때문이야?"]}):
            judge, plan = Judge(), Plan()
            result = Talk("B", judge=judge, plan=plan).say(text)
            self.assertEqual(Talk("B", judge=Judge()).say(text)["reason"], "compare_query")  # B 꺼짐
        self.assertEqual(judge.calls, [])
        self.assertEqual(plan.calls, [claims.PLAN_PROMPT, claims.ANSWER_PROMPT])  # B 계획 · 답이 실제로 불렸다
        reply = result["reply"]
        self.assertEqual((result["status"], reply["trace"]["fallback"]), ("replied", None))  # B 답 (서버 설명으로 빠지지 않음)
        self.assertIn("배달앱", " ".join(reply["lines"]))
        self.assertEqual(reply["notices"][-1], agent_loop.UNMET["where_changed"].format(name="시간대"))

    def test_a3_budget_and_compare_runs_the_compare_first(self):
        """A3: budget_left + 지난달 비교 + multi_step → 소비 비교 먼저(기록) → 지금 예산 주기 잔액 추가 확인. 예산 되묻기를 남기지 않는다"""
        with mock.patch.object(adapter, "run", wraps=adapter.run) as run:
            talk, result = self.said("A", BUDGET)
        self.assertEqual([[(i["type"], i["category"]) for i in c.args[1].items] for c in run.call_args_list], [[(7, "FOOD")], [(3, "FOOD")]])
        reply = result["reply"]
        self.assertEqual((result["status"], reply["trace"]["agent"]["choice"]["tool"], reply["trace"]["agent"]["unmet"]),
                         ("replied", "budget_lookup", []))
        self.assertEqual(reply["view"][:3], reply_of("A", COMPARE)["view"])
        self.assertTrue(any("이번 예산 기간은" in line for line in reply["lines"]))
        items = talk.say("그럼 교통비는?")["decision"].items  # 되묻기 대기 없이 비교를 이어받는다
        self.assertEqual([(i["type"], i["category"]) for i in items], [(7, "TRANSPORT_CAR")])

    def test_a3_pure_budget_compare_keeps_asking(self):
        """multi_step 없는 '지난달보다 예산 많이 남았어?'(순수 예산 비교) · 지난달 예산은 기존 되묻기 그대로 (스위치 꺼짐과 같은 답)"""
        for text, reading in (("지난달보다 식비 예산 많이 남았어?", {"target": "budget_left", "compare": "prev_month", "categories": ["FOOD"]}),
                              ("지난달 식비 예산 얼마 남았었어?", {"target": "budget_left", "period": "last_month", "categories": ["FOOD"]})):
            with self.subTest(text=text), mock.patch.dict(fake.SPENDING_SAID, {text: {"mine": True, **reading}}):
                judge = Judge()
                on, off = Talk("A", judge=judge).say(text), Talk("A").say(text)
                self.assertEqual((on["status"], on["reply"], on["detail"]), (off["status"], off["reply"], off["detail"]))
                self.assertEqual(judge.calls, [])

    def test_a4_weekday_daily_average_is_kept(self):
        """A4: measure=daily_avg + 요일 → 전체 비교 먼저 + 요일별 하루 평균으로 나누기 (합계로 바꾸지 않는다)"""
        judge = Judge()
        _, result = self.said("A", "지난달보다 어느 요일에 더 썼어?", judge=judge)
        self.assertEqual(judge.calls[0]["needs"], [{"tool": "where_changed", "by": "weekday", "measure": "daily_avg"}])
        trace = result["reply"]["trace"]["agent"]
        self.assertEqual((trace["status"], trace["choice"]["args"], trace["unmet"]), ("tool", {"by": "weekday"}, []))
        rows = result["reply"]["view"][-2]
        self.assertEqual(rows["title"], "요일별 하루 평균 변화 (고정지출 제외)")
        self.assertTrue(rows["rows"] and all(r["name"].endswith("요일") for r in rows["rows"]), rows)
        said = [line for line in result["reply"]["lines"] if "요일" in line]
        self.assertTrue(said and all("하루 평균" in line for line in said), said)  # 합계로 바꾸지 않았다

    def test_a4_weekday_sum_is_kept(self):
        """요일별 '합계'(measure=sum)를 물으면 합계로 나눈다 — 하루 평균으로 바꾸지 않는다 (순위가 달라질 수 있다)"""
        text, reading = "지난달보다 요일별로 얼마 더 썼어?", {"mine": True, "target": "spend", "compare": "prev_month", "measure": "sum",
                                                    "group_by": "weekday"}
        with mock.patch.dict(fake.SPENDING_SAID, {text: reading}):
            judge = Judge()
            talk, result = self.said("A", text, judge=judge)
        self.assertEqual(judge.calls[0]["needs"], [{"tool": "where_changed", "by": "weekday", "measure": "sum"}])
        rows = result["reply"]["view"][-2]
        self.assertEqual(rows["title"], "요일별 변화 (고정지출 제외)")
        ev = explain.evidence(talk.user, talk.store.get_analysis("talk-1")["items"][0], TODAY)
        now, before = ev["sides"]
        sums = {f"{DAY_NAMES[d]}요일": (before["weekdays"].get(d, 0), now["weekdays"].get(d, 0)) for d in range(7)}
        for r in rows["rows"]:
            b, a = sums[r["name"]]
            self.assertEqual(r["value"], f"{won(b)} → {won(a)}")  # 요일 합계 그대로 (하루 평균 아님)
        self.assertFalse(any("하루 평균" in line for line in result["reply"]["lines"]))
        top = max(sums, key=lambda k: sums[k][1] - sums[k][0])
        self.assertEqual(rows["rows"][0]["name"], top)  # 합계 증가가 가장 큰 요일이 1위

    def test_a4_other_daily_average_is_not_split(self):
        """시간대별 하루 평균은 지킬 수 없다 — 합계로 바꿔 답하지 않고 기존 미지원 안내 그대로"""
        text = "지난달보다 시간대별 하루 평균 더 썼어?"
        reading = {"mine": True, "target": "spend", "compare": "prev_month", "measure": "daily_avg", "group_by": "time_slot"}
        with mock.patch.dict(fake.SPENDING_SAID, {text: reading}):
            self.assertEqual(Talk("A", judge=Judge()).say(text)["reason"], "compare_query")

    def test_this_month_against_last_month(self):
        """'이번달 식비를 지난달과 비교': 맞는 해석(period=null + prev_month)이면 지난달 비교 카드. 화면에서 나온 잘못된 해석
        (period=last_month + prev_month — 지난달을 지난달과)은 서버가 하나를 짐작하지 않고 기간을 묻는다 (지시문 4번으로 고침)"""
        text = "이번달 식비를 지난달과 비교"
        result = Talk("A", judge=Judge()).say(text)
        self.assertEqual(result["reply"]["view"][0]["title"], "식비 · 지난달 같은 기간과 비교")
        self.assertEqual(result["reply"]["view"], reply_of("A", COMPARE)["view"])
        live = {**fake.SPENDING_SAID[text], "period": "last_month"}
        with mock.patch.dict(fake.SPENDING_SAID, {text: live}):
            asked = Talk("A", judge=Judge()).say(text)
        self.assertEqual((asked["status"], asked["detail"]["asking"], asked["reason"]), ("awaiting_input", "period_choice", None))

    def test_f2_follow_up_after_the_split(self):
        """F2: A1 뒤 '그럼 교통비는?' → 지난달 비교를 이어받는다 (화면에 '지난달 같은 기간과 비교')"""
        _, result = self.said("B", SPLIT, "그럼 교통비는?")
        self.assertEqual([(i["type"], i["category"]) for i in result["decision"].items], [(7, "TRANSPORT_CAR")])
        self.assertEqual(result["reply"]["view"][0]["title"], "교통·차량 · 지난달 같은 기간과 비교")

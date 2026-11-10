"""B 1차 서버 부분(MEMO 26절, spending_analysis/claims.py) — 주장 · 비교하며 묶기 · 멈춤 판정 · 답 검사. LLM 호출 없음
실행: python manage.py test spending_fixture.test_claims --settings=spending_fixture.settings
"""
import json
import re

from django.test import TestCase

from spending_analysis import claims as C
from spending_analysis import explain
from spending_analysis.answer import NAMES

from . import seed
from .test_conversation import TODAY, Chat
from .test_fixture import DESIGN, fixture_db_only, user


def _ev(persona, category=None):
    item = {"type": 7 if category else 6, "category": category, "fresh": True}
    return item, explain.evidence(user(persona), item, TODAY)


def _by(claims, axis, rank=None, kind="change"):
    return next(c for c in claims if c["axis"] == axis and c["kind"] == kind and (rank is None or c["rank"] == rank))


def _answer(*sentences):
    return json.dumps({"sentences": [{"text": t, "claims": ids} for t, ids in sentences]}, ensure_ascii=False)



def _server_numbers(ev):
    """근거 안의 모든 정수(금액 · 건수 · 증감)를 문장 모양('12,000원' · '26건')으로"""
    out, stack = [], [ev]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            stack += list(v.values())
        elif isinstance(v, (list, tuple)):
            stack += list(v)
        elif isinstance(v, int) and not isinstance(v, bool):
            out += [f"{abs(v):,}원", f"{abs(v)}건"]
    return out


@fixture_db_only
class ClaimValueTest(TestCase):
    """주장 값 = 지금 템플릿 설명 숫자 (같은 evidence). 3명 × 전체 · 카테고리 12개, 늘어난 경우 전부"""

    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def test_every_claim_number_is_a_server_number(self):
        """주장의 금액 · 건수는 모두 서버 근거(evidence)가 계산한 값이다 (짧은 기본 답에 없는 값도 근거에는 있다, MEMO 31)"""
        checked = 0
        for persona in ("A", "B", "C"):
            for code in (None, *[c for c in NAMES if c != "UNCLASSIFIED"]):
                item, ev = _ev(persona, code)
                if ev["state"] != "ok" or ev["delta"] <= 0:
                    continue
                said = json.dumps(explain.answer(user(persona), [item], today=TODAY), ensure_ascii=False) + " ".join(_server_numbers(ev))
                for c in C.first_claims(ev, item):
                    for number in re.findall(r"[\d,]+[원건]", c["text"]):
                        self.assertIn(number, said, (persona, code, c))
                    checked += 1
        self.assertGreater(checked, 40)

    def test_yunseo_food_claims(self):
        item, ev = _ev("A", "FOOD")
        got = [(c["axis"], c["subject"], c["direction"], c["rank"], c["share"], c["text"]) for c in C.first_claims(ev, item)]
        self.assertEqual([g[:5] for g in got[:5]], [("scope", "식비", "up", None, None), ("count", "결제 건수", "down", None, None),
                                                   ("average", "건당 평균", "up", None, None), ("time_slot", "점심", "up", 1, "all"),
                                                   ("visit", "지난달에도 간 곳", "up", None, "all")])
        # 서버 절 (26-16) — 방향 · 조사 · 단위까지. 시간대 · 지난달에도 간 곳 금액은 템플릿의 '점심 시간대(+48,000원)' · '42,000원'과 같다
        self.assertEqual([g[5] for g in got[:3]], ["식비 지출은 지난달 같은 기간보다 42,000원 늘었어요.", "결제 건수는 29건에서 26건으로 줄었어요.",
                                                  "건당 평균은 12,000원에서 15,000원으로 올랐어요."])
        self.assertRegex(got[3][5], r"^고정지출을 빼고 보면 점심 시간대는 [\d,]+원에서 [\d,]+원으로 48,000원 늘어 시간대 중 가장 많이 늘었어요\.$")
        self.assertRegex(got[4][5], r"^지난달에도 간 곳\(\d+곳\)에서 42,000원 늘었어요\.$")
        shops = [c for c in C.first_claims(ev, item) if c["axis"] == "merchant"]
        self.assertEqual([(c["direction"], c["rank"]) for c in shops], [("up", 1), ("up", 2), ("up", 3), ("down", 1), ("down", 2), ("down", 3)])
        sent = json.dumps(C.for_llm(C.first_claims(ev, item)), ensure_ascii=False)
        for secret in ("범카츠", "42,000", "12,000원"):  # 가게 이름 · 금액은 보내지 않는다
            self.assertNotIn(secret, sent)
        self.assertIn("가게 1", sent)

    def test_compare_group_and_argument_checks(self):
        item, ev = _ev("B", "FOOD")
        slots = C.compare_group(ev, item, "time_slot")
        self.assertEqual((slots[0]["subject"], slots[0]["rank"]), ("저녁", 1))
        self.assertEqual(slots[0]["subject"], _by(C.first_claims(ev, item), "time_slot")["subject"])  # 첫 주장의 가장 큰 시간대와 같다
        brand = C.compare_group(ev, item, "merchant", focus="배민")  # 브랜드로 물어도 (평가 e07 — 예전엔 버렸다)
        self.assertEqual((brand[0]["kind"], brand[0]["subject"]), ("absent", "배민"))
        for bad in (("category", None), ("amount", None), ("time_slot", "배달"), ("merchant", "스타벅스")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                C.compare_group(ev, item, *bad)
        absent = C.compare_group(ev, item, "merchant", focus="배달")  # 더미 3명 모두 배달앱 결제 0건
        self.assertEqual((absent[0]["kind"], absent[0]["subject"]), ("absent", "배달앱"))
        self.assertIn("배달의민족", absent[0]["conditions"]["search_scope"])


@fixture_db_only
class StopTest(TestCase):
    """시연 장면 3개의 멈춤 판정 (26-9)"""

    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def test_yunseo_hypothesis_goes_to_the_planner(self):
        a = Chat(user("A"))
        a.say("지난달보다 식비 더 썼어?")
        item = a.store.get_analysis("talk-1")["items"][0]
        ev = explain.evidence(user("A"), item, TODAY)
        # 첫 결과로 답할 수 있는지는 계획 LLM 이 정한다(26-12 ②) — 서버는 멈추지 않고, 가설 축은 평가용 참고값으로만
        self.assertEqual(C.stop(ev, item, C.first_claims(ev, item)), None)

    def test_minjae_needs_a_plan(self):
        item, ev = _ev("B", "FOOD")
        reason = C.stop(ev, item, C.first_claims(ev, item))
        self.assertIsNone(reason)
        # 가게별 순위는 첫 주장에 이미 있다 → 추가로 볼 묶음은 시간대 · 요일 전체 순위
        self.assertEqual(C.candidates(ev, item, C.first_claims(ev, item)), ["time_slot", "weekday"])
        self.assertEqual(C.stop(ev, item, [], used=2), "nothing_more")  # 상한

    def test_jiwoo_insufficient(self):
        item, ev = _ev("C", "FOOD")  # 지난달 자료 없음
        self.assertEqual((ev["state"], C.stop(ev, item, [])), ("incomplete", "insufficient"))

    def test_delivery_question_is_not_answered_by_first_claims(self):
        item, ev = _ev("A", "FOOD")
        reason = C.stop(ev, item, C.first_claims(ev, item))
        self.assertIsNone(reason)  # 계획 LLM 이 배달 묶음을 찾아보게


@fixture_db_only
class CheckTest(TestCase):
    """답 검사: 맞는 답은 통과(서버 값을 끼움), 일부러 만든 나쁜 답은 모두 탈락"""

    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def setUp(self):
        self.item, self.ev = _ev("A", "FOOD")
        self.claims = C.first_claims(self.ev, self.item)
        self.claims += C.compare_group(self.ev, self.item, "merchant", focus="배달", start=len(self.claims) + 1)
        self.id = {k: _by(self.claims, k)["id"] for k in ("scope", "count", "average", "time_slot")}
        self.id["shop2"] = _by(self.claims, "merchant", 2)["id"]
        self.id["absent"] = _by(self.claims, "merchant", kind="absent")["id"]

    def test_good_answer_passes_with_server_clauses(self):
        i = self.id
        out, why = C.check(_answer(
            (f"{{{i['average']}}} 반면 {{{i['count']}}}", [i["average"], i["count"]]),
            (f"{{{i['scope']}}}", [i["scope"]])), self.claims, {"axis": "average", "subject": None})
        self.assertEqual(why, [])
        self.assertEqual(out[0], "건당 평균은 12,000원에서 15,000원으로 올랐어요. 반면 결제 건수는 29건에서 26건으로 줄었어요.")
        out, why = C.check(_answer((f"{{{i['absent']}}}", [i["absent"]])), self.claims,
                           {"axis": "merchant", "subject": "배달"})
        self.assertTrue(out[0].startswith("확인한 배달앱 이름("), why)

    def test_observations_do_not_license_yes_no_or_soft_negation(self):
        # 증감·검색 부재가 맞아도 사용자가 추측한 원인·행동의 긍정/부정은 별개의 결론이다.
        for axis in ("average", "count"):
            for prefix in ("네, ", "아니요, ", "자주 사 먹어서라고 보기는 어려워요. "):
                with self.subTest(axis=axis, prefix=prefix):
                    cid = self.id[axis]
                    lines, why = C.check(_answer((prefix + "{" + cid + "}", [cid])), self.claims,
                                         {"axis": axis, "subject": None})
                    self.assertIsNone(lines)
                    self.assertIn("1:free_text", why)

    def test_key_factor_is_required_without_hypothesis(self):
        """26-17 ②: 가설 없는 '왜 늘었어?'는 가장 크게 바뀐 묶음 주장(동률이면 그중 하나)을 인용해야 한다"""
        i, key = self.id, C.key_claims(self.claims)
        # 윤서 식비: 점심 시간대 · 범카츠건대역점 모두 +48,000 (동률 — 둘 중 하나면 된다), 지난달에도 간 곳 +42,000 보다 큼
        self.assertEqual([c["subject"] for c in self.claims if c["id"] in key], ["점심", "범카츠건대역점"])
        self.assertEqual(C.check(_answer((f"{{{i['scope']}}}", [i["scope"]])), self.claims, None, [key])[1], ["0:missing_must1"])
        self.assertEqual(C.check(_answer((f"{{{i['scope']}}} 특히 {{{i['time_slot']}}}", [i["scope"], i["time_slot"]])),
                                 self.claims, None, [key])[1], [])

    def test_group_hypothesis_needs_the_group_claim_and_length_limits(self):
        """26-17 ③ ④: 주말 가설 부정은 주말 묶음 주장으로만, 한 문장 절 2개까지, '고정지출을 빼고 보면'은 답에 한 번"""
        item, ev = _ev("B", "FASHION_SHOPPING")
        claims = C.first_claims(ev, item)
        claims += C.compare_group(ev, item, "weekday", start=len(claims) + 1)
        weekend = next(c for c in claims if c["subject"] == "주말")
        saturday = next(c for c in claims if c["subject"] == "토요일")
        hyp = {"axis": "weekday", "subject": "주말"}
        self.assertEqual(weekend["text"].split(" 하루 평균")[0], "고정지출을 빼고 보면 주말")
        only_saturday = C.check(_answer((f"주말 쇼핑이라고 보기는 어려워요. {{{saturday['id']}}}", [saturday["id"]])), claims, hyp)[1]
        self.assertIn("0:hypothesis_first", only_saturday)  # 토요일 하나로 주말을 부정하지 않는다 (e11)
        if weekend["direction"] in ("down", "same"):
            self.assertEqual(C.check(_answer((f"{{{weekend['id']}}}", [weekend["id"]])), claims, hyp)[1], [])
        three = [c["id"] for c in claims[:3]]
        self.assertIn("1:too_many_clauses", C.check(_answer(("{%s} {%s} {%s}" % tuple(three), three)), claims, hyp)[1])
        slot = [c for c in claims if c["axis"] == "time_slot"] + [c for c in claims if c["axis"] == "weekday" and c["subject"] == "토요일"]
        out, _ = C.check(_answer((f"{{{slot[0]['id']}}}", [slot[0]["id"]]), (f"또 {{{slot[1]['id']}}}", [slot[1]["id"]])), claims)
        self.assertEqual(sum(line.count(C.FIXED) for line in out), 1)

    def test_bad_answers_are_all_rejected(self):
        i = self.id
        bad = {
            "free_text": (f"{{{i['count']}}} 결국 늘었어요.", [i["count"]]),                         # 증감 동사를 직접 (방향 반대)
            "free_text ": (f"가장 많이 늘어난 곳은 {{{i['shop2']}}}", [i["shop2"]]),                  # 순위를 직접 (2위)
            "negation": (f"배달앱 때문은 아니에요. {{{i['absent']}}}", [i["absent"]]),               # 단정한 부정
            "negation ": (f"점심 때문이라고 보기는 어려워요. {{{i['time_slot']}}}", [i["time_slot"]]),  # 근거(반대 방향) 없는 완곡한 부정
            "cause": (f"회식 때문에 {{{i['scope']}}}", [i["scope"]]),                                # 새 원인 (인과 단정)
            "new_group": (f"저녁에도 {{{i['scope']}}}", [i["scope"]]),                               # 주장에 없는 묶음
            "evaluation": (f"{{{i['scope']}}} 과소비예요.", [i["scope"]]),                           # 평가
            "number": (f"{{{i['scope']}}} 42,000원", [i["scope"]]),                                  # 숫자 직접
            "claim_id": (f"{{{i['scope']}}}", ["C99"]),                                              # 없는 주장
            "placeholder": (f"{{{i['average']}}}", [i["scope"]]),                                    # 인용하지 않은 자리표시
            "no_clause": ("식비가 늘었어요.", [i["scope"]]),                                          # 서버 절 없이 직접 쓴 문장
        }
        for reason, sentence in bad.items():
            with self.subTest(reason=reason):
                out, why = C.check(_answer((f"{{{i['scope']}}}", [i["scope"]]), sentence), self.claims)
                self.assertIsNone(out)
                self.assertIn(f"2:{reason.strip() if reason.strip() in ('number', 'claim_id', 'placeholder', 'no_clause') else 'free_text'}", why)

    def test_unclassified_scope_clause_says_classified_basis(self):
        ev = {**self.ev, "unclassified": (20_000, 10_000)}
        scope = C.first_claims(ev, self.item)[0]
        self.assertEqual((scope["text"], scope["rate"]), ("식비 지출은 분류된 내역 기준으로 지난달 같은 기간보다 42,000원 늘었어요.", None))


class _Slow:
    """가짜 시계 + 느린 계획 · 답. hang = 받은 대기 한도까지 기다리다 시간 초과, 숫자 = 그만큼 걸리고 답 (한도를 무시)"""

    def __init__(self, plan, answer, answer_text):
        self.now, self.plan, self.answer, self.answer_text, self.calls = 0.0, plan, answer, answer_text, []

    def clock(self):
        return self.now

    def __call__(self, system, user, timeout=None):
        takes = self.plan if system == C.PLAN_PROMPT else self.answer
        self.calls.append(("plan" if system == C.PLAN_PROMPT else "answer", timeout))
        if takes == "hang":
            self.now += timeout
            raise TimeoutError("대기 한도")
        self.now += takes
        plan = {"queries": [], "reason": "충분하다", "hypothesis": {"axis": "average", "subject": None}}
        return json.dumps(plan) if system == C.PLAN_PROMPT else self.answer_text


@fixture_db_only
class TimeLimitTest(TestCase):
    """26-15: 호출마다 12초 안의 남은 시간을 대기 한도로 — 느린 계획 · 느린 답에도 사용자 대기가 12초를 넘지 않는다"""

    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def run_with(self, plan, answer):
        from unittest import mock
        item, ev = _ev("B", "FOOD")
        sid = _by(C.first_claims(ev, item), "average")["id"]
        slow = _Slow(plan, answer, _answer((f"{{{sid}}}", [sid])))
        with mock.patch.object(C, "clock", slow.clock):
            lines, trace = C.run(ev, item, "왜 식비가 늘었어?", slow)
        return lines, trace, slow

    def test_fast_calls_answer(self):
        lines, trace, slow = self.run_with(1, 1)
        item, ev = _ev("B", "FOOD")
        self.assertEqual((lines, trace["fallback"]), ([_by(C.first_claims(ev, item), "average")["text"]], None))
        self.assertEqual(slow.calls, [("plan", C.TIMEOUT - C.ANSWER_MIN), ("answer", C.TIMEOUT - 1)])

    def test_slow_plan_and_slow_answer_stop_at_the_limit(self):
        lines, trace, slow = self.run_with("hang", "hang")
        self.assertEqual((lines, trace["fallback"]), (None, "plan_failed"))
        self.assertEqual([c[0] for c in slow.calls], ["plan"])
        self.assertLessEqual(slow.now, C.TIMEOUT)  # 계획 실패면 답을 더 호출하지 않는다

    def test_slow_answer_stops_at_the_limit(self):
        lines, trace, slow = self.run_with(1, "hang")
        self.assertEqual((lines, trace["fallback"]), (None, "answer_failed"))
        self.assertLessEqual(slow.now, C.TIMEOUT)

    def test_no_time_left_skips_the_answer_call(self):
        lines, trace, slow = self.run_with(10.5, 1)  # 계획이 한도를 넘겨 10.5초 → 답에 1.5초뿐
        self.assertEqual((lines, trace["fallback"], [c[0] for c in slow.calls]), (None, "no_time", ["plan"]))


@fixture_db_only
class QueryTopShareTest(TestCase):
    """26-19 ③: 추가 조회 1위는 범위 전체 증감의 20% 이상일 때만 필수 — 작으면 답에 안 써도 통과, 분석 과정에 '답에 안 쓴 조회'"""

    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def test_general_explanation_skips_incomparable_daily_average(self):
        item, ev = _ev("B", "FOOD")  # 식비 +224,000, 요일 1위 하루 평균 변화는 그 20%(44,800) 미만
        first = C.first_claims(ev, item)
        key = next(i for i in C.key_claims(first))

        def complete(system, user, timeout=None):
            if system == C.PLAN_PROMPT:
                return json.dumps({"queries": [{"by": "weekday", "focus": None}], "reason": "요일을 본다", "hypothesis": None})
            return _answer((f"{{{first[0]['id']}}}", [first[0]["id"]]), (f"특히 {{{key}}}", [key]))
        lines, trace = C.run(ev, item, "왜 식비가 늘었어?", complete)
        self.assertIsNotNone(lines, trace)
        self.assertEqual((trace["must"], trace["unused_queries"], trace["queries"]), ([[first[0]["id"]], C.key_claims(first)], [], []))
        self.assertEqual(trace["query_decisions"][0]["reason"], "different_metric")

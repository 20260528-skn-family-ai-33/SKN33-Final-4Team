"""라우터 → 소비 분석 → 답변 → 대화 맥락 기록 (독립 실행 연결) 검증
LLM 은 가짜 추출 응답, 계산은 격리 fixture DB 의 실제 analyze. 숫자 정답은 05 독립 기대값 · 고정 산술값.
chat API · replies.py · 실제 대화 상태 DB 는 쓰지 않는다 (MemoryStateStore).
실행: python manage.py test spending_fixture --settings=spending_fixture.settings
"""
import json
from functools import partial
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from importlib import import_module
from unittest import mock

from django.db import OperationalError
from django.test import SimpleTestCase, TestCase

from budgets.models import BudgetCycle
from chat.interpretation.legacy import RoleFakeLLM, view
from chat.router_core import MemoryStateStore, run_turn
from finance.models import BankAccount, Card, Connection, FinancialTransaction, SyncRun
from spending_analysis import adapter, explain
from spending_analysis import reading as spending_reading
from spending_analysis.periods import recent_months_to_date
from spending_analysis import agent as spending_agent
from spending_analysis.analyze import analyze
from spending_analysis.answer import compose
from spending_analysis.periods import Period
from spending_analysis.result import trend_stats
from users.models import User

from . import seed
from .test_fixture import DESIGN, EXPECTED, fixture_db_only, user

TODAY, AS_OF = seed.AS_OF.date(), seed.AS_OF
DESIGN_ORDER = import_module("budgets.migrations.0003_seed_categories").CATEGORIES  # 동률 정렬 기준 (DB 시드 순서)
# 공통 기준 문장 (답변 맨 앞에 한 번)
COMPARE = "이번 달(10월 1일~29일) 소비 지난달 같은 기간(9월 1일~29일)과 비교했어요. "
A_CYCLE = "이번 예산 기간은 10월 25일~11월 24일이고, 10월 29일까지 쓴 금액으로 계산했어요. "
B_CYCLE = "이번 예산 기간은 10월 10일~11월 9일이고, 10월 29일까지 쓴 금액으로 계산했어요. "
SAID = {
    "이번 달 얼마 썼어?": {"intents": ["spending"], "target": "spend", "period": "this_month"},
    "식비 얼마 썼어?": {"intents": ["spending"], "target": "spend", "categories": ["FOOD"]},
    "식비랑 교통비 얼마 썼어?": {"intents": ["spending"], "target": "spend", "categories": ["FOOD", "TRANSPORT_CAR"]},
    "식비 예산 얼마 남았어?": {"intents": ["spending"], "target": "budget_left", "categories": ["FOOD"]},
    "예산 얼마 남았어?": {"intents": ["spending"], "target": "budget_left"},
    "소비 분석해줘": {"intents": ["spending"], "target": "overview"},
    "지난달보다 많이 썼어?": {"intents": ["spending"], "target": "spend", "compare": "prev_month"},
    "지난달보다 식비 더 썼어?": {"intents": ["spending"], "target": "spend", "compare": "prev_month", "categories": ["FOOD"]},
    "최근 몇 달 소비 어때?": {"intents": ["spending"], "target": "overview", "period": "recent"},
    "최근 6개월 식비 어때?": {"intents": ["spending"], "target": "overview", "period": "recent", "months": 6,
                          "categories": ["FOOD"]},
    "그럼 교통비는?": {"intents": ["spending"], "categories": ["TRANSPORT_CAR"], "refers_to": "previous"},
    "그럼 식비는?": {"intents": ["spending"], "categories": ["FOOD"], "refers_to": "previous"},
    "전체는?": {"intents": ["spending"], "all_categories": True, "refers_to": "previous"},
    "왜 늘었어?": {"intents": ["spending"], "explain": True, "refers_to": "previous"},
    "지난달보다 식비랑 주거비 더 썼어?": {"intents": ["spending"], "target": "spend", "compare": "prev_month",
                              "categories": ["FOOD", "HOUSING_TELECOM"]},
}


# 통합 형식에 없는 소비 칸(이번 달 포함 · 세부 지출)을 쓰는 문장 — 라우터에는 소비 요청, 소비 해석에는 이 값 그대로
SPENDING_SAID = {
    "이번 달 포함 최근 3개월 식비 어때?": {"mine": True, "target": "overview", "period": "recent", "months": 3,
                                "include_current": True, "categories": ["FOOD"]},
    "이번 달 포함 최근 3개월 소비 어때?": {"mine": True, "target": "overview", "period": "recent", "months": 3,
                                "include_current": True},
    "커피 얼마나 썼어?": {"mine": True, "target": "spend", "categories": [{"detail": "커피", "category": "FOOD"}]},
    "지난달보다 커피 더 썼어?": {"mine": True, "target": "spend", "compare": "prev_month",
                         "categories": [{"detail": "커피", "category": "FOOD"}]},
}


def FakeLLM(broken=False):
    """통합 형식 가짜 추출값(SAID)을 부른 역할(라우터 · 소비) 형식으로 바꿔 답하는 대역 (단계 B)"""
    return RoleFakeLLM(SAID, broken)


class Chat:
    """한 회원의 독립 대화 (단계 B): run_turn → 라우터 → 소비 분석 에이전트(원문 자체 해석 · 분석 · 답변 · 기록)
    분리 전(22차)에는 route → adapter.run → compose · adapter.record 를 직접 이었다. 기대 문장은 그대로"""

    def __init__(self, u, store=None):
        self.user, self.store = u, store or MemoryStateStore()

    def say(self, text, talk="talk-1", llm=None):
        llm = llm or FakeLLM()
        if llm is not None and text in SPENDING_SAID:
            said = SPENDING_SAID[text]
            llm = lambda system, user: json.dumps(said if system == spending_reading.PROMPT else {"intents": ["spending"]},  # noqa: E731
                                                  ensure_ascii=False)
        agent = partial(spending_agent.handle, store=self.store, complete=llm, user=self.user)
        turn = run_turn(talk, self.store, llm, text=text, today=TODAY, as_of=AS_OF, agents={"spending": agent})
        reply = (turn["result"] or {}).get("reply")
        return view(turn), reply or None

    def text(self, said, talk="talk-1"):
        out = self.say(said, talk)[1]
        return " ".join(out.get("lead", []) + out["lines"]
                        + [t for s in out.get("sections", []) for t in [s["title"], *s["lines"]]] + out["notices"])


def sources(email):
    """입출금 계좌 1 + 카드 1 인 별도 사용자 (조회 기록 · 거래는 직접 만든다)"""
    u = User.objects.create_user(email=email)
    conn = {k: Connection.objects.create(user=u, business_type=k, organization_code=f"FIXTURE_{k.upper()}", status="connected")
            for k in ("bank", "card")}
    account = BankAccount.objects.create(user=u, connection=conn["bank"], source_account_key=f"fixture:{email}",
                                         account_type="demand")
    card = Card.objects.create(user=u, connection=conn["card"], payment_account=account, origin="provider",
                               source_card_key=f"fixture:{email}")
    return u, conn, card


def cover(u, conn, card, start, end):
    for kind, product in (("bank", "bank_sync"), ("card", "card_approval_list")):
        SyncRun.objects.create(user=u, connection=conn[kind], card=card if kind == "card" else None, product_code=product,
                               status="success", covered_from=start, covered_to=end)


@fixture_db_only
class PersonaAnswerTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def test_a_and_b_answers_types_1_to_9(self):
        expected = {
            "A": {
                "이번 달 얼마 썼어?": "이번 달(10월 1일~29일) 소비는 1,510,000원이에요. 고정지출 700,000원을 빼면 생활비는 810,000원이에요.",
                "식비 얼마 썼어?": "이번 달(10월 1일~29일) 식비에 390,000원 썼어요. 전체 소비 1,510,000원 중 25.83%예요.",
                "식비 예산 얼마 남았어?": A_CYCLE + "식비 예산 350,000원 중 60,000원을 썼고 290,000원 남았어요.",
                "예산 얼마 남았어?": A_CYCLE + "생활비 예산 900,000원 중 180,000원을 썼고 720,000원 남았어요.",
                "소비 분석해줘": "이번 달(10월 1일~29일) 소비는 1,510,000원이에요. 고정지출 700,000원을 빼면 생활비는 810,000원이에요. "
                           "분류된 소비 중 가장 많이 쓴 곳은 주거·통신, 700,000원이에요. 전체 소비 1,510,000원의 46.36%예요.",
                "지난달보다 많이 썼어?": COMPARE + "전체 · 1,510,000원 지난달보다 42,000원 증가했어요. ↑ 2.86%",
                "지난달보다 식비 더 썼어?": COMPARE + "식비 · 390,000원 지난달보다 42,000원 증가했어요. ↑ 12.07%",
                "최근 몇 달 소비 어때?": "최근 3개월 전체 지출이에요. 7월 1,420,000원 8월 1,460,000원 9월 1,480,000원 "
                                "7·8·9월 세 달 평균은 약 1,453,333원이에요. 8월에는 7월보다 40,000원, 약 2.82% 더 썼어요. "
                                "9월에는 8월보다 20,000원, 약 1.37% 더 썼어요.",
                "최근 6개월 식비 어때?": "최근 3개월까지만 볼 수 있어요. 최근 3개월 기준으로 알려드릴게요. "
                                  "최근 3개월 식비 지출이에요. 7월 330,000원 8월 350,000원 9월 360,000원 "
                                  "7·8·9월 세 달 평균은 약 346,667원이에요. 8월에는 7월보다 20,000원, 약 6.06% 더 썼어요. "
                                  "9월에는 8월보다 10,000원, 약 2.86% 더 썼어요.",
            },
            "B": {
                "식비 예산 얼마 남았어?": B_CYCLE + "식비 예산 400,000원 중 532,000원을 써서 132,000원 초과했어요.",
                "예산 얼마 남았어?": B_CYCLE + "생활비 예산 1,300,000원 중 1,362,000원을 써서 62,000원 초과했어요. "
                               "초과한 62,000원은 여윳돈으로 메울 수 있어요. 남는 여윳돈은 138,000원이에요.",
                "지난달보다 식비 더 썼어?": COMPARE + "식비 · 630,000원 지난달보다 224,000원 증가했어요. ↑ 55.17%",
                "지난달보다 많이 썼어?": COMPARE + "전체 · 2,290,000원 지난달보다 434,000원 증가했어요. ↑ 23.38%",
                "최근 몇 달 소비 어때?": "최근 3개월 전체 지출이에요. 7월 1,790,000원 8월 1,980,000원 9월 1,870,000원 "
                                "7·8·9월 세 달 평균은 1,880,000원이에요. 8월에는 7월보다 190,000원, 약 10.61% 더 썼어요. "
                                "9월에는 8월보다 110,000원, 약 5.56% 덜 썼어요.",
            },
        }
        for p, cases in expected.items():
            for said, want in cases.items():
                with self.subTest(persona=p, said=said):
                    self.assertEqual(Chat(user(p)).text(said), want)

    def test_multi_category_compare_shows_common_period_once(self):
        a = EXPECTED["personas"]["A"]
        now, before = a["current_month_known"]["by_category"], a["previous_same_dates_known"]["by_category"]
        self.assertEqual((now["FOOD"] - before["FOOD"], now["HOUSING_TELECOM"] - before["HOUSING_TELECOM"]), (42_000, 0))
        _, out = Chat(user("A")).say("지난달보다 식비랑 주거비 더 썼어?")
        self.assertEqual(out["lines"], ["이번 달(10월 1일~29일) 소비", "지난달 같은 기간(9월 1일~29일)과 비교했어요."])
        self.assertEqual(out["sections"], [
            {"title": "식비 · 390,000원", "lines": ["지난달보다 42,000원 증가했어요. ↑ 12.07%"]},  # 4,200,000 / 348,000
            {"title": "주거·통신 · 700,000원", "lines": ["지난달과 같아요."]}])

    def test_overview_chart_rows_follow_server_amounts(self):
        known = EXPECTED["personas"]["A"]["current_month_known"]
        order = [code for code, *_ in DESIGN_ORDER]
        want = sorted(known["by_category"], key=lambda c: (-known["by_category"][c], order.index(c)))
        _, out = Chat(user("A")).say("소비 분석해줘")
        rows = out["chart"]["rows"]
        self.assertEqual([r["code"] for r in rows], want)  # 금액 내림차순, 동률(교통 · 문화, 패션 · 교육)은 카테고리 순서
        for r in rows:  # 비율 = 카테고리 ÷ 전체 소비(고정지출 포함), 소수 둘째 자리 반올림
            want_share = (Decimal(known["by_category"][r["code"]]) * 100 / known["total"]).quantize(Decimal("0.01"), ROUND_HALF_UP)
            self.assertEqual(Decimal(r["share"]), want_share)
        self.assertEqual(rows[0]["text"], "주거·통신 700,000원 (46.36%)")
        self.assertEqual((out["chart"]["limited"], out["chart"]["empty"]), (None, None))
        self.assertNotIn("suggest", out)

    def test_trend_stats_match_independent_arithmetic(self):
        """월평균 · 이웃 달 증감 = 05 독립 기대값의 월별 합계로 직접 계산한 값 (원 · 비율 모두 소수 첫째 · 둘째 자리 HALF_UP)"""
        q = lambda x, e: x.quantize(Decimal(e), ROUND_HALF_UP)  # noqa: E731
        for p in ("A", "B"):
            sums = [EXPECTED["personas"][p]["monthly_known_sums"][m]["total"] for m in ("7", "8", "9")]
            stats = analyze(user(p), {"type": 8, "category": None, "months_requested": None, "months_applied": 3},
                            today=TODAY, as_of=AS_OF)["stats"]
            with self.subTest(persona=p):
                self.assertEqual(stats["average"]["value"], q(Decimal(sum(sums)) / 3, "1"))
                self.assertEqual([c["delta"] for c in stats["changes"]], [sums[1] - sums[0], sums[2] - sums[1]])
                self.assertEqual([c["rate"]["value"] for c in stats["changes"]],
                                 [q(Decimal(b - a) * 100 / a, "0.01") for a, b in zip(sums, sums[1:])])
                self.assertEqual((len(stats["used"]), stats["excluded"]), (3, []))

    def test_budget_over_suggests_adjustment_only_when_over(self):
        self.assertEqual(Chat(user("B")).say("식비 예산 얼마 남았어?")[1]["suggest"], ["budget_adjust"])
        self.assertNotIn("suggest", Chat(user("A")).say("식비 예산 얼마 남았어?")[1])

    def test_partial_overview_chart_is_marked_and_keeps_unclassified(self):
        _, out = Chat(user("C")).say("소비 분석해줘")
        chart = out["chart"]
        self.assertIsNotNone(chart["limited"])
        self.assertIn(("UNCLASSIFIED", 50_000), [(r["code"], r["amount"]) for r in chart["rows"]])
        self.assertEqual(sum(r["amount"] for r in chart["rows"]), 390_000)  # 관측 합계 = 표시한 전체

    def test_c_limited_data_and_no_approved_budget(self):
        c = Chat(user("C"))
        self.assertEqual(c.text("이번 달 얼마 썼어?"),
                         "이번 달(10월 1일~29일) 전체 지출은 일부 내역 기준 390,000원이에요. 카테고리가 아직 정해지지 않은 거래 "
                         "2건, 총 50,000원도 포함했어요. 일부 내역이 빠져 있을 수 있어요. 현재 확인된 내역으로 계산했어요.")
        budget = c.text("예산 얼마 남았어?")
        self.assertTrue(budget.startswith("아직 승인된 예산이 없어서 생활비 남은 예산을 알려드릴 수 없어요."))
        self.assertNotIn("510,000", budget)
        trend = c.text("최근 몇 달 소비 어때?")
        self.assertEqual(trend, "최근 3개월 전체 지출은 확인할 수 있는 내역이 없어요.")  # 달마다 같은 문장을 반복하지 않는다
        self.assertIn("지난달 같은 기간의 내역이 충분하지 않아 비교하기 어려워요.", c.text("지난달보다 많이 썼어?"))

    def test_no_approved_budget_and_db_failure_differ(self):
        c = Chat(user("C"))
        with mock.patch.object(BudgetCycle.objects, "filter", side_effect=OperationalError("down")):
            failed = c.text("예산 얼마 남았어?", talk="talk-2")
        self.assertEqual(failed, "생활비 남은 예산 결과를 불러오지 못했어요. 불러오지 못한 항목은 잠시 후 다시 물어봐 주세요.")
        self.assertIn("아직 승인된 예산이 없어서", c.text("예산 얼마 남았어?"))

    def test_normal_then_category_then_all(self):
        a = Chat(user("A"))
        a.say("식비 얼마 썼어?")
        d, out = a.say("그럼 교통비는?")
        self.assertEqual(d.items, ({"type": 2, "category": "TRANSPORT_CAR"},))
        self.assertEqual(out["lines"][0], "이번 달(10월 1일~29일) 교통·차량에 90,000원 썼어요.")
        d, out = a.say("전체는?")
        self.assertEqual((d.items, out["lines"][0]), (({"type": 1, "category": None},),
                                                      "이번 달(10월 1일~29일) 소비는 1,510,000원이에요."))

    def test_partial_then_category_follow_up(self):
        c = Chat(user("C"))
        c.say("이번 달 얼마 썼어?")
        saved = c.store.get_analysis("talk-1")
        self.assertEqual((saved["items"][0]["evidence"], saved["items"][0]["limits"]), ("limited", ["current:partial"]))
        self.assertNotIn("facts", saved["items"][0])
        d, out = c.say("그럼 식비는?")  # 부분 자료여도 이번 달 조건을 이어받는다
        self.assertEqual(d.items, ({"type": 2, "category": "FOOD"},))
        self.assertEqual(out["lines"], ["이번 달(10월 1일~29일) 식비 지출은 일부 내역 기준 110,000원이에요."])

    def test_explain_uses_only_confirmed_evidence(self):
        a = Chat(user("A"))
        a.say("지난달보다 식비 더 썼어?")
        d, _ = a.say("왜 늘었어?")
        facts = d.explain["items"][0]["facts"]
        self.assertEqual((d.status, facts["comparison"]["category"]["delta"], facts["comparison"]["category"]["rate"]["value"]),
                         ("ready", 42_000, "12.07"))
        # 지난달 카드 조회 기록이 없어져 비교 불가가 되면, 앞의 확정 분석으로 바꿔 설명하지 않는다
        SyncRun.objects.filter(user=user("A"), product_code="card_approval_list", covered_from=date(2026, 9, 1)).delete()
        _, out = a.say("지난달보다 식비 더 썼어?")
        self.assertEqual(out["lead"], ["지난달 같은 기간의 내역이 충분하지 않아 비교하기 어려워요."])  # 핵심 안내가 먼저
        d, out = a.say("왜 늘었어?")
        self.assertEqual((d.status, d.reason, d.explain, out), ("unsupported", "evidence_incomplete", None, None))

    def test_explain_builds_transaction_evidence(self):
        """'왜 늘었어?' = 서버가 다시 조회한 근거. 05 기대값의 식비 합계 = 건수 × 1건 금액 (9월 29 × 12,000 · 10월 26 × 15,000)"""
        food = EXPECTED["personas"]["A"]
        self.assertEqual((food["previous_same_dates_known"]["by_category"]["FOOD"], food["current_month_known"]["by_category"]["FOOD"]),
                         (29 * 12_000, 26 * 15_000))
        a = Chat(user("A"))
        a.say("지난달보다 식비 더 썼어?")
        _, out = a.say("왜 늘었어?")
        self.assertEqual(out["lines"], [
            "식비 지출은 지난달 같은 기간보다 42,000원 늘었어요. ↑ 12.07%",
            "아래는 거래 내역에서 확인한 변화예요. 이것만으로 이유를 단정할 수는 없어요.",
            "결제 건수는 지난달 같은 기간 29건, 이번 달 26건이에요.",
            "건당 평균 결제액은 지난달 같은 기간 12,000원, 이번 달 15,000원이에요.",
            "가맹점별로는 가상한끼점 42,000원 늘었어요."])
        self.assertEqual(out["explain"], {"llm": False, "rejected": False, "failed": False})

    def test_explain_total_lists_categories_in_the_same_direction(self):
        a = Chat(user("A"))
        a.say("지난달보다 많이 썼어?")
        lines = a.say("왜 늘었어?")[1]["lines"]
        now, before = (EXPECTED["personas"]["A"][k]["by_category"] for k in ("current_month_known", "previous_same_dates_known"))
        up = sorted(((c, now.get(c, 0) - before.get(c, 0)) for c in set(now) | set(before) if now.get(c, 0) > before.get(c, 0)),
                    key=lambda cd: -cd[1])
        self.assertEqual(up, [("FOOD", 42_000), ("CULTURE_LEISURE", 20_000), ("FASHION_SHOPPING", 10_000)])  # 의료 −30,000 은 빠진다
        self.assertIn("카테고리별로는 식비 42,000원, 문화·여가 20,000원, 패션·쇼핑 10,000원 늘었어요.", lines)

    def test_explain_trend_uses_latest_neighbouring_months(self):
        a = Chat(user("A"))
        a.say("최근 6개월 식비 어때?")
        self.assertEqual(a.say("왜 늘었어?")[1]["lines"][0], "식비 지출은 8월보다 10,000원 늘었어요. ↑ 2.86%")

    def test_explain_refuses_without_basis_or_after_data_changed(self):
        a = Chat(user("A"))
        a.say("식비 얼마 썼어?")
        self.assertEqual(a.say("왜 늘었어?")[1]["lines"],
                         ["이 결과는 비교한 기간이 없어 무엇이 늘거나 줄었는지 설명할 수 없어요. 지난달 비교나 최근 추이를 본 뒤 물어봐 주세요."])
        a.say("지난달보다 식비 더 썼어?")
        card = Card.objects.filter(user=user("A")).first()
        FinancialTransaction.objects.create(
            user=user("A"), source_kind="card", card=card, source_key="fixture:explain-changed", occurred_on=date(2026, 10, 28),
            occurred_time=time(12), amount=9_000, direction="out", transaction_type="spending",
            confirmed_category_id="FOOD", confirmation_state="confirmed")
        self.assertEqual(a.say("왜 늘었어?")[1]["lines"],
                         ["그 사이 내역이 바뀌어 앞의 결과로는 설명할 수 없어요. 다시 조회한 뒤 물어봐 주세요."])

    def test_explain_llm_gets_labels_only_and_numbers_come_from_server(self):
        a = Chat(user("A"))
        a.say("지난달보다 식비 더 썼어?")
        item = a.store.get_analysis("talk-1")["items"][0]
        seen = []

        def good(system, payload):
            seen.append(payload)
            return json.dumps({"sentences": ["건당 평균 결제액은 {F4}에서 {F5}까지 올랐어요."]}, ensure_ascii=False)
        out = explain.answer(user("A"), [item], good)
        self.assertEqual((out["lines"][2], out["explain"]), ("건당 평균 결제액은 12,000원에서 15,000원까지 올랐어요.",
                                                             {"llm": True, "rejected": False, "failed": False}))
        # 자리표시 바로 뒤 조사는 끼운 값의 받침으로 고친다 ('이에요' 같은 서술은 그대로)
        said = json.dumps({"sentences": ["건당 평균 결제액이 {F4}에서 {F5}로 바뀌었어요.", "결제는 {F3}가 있었고 {F1}이에요."]},
                          ensure_ascii=False)
        out = explain.answer(user("A"), [item], lambda system, payload: said)
        self.assertEqual(out["lines"][2:4], ["건당 평균 결제액이 12,000원에서 15,000원으로 바뀌었어요.",
                                             "결제는 26건이 있었고 42,000원이에요."])

        def down(system, payload):
            raise TimeoutError("응답 없음")
        out = explain.answer(user("A"), [item], down)  # 호출 실패는 검사 탈락과 구분해 표시, 재시도 없이 템플릿
        self.assertEqual((out["lines"][2], out["explain"]), ("결제 건수는 지난달 같은 기간 29건, 이번 달 26건이에요.",
                                                             {"llm": True, "rejected": False, "failed": True}))
        for value in ("42,000", "42000", "12,000", "15,000", "29", "26", "12.07"):  # 금액 · 건수 · 비율은 넘기지 않는다
            self.assertNotIn(value, seen[0])
        for bad in ({"sentences": ["4만 2천 원보다 큰 {F1} 증가예요. 12% 늘었어요."]},  # 숫자 직접
                    {"sentences": ["외식 습관이 늘어난 {F1} 때문이에요."]},             # 습관 추측
                    {"sentences": ["{F99} 증가예요."]},                              # 없는 근거
                    {"sentences": ["근거 없이 쓴 문장"]}, "JSON 아님"):
            with self.subTest(bad=bad):
                raw = bad if isinstance(bad, str) else json.dumps(bad, ensure_ascii=False)
                out = explain.answer(user("A"), [item], lambda system, payload: raw)
                self.assertEqual((out["lines"][2], out["explain"]["rejected"]), ("결제 건수는 지난달 같은 기간 29건, 이번 달 26건이에요.", True))

    def test_multi_category_partial_failure_and_single_notice(self):
        c = Chat(user("C"))
        d, out = c.say("식비랑 교통비 얼마 썼어?")
        self.assertEqual(out["lines"], ["이번 달(10월 1일~29일) 식비 지출은 일부 내역 기준 110,000원이에요.",
                                        "이번 달(10월 1일~29일) 교통·차량 지출은 일부 내역 기준 30,000원이에요."])
        self.assertEqual(out["notices"], ["일부 내역이 빠져 있을 수 있어요. 현재 확인된 내역으로 계산했어요."])  # 한 번만

        def flaky(u, item, **kw):
            return {"state": "error", "reason": "db_error"} if item["category"] == "TRANSPORT_CAR" else analyze(u, item, **kw)

        a = Chat(user("A"))
        with mock.patch("spending_analysis.adapter.analyze", side_effect=flaky):
            d, out = a.say("식비랑 교통비 얼마 썼어?")
        self.assertEqual(out["lines"], ["이번 달(10월 1일~29일) 식비에 390,000원 썼어요.", "전체 소비 1,510,000원 중 25.83%예요.",
                                        "교통·차량 소비 결과를 불러오지 못했어요."])
        items = a.store.get_analysis("talk-1")["items"]
        self.assertEqual([(i["category"], i["ok"], i.get("evidence")) for i in items],
                         [("FOOD", True, "confirmed"), ("TRANSPORT_CAR", False, None)])

    def test_error_state_is_never_recorded_as_success(self):
        a = Chat(user("A"))
        a.say("식비 얼마 썼어?")
        before = json.dumps(a.store.get_analysis("talk-1"), sort_keys=True)
        with mock.patch("spending_analysis.adapter.analyze", return_value={"state": "error", "reason": "db_error"}):
            _, out = a.say("이번 달 얼마 썼어?")
        self.assertEqual(out["lines"], ["전체 소비 결과를 불러오지 못했어요."])
        self.assertEqual(json.dumps(a.store.get_analysis("talk-1"), sort_keys=True), before)  # 전부 실패 → 기존 기록 보존

    def test_llm_failure_keeps_state_and_talks_are_separate(self):
        store = MemoryStateStore()
        a, b = Chat(user("A"), store), Chat(user("B"), store)
        a.say("식비 얼마 썼어?", talk="t-a")
        b.say("지난달보다 많이 썼어?", talk="t-b")
        before = json.dumps(store.get_analysis("t-a"), sort_keys=True)
        decision, out = a.say("그럼 교통비는?", talk="t-a", llm=FakeLLM(broken=True))
        self.assertEqual((decision.status, out), ("failed", None))
        self.assertEqual(json.dumps(store.get_analysis("t-a"), sort_keys=True), before)
        self.assertEqual(store.get_analysis("t-b")["items"][0]["type"], 6)
        self.assertEqual(store.get_analysis("t-a")["items"][0]["type"], 2)

    def test_json_round_trip(self):
        a = Chat(user("A"))
        for said in ("지난달보다 많이 썼어?", "식비 예산 얼마 남았어?", "최근 몇 달 소비 어때?"):
            a.say(said)
            saved = a.store.get_analysis("talk-1")
            restored = json.loads(json.dumps(saved, ensure_ascii=False))
            self.assertEqual(restored, saved)
            item = restored["items"][0]
            with self.subTest(said=said):
                self.assertEqual(datetime.fromisoformat(item["as_of"]), AS_OF)
                self.assertEqual(item["evidence"], "confirmed")
                if said.startswith("지난달"):
                    self.assertEqual((adapter.period_from_json(item["period"]), adapter.period_from_json(item["compare_period"])),
                                     (Period(date(2026, 10, 1), date(2026, 10, 30)), Period(date(2026, 9, 1), date(2026, 9, 30))))
                    self.assertEqual(Decimal(item["facts"]["comparison"]["total"]["rate"]["value"]), Decimal("2.86"))
                    self.assertEqual(item["facts"]["comparison"]["total"]["rate"]["exact"],
                                     EXPECTED["personas"]["A"]["comparison"]["total_rate_exact"])
                elif said.startswith("식비"):
                    self.assertEqual((item["facts"]["difference"], item["facts"]["version_no"], item["period"]),
                                     (290_000, 1, {"from": "2026-10-25", "to": "2026-10-29"}))
                else:
                    self.assertEqual([s["confirmed"] for s in item["facts"]["series"]], [1_420_000, 1_460_000, 1_480_000])
        self.assertEqual(restored["request"]["target"], "overview")

    def test_type6_category_table(self):
        e = EXPECTED["personas"]
        a = analyze(user("A"), {"type": 6}, today=TODAY, as_of=AS_OF)["comparison"]["categories"]
        self.assertEqual(a["FOOD"]["delta"], e["A"]["comparison"]["food_delta"])
        self.assertEqual((a["MEDICAL_HEALTH"]["delta"], a["MEDICAL_HEALTH"]["rate"]["value"]), (-30_000, Decimal("-100.00")))
        self.assertNotIn("UNCLASSIFIED", a)
        b = analyze(user("B"), {"type": 6}, today=TODAY, as_of=AS_OF)["comparison"]["categories"]
        self.assertEqual(b["FOOD"]["delta"], e["B"]["comparison"]["food_delta"])
        c = analyze(user("C"), {"type": 6}, today=TODAY, as_of=AS_OF)["comparison"]["categories"]
        self.assertTrue(c and all(v["delta"] is None and not v["available"] for v in c.values()))


@fixture_db_only
class ZeroAndNoDataAnswerTest(TestCase):
    def test_real_zero_and_no_data_differ(self):
        u, conn, card = sources("fixture-zero@example.test")
        none = Chat(u).text("이번 달 얼마 썼어?")
        self.assertEqual(none, "이번 달(10월 1일~29일) 전체 지출은 확인할 수 있는 내역이 없어요.")
        cover(u, conn, card, date(2026, 10, 1), date(2026, 10, 29))
        self.assertEqual(Chat(u).text("이번 달 얼마 썼어?"), "이번 달(10월 1일~29일) 소비는 0원이에요.")

    def test_trend_groups_months_without_data_and_keeps_real_zero(self):
        u, conn, card = sources("fixture-trend-gaps@example.test")
        cover(u, conn, card, date(2026, 9, 1), date(2026, 9, 30))  # 9월: 수집 완료 · 거래 없음 = 실제 0원
        cover(u, conn, card, date(2026, 8, 1), date(2026, 8, 15))  # 8월: 절반만 수집
        FinancialTransaction.objects.create(
            user=u, source_kind="card", card=card, source_key="fixture:trend-gaps", occurred_on=date(2026, 8, 10),
            occurred_time=time(12), amount=12_000, direction="out", transaction_type="spending",
            confirmed_category_id="FOOD", confirmation_state="confirmed")
        self.assertEqual(Chat(u).text("최근 몇 달 소비 어때?"),
                         "최근 3개월 전체 지출이에요. 8월 12,000원 (일부 내역 기준) 9월 0원 7월은 확인할 수 있는 내역이 없어요. "
                         "내역이 다 모인 달이 두 달보다 적어 평균과 증감은 계산하지 않았어요.")  # 9월 하나뿐 → 추세 단정 없음
        none = Chat(sources("fixture-trend-none@example.test")[0]).say("최근 몇 달 소비 어때?")[1]
        self.assertEqual(none["lines"], ["최근 3개월 전체 지출은 확인할 수 있는 내역이 없어요."])

    def test_chart_for_zero_and_no_data(self):
        u, conn, card = sources("fixture-chart-zero@example.test")
        self.assertEqual(Chat(u).say("소비 분석해줘")[1]["chart"]["empty"], "확인할 수 있는 내역이 없어요.")
        cover(u, conn, card, date(2026, 10, 1), date(2026, 10, 29))
        chart = Chat(u).say("소비 분석해줘")[1]["chart"]
        self.assertEqual((chart["empty"], chart["rows"]), ("이번 달 소비가 0원이라 나눌 항목이 없어요.", []))

    def test_previous_zero_uses_delta_not_rate(self):
        u, conn, card = sources("fixture-prev-zero@example.test")
        cover(u, conn, card, date(2026, 9, 1), date(2026, 9, 30))
        cover(u, conn, card, date(2026, 10, 1), date(2026, 10, 29))
        FinancialTransaction.objects.create(
            user=u, source_kind="card", card=card, source_key="fixture:prev-zero", occurred_on=date(2026, 10, 5),
            occurred_time=time(12), amount=15_000, direction="out", transaction_type="spending",
            confirmed_category_id="FOOD", confirmation_state="confirmed")
        self.assertEqual(Chat(u).text("지난달보다 많이 썼어?"),
                         COMPARE + "전체 · 15,000원 지난달엔 0원이라 증감률 없이 금액으로 알려드려요. 15,000원 증가했어요.")


@fixture_db_only
class ComposeWithoutDbTest(SimpleTestCase):
    def test_unavailable_comparison_has_no_delta(self):
        block = {"period": Period(date(2026, 10, 1), date(2026, 10, 30)), "coverage": {"state": "complete"},
                 "observed": {"total": 100, "unclassified_amount": 0, "needs_review_count": 0}}
        result = {"type": 6, "item": {"type": 6, "category": None},
                  "current": {**block, "confirmed": block["observed"]},
                  "previous": {**block, "period": Period(date(2026, 9, 1), date(2026, 9, 30)), "coverage": {"state": "partial"},
                               "confirmed": None},
                  "comparison": {"total": {"available": False, "previous_incomplete": True, "current_incomplete": False,
                                           "delta": None, "rate": None}}}
        out = compose([{"item": result["item"], "ok": True, "result": result}])
        self.assertEqual((out["lines"], out["sections"]), (["참고로 이번 달(10월 1일~29일) 금액이에요."],
                                                           [{"title": "전체 · 100원", "lines": []}]))
        self.assertEqual((out["lead"], out["notices"]), (["지난달 같은 기간의 내역이 충분하지 않아 비교하기 어려워요."], []))

    def test_gap_month_is_not_bridged_and_averages_skip_it(self):
        """7 · 9월만 완전 수집 → 평균은 두 달, 8월을 건너뛴 7→9월 비교는 하지 않는다. 진행 중인 달은 series 에 없다"""
        def month(m, state, confirmed, observed):
            return {"period": Period(date(2026, m, 1), date(2026, m + 1, 1)), "state": state,
                    "confirmed": confirmed, "observed": observed}
        series = [month(7, "complete", 350_000, 350_000), month(8, "none", None, 0), month(9, "complete", 360_000, 360_000)]
        stats = trend_stats(series)
        self.assertEqual((stats["average"]["value"], stats["changes"]), (355_000, []))
        result = {"type": 9, "item": {"type": 9, "category": "FOOD"}, "series": series, "stats": stats}
        out = compose([{"item": result["item"], "ok": True, "result": result}])
        self.assertEqual(out["lines"], ["최근 3개월 식비 지출이에요.", "7월 350,000원", "9월 360,000원",
                                        "8월은 확인할 수 있는 내역이 없어요.", "7·9월 두 달 평균은 355,000원이에요.",
                                        "8월은 내역이 다 모이지 않아 평균 · 증감 계산에서 뺐어요."])
        series = [month(7, "none", None, 0), month(8, "none", None, 0), month(9, "complete", 0, 0)]
        out = compose([{"item": result["item"], "ok": True,
                        "result": {**result, "series": series, "stats": trend_stats(series)}}])
        self.assertIn("7·8월은 확인할 수 있는 내역이 없어요.", out["lines"])
        self.assertIn("9월 0원", out["lines"])  # 수집이 끝난 실제 0원은 숨기지 않는다

    def test_tied_top_categories_share_one_sentence(self):
        fig = {"total": 300, "fixed_amount": 0, "living_amount": 300, "unclassified_amount": 0, "needs_review_count": 0,
               "by_category": {"FOOD": 100, "TRANSPORT_CAR": 100, "UNCLASSIFIED": 100},
               "top_spending": [{"category": "FOOD", "amount": 100}, {"category": "TRANSPORT_CAR", "amount": 100}],
               "shares": {c: {"value": Decimal("33.33")} for c in ("FOOD", "TRANSPORT_CAR", "UNCLASSIFIED")}}
        block = {"period": Period(date(2026, 10, 1), date(2026, 10, 30)), "coverage": {"state": "complete"},
                 "observed": fig, "confirmed": fig}
        result = {"type": 5, "item": {"type": 5, "category": None}, "current": block}
        out = compose([{"item": result["item"], "ok": True, "result": result}])
        self.assertEqual(out["lines"][-1], "분류된 소비 중 가장 많이 쓴 곳은 식비·교통·차량, 각각 100원이에요. 각각 전체 소비 300원의 33.33%예요.")
        self.assertEqual([r["code"] for r in out["chart"]["rows"]], ["FOOD", "TRANSPORT_CAR", "UNCLASSIFIED"])


@fixture_db_only
class UserTestAnswerTest(TestCase):
    """사용자 테스트 보완 (3 · 4 · 6번) — 실제 analyze. 숫자 정답은 05 독립 기대값"""

    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def test_include_current_compares_same_dates_each_month(self):
        a = Chat(user("A"))
        _, out = a.say("이번 달 포함 최근 3개월 식비 어때?")
        known = EXPECTED["personas"]["A"]
        sep, oct_ = known["previous_same_dates_known"]["by_category"]["FOOD"], known["current_month_known"]["by_category"]["FOOD"]
        self.assertEqual(out["lines"][0], "이번 달을 포함한 최근 3개월 식비 지출이에요. 달마다 같은 날짜까지 맞춰 비교했어요.")
        self.assertEqual(out["lines"][2:4], [f"9월 1일~29일 {sep:,}원", f"10월 1일~29일 {oct_:,}원"])
        self.assertIn("10월에는 9월 같은 기간보다 42,000원, 약 12.07% 더 썼어요.", out["lines"])  # 4,200,000 / 348,000
        saved = a.store.get_analysis("talk-1")["items"][0]
        self.assertEqual((saved["include_current"], saved["period"]), (True, {"from": "2026-08-01", "to": "2026-10-29"}))
        self.assertEqual(a.say("왜 늘었어?")[1]["lines"][0], "식비 지출은 9월 1일~29일보다 42,000원 늘었어요. ↑ 12.07%")
        _, plain = Chat(user("A")).say("최근 몇 달 소비 어때?")  # 기존 최근 N개월은 완료된 달 그대로
        self.assertEqual(plain["lines"][1:4], ["7월 1,420,000원", "8월 1,460,000원", "9월 1,480,000원"])

    def test_include_current_date_boundaries(self):
        self.assertEqual([(p.start, p.last_day) for p in recent_months_to_date(date(2026, 3, 31), 3)],
                         [(date(2026, 1, 1), date(2026, 1, 31)), (date(2026, 2, 1), date(2026, 2, 28)),
                          (date(2026, 3, 1), date(2026, 3, 31))])
        self.assertEqual(recent_months_to_date(date(2028, 3, 30), 2)[0].last_day, date(2028, 2, 29))  # 윤년 말일
        self.assertEqual(recent_months_to_date(date(2026, 1, 15), 2)[0], Period(date(2025, 12, 1), date(2025, 12, 16)))

    def test_include_current_without_data(self):
        _, out = Chat(user("C")).say("이번 달 포함 최근 3개월 소비 어때?")
        self.assertEqual(out["lines"], ["이번 달을 포함한 최근 3개월 전체 지출이에요. 달마다 같은 날짜까지 맞춰 비교했어요.",
                                        "10월 1일~29일 390,000원 (일부 내역 기준)", "8·9월은 확인할 수 있는 내역이 없어요.",
                                        "내역이 다 모인 달이 두 달보다 적어 평균과 증감은 계산하지 않았어요."])

    def test_detail_fallback_shows_actual_scope_everywhere(self):
        a = Chat(user("A"))
        d, out = a.say("커피 얼마나 썼어?")
        self.assertEqual((d.asking, out), ("fallback_scope", None))  # 동의 전에는 조회하지 않는다
        _, out = a.say("식비 전체 보기")
        self.assertEqual(out["lead"], ["커피에 쓴 돈만 따로 구분할 수 없어 식비 전체로 조회했어요."])
        self.assertEqual(out["lines"][0], "이번 달(10월 1일~29일) 식비 전체에 390,000원 썼어요.")
        self.assertEqual(a.store.get_shared("talk-1", "spending")["items"][0]["instead_of"], "커피")  # 다른 에이전트에도 실제 범위

        a.say("지난달보다 커피 더 썼어?")
        a.say("식비 전체 보기")
        lines = a.say("왜 늘었어?")[1]["lines"]
        self.assertEqual((lines[0], lines[-1]), ("식비 전체 지출은 지난달 같은 기간보다 42,000원 늘었어요. ↑ 12.07%",
                                                 "식비 전체 기준이라 커피만의 변화는 알 수 없어요."))
        seen = []
        item = a.store.get_analysis("talk-1")["items"][0]
        explain.answer(user("A"), [item], lambda system, payload: seen.append(json.loads(payload)) or "JSON 아님")
        self.assertEqual((seen[0]["scope"], seen[0]["requested"]), ("식비 전체", "커피"))

    def test_incomplete_comparison_is_the_lead(self):
        _, out = Chat(user("C")).say("지난달보다 식비 더 썼어?")
        self.assertEqual(out["lead"], ["지난달 같은 기간의 내역이 충분하지 않아 비교하기 어려워요."])
        self.assertEqual(out["lines"], ["참고로 이번 달(10월 1일~29일) 금액이에요."])
        self.assertEqual(out["sections"], [{"title": "식비 · 일부 내역 기준 110,000원", "lines": []}])
        self.assertEqual(out["notices"], [])  # 일부 내역 기준은 금액 줄에 직접 표시

"""라우터 → 소비 분석 → 답변 → 대화 맥락 기록 (독립 실행 연결) 검증
LLM 은 가짜 추출 응답, 계산은 격리 fixture DB 의 실제 analyze. 숫자 정답은 05 독립 기대값 · 고정 산술값.
chat API · replies.py · 실제 대화 상태 DB 는 쓰지 않는다 (MemoryStateStore).
실행: python manage.py test spending_fixture --settings=spending_fixture.settings
"""
import json
from functools import partial
from pathlib import Path
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
from spending_analysis import adapter, dialog, explain
from spending_analysis import reading as spending_reading
from spending_analysis.periods import recent_months_to_date
from spending_analysis import agent as spending_agent
from spending_analysis.analyze import analyze
from spending_analysis.answer import compose
from spending_analysis.periods import Period
from spending_analysis.result import percent, trend_stats
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
    "이번 달 카드 얼마 긁었지?": {"mine": True, "target": "spend", "payment": "card"},
    "지난달보다 카드 더 긁었어?": {"mine": True, "target": "spend", "compare": "prev_month", "payment": "card"},
    "과자서원에서 얼마 썼어?": {"mine": True, "target": "spend", "categories": [{"detail": "과자서원", "category": "FOOD"}]},
    "식비는 왜 늘었을까?": {"mine": True, "explain": True, "categories": ["FOOD"], "refers_to": "previous"},
    "제일 많이 쓴 데는 왜 많이 나왔을까?": {"mine": True, "explain": True, "refers_to": "previous"},
    "카테고리별로 예산 얼마나 남았어?": {"mine": True, "target": "budget_left", "compare": "category"},
    "카테고리별로 얼마 썼어?": {"mine": True, "target": "spend", "compare": "category"},
    "남은 기간 동안 생활비 하루에 얼마 쓸 수 있어?": {"mine": True, "target": "budget_left"},
    "그럼 카테고리별로는?": {"mine": True, "compare": "category", "refers_to": "previous"},
    "주말 식비 얼마 썼어?": {"mine": True, "target": "spend", "weekday": ["weekend"], "categories": ["FOOD"]},
    "자주 간 가게 3곳 알려줘": {"mine": True, "measure": "count", "group_by": "merchant", "top_n": 3},
    "밤에 쓴 돈 얼마야?": {"mine": True, "target": "spend", "time_slot": ["night"]},
    "야식으로 얼마 썼어?": {"mine": True, "target": "spend", "time_slot": ["late_night"]},
    "이번 달 제일 큰 지출 뭐야?": {"mine": True, "measure": "max"},
    "하루 평균 얼마 썼어?": {"mine": True, "measure": "daily_avg"},
    "과자서원 몇 번 갔어?": {"mine": True, "measure": "count", "categories": [{"detail": "과자서원", "category": "FOOD"}]},
    "배달 몇 번 시켰어?": {"mine": True, "measure": "count", "categories": [{"detail": "배달", "category": "FOOD"}]},
    "지난달 식비 얼마 썼어?": {"mine": True, "target": "spend", "period": "last_month", "categories": ["FOOD"]},
    "계좌이체로 쓴 돈 얼마야?": {"mine": True, "target": "spend", "payment": "bank"},
    "만 원 넘게 쓴 거 몇 번이야?": {"mine": True, "measure": "count", "amount_min": 10000},
    "식비 한 번에 평균 얼마 써?": {"mine": True, "measure": "avg", "categories": ["FOOD"]},
    "요일별 소비 보여줘": {"mine": True, "group_by": "weekday"},
    "시간대별로 보여줘": {"mine": True, "group_by": "time_slot"},
    "예산 밤 시간대로 보여줘": {"mine": True, "target": "budget_left", "time_slot": ["night"]},
    "이번 주 얼마 썼어?": {"mine": True, "target": "spend", "period": "this_week", "period_said": "이번 주"},
    "지난주 식비 얼마야?": {"mine": True, "target": "spend", "period": "last_week", "period_said": "지난주", "categories": ["FOOD"]},
    "8월 식비 얼마 썼어?": {"mine": True, "target": "spend", "period": "month", "month": 8, "period_said": "8월",
                       "categories": ["FOOD"]},
    "10월 3일부터 10일까지 얼마 썼어?": {"mine": True, "target": "spend", "period": "range", "date_from": "10-03",
                                "date_to": "10-10", "period_said": "10월 3일부터 10일까지"},
    "5월 식비 얼마 썼어?": {"mine": True, "target": "spend", "period": "month", "month": 5, "period_said": "5월",
                       "categories": ["FOOD"]},
    "작년 식비 얼마 썼어?": {"mine": True, "target": "spend", "period_said": "작년", "categories": ["FOOD"]},
    "지난주보다 더 썼어?": {"mine": True, "target": "spend", "compare": "other_period", "period_said": "지난주보다"},
    "고정지출 포함해서 보기": {"mine": True, "refers_to": "previous", "include_fixed": True},
    "20일부터 27일까지 쓴 돈 얼마야?": {"mine": True, "target": "spend", "period": "range", "date_from": "20", "date_to": "27",
                               "period_said": "20일부터 27일까지"},
    "25일부터 31일까지 얼마 썼어?": {"mine": True, "target": "spend", "period": "range", "date_from": "25", "date_to": "31",
                             "period_said": "25일부터 31일까지"},
    "30일부터 31일까지 얼마 썼어?": {"mine": True, "target": "spend", "period": "range", "date_from": "30", "date_to": "31",
                             "period_said": "30일부터 31일까지"},
    "주거비 예산 얼마 남았어?": {"mine": True, "target": "budget_left", "categories": ["HOUSING_TELECOM"]},
    "식비랑 여행 예산 얼마 남았어?": {"mine": True, "target": "budget_left", "categories": ["FOOD", "TRAVEL_LODGING"]},
    "10월 5일부터 10일까지 카테고리별로 얼마 썼어?": {"mine": True, "target": "spend", "period": "range", "date_from": "10-05",
                                        "date_to": "10-10", "compare": "category", "period_said": "10월 5일부터 10일까지"},
    "엄마가 이번 달 식비 얼마 썼어?": {"mine": True, "target": "spend", "period": "this_month", "period_said": "이번 달",
                              "categories": ["FOOD"], "other_person": True},
    "패션 쇼핑 몇 건 결제했어?": {"mine": True, "measure": "count", "categories": ["FASHION_SHOPPING"]},
    "패션 쇼핑 한 번에 평균 얼마야?": {"mine": True, "measure": "avg", "categories": ["FASHION_SHOPPING"]},
    "가게별 상위 3곳 보여줘": {"mine": True, "group_by": "merchant", "top_n": 3},
    "요일별로 몇 번 결제했어?": {"mine": True, "measure": "count", "group_by": "weekday"},
    "이번 달 결제 몇 건이야?": {"mine": True, "measure": "count", "period": "this_month"},
    "카테고리별로 몇 번 썼어?": {"mine": True, "measure": "count", "group_by": "category"},
    "배달 얼마 썼어?": {"mine": True, "target": "spend", "categories": [{"detail": "배달", "category": "FOOD"}]},
    "배민 얼마 썼어?": {"mine": True, "target": "spend", "categories": [{"detail": "배민", "category": "FOOD"}]},
    "그럼 왜 늘었는데?": {"mine": True, "explain": True, "explain_kind": "change", "refers_to": "previous"},
    "왜 이렇게 많이 썼어?": {"mine": True, "explain": True, "explain_kind": "composition", "refers_to": "previous"},
    "과자서원이랑 식비 얼마 썼어?": {"mine": True, "target": "spend",
                           "categories": [{"detail": "과자서원", "category": "FOOD"}, "FOOD"]},
    "GWAJA 얼마 썼어?": {"mine": True, "target": "spend",
                       "categories": [{"detail": "GWAJA", "category": "FOOD", "names": ["과자서원"]}]},
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
                # 남은 날 = 10/30~11/24 26일, 하루 금액 = 남은 금액 ÷ 26 (원 미만 버림)
                "식비 예산 얼마 남았어?": A_CYCLE + "식비 예산 350,000원 중 60,000원을 썼고 290,000원 남았어요. "
                                          "남은 26일 동안 하루 11,153원씩 쓸 수 있어요.",
                "예산 얼마 남았어?": A_CYCLE + "생활비 예산 900,000원 중 180,000원을 썼고 720,000원 남았어요. "
                                       "남은 26일 동안 하루 27,692원씩 쓸 수 있어요.",
                "소비 분석해줘": "이번 달(10월 1일~29일) 소비는 1,510,000원이에요. 고정지출 700,000원을 빼면 생활비는 810,000원이에요. "
                           "분류된 소비 중 가장 많이 쓴 곳은 주거·통신, 700,000원이에요. 전체 소비 1,510,000원의 46.36%예요.",
                "지난달보다 많이 썼어?": COMPARE + "전체 · 1,510,000원 지난달보다 42,000원 증가했어요. ↑ 2.86%",
                "지난달보다 식비 더 썼어?": COMPARE + "식비 · 390,000원 지난달보다 42,000원 증가했어요. ↑ 12.07%",
                "최근 몇 달 소비 어때?": "최근 3개월 전체 지출이에요. 7월 1,420,000원 8월 1,460,000원 9월 1,480,000원 "
                                "7·8·9월 세 달 평균은 약 1,453,333원이에요. 8월에는 7월보다 40,000원, 약 2.82% 더 썼어요. "
                                "9월에는 8월보다 20,000원, 약 1.37% 더 썼어요.",
                "최근 6개월 식비 어때?": "3개월까지만 볼 수 있어 3개월로 보여드려요. "
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
            # 10/30 사용자 결정: 늘어난 곳 · 줄어든 곳을 나눠 지난달 → 이번 달 금액과 함께, 전체 증감과 잇는 한 문장
            # 11/2 보완: 큰 결제 · 몰린 때(고정지출 제외) → 새로 간 곳 · 자주 가던 곳으로 전체와 잇기 → (두 달 모두 간 가게가 있어) 가게별 보조
            # → 줄이는 방법 한 줄 (가장 크게 늘어난 요인의 건당 평균 × 2번, 서버 계산)
            "이번 달 큰 결제(고정지출 제외)는 목삼화로 15,000원(10월 1일), 과자서원 15,000원(10월 2일), 범카츠건대역점 15,000원(10월 3일)이에요.",
            # 11/2: 요일은 하루 평균(요일 합계 ÷ 그 달의 그 요일 수)으로 비교, 한 묶음에서만 나온 증가는 숫자를 되풀이하지 않는다
            # 요일은 화 · 목 · 금 · 일이 모두 하루 평균 +3,000원(뚜렷한 한 요일 없음) → 요일 문장을 쓰지 않는다 (MEMO 23-18)
            "고정지출을 빼고 보면 늘어난 금액은 점심 시간대(+48,000원)에 가장 많이 몰렸어요.",
            "늘어난 금액은 모두 자주 가던 곳에서 나왔어요.",
            "많이 늘어난 곳은 범카츠건대역점(12,000원 → 60,000원), 명일탄(24,000원 → 45,000원), 삼촌네가마솥순대국(12,000원 → 30,000원)이에요.",
            "줄어든 곳은 난곡반점(60,000원 → 15,000원), 상아김밥문정 직영점(48,000원 → 15,000원), 옐로우피자(60,000원 → 30,000원)이에요.",
            "점심 시간대 식비 결제는 12번, 건당 평균 15,000원이에요. 한 달에 2번 줄이면 약 30,000원 덜 쓰게 돼요."])
        self.assertEqual(out["suggest"], ["budget_adjust"])  # 줄이는 방법을 보이면 [예산 조정하기]
        self.assertEqual(out["explain"], {"llm": False, "rejected": False, "failed": False})

    def test_explain_total_lists_categories_in_the_same_direction(self):
        a = Chat(user("A"))
        a.say("지난달보다 많이 썼어?")
        lines = a.say("왜 늘었어?")[1]["lines"]
        now, before = (EXPECTED["personas"]["A"][k]["by_category"] for k in ("current_month_known", "previous_same_dates_known"))
        up = sorted(((c, now.get(c, 0) - before.get(c, 0)) for c in set(now) | set(before) if now.get(c, 0) > before.get(c, 0)),
                    key=lambda cd: -cd[1])
        self.assertEqual(up, [("FOOD", 42_000), ("CULTURE_LEISURE", 20_000), ("FASHION_SHOPPING", 10_000)])
        self.assertIn("많이 늘어난 카테고리는 식비(348,000원 → 390,000원), 문화·여가(70,000원 → 90,000원), "
                      "패션·쇼핑(60,000원 → 70,000원)이에요.", lines)
        self.assertIn("줄어든 카테고리는 의료·건강(30,000원 → 0원)이에요.", lines)  # 줄어든 쪽도 따로 (이전엔 빠졌다)
        # 새로 간 곳 + 자주 가던 곳 − 안 간 곳 = 전체 증감 (이름 없는 결제까지 넣어 합이 맞는다)
        self.assertIn("새로 간 곳(4곳)에서 150,000원 늘고, 자주 가던 곳에서 77,000원 늘고, 이번엔 안 간 곳(5곳)에서 185,000원 줄어, "
                      "전체로는 42,000원 늘었어요.", lines)
        self.assertEqual(150_000 + 77_000 - 185_000, 42_000)
        self.assertFalse(any("월세" in line for line in lines[4:]))  # 큰 결제 · 몰린 때 · 줄이는 방법은 고정지출 제외

    def test_explain_trend_uses_latest_neighbouring_months(self):
        a = Chat(user("A"))
        a.say("최근 6개월 식비 어때?")
        self.assertEqual(a.say("왜 늘었어?")[1]["lines"][0], "식비 지출은 8월보다 10,000원 늘었어요. ↑ 2.86%")

    def test_explain_refuses_without_basis_or_after_data_changed(self):
        a = Chat(user("A"))
        a.say("식비 예산 얼마 남았어?")  # 예산 결과는 설명 근거가 아니다 (구성 · 증감 설명 없음)
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
        # 자리표시 바로 뒤 조사는 끼운 값의 받침으로 고친다. 증감은 방향까지 든 완성 단위({F1} = '42,000원 늘었어요')
        said = json.dumps({"sentences": ["건당 평균 결제액이 {F4}에서 {F5}로 바뀌었어요.", "결제는 {F3}가 있었고 전체로 {F1}."]},
                          ensure_ascii=False)
        out = explain.answer(user("A"), [item], lambda system, payload: said)
        self.assertEqual(out["lines"][2:4], ["건당 평균 결제액이 12,000원에서 15,000원으로 바뀌었어요.",
                                             "결제는 26건이 있었고 전체로 42,000원 늘었어요."])
        # 서술어까지 든 값 뒤에 말을 붙이면('{F1}했어요' → '42,000원 늘었어요했어요') 거절하고 템플릿으로
        glued = json.dumps({"sentences": ["식비 지출이 {F1}했어요."]}, ensure_ascii=False)
        out = explain.answer(user("A"), [item], lambda system, payload: glued)
        self.assertEqual((out["lines"][2], out["explain"]["rejected"]), ("결제 건수는 지난달 같은 기간 29건, 이번 달 26건이에요.", True))

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
        self.assertEqual(out["lead"], ["커피만 따로 볼 수 없어 식비 전체로 조회했어요."])
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


@fixture_db_only
class CardViewTest(TestCase):
    """카드 보기(view): 기간 · 범위는 머리줄에 한 번, 큰 금액 · 줄 목록 · 증감 글자. 숫자는 05 독립 기대값"""

    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def view(self, persona, said):
        return Chat(user(persona)).say(said)[1]["view"]

    def test_single_category_and_overview(self):
        a = EXPECTED["personas"]["A"]["current_month_known"]
        self.assertEqual(self.view("A", "식비 얼마 썼어?"), [
            {"kind": "head", "title": "10월 1일~29일 · 식비"},
            {"kind": "total", "amount": a["by_category"]["FOOD"], "note": "전체 소비 1,510,000원 중 25.83%"}])
        head, total, text, _ = self.view("A", "소비 분석해줘")  # 마지막 = 생활비 예산 막대 (LivingMeterTest)
        self.assertEqual((head["title"], total["amount"]), ("10월 1일~29일 · 소비", a["total"]))
        self.assertEqual(text["lines"], [f"고정지출 {a['fixed_amount']:,}원을 빼면 생활비는 {a['living_amount']:,}원이에요.",
                                         "분류된 소비 중 가장 많이 쓴 곳은 주거·통신이에요.", "700,000원으로 전체 소비의 46.36%예요."])
        self.assertNotIn("10월 1일", " ".join(text["lines"]))  # 기간은 머리줄에만

    def test_living_budget_meter_has_its_own_period(self):
        """1 · 5번 단독 답 아래 생활비 예산 막대: 기간은 예산 주기로 따로, 초과는 글자로도. 예산 없으면 안 붙음 · 카테고리 질문엔 없음"""
        meter = self.view("A", "이번 달 얼마 썼어?")[-1]
        a = EXPECTED["personas"]["A"]["budget"]
        self.assertEqual(meter["title"], "이번 예산 기간(10월 25일~11월 24일) · 생활비")
        self.assertEqual((meter["ratio"], meter["over"]), (str(percent(a["living_spent"], a["living_budget"])["value"]), False))
        self.assertEqual(meter["lines"], [f"{a['living_budget']:,}원 중 {a['living_spent']:,}원 사용 ({meter['ratio']}%)",
                                          f"{a['living_remaining']:,}원 남음 · 10월 29일까지"])
        b = EXPECTED["personas"]["B"]["budget"]
        meter = self.view("B", "소비 분석해줘")[-1]
        self.assertEqual((meter["kind"], meter["over"]), ("meter", True))
        self.assertEqual(meter["lines"][1], f"{b['over']:,}원 초과 · 10월 29일까지")
        self.assertNotIn("meter", [v["kind"] for v in self.view("C", "이번 달 얼마 썼어?")])
        self.assertNotIn("meter", [v["kind"] for v in self.view("A", "식비 얼마 썼어?")])

    def test_explain_card(self):
        """'왜 늘었어?' 한 항목: 큰 금액 = 증감, 줄 = 건수 · 평균 · 가맹점 (글자 값), 단정 불가 안내는 맨 아래"""
        a = Chat(user("A"))
        a.say("지난달보다 식비 더 썼어?")
        head, total, rows, big, when, visits, up, down, text = a.say("왜 늘었어?")[1]["view"]
        self.assertEqual(head["title"], "식비 · 지난달 같은 기간과 비교")
        self.assertEqual((total["caption"], total["amount"], total["note"]), ("지난달 같은 기간보다 늘어난 금액", 42_000, "12.07% 늘었어요"))
        self.assertEqual(rows["title"], "거래에서 확인한 변화 (지난달 같은 기간 → 이번 달)")
        self.assertEqual(rows["rows"][0], {"name": "결제 건수", "value": "29건 → 26건"})
        self.assertEqual((up["title"], up["rows"][0]), ("많이 늘어난 곳", {"name": "범카츠건대역점", "value": "12,000원 → 60,000원",
                                                                     "note": "+48,000원", "direction": "up"}))
        self.assertEqual((down["title"], down["rows"][0]["note"], down["rows"][0]["direction"]), ("줄어든 곳", "−45,000원", "down"))
        self.assertEqual((big["title"], when["title"], visits["title"]),
                         ("이번 달 큰 결제 (고정지출 제외)", "늘어난 금액이 몰린 때", "새로 간 곳 · 자주 가던 곳"))
        self.assertEqual(text["lines"], ["늘어난 금액은 모두 자주 가던 곳에서 나왔어요.",
                                         "점심 시간대 식비 결제는 12번, 건당 평균 15,000원이에요. 한 달에 2번 줄이면 약 30,000원 덜 쓰게 돼요.",
                                         "위 변화는 거래 내역에서 확인한 것이라, 이것만으로 이유를 단정할 수는 없어요."])

    def test_compare_rows_have_words_not_only_colour(self):
        head, rows = self.view("A", "지난달보다 식비랑 주거비 더 썼어?")
        self.assertEqual(head["title"], "10월 1일~29일 · 지난달 같은 기간과 비교")
        self.assertEqual([(r["name"], r["amount"], r["badge"]) for r in rows["rows"]], [
            ("식비", 390_000, {"text": "42,000원 증가 (12.07%)", "direction": "up"}),
            ("주거·통신", 700_000, {"text": "변화 없음", "direction": "same"})])
        head, total, rows = self.view("A", "지난달보다 식비 더 썼어?")  # 한 항목: 차이를 크게 + 두 기간 막대
        self.assertEqual(head["title"], "식비 · 지난달 같은 기간과 비교")
        self.assertEqual(total, {"kind": "total", "caption": "지난달보다 더 쓴 금액", "amount": 42_000, "note": "12.07% 늘었어요",
                                 "direction": "up"})
        self.assertEqual([(r["name"], r["amount"], r["note"], r["ratio"]) for r in rows["rows"]], [
            ("이번 달", 390_000, "10월 1일~29일", "100.00"), ("지난달", 348_000, "9월 1일~29일", "89.23")])
        head, *_ = self.view("A", "지난달보다 많이 썼어?")
        self.assertEqual(head["title"], "전체 소비 · 지난달 같은 기간과 비교")
        head, rows = self.view("C", "지난달보다 식비 더 썼어?")  # 비교 불가: 참고 금액만 (핵심 안내는 lead)
        self.assertEqual((head["title"], rows["rows"]), ("10월 1일~29일 · 이번 달 금액",
                                                         [{"name": "식비", "amount": 110_000, "note": "일부 내역 기준"}]))

    def test_trend_rows_and_budget_total(self):
        sums = [EXPECTED["personas"]["A"]["monthly_known_sums"][m]["by_category"]["FOOD"] for m in ("7", "8", "9")]
        head, rows, text = self.view("A", "최근 6개월 식비 어때?")
        self.assertEqual(head["title"], "7월~9월 · 식비")
        self.assertEqual([(r["name"], r["amount"]) for r in rows["rows"]], list(zip(("7월", "8월", "9월"), sums)))
        self.assertEqual(rows["rows"][-1]["ratio"], "100.00")  # 막대 길이 = 가장 큰 달 대비
        self.assertEqual(text["lines"][0], "7·8·9월 세 달 평균은 약 346,667원이에요.")
        self.assertEqual(self.view("C", "최근 몇 달 소비 어때?"),
                         [{"kind": "head", "title": "7월~9월 · 전체 소비"}, {"kind": "text", "lines": ["확인할 수 있는 내역이 없어요."]}])
        b = EXPECTED["personas"]["B"]["budget"]
        head, total, spare = self.view("B", "예산 얼마 남았어?")
        self.assertEqual(head["title"], "10월 10일~11월 9일 · 생활비 예산")
        self.assertEqual((total["caption"], total["amount"]), ("초과한 금액", b["over"]))
        self.assertEqual(total["note"], f"예산 {b['living_budget']:,}원 중 {b['living_spent']:,}원 사용 · 10월 29일까지")
        self.assertEqual(spare["lines"], ["초과한 62,000원은 여윳돈으로 메울 수 있어요. 남는 여윳돈은 138,000원이에요."])

    def test_failed_item_is_kept_as_text(self):
        def flaky(u, item, **kw):
            return {"state": "error", "reason": "db_error"} if item["category"] == "TRANSPORT_CAR" else analyze(u, item, **kw)
        with mock.patch("spending_analysis.adapter.analyze", side_effect=flaky):
            view = Chat(user("A")).say("식비랑 교통비 얼마 썼어?")[1]["view"]
        self.assertEqual(view[-1], {"kind": "text", "lines": ["교통·차량 소비 결과를 불러오지 못했어요."]})
        self.assertEqual([r["name"] for r in view[1]["rows"]], ["식비"])


@fixture_db_only
class CardMerchantExplainTest(TestCase):
    """카드 결제만 · 가게 이름 검색 · 직전 결과에 없는 카테고리 설명(새로 비교) · 구성 설명 (A: 10월 카드 결제 810,000 =
    전체 1,510,000 − 계좌로 나간 월세 600,000 · 관리비 50,000 · 통신료 50,000 / 과자서원(상가정보 상호) 5건 75,000)"""

    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def test_card_only_answer_and_follow_up_keeps_card(self):
        a = Chat(user("A"))
        _, out = a.say("이번 달 카드 얼마 긁었지?")
        self.assertEqual(out["lines"], ["이번 달(10월 1일~29일) 카드 결제에 810,000원 썼어요."])
        self.assertIn("계좌 이체 · 자동이체는 빼고 카드 결제만 모았어요.", out["notices"])
        self.assertEqual(out["view"][0]["title"], "10월 1일~29일 · 카드 결제")
        self.assertNotIn("meter", [v["kind"] for v in out["view"]])  # 생활비 막대는 전체 소비 답에만
        _, out = a.say("그럼 교통비는?")  # 이어받기: 카드 결제만 그대로
        self.assertEqual(out["lines"], ["이번 달(10월 1일~29일) 교통·차량 카드 결제에 90,000원 썼어요."])

    def test_merchant_search_or_fallback(self):
        a = Chat(user("A"))
        _, out = a.say("과자서원에서 얼마 썼어?")
        self.assertEqual(out["lead"], ["가게 이름에 '과자서원'이 들어간 결제만 모았어요."])
        self.assertEqual(out["lines"], ["이번 달(10월 1일~29일) 과자서원에 75,000원 썼어요."])
        _, out = a.say("커피 얼마나 썼어?")  # 이름에 '커피'가 들어간 결제가 없다 → 기존처럼 식비 전체로 볼지 묻는다
        self.assertIsNone(out)

    def test_explain_category_missing_from_last_result_compares_fresh(self):
        a = Chat(user("A"))
        a.say("지난달보다 식비 더 썼어?")
        a.say("그럼 교통비는?")
        _, out = a.say("식비는 왜 늘었을까?")
        self.assertEqual(out["lines"][0], "식비 지출은 지난달 같은 기간보다 42,000원 늘었어요. ↑ 12.07%")
        self.assertEqual(out["view"][0]["title"], "식비 · 지난달 같은 기간과 비교")

    def test_explain_overview_explains_top_category_mix(self):
        a = Chat(user("A"))
        a.say("소비 분석해줘")
        _, out = a.say("제일 많이 쓴 데는 왜 많이 나왔을까?")
        self.assertEqual(out["lines"], [
            "이번 달 가장 많이 쓴 주거·통신 기준으로 봤어요.", "이번 달(10월 1일~29일) 주거·통신에 700,000원 썼어요.",
            "결제는 3건, 건당 평균 233,333원이에요.", "많이 쓴 곳은 월세 600,000원, 관리비 50,000원, 통신요금 50,000원이에요.",
            "이 중 700,000원은 매달 나가는 고정지출이에요.", "거래 내역에서 확인한 구성이에요. 이것만으로 이유를 단정할 수는 없어요."])
        head, total, counts, places, text = out["view"]
        self.assertEqual((head["title"], total["amount"]), ("주거·통신 · 이번 달 쓴 곳", 700_000))
        self.assertEqual([(r["name"], r["amount"]) for r in places["rows"]][0], ("월세", 600_000))


@fixture_db_only
class BudgetBreakdownTest(TestCase):
    """카테고리별 남은 예산 · 하루에 쓸 수 있는 금액 · 카테고리별 소비 · 가게 이름 다른 표기
    (A: 예산 주기 10/25~11/24, 10/29 까지 사용 → 남은 날 10/30~11/24 = 26일)"""

    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def test_daily_allowance(self):
        b = EXPECTED["personas"]["A"]["budget"]
        _, out = Chat(user("A")).say("남은 기간 동안 생활비 하루에 얼마 쓸 수 있어?")
        daily = b["living_remaining"] // 26
        self.assertIn(f"남은 26일 동안 하루 {daily:,}원씩 쓸 수 있어요.", out["lines"])
        self.assertIn({"kind": "text", "lines": [f"남은 26일 동안 하루 {daily:,}원씩 쓸 수 있어요."]}, out["view"])
        _, out = Chat(user("B")).say("남은 기간 동안 생활비 하루에 얼마 쓸 수 있어?")  # 초과면 하루 금액 없음
        self.assertNotIn("하루", " ".join(out["lines"]))

    def test_budget_by_category(self):
        b = EXPECTED["personas"]["A"]["budget"]
        _, out = Chat(user("A")).say("카테고리별로 예산 얼마나 남았어?")
        rows = next(v for v in out["view"] if v.get("title") == "카테고리별 남은 예산")["rows"]
        food = next(r for r in rows if r["name"] == "식비")
        self.assertEqual((food["amount"], food["note"]), (b["food_signed_difference"], f"남음 · 예산 {b['food_budget']:,}원"))
        self.assertEqual(food["ratio"], str(percent(b["food_spent"], b["food_budget"])["value"]))
        self.assertEqual(out["sections"][0]["title"], "카테고리별 남은 예산")

    def test_follow_up_by_category_widens_scope(self):
        a = Chat(user("A"))
        a.say("식비 예산 얼마 남았어?")
        _, out = a.say("그럼 카테고리별로는?")  # 직전 '식비'를 이어받지 않고 카테고리별 남은 예산 전체
        self.assertIn("카테고리별 남은 예산", [v.get("title") for v in out["view"]])
        self.assertEqual(out["view"][0]["title"], "10월 25일~11월 24일 · 생활비 예산")

    def test_spend_by_category_is_overview_chart(self):
        _, out = Chat(user("A")).say("카테고리별로 얼마 썼어?")
        self.assertEqual(out["chart"]["rows"][0]["name"], "주거·통신")

    def test_merchant_other_spelling(self):
        _, out = Chat(user("A")).say("GWAJA 얼마 썼어?")  # 'GWAJA' 는 이름에 없고 다른 표기 '과자서원'으로 찾는다
        self.assertEqual(out["lines"], ["이번 달(10월 1일~29일) GWAJA에 75,000원 썼어요."])


@fixture_db_only
class ExplainKindTest(TestCase):
    """설명 종류(change · composition)가 직전 결과 종류와 다르면 같은 범위로 바꿔 본다 · 섞인 결과의 설명"""

    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def test_change_after_this_month_result_compares(self):
        a = Chat(user("A"))
        a.say("식비 얼마 썼어?")
        _, out = a.say("그럼 왜 늘었는데?")  # 이번 달 결과 + '늘었' → 이번 달 vs 지난달 같은 기간 새 비교
        self.assertEqual(out["lines"][0], "식비 지출은 지난달 같은 기간보다 42,000원 늘었어요. ↑ 12.07%")

    def test_composition_after_compare_shows_this_month_mix(self):
        a = Chat(user("A"))
        a.say("지난달보다 식비 더 썼어?")
        _, out = a.say("왜 이렇게 많이 썼어?")  # 비교 결과 + '많이' → 그 비교의 이번 달 구성
        self.assertEqual(out["lines"][:2], ["이번 달(10월 1일~29일) 식비에 390,000원 썼어요.", "결제는 26건, 건당 평균 15,000원이에요."])
        self.assertEqual(out["view"][0]["title"], "식비 · 이번 달 쓴 곳")

    def test_mixed_merchant_and_category_results_are_all_explained(self):
        a = Chat(user("A"))
        a.say("과자서원이랑 식비 얼마 썼어?")
        _, out = a.say("왜 늘었어?")  # 고를 수 없는 결과가 섞임 → 묻지 않고 둘 다 (이전엔 서버 오류)
        self.assertEqual([l for l in out["lines"] if l.startswith("이번 달(")],
                         ["이번 달(10월 1일~29일) 과자서원에 75,000원 썼어요.", "이번 달(10월 1일~29일) 식비에 390,000원 썼어요."])
        self.assertNotIn("view", out)  # 여러 항목 설명은 카드 없이 문장


@fixture_db_only
class DeliveryAppTest(TestCase):
    """온라인 서비스 별칭 표: '배달' = 배달앱 브랜드 전부, '배민' = 배달의민족 표기만 (운영사 이름 포함). '쿠팡'(쇼핑)은 배달앱 아님"""

    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)
        card = Card.objects.filter(user=user("A")).first()
        for i, (name, amount) in enumerate((("(주)우아한형제들", 23_000), ("쿠팡이츠", 18_000), ("쿠팡", 50_000))):
            FinancialTransaction.objects.create(
                user=user("A"), source_kind="card", card=card, source_key=f"fixture:delivery-{i}", occurred_on=date(2026, 10, 20),
                occurred_time=time(19), amount=amount, direction="out", transaction_type="spending", merchant_name=name,
                confirmed_category_id="FASHION_SHOPPING" if name == "쿠팡" else "FOOD", confirmation_state="confirmed")

    def test_delivery_group_and_single_brand(self):
        _, out = Chat(user("A")).say("배달 얼마 썼어?")
        self.assertEqual(out["lead"], ["배달의민족 · 쿠팡이츠 · 요기요 등 배달앱 결제만 모았어요."])
        self.assertEqual(out["lines"], ["이번 달(10월 1일~29일) 배달앱에 41,000원 썼어요."])  # 쿠팡 50,000 은 빠짐
        self.assertIn("앱 결제 금액이라 음식값과 배달비가 함께 들어 있어요.", out["notices"])
        _, out = Chat(user("A")).say("배민 얼마 썼어?")
        self.assertEqual(out["lines"], ["이번 달(10월 1일~29일) 배민에 23,000원 썼어요."])


QUERY_EXPECTED = json.loads((Path(seed.__file__).parent / "data" / "expected_query.json").read_text(encoding="utf-8"))
EXP = QUERY_EXPECTED["A"]


@fixture_db_only
class GeneralQueryTest(TestCase):
    """범용 조회(MEMO 23절 2단계) — 가짜 해석 → 경로 A → 답 카드. 숫자는 make_expected_query.py 가 design.json 에서 따로 계산한 값"""

    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def view(self, said, persona="A"):
        out = Chat(user(persona)).say(said)[1]
        return out, {b["kind"]: b for b in out["view"]}

    def test_b_duplicate_and_cancels(self):
        """민재: 같은 구매의 계좌 쪽은 빠지고, 부분 취소는 순금액, 전액 취소는 건수에서 빠진다"""
        e = QUERY_EXPECTED["B"]
        _, b = self.view("패션 쇼핑 몇 건 결제했어?", "B")
        self.assertEqual((b["total"]["value"], b["total"]["amount"]),
                         (f"{e['패션 쇼핑 몇 건 결제했어?']['count']}건", e["패션 쇼핑 몇 건 결제했어?"]["sum"]))
        _, b = self.view("패션 쇼핑 한 번에 평균 얼마야?", "B")
        self.assertEqual(b["total"]["amount"], e["패션 쇼핑 한 번에 평균 얼마야?"]["avg"])
        out, b = self.view("이번 달 제일 큰 지출 뭐야?", "B")
        self.assertEqual(b["total"]["amount"], e["이번 달 제일 큰 지출 뭐야?"]["max"][3])
        self.assertIn(f"쓴 {e['이번 달 제일 큰 지출 뭐야?']['max_living'][3]:,}원이에요.", out["lines"][-1])
        _, b = self.view("가게별 상위 3곳 보여줘", "B")
        self.assertEqual([[r["name"], r["amount"], int(r["note"][:-1])] for r in b["rows"]["rows"]],
                         e["가게별 상위 3곳 보여줘"]["groups"])
        _, b = self.view("요일별로 몇 번 결제했어?", "B")
        self.assertEqual([[r["name"], int(r["value"][:-1]), r["amount"]] for r in b["rows"]["rows"]],
                         e["요일별로 몇 번 결제했어?"]["groups"])
        _, b = self.view("하루 평균 얼마 썼어?", "B")
        self.assertEqual((b["total"]["amount"], b["total"]["note"]),
                         (e["하루 평균 얼마 썼어?"]["daily_avg"], f"고정지출 빼면 하루 {e['하루 평균 얼마 썼어?']['daily_avg_living']:,}원"))

    def test_c_partial_unclassified_and_empty_month(self):
        """지우: 이번 달은 일부 수집(값은 관측값 + '일부 내역 기준'), 확인 필요(UNCLASSIFIED) 포함, 지난달은 자료 없음(0원이 아님)"""
        e, names = QUERY_EXPECTED["C"], DESIGN["category_names"]
        self.assertEqual((e["state"], e["last_month_state"]), ("partial", "none"))
        out, b = self.view("이번 달 결제 몇 건이야?", "C")
        self.assertEqual((b["total"]["value"], b["total"]["amount"], b["total"]["note"]),
                         (f"{e['이번 달 결제 몇 건이야?']['count']}건", e["이번 달 결제 몇 건이야?"]["sum"], "일부 내역 기준"))
        self.assertIn("일부 내역이 빠져 있을 수 있어요. 현재 확인된 내역으로 계산했어요.", out["notices"])
        _, b = self.view("카테고리별로 몇 번 썼어?", "C")
        self.assertEqual([[r["name"], int(r["value"][:-1]), r["amount"]] for r in b["rows"]["rows"]],
                         [[names[c], n, a] for c, n, a in e["카테고리별로 몇 번 썼어?"]["groups"]])
        _, b = self.view("가게별 상위 3곳 보여줘", "C")
        self.assertEqual([[r["name"], r["amount"], int(r["note"][:-1])] for r in b["rows"]["rows"]],
                         e["가게별 상위 3곳 보여줘"]["groups"])
        _, b = self.view("이번 달 제일 큰 지출 뭐야?", "C")
        self.assertEqual(b["total"]["amount"], e["이번 달 제일 큰 지출 뭐야?"]["max"][3])
        out, _ = self.view("지난달 식비 얼마 썼어?", "C")
        self.assertEqual(e["지난달 식비 얼마 썼어?"], {"sum": 0, "rows": 0})
        self.assertEqual(out["lines"], ["지난달(9월 1일~30일) 식비 지출은 확인할 수 있는 내역이 없어요."])

    def test_sums_with_conditions(self):
        for said in ("주말 식비 얼마 썼어?", "밤에 쓴 돈 얼마야?", "야식으로 얼마 썼어?", "지난달 식비 얼마 썼어?", "계좌이체로 쓴 돈 얼마야?"):
            with self.subTest(said):
                out, blocks = self.view(said)
                self.assertEqual(blocks["total"]["amount"], EXP[said]["sum"])
                self.assertIn(f"{EXP[said]['sum']:,}원", " ".join(out["lines"]))
        out, blocks = self.view("주말 식비 얼마 썼어?")
        self.assertEqual(blocks["head"]["title"], "10월 1일~29일 · 식비 · 주말")
        self.assertIsNone(blocks["total"]["note"])  # 거른 조회엔 '전체 소비 중 n%' 를 붙이지 않는다 (분모가 다름)
        out, blocks = self.view("지난달 식비 얼마 썼어?")
        self.assertEqual(out["lines"], [f"지난달(9월 1일~30일) 식비에 {EXP['지난달 식비 얼마 썼어?']['sum']:,}원 썼어요."])
        out, _ = self.view("계좌이체로 쓴 돈 얼마야?")
        self.assertIn("카드 결제는 빼고 계좌에서 나간 소비만 모았어요.", out["notices"])

    def test_count_avg_daily_max(self):
        _, b = self.view("과자서원 몇 번 갔어?")
        self.assertEqual((b["total"]["value"], b["total"]["amount"]),
                         (f"{EXP['과자서원 몇 번 갔어?']['count']}건", EXP["과자서원 몇 번 갔어?"]["sum"]))
        _, b = self.view("만 원 넘게 쓴 거 몇 번이야?")
        self.assertEqual(b["total"]["value"], f"{EXP['만 원 넘게 쓴 거 몇 번이야?']['count']}건")
        _, b = self.view("식비 한 번에 평균 얼마 써?")
        self.assertEqual(b["total"]["amount"], EXP["식비 한 번에 평균 얼마 써?"]["avg"])
        want = EXP["하루 평균 얼마 썼어?"]
        _, b = self.view("하루 평균 얼마 썼어?")
        self.assertEqual((b["total"]["amount"], b["total"]["caption"], b["total"]["note"]),
                         (want["daily_avg"], f"하루 평균 ({want['days']}일 · 고정지출 포함)", f"고정지출 빼면 하루 {want['daily_avg_living']:,}원"))
        day, time_, merchant, amount = EXP["이번 달 제일 큰 지출 뭐야?"]["max"]
        out, b = self.view("이번 달 제일 큰 지출 뭐야?")
        self.assertEqual((b["total"]["amount"], b["total"]["caption"]), (amount, "가장 큰 지출 (고정지출)"))
        self.assertTrue(b["total"]["note"].startswith(f"{int(day[5:7])}월 {int(day[8:])}일 {time_} · {merchant}"))
        day, time_, merchant, amount = EXP["이번 달 제일 큰 지출 뭐야?"]["max_living"]  # 고정지출을 뺀 가장 큰 지출 한 줄
        self.assertIn(f"고정지출을 빼면 가장 큰 지출은 {int(day[5:7])}월 {int(day[8:])}일 {time_} {merchant}에서 쓴 {amount:,}원이에요.",
                      out["lines"])

    def test_groups(self):
        _, b = self.view("자주 간 가게 3곳 알려줘")
        self.assertEqual([[r["name"], int(r["value"][:-1]), r["amount"]] for r in b["rows"]["rows"]],
                         EXP["자주 간 가게 3곳 알려줘"]["groups"])
        for said in ("요일별 소비 보여줘", "시간대별로 보여줘"):  # 정해진 순서(월~일 · 새벽~밤) 그대로
            with self.subTest(said):
                _, b = self.view(said)
                self.assertEqual([[r["name"], r["amount"], int(r["note"][:-1])] for r in b["rows"]["rows"]],
                                 EXP[said]["groups"])

    def test_budget_daily_reading_stays_budget(self):
        """해석이 남은 예산에 하루 평균(daily_avg)을 붙여도 예산 답(하루 쓸 수 있는 금액 포함) — 미지원 안내로 보내지 않는다"""
        said = lambda system, user: json.dumps({"mine": True, "target": "budget_left", "categories": ["FOOD"], "measure": "daily_avg"}  # noqa: E731
                                                if system == spending_reading.PROMPT else {"intents": ["spending"]})
        out = Chat(user("A")).say("남은 기간 식비 하루 얼마", llm=said)[1]
        self.assertTrue(any(l.startswith("남은 26일 동안 하루") for l in out["lines"]))

    def test_unsupported_and_no_store(self):
        a = Chat(user("A"))
        self.assertIsNone(a.say("배달 몇 번 시켰어?")[1])  # 배달앱 결제 없음 → 식비 전체로 볼지 되묻기 (답 카드 없음)
        _, out = Chat(user("A")).say("예산 밤 시간대로 보여줘")
        self.assertIsNone(out)  # 미지원 안내는 라우터 문구 (messages.UNSUPPORTED budget_query)

    def test_follow_up_keeps_conditions(self):
        a = Chat(user("A"))
        a.say("주말 식비 얼마 썼어?")
        out = a.say("그럼 교통비는?")[1]  # 주말 조건 이어받기
        self.assertEqual(out["view"][0]["title"], "10월 1일~29일 · 교통·차량 · 주말")


@fixture_db_only
class PeriodAndFixedTest(TestCase):
    """사용자 테스트 보완 10/30: 지정 기간(이번 주 · 지난주 · 특정 달 · 날짜 범위) · 볼 수 없는 기간 안내 · 고정지출 기본 제외"""

    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def test_named_periods(self):
        for said, head in (("이번 주 얼마 썼어?", "이번 주(10월 26일~29일)"), ("지난주 식비 얼마야?", "지난주(10월 19일~25일)"),
                           ("8월 식비 얼마 썼어?", "8월(8월 1일~31일)"), ("10월 3일부터 10일까지 얼마 썼어?", "10월 3일~10일")):
            with self.subTest(said):
                out = Chat(user("A")).say(said)[1]
                total = next(b for b in out["view"] if b["kind"] == "total")
                self.assertEqual(total["amount"], EXP[said]["sum"])
                self.assertTrue(out["lines"][0].startswith(head), out["lines"][0])

    def test_short_period_total_shows_fixed_separately(self):
        want = EXP["이번 주 얼마 썼어?"]
        out = Chat(user("A")).say("이번 주 얼마 썼어?")[1]
        line = next(l for l in out["lines"] if l.startswith("같은 기간 고정지출"))
        self.assertEqual(line, f"같은 기간 고정지출 {want['fixed']:,}원은 따로 뺐어요.")  # 11/2 2차: 항목 이름 없이 금액만
        self.assertIn(line, [l for b in out["view"] if b["kind"] == "text" for l in b["lines"]])
        self.assertEqual(out["suggest"], ["include_fixed"])
        self.assertNotIn("suggest", Chat(user("A")).say("8월 식비 얼마 썼어?")[1])  # 달 단위 총액은 그대로 포함

    def test_unavailable_period_asks_with_choices_not_silently(self):
        for said, word in (("5월 식비 얼마 썼어?", "5월"), ("작년 식비 얼마 썼어?", "작년")):
            with self.subTest(said):
                a = Chat(user("A"))
                self.assertIsNone(a.say(said)[1])  # 조회하지 않고 되묻는다
                asked = dialog.pending(a.store, "talk-1")
                self.assertEqual((asked["asking"], asked["ask_about"]), ("period_choice", [word, "7월", "10월"]))
                self.assertEqual([o["label"] for o in asked["options"]], ["이번 달", "지난달", "최근 3개월"])
        self.assertIsNone(Chat(user("A")).say("지난주보다 더 썼어?")[1])  # 기간끼리 비교는 안내만 (period_compare)

    def test_fixed_excluded_by_default_with_notice_and_choice(self):
        a = Chat(user("A"))
        out = a.say("요일별 소비 보여줘")[1]
        self.assertIn(f"고정지출 {EXP['fixed_this_month']:,}원(월세 · 관리비 · 통신요금)은 빼고 봤어요.", out["notices"])
        self.assertEqual(out["suggest"], ["include_fixed"])
        out = a.say("고정지출 포함해서 보기")[1]  # 선택지 이름 그대로 → 직전 조건 + 고정지출 포함
        rows = next(b for b in out["view"] if b["kind"] == "rows")["rows"]
        self.assertEqual(sum(r["amount"] for r in rows), EXPECTED["personas"]["A"]["current_month_known"]["total"])
        self.assertNotIn("suggest", out)
        out = Chat(user("A")).say("이번 달 얼마 썼어?")[1]  # 총액은 지금처럼 포함
        self.assertNotIn("suggest", out)


@fixture_db_only
class PeriodGuardTest(TestCase):
    """10/30 보완 2~4: 기간을 버리는 경로 막기 · 볼 수 있는 범위를 말하는 안내 · 다른 사람의 소비"""

    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def test_range_with_category_wise_keeps_range(self):
        out = Chat(user("A")).say("10월 5일부터 10일까지 카테고리별로 얼마 썼어?")[1]  # 예전: 10월 전체 전반 분석으로 답함
        self.assertTrue(out["lines"][0].startswith("10월 5일~10일"), out["lines"][0])
        self.assertEqual(next(b for b in out["view"] if b["kind"] == "rows")["title"], "카테고리별")

    def test_period_said_must_match_items(self):
        said = spending_reading.parse({"mine": True, "period": "range", "date_from": "10-05", "date_to": "10-10",
                                        "period_said": "10월 5일부터 10일까지"})
        self.assertTrue(dialog._period_differs(said, [{"type": 5, "category": None}]))  # 이번 달 전체로 바뀌면 걸린다
        self.assertFalse(dialog._period_differs(said, [{"type": 10, "category": None, "query": {"period": "range"}}]))

    def test_unavailable_period_message_says_real_range(self):
        from chat.router_core import messages as router_messages
        out = router_messages._period_ask("period_unavailable", ("5월", "7월", "10월"), [{"value": "x", "label": "이번 달"}])
        self.assertEqual(out["lines"], ["5월은 볼 수 없어요. 지금은 7월부터 이번 달(10월)까지 볼 수 있어요.", "어느 기간을 볼까요?"])
        out = router_messages._period_ask("period_unavailable", ("작년 이맘때", "7월", "10월"), [])
        self.assertEqual(out["lines"][0], "작년 이맘때는 볼 수 없어요. 지금은 7월부터 이번 달(10월)까지 볼 수 있어요.")

    def test_other_person_offers_mine(self):
        a = Chat(user("A"))
        self.assertIsNone(a.say("엄마가 이번 달 식비 얼마 썼어?")[1])
        asked = dialog.pending(a.store, "talk-1")
        self.assertEqual((asked["asking"], [o["label"] for o in asked["options"]]), ("self_instead", ["내 식비 보기", "취소"]))
        out = a.say("내 식비 보기")[1]  # 이름 그대로 → 같은 조건으로 내 식비
        self.assertEqual(out["lines"][0], "이번 달(10월 1일~29일) 식비에 390,000원 썼어요.")


@fixture_db_only
class BudgetScopeTest(TestCase):
    """11/2 예산 범위 변경(임시 (나)): 주거·통신 · 여행·숙박은 예산 범위 밖 — 예산 질문은 안내 + 사용액 제안, 생활비 값은 그대로"""

    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def test_out_of_budget_category_offers_spend(self):
        from chat.router_core import messages as router_messages
        a = Chat(user("A"))
        self.assertIsNone(a.say("주거비 예산 얼마 남았어?")[1])
        asked = dialog.pending(a.store, "talk-1")
        self.assertEqual((asked["asking"], [o["label"] for o in asked["options"]]), ("spend_instead", ["이번 달 주거·통신 보기", "취소"]))
        out = router_messages._ask("spend_instead", "out_of_budget", ["HOUSING_TELECOM"], asked["options"], False)
        self.assertEqual(out["lines"], ["주거·통신은 예산 범위가 아니에요.", "대신 이번 달 주거·통신 사용액을 볼까요?"])
        out = a.say("이번 달 주거·통신 보기")[1]
        self.assertEqual(out["lines"][0], "이번 달(10월 1일~29일) 주거·통신에 700,000원 썼어요.")

    def test_mixed_budget_question_answers_in_scope_only(self):
        decision, out = Chat(user("A")).say("식비랑 여행 예산 얼마 남았어?")
        self.assertEqual(out["view"][0]["title"], "10월 25일~11월 24일 · 식비 예산")  # 범위 안(식비)만 답한다
        self.assertEqual(list(decision.skipped), [{"category": "TRAVEL_LODGING", "reason": "out_of_budget"}])

    def test_living_values_unchanged(self):
        b = EXPECTED["personas"]["A"]["budget"]
        _, out = Chat(user("A")).say("예산 얼마 남았어?")
        self.assertIn(f"생활비 예산 {b['living_budget']:,}원 중 {b['living_spent']:,}원을 썼고 {b['living_remaining']:,}원 남았어요.",
                      out["lines"])
        self.assertNotIn("주거·통신, 여행·숙박은 예산 범위가 아니라 생활비 사용액에서 뺐어요.", out["notices"])  # 이번 주기 해당 지출 0원


@fixture_db_only
class OmittedDateTest(TestCase):
    """11/2 2차: 월 · 연도를 생략한 날짜는 기준일로 채운다 · 끝이 기준일 뒤면 자르고 안내 · 시작이 뒤면 지난달 같은 날짜 선택지"""

    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def test_day_only_range_uses_this_month(self):
        out = Chat(user("A")).say("20일부터 27일까지 쓴 돈 얼마야?")[1]  # 예전: 볼 수 없는 기간 안내
        self.assertTrue(out["lines"][0].startswith("10월 20일~27일"), out["lines"][0])

    def test_end_after_today_is_clipped_with_notice(self):
        out = Chat(user("A")).say("25일부터 31일까지 얼마 썼어?")[1]
        self.assertTrue(out["lines"][0].startswith("10월 25일~29일"), out["lines"][0])
        self.assertIn("끝 날짜가 아직 지나지 않아 10월 29일까지만 봤어요.", out["notices"])

    def test_start_after_today_offers_last_month_same_days(self):
        from chat.router_core import messages as router_messages
        a = Chat(user("A"))
        self.assertIsNone(a.say("30일부터 31일까지 얼마 썼어?")[1])
        asked = dialog.pending(a.store, "talk-1")
        self.assertEqual((asked["asking"], [o["label"] for o in asked["options"]]),
                         ("period_choice", ["지난달 같은 날짜(9월 30일)", "이번 달", "지난달"]))
        out = router_messages._period_ask("period_future", asked["ask_about"], asked["options"])
        self.assertEqual(out["lines"], ["30일부터 31일까지는 아직 지나지 않은 기간이에요.", "어느 기간을 볼까요?"])
        out = a.say("지난달 같은 날짜(9월 30일)")[1]  # 이름 그대로 → 그 날짜로 조회 (말한 기간 검사와 어긋나지 않음)
        self.assertTrue(out["lines"][0].startswith("9월 30일"), out["lines"][0])

    def test_clear_day_rule(self):
        row = lambda k, d: {"key": k, "name": f"{k}", "before": 0, "after": d, "delta": d}  # noqa: E731
        self.assertIsNone(explain._clear_day([row(0, 3000), row(1, 3000)], 1))  # 같은 값 여럿
        self.assertIsNone(explain._clear_day([row(0, 3000), row(1, 2500)], 1))  # 차이가 작다
        self.assertIsNone(explain._clear_day([row(0, 900)], 1))  # 너무 작다
        self.assertEqual(explain._clear_day([row(0, 6000), row(1, 2000), row(2, -5000)], 1)["key"], 0)

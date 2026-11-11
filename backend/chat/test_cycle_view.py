"""소비 분석 답의 '예산 주기' 보기와 초과한 카테고리 답 (11/11). 실제 메시지 API · 실제 두 에이전트 · 가짜 LLM(정해 둔 문장)
- 이번 달 조회(달력 한 달)에 같은 조회의 예산 주기 판(alt)이 함께 온다 — 화면이 [이번 달 | 예산 주기]로 바꿔 보여 준다
- "초과한 카테고리 뭐야?" → 카테고리별 남은 예산 + 예산을 넘은 카테고리 한 줄 + [예산 조정하기]
"""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from budgets.models import BudgetCycle
from chat.test_agent_collab import SETTINGS, CollabFixture
from finance.models import Card, Connection, FinancialTransaction, SyncRun


@override_settings(**SETTINGS)
class CycleViewTest(CollabFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.start = self.today - timedelta(days=40)  # 주기가 지난달(또는 그 전)에 시작 — 달력 한 달과 다른 기간
        BudgetCycle.objects.filter(user=self.user).update(starts_on=self.start)
        link = Connection.objects.create(user=self.user, business_type="card", organization_code="FIXTURE_CARD", status="connected")
        self.card = Card.objects.create(user=self.user, connection=link, origin="provider", source_card_key="cycle-view")
        SyncRun.objects.create(user=self.user, connection=link, card=self.card, product_code="card_approval_list", status="success",
                               covered_from=self.start, covered_to=self.today)

    def spend(self, day, amount, category="FOOD"):
        FinancialTransaction.objects.create(
            user=self.user, occurred_on=day, amount=amount, direction="out", transaction_type="spending",
            confirmed_category_id=category, confirmation_state="confirmed", source_kind="card", card=self.card)

    @staticmethod
    def total(view):
        return next(block["amount"] for block in view if block["kind"] == "total")

    def test_this_month_answer_carries_the_budget_cycle_view(self):
        self.spend(self.start + timedelta(days=1), 70000)  # 주기 안 · 이번 달 밖
        self.spend(self.today, 30000)
        data, answer = self.say("이번 달 얼마 썼어?")
        self.assertEqual(data["agent"], "spending")
        self.assertEqual(self.total(answer["view"]), 30000)  # 달력 이번 달
        alt = answer["alt"]
        self.assertEqual((alt["label"], alt["baseLabel"]), ("예산 주기", "이번 달"))
        self.assertEqual(self.total(alt["view"]), 100000)  # 주기 시작일 ~ 오늘
        self.assertIn(f"{self.start.month}월 {self.start.day}일", alt["view"][0]["title"])
        self.assertIn("이번 예산 기간(", alt["lines"][0])
        self.assertNotIn("first", alt)  # 넘겨 둔 예산 조정이 없으면 이번 달을 먼저 보인다

    def test_no_cycle_view_when_the_cycle_is_the_calendar_month(self):
        BudgetCycle.objects.filter(user=self.user).update(starts_on=self.today.replace(day=1))
        self.spend(self.today, 30000)
        data, answer = self.say("이번 달 얼마 썼어?")
        self.assertNotIn("alt", answer)

    def test_cycle_view_comes_first_while_a_budget_adjustment_is_kept(self):
        """예산 조정 중에 소비를 물으면(넘겨 둔 조정) 예산 조정과 같은 기준인 예산 주기를 먼저 보인다"""
        self.spend(self.start + timedelta(days=1), 70000)
        self.proposal()
        self.say("교통은 얼마 남았어?")  # 조정 중 조회 → 소비 분석으로 넘어옴 (조정은 넘겨 둔 채)
        data, answer = self.say("이번 달 얼마 썼어?")
        self.assertEqual(data["agent"], "spending")
        self.assertIs(answer["alt"]["first"], True)

    def test_over_categories_question_lists_them_and_offers_adjustment(self):
        self.spend(self.today, 130000, "TRANSPORT_CAR")  # 교통 예산 100,000원 → 30,000원 초과
        self.ctx["spent"]["TRANSPORT_CAR"] = 130000
        data, answer = self.say("초과한 카테고리 뭐야?")
        self.assertEqual(data["agent"], "spending")
        line = "예산을 넘은 카테고리: 교통·차량 30,000원 초과"
        self.assertIn(line, answer["text"])
        self.assertIn(line, [text for block in answer["view"] if block["kind"] == "text" for text in block["lines"]])
        self.assertIn({"value": "budget", "label": "예산 조정하기"}, answer["choices"])  # 생활비 전체는 안 넘어도 제안한다
        data, started = self.pick(answer, "budget")
        self.assertEqual((data["agent"], self.roles()), ("budget", []))
        self.assertIn("교통·차량 예산을 30,000원 넘었어요", started["text"])

    def test_over_categories_question_says_none_when_nothing_is_over(self):
        self.spend(self.today, 10000, "TRANSPORT_CAR")
        data, answer = self.say("초과한 카테고리 뭐야?")
        self.assertIn("예산을 넘은 카테고리는 없어요.", answer["text"])
        self.assertNotIn({"value": "budget", "label": "예산 조정하기"}, answer["choices"])

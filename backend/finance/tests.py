"""finance API 테스트. 실행: backend 폴더에서 python manage.py test finance
(테스트용 DB를 따로 만들었다가 지운다. 내 연습 데이터는 건드리지 않는다)"""
from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .models import BankAccount, Card, Connection, FinancialTransaction, TransactionLink

URL = "/api/finance/transactions/"


class TransactionListTest(TestCase):
    """16. GET /api/finance/transactions/ (transactions.md '거래 목록 보기')"""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email="sejin@example.com", password="Example123!")
        cls.other = get_user_model().objects.create_user(email="other@example.com", password="Example123!")
        conn = Connection.objects.create(user=cls.user, business_type="bank", organization_code="0004", status="connected")
        cls.account = BankAccount.objects.create(
            user=cls.user, connection=conn, source_account_key="acc-1", masked_number="123-****-5678",
            name="급여통장", account_type="demand",
        )
        cls.card = Card.objects.create(user=cls.user, name="신한 Deep Dream", masked_number="****1234", origin="manual")
        cls.today = timezone.localdate()
        cls.month_start = cls.today.replace(day=1)

    def tx(self, user=None, **fields):
        """거래 1건 저장. 기본 = 이번 달 1일 계좌 소비 1만 원, 식비 확정"""
        data = dict(
            user=user or self.user, source_kind="bank", account=self.account, occurred_on=self.month_start,
            amount=10000, direction="out", transaction_type="spending",
            confirmed_category_id="FOOD", confirmation_state="confirmed",
        )
        data.update(fields)
        if data["source_kind"] == "card":
            data["account"] = None
        return FinancialTransaction.objects.create(**data)

    def get(self, query=""):
        self.client.force_login(self.user)
        return self.client.get(URL + query)

    def test_login_required(self):
        res = self.client.get(URL)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["error"]["code"], "not_authenticated")

    def test_default_month_list_and_summary(self):
        self.tx(source_description="스타벅스", amount=5500, occurred_time=time(9, 0))
        self.tx(source_kind="card", card=self.card, merchant_name="쿠팡", amount=32000, canceled_amount=2000,
                confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review", occurred_time=time(18, 0))
        self.tx(source_description="급여", amount=2600000, direction="in", transaction_type="income")
        self.tx(source_description="내 적금", amount=300000, transaction_type="saving")  # 목록에 안 나옴
        self.tx(user=self.other, amount=99999)  # 남의 거래

        res = self.get()
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["period"], {"from": str(self.month_start), "to": str(self.today), "basis": "month"})
        self.assertEqual([i["merchantName"] for i in body["items"]], ["쿠팡", "스타벅스", "급여"])  # 시각 없는 급여가 맨 뒤
        self.assertEqual(body["summary"], {"spentTotal": 5500 + 30000, "needsReviewCount": 1})

        coupang, starbucks, salary = body["items"]
        self.assertEqual(coupang["cancel"], {"state": "partial", "amount": 2000})
        self.assertEqual(coupang["netAmount"], 30000)
        self.assertEqual(coupang["classifyState"], "needs_review")
        self.assertEqual(coupang["paymentCard"]["displayName"], "신한 Deep Dream · ****1234")
        self.assertIsNone(coupang["paymentAccount"])
        self.assertEqual(starbucks["paymentAccount"]["displayName"], "국민 급여통장 · ****5678")
        self.assertEqual(starbucks["funding"], "living")
        self.assertEqual(starbucks["occurredTime"], "09:00")
        self.assertIsNone(salary["category"])
        self.assertIsNone(salary["funding"])

    def test_category_filter(self):
        self.tx(source_description="스타벅스")
        self.tx(source_description="지하철", confirmed_category_id="TRANSPORT_CAR")
        self.tx(source_description="급여", direction="in", transaction_type="income")
        body = self.get("?category=FOOD").json()
        self.assertEqual([i["merchantName"] for i in body["items"]], ["스타벅스"])

    def test_unclassified_without_period_is_all_time(self):
        old = date(2025, 1, 15)
        self.tx(occurred_on=old, confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review")
        self.tx()  # 식비 — 안 나옴
        body = self.get("?category=UNCLASSIFIED").json()
        self.assertEqual(body["period"], {"from": str(old), "to": str(self.today), "basis": "all"})
        self.assertEqual(body["summary"]["needsReviewCount"], 1)

    def test_custom_period(self):
        self.tx(occurred_on=date(2026, 9, 10))
        body = self.get("?from=2026-09-01&to=2026-09-30").json()
        self.assertEqual(body["period"]["basis"], "custom")
        self.assertEqual(len(body["items"]), 1)

    def test_same_purchase_bank_side_hidden(self):
        card_tx = self.tx(source_kind="card", card=self.card, merchant_name="GS25")
        bank_tx = self.tx(source_description="체크카드 출금")
        TransactionLink.objects.create(
            user=self.user, from_transaction=card_tx, to_transaction=bank_tx,
            relation_type="SAME_PURCHASE", matched_amount=10000, confirmation_state="confirmed",
        )
        body = self.get().json()
        self.assertEqual([i["merchantName"] for i in body["items"]], ["GS25"])
        self.assertEqual(body["summary"]["spentTotal"], 10000)

    def test_bad_requests(self):
        res = self.get("?category=NOPE")
        self.assertEqual(res.status_code, 400)
        self.assertIn("category", res.json()["error"]["fields"])
        for query in ("?from=2026-13-01", "?from=2026-10-20&to=2026-10-01"):
            res = self.get(query)
            self.assertEqual(res.status_code, 400)
            self.assertEqual(res.json()["error"]["fields"]["from"], ["기간을 다시 골라 주세요."])

    def test_empty(self):
        body = self.get().json()
        self.assertEqual(body["items"], [])
        self.assertEqual(body["summary"], {"spentTotal": 0, "needsReviewCount": 0})

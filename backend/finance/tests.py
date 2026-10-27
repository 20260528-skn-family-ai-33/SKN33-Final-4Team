"""finance API 테스트. 실행: backend 폴더에서 python manage.py test finance
(테스트용 DB를 따로 만들었다가 지운다. 내 연습 데이터는 건드리지 않는다)"""
import json
from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from users.models import Consent

from .merchant_rules import remembered_category
from .models import BankAccount, Card, Connection, FinancialTransaction, RecurringExpense, TransactionLink

URL = "/api/finance/transactions/"


class FinanceData(TestCase):
    """테스트마다 쓰는 기본 데이터: 회원 2명 · 국민은행 연결 · 입출금 계좌 · 직접 등록 카드"""

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


class TransactionListTest(FinanceData):
    """16. GET /api/finance/transactions/ (transactions.md '거래 목록 보기')"""

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


class TransactionDetailTest(FinanceData):
    """17. GET · 18. PATCH /api/finance/transactions/<id>/"""

    def url(self, tx):
        return f"{URL}{tx.transaction_id}/"

    def patch(self, tx, body):
        self.client.force_login(self.user)
        return self.client.patch(self.url(tx), data=json.dumps(body), content_type="application/json")

    def test_detail_with_suggestion(self):
        tx = self.tx(
            source_kind="card", card=self.card, merchant_name="GS25 역삼점", amount=6800, installment_months=0,
            confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review",
            suggestion_candidates=[{"category": "FOOD", "confidence": 0.41}, {"category": "HOUSEHOLD", "confidence": 0.3}],
            suggestion_reason="편의점은 여러 물건을 팔아서 무엇을 샀는지 알 수 없어요.",
        )
        self.client.force_login(self.user)
        body = self.client.get(self.url(tx)).json()
        self.assertEqual(body["transactionId"], str(tx.transaction_id))
        self.assertEqual(body["installmentMonths"], 0)
        self.assertEqual(body["suggestion"]["candidates"], [
            {"category": "FOOD", "categoryName": "식비"}, {"category": "HOUSEHOLD", "categoryName": "생활용품"},
        ])  # 확신도 숫자는 안 보냄
        self.assertEqual(body["suggestion"]["reason"], "편의점은 여러 물건을 팔아서 무엇을 샀는지 알 수 없어요.")
        self.assertIsNone(body["classifiedBy"])
        self.assertIn("updatedAt", body)

    def test_not_found_cases(self):
        mine_hidden = self.tx(transaction_type="card_payment")  # 카드값은 목록에 없음
        card_tx = self.tx(source_kind="card", card=self.card)
        bank_tx = self.tx()
        TransactionLink.objects.create(
            user=self.user, from_transaction=card_tx, to_transaction=bank_tx,
            relation_type="SAME_PURCHASE", matched_amount=10000, confirmation_state="confirmed",
        )
        others = self.tx(user=self.other)
        self.client.force_login(self.user)
        for tx in (mine_hidden, bank_tx, others):
            with self.subTest(tx=tx.source_description):
                res = self.client.get(self.url(tx))
                self.assertEqual(res.status_code, 404)
                self.assertEqual(res.json()["error"]["code"], "not_found")
        self.assertEqual(self.client.get(self.url(card_tx)).status_code, 200)

    def test_patch_confirms_category(self):
        tx = self.tx(source_kind="card", card=self.card, merchant_name="김밥천국",
                     confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review")
        res = self.patch(tx, {"category": "FOOD"})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual((body["category"], body["categoryName"]), ("FOOD", "식비"))
        self.assertEqual((body["classifyState"], body["classifiedBy"]), ("confirmed", "user"))
        self.assertIsNone(body["suggestion"])
        self.assertEqual(remembered_category(self.user, "김밥천국"), "FOOD")  # 같은 가맹점 새 거래에 먼저 쓰임
        self.assertEqual(self.patch(tx, {"category": "FOOD"}).status_code, 200)  # 같은 값 다시 보내도 성공

    def test_patch_bad_requests(self):
        spending = self.tx()
        income = self.tx(direction="in", transaction_type="income")
        cases = [
            (spending, {"category": "FOOD", "amount": 1}, "amount", "이 칸은 보낼 수 없어요."),
            (spending, {}, "category", "카테고리를 다시 골라 주세요."),
            (spending, {"category": "UNCLASSIFIED"}, "category", "카테고리를 다시 골라 주세요."),
            (spending, {"category": "NOPE"}, "category", "카테고리를 다시 골라 주세요."),
            (spending, {"category": None}, "category", "카테고리를 다시 골라 주세요."),  # 원래 소비였던 거래
            (income, {"category": "FOOD"}, "category", "이 거래는 카테고리를 고를 수 없어요."),
        ]
        for tx, body, field, message in cases:
            with self.subTest(body=body):
                res = self.patch(tx, body)
                self.assertEqual(res.status_code, 400)
                self.assertEqual(res.json()["error"]["fields"][field], [message])

    def test_transfer_to_spending_and_back(self):
        tx = self.tx(source_description="이하늘", transaction_type="transfer")
        body = self.patch(tx, {"category": "FOOD"}).json()
        self.assertEqual(body["transactionType"], "spending")
        tx.refresh_from_db()
        self.assertEqual(tx.field_states["original_transaction_type"], "transfer")

        body = self.patch(tx, {"category": None}).json()
        self.assertEqual(body["transactionType"], "transfer")
        self.assertIsNone(body["category"])
        tx.refresh_from_db()
        self.assertNotIn("original_transaction_type", tx.field_states)

    def test_login_required(self):
        tx = self.tx()
        self.assertEqual(self.client.get(self.url(tx)).status_code, 403)
        res = self.client.patch(self.url(tx), data=json.dumps({"category": "FOOD"}), content_type="application/json")
        self.assertEqual(res.status_code, 403)


class ConnectTest(FinanceData):
    """6 동의 저장 · 7 계좌 후보 · 8 계좌 선택 · 9 연결 목록 (finance-connect.md)"""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        conn = cls.account.connection
        cls.second = BankAccount.objects.create(
            user=cls.user, connection=conn, source_account_key="acc-2", masked_number="****9012", account_type="demand",
        )
        cls.deposit = BankAccount.objects.create(  # 예적금 — 후보 아님
            user=cls.user, connection=conn, source_account_key="acc-3", masked_number="****3333", account_type="deposit",
        )
        gone = Connection.objects.create(  # 해제한 연결 — 후보 · 목록 아님
            user=cls.user, business_type="bank", organization_code="0088", status="disconnected",
            disconnected_at=timezone.now(),
        )
        cls.gone_account = BankAccount.objects.create(
            user=cls.user, connection=gone, source_account_key="acc-4", masked_number="****4444", account_type="demand",
        )

    def login(self, consent=True):
        if consent:
            Consent.objects.create(user=self.user, purpose="finance_inquiry", product_scope="bank",
                                   document_version="v1", document_hash="test")
        self.client.force_login(self.user)

    def send(self, method, path, body):
        return getattr(self.client, method)(path, data=json.dumps(body), content_type="application/json")

    def test_consent_save(self):
        self.login(consent=False)
        body = {"consents": [
            {"purpose": "finance_inquiry", "productScope": "bank", "documentVersion": "v1"},
            {"purpose": "finance_inquiry", "productScope": "card", "documentVersion": "v1"},
        ]}
        first = self.send("post", "/api/finance/consents/", body)
        self.assertEqual(first.status_code, 201)
        self.assertEqual([i["productScope"] for i in first.json()["items"]], ["bank", "card"])
        again = self.send("post", "/api/finance/consents/", body)  # 이미 있으면 새로 안 만듦 (N⑥)
        self.assertEqual(again.json()["items"], first.json()["items"])
        self.assertEqual(Consent.objects.filter(user=self.user).count(), 2)

    def test_consent_bad_requests(self):
        self.login(consent=False)
        card_only = {"consents": [{"purpose": "finance_inquiry", "productScope": "card", "documentVersion": "v1"}]}
        res = self.send("post", "/api/finance/consents/", card_only)
        self.assertEqual(res.json()["error"]["fields"]["consents"], ["계좌 조회 동의는 꼭 필요해요."])
        extra = {"consents": [{"purpose": "finance_inquiry", "productScope": "bank", "documentVersion": "v1", "documentHash": "x"}]}
        res = self.send("post", "/api/finance/consents/", extra)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"]["fields"]["documentHash"], ["이 칸은 보낼 수 없어요."])
        res = self.send("post", "/api/finance/consents/", {"consents": []})
        self.assertEqual(res.status_code, 400)

    def test_accounts_need_consent(self):
        self.login(consent=False)
        res = self.client.get("/api/finance/connect/accounts/")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["error"]["code"], "consent_required")

    def test_account_candidates(self):
        self.login()
        items = {i["accountId"]: i for i in self.client.get("/api/finance/connect/accounts/").json()["items"]}
        # 입출금 계좌 2개만 (예적금 · 해제한 연결의 계좌는 빠짐)
        self.assertEqual(set(items), {str(self.account.account_id), str(self.second.account_id)})
        first = items[str(self.account.account_id)]
        self.assertEqual(first["displayName"], "국민 급여통장 · ****5678")
        self.assertEqual(first["organizationName"], "국민은행")
        self.assertEqual(first["maskedNumber"], "****5678")
        self.assertEqual(items[str(self.second.account_id)]["displayName"], "국민 입출금 · ****9012")

    def test_account_select(self):
        self.login()
        res = self.send("put", "/api/finance/connect/accounts/", {"selectedAccountIds": [str(self.second.account_id)]})
        self.assertEqual(res.status_code, 200)
        selected = {i["accountId"]: i["isSelected"] for i in res.json()["items"]}
        self.assertEqual(selected, {str(self.account.account_id): False, str(self.second.account_id): True})

        for bad in ([str(self.second.account_id), str(self.deposit.account_id)],  # 예적금 섞임
                    [str(self.gone_account.account_id)],                          # 해제한 연결
                    ["not-a-uuid"],
                    [str(self.second.account_id), str(self.second.account_id)]):  # 중복
            with self.subTest(bad=bad):
                res = self.send("put", "/api/finance/connect/accounts/", {"selectedAccountIds": bad})
                self.assertEqual(res.json()["error"]["fields"]["selectedAccountIds"], ["선택할 수 없는 계좌예요."])
        self.second.refresh_from_db()
        self.assertTrue(self.second.is_selected)  # 틀린 요청은 아무것도 안 바꿈

        res = self.send("put", "/api/finance/connect/accounts/", {"selectedAccountIds": []})
        self.assertEqual(res.json()["error"]["fields"]["selectedAccountIds"], ["계좌를 하나 이상 골라 주세요."])
        res = self.send("put", "/api/finance/connect/accounts/", {"selectedAccountIds": [str(self.second.account_id)], "isSelected": True})
        self.assertEqual(res.json()["error"]["fields"]["isSelected"], ["이 칸은 보낼 수 없어요."])

    def test_connections(self):
        self.login(consent=False)
        items = self.client.get("/api/finance/connections/").json()["items"]
        self.assertEqual(len(items), 1)  # 해제한 연결은 안 나옴
        item = items[0]
        self.assertEqual((item["businessType"], item["organizationName"], item["status"]), ("bank", "국민은행", "connected"))
        self.assertEqual(item["accountCount"], 3)
        self.assertEqual(item["cardCount"], 0)
        self.assertNotIn("connectedId", item)

    def test_login_required(self):
        for path in ("/api/finance/connect/accounts/", "/api/finance/connections/"):
            self.assertEqual(self.client.get(path).status_code, 403)


class DisconnectTest(FinanceData):
    """10. DELETE /api/finance/connections/<id>/ (finance-connect.md '연결 해제')"""

    def url(self, connection):
        return f"/api/finance/connections/{connection.connection_id}/"

    def test_disconnect_keeps_row_accounts_and_fixed_expenses(self):
        conn = self.account.connection
        BankAccount.objects.filter(pk=self.account.pk).update(is_selected=True)
        fixed = RecurringExpense.objects.create(
            user=self.user, payment_account=self.account, name="월세", amount=700000, payment_day=25,
            category_id="HOUSING_TELECOM", status="confirmed", source_type="manual",
        )
        tx = self.tx()
        self.client.force_login(self.user)

        res = self.client.delete(self.url(conn))
        self.assertEqual(res.status_code, 204)
        self.assertEqual(res.content, b"")
        conn.refresh_from_db()
        self.assertEqual(conn.status, "disconnected")
        self.assertIsNotNone(conn.disconnected_at)
        first_time = conn.disconnected_at

        # 행·계좌 선택·고정지출·지난 거래는 그대로
        self.account.refresh_from_db()
        self.assertTrue(self.account.is_selected)
        fixed.refresh_from_db()
        self.assertEqual((fixed.status, fixed.payment_account_id), ("confirmed", self.account.pk))
        self.assertTrue(FinancialTransaction.objects.filter(pk=tx.pk).exists())

        # 연결 목록에서 빠지고, 거래의 계좌 표시는 disconnected
        self.assertEqual(self.client.get("/api/finance/connections/").json()["items"], [])
        item = self.client.get("/api/finance/transactions/").json()["items"][0]
        self.assertEqual(item["paymentAccount"]["status"], "disconnected")

        # 다시 해제해도 204, 해제 시각은 처음 값 유지
        self.assertEqual(self.client.delete(self.url(conn)).status_code, 204)
        conn.refresh_from_db()
        self.assertEqual(conn.disconnected_at, first_time)

    def test_unknown_or_others_connection_is_404(self):
        others = Connection.objects.create(user=self.other, business_type="bank", organization_code="0004", status="connected")
        self.client.force_login(self.user)
        for url in (self.url(others), "/api/finance/connections/00000000-0000-4000-8000-000000000000/"):
            with self.subTest(url=url):
                res = self.client.delete(url)
                self.assertEqual(res.status_code, 404)
                self.assertEqual(res.json()["error"]["code"], "not_found")
        others.refresh_from_db()
        self.assertEqual(others.status, "connected")

    def test_login_required(self):
        res = self.client.delete(self.url(self.account.connection))
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["error"]["code"], "not_authenticated")

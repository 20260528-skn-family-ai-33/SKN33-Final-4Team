"""알파 테스트(11/05) 공통 정답표로 거래 저장 · 수정 · 집계를 확인한다 (11/03 지시서 S3 '같은 자료로 숫자와 저장을 확인').
판별 알고리즘(같은 구매 · 내 계좌 이체 · 고정비 매칭)을 검증하는 게 아니라, 유형과 연결 근거를 명시한 합성 거래로
저장 · 수정 API · 거래 목록 집계가 맞는지 본다. 생활비 사용액(320,000 → 350,000)은 예산 쪽(나은 님) 집계로 대조한다.

실행: backend 폴더에서 python manage.py test finance.test_alpha_dataset"""
import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command

from . import classifier
from .models import PaymentDue, PaymentMatch, RecurringExpense, TransactionLink
from .tests import URL, FinanceData


def build_alpha_transactions(case):
    """지시서 합성 자료 9건. case = FinanceData 테스트 (user · account · card · tx() 사용).
    같은 구매 · 고정비 납부는 근거(TransactionLink · PaymentMatch)를 명시해서 만든다. 이름 → 거래 dict"""
    on = case.month_start
    txs = {
        "계좌 식비": case.tx(amount=120_000, merchant_name="김밥천국", occurred_on=on),
        "카드 식비": case.tx(amount=200_000, source_kind="card", card=case.card, merchant_name="송황칼국수", occurred_on=on),
        # 체크카드 결제의 은행 출금: 같은 구매로 연결되어 목록 · 합계에서 빠진다
        "같은 구매 출금": case.tx(amount=200_000, merchant_name="신한체크", occurred_on=on),
        "내 계좌 이체": case.tx(amount=100_000, transaction_type="own_transfer", merchant_name="내 저축계좌",
                           confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review", occurred_on=on),
        "친구 송금": case.tx(amount=30_000, transaction_type="transfer", merchant_name="홍길동",
                         confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review", occurred_on=on),
        "카드대금": case.tx(amount=50_000, transaction_type="card_payment", merchant_name="신한카드",
                          confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review", occurred_on=on),
        "월세": case.tx(amount=700_000, confirmed_category_id="HOUSING_TELECOM", merchant_name="집주인", occurred_on=on),
        "주거통신 미등록": case.tx(amount=20_000, confirmed_category_id="HOUSING_TELECOM", merchant_name="관리사무소", occurred_on=on),
        "여행": case.tx(amount=200_000, source_kind="card", card=case.card, confirmed_category_id="TRAVEL_LODGING",
                      merchant_name="제주호텔", occurred_on=on),
    }
    TransactionLink.objects.create(user=case.user, from_transaction=txs["카드 식비"], to_transaction=txs["같은 구매 출금"],
                                   relation_type="SAME_PURCHASE", matched_amount=200_000, confirmation_state="confirmed")
    rent = RecurringExpense.objects.create(user=case.user, name="월세", amount=700_000, category_id="HOUSING_TELECOM",
                                           status="confirmed", source_type="manual")
    due = PaymentDue.objects.create(user=case.user, recurring=rent, obligation_type="recurring", occurrence_key="alpha-rent",
                                    due_date=on, confirmed_amount=700_000, paid_amount=700_000,
                                    payment_state="paid", obligation_state="active")
    PaymentMatch.objects.create(user=case.user, due=due, transaction=txs["월세"], matched_amount=700_000)
    return txs


class AlphaDatasetTest(FinanceData):
    def setUp(self):
        self.txs = build_alpha_transactions(self)
        self.client.force_login(self.user)

    def summary(self):
        return self.client.get(URL).json()["summary"]["spentTotal"]

    def edit(self, tx, body):
        return self.client.patch(f"{URL}{tx.pk}/", data=json.dumps(body), content_type="application/json")

    def test_total_spending_matches_answer_table(self):
        # 정답: 120,000 + 200,000 + 700,000 + 20,000 + 200,000 (같은 구매 출금 · 내 계좌 이체 · 친구 송금 · 카드대금 제외)
        self.assertEqual(self.summary(), 1_240_000)
        items = {i["merchantName"]: i for i in self.client.get(URL).json()["items"]}
        self.assertNotIn("신한체크", items)       # 같은 구매 은행 출금은 목록에서 빠짐
        self.assertNotIn("내 저축계좌", items)     # 내 계좌 이체 · 카드대금은 목록 유형 밖
        self.assertNotIn("신한카드", items)
        self.assertEqual(items["홍길동"]["transactionType"], "transfer")  # 은행 출금 이체를 숨기지 않는다
        self.assertEqual(items["집주인"]["funding"], "fixed")            # 납부 연결된 월세 = 고정비

    def test_friend_transfer_to_spending_and_back(self):
        friend = self.txs["친구 송금"]
        res = self.edit(friend, {"category": "FOOD"})
        self.assertEqual((res.status_code, res.json()["transactionType"], res.json()["category"]), (200, "spending", "FOOD"))
        self.assertEqual(self.summary(), 1_270_000)
        res = self.edit(friend, {"category": None})  # 원래 이체였던 거래만 이체로 되돌릴 수 있다
        self.assertEqual((res.status_code, res.json()["transactionType"]), (200, "transfer"))
        self.assertEqual(self.summary(), 1_240_000)

    def test_restore_to_transfer_only_for_original_transfers(self):
        res = self.edit(self.txs["계좌 식비"], {"category": None})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.summary(), 1_240_000)
        # 목록에 안 나오는 유형(내 계좌 이체 · 카드대금 · 같은 구매 출금)은 수정할 수 없다
        for name in ("내 계좌 이체", "카드대금", "같은 구매 출금"):
            self.assertEqual(self.edit(self.txs[name], {"category": "FOOD"}).status_code, 404, name)
        income = self.tx(amount=2_800_000, transaction_type="income", direction="in", confirmed_category_id="UNCLASSIFIED")
        self.assertEqual(self.edit(income, {"category": "FOOD"}).status_code, 400)

    def test_housing_and_travel_can_be_chosen_and_reread(self):
        meal = self.txs["계좌 식비"]
        for code in ("HOUSING_TELECOM", "TRAVEL_LODGING"):
            self.assertEqual(self.edit(meal, {"category": code}).status_code, 200)
            self.assertEqual(self.client.get(f"{URL}{meal.pk}/").json()["category"], code)
        self.assertEqual(self.summary(), 1_240_000)  # 카테고리만 바뀌고 전체 소비는 그대로

    @patch("finance.classifier.judge", return_value=None)
    @patch("finance.classifier.predict", side_effect=lambda names: [[("FOOD", 0.99)] for _ in names])
    def test_user_choice_survives_reclassify(self, predict, judge):
        friend = self.txs["친구 송금"]
        self.edit(friend, {"category": "BEAUTY"})
        classifier.classify(self.user, type(friend).objects.filter(user=self.user))
        call_command("classify_transactions", "--email", self.user.email, stdout=StringIO())
        friend.refresh_from_db()
        self.assertEqual((friend.confirmed_category_id, friend.confirmation_state, friend.classification_source),
                         ("BEAUTY", "confirmed", "user"))

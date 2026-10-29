"""예산의 카드 첫 청구서 선차감 연결 (리뷰 3, 결정 Q · cards.md C-17)."""
import json
import uuid
from datetime import date, datetime, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from django.http import Http404

from finance.models import (BankAccount, Card, CardBill, Connection, FinancialTransaction, PaymentDue, PaymentMatch,
                            RecurringExpense, SyncRun)
from users.models import Consent, FinancialProfile

from .models import Budget, BudgetCycle
from .protect import CARD_BILL_SYNC, CARD_SPENDING_SYNC, card_reserved, covered, covered_until, fixed_total


TODAY = date(2026, 10, 28)
URL = "/api/budgets/protect/"


class CoveredTest(SimpleTestCase):
    def test_joins_overlapping_and_touching_runs(self):
        runs = [(date(2026, 10, 24), date(2026, 10, 28)), (date(2026, 10, 20), date(2026, 10, 24))]
        self.assertTrue(covered(runs, date(2026, 10, 20), date(2026, 10, 28)))
        self.assertTrue(covered([(date(2026, 10, 20), date(2026, 10, 23)), (date(2026, 10, 24), date(2026, 10, 28))],
                                date(2026, 10, 20), date(2026, 10, 28)))

    def test_gap_or_short_range_is_not_complete(self):
        self.assertFalse(covered([(date(2026, 10, 20), date(2026, 10, 22)), (date(2026, 10, 24), date(2026, 10, 28))],
                                 date(2026, 10, 20), date(2026, 10, 28)))
        self.assertFalse(covered([(date(2026, 10, 21), date(2026, 10, 28))], date(2026, 10, 20), date(2026, 10, 28)))
        self.assertFalse(covered([(date(2026, 10, 20), date(2026, 10, 27))], date(2026, 10, 20), date(2026, 10, 28)))
        self.assertFalse(covered([], date(2026, 10, 20), date(2026, 10, 28)))


class CoveredUntilTest(SimpleTestCase):
    def test_last_continuous_day(self):
        runs = [(date(2026, 10, 20), date(2026, 10, 26)), (date(2026, 10, 25), date(2026, 10, 28)), (date(2026, 10, 30), date(2026, 10, 31))]
        self.assertEqual(covered_until(runs, date(2026, 10, 20)), date(2026, 10, 28))
        self.assertIsNone(covered_until(runs, date(2026, 10, 19)))
        self.assertIsNone(covered_until(runs, None))


class CardReservedTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="card-reserved@example.invalid")
        self.profile = FinancialProfile.objects.create(
            user=self.user, housing_type="family", monthly_take_home_income=1_500_000, payday=25,
        )
        RecurringExpense.objects.create(user=self.user, name="통신", amount=100_000,
                                        category_id="HOUSING_TELECOM", status="confirmed", source_type="manual")
        bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        BankAccount.objects.create(user=self.user, connection=bank, source_account_key="card-reserved",
                                   account_type="demand", currency="KRW", is_selected=True, balance=1_000_000)
        self.cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25))
        self.connection = Connection.objects.create(user=self.user, business_type="card", organization_code="0306", status="connected")
        self.consent = Consent.objects.create(user=self.user, purpose="finance_inquiry", product_scope="card",
                                              document_version="v1", document_hash="test")
        # 한국 시간 10/20 00시 최초 연결
        self.card = Card.objects.create(user=self.user, connection=self.connection, origin="provider", issuer_code="0306",
                                        first_connected_at=datetime(2026, 10, 19, 15, tzinfo=dt_timezone.utc), is_selected=True)
        # CODEF 청구서는 명세서 단위 카드사 통합 (finance/sync_cards.py)
        CardBill.objects.create(user=self.user, connection=self.connection, card=None, issuer_code="0306",
                                billing_month=date(2026, 11, 1), total_amount=300_000, source_type="provider",
                                scope_type="issuer", withdrawal_due_date=date(2026, 11, 10))
        FinancialTransaction.objects.create(user=self.user, card=self.card, source_kind="card", occurred_on=date(2026, 10, 26),
                                            amount=50_000, direction="out", transaction_type="spending",
                                            confirmed_category_id="FOOD", confirmation_state="confirmed")
        clock = patch("django.utils.timezone.localdate", side_effect=self.localdate)
        clock.start()
        self.addCleanup(clock.stop)
        external = patch("finance.codef._request", side_effect=AssertionError("계산 중 CODEF 호출 금지"))
        external.start()
        self.addCleanup(external.stop)

    @staticmethod
    def localdate(value=None, timezone=None):
        from django.utils import timezone as django_timezone
        if value is None:
            return TODAY
        return django_timezone.localtime(value, timezone).date()

    def run_(self, product, start, end, status="success"):
        SyncRun.objects.create(user=self.user, connection=self.connection, card=self.card, product_code=product,
                               covered_from=start, covered_to=end, status=status)

    def collect_all(self):
        self.run_(CARD_BILL_SYNC, date(2026, 10, 20), TODAY)
        self.run_(CARD_SPENDING_SYNC, date(2026, 10, 20), date(2026, 10, 26))
        self.run_(CARD_SPENDING_SYNC, date(2026, 10, 25), TODAY)  # 다시 불러오기로 이어진 기간

    def test_first_bill_minus_cycle_card_spending_goes_into_fixed_total(self):
        self.collect_all()
        self.assertEqual(card_reserved(self.user, self.cycle), {"amount": 250_000, "pending": []})
        self.assertEqual(fixed_total(self.user, self.cycle), 350_000)

    def test_missing_history_is_pending_not_zero(self):
        self.run_(CARD_BILL_SYNC, date(2026, 10, 20), TODAY)
        self.run_(CARD_SPENDING_SYNC, date(2026, 10, 25), TODAY, status="failed")
        result = card_reserved(self.user, self.cycle)
        self.assertIsNone(result["amount"])
        self.assertEqual(result["pending"], [{"card_id": str(self.card.pk), "reason": "spending_history_incomplete"}])
        self.assertIsNone(fixed_total(self.user, self.cycle))

    def test_unselected_card_and_no_consent_are_skipped(self):
        self.card.is_selected = False
        self.card.save()
        self.assertEqual(card_reserved(self.user, self.cycle), {"amount": 0, "pending": []})
        self.card.is_selected = True
        self.card.save()
        self.consent.delete()
        self.assertEqual(card_reserved(self.user, self.cycle), {"amount": 0, "pending": []})

    def test_protect_shows_pending_and_blocks_save(self):
        client = APIClient()
        client.force_authenticate(self.user)
        data = client.get(URL).json()
        self.assertEqual(data["blocked"], {"reason": "card_bill_pending", "shortfall": None})
        self.assertIsNone(data["base"])
        self.assertIsNone(data["recommended"])
        self.assertEqual(data["cardPending"], [{"cardId": str(self.card.pk), "reason": "bill_history_incomplete"}])
        response = client.put(URL, json.dumps({"livingBudget": 100_000, "savingFloor": 100_000, "cushionMonthly": 100_000}),
                              content_type="application/json")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "not_ready"))
        self.assertIn("카드 청구서", response.json()["error"]["message"])

        self.collect_all()
        data = client.get(URL).json()
        self.assertIsNone(data["blocked"])
        self.assertEqual((data["base"]["fixedTotal"], data["cardPending"]), (350_000, []))

    # ---------- 리뷰 보완: 초안 고정 · 날짜 지남 · 카드 고정지출 · 이상한 자료 ----------

    def client_(self):
        client = APIClient()
        client.force_authenticate(self.user)
        return client

    def recommend_draft(self, client):
        saved = client.put(URL, json.dumps({"livingBudget": 500_000, "savingFloor": 100_000, "cushionMonthly": 100_000}),
                           content_type="application/json")
        self.assertEqual(saved.status_code, 200, saved.content)
        response = client.post("/api/budgets/recommend/", "{}", content_type="application/json", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(response.status_code, 202, response.content)
        return Budget.objects.get(user=self.user, status="validated")

    def approve(self, client, budget):
        return client.post(f"/api/budgets/{budget.pk}/approve/", "{}", content_type="application/json",
                           HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))

    def test_single_numeric_statement_allows_protect_and_recommend(self):
        self.collect_all()
        client = self.client_()
        for value in ("0", "1"):
            with self.subTest(value=value):
                CardBill.objects.filter(user=self.user).update(field_states={"codefBillType": value})
                self.assertEqual(card_reserved(self.user, self.cycle), {"amount": 250_000, "pending": []})
                response = client.get(URL)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertIsNone(response.json()["blocked"])
                self.assertEqual(response.json()["cardPending"], [])
                draft = self.recommend_draft(client)
                self.assertEqual(draft.items.get(category_detail="card_reserved").allocated_amount, 250_000)

    def test_card_purchase_after_recommend_does_not_expire_draft(self):
        self.collect_all()
        client = self.client_()
        draft = self.recommend_draft(client)
        self.assertEqual(draft.items.get(category_detail="card_reserved").allocated_amount, 250_000)
        FinancialTransaction.objects.create(user=self.user, card=self.card, source_kind="card", occurred_on=TODAY,
                                            amount=4_500, direction="out", transaction_type="spending",
                                            confirmed_category_id="FOOD", confirmation_state="confirmed")
        self.assertEqual(card_reserved(self.user, self.cycle)["amount"], 245_500)  # 지금 값은 바뀌지만
        response = self.approve(client, draft)
        self.assertEqual(response.status_code, 200, response.content)  # 초안은 만들 때 값으로 승인

    def test_changing_selected_cards_expires_draft(self):
        self.collect_all()
        client = self.client_()
        draft = self.recommend_draft(client)
        self.card.is_selected = False
        self.card.save()
        response = self.approve(client, draft)
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "expired"))

    def test_next_day_without_new_sync_uses_last_synced_day(self):
        self.collect_all()
        with patch("django.utils.timezone.localdate", side_effect=lambda value=None, timezone=None:
                   date(2026, 10, 30) if value is None else self.localdate(value, timezone)):
            FinancialTransaction.objects.create(user=self.user, card=self.card, source_kind="card", occurred_on=date(2026, 10, 29),
                                                amount=10_000, direction="out", transaction_type="spending",
                                                confirmed_category_id="FOOD", confirmation_state="confirmed")
            # 10/28까지 불러온 자료 기준: 10/29 거래는 아직 안 뺀다 (선차감이 작아지지 않게)
            self.assertEqual(card_reserved(self.user, self.cycle), {"amount": 250_000, "pending": []})

    def test_bill_history_before_cycle_start_is_pending(self):
        SyncRun.objects.all().delete()
        self.run_(CARD_BILL_SYNC, date(2026, 10, 20), date(2026, 10, 23))
        self.run_(CARD_SPENDING_SYNC, date(2026, 10, 25), TODAY)
        self.assertEqual(card_reserved(self.user, self.cycle)["pending"],
                         [{"card_id": str(self.card.pk), "reason": "bill_history_incomplete"}])

    def test_card_fixed_expense_is_not_counted_twice(self):
        self.collect_all()
        expense = RecurringExpense.objects.create(user=self.user, name="구독", amount=10_000, category_id="CULTURE_LEISURE",
                                                  status="confirmed", source_type="manual", payment_card=self.card)
        # 고정비 100,000 + 구독 10,000 / 첫 청구서 300,000 − 그 카드로 쓴 돈 50,000 − 카드 고정지출 10,000
        self.assertEqual(card_reserved(self.user, self.cycle)["amount"], 240_000)
        self.assertEqual(fixed_total(self.user, self.cycle), 350_000)
        # 이번 주기에 구독이 결제돼 고정지출에 연결돼도 같은 금액 (카드로 쓴 돈에서 다시 빼지 않음)
        linked = FinancialTransaction.objects.create(user=self.user, card=self.card, source_kind="card", occurred_on=TODAY,
                                                     amount=10_000, direction="out", transaction_type="spending",
                                                     confirmed_category_id="CULTURE_LEISURE", confirmation_state="confirmed")
        due = PaymentDue.objects.create(user=self.user, recurring=expense, obligation_type="recurring", occurrence_key="sub-1",
                                        due_date=TODAY, confirmed_amount=10_000, paid_amount=10_000,
                                        payment_state="paid", obligation_state="active")
        PaymentMatch.objects.create(user=self.user, due=due, transaction=linked, matched_amount=10_000)
        self.assertEqual(card_reserved(self.user, self.cycle)["amount"], 240_000)

    def test_broken_card_data_is_pending_not_error(self):
        self.collect_all()
        for error in (Http404, ValueError("음수 금액")):
            with self.subTest(error=error), patch("finance.card_deduction.get_issuer_first_bill_deduction", side_effect=error):
                self.assertEqual(card_reserved(self.user, self.cycle)["pending"],
                                 [{"card_id": str(self.card.pk), "reason": "card_data_invalid"}])
                self.assertEqual(self.client_().get(URL).status_code, 200)

    def test_connected_this_cycle_with_spending_behind_is_pending_not_zero(self):
        # 카드를 10/27에 연결했는데 카드 거래는 10/26까지만 불러옴 → '연결 전'으로 0원이 되면 안 됨
        Card.objects.filter(pk=self.card.pk).update(first_connected_at=datetime(2026, 10, 26, 15, tzinfo=dt_timezone.utc))
        self.run_(CARD_BILL_SYNC, date(2026, 10, 27), TODAY)
        self.run_(CARD_SPENDING_SYNC, date(2026, 10, 25), date(2026, 10, 26))
        self.assertEqual(card_reserved(self.user, self.cycle),
                         {"amount": None, "pending": [{"card_id": str(self.card.pk), "reason": "spending_history_incomplete"}]})
        self.run_(CARD_SPENDING_SYNC, date(2026, 10, 26), TODAY)
        self.assertEqual(card_reserved(self.user, self.cycle), {"amount": 250_000, "pending": []})

    def test_fixed_expense_without_card_but_paid_by_this_card_is_not_counted_twice(self):
        self.collect_all()
        expense = RecurringExpense.objects.create(user=self.user, name="넷플릭스", amount=17_000, category_id="CULTURE_LEISURE",
                                                  status="confirmed", source_type="manual")  # 결제 카드 안 적음
        linked = FinancialTransaction.objects.create(user=self.user, card=self.card, source_kind="card", occurred_on=TODAY,
                                                     amount=17_000, direction="out", transaction_type="spending",
                                                     confirmed_category_id="CULTURE_LEISURE", confirmation_state="confirmed")
        due = PaymentDue.objects.create(user=self.user, recurring=expense, obligation_type="recurring", occurrence_key="nf-1",
                                        due_date=TODAY, confirmed_amount=17_000, paid_amount=17_000,
                                        payment_state="paid", obligation_state="active")
        PaymentMatch.objects.create(user=self.user, due=due, transaction=linked, matched_amount=17_000)
        # 300,000 − (67,000 − 17,000) − 17,000
        self.assertEqual(card_reserved(self.user, self.cycle)["amount"], 233_000)

    def test_fixed_expense_is_subtracted_once_when_registered_and_paid_cards_differ(self):
        # 리뷰 10/28: 고정지출에 적은 카드(A)와 실제 결제 카드(B)가 다르면 두 카드에서 각각 빼면 안 됨
        # (10/29 카드사 단위: 두 카드를 다른 카드사로 두어 선차감이 두 번 생기게 함)
        self.collect_all()
        connection = Connection.objects.create(user=self.user, business_type="card", organization_code="0301", status="connected")
        other = Card.objects.create(user=self.user, connection=connection, origin="provider", issuer_code="0301",
                                    first_connected_at=datetime(2026, 10, 19, 15, tzinfo=dt_timezone.utc), is_selected=True)
        CardBill.objects.create(user=self.user, connection=connection, card=None, issuer_code="0301",
                                billing_month=date(2026, 11, 1), total_amount=100_000, source_type="provider",
                                scope_type="issuer", withdrawal_due_date=date(2026, 11, 12))
        SyncRun.objects.create(user=self.user, connection=connection, card=other, product_code=CARD_BILL_SYNC,
                               covered_from=date(2026, 10, 20), covered_to=TODAY, status="success")
        SyncRun.objects.create(user=self.user, connection=connection, card=other, product_code=CARD_SPENDING_SYNC,
                               covered_from=date(2026, 10, 25), covered_to=TODAY, status="success")
        before = card_reserved(self.user, self.cycle)["amount"]  # 250,000 + 100,000
        self.assertEqual(before, 350_000)
        expense = RecurringExpense.objects.create(user=self.user, name="구독", amount=17_000, category_id="CULTURE_LEISURE",
                                                  status="confirmed", source_type="manual", payment_card=self.card)  # A로 등록
        self.assertEqual(card_reserved(self.user, self.cycle)["amount"], before - 17_000)  # 아직 결제 전: A에서 한 번
        linked = FinancialTransaction.objects.create(user=self.user, card=other, source_kind="card", occurred_on=TODAY,
                                                     amount=17_000, direction="out", transaction_type="spending",
                                                     confirmed_category_id="CULTURE_LEISURE", confirmation_state="confirmed")
        due = PaymentDue.objects.create(user=self.user, recurring=expense, obligation_type="recurring", occurrence_key="sub-b",
                                        due_date=TODAY, confirmed_amount=17_000, paid_amount=17_000,
                                        payment_state="paid", obligation_state="active")
        PaymentMatch.objects.create(user=self.user, due=due, transaction=linked, matched_amount=17_000)
        # 실제로는 B로 결제: B에서만 한 번 (A 250,000 그대로 + B 100,000 − 17,000)
        self.assertEqual(card_reserved(self.user, self.cycle)["amount"], before - 17_000)

    # ---------- 카드사 단위 (10/29 나은) ----------

    def same_issuer_card(self, **fields):
        return Card.objects.create(user=self.user, connection=self.connection, origin="provider", issuer_code="0306",
                                   first_connected_at=datetime(2026, 10, 19, 15, tzinfo=dt_timezone.utc), **fields)

    def test_same_issuer_unmanaged_card_share_is_deducted_together(self):
        # 같은 카드사 카드 2장(A만 관리 대상): 첫 청구서 300,000에는 B 몫도 있다 → B로 쓴 돈은 빼지 않아 같이 뗀다
        self.collect_all()
        other = self.same_issuer_card(is_selected=False)
        FinancialTransaction.objects.create(user=self.user, card=other, source_kind="card", occurred_on=date(2026, 10, 27),
                                            amount=40_000, direction="out", transaction_type="spending",
                                            confirmed_category_id="FOOD", confirmation_state="confirmed")
        self.assertEqual(card_reserved(self.user, self.cycle), {"amount": 250_000, "pending": []})
        # B로 내는 고정지출도 첫 청구서에 들어 있고 고정비에 이미 있으므로 뺀다 (그 카드사 카드 고정지출)
        RecurringExpense.objects.create(user=self.user, name="구독", amount=20_000, category_id="CULTURE_LEISURE",
                                        status="confirmed", source_type="manual", payment_card=other)
        self.assertEqual(card_reserved(self.user, self.cycle)["amount"], 230_000)
        # B도 고르면 B로 쓴 돈도 빼고, 첫 청구서는 카드사에 한 번뿐 (300,000 − 50,000 − 40,000 − 20,000)
        Card.objects.filter(pk=other.pk).update(is_selected=True)
        self.assertEqual(card_reserved(self.user, self.cycle)["amount"], 190_000)

    def test_pending_lists_every_managed_card_of_issuer(self):
        other = self.same_issuer_card(is_selected=True)
        self.assertEqual(card_reserved(self.user, self.cycle), {"amount": None, "pending": [
            {"card_id": str(card.pk), "reason": "bill_history_incomplete"}
            for card in sorted((self.card, other), key=lambda card: card.pk)
        ]})

    def test_bill_history_of_any_issuer_card_counts(self):
        # 청구서 · 승인내역은 카드사 단위로 한 번에 불러오므로 다른 카드의 조회 기록도 그 카드사 기록이다
        other = self.same_issuer_card(is_selected=False)
        for product, start in ((CARD_BILL_SYNC, date(2026, 10, 20)), (CARD_SPENDING_SYNC, date(2026, 10, 25))):
            SyncRun.objects.create(user=self.user, connection=self.connection, card=other, product_code=product,
                                   covered_from=start, covered_to=TODAY, status="success")
        self.assertEqual(card_reserved(self.user, self.cycle), {"amount": 250_000, "pending": []})


class SyncedCardReservedTest(TestCase):
    """CODEF 카드 저장(sync_card) → 관리 대상 선택 → 지킬 돈 (10/29 나은 테스트 부탁)"""

    def setUp(self):
        from finance.test_card_sync import approval, bill, card

        self.user = get_user_model().objects.create_user(email="synced-reserved@example.invalid")
        FinancialProfile.objects.create(user=self.user, housing_type="family", monthly_take_home_income=1_500_000, payday=25)
        bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        BankAccount.objects.create(user=self.user, connection=bank, source_account_key="synced-reserved",
                                   account_type="demand", currency="KRW", is_selected=True, balance=1_000_000)
        self.cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25))
        self.connection = Connection.objects.create(user=self.user, business_type="card", organization_code="0302",
                                                    status="connected")
        Consent.objects.create(user=self.user, purpose="finance_inquiry", product_scope="card",
                               document_version="v1", document_hash="test",
                               granted_at=datetime(2026, 6, 1, tzinfo=dt_timezone.utc))
        # 10/29 데모(현대카드 · 결제일 5일): 명세서 구분 빈 값, 그날 명세서 1장
        self.bills = {
            "202609": bill(due="20260905", resBillType=""), "202610": bill(due="20261005", resBillType=""),
            "202611": bill(due="20261105", resBillType="", resTotalAmount="300,000"),
        }
        self.cards = [card()]
        self.approvals = [approval(resUsedDate="20261026", resUsedAmount="50,000")]
        for target, effect in (("finance.codef._request", self.response),
                               ("finance.codef.encrypt_rsa", lambda value, key: "encrypted"),
                               ("django.utils.timezone.localdate", CardReservedTest.localdate)):
            patcher = patch(target, side_effect=effect)
            patcher.start()
            self.addCleanup(patcher.stop)

    def response(self, path, params):
        from finance import codef

        if path == codef.PATH_CARD_LIST:
            return self.cards
        if path == codef.PATH_CARD_APPROVALS:
            return self.approvals
        return self.bills.get(params["startDate"], {})

    def sync(self, end, months, start=date(2026, 6, 1)):
        from finance.sync_cards import sync_card

        # 최초 연결 시각을 조회일 정오(한국 시간)로
        with patch("finance.models.timezone.now", return_value=datetime(end.year, end.month, end.day, 3, tzinfo=dt_timezone.utc)):
            sync_card(self.user, "cid", "0302", start, end, months, connection_id=self.connection.pk)

    def test_signup_on_1028_uses_next_month_bill_and_protect_is_not_blocked(self):
        from finance.sync_ranges import bill_months

        self.assertEqual(bill_months(TODAY), ("202609", "202610", "202611"))
        self.sync(TODAY, ("202609", "202610"))  # 예전 범위(지난달 + 이번 달)로는 첫 청구서가 아직 없음
        card = Card.objects.get(user=self.user)
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.put("/api/finance/connect/cards/", json.dumps({"selectedCardIds": [str(card.pk)]}),
                              content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(card_reserved(self.user, self.cycle)["pending"],
                         [{"card_id": str(card.pk), "reason": "first_bill_unavailable"}])

        self.sync(TODAY, bill_months(TODAY))
        # 10/5 청구서는 가입 전, 다음 달 11/5 청구서가 첫 청구서: 300,000 − 10/26에 쓴 50,000
        self.assertEqual(card_reserved(self.user, self.cycle), {"amount": 250_000, "pending": []})
        data = client.get(URL).json()
        self.assertIsNone(data["blocked"])
        self.assertEqual((data["base"]["fixedTotal"], data["cardPending"]), (250_000, []))

    def test_refresh_after_two_skipped_months_is_not_pending(self):
        from finance.api_sync import last_bill_day
        from finance.sync_ranges import bill_months

        first_day = date(2026, 8, 20)
        self.sync(first_day, bill_months(first_day))
        Card.objects.filter(user=self.user).update(is_selected=True)
        card = Card.objects.get(user=self.user)
        self.assertEqual(last_bill_day(self.connection), first_day)
        # 예전 범위(지난달 + 이번 달)로 불러오면 8/21 ~ 8/31이 비어 계속 보류
        self.sync(TODAY, ("202609", "202610"), start=date(2026, 8, 13))
        self.assertEqual(card_reserved(self.user, self.cycle)["pending"],
                         [{"card_id": str(card.pk), "reason": "bill_history_incomplete"}])
        # 마지막으로 성공한 달(8월)부터 불러오면 빈 달이 없다. 첫 청구서(9/5)는 이번 주기 밖이라 0원
        months = bill_months(TODAY, last_bill_day(self.connection))
        self.assertEqual(months, ("202608", "202609", "202610", "202611"))
        self.sync(TODAY, months, start=date(2026, 8, 13))
        self.assertEqual(card_reserved(self.user, self.cycle), {"amount": 0, "pending": []})

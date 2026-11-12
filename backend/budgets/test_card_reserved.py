"""예산의 카드 첫 청구서 선차감 연결 (리뷰 3, 결정 Q · cards.md C-17)."""
import json
import uuid
from datetime import date, datetime, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from django.http import Http404

from finance.card_review import FOREIGN_PENDING_RUN
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
        self.bill = CardBill.objects.create(user=self.user, connection=self.connection, card=None, issuer_code="0306",
                                            billing_month=date(2026, 11, 1), total_amount=300_000, source_type="provider",
                                            scope_type="issuer", withdrawal_due_date=date(2026, 11, 10), items_state="complete")
        # 11/06: 청구서 상세 = 주기 전(10/10) 구매 250,000 + 이번 주기(10/26) 구매 50,000 → 50,000만 이번 주기 소비와 같은 지출(근거 있음)
        self.item(date(2026, 10, 10), 250_000)
        self.item(date(2026, 10, 26), 50_000)
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

    def item(self, purchased_on, amount, billed=None, bill=None, **fields):
        from finance.models import CardBillItem
        return CardBillItem.objects.create(user=self.user, bill=bill or self.bill, item_type=fields.pop("item_type", "one_time"),
                                           purchased_on=purchased_on, original_purchase_amount=amount,
                                           billed_amount=amount if billed is None else billed, **fields)

    def run_(self, product, start, end, status="success"):
        SyncRun.objects.create(user=self.user, connection=self.connection, card=self.card, product_code=product,
                               covered_from=start, covered_to=end, status=status)

    def collect_all(self):
        self.run_(CARD_BILL_SYNC, date(2026, 10, 20), TODAY)
        self.run_(CARD_SPENDING_SYNC, date(2026, 10, 20), date(2026, 10, 26))
        self.run_(CARD_SPENDING_SYNC, date(2026, 10, 25), TODAY)  # 다시 불러오기로 이어진 기간

    def foreign_mark(self, day):
        SyncRun.objects.create(user=self.user, connection=self.connection, product_code=FOREIGN_PENDING_RUN,
                               requested_from=day, requested_to=day, status="skipped")

    def test_foreign_payment_without_won_amount_is_a_note_and_keeps_the_bill_reserved(self):
        # 11/07: 금액을 몰라 거래가 없는 외화 결제가 청구서에 들어와도 중복으로 빼지 않아 카드값이 적게 잡히지 않는다
        self.collect_all()
        self.bill.total_amount = 320_000
        self.bill.save()
        self.item(date(2026, 10, 27), 20_000)
        self.foreign_mark(date(2026, 10, 27))
        self.foreign_mark(date(2026, 10, 1))  # 이번 주기 전 결제는 이번 예산 안내에 넣지 않는다
        result = card_reserved(self.user, self.cycle)
        self.assertEqual((result["amount"], result["pending"]), (270_000, []))
        self.assertIn({"card_id": str(self.card.pk), "issuer_code": "0306", "reason": "foreign_amount_pending",
                       "amount": None, "count": 1}, result["notes"])
        self.assertIn({"card_id": str(self.card.pk), "issuer_code": "0306", "reason": "card_overlap_uncertain",
                       "amount": 20_000, "count": 1}, result["notes"])

    def test_first_bill_minus_cycle_card_spending_goes_into_fixed_total(self):
        self.collect_all()
        self.assertEqual(card_reserved(self.user, self.cycle), {"amount": 250_000, "pending": []})
        self.assertEqual(fixed_total(self.user, self.cycle), 350_000)

    def auto_link_card_expense(self):
        from .due_match import match_card_fixed
        from django.db import transaction
        old = FinancialTransaction.objects.create(
            user=self.user, card=self.card, source_kind="card", occurred_on=date(2026, 9, 27),
            amount=12_000, direction="out", transaction_type="spending", merchant_name="넷플릭스",
            confirmed_category_id="CULTURE_LEISURE", confirmation_state="confirmed")
        expense = RecurringExpense.objects.create(
            user=self.user, name="넷플릭스", amount=12_000, category_id="CULTURE_LEISURE",
            status="confirmed", source_type="manual", payment_card=self.card, evidence=[str(old.pk)])
        self.collect_all()
        # 11/06: 청구서 상세에 이번 주기(10/28) 넷플릭스 12,000 이 있음. 아직 거래가 없으면 같은 지출인지 몰라 빼지 않는다
        CardBill.objects.filter(pk=self.bill.pk).update(total_amount=312_000)
        self.item(TODAY, 12_000)
        self.assertEqual(card_reserved(self.user, self.cycle)["amount"], 262_000)   # 312,000 − 50,000 (확인된 중복만)
        tx = FinancialTransaction.objects.create(
            user=self.user, card=self.card, source_kind="card", occurred_on=TODAY,
            amount=12_000, direction="out", transaction_type="spending", merchant_name="넷플릭스",
            confirmed_category_id="CULTURE_LEISURE", confirmation_state="confirmed")
        with transaction.atomic():
            match_card_fixed(expense, [self.cycle], TODAY)
        self.assertFalse(PaymentMatch.objects.filter(transaction=tx).exists())
        expense.refresh_from_db()
        self.assertIn(str(tx.pk), expense.evidence)
        return expense, tx

    def test_evidence_auto_link_keeps_reserved_and_transaction_funding(self):
        from .protect import fixed_linked_amount
        from finance.api import listed_transactions, transaction_item, with_card_fixed_id
        expense, tx = self.auto_link_card_expense()
        self.assertEqual(fixed_linked_amount(self.user, [self.card], self.cycle.starts_on, TODAY), 12_000)
        self.assertEqual(card_reserved(self.user, self.cycle)["amount"], 250_000)   # 고정지출로 잡힌 결제와 같은 지출 → 한 번만
        item = transaction_item(listed_transactions(self.user).get(pk=tx.pk))
        self.assertEqual((item["funding"], item["fixed_expense_id"]), ("fixed", expense.pk))
        # 기억에서 뺀 기록·중지 항목·UUID 표현도 공통 판정 유지, 거래 수와 무관하게 한 쿼리.
        tx.field_states = {"fixed_memory_off": [str(expense.pk)]}
        tx.save()
        expense.evidence = ["{" + str(tx.pk).upper() + "}"]
        expense.status = "stopped"
        expense.save()
        with self.assertNumQueries(1):
            rows = list(with_card_fixed_id(FinancialTransaction.objects.filter(user=self.user)))
        self.assertEqual(next(t.card_fixed_id for t in rows if t.pk == tx.pk), expense.pk)
        self.assertEqual(fixed_linked_amount(self.user, [self.card], self.cycle.starts_on, TODAY), 12_000)

    def test_evidence_actual_card_owns_fixed_amount_once(self):
        from .protect import card_fixed_by_card
        expense, tx = self.auto_link_card_expense()
        other = self.same_issuer_card(is_selected=True)
        expense.payment_card = other
        expense.save()
        self.assertEqual(card_fixed_by_card(self.user, [self.card, other], self.cycle, TODAY),
                         {self.card.pk: 12_000})
        tx.is_canceled, tx.canceled_amount = True, 12_000
        tx.save()
        self.assertEqual(card_fixed_by_card(self.user, [self.card, other], self.cycle, TODAY),
                         {other.pk: 12_000})

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

    def test_missing_bill_can_start_and_rerecommend_in_same_cycle(self):
        self.collect_all()
        # 지난 주기 청구서만 있음. 이번 주기 청구서는 나중에 들어온다.
        CardBill.objects.filter(pk=self.bill.pk).update(withdrawal_due_date=date(2026, 10, 5))
        client = self.client_()
        notes = [{"cardId": str(self.card.pk), "reason": "first_bill_unavailable"}]
        state = client.get(URL).json()
        self.assertIsNone(state["blocked"])
        self.assertEqual((state["base"]["fixedTotal"], state["cardPending"], state["cardNotes"]), (100_000, [], notes))
        first = self.recommend_draft(client)
        self.assertEqual(first.input_snapshot["fixed"]["card_reserved"], 0)
        self.assertEqual(client.get("/api/budgets/").json()["cardNotes"], notes)
        response = self.approve(client, first)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["cardNotes"], notes)
        self.assertEqual(client.get("/api/home/").json()["budget"]["cardNotes"], notes)
        self.assertEqual(client.get("/api/home/").json()["budget"]["cardBillsReady"], [])  # 아직 청구서 없음

        # 청구서를 불러온 것만으로 승인 예산의 금액·안내가 바뀌지 않는다.
        CardBill.objects.filter(pk=self.bill.pk).update(withdrawal_due_date=date(2026, 11, 10))
        self.assertEqual(card_reserved(self.user, self.cycle)["amount"], 250_000)
        self.assertEqual(client.get("/api/budgets/").json()["cardNotes"], notes)
        self.assertEqual(client.get("/api/home/").json()["budget"]["cardNotes"], notes)
        # 11/12: 대신 '지금은 청구서가 나온 카드'로 알려서 화면이 바로 다시 추천으로 보낸다
        self.assertEqual(client.get("/api/home/").json()["budget"]["cardBillsReady"], [str(self.card.pk)])
        self.assertEqual(client.get("/api/budgets/").json()["cardBillsReady"], [str(self.card.pk)])
        first.refresh_from_db()
        self.assertEqual(first.input_snapshot["fixed"]["card_reserved"], 0)

        second = self.recommend_draft(client)
        self.assertEqual(second.cycle_id, first.cycle_id)
        self.assertEqual(second.items.get(category_detail="card_reserved").allocated_amount, 250_000)
        self.assertEqual(client.get("/api/budgets/").json()["cardNotes"], [])
        # 새 초안만으로는 사용 중인 홈 예산이 바뀌지 않는다.
        self.assertEqual(client.get("/api/home/").json()["budget"]["cardNotes"], notes)
        response = self.approve(client, second)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["split"]["fixed"], response.json()["cardNotes"]), (350_000, []))
        self.assertEqual(client.get("/api/home/").json()["budget"]["cardNotes"], [])
        self.assertEqual(client.get("/api/home/").json()["budget"]["cardBillsReady"], [])  # 반영한 뒤에는 없음
        first.refresh_from_db()
        self.assertEqual(first.status, "superseded")

    def test_missing_bill_does_not_bypass_missing_or_failed_spending_history(self):
        self.bill.delete()
        self.run_(CARD_BILL_SYNC, date(2026, 10, 20), TODAY)
        self.run_(CARD_SPENDING_SYNC, date(2026, 10, 20), TODAY, status="failed")
        self.assertEqual(card_reserved(self.user, self.cycle), {
            "amount": None, "pending": [{"card_id": str(self.card.pk), "reason": "spending_history_incomplete"}],
        })
        client = self.client_()
        response = client.put(URL, json.dumps({"livingBudget": 500_000, "savingFloor": 100_000, "cushionMonthly": 100_000}),
                              content_type="application/json")
        self.assertEqual(response.status_code, 409)

    def test_bill_arrival_expires_unapproved_omission_even_when_reserved_stays_zero(self):
        self.collect_all()
        CardBill.objects.filter(pk=self.bill.pk).update(withdrawal_due_date=date(2026, 10, 5))
        client = self.client_()
        draft = self.recommend_draft(client)
        self.bill.items.all().delete()
        CardBill.objects.filter(pk=self.bill.pk).update(withdrawal_due_date=date(2026, 11, 10), total_amount=0)
        self.assertEqual(card_reserved(self.user, self.cycle)["amount"], 0)
        response = self.approve(client, draft)
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "expired"))

    def test_one_missing_issuer_does_not_omit_other_issuer_bill(self):
        self.collect_all()
        connection = Connection.objects.create(user=self.user, business_type="card", organization_code="0302", status="connected")
        other = Card.objects.create(user=self.user, connection=connection, origin="provider", issuer_code="0302",
                                    first_connected_at=self.card.first_connected_at, is_selected=True)
        for product in (CARD_BILL_SYNC, CARD_SPENDING_SYNC):
            SyncRun.objects.create(user=self.user, connection=connection, card=other, product_code=product,
                                   covered_from=self.cycle.starts_on, covered_to=TODAY, status="success")
        result = card_reserved(self.user, self.cycle)
        self.assertEqual(result, {"amount": 250_000, "pending": [],
                                  "notes": [{"card_id": str(other.pk), "reason": "first_bill_unavailable"}]})

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
        # 11/06: 청구서에 없는 새 소비는 선차감을 바꾸지 않는다 (이번 주기 소비 전체를 빼지 않음) → 초안 그대로 승인
        self.assertEqual(card_reserved(self.user, self.cycle)["amount"], 250_000)
        response = self.approve(client, draft)
        self.assertEqual(response.status_code, 200, response.content)

    def test_changing_selected_cards_expires_draft(self):
        self.collect_all()
        client = self.client_()
        draft = self.recommend_draft(client)
        self.card.is_selected = False
        self.card.save()
        response = self.approve(client, draft)
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "expired"))

    def test_payment_check_does_not_expire_draft_but_bill_change_does(self):
        # 11/06: 납부 확인은 예산 반영액을 바꾸지 않는다(이미 낸 카드값도 이번 월급에서 쓴 돈) → 초안 유지.
        # 청구액 정정처럼 선차감이 바뀌면 낡은 초안은 승인하지 않고 만료
        from finance.card_payments import USER_CHECK
        self.collect_all()
        client = self.client_()
        draft = self.recommend_draft(client)
        CardBill.objects.filter(pk=self.bill.pk).update(field_states={USER_CHECK: {"answer": "paid", "remaining_amount": None, "bill_total": 300_000}})
        self.assertEqual(self.approve(client, draft).status_code, 200)
        Budget.objects.filter(pk=draft.pk).update(status="superseded")
        draft = self.recommend_draft(client)
        CardBill.objects.filter(pk=self.bill.pk).update(total_amount=320_000)
        self.item(date(2026, 10, 11), 20_000)
        response = self.approve(client, draft)
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "expired"))

    def test_approved_budget_is_not_recalculated_after_payment_check(self):
        from finance.card_payments import USER_CHECK
        self.collect_all()
        client = self.client_()
        draft = self.recommend_draft(client)
        self.assertEqual(self.approve(client, draft).status_code, 200)
        CardBill.objects.filter(user=self.user).update(field_states={USER_CHECK: {"answer": "paid", "remaining_amount": None, "bill_total": 300_000}})
        draft.refresh_from_db()
        self.assertEqual((draft.status, draft.items.get(category_detail="card_reserved").allocated_amount), ("approved", 250_000))

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
        # 11/06: 카드 고정지출을 등록했다는 것만으로 청구서에서 빼지 않는다 (이 청구서에 이번 주기 결제분이 있다는 근거 없음)
        self.assertEqual(card_reserved(self.user, self.cycle)["amount"], 250_000)
        self.assertEqual(fixed_total(self.user, self.cycle), 360_000)   # 고정비 100,000 + 구독 10,000 + 카드 250,000
        # 이번 주기 구독 결제(10/28)가 청구서에 들어 있고 고정지출에 연결되면 그 금액만 한 번 뺀다
        CardBill.objects.filter(pk=self.bill.pk).update(total_amount=310_000)
        self.item(TODAY, 10_000)
        linked = FinancialTransaction.objects.create(user=self.user, card=self.card, source_kind="card", occurred_on=TODAY,
                                                     amount=10_000, direction="out", transaction_type="spending",
                                                     confirmed_category_id="CULTURE_LEISURE", confirmation_state="confirmed")
        due = PaymentDue.objects.create(user=self.user, recurring=expense, obligation_type="recurring", occurrence_key="sub-1",
                                        due_date=TODAY, confirmed_amount=10_000, paid_amount=10_000,
                                        payment_state="paid", obligation_state="active")
        PaymentMatch.objects.create(user=self.user, due=due, transaction=linked, matched_amount=10_000)
        self.assertEqual(card_reserved(self.user, self.cycle)["amount"], 250_000)   # 310,000 − 50,000 − 10,000

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
        self.run_(CARD_BILL_SYNC, date(2026, 10, 1), TODAY)  # 청구서는 달 단위(1일부터)로 기록된다 (sync_cards)
        self.run_(CARD_SPENDING_SYNC, date(2026, 10, 25), date(2026, 10, 26))
        self.assertEqual(card_reserved(self.user, self.cycle),
                         {"amount": None, "pending": [{"card_id": str(self.card.pk), "reason": "spending_history_incomplete"}]})
        self.run_(CARD_SPENDING_SYNC, date(2026, 10, 26), TODAY)
        self.assertEqual(card_reserved(self.user, self.cycle), {"amount": 250_000, "pending": []})

    def test_fixed_expense_without_card_but_paid_by_this_card_is_not_counted_twice(self):
        self.collect_all()
        expense = RecurringExpense.objects.create(user=self.user, name="넷플릭스", amount=17_000, category_id="CULTURE_LEISURE",
                                                  status="confirmed", source_type="manual")  # 결제 카드 안 적음
        CardBill.objects.filter(pk=self.bill.pk).update(total_amount=317_000)
        self.item(TODAY, 17_000)   # 이번 주기 결제분이 청구서에 있음
        linked = FinancialTransaction.objects.create(user=self.user, card=self.card, source_kind="card", occurred_on=TODAY,
                                                     amount=17_000, direction="out", transaction_type="spending",
                                                     confirmed_category_id="CULTURE_LEISURE", confirmation_state="confirmed")
        due = PaymentDue.objects.create(user=self.user, recurring=expense, obligation_type="recurring", occurrence_key="nf-1",
                                        due_date=TODAY, confirmed_amount=17_000, paid_amount=17_000,
                                        payment_state="paid", obligation_state="active")
        PaymentMatch.objects.create(user=self.user, due=due, transaction=linked, matched_amount=17_000)
        # 317,000 − 50,000(소비) − 17,000(고정지출로 잡힌 결제) — 같은 결제를 소비 · 고정비로 두 번 빼지 않음
        self.assertEqual(card_reserved(self.user, self.cycle)["amount"], 250_000)

    def test_fixed_expense_is_subtracted_once_when_registered_and_paid_cards_differ(self):
        # 리뷰 10/28: 고정지출에 적은 카드(A)와 실제 결제 카드(B)가 다르면 두 카드에서 각각 빼면 안 됨
        # (10/29 카드사 단위: 두 카드를 다른 카드사로 두어 선차감이 두 번 생기게 함)
        self.collect_all()
        connection = Connection.objects.create(user=self.user, business_type="card", organization_code="0301", status="connected")
        other = Card.objects.create(user=self.user, connection=connection, origin="provider", issuer_code="0301",
                                    first_connected_at=datetime(2026, 10, 19, 15, tzinfo=dt_timezone.utc), is_selected=True)
        b_bill = CardBill.objects.create(user=self.user, connection=connection, card=None, issuer_code="0301",
                                         billing_month=date(2026, 11, 1), total_amount=117_000, source_type="provider",
                                         scope_type="issuer", withdrawal_due_date=date(2026, 11, 12), items_state="complete")
        self.item(date(2026, 10, 1), 100_000, bill=b_bill)
        self.item(TODAY, 17_000, bill=b_bill)   # B 청구서에 이번 주기 구독 결제분
        SyncRun.objects.create(user=self.user, connection=connection, card=other, product_code=CARD_BILL_SYNC,
                               covered_from=date(2026, 10, 20), covered_to=TODAY, status="success")
        SyncRun.objects.create(user=self.user, connection=connection, card=other, product_code=CARD_SPENDING_SYNC,
                               covered_from=date(2026, 10, 25), covered_to=TODAY, status="success")
        before = card_reserved(self.user, self.cycle)["amount"]  # 250,000 + 117,000 (B 의 17,000 은 아직 거래가 없어 확인 못 함)
        self.assertEqual(before, 367_000)
        expense = RecurringExpense.objects.create(user=self.user, name="구독", amount=17_000, category_id="CULTURE_LEISURE",
                                                  status="confirmed", source_type="manual", payment_card=self.card)  # A로 등록
        self.assertEqual(card_reserved(self.user, self.cycle)["amount"], before)  # 등록 카드(A)라는 이유로 A 청구서에서 빼지 않음
        linked = FinancialTransaction.objects.create(user=self.user, card=other, source_kind="card", occurred_on=TODAY,
                                                     amount=17_000, direction="out", transaction_type="spending",
                                                     confirmed_category_id="CULTURE_LEISURE", confirmation_state="confirmed")
        due = PaymentDue.objects.create(user=self.user, recurring=expense, obligation_type="recurring", occurrence_key="sub-b",
                                        due_date=TODAY, confirmed_amount=17_000, paid_amount=17_000,
                                        payment_state="paid", obligation_state="active")
        PaymentMatch.objects.create(user=self.user, due=due, transaction=linked, matched_amount=17_000)
        # 실제로 B로 결제돼 B 청구서에 든 것만 B에서 한 번 (A 250,000 그대로 + B 117,000 − 17,000)
        self.assertEqual(card_reserved(self.user, self.cycle)["amount"], 350_000)

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
        self.assertEqual(card_reserved(self.user, self.cycle), {"amount": 250_000, "pending": []})  # 청구서에 없는 소비
        # 11/06: B 소비(10/27 40,000)가 이 청구서에 들어 있으면 — B 가 관리 대상이 아니면 예산에 안 잡혀 중복 아님 → 그대로 떼어 둔다
        CardBill.objects.filter(pk=self.bill.pk).update(total_amount=340_000)
        self.item(date(2026, 10, 27), 40_000)
        self.assertEqual(card_reserved(self.user, self.cycle)["amount"], 290_000)
        # B 로 내는 고정지출 등록만으로는 빼지 않는다 (청구서에 그 결제분이 있다는 근거 없음)
        RecurringExpense.objects.create(user=self.user, name="구독", amount=20_000, category_id="CULTURE_LEISURE",
                                        status="confirmed", source_type="manual", payment_card=other)
        self.assertEqual(card_reserved(self.user, self.cycle)["amount"], 290_000)
        # B 도 고르면 B 소비가 이번 주기 생활비에 잡히므로 그 40,000 을 중복으로 뺀다. 첫 청구서는 카드사에 한 번뿐
        Card.objects.filter(pk=other.pk).update(is_selected=True)
        self.assertEqual(card_reserved(self.user, self.cycle)["amount"], 250_000)

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
            "202611": bill(due="20261105", resBillType="", resTotalAmount="300,000", resChargeHistoryList=[
                {"resPaymentType": "1", "resPaymentAmt": "300,000", "resUsedDate": "20261001"},
            ]),
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
        self.assertEqual(card_reserved(self.user, self.cycle)["notes"],
                         [{"card_id": str(card.pk), "reason": "first_bill_unavailable"}])

        self.sync(TODAY, bill_months(TODAY))
        # 이번 주기에 결제일인 11/5 청구서 300,000. 상세는 10/1 구매 1건뿐이라 10/26 소비(50,000)는 이 청구서에 없음 → 빼지 않는다
        # (예전 계산은 이번 주기 카드 소비 전체를 빼서 250,000)
        self.assertEqual(card_reserved(self.user, self.cycle), {"amount": 300_000, "pending": []})
        data = client.get(URL).json()
        self.assertIsNone(data["blocked"])
        self.assertEqual((data["base"]["fixedTotal"], data["cardPending"]), (300_000, []))

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
        # 8월 빈 기간은 이번 주기 판단과 무관. 11월 청구서는 아직 없어 미반영 안내와 함께 시작 가능.
        self.assertEqual(card_reserved(self.user, self.cycle)["notes"],
                         [{"card_id": str(card.pk), "reason": "first_bill_unavailable"}])
        months = bill_months(TODAY, last_bill_day(self.connection))
        self.assertEqual(months, ("202608", "202609", "202610", "202611"))
        self.sync(TODAY, months, start=date(2026, 8, 13))
        # 승인 예산이 아직 없으므로 이번 주기가 최초 적용 주기: 11/5 청구서 300,000 (10/26 소비는 이 청구서에 없음)
        self.assertEqual(card_reserved(self.user, self.cycle), {"amount": 300_000, "pending": []})

    def test_payday_sixth_start_without_december_bill_then_reflect_on_november_24(self):
        from finance.test_card_sync import approval, bill
        from finance.sync_ranges import bill_months

        FinancialProfile.objects.filter(user=self.user).update(payday=6)
        self.cycle.delete()
        self.cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 11, 6), ends_before=date(2026, 12, 6))
        self.approvals = []
        today = date(2026, 11, 9)
        client = APIClient()
        client.force_authenticate(self.user)

        def recommend_and_approve():
            state = client.get(URL).json()
            self.assertIsNone(state["blocked"])
            values = {key: state["recommended"][key] for key in ("livingBudget", "savingFloor", "cushionMonthly")}
            response = client.put(URL, values, format="json")
            self.assertEqual(response.status_code, 200, response.content)
            response = client.post("/api/budgets/recommend/", {}, format="json", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
            self.assertEqual(response.status_code, 202, response.content)
            draft = Budget.objects.get(user=self.user, status="validated")
            response = client.post(f"/api/budgets/{draft.pk}/approve/", {}, format="json", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
            self.assertEqual(response.status_code, 200, response.content)
            return draft

        # 모델 필드의 default는 import 때 바인딩되므로 조회 시작 시각도 테스트 날짜로 맞춘다.
        now = lambda: datetime(today.year, today.month, today.day, 3, tzinfo=dt_timezone.utc)
        with patch("django.utils.timezone.now", side_effect=now), \
                patch.object(SyncRun._meta.get_field("started_at"), "get_default", side_effect=now), \
                patch("django.utils.timezone.localdate", side_effect=lambda value=None, timezone=None:
                      today if value is None else CardReservedTest.localdate(value, timezone)):
            # 11/5 청구서는 이전 주기. 12/5 청구서는 요청해도 아직 응답에 없음.
            self.sync(today, bill_months(today))
            Card.objects.filter(user=self.user).update(is_selected=True)
            first = recommend_and_approve()
            self.assertEqual(first.input_snapshot["fixed"]["card_reserved"], 0)
            self.assertEqual(first.input_snapshot["fixed"]["card_notes"][0]["reason"], "first_bill_unavailable")

            today = date(2026, 11, 24)
            self.approvals = [approval(resUsedDate="20261110", resUsedAmount="50,000", resMemberStoreName="스타벅스",
                                       commStartDate="20260601", commEndDate="20261124")]
            self.bills["202612"] = bill(due="20261205", resBillType="", resTotalAmount="350,000", resChargeHistoryList=[
                {"resPaymentType": "1", "resPaymentAmt": "300,000", "resUsedDate": "20261102"},
                {"resPaymentType": "1", "resPaymentAmt": "50,000", "resUsedDate": "20261110"},
            ])
            self.sync(today, bill_months(today))
            # 같은 주기에서 새 청구서의 이번 주기 거래(5만 원)는 중복 제외하고 이전 사용분 30만 원만 반영.
            self.assertEqual(card_reserved(self.user, self.cycle)["amount"], 300_000)
            self.assertTrue(client.get("/api/home/").json()["budget"]["cardNotes"])
            second = recommend_and_approve()
            self.assertEqual(second.cycle_id, first.cycle_id)
            self.assertEqual(second.input_snapshot["fixed"]["card_reserved"], 300_000)
            self.assertEqual(client.get("/api/home/").json()["budget"]["cardNotes"], [])


class BudgetReserveVsRemainingTest(TestCase):
    """11/06 필수 예시 (다른 고정비 · 할부 없음). 주기 10/25 ~ 11/25, 오늘 11/12, 청구서 결제 11/10 · 청구액 500,000.
    청구서 상세 = 주기 전 구매 400,000 + 이번 주기(10/26) 구매 100,000. 이번 주기 관리 카드 소비 = 100,000(청구서에 있음) + 200,000(없음)"""

    def setUp(self):
        self.user = get_user_model().objects.create_user(email="reserve-remaining@example.invalid")
        FinancialProfile.objects.create(user=self.user, housing_type="family", monthly_take_home_income=2_800_000, payday=25)
        self.cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25))
        self.conn = Connection.objects.create(user=self.user, business_type="card", organization_code="0306", status="connected")
        Consent.objects.create(user=self.user, purpose="finance_inquiry", product_scope="card", document_version="v1", document_hash="t")
        bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        self.account = BankAccount.objects.create(user=self.user, connection=bank, source_account_key="rr", account_type="demand")
        self.card = Card.objects.create(user=self.user, connection=self.conn, origin="provider", issuer_code="0306", is_selected=True,
                                        first_connected_at=datetime(2026, 10, 19, 15, tzinfo=dt_timezone.utc))
        for product in (CARD_BILL_SYNC, CARD_SPENDING_SYNC):
            SyncRun.objects.create(user=self.user, connection=self.conn, card=self.card, product_code=product,
                                   covered_from=date(2026, 10, 1), covered_to=date(2026, 11, 12), status="success")
        self.bill = CardBill.objects.create(user=self.user, connection=self.conn, issuer_code="0306", statement_key="b",
                                            scope_type="issuer", billing_month=date(2026, 11, 1), total_amount=500_000,
                                            source_type="provider", withdrawal_due_date=date(2026, 11, 10), items_state="complete")
        self.item(date(2026, 10, 20), 400_000)
        self.in_bill = self.item(date(2026, 10, 26), 100_000)
        self.spend(date(2026, 10, 26), 100_000)        # 청구서에 든 이번 주기 소비
        self.spend(date(2026, 11, 3), 200_000)         # 다음 청구서로 갈 이번 주기 소비
        clock = patch("django.utils.timezone.localdate", side_effect=lambda value=None, timezone=None:
                      date(2026, 11, 12) if value is None else CardReservedTest.localdate(value, timezone))
        clock.start()
        self.addCleanup(clock.stop)
        external = patch("finance.codef._request", side_effect=AssertionError("CODEF 호출 금지"))
        external.start()
        self.addCleanup(external.stop)

    item = CardReservedTest.item

    def spend(self, day, amount, **fields):
        return FinancialTransaction.objects.create(**{**dict(
            user=self.user, card=self.card, source_kind="card", occurred_on=day, amount=amount, direction="out",
            transaction_type="spending", confirmed_category_id="FOOD", confirmation_state="confirmed"), **fields})

    def reserved(self):
        return card_reserved(self.user, self.cycle)

    def remaining(self):
        from finance.card_payments import bill_status
        self.bill.refresh_from_db()
        return bill_status(self.bill, PaymentDue.objects.filter(bill=self.bill).first(), date(2026, 11, 12))["remaining_amount"]

    def answer(self, answer, remaining=None):
        from finance.card_payments import USER_CHECK
        CardBill.objects.filter(pk=self.bill.pk).update(field_states={USER_CHECK: {
            "answer": answer, "remaining_amount": remaining, "bill_total": 500_000}})

    def pay(self, day, amount):
        out = FinancialTransaction.objects.create(user=self.user, source_kind="bank", account=self.account, occurred_on=day,
                                                  amount=amount, direction="out", transaction_type="card_payment",
                                                  confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review")
        due, _ = PaymentDue.objects.get_or_create(bill=self.bill, defaults=dict(
            user=self.user, obligation_type="card_bill", occurrence_key="b", due_date=date(2026, 11, 10),
            confirmed_amount=500_000, source_type="provider", confirmation_state="unknown"))
        PaymentMatch.objects.create(user=self.user, due=due, transaction=out, matched_amount=amount)

    def test_a_only_spending_inside_the_bill_is_subtracted(self):
        # 예전: 500,000 − 이번 주기 소비 300,000 = 200,000 (다음 청구서로 갈 200,000 까지 빠짐). 이제 400,000
        self.assertEqual(self.reserved(), {"amount": 400_000, "pending": []})

    def test_b_paid_with_this_salary_keeps_reserve_and_home_shows_zero(self):
        self.pay(date(2026, 11, 10), 500_000)          # 이번 월급(주기 안)으로 전액 납부
        self.assertEqual((self.reserved()["amount"], self.remaining()), (400_000, 0))
        self.answer("paid")                            # 사용자 확인도 반영액을 줄이지 않음
        self.assertEqual((self.reserved()["amount"], self.remaining()), (400_000, 0))

    def test_c_partial_payment_changes_only_remaining(self):
        self.answer("partial", 200_000)
        self.assertEqual((self.reserved()["amount"], self.remaining()), (400_000, 200_000))
        self.answer("unpaid")
        self.assertEqual((self.reserved()["amount"], self.remaining()), (400_000, 500_000))

    def test_payment_before_cycle_start_was_previous_salary(self):
        self.pay(date(2026, 10, 24), 150_000)          # 주기 시작(10/25) 전 선결제 = 지난 월급 → 이번 예산에서 다시 떼지 않음
        self.assertEqual(self.reserved()["amount"], 250_000)

    def test_d_ambiguous_candidates_are_not_linked_and_not_subtracted(self):
        self.spend(date(2026, 10, 26), 100_000)        # 같은 날 · 같은 금액 거래가 둘 → 상세 1건과 짝을 정할 수 없음
        result = self.reserved()
        self.assertEqual(result["amount"], 500_000)     # 확인 못 한 100,000 은 빼지 않는다 (넉넉하게)
        self.assertEqual(result["notes"], [{"card_id": str(self.card.pk), "issuer_code": "0306",
                                            "reason": "card_overlap_uncertain", "amount": 100_000, "count": 1}])

    def test_d_partial_details_do_not_invent_missing_items(self):
        CardBill.objects.filter(pk=self.bill.pk).update(items_state="partial", total_amount=520_000)
        result = self.reserved()
        self.assertEqual(result["amount"], 420_000)     # 확인된 중복 100,000 만 빼고, 빠진 상세는 모름
        self.assertEqual([(n["reason"], n["amount"], n["count"]) for n in result["notes"]], [("bill_items_incomplete", None, None)])

    def test_d_cancel_installment_fee_and_unmanaged(self):
        from finance.models import CardBillItem
        # 취소: 이번 주기 구매가 전액 취소돼 생활비엔 0원 → 청구서에 남은 100,000 은 중복이 아니라 그대로 떼어 둔다
        FinancialTransaction.objects.filter(amount=100_000).update(is_canceled=True, canceled_amount=100_000)
        self.assertEqual(self.reserved()["amount"], 500_000)
        FinancialTransaction.objects.filter(amount=100_000).update(is_canceled=False, canceled_amount=None)
        # 할부: 이번 주기 300,000 3개월 할부 — 생활비엔 300,000, 청구서엔 1회차 100,000 만 → 그 회차만 중복
        CardBillItem.objects.filter(pk=self.in_bill.pk).update(item_type="installment", original_purchase_amount=300_000,
                                                                installment_months=3, installment_round=1)
        FinancialTransaction.objects.filter(amount=100_000, occurred_on=date(2026, 10, 26)).update(amount=300_000)
        self.assertEqual(self.reserved()["amount"], 400_000)
        # 수수료 항목은 이번 주기 소비가 아니라 빼지 않는다
        CardBill.objects.filter(pk=self.bill.pk).update(total_amount=503_000)
        self.item(date(2026, 10, 28), 3_000, item_type="fee")
        self.assertEqual(self.reserved()["amount"], 403_000)
        # 관리 대상 아닌 카드의 소비는 예산에 안 잡혀 중복이 아님
        Card.objects.filter(pk=self.card.pk).update(is_selected=False)
        other = Card.objects.create(user=self.user, connection=self.conn, origin="provider", issuer_code="0306", is_selected=True,
                                    first_connected_at=datetime(2026, 10, 19, 15, tzinfo=dt_timezone.utc))
        self.assertEqual(card_reserved(self.user, self.cycle)["amount"], 503_000)
        other.delete()

    def test_total_lower_than_details_is_pending_not_zero_or_sum(self):
        # 결제일 당일 조회 총액 0원 · 상세 합계 500,000 같은 불일치: 0원이나 상세 합계로 고르지 않고 보류
        CardBill.objects.filter(pk=self.bill.pk).update(total_amount=0, items_state="partial")
        self.assertEqual(self.reserved()["pending"], [{"card_id": str(self.card.pk), "reason": "bill_total_mismatch"}])
        from finance.card_payments import bill_status
        self.bill.refresh_from_db()
        status = bill_status(self.bill, None, date(2026, 11, 12))
        self.assertEqual((status["status"], status["remaining_amount"]), ("total_mismatch", None))   # '낼 금액 없음'이 아님


class ReportedCaseTest(TestCase):
    """실제 확인 사례 재현 (합성): 주기 10/10 ~ 11/10, 현대카드 청구서 결제 11/5, 최초 연결 11/6, 조회 · 카드 지정 완료"""

    def setUp(self):
        self.user = get_user_model().objects.create_user(email="reported-case@example.invalid")
        FinancialProfile.objects.create(user=self.user, housing_type="family", monthly_take_home_income=2_800_000, payday=10)
        self.cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 10, 10), ends_before=date(2026, 11, 10))
        conn = Connection.objects.create(user=self.user, business_type="card", organization_code="0302", status="connected")
        Consent.objects.create(user=self.user, purpose="finance_inquiry", product_scope="card", document_version="v1", document_hash="t")
        self.card = Card.objects.create(user=self.user, connection=conn, origin="provider", issuer_code="0302", is_selected=True,
                                        first_connected_at=datetime(2026, 11, 5, 15, tzinfo=dt_timezone.utc))
        for product in (CARD_BILL_SYNC, CARD_SPENDING_SYNC):
            SyncRun.objects.create(user=self.user, connection=conn, card=self.card, product_code=product,
                                   covered_from=date(2026, 10, 1), covered_to=date(2026, 11, 6), status="success")
        self.bill = CardBill.objects.create(user=self.user, connection=conn, issuer_code="0302", statement_key="h", scope_type="issuer",
                                            billing_month=date(2026, 11, 1), total_amount=511_515, source_type="provider",
                                            withdrawal_due_date=date(2026, 11, 5), field_states={"codefBillType": ""})
        clock = patch("django.utils.timezone.localdate", side_effect=lambda value=None, timezone=None:
                      date(2026, 11, 7) if value is None else CardReservedTest.localdate(value, timezone))
        clock.start()
        self.addCleanup(clock.stop)
        external = patch("finance.codef._request", side_effect=AssertionError("CODEF 호출 금지"))
        external.start()
        self.addCleanup(external.stop)

    def check(self, answer, remaining=None):
        from finance.card_payments import USER_CHECK
        CardBill.objects.filter(pk=self.bill.pk).update(field_states={"codefBillType": "", USER_CHECK: {
            "answer": answer, "remaining_amount": remaining, "bill_total": 511_515}})

    def test_bill_before_connection_is_used_without_resync_or_payment_block(self):
        # 예전: 연결일(11/6) 전 청구서를 빼서 first_bill_unavailable → 재조회 · 카드번호 요구.
        # 이제: 이번 주기 결제 청구서로 계산. 납부 확인 여부로 보류하지 않고, 답해도 예산 반영액은 그대로(이번 월급에서 쓴 돈)
        for answer, remaining in ((None, None), ("paid", None), ("partial", 200_000), ("unpaid", None)):
            with self.subTest(answer=answer):
                if answer:
                    self.check(answer, remaining)
                result = card_reserved(self.user, self.cycle)
                # 상세가 없어 중복을 확인할 근거가 없음 → 빼지 않고 안내 (빠진 항목 건수 · 금액은 모름)
                self.assertEqual((result["amount"], result["pending"]), (511_515, []))
                self.assertEqual([n["reason"] for n in result["notes"]], ["bill_items_incomplete"])


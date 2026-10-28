"""카드 3개 API 회귀 검사: python backend/manage.py test finance.test_cards"""
import json
import uuid
from datetime import date, datetime, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from budgets.models import BudgetCycle
from users.models import Consent

from .models import BankAccount, Card, CardBill, CardBillItem, Connection, FinancialTransaction, PaymentDue, RecurringExpense


URL = "/api/finance/connect/cards/"
FETCHED = datetime(2026, 10, 21, 0, 30, tzinfo=dt_timezone.utc)


@override_settings(ALLOWED_HOSTS=["testserver"])
class CardAPITest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email=f"card-test-{uuid.uuid4().hex}@example.invalid")
        self.other = get_user_model().objects.create_user(email=f"card-other-{uuid.uuid4().hex}@example.invalid")
        self.conn = self.connection(self.user, "0306")
        self.other_conn = self.connection(self.other, "0306")
        self.card = self.make_card(self.conn, name="Deep Dream", issuer_code="0306", is_selected=True)
        self.second = self.make_card(self.conn, name="두번째 카드")
        self.foreign = self.make_card(self.other_conn, name="다른 회원 카드", is_selected=True)
        self.consent = Consent.objects.create(
            user=self.user, purpose="finance_inquiry", product_scope="card",
            document_version="v1", document_hash="test",
        )
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.client.get("/api/csrf/")
        self.token = self.client.cookies["csrftoken"].value
        external = patch("finance.codef._request", side_effect=AssertionError("외부 CODEF 호출 금지"))
        external.start()
        self.addCleanup(external.stop)

    def connection(self, user, code, status="connected", business="card"):
        return Connection.objects.create(
            user=user, business_type=business, organization_code=code, status=status,
            disconnected_at=timezone.now() if status == "disconnected" else None,
        )

    def make_card(self, conn, **fields):
        return Card.objects.create(
            user=conn.user if conn else self.user, connection=conn,
            origin="provider", fetched_at=FETCHED, masked_number="1234-****-****-5678", **fields,
        )

    def bill(self, **fields):
        data = dict(user=self.user, connection=self.conn, issuer_code="0306", billing_month=date(2026, 10, 1),
                    total_amount=420_000, source_type="provider")
        data.update(fields)
        return CardBill.objects.create(**data)

    def item(self, bill, **fields):
        data = dict(user=self.user, bill=bill, item_type="one_time", merchant_name="가전 매장", billed_amount=360_000)
        data.update(fields)
        return CardBillItem.objects.create(**data)

    def get_bill(self, bill):
        return self.client.get(f"/api/finance/card-bills/{bill.pk}/")

    def put(self, body):
        return self.client.put(URL, data=json.dumps(body), content_type="application/json", HTTP_X_CSRFTOKEN=self.token)

    def selection(self):
        return dict(Card.objects.filter(user=self.user).values_list("pk", "is_selected"))

    def test_candidates_scope_status_timestamp_and_private_fields(self):
        relogin = self.make_card(self.connection(self.user, "0301", "relogin"), card_status="휴면", card_type="신용")
        self.make_card(self.connection(self.user, "0303", "disconnected"))
        self.make_card(None)
        self.make_card(self.connection(self.user, "0004", business="bank"))
        Card.objects.create(user=self.user, connection=self.other_conn, origin="provider")
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        rows = {row["cardId"]: row for row in response.json()["items"]}
        self.assertEqual(set(rows), {str(self.card.pk), str(self.second.pk), str(relogin.pk)})
        self.assertEqual(rows[str(relogin.pk)]["status"], "relogin")
        self.assertEqual(rows[str(relogin.pk)]["cardStatus"], "휴면")
        self.assertEqual(rows[str(self.card.pk)]["fetchedAt"], "2026-10-21T09:30:00+09:00")
        self.assertEqual(rows[str(self.card.pk)]["displayName"], "Deep Dream · ****5678")
        self.assertEqual(set(rows[str(self.card.pk)]), {"cardId", "displayName", "status", "cardType", "cardStatus", "isSelected", "fetchedAt"})

    def test_name_fallback_and_missing_last_four_digits(self):
        for name, issuer, number, expected in (
            (None, "0306", None, "신한카드"),
            (None, "missing", "1234-****", "카드"),
            (None, "missing", "***123", "카드"),
            ("내 카드", "0306", "****4321", "내 카드 · ****4321"),
        ):
            with self.subTest(expected=expected):
                Card.objects.filter(pk=self.card.pk).update(name=name, issuer_code=issuer, masked_number=number, fetched_at=None)
                row = next(row for row in self.client.get(URL).json()["items"] if row["cardId"] == str(self.card.pk))
                self.assertEqual(row["displayName"], expected)
                self.assertIsNone(row["fetchedAt"])

    def test_missing_and_revoked_consent(self):
        bill = self.bill()
        for scope, revoked in (("bank", None), ("card", timezone.now())):
            Consent.objects.filter(pk=self.consent.pk).update(product_scope=scope, revoked_at=revoked)
            for url in (URL, f"/api/finance/card-bills/{bill.pk}/"):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["error"]["code"], "consent_required")
                self.assertNotIn("fields", response.json()["error"])

    def test_auth_and_csrf(self):
        bill = self.bill()
        anonymous = Client()
        for url in (URL, f"/api/finance/card-bills/{bill.pk}/"):
            response = anonymous.get(url)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["error"]["code"], "not_authenticated")
        response = anonymous.put(URL, data='{"selectedCardIds": []}', content_type="application/json")
        self.assertEqual(response.status_code, 403)
        before = self.selection()
        response = self.client.put(URL, data='{"selectedCardIds": []}', content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "permission_denied")
        self.assertEqual(self.selection(), before)

    def test_full_selection_replacement_repeat_and_empty(self):
        disconnected = self.make_card(self.connection(self.user, "0303", "disconnected"), is_selected=True)
        relogin = self.make_card(self.connection(self.user, "0301", "relogin"))
        orphan = self.make_card(None, is_selected=True)
        ids = [str(self.second.pk), str(relogin.pk)]
        first = self.put({"selectedCardIds": ids})
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual({key for key, value in self.selection().items() if value}, {self.second.pk, relogin.pk})
        self.assertFalse(self.selection()[disconnected.pk])
        self.assertFalse(self.selection()[orphan.pk])
        self.assertEqual(self.put({"selectedCardIds": ids}).json(), first.json())
        empty = self.put({"selectedCardIds": []})
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(len(empty.json()["items"]), 3)
        self.assertTrue(all(not row["isSelected"] for row in empty.json()["items"]))
        self.foreign.refresh_from_db()
        self.assertTrue(self.foreign.is_selected)
        self.card.refresh_from_db()
        self.assertEqual(self.card.fetched_at, FETCHED)
        self.conn.refresh_from_db()
        self.assertEqual(self.conn.status, "connected")

    def test_invalid_ids_are_same_error_and_entire_selection_unchanged(self):
        disconnected = self.make_card(self.connection(self.user, "0303", "disconnected"))
        orphan = self.make_card(None)
        mismatched = Card.objects.create(user=self.user, connection=self.other_conn, origin="provider")
        for value in ("abc", str(uuid.uuid4()), str(self.foreign.pk), str(disconnected.pk), str(orphan.pk), str(mismatched.pk), 1, True, None, {}, []):
            with self.subTest(value=value):
                before = self.selection()
                response = self.put({"selectedCardIds": [str(self.second.pk), value]})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["fields"], {"selectedCardIds": ["선택할 수 없는 카드예요."]})
                self.assertEqual(self.selection(), before)
        response = self.put({"selectedCardIds": [str(self.card.pk), str(self.card.pk)]})
        self.assertEqual(response.status_code, 400)

    def test_required_array_unknown_fields_and_non_object(self):
        before = self.selection()
        for data in ({}, {"selectedCardIds": None}, {"selectedCardIds": ""}, {"selectedCardIds": {}},
                     {"selectedCardIds": True}, {"selectedCardIds": [], "status": "connected"}, [], None, "text"):
            with self.subTest(data=data):
                response = self.put(data)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(response.json()["error"]["code"], "invalid")
                self.assertEqual(self.selection(), before)

    def test_selection_does_not_modify_history_recurring_or_budget(self):
        expense = RecurringExpense.objects.create(
            user=self.user, payment_card=self.card, name="구독", amount=10_000,
            category_id="HOUSING_TELECOM", status="confirmed", source_type="manual",
        )
        tx = FinancialTransaction.objects.create(
            user=self.user, source_kind="card", card=self.card, occurred_on=date(2026, 10, 20),
            amount=10_000, direction="out", transaction_type="spending",
            confirmed_category_id="HOUSING_TELECOM", confirmation_state="confirmed",
        )
        cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25), confirmed_income=3_000_000)
        self.assertEqual(self.put({"selectedCardIds": []}).status_code, 200)
        expense.refresh_from_db()
        tx.refresh_from_db()
        cycle.refresh_from_db()
        self.assertEqual(expense.payment_card_id, self.card.pk)
        self.assertEqual(expense.status, "confirmed")
        self.assertEqual(tx.card_id, self.card.pk)
        self.assertEqual(cycle.confirmed_income, 3_000_000)
        self.assertFalse(PaymentDue.objects.filter(user=self.user).exists())

    def test_failed_response_rolls_back_selection(self):
        before = self.selection()
        with override_settings(DEBUG=True), patch("finance.api_cards.candidate_item", side_effect=RuntimeError("rollback")):
            with self.assertRaises(RuntimeError):
                self.put({"selectedCardIds": [str(self.second.pk)]})
        self.assertEqual(self.selection(), before)

    def test_put_can_clear_without_read_consent(self):
        self.consent.delete()
        self.assertEqual(self.put({"selectedCardIds": []}).status_code, 200)

    def test_no_candidates_returns_empty_list(self):
        self.conn.status, self.conn.disconnected_at = "disconnected", timezone.now()
        self.conn.save(update_fields=["status", "disconnected_at"])
        self.assertEqual(self.client.get(URL).json(), {"items": []})
        self.assertEqual(self.put({"selectedCardIds": []}).json(), {"items": []})

    def test_bill_unknown_values_preserve_null_and_zero(self):
        bill = self.bill(total_amount=0, issuer_code="missing")
        response = self.get_bill(bill)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["billingMonth"], "2026-10")
        self.assertEqual(body["totalAmount"], 0)
        for key in ("issuerName", "scopeType", "confirmationStatus", "fetchedAt", "paymentDueDate", "withdrawalDueDate", "paymentAccount", "card", "cards", "itemsState", "items"):
            self.assertIsNone(body[key], key)

    def test_full_issuer_bill_and_installment_do_not_recompute_total(self):
        bank = self.connection(self.user, "0004", business="bank")
        account = BankAccount.objects.create(user=self.user, connection=bank, source_account_key="test", alias="급여통장", masked_number="123-****-5678")
        bill = self.bill(scope_type="issuer", card=self.card, payment_account=account, items_state="complete", confirmation_state="confirmed", fetched_at=FETCHED, payment_due_date=date(2026, 10, 25))
        self.item(bill, card=self.card, original_purchase_amount=360_000, purchased_on=date(2026, 9, 15))
        self.item(bill, card=self.second, item_type="installment", billed_amount=60_000, original_purchase_amount=180_000, installment_months=3, installment_round=2, fetched_at=FETCHED)
        body = self.get_bill(bill).json()
        self.assertEqual(body["issuerName"], "신한카드")
        self.assertIsNone(body["card"])
        self.assertEqual({row["cardId"] for row in body["cards"]}, {str(self.card.pk), str(self.second.pk)})
        self.assertEqual(body["totalAmount"], 420_000)
        self.assertEqual(body["paymentAccount"]["displayName"], "국민 급여통장 · ****5678")
        self.assertIsNone(body["withdrawalDueDate"])
        self.assertEqual(body["fetchedAt"], "2026-10-21T09:30:00+09:00")
        installment = next(row for row in body["items"] if row["itemType"] == "installment")
        self.assertEqual((installment["originalPurchaseAmount"], installment["billedAmount"], installment["installmentRound"]), (180_000, 60_000, 2))
        self.assertNotIn("sourceItemKey", installment)
        self.assertFalse(PaymentDue.objects.filter(user=self.user).exists())

    def test_single_card_bill_and_relogin_disconnected_history(self):
        bill = self.bill(scope_type="card", card=self.card, items_state="unavailable")
        for state in ("relogin", "disconnected"):
            self.conn.status = state
            self.conn.disconnected_at = timezone.now() if state == "disconnected" else None
            self.conn.save(update_fields=["status", "disconnected_at"])
            body = self.get_bill(bill).json()
            self.assertEqual(body["cards"], [body["card"]])
            self.assertEqual(body["card"]["status"], state)
            self.assertIsNone(body["items"])

    def test_details_complete_empty_vs_unavailable(self):
        bill = self.bill()
        for state, expected in ((None, None), ("unavailable", None), ("partial", None), ("complete", [])):
            bill.items_state = state
            bill.save(update_fields=["items_state"])
            body = self.get_bill(bill).json()
            self.assertEqual(body["itemsState"], state)
            self.assertEqual(body["items"], expected)
            self.assertIsNone(body["cards"])

    def test_partial_detail_does_not_claim_card_coverage_or_invent_fetch_time(self):
        bill = self.bill(items_state="partial", confirmation_state="estimated", scope_type="issuer")
        self.item(bill, card=self.card, billed_amount=0, fetched_at=FETCHED)
        self.item(bill, card=self.card, billed_amount=None)
        body = self.get_bill(bill).json()
        self.assertEqual(body["totalAmount"], 420_000)
        self.assertEqual(body["confirmationStatus"], "estimated")
        self.assertIsNone(body["fetchedAt"])
        self.assertIsNone(body["cards"])
        self.assertEqual({row["billedAmount"] for row in body["items"]}, {0, None})

    def test_known_cards_deduplicated_and_missing_card_means_unknown_coverage(self):
        bill = self.bill(scope_type="issuer", items_state="complete")
        self.item(bill, card=self.card)
        self.item(bill, card=self.card)
        self.assertEqual(len(self.get_bill(bill).json()["cards"]), 1)
        self.item(bill)
        self.assertIsNone(self.get_bill(bill).json()["cards"])

    def test_missing_or_foreign_bill_is_same_404(self):
        foreign_bill = self.bill(user=self.other, connection=self.other_conn)
        for bill_id in ("abc", str(uuid.uuid4()), str(foreign_bill.pk), str(self.card.pk)):
            response = self.client.get(f"/api/finance/card-bills/{bill_id}/")
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["error"]["code"], "not_found")
            self.assertNotIn("fields", response.json()["error"])

    def test_foreign_related_data_is_not_exposed(self):
        foreign_bank = self.connection(self.other, "0004", business="bank")
        account = BankAccount.objects.create(user=self.other, connection=foreign_bank, source_account_key="foreign")
        for fields in ({"connection": self.other_conn}, {"card": self.foreign}, {"payment_account": account}):
            with self.subTest(fields=list(fields)):
                self.assertEqual(self.get_bill(self.bill(**fields)).status_code, 404)
        bill = self.bill(items_state="complete")
        item = self.item(bill, user=self.other)
        self.assertEqual(self.get_bill(bill).status_code, 404)
        item.user, item.card = self.user, self.foreign
        item.save(update_fields=["user", "card"])
        self.assertEqual(self.get_bill(bill).status_code, 404)

    def test_connectionless_card_does_not_invent_connected_status(self):
        orphan = self.make_card(None)
        bill = self.bill(scope_type="card", card=orphan, items_state="complete")
        self.item(bill, card=orphan)
        body = self.get_bill(bill).json()
        self.assertIsNone(body["card"])
        self.assertIsNone(body["cards"])
        self.assertIsNone(body["items"][0]["card"])

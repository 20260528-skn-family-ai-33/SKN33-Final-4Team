"""이번 회차 할부금 테스트 (budgets/protect.py installment_rounds · cards.md C-17)."""
from datetime import date, datetime, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from finance.models import Card, CardBill, CardBillItem, Connection, SyncRun
from users.models import Consent

from .models import BudgetCycle
from .protect import CARD_BILL_SYNC, CARD_SPENDING_SYNC, card_reserved

TODAY = date(2026, 11, 3)
BEFORE, AFTER = date(2026, 9, 1), date(2026, 10, 21)  # 최초 연결일(10/20) 전 · 뒤


class InstallmentTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="installment@example.invalid")
        self.cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25))
        self.connection = Connection.objects.create(user=self.user, business_type="card", organization_code="0306", status="connected")
        Consent.objects.create(user=self.user, purpose="finance_inquiry", product_scope="card",
                               document_version="v1", document_hash="test")
        self.card = Card.objects.create(user=self.user, connection=self.connection, origin="provider", issuer_code="0306",
                                        first_connected_at=datetime(2026, 10, 19, 15, tzinfo=dt_timezone.utc), is_selected=True)
        for product in (CARD_BILL_SYNC, CARD_SPENDING_SYNC):
            SyncRun.objects.create(user=self.user, connection=self.connection, card=self.card, product_code=product,
                                   covered_from=date(2026, 10, 20), covered_to=TODAY, status="success")
        self.first = self.bill("first", date(2026, 10, 22), 100_000)   # 지난 주기에 낸 첫 청구서
        self.second = self.bill("second", date(2026, 11, 10), 300_000)
        clock = patch("django.utils.timezone.localdate", side_effect=self.localdate)
        clock.start()
        self.addCleanup(clock.stop)

    @staticmethod
    def localdate(value=None, timezone=None):
        from django.utils import timezone as django_timezone
        return TODAY if value is None else django_timezone.localtime(value, timezone).date()

    def bill(self, key, due, amount, bill_type=""):
        return CardBill.objects.create(
            user=self.user, connection=self.connection, issuer_code="0306", statement_key=key, scope_type="issuer",
            billing_month=due.replace(day=1), total_amount=amount, withdrawal_due_date=due, source_type="provider",
            field_states={"codefBillType": bill_type},
        )

    def item(self, bill=None, billed=60_000, bought=BEFORE, item_type="installment"):
        return CardBillItem.objects.create(user=self.user, bill=bill or self.second, item_type=item_type,
                                           billed_amount=billed, purchased_on=bought)

    def reserved(self):
        return card_reserved(self.user, self.cycle)

    def test_adds_rounds_of_purchases_before_connection(self):
        self.item(), self.item(billed=40_000)
        self.assertEqual(self.reserved(), {"amount": 100_000, "pending": []})

    def test_skips_what_budget_already_counted_or_other_items(self):
        self.item(bought=AFTER)                       # 연결 뒤 구매 = 긁을 때 소비로 셈
        self.item(item_type="one_time")               # 일시불
        self.item(bill=self.first)                    # 첫 청구서 안 = 총액에 이미 있음
        self.item(bill=self.bill("next", date(2026, 11, 25), 1))  # 다음 주기 청구서
        self.item(bill=self.bill("loan", date(2026, 11, 10), 1, bill_type="카드론"))
        self.assertEqual(self.reserved(), {"amount": 0, "pending": []})

    def test_unknown_purchase_date_is_reserved(self):
        self.item(bought=None)
        self.assertEqual(self.reserved()["amount"], 60_000)

    def test_unknown_round_amount_holds(self):
        self.item(billed=None)
        self.assertEqual(self.reserved(), {"amount": None, "pending": [
            {"card_id": str(self.card.pk), "reason": "installment_amount_unknown"},
        ]})

    def test_added_on_top_of_first_bill_in_this_cycle(self):
        CardBill.objects.filter(pk=self.first.pk).delete()
        self.item()                                   # 이 청구서가 첫 청구서가 됨 → 총액에 이미 있어 더하지 않음
        self.assertEqual(self.reserved()["amount"], 300_000)
        self.item(bill=self.bill("later", date(2026, 11, 20), 50_000))
        self.assertEqual(self.reserved()["amount"], 360_000)

"""이번 회차 할부금 테스트 (budgets/protect.py installment_rounds · cards.md C-17)."""
from datetime import date, datetime, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from finance.models import Card, CardBill, CardBillItem, Connection, SyncRun
from users.models import Consent

from .models import Budget, BudgetCycle
from .protect import CARD_BILL_SYNC, CARD_SPENDING_SYNC, card_reserved

TODAY = date(2026, 11, 3)
BEFORE, THIS_CYCLE = date(2026, 9, 1), date(2026, 10, 26)  # 최초 연결일(10/20) 전 · 이번 주기(10/25 시작)
BEFORE_FIRST_CYCLE = date(2026, 10, 21)                      # 연결 뒤, 첫 예산 주기 전
HOLD = {"amount": None, "reason": "installment_amount_unknown"}


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

    def bill(self, key, due, amount, bill_type="", items_state="complete"):
        return CardBill.objects.create(
            user=self.user, connection=self.connection, issuer_code="0306", statement_key=key, scope_type="issuer",
            billing_month=due.replace(day=1), total_amount=amount, withdrawal_due_date=due, source_type="provider",
            field_states={"codefBillType": bill_type}, items_state=items_state,
        )

    def item(self, bill=None, billed=60_000, bought=BEFORE, item_type="installment"):
        return CardBillItem.objects.create(user=self.user, bill=bill or self.second, item_type=item_type,
                                           billed_amount=billed, purchased_on=bought)

    def reserved(self):
        return card_reserved(self.user, self.cycle)

    def assert_held(self):
        result = self.reserved()
        self.assertEqual({"amount": result["amount"], "reason": result["pending"][0]["reason"]}, HOLD)

    def test_adds_rounds_of_purchases_before_connection(self):
        self.item(), self.item(billed=40_000)
        self.assertEqual(self.reserved(), {"amount": 100_000, "pending": []})

    def test_skips_what_budget_already_counted_or_other_items(self):
        self.item(bought=THIS_CYCLE)                  # 이번 주기 구매 = 긁을 때 소비로 셈
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
        self.assert_held()

    def test_purchase_before_first_budget_cycle_is_reserved(self):
        self.item(bought=BEFORE_FIRST_CYCLE)          # 10/20 연결 → 10/21 구매 → 10/25 첫 주기: 어느 예산에도 안 잡힘
        self.assertEqual(self.reserved()["amount"], 60_000)

    def approve(self, starts_on, ends_before):
        past = BudgetCycle.objects.create(user=self.user, starts_on=starts_on, ends_before=ends_before, status="closed")
        return Budget.objects.create(user=self.user, cycle=past, version_no=1, status="approved")

    def test_purchase_in_approved_budget_cycle_is_not_reserved_twice(self):
        self.approve(date(2026, 9, 25), date(2026, 10, 25))
        self.item(bought=BEFORE_FIRST_CYCLE)
        self.assertEqual(self.reserved()["amount"], 0)

    def test_draft_budget_cycle_did_not_count_the_purchase(self):
        Budget.objects.filter(pk=self.approve(date(2026, 9, 25), date(2026, 10, 25)).pk).update(status="draft")
        self.item(bought=BEFORE_FIRST_CYCLE)
        self.assertEqual(self.reserved()["amount"], 60_000)

    def test_purchase_before_connection_in_approved_cycle_is_reserved(self):
        self.approve(date(2026, 8, 25), date(2026, 9, 25))
        self.item(bought=BEFORE)                      # 그때 예산은 이 카드 구매를 몰랐다
        self.assertEqual(self.reserved()["amount"], 60_000)

    def test_missing_details_hold(self):
        for state in ("unavailable", "partial", None):
            with self.subTest(state=state):
                CardBill.objects.filter(pk=self.second.pk).update(items_state=state)
                self.assert_held()

    def test_partial_details_hold_even_with_a_known_round(self):
        CardBill.objects.filter(pk=self.second.pk).update(items_state="partial")
        self.item()
        self.assert_held()

    def test_complete_details_without_installments_are_zero(self):
        self.item(item_type="one_time")
        self.assertEqual(self.reserved(), {"amount": 0, "pending": []})

    def test_details_of_bills_outside_the_sum_do_not_hold(self):
        CardBill.objects.filter(pk=self.first.pk).update(items_state="unavailable")      # 첫 청구서는 총액으로 셈
        self.bill("next", date(2026, 11, 25), 1, items_state="unavailable")              # 다음 주기
        self.bill("loan", date(2026, 11, 10), 1, bill_type="카드론", items_state="unavailable")
        self.assertEqual(self.reserved(), {"amount": 0, "pending": []})

    def test_added_on_top_of_first_bill_in_this_cycle(self):
        CardBill.objects.filter(pk=self.first.pk).delete()
        self.item()                                   # 이 청구서가 첫 청구서가 됨 → 총액에 이미 있어 더하지 않음
        self.assertEqual(self.reserved()["amount"], 300_000)
        self.item(bill=self.bill("later", date(2026, 11, 20), 50_000))
        self.assertEqual(self.reserved()["amount"], 360_000)

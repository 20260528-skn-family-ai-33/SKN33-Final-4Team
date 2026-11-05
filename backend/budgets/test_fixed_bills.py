"""개별 확정 → 합계·청구서 참고 금액 재조회. 실제 예산 계산은 기존 경로 유지."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from finance.models import Card, CardBill
from users.models import Consent, FinancialProfile

from .test_fixed import FixedFixture, URL, send


class FixedBillsTest(FixedFixture, TestCase):
    def setUp(self):
        self.make_world()
        self.card.issuer_code = "0301"
        self.card.save(update_fields=["issuer_code"])
        self.consent = Consent.objects.create(user=self.user, purpose="finance_inquiry", product_scope="card",
                                              document_version="v1", document_hash="test")
        self.bill = self.make_bill()

    def make_bill(self, **overrides):
        fields = dict(user=self.user, connection=self.card.connection, issuer_code="0301",
                      billing_month=self.today.replace(day=1), withdrawal_due_date=self.today,
                      total_amount=100000, source_type="provider", scope_type="issuer", confirmation_state="confirmed")
        fields.update(overrides)
        return CardBill.objects.create(**fields)

    def preview(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["cardBills"][0]

    def test_confirm_refresh_and_repeat_do_not_double_count(self):
        expense = self.make_expense(status="suggested", amount=12000, payment_card=self.card)
        self.make_expense(amount=500000, payment_account=self.account)
        self.assertEqual(self.preview()["amountAfterFixed"], 100000)
        for _ in range(2):
            response = send(self.client, "post", f"{URL}confirm/", {"recurringIds": [str(expense.pk)]})
            self.assertEqual(response.status_code, 200)
            data = self.client.get(URL).json()
            self.assertEqual(data["summary"], {"confirmedMonthlyTotal": 512000, "suggestedCount": 0})
            bill = data["cardBills"][0]
            self.assertEqual((bill["totalAmount"], bill["confirmedFixedAmount"], bill["amountAfterFixed"]),
                             (100000, 12000, 88000))
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.total_amount, 100000)

    def test_one_issuer_bill_for_multiple_cards_and_excludes_other_user(self):
        second = Card.objects.create(user=self.user, connection=self.card.connection, issuer_code="0301",
                                     origin="provider", is_selected=True)
        self.make_expense(amount=12000, payment_card=self.card)
        self.make_expense(amount=8000, payment_card=second)
        other = get_user_model().objects.create_user(email="other-bill@example.invalid")
        self.make_bill(user=other, connection=None, total_amount=999999)
        data = self.client.get(URL).json()
        self.assertEqual(len(data["cardBills"]), 1)
        self.assertEqual(data["cardBills"][0]["amountAfterFixed"], 80000)

    def test_ignored_and_stopped_do_not_reduce_bill(self):
        self.make_expense(status="stopped", amount=10000, payment_card=self.card)
        expense = self.make_expense(status="suggested", amount=12000, payment_card=self.card)
        self.assertEqual(send(self.client, "post", f"{URL}ignore/", {"recurringIds": [str(expense.pk)]}).status_code, 200)
        self.assertEqual(self.preview()["confirmedFixedAmount"], 0)

    def test_missing_bill_is_unknown_but_zero_bill_is_zero(self):
        self.bill.total_amount = 0
        self.bill.save()
        self.assertEqual(self.preview()["amountAfterFixed"], 0)
        self.bill.delete()
        self.assertIsNone(self.preview()["amountAfterFixed"])
        self.assertEqual(self.preview()["reason"], "bill_unavailable")

    def test_minimum_zero_and_estimated_label(self):
        self.make_expense(amount=120000, payment_card=self.card)
        self.bill.confirmation_state = "estimated"
        self.bill.save()
        preview = self.preview()
        self.assertEqual(preview["amountAfterFixed"], 0)
        self.assertTrue(preview["isEstimated"])

    def test_cycle_boundaries_and_payment_due_fallback(self):
        self.bill.withdrawal_due_date = None
        self.bill.payment_due_date = self.cycle.starts_on
        self.bill.save()
        self.make_bill(withdrawal_due_date=self.cycle.ends_before, total_amount=900000)
        self.make_bill(withdrawal_due_date=self.cycle.starts_on - timedelta(days=1), total_amount=900000)
        self.assertEqual(self.preview()["totalAmount"], 100000)

    def test_loan_statement_excluded_and_ambiguous_statements_block(self):
        self.bill.field_states = {"codefBillType": "카드 이용"}
        self.bill.save()
        self.make_bill(field_states={"codefBillType": "장기카드대출"}, total_amount=500000)
        self.assertEqual(self.preview()["totalAmount"], 100000)
        self.make_bill()  # 같은 날 범위 불명 명세서: 임의 합산 금지
        self.assertEqual(self.preview()["reason"], "first_bill_ambiguous")
        self.assertIsNone(self.preview()["amountAfterFixed"])

    def test_invalid_scope_date_amount_are_not_shown_as_zero(self):
        for field, value, reason in [("scope_type", "card", "bill_scope_unknown"),
                                     ("withdrawal_due_date", None, "bill_due_date_unknown"),
                                     ("total_amount", -1, "bill_amount_invalid")]:
            with self.subTest(field=field):
                original = getattr(self.bill, field)
                setattr(self.bill, field, value)
                self.bill.save()
                preview = self.preview()
                self.assertEqual(preview["reason"], reason)
                self.assertIsNone(preview["amountAfterFixed"])
                setattr(self.bill, field, original)
                self.bill.save()

    def test_no_cycle(self):
        self.cycle.delete()
        self.assertEqual(self.preview()["reason"], "cycle_unavailable")

    def test_onboarding_payday_without_saved_cycle_can_preview_bill(self):
        self.cycle.delete()
        FinancialProfile.objects.create(user=self.user, payday=self.today.day)
        self.make_expense(amount=33309, payment_card=self.card)
        self.bill.total_amount = 0
        self.bill.save()
        preview = self.preview()
        self.assertIsNone(preview["reason"])
        self.assertEqual((preview["totalAmount"], preview["confirmedFixedAmount"], preview["amountAfterFixed"]),
                         (0, 33309, 0))
        self.assertFalse(self.user.budget_cycles.exists())

    def test_no_consent_no_selection_or_disconnected_hides_bills(self):
        self.card.is_selected = False
        self.card.save()
        self.assertEqual(self.client.get(URL).json()["cardBills"], [])
        self.card.is_selected = True
        self.card.save()
        self.consent.revoked_at = timezone.now()
        self.consent.save()
        self.assertEqual(self.client.get(URL).json()["cardBills"], [])
        self.consent.revoked_at = None
        self.consent.save()
        self.card.connection.status = "disconnected"
        self.card.connection.disconnected_at = timezone.now()
        self.card.connection.save()
        self.assertEqual(self.client.get(URL).json()["cardBills"], [])

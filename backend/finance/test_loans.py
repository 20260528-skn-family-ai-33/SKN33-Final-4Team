"""대출 직접 등록·기존 보완 회귀 검사.

실행: python backend/manage.py test finance.test_loans
Django가 별도 테스트 DB를 사용하며, 각 테스트의 데이터는 롤백한다.
"""
import json
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from budgets.models import BudgetCycle
from users.models import IdempotencyRecord

from .models import Connection, Loan, PaymentDue


URL = "/api/finance/loans/"
BASE = {"lenderName": "한국장학재단", "productName": "학자금대출", "manualBalance": 5_000_000}
FULL = {
    **BASE, "manualRate": 1.7, "maturityDate": "2030-02-28",
    "monthlyPayment": 100_000, "paymentDay": 28, "repaymentMethod": "equal_principal",
}


@override_settings(ALLOWED_HOSTS=["testserver"], DEBUG=True)
class LoanCreateTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email=f"loan-test-{uuid.uuid4().hex}@example.invalid", nickname="대출검증",
        )
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.client.get("/api/csrf/")
        self.token = self.client.cookies["csrftoken"].value
        self.cycle = BudgetCycle.objects.create(
            user=self.user, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25),
        )
        self.clock = patch("django.utils.timezone.localdate", return_value=date(2026, 10, 27))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.external = patch("finance.codef._request", side_effect=AssertionError("CODEF 호출 금지"))
        self.external.start()
        self.addCleanup(self.external.stop)

    def post(self, body, key=None):
        return self.client.post(
            URL, data=json.dumps(body), content_type="application/json",
            HTTP_X_CSRFTOKEN=self.token, HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()),
        )

    def patch_loan(self, loan, body):
        return self.client.patch(
            f"{URL}{loan.pk}/", data=json.dumps(body), content_type="application/json",
            HTTP_X_CSRFTOKEN=self.token,
        )

    def counts(self):
        return (
            Loan.objects.filter(user=self.user).count(),
            PaymentDue.objects.filter(user=self.user).count(),
            IdempotencyRecord.objects.filter(user=self.user).count(),
        )

    def assert_invalid(self, body, field):
        before = self.counts()
        response = self.post(body)
        self.assertEqual(response.status_code, 400, response.content)
        error = response.json()["error"]
        self.assertEqual(error["code"], "invalid")
        self.assertIn(field, error["fields"])
        self.assertEqual(self.counts(), before)

    def test_minimum_body_and_unknown_fields_in_response(self):
        response = self.post(BASE)
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        loan = Loan.objects.get(pk=body["loanId"])
        self.assertEqual(loan.user_id, self.user.pk)
        self.assertEqual((body["origin"], body["status"]), ("manual", "repaying"))
        self.assertNotIn("items", body)
        self.assertEqual(body["manualBalance"], 5_000_000)
        for field in (
            "reportedPrincipal", "reportedBalance", "reportedRate", "reportedPaymentDate",
            "manualRate", "maturityDate", "monthlyPayment", "paymentDay", "repaymentMethod",
            "paymentHistory", "estimatedMonthlyPayment", "overdue", "fetchedAt",
        ):
            self.assertIsNone(body[field], field)
        self.assertEqual(body["fieldStates"]["manualBalance"]["source"], "manual")
        self.assertEqual(body["fieldStates"]["manualBalance"]["confirmationStatus"], "confirmed")
        self.assertTrue(body["fieldStates"]["manualBalance"]["confirmedAt"].endswith("+09:00"))
        for field in ("manualRate", "monthlyPayment", "paymentDay", "repaymentMethod"):
            self.assertEqual(body["fieldStates"][field], {
                "source": None, "confirmationStatus": "unknown", "confirmedAt": None,
            })
        self.assertIsNone(loan.connection_id)
        self.assertIsNone(loan.linked_account_id)
        self.assertFalse(Connection.objects.filter(user=self.user).exists())
        self.assertEqual(self.counts(), (1, 0, 1))

    def test_full_body_creates_due_and_confirms_values(self):
        response = self.post(FULL)
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["manualRate"], 1.7)
        self.assertEqual(body["maturityDate"], "2030-02-28")
        loan = Loan.objects.get(pk=body["loanId"])
        loan.refresh_from_db()
        self.assertEqual(loan.manual_rate, Decimal("1.70000"))
        for state in body["fieldStates"].values():
            self.assertEqual((state["source"], state["confirmationStatus"]), ("manual", "confirmed"))
            self.assertIsNotNone(state["confirmedAt"])
        due = PaymentDue.objects.get(loan=loan)
        self.assertEqual(due.user_id, self.user.pk)
        self.assertEqual(due.due_date, date(2026, 10, 28))
        self.assertEqual(due.confirmed_amount, 100_000)
        self.assertEqual(due.occurrence_key, f"loan:{loan.pk}:2026-10-25")
        self.assertEqual((due.source_type, due.confirmation_state), ("manual", "confirmed"))
        self.assertEqual(due.payment_state, "unpaid")
        self.cycle.refresh_from_db()
        self.assertIsNone(self.cycle.confirmed_income)
        self.assertEqual(self.cycle.revision_no, 0)

    def test_optional_nulls_and_incomplete_schedule(self):
        for extras in (
            {field: None for field in FULL if field not in BASE},
            {"monthlyPayment": 100_000}, {"paymentDay": 28},
            {"monthlyPayment": None, "paymentDay": 28},
            {"monthlyPayment": 100_000, "paymentDay": None},
        ):
            with self.subTest(extras=extras):
                response = self.post({**BASE, **extras})
                self.assertEqual(response.status_code, 201, response.content)
                self.assertFalse(PaymentDue.objects.filter(loan_id=response.json()["loanId"]).exists())

    def test_boundaries_and_zero_rate(self):
        for values in (
            {"manualBalance": 1, "manualRate": 0, "monthlyPayment": 1, "paymentDay": 1},
            {"manualBalance": 1_000_000_000, "manualRate": 30, "monthlyPayment": 100_000_000, "paymentDay": 31},
            {"manualRate": 1.23456, "maturityDate": "2028-02-29", "lenderName": "가" * 50, "productName": "나" * 50},
        ):
            with self.subTest(values=values):
                response = self.post({**BASE, **values})
                self.assertEqual(response.status_code, 201, response.content)
                self.assertEqual(response.json()["manualRate"], values["manualRate"])

    def test_required_fields_and_name_types(self):
        for field in BASE:
            with self.subTest(missing=field):
                self.assert_invalid({key: value for key, value in BASE.items() if key != field}, field)
            with self.subTest(null=field):
                self.assert_invalid({**BASE, field: None}, field)
        for field in ("lenderName", "productName"):
            for value in ("", "   ", "가" * 51, 123, True, [], {}):
                with self.subTest(field=field, value=value):
                    self.assert_invalid({**BASE, field: value}, field)

    def test_integer_types_and_ranges(self):
        for field, maximum in (("manualBalance", 1_000_000_000), ("monthlyPayment", 100_000_000), ("paymentDay", 31)):
            for value in (0, -1, maximum + 1, "1", "", True, False, 1.0, 1.5, [], {}):
                with self.subTest(field=field, value=value):
                    self.assert_invalid({**BASE, field: value}, field)

    def test_rate_types_ranges_and_storage_precision(self):
        for value in (-0.1, 30.00001, "1.7", "", True, False, [], {}, 1.234567):
            with self.subTest(value=value):
                self.assert_invalid({**BASE, "manualRate": value}, "manualRate")

    def test_date_format_and_calendar(self):
        for value in ("", "2026-02-29", "2030-02-30", "20300228", "2030-2-28", "2030-02-28T00:00:00", 20300228, True, [], {}):
            with self.subTest(value=value):
                self.assert_invalid({**BASE, "maturityDate": value}, "maturityDate")

    def test_repayment_methods(self):
        for method in ("equal_payment", "equal_principal", "bullet", "unknown", None):
            with self.subTest(method=method):
                response = self.post({**BASE, "repaymentMethod": method})
                self.assertEqual(response.status_code, 201, response.content)
                self.assertEqual(response.json()["repaymentMethod"], method)
        for method in ("", "invalid", 1, True, [], {}):
            with self.subTest(method=method):
                self.assert_invalid({**BASE, "repaymentMethod": method}, "repaymentMethod")

    def test_server_fields_are_rejected(self):
        for field in ("userId", "loanId", "connectionId", "origin", "status", "reportedBalance", "fieldStates", "unexpected"):
            with self.subTest(field=field):
                self.assert_invalid({**BASE, field: "forbidden"}, field)

    def test_non_object_and_malformed_json(self):
        for value in ([], "text", 1, None):
            with self.subTest(value=value):
                self.assert_invalid(value, "nonFieldErrors")
        response = self.client.post(URL, data="{", content_type="application/json", HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "parse_error")
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_idempotent_replay_and_conflict(self):
        key = str(uuid.uuid4())
        first = self.post(FULL, key)
        self.assertEqual(first.status_code, 201, first.content)
        loan = Loan.objects.get(pk=first.json()["loanId"])
        self.assertEqual(self.patch_loan(loan, {"monthlyPayment": 200_000}).status_code, 200)
        repeat = self.post(FULL, key)
        self.assertEqual(repeat.status_code, 201)
        self.assertEqual(repeat.json(), first.json())  # 현재 값 대신 처음 성공 응답 재사용
        conflict = self.post({**FULL, "manualBalance": 1}, key)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "idempotency_conflict")
        self.assertNotIn("fields", conflict.json()["error"])
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_new_key_can_create_same_named_loan(self):
        first = self.post(FULL)
        second = self.post(FULL)
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertNotEqual(first.json()["loanId"], second.json()["loanId"])
        self.assertEqual(self.counts(), (2, 2, 2))

    def test_key_is_scoped_to_user(self):
        key = str(uuid.uuid4())
        first = self.post(BASE, key)
        other = get_user_model().objects.create_user(email=f"loan-other-{uuid.uuid4().hex}@example.invalid")
        self.client.force_login(other)
        second = self.post(BASE, key)
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertNotEqual(first.json()["loanId"], second.json()["loanId"])
        self.assertEqual(Loan.objects.get(pk=second.json()["loanId"]).user_id, other.pk)

    def test_invalid_body_can_retry_same_key(self):
        key = str(uuid.uuid4())
        self.assertEqual(self.post({**FULL, "paymentDay": 32}, key).status_code, 400)
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual(self.post(FULL, key).status_code, 201)
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_failure_after_due_creation_rolls_back_and_allows_retry(self):
        key = str(uuid.uuid4())
        with patch("finance.api_loans.loan_item", side_effect=RuntimeError("test rollback")):
            with self.assertRaises(RuntimeError):
                self.post(FULL, key)
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual(self.post(FULL, key).status_code, 201)
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_key_format_and_optional_header_follow_common_rule(self):
        self.assertEqual(self.post(BASE, "bad").status_code, 400)
        self.assertEqual(self.counts(), (0, 0, 0))
        response = self.client.post(URL, data=json.dumps(BASE), content_type="application/json", HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.counts(), (1, 0, 0))

    def test_session_and_csrf_required_even_for_replay(self):
        response = Client().post(URL, data=json.dumps(BASE), content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "not_authenticated")
        key = str(uuid.uuid4())
        self.assertEqual(self.post(BASE, key).status_code, 201)
        response = self.client.post(URL, data=json.dumps(BASE), content_type="application/json", HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "permission_denied")
        self.assertEqual(self.counts(), (1, 0, 1))

    def test_no_current_cycle_saves_settings_without_inventing_due(self):
        self.cycle.status = "closed"
        self.cycle.save(update_fields=["status"])
        response = self.post(FULL)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["monthlyPayment"], 100_000)
        self.assertEqual(self.counts(), (1, 0, 1))

    def test_end_of_month_and_past_due(self):
        self.cycle.starts_on = date(2027, 2, 1)
        self.cycle.ends_before = date(2027, 3, 1)
        self.cycle.save(update_fields=["starts_on", "ends_before"])
        with patch("django.utils.timezone.localdate", return_value=date(2027, 2, 10)):
            response = self.post({**FULL, "paymentDay": 31})
            self.assertEqual(response.status_code, 201)
            self.assertEqual(PaymentDue.objects.get(loan_id=response.json()["loanId"]).due_date, date(2027, 2, 28))
            response = self.post({**FULL, "paymentDay": 1})
            self.assertEqual(response.status_code, 201)
            self.assertEqual(PaymentDue.objects.get(loan_id=response.json()["loanId"]).payment_state, "needs_review")

    def test_get_still_requires_consent_and_post_does_not(self):
        self.assertEqual(self.post(BASE).status_code, 201)
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "consent_required")
        self.assertEqual(self.counts(), (1, 0, 1))

    def test_manual_loan_patch_cancels_reactivates_and_protects_paid_due(self):
        response = self.post(FULL)
        self.assertEqual(response.status_code, 201)
        loan = Loan.objects.get(pk=response.json()["loanId"])
        due = PaymentDue.objects.get(loan=loan)
        self.assertEqual(self.patch_loan(loan, {"monthlyPayment": 200_000}).status_code, 200)
        due.refresh_from_db()
        self.assertEqual(due.confirmed_amount, 200_000)
        self.assertEqual(self.patch_loan(loan, {"paymentDay": None}).status_code, 200)
        due.refresh_from_db()
        self.assertEqual(due.obligation_state, "canceled")
        self.assertIsNotNone(due.canceled_at)
        self.assertEqual(self.patch_loan(loan, {"paymentDay": 29}).status_code, 200)
        due.refresh_from_db()
        self.assertEqual((due.obligation_state, due.due_date), ("active", date(2026, 10, 29)))
        self.assertIsNone(due.canceled_at)
        for state, amount in (("partial", 100_000), ("paid", 200_000)):
            due.payment_state, due.paid_amount = state, amount
            due.save(update_fields=["payment_state", "paid_amount"])
            self.assertEqual(self.patch_loan(loan, {"monthlyPayment": 400_000, "paymentDay": None}).status_code, 200)
            due.refresh_from_db()
            self.assertEqual((due.confirmed_amount, due.payment_state, due.paid_amount), (200_000, state, amount))
            self.assertEqual(due.obligation_state, "active")
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_manual_loan_balance_remains_outside_patch_contract(self):
        response = self.post(BASE)
        loan = Loan.objects.get(pk=response.json()["loanId"])
        response = self.patch_loan(loan, {"manualBalance": 123})
        self.assertEqual(response.status_code, 400)
        loan.refresh_from_db()
        self.assertEqual(loan.manual_balance, BASE["manualBalance"])

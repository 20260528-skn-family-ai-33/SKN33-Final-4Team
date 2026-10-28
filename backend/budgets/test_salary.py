from datetime import date, datetime, timezone as dt_timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections, transaction
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from users.models import FinancialProfile
from finance.models import BankAccount, Connection, FinancialTransaction, Loan, PaymentDue, PaymentMatch, RecurringExpense
from .api import previous_start
from .models import Budget, BudgetCycle


URL = "/api/budgets/salary/"


def payload(**changes):
    return {"takeHomeIncome": 2_800_000, "payday": 25, **changes}


class SalaryTests(TestCase):
    def setUp(self):
        clock = patch("django.utils.timezone.localdate", return_value=date(2026, 10, 28))
        self.today = clock.start()
        self.addCleanup(clock.stop)
        self.user = get_user_model().objects.create_user(email="salary@example.com")
        self.other = get_user_model().objects.create_user(email="other-salary@example.com")
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.client.get("/api/csrf/")
        self.client.credentials(HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value)

    def put(self, data):
        return self.client.put(URL, data, format="json")

    def assert_invalid(self, data, field):
        before = self.client.get(URL).json()
        response = self.put(data)
        self.assertEqual(response.status_code, 400, response.content)
        error = response.json()["error"]
        self.assertEqual(error["code"], "invalid")
        self.assertIn(field, error["fields"])
        self.assertEqual(self.client.get(URL).json(), before)

    def test_empty_get_does_not_create_profile(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["takeHomeIncome"])
        self.assertIsNone(response.json()["payday"])
        self.assertFalse(FinancialProfile.objects.filter(user=self.user).exists())

    def test_first_save_without_profile_or_finance_consent(self):
        response = self.put(payload())
        self.assertEqual(response.status_code, 200, response.content)
        saved = FinancialProfile.objects.get(user=self.user)
        self.assertEqual(saved.monthly_take_home_income, 2_800_000)
        self.assertEqual(saved.payday, 25)
        self.assertEqual(self.client.get(URL).json(), response.json())

    def test_anonymous_get_and_put(self):
        client = APIClient()
        for method in ("get", "put"):
            response = getattr(client, method)(URL, payload() if method == "put" else None, format="json")
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["error"]["code"], "not_authenticated")

    def test_csrf_required(self):
        for token in (None, "x" * 32):
            self.client.credentials(**({"HTTP_X_CSRFTOKEN": token} if token else {}))
            response = self.put(payload())
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["error"]["code"], "permission_denied")
        self.assertFalse(FinancialProfile.objects.filter(user=self.user).exists())

    def test_unsupported_methods(self):
        for method in ("post", "patch", "delete"):
            self.assertEqual(getattr(self.client, method)(URL, payload(), format="json").status_code, 405)

    def test_required_values(self):
        for field in ("takeHomeIncome", "payday"):
            data = payload()
            del data[field]
            self.assert_invalid(data, field)
            self.assert_invalid(payload(**{field: None}), field)

    def test_income_bounds_and_types(self):
        for value in (0, -1, 10_000_000_000_000, 1.0, "2800000", "2,800,000", True, False, [], {}):
            with self.subTest(value=value):
                self.assert_invalid(payload(takeHomeIncome=value), "takeHomeIncome")
        for value in (1, 9_999_999_999_999):
            response = self.put(payload(takeHomeIncome=value))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["takeHomeIncome"], value)

    def test_payday_bounds_and_types(self):
        for value in (0, -1, 32, 25.0, "25", True, False, [], {}):
            with self.subTest(value=value):
                self.assert_invalid(payload(payday=value), "payday")

    def test_unknown_fields_and_owner_injection(self):
        for field, value in (("userId", str(self.other.pk)), ("frequency", "weekly"), ("housingType", "alone"),
                             ("monthlyDisposableIncome", 1), ("paydayRule", "business_day")):
            self.assert_invalid(payload(**{field: value}), field)

    def test_invalid_json_and_root(self):
        response = self.client.put(URL, '{"payday":', content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "parse_error")
        for value in ([], "text", 1, True):
            self.assert_invalid(value, "nonFieldErrors")

    def test_preserves_other_profile_values(self):
        FinancialProfile.objects.create(
            user=self.user, birth_date=date(1999, 3, 15), gender="female", has_car=True, housing_type="alone",
            monthly_disposable_income=123, disposable_income_basis="legacy", payday_rule="legacy",
            living_budget=1_000_000, savings_floor=200_000, cushion_monthly=100_000, cushion_target=8_000_000,
        )
        before = FinancialProfile.objects.filter(user=self.user).values().get()
        self.assertEqual(self.put(payload()).status_code, 200)
        after = FinancialProfile.objects.filter(user=self.user).values().get()
        for field in ("monthly_take_home_income", "payday", "updated_at"):
            before.pop(field)
            after.pop(field)
        self.assertEqual(after, before)

    def test_only_own_salary_is_read_and_changed(self):
        FinancialProfile.objects.create(user=self.other, monthly_take_home_income=5_000_000, payday=10)
        self.assertIsNone(self.client.get(URL).json()["payday"])
        self.assertEqual(self.put(payload()).status_code, 200)
        saved = FinancialProfile.objects.get(user=self.other)
        self.assertEqual((saved.monthly_take_home_income, saved.payday), (5_000_000, 10))

    def home_period(self):
        response = self.client.get("/api/home/")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["period"]

    def test_home_missing_salary_then_uses_saved_values(self):
        self.assertIsNone(self.home_period())
        self.put(payload())
        period = self.home_period()
        self.assertEqual((period["startDate"], period["endDate"]), ("2026-10-25", "2026-11-24"))
        self.assertEqual(period["salaryState"], "waiting")

    def test_change_requires_explicit_choice_and_saves_nothing(self):
        self.put(payload())
        self.assert_invalid(payload(takeHomeIncome=3_000_000, payday=10), "paydayApplyFrom")
        saved = FinancialProfile.objects.get(user=self.user)
        self.assertEqual((saved.monthly_take_home_income, saved.payday), (2_800_000, 25))

    def test_first_registration_cannot_be_delayed(self):
        self.assert_invalid(payload(paydayApplyFrom="next_cycle"), "paydayApplyFrom")
        self.assertFalse(FinancialProfile.objects.filter(user=self.user).exists())

    def test_choice_values_are_validated(self):
        for value in (None, "next", 1, True, {}, []):
            self.assert_invalid(payload(paydayApplyFrom=value), "paydayApplyFrom")

    def test_current_cycle_choice_applies_immediately(self):
        self.put(payload())
        self.home_period()
        response = self.put(payload(payday=10, paydayApplyFrom="current_cycle"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["payday"], 10)
        self.assertIsNone(response.json()["pendingPaydayChange"])
        period = self.home_period()
        self.assertEqual((period["startDate"], period["endDate"]), ("2026-10-10", "2026-11-09"))
        self.assertEqual(BudgetCycle.objects.filter(user=self.user, status="active").count(), 1)

    def test_next_cycle_choice_preserves_start_and_moves_end(self):
        self.put(payload())
        self.home_period()
        response = self.put(payload(payday=10, paydayApplyFrom="next_cycle"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "takeHomeIncome": 2_800_000, "payday": 25,
            "pendingPaydayChange": {"payday": 10, "effectiveOn": "2026-11-10"},
        })
        cycle = BudgetCycle.objects.get(user=self.user, status="active")
        self.assertEqual(cycle.ends_before, date(2026, 11, 10))
        period = self.home_period()
        self.assertEqual((period["startDate"], period["endDate"]), ("2026-10-25", "2026-11-09"))

    def test_transition_keeps_correct_previous_cycle(self):
        self.put(payload())
        self.home_period()
        self.put(payload(payday=10, paydayApplyFrom="next_cycle"))
        self.today.return_value = date(2026, 11, 10)
        period = self.home_period()
        self.assertEqual((period["startDate"], period["endDate"]), ("2026-11-10", "2026-12-09"))
        active = BudgetCycle.objects.get(user=self.user, status="active")
        self.assertEqual(previous_start(active), date(2026, 10, 25))
        old = BudgetCycle.objects.get(user=self.user, starts_on=date(2026, 10, 25))
        self.assertEqual((old.ends_before, old.status), (date(2026, 11, 10), "closed"))
        self.assertFalse(BudgetCycle.objects.filter(user=self.user, starts_on=date(2026, 10, 10)).exists())
        self.assertIsNone(self.client.get(URL).json()["pendingPaydayChange"])
        self.today.return_value = date(2026, 12, 10)
        period = self.home_period()
        self.assertEqual((period["startDate"], period["endDate"]), ("2026-12-10", "2027-01-09"))

    def test_transition_can_extend_current_cycle(self):
        self.put(payload(payday=10))
        self.home_period()
        self.put(payload(payday=25, paydayApplyFrom="next_cycle"))
        self.today.return_value = date(2026, 11, 15)
        period = self.home_period()
        self.assertEqual((period["startDate"], period["endDate"]), ("2026-10-10", "2026-11-24"))
        self.today.return_value = date(2026, 11, 25)
        self.assertEqual(self.home_period()["startDate"], "2026-11-25")

    def test_new_payday_today_means_next_month_for_next_cycle(self):
        self.put(payload())
        response = self.put(payload(payday=28, paydayApplyFrom="next_cycle"))
        self.assertEqual(response.json()["pendingPaydayChange"]["effectiveOn"], "2026-11-28")

    def test_month_end_leap_year_and_year_rollover(self):
        for today, expected in ((date(2027, 2, 10), "2027-02-28"),
                                (date(2028, 2, 10), "2028-02-29"),
                                (date(2026, 12, 31), "2027-01-31")):
            with self.subTest(today=today):
                self.today.return_value = today
                FinancialProfile.objects.filter(user=self.user).delete()
                self.put(payload())
                response = self.put(payload(payday=31, paydayApplyFrom="next_cycle"))
                self.assertEqual(response.json()["pendingPaydayChange"]["effectiveOn"], expected)
                self.today.return_value = date.fromisoformat(expected)
                self.assertEqual(self.client.get(URL).json()["payday"], 31)
        self.assertEqual(self.home_period()["startDate"], "2027-01-31")

    def test_repeat_schedule_does_not_move_effective_date(self):
        self.put(payload())
        change = payload(payday=10, paydayApplyFrom="next_cycle")
        first = self.put(change).json()
        self.today.return_value = date(2026, 11, 9)
        self.assertEqual(self.put(change).json(), first)
        self.today.return_value = date(2026, 11, 10)
        response = self.put(change)
        self.assertEqual(response.json()["payday"], 10)
        self.assertIsNone(response.json()["pendingPaydayChange"])

    def test_income_change_keeps_schedule_and_uses_full_take_home_income(self):
        self.put(payload())
        self.put(payload(payday=10, paydayApplyFrom="next_cycle"))
        response = self.put(payload(takeHomeIncome=3_000_000))
        self.assertEqual(response.json()["takeHomeIncome"], 3_000_000)
        self.assertEqual(response.json()["pendingPaydayChange"]["payday"], 10)
        saved = FinancialProfile.objects.get(user=self.user)
        self.assertEqual(saved.monthly_take_home_income, 3_000_000)
        self.assertIsNone(saved.monthly_disposable_income)

    def test_schedule_can_be_replaced_or_explicitly_canceled(self):
        self.put(payload())
        self.put(payload(payday=10, paydayApplyFrom="next_cycle"))
        response = self.put(payload(payday=5, paydayApplyFrom="next_cycle"))
        self.assertEqual(response.json()["pendingPaydayChange"]["effectiveOn"], "2026-11-05")
        response = self.put(payload(paydayApplyFrom="current_cycle"))
        self.assertIsNone(response.json()["pendingPaydayChange"])
        self.assertEqual(self.home_period()["endDate"], "2026-11-24")

    def make_budget(self, user=None, status="draft", version=1):
        user = user or self.user
        cycle, _ = BudgetCycle.objects.get_or_create(user=user, starts_on=date(2026, 10, 25),
                                                    defaults={"ends_before": date(2026, 11, 25)})
        return Budget.objects.create(user=user, cycle=cycle, status=status, version_no=version)

    def test_changed_salary_expires_only_own_unapproved_budgets(self):
        self.put(payload())
        draft = self.make_budget()
        validated = self.make_budget(status="validated", version=2)
        approved = self.make_budget(status="approved", version=3)
        other = self.make_budget(user=self.other)
        self.assertEqual(self.put(payload(takeHomeIncome=3_000_000)).status_code, 200)
        for row, expected in ((draft, "expired"), (validated, "expired"), (approved, "approved"), (other, "draft")):
            row.refresh_from_db()
            self.assertEqual(row.status, expected)

    def test_identical_put_preserves_timestamp_and_new_draft(self):
        self.put(payload())
        before = FinancialProfile.objects.get(user=self.user).updated_at
        draft = self.make_budget()
        self.assertEqual(self.put(payload()).status_code, 200)
        self.assertEqual(FinancialProfile.objects.get(user=self.user).updated_at, before)
        draft.refresh_from_db()
        self.assertEqual(draft.status, "draft")


    def test_activation_expires_drafts_only_once(self):
        self.put(payload())
        self.put(payload(payday=10, paydayApplyFrom="next_cycle"))
        first = self.make_budget()
        self.today.return_value = date(2026, 11, 10)
        self.client.get(URL)
        first.refresh_from_db()
        self.assertEqual(first.status, "expired")
        second = self.make_budget(version=2)
        self.client.get(URL)
        second.refresh_from_db()
        self.assertEqual(second.status, "draft")

    def test_write_failure_rolls_back_profile_and_draft_changes(self):
        self.put(payload())
        draft = self.make_budget()
        before = self.client.get(URL).json()
        with patch("budgets.home.ensure_cycle", side_effect=RuntimeError("test rollback")):
            with self.settings(DEBUG=True):
                with self.assertRaises(RuntimeError):
                    self.put(payload(payday=10, paydayApplyFrom="next_cycle"))
        self.assertEqual(self.client.get(URL).json(), before)
        draft.refresh_from_db()
        self.assertEqual(draft.status, "draft")


    def make_expense(self):
        return RecurringExpense.objects.create(
            user=self.user, name="월세", amount=100_000, payment_day=20, status="confirmed",
            source_type="manual", category_id="HOUSING_TELECOM",
            confirmed_at=datetime(2026, 9, 1, tzinfo=dt_timezone.utc),
        )

    def make_loan(self):
        return Loan.objects.create(user=self.user, origin="manual", status="repaying",
                                   monthly_payment=100_000, payment_day=20, lender_name="테스트")

    def mark_partial(self, due):
        bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        account = BankAccount.objects.create(user=self.user, connection=bank, source_account_key=str(due.pk), account_type="demand")
        tx = FinancialTransaction.objects.create(
            user=self.user, account=account, source_kind="bank", occurred_on=due.due_date,
            amount=50_000, direction="out", transaction_type="transfer", confirmed_category_id="UNCLASSIFIED",
            confirmation_state="needs_review",
        )
        match = PaymentMatch.objects.create(user=self.user, due=due, transaction=tx, matched_amount=50_000)
        due.paid_amount, due.payment_state = 50_000, "partial"
        due.save(update_fields=["paid_amount", "payment_state"])
        return match

    def test_recurring_due_is_reused_when_cycle_start_changes(self):
        self.put(payload())
        expense = self.make_expense()
        self.home_period()
        before = PaymentDue.objects.get(recurring=expense, due_date=date(2026, 11, 20))
        match = self.mark_partial(before)
        snapshot = PaymentDue.objects.filter(pk=before.pk).values().get()
        self.assertEqual(self.put(payload(payday=10, paydayApplyFrom="next_cycle")).status_code, 200)
        self.today.return_value = date(2026, 11, 10)
        self.home_period()
        self.home_period()
        self.assertEqual(PaymentDue.objects.filter(pk=before.pk).values().get(), snapshot)
        self.assertEqual(PaymentDue.objects.filter(recurring=expense, due_date=date(2026, 11, 20)).count(), 1)
        self.assertTrue(PaymentMatch.objects.filter(pk=match.pk, due=before).exists())

    def test_long_cycle_has_each_months_recurring_due_once(self):
        self.put(payload(payday=10))
        expense = self.make_expense()
        self.home_period()
        self.assertEqual(self.put(payload(payday=25, paydayApplyFrom="next_cycle")).status_code, 200)
        self.home_period()
        self.home_period()
        rows = PaymentDue.objects.filter(recurring=expense, due_date__gte=date(2026, 10, 10), due_date__lt=date(2026, 11, 25))
        self.assertEqual(list(rows.order_by("due_date").values_list("due_date", flat=True)), [date(2026, 10, 20), date(2026, 11, 20)])
        response = self.client.patch(f"/api/budgets/fixed/{expense.pk}/", {"amount": 200_000}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(list(rows.order_by("due_date").values_list("due_date", "confirmed_amount")),
                         [(date(2026, 10, 20), 200_000), (date(2026, 11, 20), 200_000)])

    def test_loan_due_is_reused_after_shortened_cycle(self):
        from finance.api_loans import sync_loan_due
        self.put(payload())
        self.home_period()
        loan = self.make_loan()
        with transaction.atomic():
            before = sync_loan_due(loan)
        original_id = before.pk
        self.put(payload(payday=10, paydayApplyFrom="next_cycle"))
        self.today.return_value = date(2026, 11, 10)
        self.home_period()
        self.home_period()
        due = PaymentDue.objects.get(loan=loan, due_date=date(2026, 11, 20))
        self.assertEqual(due.pk, original_id)
        self.assertEqual(due.obligation_state, "active")

    def test_long_cycle_loan_months_preserve_partial_and_update_unpaid(self):
        from finance.api_loans import sync_loan_due
        self.put(payload(payday=10))
        self.home_period()
        loan = self.make_loan()
        with transaction.atomic():
            first = sync_loan_due(loan)
        match = self.mark_partial(first)
        snapshot = PaymentDue.objects.filter(pk=first.pk).values().get()
        self.put(payload(payday=25, paydayApplyFrom="next_cycle"))
        response = self.client.patch(f"/api/finance/loans/{loan.pk}/", {"monthlyPayment": 150_000}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.home_period()
        self.assertEqual(PaymentDue.objects.filter(pk=first.pk).values().get(), snapshot)
        self.assertTrue(PaymentMatch.objects.filter(pk=match.pk, due=first).exists())
        self.assertEqual(PaymentDue.objects.filter(loan=loan, due_date=date(2026, 11, 20)).count(), 1)
        self.assertEqual(PaymentDue.objects.get(loan=loan, due_date=date(2026, 11, 20)).confirmed_amount, 150_000)
        response = self.client.patch(f"/api/finance/loans/{loan.pk}/", {"paymentDay": None}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PaymentDue.objects.get(loan=loan, due_date=date(2026, 11, 20)).obligation_state, "canceled")
        first.refresh_from_db()
        self.assertEqual(first.obligation_state, "active")

    def test_immediate_change_reuses_existing_paid_month(self):
        self.put(payload())
        expense = self.make_expense()
        self.home_period()
        paid = PaymentDue.objects.get(recurring=expense, due_date=date(2026, 10, 20))
        paid.payment_state, paid.paid_amount = "paid", 100_000
        paid.save(update_fields=["payment_state", "paid_amount"])
        snapshot = PaymentDue.objects.filter(pk=paid.pk).values().get()
        self.put(payload(payday=10, paydayApplyFrom="current_cycle"))
        self.home_period()
        self.assertEqual(PaymentDue.objects.filter(pk=paid.pk).values().get(), snapshot)
        self.assertEqual(PaymentDue.objects.filter(recurring=expense, due_date=date(2026, 10, 20)).count(), 1)

    def test_failed_due_preparation_rolls_back_salary_cycle_and_dues(self):
        self.put(payload(payday=10))
        self.make_expense()
        self.home_period()
        before = self.client.get(URL).json()
        due_ids = set(PaymentDue.objects.values_list("pk", flat=True))
        with patch("budgets.salary.prepare_salary_loans", side_effect=RuntimeError("test rollback")):
            with self.settings(DEBUG=True):
                with self.assertRaises(RuntimeError):
                    self.put(payload(payday=25, paydayApplyFrom="next_cycle"))
        self.assertEqual(self.client.get(URL).json(), before)
        self.assertEqual(set(PaymentDue.objects.values_list("pk", flat=True)), due_ids)
        self.assertEqual(BudgetCycle.objects.get(user=self.user, status="active").ends_before, date(2026, 11, 10))


    def test_fixed_api_applies_due_change_without_visiting_home(self):
        self.put(payload())
        self.home_period()
        self.put(payload(payday=10, paydayApplyFrom="next_cycle"))
        self.today.return_value = date(2026, 11, 10)
        response = self.client.get("/api/budgets/fixed/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(FinancialProfile.objects.get(user=self.user).payday, 10)
        self.assertEqual(BudgetCycle.objects.get(user=self.user, status="active").starts_on, date(2026, 11, 10))

    def test_loan_patch_applies_due_change_without_visiting_home(self):
        self.put(payload())
        self.home_period()
        loan = self.make_loan()
        self.put(payload(payday=10, paydayApplyFrom="next_cycle"))
        self.today.return_value = date(2026, 11, 10)
        response = self.client.patch(f"/api/finance/loans/{loan.pk}/", {"monthlyPayment": 150_000}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(FinancialProfile.objects.get(user=self.user).payday, 10)
        self.assertEqual(BudgetCycle.objects.get(user=self.user, status="active").starts_on, date(2026, 11, 10))
        self.assertEqual(PaymentDue.objects.get(loan=loan, due_date=date(2026, 11, 20)).confirmed_amount, 150_000)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL 행 잠금 검사")
class SalaryConcurrencyTests(TransactionTestCase):
    serialized_rollback = True

    def setUp(self):
        self.user = get_user_model().objects.create_user(email="salary-lock@example.com")
        clock = patch("django.utils.timezone.localdate", return_value=date(2026, 10, 28))
        clock.start()
        self.addCleanup(clock.stop)

    def request(self, method, path, data, started, pids):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '5s'")
                cursor.execute("SELECT pg_backend_pid()")
                pids.append(cursor.fetchone()[0])
            client = APIClient()
            client.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
            started.set()
            response = getattr(client, method)(path, data, format="json")
            self.assertEqual(response.status_code, 200, response.content)
            return response.json()
        finally:
            connections.close_all()

    def wait_for_locks(self, pids):
        deadline = monotonic() + 3
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM pg_stat_activity WHERE pid = ANY(%s) AND wait_event_type = 'Lock'", [pids])
                if cursor.fetchone()[0] == len(pids):
                    return
            sleep(0.01)
        self.fail("회원 잠금을 기다리는 요청이 확인되지 않았습니다.")

    def test_concurrent_first_puts_keep_one_complete_profile(self):
        first_data = payload(paydayApplyFrom="current_cycle")
        second_data = payload(takeHomeIncome=3_500_000, payday=10, paydayApplyFrom="current_cycle")
        with ThreadPoolExecutor(max_workers=2) as pool:
            with transaction.atomic():
                get_user_model().objects.select_for_update(no_key=True).get(pk=self.user.pk)
                pids, first_started, second_started = [], Event(), Event()
                first = pool.submit(self.request, "put", URL, first_data, first_started, pids)
                second = pool.submit(self.request, "put", URL, second_data, second_started, pids)
                self.assertTrue(first_started.wait(3))
                self.assertTrue(second_started.wait(3))
                self.wait_for_locks(pids)
            first.result(timeout=10)
            second.result(timeout=10)
        profile = FinancialProfile.objects.get(user=self.user)
        self.assertIn((profile.monthly_take_home_income, profile.payday), [(2_800_000, 25), (3_500_000, 10)])

    def test_home_reads_salary_only_after_acquiring_user_lock(self):
        FinancialProfile.objects.create(user=self.user, monthly_take_home_income=2_800_000, payday=25)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                get_user_model().objects.select_for_update(no_key=True).get(pk=self.user.pk)
                pids, started = [], Event()
                home = pool.submit(self.request, "get", "/api/home/", None, started, pids)
                self.assertTrue(started.wait(3))
                self.wait_for_locks(pids)
                FinancialProfile.objects.filter(user=self.user).update(payday=10, monthly_take_home_income=3_500_000)
            period = home.result(timeout=10)["period"]
        self.assertEqual((period["startDate"], period["endDate"]), ("2026-10-10", "2026-11-09"))

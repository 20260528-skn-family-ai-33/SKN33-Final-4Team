import hashlib
import uuid
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from users.models import Consent

from . import codef
from .codef_loans import LoanDataError, normalize_loan_accounts, normalize_loan_details
from .models import Connection, FinancialTransaction, Loan, PaymentDue, SyncRun
from .sync_loans import sync_bank_loans


NUMBER = "12345678901234"
START, END = date(2019, 6, 1), date(2019, 7, 31)


def account(number=NUMBER):
    return {"resAccount": number, "resAccountDisplay": "123456-78-901234",
            "resAccountCurrency": "KRW", "resAccountLoanExecNo": "",
            "resAccountName": "테스트 대출"}


def detail(number=NUMBER):
    return {"resAccount": number, "resPrincipal": "120,000,000", "resLoanBalance": "120000000",
            "resRate": "1.44", "resAccountEndDate": "20210628", "resDatePayment": "20190728",
            "resState": "", "resLoanKind": "대출 종류", "commStartDate": "20190601",
            "commEndDate": "20190708", "resTrHistoryList": [
                {"resTransTypeNm": "일반신규", "resPrincipal": "120000000", "resInterest": "0"},
            ]}


class LoanNormalizationTest(SimpleTestCase):
    def test_separate_endpoints_and_params(self):
        with patch("finance.codef._request", return_value={"resLoan": [account()]}) as request:
            self.assertEqual(codef.list_loan_accounts("id", "0004")[0]["account_no"], NUMBER)
            request.assert_called_once_with(codef.PATH_ACCOUNT_LIST, {"connectedId": "id", "organization": "0004"})
        with patch("finance.codef._request", return_value=detail()) as request:
            result = codef.get_loan_details("id", "0004", NUMBER, START, END)
            self.assertEqual(result["reported_rate"], Decimal("1.44"))
            self.assertEqual(request.call_args.args[0], "/v1/kr/bank/p/loan/transaction-list")
            self.assertEqual(request.call_args.args[1]["startDate"], "20190601")

    def test_empty_and_zero_are_different(self):
        for value, expected in [("", None), (None, None), ("0", 0)]:
            with self.subTest(value=value):
                row = detail()
                row.update(resPrincipal=value, resLoanBalance=value, resRate=value)
                result = normalize_loan_details(row, NUMBER)
                self.assertEqual(result["reported_balance"], expected)
                self.assertEqual(result["reported_rate"], expected)

    def test_invalid_values_fail_without_echoing_input(self):
        for field, values in {
            "resPrincipal": ["private-invalid", "-1", "1.5", "12,34", True, "9223372036854775808"],
            "resRate": ["NaN", "Infinity", "-1", "1000", "1.000001", True],
            "resDatePayment": ["20260230", "2026-10-27", "2026123"],
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    row = detail()
                    row[field] = value
                    with self.assertRaises(LoanDataError) as error:
                        normalize_loan_details(row, NUMBER)
                    self.assertNotIn("private-invalid", str(error.exception))

    def test_malformed_list_is_not_empty_success(self):
        for data in [{}, {"resLoan": None}, {"resLoan": {}}, {"resLoan": [None]}, []]:
            with self.subTest(data=data), self.assertRaises(LoanDataError):
                normalize_loan_accounts(data)
        self.assertEqual(normalize_loan_accounts({"resLoan": []}), [])

    def test_duplicate_missing_currency_and_execution_are_rejected(self):
        for rows in [[account(), account()], [{**account(), "resAccount": ""}],
                     [{**account(), "resAccountCurrency": "USD"}],
                     [{**account(), "resAccountCurrency": ""}],
                     [{**account(), "resAccountLoanExecNo": "1"}]]:
            with self.subTest(rows=rows), self.assertRaises(LoanDataError):
                normalize_loan_accounts({"resLoan": rows})

    def test_identity_and_period_are_checked(self):
        for change in [{"resAccount": "other"}, {"commEndDate": ""},
                       {"commEndDate": "20190101"}, {"resTrHistoryList": {}},
                       {"resTrHistoryList": [None]}]:
            with self.subTest(change=change), self.assertRaises(LoanDataError):
                normalize_loan_details({**detail(), **change}, NUMBER)

    def test_unknown_overdue_is_not_false_or_repaid(self):
        result = normalize_loan_details({**detail(), "resState": "unverified"}, NUMBER)
        self.assertIsNone(result["overdue"])
        self.assertTrue(result["unmapped_overdue"])


@override_settings(CODEF={"ENV": "demo", "CONNECTED_ID": "test-id"})
class LoanSyncTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email=f"loan-sync-{uuid.uuid4().hex}@example.com")
        cls.connection = Connection.objects.create(
            user=cls.user, business_type="bank", organization_code="0004", status="connected",
        )
        cls.consent = Consent.objects.create(
            user=cls.user, purpose="finance_inquiry", product_scope="loan",
            document_version="test", document_hash="test-only",
        )

    def setUp(self):
        self.accounts = [account()]
        self.details = {NUMBER: detail()}
        patcher = patch("finance.codef._request", side_effect=self.response)
        self.request = patcher.start()
        self.addCleanup(patcher.stop)

    def response(self, path, params):
        if path == codef.PATH_ACCOUNT_LIST:
            return {"resLoan": deepcopy(self.accounts)}
        self.assertEqual(path, codef.PATH_LOAN_TRANSACTIONS)
        response = self.details[params["account"]]
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)

    def sync(self, user=None):
        return sync_bank_loans(user or self.user, "test-id", "0004", START, END)

    def test_saves_mapping_without_inventing_repayments(self):
        result = self.sync()
        self.assertEqual(result, {"loan_count": 1, "created_count": 1, "updated_count": 0, "skipped_count": 0})
        loan = Loan.objects.get(user=self.user)
        self.assertEqual(loan.reported_principal, 120000000)
        self.assertEqual(loan.reported_rate, Decimal("1.44"))
        self.assertEqual(loan.reported_payment_date, date(2019, 7, 28))
        self.assertEqual(loan.maturity_date, date(2021, 6, 28))
        self.assertEqual(loan.product_name, "테스트 대출")
        self.assertEqual(loan.origin, "provider")
        self.assertEqual(loan.source_loan_key, hashlib.sha256(NUMBER.encode()).hexdigest())
        for field in ["monthly_payment", "payment_day", "repayment_method", "overdue", "linked_account_id",
                      "source_loan_ref_ciphertext", "manual_balance", "manual_rate"]:
            self.assertIsNone(getattr(loan, field))
        self.assertFalse(FinancialTransaction.objects.filter(user=self.user).exists())
        self.assertFalse(PaymentDue.objects.filter(user=self.user).exists())
        run = SyncRun.objects.get(loan=loan)
        self.assertEqual(run.covered_to, date(2019, 7, 8))
        self.assertEqual(run.requested_to, END)

    def test_repeat_preserves_user_fields_and_updates_provider_values(self):
        self.sync()
        loan = Loan.objects.get(user=self.user)
        loan.monthly_payment, loan.payment_day, loan.repayment_method = 400000, 28, "bullet"
        loan.manual_balance, loan.manual_rate = 5000, Decimal("3")
        loan.confirmed_at = timezone.now()
        loan.field_states["monthlyPayment"] = {"source": "manual", "confirmationStatus": "confirmed"}
        loan.field_states["reportedBalance"] = {"source": "provider", "confirmationStatus": "confirmed", "confirmedAt": "old"}
        loan.save()
        confirmed = loan.confirmed_at
        self.details[NUMBER]["resLoanBalance"] = "119000000"
        self.assertEqual(self.sync()["updated_count"], 1)
        loan.refresh_from_db()
        self.assertEqual(Loan.objects.filter(user=self.user).count(), 1)
        self.assertEqual((loan.monthly_payment, loan.payment_day, loan.repayment_method), (400000, 28, "bullet"))
        self.assertEqual((loan.manual_balance, loan.manual_rate), (5000, Decimal("3")))
        self.assertEqual(loan.confirmed_at, confirmed)
        self.assertEqual(loan.reported_balance, 119000000)
        self.assertEqual(loan.field_states["monthlyPayment"]["source"], "manual")
        self.assertIsNone(loan.field_states["reportedBalance"]["confirmedAt"])

    def test_missing_values_clear_provider_snapshot_only(self):
        self.sync()
        self.details[NUMBER].update(resLoanBalance="", resRate="")
        self.sync()
        loan = Loan.objects.get(user=self.user)
        self.assertIsNone(loan.reported_balance)
        self.assertIsNone(loan.reported_rate)
        self.assertEqual(loan.field_states["reportedBalance"]["confirmationStatus"], "unknown")

    def test_empty_list_does_not_delete_or_mark_repaid(self):
        self.sync()
        self.accounts = []
        self.assertEqual(self.sync()["loan_count"], 0)
        self.assertEqual(Loan.objects.get(user=self.user).status, "repaying")

    def test_zero_balance_does_not_mark_repaid(self):
        self.details[NUMBER]["resLoanBalance"] = "0"
        self.sync()
        self.assertEqual(Loan.objects.get(user=self.user).status, "repaying")

    def test_detail_failure_leaves_existing_snapshot_unchanged(self):
        self.sync()
        self.details[NUMBER]["resLoanBalance"] = "100"
        second = "22345678901234"
        self.accounts.append(account(second))
        self.details[second] = codef.CodefError("CF-99999", "secret-id and password")
        with self.assertRaises(codef.CodefError):
            self.sync()
        self.assertEqual(Loan.objects.get(user=self.user).reported_balance, 120000000)
        run = SyncRun.objects.filter(user=self.user, status="failed").get()
        self.assertEqual(run.business_code, "CF-99999")
        self.assertNotIn("secret", str(run.__dict__))

    def test_save_failure_rolls_back_all_loans(self):
        second = "22345678901234"
        self.accounts.append(account(second))
        self.details[second] = detail(second)
        original = Loan.save
        def save(instance, *args, **kwargs):
            if instance.source_loan_key == hashlib.sha256(second.encode()).hexdigest():
                raise RuntimeError("simulated DB error")
            return original(instance, *args, **kwargs)
        with patch.object(Loan, "save", save), self.assertRaises(RuntimeError):
            self.sync()
        self.assertFalse(Loan.objects.filter(user=self.user).exists())
        self.assertEqual(SyncRun.objects.filter(user=self.user).get().status, "failed")

    def test_other_user_is_not_reused(self):
        self.sync()
        other = get_user_model().objects.create_user(email=f"loan-other-{uuid.uuid4().hex}@example.com")
        Connection.objects.create(user=other, business_type="bank", organization_code="0004", status="connected")
        Consent.objects.create(user=other, purpose="finance_inquiry", product_scope="loan", document_version="test", document_hash="test")
        self.sync(other)
        self.assertEqual(Loan.objects.filter(source_loan_key=hashlib.sha256(NUMBER.encode()).hexdigest()).count(), 2)

    def test_reconnect_reuses_original_id(self):
        self.sync()
        loan = Loan.objects.get(user=self.user)
        self.connection.status, self.connection.disconnected_at = "disconnected", timezone.now()
        self.connection.save()
        new = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        self.sync()
        loan.refresh_from_db()
        self.assertEqual(loan.connection_id, new.pk)
        self.assertEqual(Loan.objects.filter(user=self.user).count(), 1)

    def test_manual_identity_collision_does_not_overwrite(self):
        Loan.objects.create(user=self.user, connection=self.connection, origin="manual",
                            source_loan_key=hashlib.sha256(NUMBER.encode()).hexdigest())
        with self.assertRaises(LoanDataError):
            self.sync()
        self.assertIsNone(Loan.objects.get(user=self.user).reported_balance)

    def test_no_consent_no_provider_call(self):
        self.consent.revoked_at = timezone.now()
        self.consent.save()
        with self.assertRaises(PermissionError):
            self.sync()
        self.request.assert_not_called()

    def test_disconnected_no_provider_call(self):
        self.connection.status, self.connection.disconnected_at = "disconnected", timezone.now()
        self.connection.save()
        with self.assertRaises(Connection.DoesNotExist):
            self.sync()
        self.request.assert_not_called()

    def test_revocation_during_fetch_blocks_save(self):
        original = self.response
        def response(path, params):
            result = original(path, params)
            if path == codef.PATH_LOAN_TRANSACTIONS:
                Consent.objects.filter(pk=self.consent.pk).update(revoked_at=timezone.now())
            return result
        self.request.side_effect = response
        with self.assertRaises(PermissionError):
            self.sync()
        self.assertFalse(Loan.objects.filter(user=self.user).exists())

    def test_newer_snapshot_is_not_overwritten(self):
        self.sync()
        Loan.objects.filter(user=self.user).update(fetched_at=timezone.now() + timedelta(minutes=1))
        self.details[NUMBER]["resLoanBalance"] = "100"
        self.assertEqual(self.sync()["skipped_count"], 1)
        self.assertEqual(Loan.objects.get(user=self.user).reported_balance, 120000000)

    def test_product_name_masks_raw_and_formatted_identifiers(self):
        self.accounts[0]["resAccountName"] = f"{NUMBER} / 123456-78-901234"
        self.sync()
        loan = Loan.objects.get(user=self.user)
        self.assertNotIn(NUMBER, str(loan.__dict__))
        self.assertNotIn("123456-78-901234", str(loan.__dict__))
        self.assertIn("****1234", loan.product_name)

    def test_command_and_safe_failure_output(self):
        output = StringIO()
        call_command("codef_sync_loans", email=self.user.email, org="0004", start=START, end=END, stdout=output)
        self.assertIn("신규 1개", output.getvalue())
        self.details[NUMBER] = codef.CodefError("CF-99999", "secret-message")
        with self.assertRaises(CommandError) as error:
            call_command("codef_sync_loans", email=self.user.email, org="0004", start=START, end=END)
        self.assertNotIn("secret", str(error.exception))

    @override_settings(CODEF={"ENV": "sandbox", "CONNECTED_ID": "example"})
    def test_command_rejects_sandbox_in_user_database(self):
        with self.assertRaises(CommandError):
            call_command("codef_sync_loans", email=self.user.email, org="0004", start=START, end=END)
        self.request.assert_not_called()

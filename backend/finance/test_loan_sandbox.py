"""공개 샌드박스 통합 검사. scripts/check_codef_loans_sandbox.py로 명시 실행한다."""
import os
from datetime import date
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import patch
from urllib.parse import urlparse

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from users.models import Consent

from . import codef
from .models import Connection, FinancialTransaction, Loan, PaymentDue, SyncRun
from .sync_loans import sync_bank_loans


@skipUnless(os.environ.get("RUN_CODEF_LOAN_SANDBOX") == "1", "공개 샌드박스 검사는 명시 실행")
@override_settings(CODEF={"ENV": "sandbox", "PUBLIC_KEY": "sandbox-read-only-no-rsa-operation"})
class LoanSandboxTest(TestCase):
    def test_public_sandbox_saves_and_refreshes_without_duplicates(self):
        codef._client.cache_clear()
        self.addCleanup(codef._client.cache_clear)
        user = get_user_model().objects.create_user(email="isolated-codef-sandbox@example.invalid")
        Connection.objects.create(user=user, business_type="bank", organization_code="0004", status="connected")
        Consent.objects.create(user=user, purpose="finance_inquiry", product_scope="loan",
                               document_version="sandbox-test", document_hash="test-only")
        # 공식 SDK README의 공개 샘플. 개인 CODEF 연결/인증정보를 사용하지 않는다.
        sample_id = "byi1wYwD40k8hEIiXl6bRF"
        original_request = requests.sessions.Session.request

        def bounded_request(session, method, url, **kwargs):
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname not in {"sandbox.codef.io", "oauth.codef.io"}:
                raise RuntimeError("Unexpected sandbox host")
            kwargs["timeout"] = (10, 35)
            kwargs["allow_redirects"] = False
            return original_request(session, method, url, **kwargs)

        def sync():
            try:
                return sync_bank_loans(user, sample_id, "0004", date(2019, 6, 1), date(2019, 7, 31))
            except Exception as exc:
                # 기관 오류의 원문과 식별값은 테스트 출력에서도 숨긴다.
                raise AssertionError(f"Sandbox sync failed ({type(exc).__name__}); details omitted") from None

        with patch.object(requests.sessions.Session, "request", bounded_request):
            self.assertEqual(sync()["created_count"], 1)
            loan = Loan.objects.get(user=user)
            self.assertEqual(loan.reported_principal, 120000000)
            self.assertEqual(loan.reported_balance, 120000000)
            self.assertEqual(loan.reported_rate, Decimal("1.44"))
            for field in ("monthly_payment", "payment_day", "overdue", "linked_account_id"):
                self.assertIsNone(getattr(loan, field))
            run = SyncRun.objects.get(loan=loan)
            self.assertEqual(run.covered_to, date(2019, 7, 8))
            self.assertEqual(run.requested_to, date(2019, 7, 31))
            loan.monthly_payment, loan.payment_day = 400000, 28
            loan.save(update_fields=["monthly_payment", "payment_day"])
            self.assertEqual(sync()["updated_count"], 1)
            loan.refresh_from_db()
            self.assertEqual(Loan.objects.count(), 1)
            self.assertEqual((loan.monthly_payment, loan.payment_day), (400000, 28))
            self.assertFalse(FinancialTransaction.objects.exists())
            self.assertFalse(PaymentDue.objects.exists())
            self.assertFalse(SyncRun.objects.exclude(status="success").exists())

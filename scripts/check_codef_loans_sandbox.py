"""실제 CODEF 샌드박스 → 격리된 메모리 DB 저장 검사. .env/사용자 DB는 읽지 않는다.

실행: .venv/Scripts/python.exe scripts/check_codef_loans_sandbox.py
공식 공개 샘플만 사용하고 연결 ID·토큰·계좌 원문은 출력하지 않는다.
"""
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

import django
import requests
from django.conf import settings


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    # 이 스크립트는 별도 프로세스 전용이다. 실행 중인 서버 설정을 바꾸지 않는다.
    settings.configure(
        SECRET_KEY="isolated-public-sandbox-check-only",
        INSTALLED_APPS=["django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions",
                        "users", "finance", "budgets", "chat"],
        AUTH_USER_MODEL="users.User", USE_TZ=True, TIME_ZONE="Asia/Seoul",
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        CODEF={"ENV": "sandbox", "PUBLIC_KEY": "sandbox-read-only-no-rsa-operation"},
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()
    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from finance.models import Connection, FinancialTransaction, Loan, PaymentDue, SyncRun
    from finance.sync_loans import sync_bank_loans
    from users.models import Consent

    call_command("migrate", verbosity=0, interactive=False)
    user = get_user_model().objects.create_user(email="isolated-codef-sandbox@example.com")
    Connection.objects.create(user=user, business_type="bank", organization_code="0004", status="connected")
    Consent.objects.create(user=user, purpose="finance_inquiry", product_scope="loan",
                           document_version="sandbox-test", document_hash="test-only")

    # CODEF 공식 SDK README의 공개 샘플. 개인 .env의 연결 ID를 사용하지 않는다.
    sample_id = "byi1wYwD40k8hEIiXl6bRF"
    original_request = requests.sessions.Session.request
    def bounded_request(session, method, url, **kwargs):
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {"sandbox.codef.io", "oauth.codef.io"}:
            raise RuntimeError("Unexpected sandbox host")
        kwargs["timeout"] = (10, 35)
        kwargs["allow_redirects"] = False
        return original_request(session, method, url, **kwargs)

    with patch.object(requests.sessions.Session, "request", bounded_request):
        first = sync_bank_loans(user, sample_id, "0004", date(2019, 6, 1), date(2019, 7, 31))
        assert first["created_count"] == 1, "Sandbox example changed: review fixture"
        loan = Loan.objects.get(user=user)
        assert loan.reported_principal == 120000000
        assert loan.reported_balance == 120000000
        assert loan.reported_rate == Decimal("1.44")
        assert loan.monthly_payment is None and loan.payment_day is None
        assert loan.overdue is None and loan.linked_account_id is None
        run = SyncRun.objects.get(loan=loan)
        assert run.covered_to == date(2019, 7, 8) and run.requested_to == date(2019, 7, 31)
        loan.monthly_payment, loan.payment_day = 400000, 28
        loan.save(update_fields=["monthly_payment", "payment_day"])
        second = sync_bank_loans(user, sample_id, "0004", date(2019, 6, 1), date(2019, 7, 31))
        loan.refresh_from_db()
        assert Loan.objects.count() == 1 and second["updated_count"] == 1
        assert loan.monthly_payment == 400000 and loan.payment_day == 28
        assert not FinancialTransaction.objects.exists() and not PaymentDue.objects.exists()
        assert not SyncRun.objects.exclude(status="success").exists()
    print("PASS: actual CODEF sandbox -> isolated SQLite Loan; create=1, repeat update=1, duplicates=0")
    print("PASS: principal/balance=120000000, rate=1.44; user input preserved; no repayment inferred")
    print("PASS: actual coverage=2019-06-01..2019-07-08 (requested end=2019-07-31)")
    print("PASS: no personal credentials read; no persistent database changed")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # SDK 예외 본문에 응답 원문이 들어 있을 수 있다.
        print(f"FAIL: {type(exc).__name__}; sandbox check did not complete (sensitive details omitted)")
        sys.exit(1)

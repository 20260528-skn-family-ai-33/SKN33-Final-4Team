"""실제 CODEF 샌드박스 → 격리된 PostgreSQL 테스트 DB 저장 검사.

실행: .venv/Scripts/python.exe scripts/check_codef_loans_sandbox.py
프로젝트의 로컬 DB 접속 설정을 사용해 임의 이름의 테스트 DB를 생성·제거한다.
CODEF는 공식 공개 샘플만 사용한다. 개발 DB에 migrate/flush하지 않는다.
"""
import os
import sys
import uuid
from pathlib import Path

import django
from django.conf import settings


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ["RUN_CODEF_LOAN_SANDBOX"] = "1"
    os.environ.setdefault("PGCONNECT_TIMEOUT", "5")
    database = settings.DATABASES["default"]
    if database["ENGINE"] != "django.db.backends.postgresql" or database.get("HOST") not in (
        "localhost", "127.0.0.1", "::1",
    ):
        print("FAIL: this check requires a local PostgreSQL server with pgvector and CREATE DATABASE permission")
        return 1
    # 기존 테스트 DB와 충돌하거나 기존 DB를 삭제하지 않도록 매번 새 이름을 쓴다.
    database["TEST"] = {"NAME": "test_codef_loan_sandbox_" + uuid.uuid4().hex}
    settings.CODEF = {"ENV": "sandbox", "PUBLIC_KEY": "sandbox-read-only-no-rsa-operation"}
    django.setup()
    from django.test.runner import DiscoverRunner
    failures = DiscoverRunner(verbosity=1, interactive=False, keepdb=False).run_tests([
        "finance.test_loan_sandbox.LoanSandboxTest",
    ])
    if not failures:
        print("PASS: actual CODEF sandbox -> isolated PostgreSQL; create, repeat update, user input preservation")
        print("PASS: full migrations including pgvector applied to test DB; test DB removed")
    return int(bool(failures))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # SDK 예외 본문에 응답 원문이 들어 있을 수 있다.
        print(f"FAIL: {type(exc).__name__}; sandbox check did not complete (sensitive details omitted)")
        sys.exit(1)

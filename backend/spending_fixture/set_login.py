"""fixture 회원 3명(윤서 · 민재 · 지우)에게만 로그인 비밀번호를 넣는다 — 더미 서비스 실행(MEMO 22절) 전용

실행 (backend/):  FIXTURE_LOGIN_PASSWORD='…' python -m spending_fixture.set_login
- 비밀번호는 환경변수 FIXTURE_LOGIN_PASSWORD 에서만 읽는다(8자 이상). 화면 · 로그 · 파일에 내보내지 않는다
- fixture DB 인지(seed.guard) 먼저 검사. 회원은 이메일(design.json 의 synthetic_email)로 찾고, 셋이 다 있어야 넣는다
- 다른 회원 · 다른 칸은 바꾸지 않는다. seed --apply 를 다시 하면 비밀번호가 지워지므로 그 뒤 다시 실행한다
"""
import os
import sys

os.environ["DJANGO_SETTINGS_MODULE"] = "spending_fixture.service_settings"  # 다른 설정이 들어와도 fixture DB 로만
import django  # noqa: E402

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.db import transaction  # noqa: E402

from . import seed  # noqa: E402

ENV = "FIXTURE_LOGIN_PASSWORD"
MIN_LENGTH = 8


def set_passwords(password, design=None):
    """→ [(별칭, 이메일)]. 비밀번호가 짧거나 · fixture DB 가 아니거나 · 회원이 빠졌으면 SystemExit (아무것도 바꾸지 않음)"""
    design = design or seed.load("design.json")
    if not password or len(password) < MIN_LENGTH:
        raise SystemExit(f"환경변수 {ENV} 에 {MIN_LENGTH}자 이상 비밀번호를 넣어 주세요")
    seed.guard()
    emails = {v["synthetic_email"]: v["alias"] for v in design["personas"].values()}
    users = list(get_user_model().objects.filter(email__in=emails))
    if len(users) != len(emails):
        raise SystemExit("fixture 회원이 없어요. 먼저: python -m spending_fixture.seed --apply")
    with transaction.atomic():
        for user in users:
            user.set_password(password)
            user.save(update_fields=["password"])
    return [(emails[e], e) for e in emails]


def main():
    done = set_passwords(os.environ.get(ENV))
    print("로그인 비밀번호를 넣었어요: " + " · ".join(f"{alias}({email})" for alias, email in done))


if __name__ == "__main__":
    sys.exit(main())

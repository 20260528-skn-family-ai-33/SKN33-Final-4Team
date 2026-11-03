"""소비 분석 fixture 전용 설정. 공통 설정을 그대로 쓰고 DB 이름만 바꾼다 (.env · config 수정 없음)

사용: python manage.py migrate --settings=spending_fixture.settings
"""
from config.settings import *  # noqa: F401,F403
from config.settings import DATABASES

FIXTURE_DB = "money_cushion_router_fixture"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")

if DATABASES["default"]["HOST"] not in LOCAL_HOSTS:
    raise ImproperlyConfigured("spending_fixture 는 로컬 DB에서만 실행한다")  # noqa: F405

DATABASES = {"default": {
    **DATABASES["default"],
    "NAME": FIXTURE_DB,
    "TEST": {"NAME": f"test_{FIXTURE_DB}"},
}}

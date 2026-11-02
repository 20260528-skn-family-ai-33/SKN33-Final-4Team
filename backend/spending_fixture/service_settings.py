"""더미 서비스 실행 설정 (MEMO 22절 1단계) — fixture DB 로 서비스 API 를 띄운다. 팀 기본 설정 · .env 는 고치지 않는다

실행 (backend/):  python manage.py runserver 127.0.0.1:8001 --settings=spending_fixture.service_settings
프론트 (frontend/): npx vite --config vite.fixture.config.js   → http://localhost:5174

- DB: spending_fixture.settings 그대로 (money_cushion_router_fixture, 로컬 호스트만)
- 쿠키: 세션 · CSRF 이름을 서비스(8000)와 다르게 → 같은 localhost 에서 두 로그인이 서로 덮지 않는다
  (CSRF 쿠키 이름은 vite.fixture.config.js 의 CSRF 와 같아야 한다)
- 외부 금융 호출: 금융 연결 · 다시 불러오기 주소는 service_urls 가 503, CODEF 키는 빈 값
- 실제 LLM: FIXTURE_LIVE_LLM=1 python manage.py runserver ... (상한 없음 · 재시도 없음, 끄면 가짜 해석)
"""
import os

from django.core.exceptions import ImproperlyConfigured

from .settings import *  # noqa: F401,F403
from .settings import CODEF, CSRF_TRUSTED_ORIGINS

ROOT_URLCONF = "spending_fixture.service_urls"

SESSION_COOKIE_NAME = "mc_fixture_sessionid"
CSRF_COOKIE_NAME = "mc_fixture_csrftoken"
# 로컬 http 전용 (spending_fixture.settings 가 로컬 DB 가 아니면 멈춘다) — DEBUG 값과 상관없이 쿠키를 http 로 보낸다
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = False
CSRF_TRUSTED_ORIGINS = [*CSRF_TRUSTED_ORIGINS, "http://localhost:5174", "http://127.0.0.1:5174"]

# 실수로 CODEF 를 불러도 인증 단계에서 실패하게 키를 비운다 (환경 이름은 그대로 — 값 검사용)
CODEF = {**CODEF, "CLIENT_ID": "", "CLIENT_SECRET": "", "PUBLIC_KEY": "", "CONNECTED_ID": ""}

# 실제 LLM 은 FIXTURE_LIVE_LLM=1 로 켤 때만 (service_chat.reading). 키 · 모델이 없으면 첫 메시지가 아니라 시작할 때 멈춘다 (값은 출력하지 않음)
if os.environ.get("FIXTURE_LIVE_LLM") == "1" and not (os.environ.get("OPENAI_API_KEY") and os.environ.get("LLM_MODEL")):
    raise ImproperlyConfigured("FIXTURE_LIVE_LLM=1 에는 .env 의 OPENAI_API_KEY · LLM_MODEL 이 필요해요")

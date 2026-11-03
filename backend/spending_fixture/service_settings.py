"""더미 서비스 실행 설정 (MEMO 22절 1단계) — fixture DB 로 서비스 API 를 띄운다. 팀 기본 설정 · .env 는 고치지 않는다

실행 (backend/):  python manage.py runserver 127.0.0.1:8001 --settings=spending_fixture.service_settings
프론트 (frontend/): npx vite --config vite.fixture.config.js   → http://localhost:5174

- DB: spending_fixture.settings 그대로 (money_cushion_router_fixture, 로컬 호스트만)
- 쿠키: 세션 · CSRF 이름을 서비스(8000)와 다르게 → 같은 localhost 에서 두 로그인이 서로 덮지 않는다
  (CSRF 쿠키 이름은 vite.fixture.config.js 의 CSRF 와 같아야 한다)
- 외부 금융 호출: 금융 연결 · 다시 불러오기 주소는 service_urls 가 503, CODEF 키는 빈 값
- 챗봇: 새 엔진을 켠다(CHAT_ENGINE = "router", chat/engine.py). 기준 날짜는 fixture 기준일 고정(CHAT_TODAY). 팀 기본 설정엔 이 값들이 없다(= legacy)
- LLM (MEMO 25-12, 환경변수 → 같은 이름의 설정값. 값이 없으면 가짜 해석):
  CHAT_LLM = fake(기본) | live — 예전 이름 FIXTURE_LIVE_LLM=1 도 live
  CHAT_LLM_MAX_CALLS = 호출 상한(1 이상 정수, 없으면 상한 없음 — 사용자 테스트 기본) · CHAT_LLM_LEDGER = 저장소 밖 장부 경로(없으면 메모리 수만)
  재시도 없음 · 모델을 바꾸지 않음. 키 · 모델은 OPENAI_API_KEY · LLM_MODEL (출력하지 않음)
"""
import os
from pathlib import Path

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

CHAT_ENGINE = "router"
CHAT_TODAY = os.environ.get("CHAT_TODAY", "2026-10-29")
if CHAT_TODAY in ("", "today"):
    CHAT_TODAY = None  # 알파는 실제 오늘; 기본 3인 시드는 기존 날짜
CHAT_LLM = "live" if os.environ.get("CHAT_LLM") == "live" or os.environ.get("FIXTURE_LIVE_LLM") == "1" else "fake"
if os.environ.get("CHAT_LLM") not in (None, "", "fake", "live"):
    raise ImproperlyConfigured("CHAT_LLM 은 fake 또는 live 예요")
CHAT_LLM_MAX_CALLS = os.environ.get("CHAT_LLM_MAX_CALLS") or None
# B 증감 설명(MEMO 26): CHAT_EXPLAIN_PLAN=on 일 때만 (기본 꺼짐 = 지금 템플릿 설명 그대로). 분석 과정(dev_trace)은 더미 실행에서만 응답에 싣는다
CHAT_EXPLAIN_PLAN = os.environ.get("CHAT_EXPLAIN_PLAN") == "on"
CHAT_DEV_TRACE = True
CHAT_LLM_LEDGER = os.environ.get("CHAT_LLM_LEDGER") or None

# 잘못된 값은 첫 메시지가 아니라 시작할 때 멈춘다 (키 값은 출력하지 않음)
if CHAT_LLM_MAX_CALLS is not None:
    if not CHAT_LLM_MAX_CALLS.isdigit() or int(CHAT_LLM_MAX_CALLS) < 1:
        raise ImproperlyConfigured("CHAT_LLM_MAX_CALLS 는 1 이상 정수예요 (상한 없이 쓰려면 비워 두세요)")
    CHAT_LLM_MAX_CALLS = int(CHAT_LLM_MAX_CALLS)
if CHAT_LLM_LEDGER is not None:
    from chat.llm.ledger import ROOT
    _ledger = Path(CHAT_LLM_LEDGER).expanduser().resolve()
    if _ledger == ROOT or ROOT in _ledger.parents:
        raise ImproperlyConfigured("CHAT_LLM_LEDGER 는 저장소 밖 경로여야 해요")
if CHAT_LLM == "live" and not (os.environ.get("OPENAI_API_KEY") and os.environ.get("LLM_MODEL")):
    raise ImproperlyConfigured("CHAT_LLM=live 에는 .env 의 OPENAI_API_KEY · LLM_MODEL 이 필요해요")

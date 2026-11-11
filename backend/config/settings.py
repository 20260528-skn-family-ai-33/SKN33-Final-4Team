"""
머니쿠션 Django 설정 (Django 5.2 LTS)

값은 프로젝트 루트의 .env에서 읽는다. 변수 목록은 .env.example, docs/ENV_SETUP.md 참고.
https://docs.djangoproject.com/en/5.2/ref/settings/
"""

import base64
import binascii
import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

# backend/ 폴더
BASE_DIR = Path(__file__).resolve().parent.parent

# 프로젝트 루트의 scripts/ (ML 코드)를 import 하려고 (finance/classifier.py 가 scripts.spending_classifier 를 씀)
sys.path.append(str(BASE_DIR.parent))

# 프로젝트 루트(backend의 한 칸 위)의 .env 읽기
load_dotenv(BASE_DIR.parent / ".env")


def required(name):
    """꼭 있어야 하는 값. 칸이 없거나 비어 있으면(.env.example을 복사만 한 상태) 서버를 켤 때 바로 알려준다"""
    value = os.getenv(name, "").strip()
    if not value:
        raise ImproperlyConfigured(f".env에 {name} 값이 비어 있어요. docs/LOCAL_SETUP.md 3단계를 확인하세요.")
    return value


def env_list(name, default=""):
    """쉼표로 구분된 환경변수를 리스트로 변환"""
    return [v.strip() for v in os.getenv(name, default).split(",") if v.strip()]


# ===== 보안 =====
SECRET_KEY = required("DJANGO_SECRET_KEY")
DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() == "true"
# 오래 걸리는 작업(config/tasks.run_later)을 요청 안에서 끝까지 돌릴지. 테스트는 늘 요청 안에서 (테스트 트랜잭션 밖 스레드는 자료를 못 본다)
TASKS_INLINE = (len(sys.argv) > 1 and sys.argv[1] == "test") or os.getenv("TASKS_INLINE", "False").lower() == "true"
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
# 관리자 화면 주소. 운영에서는 추측하기 어려운 값으로 바꾼다 (코드에 적으면 GitHub에 공개되므로 환경변수로)
ADMIN_URL = (os.getenv("DJANGO_ADMIN_URL", "").strip().strip("/") or "admin") + "/"
if ADMIN_URL.split("/")[0] == "api":
    raise ImproperlyConfigured("DJANGO_ADMIN_URL은 api/ 로 시작할 수 없어요. API 주소를 가립니다.")
if DEBUG:
    # 로컬: React 개발 서버(Vite 5173)에서 프록시로 오는 저장 요청 허용
    CSRF_TRUSTED_ORIGINS += ["http://localhost:5173", "http://127.0.0.1:5173"]


# ===== 앱 =====
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    # API (리액트에 JSON 데이터 주기)
    "rest_framework",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    # 머니쿠션 앱
    "users",
    "finance",
    "budgets",
    "chat",
    "policies",
]

# ===== 회원 모델 =====
# Django 기본 User 대신 users.User(테이블 app_user, 이메일 로그인)를 쓴다.
# ⚠️ 첫 migrate 전에 정해야 하는 값이라 바꾸지 않는다. 다른 모델은 settings.AUTH_USER_MODEL로 회원을 가리킨다.
AUTH_USER_MODEL = "users.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],  # Swagger 문서 화면
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# ===== DB (Docker PostgreSQL) =====
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": required("POSTGRES_DB"),
        "USER": required("POSTGRES_USER"),
        "PASSWORD": required("POSTGRES_PASSWORD"),
        "HOST": os.getenv("POSTGRES_HOST", "localhost"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
    }
}


# ===== 비밀번호 검사 =====
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# ===== 언어 · 시간 =====
LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True


# ===== 정적 파일 =====
STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 로컬 개발: 메일은 실제로 보내지 않고 터미널에 출력
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"


# ===== API (Django REST framework) 공통 규칙 =====
# 자세한 약속은 docs/API_RULES.md
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # 로그인 방식: Django 세션(쿠키). React 와 같은 주소(프록시)로 붙어서 쿠키가 그대로 오간다
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    # 지금은 로그인 없이 다 열어 둔다. users.User 는 만들었고, 로그인 API·화면이 붙으면 IsAuthenticated 로 바꾼다
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    # 보낼 때: 키를 camelCase 로 (living_used → livingUsed). 개발 중엔 브라우저로 열면 보기 좋은 화면도
    "DEFAULT_RENDERER_CLASSES": ["config.api.CamelJSONRenderer"]
    + (["rest_framework.renderers.BrowsableAPIRenderer"] if DEBUG else []),
    # 받을 때: React 가 보낸 camelCase 키를 snake_case 로 (livingUsed → living_used)
    "DEFAULT_PARSER_CLASSES": ["config.api.SnakeJSONParser"],
    # 에러는 항상 {"error": {"code", "message", "fields"}} 한 모양으로
    "EXCEPTION_HANDLER": "config.api.exception_handler",
    # 날짜·시각: ISO 8601 (예: 2026-10-19, 2026-10-19T15:30:00+09:00)
    "DATETIME_FORMAT": "iso-8601",
    "DATE_FORMAT": "iso-8601",
    "TIME_FORMAT": "iso-8601",
    # 비밀번호 대입 막기 (users/api.py 의 *Throttle). 넘으면 429 throttled
    "DEFAULT_THROTTLE_RATES": {"login": "5/min", "signup": "10/hour", "withdraw": "5/hour"},
}

# 횟수 제한 기록이 남는 곳. 기본(LocMem)은 프로세스마다 따로 센다
# shortcut: gunicorn 워커가 여럿이면 워커 수만큼 느슨해짐, 배포 때 settings_production 에서 DB 캐시로 바꾼다
if sys.argv[1:2] == ["test"]:
    # 테스트끼리 같은 IP(127.0.0.1)로 로그인 횟수가 쌓이지 않게. 횟수 제한 테스트는 override_settings 로 켠다
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}

# 세션·CSRF 쿠키 (배포에서는 https 에서만 보내기)
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14   # 로그인 유지 2주
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = False             # React 가 csrftoken 쿠키를 읽어 X-CSRFToken 헤더로 보낸다

# 확정 계약은 docs/openapi/parts/에서 관리한다. 아직 없는 API도 문서에 포함한다.
# 실제 응답이 계약과 다른 목업은 별도 '현재 서버 확인' 화면에서 호출한다.
SPECTACULAR_SETTINGS = {
    "TITLE": "머니쿠션 API 명세서",
    "VERSION": "2026-10-24",
    "SERVE_PUBLIC": True,
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
}


# ===== CODEF (계좌·카드·대출 조회) =====
# 키는 .env 에만. 은행 비밀번호는 어디에도 저장하지 않는다 (연결 후 connectedId 만 보관)
CODEF = {
    "ENV": (os.getenv("CODEF_ENV") or "demo").strip().lower(),   # sandbox · demo · product
    "CLIENT_ID": os.getenv("CODEF_DEMO_ID", ""),
    "CLIENT_SECRET": os.getenv("CODEF_DEMO_SECRET", ""),
    "PUBLIC_KEY": os.getenv("CODEF_PUBLIC_KEY", ""),
    "CONNECTED_ID": os.getenv("CODEF_CONNECTED_ID", ""),  # 개발용 내 연결 ID (회원별 저장은 DB 연결 후)
}


# 회원별 CODEF connectedId 암호화 키 (finance/connected_id.py, 10/27 결정)
# SECRET_KEY 와 따로 둔다: SECRET_KEY 를 바꿔도 저장된 connectedId 를 계속 풀 수 있게. 32바이트 urlsafe base64
def encryption_key(name):
    value = required(name)
    try:
        key = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error):
        key = b""
    if len(key) != 32:
        raise ImproperlyConfigured(f".env의 {name}는 32바이트 키여야 해요. .env.example에 적힌 명령으로 다시 만들어 주세요.")
    return key


CONNECTED_ID_ENCRYPTION_KEY = encryption_key("CONNECTED_ID_ENCRYPTION_KEY")

# 계좌 · 대출 · 카드 번호로 만드는 중복 확인 키의 HMAC 키 (finance/identifiers.py). 32바이트 urlsafe base64
# 한 키가 새도 다른 쪽은 안전하게 연결 ID 암호화 키와 다른 값을 쓴다
IDENTIFIER_HMAC_KEY = encryption_key("IDENTIFIER_HMAC_KEY")
if IDENTIFIER_HMAC_KEY == CONNECTED_ID_ENCRYPTION_KEY:
    raise ImproperlyConfigured(".env의 IDENTIFIER_HMAC_KEY는 CONNECTED_ID_ENCRYPTION_KEY와 다른 값이어야 해요.")


# ===== 챗봇 엔진 (chat/api.py · chat/engine.py) =====
# CHAT_ENGINE = legacy(기본, 기존 선택지 챗봇) | router(라우터 + 소비 분석 + 예산 조정)
# CHAT_LLM = fake(기본, 가짜 해석 — 네트워크 없음) | live(실제 LLM, OPENAI_API_KEY · LLM_MODEL 필요)
# 기준 날짜(CHAT_TODAY)는 두지 않는다: 일반 서버는 오늘 날짜로 실제 거래를 읽는다 (고정 날짜는 spending_fixture.service_settings 만)
CHAT_ENGINE = os.getenv("CHAT_ENGINE", "").strip() or "legacy"
CHAT_LLM = os.getenv("CHAT_LLM", "").strip() or "fake"
if CHAT_ENGINE not in ("legacy", "router"):
    raise ImproperlyConfigured("CHAT_ENGINE 은 legacy 또는 router 예요")
if CHAT_LLM not in ("fake", "live"):
    raise ImproperlyConfigured("CHAT_LLM 은 fake 또는 live 예요")
if CHAT_LLM == "live" and not (os.getenv("OPENAI_API_KEY") and os.getenv("LLM_MODEL")):
    raise ImproperlyConfigured("CHAT_LLM=live 에는 .env 의 OPENAI_API_KEY · LLM_MODEL 이 필요해요")
# 소비 분석 LLM 확장 (값이 on 이 아니면 꺼짐 = 지금 템플릿 답 그대로)
# CHAT_EXPLAIN_PLAN = on: 원인 가설을 말한 설명 질문("배달 때문이야?")을 B(계획 · 답 LLM, MEMO 26)로 확인한다 (가짜 해석은 정해 둔 계획 · 답)
# CHAT_AGENT = on: 첫 조회 뒤 필요할 때만 판단 LLM 1회 → 추가 도구 최대 1회(MEMO 31). 못 끝내면 첫 결과 + 못 확인한 요구 안내
CHAT_EXPLAIN_PLAN = os.getenv("CHAT_EXPLAIN_PLAN", "").strip() == "on"
CHAT_AGENT = os.getenv("CHAT_AGENT", "").strip() == "on"
# 같은 그룹은 감액 가능액 비례 배분. 실제 ML 참조는 별도 공급한다.
BUDGET_ADJUST_ALLOCATOR = (os.getenv("BUDGET_ADJUST_ALLOCATOR", "").strip()
                           or "budgets.adjustment_core.calculation.proportional_group_reduction")
BUDGET_ADJUST_REFERENCE_PROVIDER = os.getenv("BUDGET_ADJUST_REFERENCE_PROVIDER", "").strip() or None
BUDGET_DESIGN_ENABLED = os.getenv("BUDGET_DESIGN_ENABLED", "false").lower() == "true"
BUDGET_DESIGN_EXTERNAL_ENABLED = os.getenv("BUDGET_DESIGN_EXTERNAL_ENABLED", "false").lower() == "true"
if BUDGET_ADJUST_REFERENCE_PROVIDER is None and BUDGET_DESIGN_ENABLED:
    BUDGET_ADJUST_REFERENCE_PROVIDER = "budgets.design_service.reference_from_initial_input"

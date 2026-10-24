"""
머니쿠션 Django 설정 (Django 5.2 LTS)

값은 프로젝트 루트의 .env에서 읽는다. 변수 목록은 .env.example, docs/ENV_SETUP.md 참고.
https://docs.djangoproject.com/en/5.2/ref/settings/
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

# backend/ 폴더
BASE_DIR = Path(__file__).resolve().parent.parent

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
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
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
    # API (리액트에 JSON 데이터 주기)
    "rest_framework",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    # 머니쿠션 앱
    "users",
    "finance",
    "budgets",
    "chat",
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
}

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

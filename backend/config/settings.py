"""
머니쿠션 Django 설정 (Django 5.2 LTS)

값은 프로젝트 루트의 .env에서 읽는다. 변수 목록은 .env.example, docs/ENV_SETUP.md 참고.
https://docs.djangoproject.com/en/5.2/ref/settings/
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# backend/ 폴더
BASE_DIR = Path(__file__).resolve().parent.parent

# 프로젝트 루트(backend의 한 칸 위)의 .env 읽기
load_dotenv(BASE_DIR.parent / ".env")


def env_list(name, default=""):
    """쉼표로 구분된 환경변수를 리스트로 변환"""
    return [v.strip() for v in os.getenv(name, default).split(",") if v.strip()]


# ===== 보안 =====
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]  # 비어 있으면 바로 에러로 알려줌
DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() == "true"
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")


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
    # 머니쿠션 앱
    "users",
    "finance",
    "budgets",
    "chat",
]

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
        "DIRS": [BASE_DIR / "templates"],  # 공통 틀(base.html)과 팝업 조각
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
        "NAME": os.getenv("POSTGRES_DB"),
        "USER": os.getenv("POSTGRES_USER"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD"),
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
STATICFILES_DIRS = [BASE_DIR / "static"]  # 공통 CSS·JS

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 로컬 개발: 메일은 실제로 보내지 않고 터미널에 출력
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

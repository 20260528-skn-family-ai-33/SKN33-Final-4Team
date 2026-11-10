"""Copy to backend/config/settings_production.py before enabling deployment."""
from .settings import *  # noqa: F403

DEBUG = False
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# Only Caddy can reach the backend port; Caddy overwrites this header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR.parent / "staticfiles"
MIDDLEWARE = list(MIDDLEWARE)
MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
DATABASES["default"]["OPTIONS"] = {
    "sslmode": "verify-full",
    "sslrootcert": "/app/certs/global-bundle.pem",
    "connect_timeout": 10,
}
DATABASES["default"]["CONN_MAX_AGE"] = 60
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
# 로그인 · 가입 · 탈퇴 횟수 제한 기록 (users/api.py *Throttle).
# 기본 메모리 캐시는 gunicorn 워커마다 따로 세서 제한이 워커 수만큼 느슨해진다 → 모든 워커가 같이 보는 DB 캐시
# 캐시 표는 deploy.sh 가 migrate 다음에 createcachetable 로 만든다
CACHES = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "django_cache"}}
ROOT_URLCONF = "config.urls_production"

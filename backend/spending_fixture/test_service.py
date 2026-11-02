"""더미 서비스 실행 골격 (MEMO 22절 1단계) — 로그인 · 쿠키 분리 · 금융 연결 차단 · 비밀번호 명령. 네트워크 호출 없음
실행: python manage.py test spending_fixture.test_service --settings=spending_fixture.settings
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from . import seed
from . import service_settings as svc
from . import service_urls
from .test_fixture import DESIGN, fixture_db_only

NO_NETWORK = mock.patch("urllib.request.urlopen", side_effect=AssertionError("네트워크 호출 금지"))
SERVICE = override_settings(ROOT_URLCONF=svc.ROOT_URLCONF, SESSION_COOKIE_NAME=svc.SESSION_COOKIE_NAME,
                            CSRF_COOKIE_NAME=svc.CSRF_COOKIE_NAME, CODEF=svc.CODEF)
PASSWORD = "fixture-test-pass"  # 테스트 DB 안에서만 쓰는 값


def login(client, email, password=PASSWORD):
    client.get("/api/csrf/")
    token = client.cookies[svc.CSRF_COOKIE_NAME].value
    return client.post("/api/auth/login/", {"email": email, "password": password}, content_type="application/json",
                       HTTP_X_CSRFTOKEN=token)


@fixture_db_only
class ServiceSettingsTest(TestCase):
    def test_cookie_names_differ_from_service_and_codef_keys_are_empty(self):
        from django.conf import global_settings
        self.assertEqual((global_settings.SESSION_COOKIE_NAME, global_settings.CSRF_COOKIE_NAME), ("sessionid", "csrftoken"))
        self.assertNotIn(svc.SESSION_COOKIE_NAME, ("sessionid", "csrftoken"))
        self.assertNotIn(svc.CSRF_COOKIE_NAME, ("sessionid", "csrftoken", svc.SESSION_COOKIE_NAME))
        self.assertEqual({k: svc.CODEF[k] for k in ("CLIENT_ID", "CLIENT_SECRET", "PUBLIC_KEY", "CONNECTED_ID")},
                         dict.fromkeys(("CLIENT_ID", "CLIENT_SECRET", "PUBLIC_KEY", "CONNECTED_ID"), ""))
        self.assertIn(svc.DATABASES["default"]["NAME"], ("money_cushion_router_fixture", "test_money_cushion_router_fixture"))
        self.assertIn("http://localhost:5174", svc.CSRF_TRUSTED_ORIGINS)

    def test_frontend_config_uses_same_csrf_cookie_name(self):
        from pathlib import Path
        config = (Path(__file__).resolve().parents[2] / "frontend" / "vite.fixture.config.js").read_text(encoding="utf-8")
        self.assertIn(f"const CSRF_COOKIE = '{svc.CSRF_COOKIE_NAME}'", config)
        self.assertIn("'/api': 'http://127.0.0.1:8001'", config)


@fixture_db_only
@SERVICE
class ServiceLoginTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)
        from . import set_login
        cls.done = set_login.set_passwords(PASSWORD, DESIGN)

    def setUp(self):
        patcher = NO_NETWORK
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_yunseo_logs_in_as_fixture_member(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(login(client, "fixture-a@example.test").status_code, 200)
        me = client.get("/api/auth/me/")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(str(get_user_model().objects.get(email="fixture-a@example.test").pk), str(seed.user_ids(DESIGN)["A"]))
        self.assertEqual(me.json()["email"], "fixture-a@example.test")
        self.assertIn(svc.SESSION_COOKIE_NAME, client.cookies)
        self.assertNotIn("sessionid", client.cookies)  # 서비스(8000) 세션 쿠키를 만들거나 덮지 않는다
        self.assertNotIn("csrftoken", client.cookies)
        self.assertEqual(login(Client(enforce_csrf_checks=True), "fixture-a@example.test", "wrong-password").status_code, 400)

    def test_service_cookies_are_left_alone(self):
        """같은 브라우저(localhost)에 서비스 쿠키가 있어도 더미 서버는 읽지도 바꾸지도 않는다 (반대도 같다)"""
        client = Client(enforce_csrf_checks=True)
        client.cookies["sessionid"] = "service-session-value"
        client.cookies["csrftoken"] = "service-csrf-value"
        login(client, "fixture-b@example.test")
        self.assertEqual((client.cookies["sessionid"].value, client.cookies["csrftoken"].value),
                         ("service-session-value", "service-csrf-value"))
        self.assertEqual(client.get("/api/auth/me/").json()["email"], "fixture-b@example.test")
        with override_settings(SESSION_COOKIE_NAME="sessionid", CSRF_COOKIE_NAME="csrftoken"):  # 서비스 설정의 쿠키 이름
            self.assertEqual(client.get("/api/auth/me/").status_code, 403)  # 더미 로그인으로 서비스에 로그인되지 않는다

    def test_only_fixture_members_get_password(self):
        other = get_user_model().objects.create_user(email="someone@example.test", password="other-pass-123")
        from . import set_login
        set_login.set_passwords("another-pass-456", DESIGN)
        other.refresh_from_db()
        self.assertTrue(other.check_password("other-pass-123"))
        self.assertEqual([alias for alias, _ in self.done], ["윤서", "민재", "지우"])
        for bad in (None, "", "short"):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                set_login.set_passwords(bad, DESIGN)
        self.assertTrue(get_user_model().objects.get(email="fixture-c@example.test").check_password("another-pass-456"))

    def test_financial_connection_addresses_return_503(self):
        client = Client(enforce_csrf_checks=True)
        login(client, "fixture-a@example.test")
        blocked = [("post", "/api/finance/sync/"), ("post", "/api/finance/consents/"), ("get", "/api/finance/connect/accounts/"),
                   ("put", "/api/finance/connect/accounts/"), ("get", "/api/finance/connect/cards/"),
                   ("post", "/api/finance/connect/cards/"), ("post", "/api/finance/connections/"),
                   ("delete", "/api/finance/connections/00000000-0000-0000-0000-000000000000/")]
        for method, url in blocked:
            with self.subTest(method=method, url=url):
                response = getattr(client, method)(url, content_type="application/json")
                self.assertEqual((response.status_code, response.json()), (503, service_urls.UNAVAILABLE))
        self.assertEqual(Client().post("/api/finance/sync/").status_code, 503)  # 로그인 전에도 막는다
        self.assertEqual(client.get("/api/finance/connections/").status_code, 200)  # 읽기는 그대로
        self.assertEqual(client.get("/api/finance/transactions/").status_code, 200)

    def test_blocked_names_match_service_addresses(self):
        self.assertEqual({p.name for p in service_urls.urlpatterns[:len(service_urls.BLOCKED)]},
                         {f"fixture-blocked-{n}" for n in service_urls.BLOCKED})
        self.assertEqual(reverse("fixture-blocked-finance-sync"), "/api/finance/sync/")

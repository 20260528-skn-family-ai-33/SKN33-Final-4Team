"""로그인 · 회원 탈퇴 시도 횟수 제한 (users/api.py 의 LoginThrottle · WithdrawThrottle). 실행: backend 에서 python manage.py test users.test_throttle

- 테스트 실행 중에는 settings 가 캐시를 DummyCache 로 바꿔 횟수 제한이 꺼진다 → 이 파일에서만 LocMem 캐시로 다시 켠다
- 횟수는 settings 의 DEFAULT_THROTTLE_RATES (login 5/min, withdraw 5/hour)
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

EMAIL, PASSWORD = "throttle@example.com", "Synthetic123!"
THROTTLED = "시도가 너무 많아요. 잠시 후 다시 시도해 주세요."


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "throttle-test"}})
class ThrottleTests(TestCase):
    def setUp(self):
        cache.clear()  # 테스트끼리 시도 횟수가 이어지지 않게
        self.user = get_user_model().objects.create_user(email=EMAIL, password=PASSWORD)
        self.client = APIClient()

    def assert_throttled(self, response):
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"], {"code": "throttled", "message": THROTTLED})
        self.assertGreater(int(response["Retry-After"]), 0)

    def login(self, password):
        return self.client.post("/api/auth/login/", {"email": EMAIL, "password": password}, format="json")

    def withdraw(self, password):
        return self.client.delete("/api/auth/me/", {"password": password, "confirm": True}, format="json")

    def test_login_sixth_try_is_429(self):
        for _ in range(5):
            self.assertEqual(self.login("Wrong123!").status_code, 400)
        # 6번째는 맞는 비밀번호여도 막힌다
        self.assert_throttled(self.login(PASSWORD))

    def test_withdraw_sixth_try_is_429_and_me_get_still_200(self):
        self.client.force_login(self.user)
        for _ in range(5):
            self.assertEqual(self.withdraw("Wrong123!").status_code, 400)
        # 6번째는 맞는 비밀번호여도 막히고, 탈퇴도 되지 않는다
        self.assert_throttled(self.withdraw(PASSWORD))
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        # 횟수 제한은 DELETE(탈퇴)만 센다 → 내 계정 확인(GET)은 그대로 된다
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 200)

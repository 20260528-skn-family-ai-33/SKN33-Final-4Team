"""비밀번호 찾기 (users/api.py 의 password_reset · password_reset_confirm). 실행: backend 에서 python manage.py test users.test_password_reset

테스트 중에는 Django 가 메일을 실제로 보내지 않고 mail.outbox 에 모은다.
"""
import re
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from rest_framework.test import APIClient

from .api import RESET_LINK_INVALID

User = get_user_model()
EMAIL, OLD_PW, NEW_PW = "reset@example.com", "Synthetic123!", "Changed456!"


class PasswordResetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email=EMAIL, password=OLD_PW, nickname="재설정테스트")
        self.client = APIClient()

    def request_reset(self, email=EMAIL):
        return self.client.post("/api/auth/password-reset/", {"email": email}, format="json")

    def confirm(self, **body):
        return self.client.post("/api/auth/password-reset/confirm/", body, format="json")

    def link_values(self):
        """보낸 메일 속 링크에서 uid·token 꺼내기"""
        link = re.search(r"http\S+", mail.outbox[-1].body).group()
        # 요청 Host(테스트는 testserver)가 아니라 화면 주소로 간다
        self.assertTrue(link.startswith(f"{settings.FRONTEND_URL}/reset-password?"))
        query = parse_qs(urlparse(link).query)
        return {"uid": query["uid"][0], "token": query["token"][0]}

    def test_known_email_gets_reset_mail(self):
        # 대소문자·공백이 달라도 같은 회원
        response = self.request_reset("  Reset@Example.com ")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [EMAIL])

    def test_unknown_and_withdrawn_emails_answer_the_same_without_mail(self):
        User.objects.create_user(email="gone@example.com", password=OLD_PW, status=User.Status.WITHDRAWN)
        for email in ("nobody@example.com", "gone@example.com"):
            with self.subTest(email=email):
                response = self.request_reset(email)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(len(mail.outbox), 0)

    def test_link_sets_new_password_once_and_logs_out_other_sessions(self):
        other_device = APIClient()
        other_device.force_login(self.user)
        self.request_reset()
        values = self.link_values()

        response = self.confirm(**values, password=NEW_PW)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PW))
        # 비밀번호가 바뀌면 다른 기기 세션은 끊긴다
        self.assertEqual(other_device.get("/api/auth/me/").status_code, 403)

        # 같은 링크는 다시 못 쓴다
        again = self.confirm(**values, password="Another789!")
        self.assertEqual(again.status_code, 400)
        self.assertEqual(again.json()["error"], {"code": "invalid", "message": RESET_LINK_INVALID})

    def test_wrong_uid_or_token_is_rejected_without_fields(self):
        self.request_reset()
        values = self.link_values()
        for body in ({**values, "token": "wrong-token"}, {**values, "uid": "not-base64!"}, {**values, "uid": "MTIz"}):
            with self.subTest(body=body):
                response = self.confirm(**body, password=NEW_PW)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"], {"code": "invalid", "message": RESET_LINK_INVALID})
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD_PW))

    def test_new_password_follows_signup_rule(self):
        self.request_reset()
        response = self.confirm(**self.link_values(), password="onlyletters")
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.json()["error"]["fields"])

    def test_unknown_fields_are_rejected(self):
        response = self.client.post("/api/auth/password-reset/", {"email": EMAIL, "nickname": "x"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("nickname", response.json()["error"]["fields"])
        self.assertEqual(len(mail.outbox), 0)

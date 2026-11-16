"""시연 페르소나 시드 명령: 비밀번호를 확인하기 전에는 기존 계정을 지우지 않는다 (11/16 PR 리뷰)."""
import os
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from spending_fixture import persona_seed as ps
from users.models import User


class SeedPersonaResetTest(TestCase):
    def setUp(self):
        self.email = ps.PERSONAS["a"]["email"]
        self.existing = User.objects.create_user(email=self.email, password="Old-demo!7qR29x")

    def run_reset(self, password):
        env = {k: v for k, v in os.environ.items() if k != "DEMO_LOGIN_PASSWORD"}
        if password is not None:
            env["DEMO_LOGIN_PASSWORD"] = password
        with patch.dict(os.environ, env, clear=True):
            call_command("seed_persona", "a", "--apply", "--reset", stdout=StringIO())

    def test_missing_or_weak_password_keeps_the_existing_account(self):
        for password in (None, "", "1234"):
            with self.subTest(password=password):
                with self.assertRaises(CommandError):
                    self.run_reset(password)
                self.assertTrue(User.objects.filter(pk=self.existing.pk).exists())

    def test_failed_insert_rolls_back_the_delete(self):
        # 지우기와 새로 넣기는 한 트랜잭션: 넣다가 실패하면 지운 계정도 되돌린다
        with patch.object(ps, "insert_connected_state", side_effect=RuntimeError("insert failed")):
            with self.assertRaises(RuntimeError):
                self.run_reset("New-demo!7qR29x")
        self.assertTrue(User.objects.filter(pk=self.existing.pk).exists())

    def test_without_reset_an_existing_account_is_an_error(self):
        env = {**os.environ, "DEMO_LOGIN_PASSWORD": "New-demo!7qR29x"}
        with patch.dict(os.environ, env, clear=True), self.assertRaises(CommandError):
            call_command("seed_persona", "a", "--apply", stdout=StringIO())
        self.assertTrue(User.objects.filter(pk=self.existing.pk).exists())

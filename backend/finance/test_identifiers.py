"""계좌 · 대출 · 카드 중복 확인 키 (finance/identifiers.py)"""
import hashlib
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from .identifiers import LEGACY_MESSAGE, LegacyIdentifierKey, identifier_key, legacy_identifier_key

NUMBER = "1002123456789"


class IdentifierKeyTests(SimpleTestCase):
    def test_same_input_gives_same_key(self):
        self.assertEqual(identifier_key("account", NUMBER), identifier_key("account", NUMBER))

    def test_plain_sha256_is_not_used(self):
        # 키 없이 번호만 sha256 한 값과 같으면 DB 가 새었을 때 번호를 하나씩 넣어 보며 찾을 수 있다
        key = identifier_key("account", NUMBER)
        self.assertNotEqual(key, hashlib.sha256(NUMBER.encode()).hexdigest())
        self.assertNotEqual(key, hashlib.sha256(f"account:{NUMBER}".encode()).hexdigest())

    def test_kind_separates_keys(self):
        self.assertEqual(len({identifier_key(kind, NUMBER) for kind in ("account", "loan", "card")}), 3)

    def test_key_depends_on_secret(self):
        before = identifier_key("card", NUMBER)
        with override_settings(IDENTIFIER_HMAC_KEY=b"x" * 32):
            self.assertNotEqual(identifier_key("card", NUMBER), before)


class LegacyKeyTests(SimpleTestCase):
    def test_legacy_key_is_plain_sha256(self):
        # HMAC 전환 전 저장 방식 그대로여야 남은 행을 찾을 수 있다
        self.assertEqual(legacy_identifier_key(NUMBER), hashlib.sha256(NUMBER.encode()).hexdigest())

    def test_refresh_guides_reconnect_instead_of_retry(self):
        # API 모듈은 테스트 안에서 불러온다 (파일 맨 위에서 부르면 config.api 와 순환 import)
        from .api_new_connection import failure
        from .api_sync import run_part

        def work():
            raise LegacyIdentifierKey()
        part, reason = run_part("accounts", SimpleNamespace(pk="c1"), work)
        self.assertEqual((part["status"], reason), ("failed", ("legacy_identifier", ())))
        self.assertEqual(failure("legacy_identifier", "은행")[:3], (409, "not_ready", LEGACY_MESSAGE))

"""계좌 · 대출 · 카드 중복 확인 키 (finance/identifiers.py)"""
import hashlib

from django.test import SimpleTestCase, override_settings

from .identifiers import identifier_key

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

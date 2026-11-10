"""
계좌 · 대출 · 카드 번호로 만드는 중복 확인 키 (BankAccount.source_account_key 등)

- 번호를 그냥 sha256 하면 DB 가 새어 나갔을 때 번호 후보를 하나씩 넣어 보며 원래 번호를 찾을 수 있다.
  비밀 키가 있어야 같은 값을 만들 수 있는 HMAC-SHA256 으로 만든다.
- 키: .env 의 IDENTIFIER_HMAC_KEY (32바이트). CONNECTED_ID_ENCRYPTION_KEY 와 다른 값을 쓴다.
  키를 바꾸면 저장된 키와 맞지 않아 같은 계좌 · 카드를 새 행으로 저장하므로, 바꿀 땐 금융 데이터를 지우고 다시 연결한다.
- kind 를 앞에 붙여서 같은 번호라도 계좌 · 대출 · 카드 키가 서로 다르게 나온다.
- 전환 전(sha256) 키로 저장된 행이 남아 있으면 새 행을 만들지 않고 LegacyIdentifierKey 로 멈춘다 (PR #102 리뷰).
  동의 철회를 빠뜨려도 같은 계좌 · 거래가 조용히 두 번 저장되지 않게 한다.
"""
import hashlib
import hmac

from django.conf import settings


LEGACY_MESSAGE = "예전 방식으로 저장된 금융 정보가 남아 있어요. 금융 정보 조회 동의를 철회한 뒤 다시 연결해 주세요."


class LegacyIdentifierKey(ValueError):
    """HMAC 전환 전 키로 저장된 계좌 · 대출 · 카드가 남아 있음. 저장하지 않고 동의 철회 후 다시 연결을 안내한다"""
    code = "legacy_identifier"

    def __init__(self):
        super().__init__(LEGACY_MESSAGE)


def identifier_key(kind, value):
    return hmac.new(settings.IDENTIFIER_HMAC_KEY, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()


def legacy_identifier_key(value):
    """HMAC 전환 전 키: 번호를 그대로 sha256 (계좌 · 대출 · 카드 모두 같은 방식이었다)"""
    return hashlib.sha256(str(value).encode()).hexdigest()

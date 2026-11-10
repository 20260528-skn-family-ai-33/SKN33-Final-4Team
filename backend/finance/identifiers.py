"""
계좌 · 대출 · 카드 번호로 만드는 중복 확인 키 (BankAccount.source_account_key 등)

- 번호를 그냥 sha256 하면 DB 가 새어 나갔을 때 번호 후보를 하나씩 넣어 보며 원래 번호를 찾을 수 있다.
  비밀 키가 있어야 같은 값을 만들 수 있는 HMAC-SHA256 으로 만든다.
- 키: .env 의 IDENTIFIER_HMAC_KEY (32바이트). CONNECTED_ID_ENCRYPTION_KEY 와 다른 값을 쓴다.
  키를 바꾸면 저장된 키와 맞지 않아 같은 계좌 · 카드를 새 행으로 저장하므로, 바꿀 땐 금융 데이터를 지우고 다시 연결한다.
- kind 를 앞에 붙여서 같은 번호라도 계좌 · 대출 · 카드 키가 서로 다르게 나온다.
"""
import hashlib
import hmac

from django.conf import settings


def identifier_key(kind, value):
    return hmac.new(settings.IDENTIFIER_HMAC_KEY, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()

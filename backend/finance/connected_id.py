"""
회원별 CODEF connectedId 보관 (users.User.codef_connected_id_ciphertext)

- connectedId 는 회원의 금융기관 로그인 묶음을 가리키는 값이라 원문으로 두지 않고 AES-GCM 으로 암호화해 저장한다.
- 응답 · 로그에는 넣지 않는다 (API_RULES '보안 · 소유권').
- 암호화 키: 지금은 DJANGO_SECRET_KEY 에서 이 용도 전용으로 만든다. SECRET_KEY 를 바꾸면 저장된 값을 못 읽으므로
  전용 키(.env) 를 둘지는 명세 '확인 필요' (김나은 · 팀).
- AES 는 easycodefpy 가 이미 쓰는 pycryptodome(Crypto) 을 쓴다.
"""
import base64
import hashlib

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from django.conf import settings

_PREFIX = "v1:"
_PURPOSE = b"money-cushion/codef-connected-id/v1\x00"


class ConnectedIdUnreadable(Exception):
    """저장된 connectedId 를 풀 수 없음 (키가 바뀌었거나 값이 깨짐)"""


def _key():
    return hashlib.sha256(_PURPOSE + settings.SECRET_KEY.encode("utf-8")).digest()


def seal(connected_id):
    nonce = get_random_bytes(12)
    cipher = AES.new(_key(), AES.MODE_GCM, nonce=nonce)
    body, tag = cipher.encrypt_and_digest(connected_id.encode("utf-8"))
    return _PREFIX + base64.urlsafe_b64encode(nonce + tag + body).decode("ascii")


def unseal(value):
    if not value or not value.startswith(_PREFIX):
        raise ConnectedIdUnreadable()
    try:
        raw = base64.urlsafe_b64decode(value[len(_PREFIX):].encode("ascii"))
        nonce, tag, body = raw[:12], raw[12:28], raw[28:]
        cipher = AES.new(_key(), AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(body, tag).decode("utf-8")
    except (ValueError, KeyError) as exc:
        raise ConnectedIdUnreadable() from exc


def load(user):
    """회원의 connectedId. 아직 없으면 None"""
    if not user.codef_connected_id_ciphertext:
        return None
    return unseal(user.codef_connected_id_ciphertext)

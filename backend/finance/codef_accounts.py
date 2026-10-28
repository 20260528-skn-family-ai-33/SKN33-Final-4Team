"""
CODEF 계정 등록 (금융기관 로그인) — 새 연결 API(POST /api/finance/connections/) 전용

- 아이디 · 비밀번호 로그인만 쓴다 (loginType "1", 10/27 결정 1).
- 비밀번호 · 카드 비밀번호는 RSA 로 암호화해 CODEF 에 보내고 버린다. 카드번호도 저장하지 않는다.
  이 모듈은 받은 값을 어디에도 저장하거나 로그로 남기지 않는다 (10/27 결정 3).
- 추가 인증은 문자(SMS)만 지원한다 (10/27 결정 2). CODEF 가 CF-03002 를 주면 받은 2-way 정보를 돌려주고,
  화면이 받은 인증번호로 같은 요청을 한 번 더 보낸다 (두 번 나눠 보내기).
- CODEF 원래 코드(CF-xxxxx)는 호출한 쪽이 로그에만 남긴다. 화면에는 reason 으로 바꾼 안내만 보낸다.
"""
import json
import re
from dataclasses import dataclass

from django.conf import settings
from easycodefpy import encrypt_rsa

from . import codef
from .institutions import CODEF_BUSINESS_TYPE

PATHS = {
    "create": "/v1/account/create",   # 회원의 첫 연결 → connectedId 발급
    "add": "/v1/account/add",         # 이미 있는 connectedId 에 기관 추가
    "update": "/v1/account/update",   # 이미 붙어 있는 기관의 로그인 정보 갱신 (다시 연결)
}
SMS_REQUIRED = "CF-03002"
SMS_METHOD = "smsAuthNo"
TWO_WAY_KEYS = ("jobIndex", "threadIndex", "jti", "twoWayTimestamp")

# CODEF 코드 → 실패 이유. 실제 응답으로 확인한 것만 넣는다 (10/28 나은: CODEF 데모 현대카드로 확인)
# - CF-04000(계정 등록 실패)은 겉 코드라 표에 넣지 않는다. 진짜 이유는 data.errorList 첫 줄의 코드로 고른다 (read_result)
CODE_REASONS = {
    "CF-12401": "card_fields",  # 로그인 값 누락. extraMessage 에 빠진 칸 이름이 {cardNo} · {cardPassword} 처럼 온다
    "CF-13001": "unavailable",  # 조회일 · 기간 오류 (청구서 startDate 를 일 단위로 보냄 = 우리 요청 실수 → 로그로 확인)
    "CF-12100": "login",        # 아이디 · 비밀번호 오류 (codef.py 주석 기준, 직접 재현은 아직)
}
# CODEF 칸 이름 → 우리 요청 칸 이름 (CF-12401 extraMessage 의 {칸})
CODEF_FIELDS = {"cardNo": "card_no", "cardPassword": "card_password"}

# 위 표에 없는 코드는 CODEF 안내 문장으로 임시 판단한다 (실제 코드를 확인하면 CODE_REASONS 로 옮긴다).
# 순서가 중요하다: '카드 비밀번호' · '비밀번호 오류 횟수 초과' 가 일반 로그인 오류로 먼저 잡히지 않게 한다.
KEYWORD_RULES = (
    ("sms_expired", ("인증",), ("시간", "만료", "초과")),
    ("sms_wrong", ("인증번호",), ("일치", "틀", "오류", "올바르지", "잘못")),
    ("card_fields", ("카드번호", "카드 비밀번호", "카드비밀번호"), ("누락", "입력", "필수", "없")),
    ("already_registered", ("이미",), ("등록",)),
    ("not_registered", ("등록되지", "존재하지"), ("",)),
    ("locked", ("잠김", "잠금", "잠겼", "잠긴", "정지", "횟수", "변경"), ("",)),
    ("login", ("아이디", "비밀번호", "ID", "PW"), ("일치", "오류", "확인", "틀", "올바르지", "잘못")),
)


@dataclass
class Outcome:
    """CODEF 계정 등록 한 번의 결과
    kind: connected(연결됨) · sms(문자 인증 필요) · failed(실패)
    reason: failed 일 때 이유 (login · locked · card_fields · sms_wrong · sms_expired · unsupported_auth ·
            already_registered · not_registered · unavailable)"""
    kind: str
    connected_id: str = None
    two_way: dict = None
    reason: str = None
    codef_code: str = None
    missing: tuple = ()  # card_fields 일 때 CODEF 가 빠졌다고 한 칸 (예: ("card_no",))


def missing_fields(*texts):
    """CF-12401 extraMessage 의 {cardNo} · {cardPassword} → ("card_no", "card_password"). 우리가 받는 칸만"""
    found = []
    for text in texts:
        for name in re.findall(r"\{(\w+)\}", text or "") if isinstance(text, str) else ():
            field = CODEF_FIELDS.get(name)
            if field and field not in found:
                found.append(field)
    return tuple(found)


def classify(code, *texts):
    """CODEF 실패 코드 · 안내 문장 → 실패 이유. 모르면 unavailable (기관 점검 · 응답 없음과 같은 안내)"""
    if code in CODE_REASONS:
        return CODE_REASONS[code]
    text = " ".join(t for t in texts if isinstance(t, str))
    for reason, first, second in KEYWORD_RULES:
        if any(word in text for word in first) and any(word in text for word in second):
            return reason
    return "unavailable"


def account_item(business_type, organization_code, login_id, password, card_no=None, card_password=None):
    """CODEF accountList 한 줄. 비밀번호 · 카드 비밀번호는 여기서 암호화한다"""
    public_key = settings.CODEF["PUBLIC_KEY"]
    item = {
        "countryCode": "KR",
        "businessType": CODEF_BUSINESS_TYPE[business_type],
        "clientType": "P",
        "organization": organization_code,
        "loginType": "1",
        "id": login_id,
        "password": encrypt_rsa(password, public_key),
    }
    if card_no:
        item["cardNo"] = card_no
    if card_password:
        item["cardPassword"] = encrypt_rsa(card_password, public_key)
    return item


def register(mode, item, connected_id=None, sms=None):
    """CODEF 계정 등록 · 추가 · 갱신 한 번. sms = {"code": "123456", "two_way": {...}} 면 문자 인증 2단계 요청"""
    params = {"accountList": [item]}
    if mode != "create":
        params["connectedId"] = connected_id
    try:
        client, service = codef._client(), codef._service_type()
        if sms:
            params.update({"smsAuthNo": sms["code"], "is2Way": True, "twoWayInfo": sms["two_way"]})
            raw = client.request_certification(PATHS[mode], service, params)
        else:
            raw = client.request_product(PATHS[mode], service, params)
        body = json.loads(raw) if raw else {}
    except Exception:  # 통신 오류 · 응답 깨짐 (예외 내용에 요청 값이 섞일 수 있어 그대로 넘기지 않는다)
        return Outcome("failed", reason="unavailable")
    return read_result(mode, body, connected_id)


def read_result(mode, body, connected_id=None):
    """CODEF 계정 API 응답 → Outcome"""
    body = body if isinstance(body, dict) else {}
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    code = result.get("code")

    if code == SMS_REQUIRED:
        two_way = {key: data.get(key) for key in TWO_WAY_KEYS}
        if any(value in (None, "") for value in two_way.values()):
            return Outcome("failed", reason="unavailable", codef_code=code)
        method = data.get("method")
        if method and method != SMS_METHOD:
            # 보안문자 · 간편인증 등 문자 말고 다른 추가 인증 (10/27 결정 2: 문자만 지원)
            return Outcome("failed", reason="unsupported_auth", codef_code=code)
        return Outcome("sms", two_way=two_way, codef_code=code)

    errors = data.get("errorList") if isinstance(data.get("errorList"), list) else []
    if code == codef.SUCCESS and not errors:
        new_id = data.get("connectedId") or (connected_id if mode != "create" else None)
        if not new_id:
            return Outcome("failed", reason="unavailable", codef_code=code)
        return Outcome("connected", connected_id=new_id, codef_code=code)

    # CF-04000(계정 등록 실패) 같은 겉 코드면 errorList 첫 줄이 진짜 이유
    first = errors[0] if errors and isinstance(errors[0], dict) else result
    failed_code = first.get("code") or code
    reason = classify(failed_code, first.get("message"), first.get("extraMessage"))
    missing = missing_fields(first.get("extraMessage"), first.get("message")) if reason == "card_fields" else ()
    return Outcome("failed", reason=reason, codef_code=failed_code, missing=missing)

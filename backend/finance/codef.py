"""
CODEF 연결 (은행 계좌 조회 전용)

- 루트의 test_codef.py · test_transactions.py 에서 하던 일을 함수로 정리한 것.
- 키는 settings.CODEF (= .env) 에서 읽는다.
- 은행 비밀번호는 RSA 로 암호화해서 CODEF 에 한 번 보내고 끝. 저장·로그 출력 금지.
  연결이 끝나면 connectedId 만 남는다 (DB 연결 후 financial_connection 에 암호화해서 저장).
- 지금은 결과를 저장하지 않고 돌려주기만 한다. 저장은 DB 모델이 생긴 뒤에.

사용 예 (python manage.py shell):
    from finance import codef
    accounts = codef.list_accounts(connected_id, "0088")
    txs = codef.list_transactions(connected_id, "0088", account_no, date(2026, 10, 1), date(2026, 10, 19))
"""
import hashlib
import json
from datetime import datetime
from functools import lru_cache

from django.conf import settings
from easycodefpy import Codef, ServiceType, encrypt_rsa

# 은행 기관 코드
BANKS = {"0004": "국민", "0088": "신한", "0020": "우리", "0081": "하나", "0011": "농협"}

# CODEF 상품 주소
PATH_ACCOUNT_LIST = "/v1/kr/bank/p/account/account-list"
PATH_TRANSACTIONS = "/v1/kr/bank/p/account/transaction-list"

SUCCESS = "CF-00000"
_SERVICE = {"sandbox": ServiceType.SANDBOX, "demo": ServiceType.DEMO, "product": ServiceType.PRODUCT}


class CodefError(Exception):
    """CODEF 가 실패를 돌려줬을 때. code 예: CF-12100 (아이디·비밀번호 오류)"""

    def __init__(self, code, message, extra=""):
        self.code, self.message, self.extra = code, message, extra
        super().__init__(f"[{code}] {message} {extra}".strip())


# ----- 기본 준비 -----
def _service_type():
    env = settings.CODEF["ENV"]
    if env not in _SERVICE:
        raise ValueError(f"CODEF_ENV 는 sandbox · demo · product 중 하나여야 해요 (지금: {env})")
    return _SERVICE[env]


@lru_cache(maxsize=1)
def _client():
    """키를 넣은 Codef 객체 (한 번 만들고 계속 재사용)"""
    conf = settings.CODEF
    c = Codef()
    c.public_key = conf["PUBLIC_KEY"]
    env = conf["ENV"]
    if env == "demo":
        c.set_demo_client_info(conf["CLIENT_ID"], conf["CLIENT_SECRET"])
    elif env == "product":
        c.set_client_info(conf["CLIENT_ID"], conf["CLIENT_SECRET"])
    # sandbox 는 라이브러리에 들어 있는 공용 키를 쓴다
    return c


def _check(raw):
    """CODEF 응답(JSON 글자)을 dict 로 바꾸고, 실패면 CodefError"""
    body = json.loads(raw)
    result = body.get("result", {})
    if result.get("code") != SUCCESS:
        raise CodefError(result.get("code", "?"), result.get("message", ""), result.get("extraMessage", ""))
    return body.get("data") or {}


def _request(path, params):
    return _check(_client().request_product(path, _service_type(), params))


def mask(account_no):
    """1234567890 → ****7890 (화면·로그에는 항상 이렇게)"""
    s = str(account_no or "")
    return f"****{s[-4:]}" if len(s) >= 4 else "****"


def token_ok():
    """키가 맞는지 확인 (입장권을 받아 보기)"""
    return bool(_client().request_token(_service_type()))


# ----- 은행 연결 -----
def _login_account(org, login_id, password):
    return {
        "countryCode": "KR",
        "businessType": "BK",   # 은행
        "clientType": "P",      # 개인
        "organization": org,
        "loginType": "1",       # 아이디·비밀번호 로그인
        "id": login_id,
        "password": encrypt_rsa(password, settings.CODEF["PUBLIC_KEY"]),
    }


def connect_bank(org, login_id, password):
    """
    은행을 처음 연결하고 connectedId 를 돌려준다.
    password 는 여기서 암호화해 보내고 버린다 (저장·출력 금지).
    """
    data = _check(_client().create_account(_service_type(), {"accountList": [_login_account(org, login_id, password)]}))
    connected_id = data.get("connectedId")
    if not connected_id:
        errors = data.get("errorList") or []
        first = errors[0] if errors else {}
        raise CodefError(first.get("code", "?"), first.get("message", "연결 ID를 받지 못했어요"), first.get("extraMessage", ""))
    return connected_id


def add_bank(connected_id, org, login_id, password):
    """이미 있는 connectedId 에 은행을 하나 더 붙인다"""
    _check(_client().add_account(_service_type(), {
        "connectedId": connected_id,
        "accountList": [_login_account(org, login_id, password)],
    }))


# ----- 조회 -----
def list_accounts(connected_id, org):
    """
    보유 계좌 목록 (입출금·예적금만). 돌려주는 모양:
      [{"account_no", "name", "balance", "kind_code", "org"}]
    대출·외화·펀드·보험 계좌는 지금 쓰지 않아서 뺀다.
    """
    data = _request(PATH_ACCOUNT_LIST, {"connectedId": connected_id, "organization": org})
    return [
        {
            "account_no": a.get("resAccount", ""),
            "name": a.get("resAccountNickName") or a.get("resAccountName", ""),
            "balance": _to_int(a.get("resAccountBalance")),
            "kind_code": a.get("resAccountDeposit", ""),  # 11 = 입출금 (CODEF 예금 구분 코드)
            "org": org,
        }
        for a in data.get("resDepositTrust", [])
    ]


def list_transactions(connected_id, org, account_no, start, end):
    """
    수시입출 거래내역 (start ~ end, 최신순). 돌려주는 모양은 ERD financial_transaction 칸에 맞췄다:
      [{"occurred_on", "occurred_time", "amount", "direction", "balance_after",
        "source_description", "source_detail", "source_key"}]
    """
    data = _request(PATH_TRANSACTIONS, {
        "connectedId": connected_id,
        "organization": org,
        "account": account_no,
        "startDate": start.strftime("%Y%m%d"),
        "endDate": end.strftime("%Y%m%d"),
        "orderBy": "0",       # 0 = 최신순
        "inquiryType": "1",
    })
    return [normalize_transaction(row) for row in data.get("resTrHistoryList", [])]


# ----- CODEF 칸 → 우리 칸 -----
def _to_int(value):
    try:
        return int(str(value).replace(",", "") or 0)
    except ValueError:
        return 0


def normalize_transaction(row):
    """
    CODEF 거래 한 줄 → financial_transaction 모양
    - 출금(resAccountOut)과 입금(resAccountIn) 중 0 이 아닌 쪽이 금액·방향
    - 설명 칸 1~4 는 은행마다 쓰임이 달라서 원문을 source_detail 에 다 남긴다
      (대표 설명은 3 → 2 → 1 순서로 처음 있는 것. 실제 응답 보고 조정)
    - CODEF 거래엔 고유 ID 가 없어서 일시·금액·잔액·설명으로 source_key(중복 방지 키)를 만든다
    """
    out_amt, in_amt = _to_int(row.get("resAccountOut")), _to_int(row.get("resAccountIn"))
    detail = {f"desc{i}": row.get(f"resAccountDesc{i}", "") for i in range(1, 5)}
    day, time = row.get("resAccountTrDate", ""), row.get("resAccountTrTime", "")
    balance = _to_int(row.get("resAfterTranBalance"))
    key_src = "|".join([day, time, str(out_amt or in_amt), str(balance), *detail.values()])
    return {
        "occurred_on": datetime.strptime(day, "%Y%m%d").date() if day else None,
        "occurred_time": datetime.strptime(time, "%H%M%S").time() if len(time) == 6 else None,
        "amount": out_amt or in_amt,
        "direction": "out" if out_amt else "in",
        "balance_after": balance,
        "source_description": detail["desc3"] or detail["desc2"] or detail["desc1"],
        "source_detail": detail,
        "source_key": hashlib.sha256(key_src.encode()).hexdigest(),
    }


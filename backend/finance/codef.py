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
import re
from datetime import datetime
from functools import lru_cache

from django.conf import settings
from easycodefpy import Codef, ServiceType, encrypt_rsa

# 은행 기관 코드
BANKS = {"0004": "국민", "0088": "신한", "0020": "우리", "0081": "하나", "0011": "농협"}

# CODEF 상품 주소
PATH_ACCOUNT_LIST = "/v1/kr/bank/p/account/account-list"
PATH_TRANSACTIONS = "/v1/kr/bank/p/account/transaction-list"
PATH_LOAN_TRANSACTIONS = "/v1/kr/bank/p/loan/transaction-list"
PATH_CARD_LIST = "/v1/kr/card/p/account/card-list"
PATH_CARD_BILLS = "/v1/kr/card/p/account/billing-list"
PATH_CARD_APPROVALS = "/v1/kr/card/p/account/approval-list"

SUCCESS = "CF-00000"
_SERVICE = {"sandbox": ServiceType.SANDBOX, "demo": ServiceType.DEMO, "product": ServiceType.PRODUCT}


class CodefError(Exception):
    """CODEF 가 실패를 돌려줬을 때. code 예: CF-12100 (아이디·비밀번호 오류)"""

    def __init__(self, code, message, extra=""):
        self.code, self.message, self.extra = code, message, extra
        super().__init__(f"[{code}] {message} {extra}".strip())


class BankDataError(ValueError):
    """은행 응답을 확인할 수 없음 (목록 누락 · 금액 누락/비정상 · 방향 불명). 원문 · 계좌번호를 메시지에 넣지 않는다"""

    def __init__(self, code="invalid_bank_data"):
        self.code = code
        super().__init__("CODEF 은행 응답을 확인할 수 없어 저장하지 않았어요.")


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
            # 잔액 미제공 = None (BankAccount.balance null · 홈 unknownCount · '잔액 확인 안 됨'). 비정상 값은 BankDataError
            "balance": _amount(a.get("resAccountBalance"), False),
            "kind_code": a.get("resAccountDeposit", ""),  # 11 = 입출금 (CODEF 예금 구분 코드)
            "org": org,
        }
        for a in _rows(data, "resDepositTrust")
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
    return [normalize_transaction(row) for row in _rows(data, "resTrHistoryList")]


def list_loan_accounts(connected_id, org):
    """보유계좌 중 resLoan만 조회한다. 기존 list_accounts와 독립된 진입점."""
    from .codef_loans import normalize_loan_accounts

    data = _request(PATH_ACCOUNT_LIST, {"connectedId": connected_id, "organization": org})
    return normalize_loan_accounts(data)


def get_loan_details(connected_id, org, account_no, start, end):
    """대출 거래내역 상품의 계좌 단위 정보와 실제 조회 기간을 반환한다.

    거래 원금은 약정원금과 다르므로 거래 행을 Loan에 합산하지 않는다.
    """
    from .codef_loans import normalize_loan_details

    if start > end:
        raise ValueError("조회 시작일은 종료일보다 늦을 수 없어요.")
    data = _request(PATH_LOAN_TRANSACTIONS, {
        "connectedId": connected_id, "organization": org, "account": account_no,
        "startDate": start.strftime("%Y%m%d"), "endDate": end.strftime("%Y%m%d"),
        "orderBy": "0", "inquiryType": "1",
    })
    return normalize_loan_details(data, account_no)


def _card_auth(card_no, card_password):
    """현대카드처럼 조회 때도 카드번호 · 카드 비밀번호가 필요한 곳. 요청에만 싣고 저장 · 출력하지 않는다"""
    params = {}
    if card_no:
        params["cardNo"] = card_no
    if card_password:
        params["cardPassword"] = encrypt_rsa(card_password, settings.CODEF["PUBLIC_KEY"])
    return params


def list_cards(connected_id, org, card_no=None, card_password=None):
    """보유카드 목록 → [{"source_card_key", "masked_number", "name", "card_type", "card_status"}]"""
    from .codef_cards import normalize_cards

    data = _request(PATH_CARD_LIST, {
        "connectedId": connected_id, "organization": org, "inquiryType": "0",   # 0 = 카드 이미지 미포함
        **_card_auth(card_no, card_password),
    })
    return normalize_cards(data)


def list_card_bills(connected_id, org, month, card_no=None, card_password=None):
    """청구내역 (청구년월 YYYYMM 한 달). 일 단위로 보내면 CF-13001"""
    from .codef_cards import MONTH, normalize_card_bills

    if not isinstance(month, str) or not MONTH.fullmatch(month):
        raise ValueError("청구년월은 YYYYMM 이어야 해요.")
    data = _request(PATH_CARD_BILLS, {
        "connectedId": connected_id, "organization": org, "startDate": month,
        "memberStoreInfoYN": "0",   # 가맹점 상세(주소 · 전화) 미포함
        **_card_auth(card_no, card_password),
    })
    return normalize_card_bills(data, month, org)


def list_card_approvals(connected_id, org, start, end, card_no=None, card_password=None):
    """카드 승인내역 (start ~ end, 카드 전체). → (거래 목록, 실제 조회 기간)
    승인내역에서는 인증용 카드번호를 loginCardNo 로 보낸다 (cardNo 는 '카드별 조회' 대상 카드라 다른 뜻)"""
    from .codef_cards import normalize_approvals

    if start > end:
        raise ValueError("조회 시작일은 종료일보다 늦을 수 없어요.")
    auth = _card_auth(card_no, card_password)
    if "cardNo" in auth:
        auth["loginCardNo"] = auth.pop("cardNo")
    data = _request(PATH_CARD_APPROVALS, {
        "connectedId": connected_id, "organization": org,
        "startDate": start.strftime("%Y%m%d"), "endDate": end.strftime("%Y%m%d"),
        "orderBy": "0", "inquiryType": "1",   # 1 = 전체 카드 조회
        "memberStoreInfoType": "0", **auth,
    })
    return normalize_approvals(data, start, end)


# ----- CODEF 칸 → 우리 칸 -----
_AMOUNT = re.compile(r"-?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)")  # 정수 · 세 자리 쉼표. 음수는 취소 줄 · 마이너스 잔액


def _amount(value, required=True):
    """은행 금액 → int. 미제공(없음 · null · 빈 글자)은 None (required 면 BankDataError).
    비정상 글자 · 소수 · bool · 다른 자료형은 0원으로 바꾸지 않고 BankDataError"""
    if type(value) is int:
        return value
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise BankDataError()
        return None
    if not isinstance(value, str) or not _AMOUNT.fullmatch(value.strip()):
        raise BankDataError()
    return int(value.strip().replace(",", ""))


def _rows(data, key):
    """은행 목록. [] 는 정상 빈 목록, 키 누락 · null · 목록 아님 · dict 아닌 행은 BankDataError (대출 resLoan 과 같은 기준)"""
    rows = data.get(key) if isinstance(data, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise BankDataError()
    return rows


def normalize_transaction(row):
    """
    CODEF 거래 한 줄 → financial_transaction 모양
    - 출금(resAccountOut)과 입금(resAccountIn) 중 0 이 아닌 쪽이 금액·방향. 반대편은 0 · 빈 값 · 생략.
      둘 다 0/없음이거나 둘 다 0 이 아니면 방향을 정할 수 없어 BankDataError (임의 금액 · 방향으로 만들지 않음)
    - 설명 칸 1~4 는 은행마다 쓰임이 달라서 원문을 source_detail 에 다 남긴다
      (대표 설명은 3 → 2 → 1 순서로 처음 있는 것. 실제 응답 보고 조정)
    - CODEF 거래엔 고유 ID 가 없어서 일시·금액·잔액·설명으로 source_key(중복 방지 키)를 만든다.
      R08: 'bank:v2:<방향>:<수정 전 키>' — 방향만 다른 거래가 같은 키가 되지 않게. 끝은 예전 키라 sync 가 예전 거래를 찾는다
    - 금액이 음수면 앞선 거래의 취소 줄이다 (선결제 환불·예약 취소가 음수 출금으로 오는 은행이 있다).
      금액은 양수로 두고 취소로 표시한다. 원거래 연결은 transfer_rules.link_bank_cancellations
    """
    out_amt, in_amt = _amount(row.get("resAccountOut"), False), _amount(row.get("resAccountIn"), False)
    if bool(out_amt) == bool(in_amt):
        raise BankDataError()
    detail = {f"desc{i}": row.get(f"resAccountDesc{i}", "") for i in range(1, 5)}
    day, time = row.get("resAccountTrDate", ""), row.get("resAccountTrTime", "")
    balance = _amount(row.get("resAfterTranBalance"), False)  # 거래 후 잔액 미제공 = None (모델도 비어 있을 수 있음)
    signed = out_amt or in_amt
    direction = "out" if out_amt else "in"
    # 미제공 잔액은 수정 전처럼 0 으로 키를 만든다 — 이미 저장된 거래와 중복되지 않게 (R08 키 방식 그대로)
    key_src = "|".join([day, time, str(signed), str(balance or 0), *detail.values()])
    return {
        "occurred_on": datetime.strptime(day, "%Y%m%d").date() if day else None,
        "occurred_time": datetime.strptime(time, "%H%M%S").time() if len(time) == 6 else None,
        "amount": abs(signed),
        "direction": direction,
        "is_canceled": signed < 0,
        "canceled_amount": abs(signed) if signed < 0 else None,
        "balance_after": balance,
        "source_description": detail["desc3"] or detail["desc2"] or detail["desc1"],
        "source_detail": detail,
        "source_key": f"bank:v2:{direction}:{hashlib.sha256(key_src.encode()).hexdigest()}",
    }


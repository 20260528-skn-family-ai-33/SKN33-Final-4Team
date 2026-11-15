"""
연결할 수 있는 금융기관 표 (서버 기준)

- 화면의 frontend/src/utils/institutions.js 와 같은 목록이다 (CODEF 개발가이드 '은행 개요' · '카드 개요' · 상세 명세, 2026-11-13 확인).
- blocked: 지금은 연결할 수 없는 이유 (없으면 None). 새 연결 API 가 이 문구로 400 을 준다.
  10/27 결정: 아이디 · 비밀번호만 → 공동인증서 전용은 막음
  11/13 결정: 아이디 로그인이어도 서버가 안 받는 값(휴대폰 본인인증 · 출금 계좌)이 필요하거나 거래내역을 못 받는 곳도 막음
- card_fields: 연결할 때 꼭 더 받아야 하는 값. 저장하지 않고 매번 받는다 (10/27 결정 3).
"""

BANK, CARD = "bank", "card"
CODEF_BUSINESS_TYPE = {BANK: "BK", CARD: "CD"}

CERT_ONLY = "이 기관은 공동인증서로만 연결돼서 아직 연결할 수 없어요."
WITHDRAW_ACCOUNT = "이 기관은 출금 계좌 확인이 필요해서 아직 연결할 수 없어요."  # 우리 · 대구: CODEF 명세상 출금계좌번호 · 비밀번호
PHONE_AUTH = "이 기관은 휴대폰 본인인증이 필요해서 아직 연결할 수 없어요."  # 롯데카드: 이름 · 주민번호 앞 7자리 · 통신사 · 휴대폰
NO_HISTORY = "이 기관은 아이디 로그인으로 거래내역을 불러올 수 없어서 아직 연결할 수 없어요."  # 우체국: 보유계좌만
ENDED = "이 기관은 개인 연결 서비스가 끝나서 연결할 수 없어요."  # K뱅크: CODEF 명세 이력 '2023 개인뱅킹 종료로 제공 중단'


def _bank(code, label, blocked=None, name=None):
    return code, {"name": name or f"{label}은행", "blocked": blocked, "card_fields": ()}


def _card(code, name, blocked=None, card_fields=()):
    return code, {"name": name, "blocked": blocked, "card_fields": card_fields}


BANKS = dict([
    _bank("0088", "신한"), _bank("0004", "국민"), _bank("0020", "우리", WITHDRAW_ACCOUNT), _bank("0081", "하나"),
    _bank("0011", "농협"), _bank("0003", "기업"), _bank("0089", "K뱅크", ENDED, name="K뱅크"),
    _bank("0023", "SC제일"), _bank("0027", "씨티"), _bank("0002", "산업", CERT_ONLY),
    _bank("0007", "수협", CERT_ONLY), _bank("0071", "우체국", NO_HISTORY, name="우체국"),
    _bank("0045", "새마을금고", name="새마을금고"), _bank("0048", "신협", CERT_ONLY, name="신협"),
    _bank("0031", "대구", WITHDRAW_ACCOUNT), _bank("0032", "부산", CERT_ONLY), _bank("0039", "경남"),
    _bank("0034", "광주", CERT_ONLY), _bank("0037", "전북"), _bank("0035", "제주", CERT_ONLY),
])

CARDS = dict([
    _card("0306", "신한카드"), _card("0301", "국민카드"), _card("0303", "삼성카드"),
    # 현대카드: 아이디 · 비밀번호에 카드번호 · 카드 비밀번호가 꼭 필요 (CF-12401, 10/27 나은 확인)
    _card("0302", "현대카드", card_fields=("card_no", "card_password")),
    _card("0311", "롯데카드", PHONE_AUTH), _card("0309", "우리카드"), _card("0313", "하나카드"), _card("0304", "NH카드"),
    _card("0305", "BC카드"), _card("0307", "씨티카드"), _card("0320", "수협카드", CERT_ONLY),
    _card("0316", "광주카드", CERT_ONLY), _card("0315", "전북카드"), _card("0321", "제주카드", CERT_ONLY),
])

TABLE = {BANK: BANKS, CARD: CARDS}


def find(business_type, organization_code):
    """(업권, 기관 코드) → 기관 정보 dict. 없으면 None"""
    return TABLE.get(business_type, {}).get(organization_code)


def name_of(business_type, organization_code):
    """화면에 쓸 기관 이름. 표에 없으면 기관 코드를 그대로"""
    info = find(business_type, organization_code)
    return info["name"] if info else organization_code

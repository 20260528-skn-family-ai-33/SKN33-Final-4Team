"""
연결할 수 있는 금융기관 표 (서버 기준)

- 화면의 frontend/src/utils/institutions.js 와 같은 목록이다 (CODEF 개발가이드 '은행 개요' · '카드 개요', 2026-10-20 확인).
- id_login=False 인 기관은 공동인증서로만 연결돼서 지금은 연결할 수 없다 (10/27 결정: 아이디 · 비밀번호만).
- card_fields: 연결할 때 꼭 더 받아야 하는 값. 저장하지 않고 매번 받는다 (10/27 결정 3).
"""

BANK, CARD = "bank", "card"
CODEF_BUSINESS_TYPE = {BANK: "BK", CARD: "CD"}


def _bank(code, label, id_login=True, name=None):
    return code, {"name": name or f"{label}은행", "id_login": id_login, "card_fields": ()}


def _card(code, name, id_login=True, card_fields=()):
    return code, {"name": name, "id_login": id_login, "card_fields": card_fields}


BANKS = dict([
    _bank("0088", "신한"), _bank("0004", "국민"), _bank("0020", "우리"), _bank("0081", "하나"),
    _bank("0011", "농협"), _bank("0003", "기업"), _bank("0089", "K뱅크", name="K뱅크"),
    _bank("0023", "SC제일"), _bank("0027", "씨티"), _bank("0002", "산업", id_login=False),
    _bank("0007", "수협", id_login=False), _bank("0071", "우체국", name="우체국"),
    _bank("0045", "새마을금고", name="새마을금고"), _bank("0048", "신협", id_login=False, name="신협"),
    _bank("0031", "대구"), _bank("0032", "부산", id_login=False), _bank("0039", "경남"),
    _bank("0034", "광주", id_login=False), _bank("0037", "전북"), _bank("0035", "제주", id_login=False),
])

CARDS = dict([
    _card("0306", "신한카드"), _card("0301", "국민카드"), _card("0303", "삼성카드"),
    # 현대카드: 아이디 · 비밀번호에 카드번호 · 카드 비밀번호가 꼭 필요 (CF-12401, 10/27 나은 확인)
    _card("0302", "현대카드", card_fields=("card_no", "card_password")),
    _card("0311", "롯데카드"), _card("0309", "우리카드"), _card("0313", "하나카드"), _card("0304", "NH카드"),
    _card("0305", "BC카드"), _card("0307", "씨티카드"), _card("0320", "수협카드", id_login=False),
    _card("0316", "광주카드", id_login=False), _card("0315", "전북카드"), _card("0321", "제주카드", id_login=False),
])

TABLE = {BANK: BANKS, CARD: CARDS}


def find(business_type, organization_code):
    """(업권, 기관 코드) → 기관 정보 dict. 없으면 None"""
    return TABLE.get(business_type, {}).get(organization_code)


def name_of(business_type, organization_code):
    """화면에 쓸 기관 이름. 표에 없으면 기관 코드를 그대로"""
    info = find(business_type, organization_code)
    return info["name"] if info else organization_code

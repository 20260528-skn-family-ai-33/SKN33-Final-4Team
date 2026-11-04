"""서버 기준 하한. 계산용으로만 쓰고 사용자 설정은 바꾸지 않음."""

REALITY_FLOORS = {
    'TRANSPORT_CAR': {'label': '서울 청년 대중교통 정액형 기준', 'amount': 55000, 'condition': 'has_vehicle is False',
        'source': '서울 청년 대중교통 정액형(모두의카드·기후동행패스) 월 55,000원',
        'url': 'https://mediahub.seoul.go.kr/archives/2019060',
        'as_of': '2026-11-03', 'next_check': '2027-02-03', 'version': '2026-11-03'},
    'FOOD': {'label': '가계동향조사 식비 기준(미확정)', 'amount': None, 'condition': '미확정', 'source': '가계동향조사 정제 데이터',
        'url': None, 'as_of': '2026-11-03', 'next_check': '2027-02-03', 'version': 'pending'},
}


def system_floors(profile):
    """Return server reference floors separately from the user's settings."""
    if not isinstance(profile, dict):
        raise ValueError('invalid_reality_input')
    if profile.get('has_vehicle') is False:
        return {'TRANSPORT_CAR': REALITY_FLOORS['TRANSPORT_CAR']['amount']}
    return {}

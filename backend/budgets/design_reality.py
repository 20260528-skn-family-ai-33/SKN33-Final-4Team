"""서버 기준 하한. 계산용으로만 쓰고 사용자 설정은 바꾸지 않음."""

REALITY_FLOORS = {
    'TRANSPORT_CAR': {'label': '교통비 참고 기준', 'amount': 60000, 'vehicle_amount': 100000,
        'condition': '자차 여부에 따라 적용; 충분한 이력이 있으면 이력까지만 보호',
        'source': '월 40회 시내버스 환급 전 요금 및 전국 휘발유·주행거리 참고 사용자 결정',
        'url': 'https://www.opinet.co.kr/user/dopospdrg/dopOsPdrgSelect.do',
        'as_of': '2026-11-05', 'next_check': '2027-02-05', 'version': '2026-11-05'},
    'FOOD': {'label': '가계동향조사 식비 기준(미확정)', 'amount': None, 'condition': '미확정', 'source': '가계동향조사 정제 데이터',
        'url': None, 'as_of': '2026-11-03', 'next_check': '2027-02-03', 'version': 'pending'},
}


def system_floors(profile):
    """Return server reference floors separately from the user's settings."""
    if not isinstance(profile, dict):
        raise ValueError('invalid_reality_input')
    vehicle = profile.get('has_vehicle')
    if vehicle is True or vehicle is False:
        reference = REALITY_FLOORS['TRANSPORT_CAR']
        floor = reference['vehicle_amount'] if vehicle else reference['amount']
        history = profile.get('transport_history')
        if history is not None:
            if isinstance(history, bool) or history < 0:
                raise ValueError('invalid_transport_history')
            floor = min(floor, int(history / 1000 + 0.5) * 1000)
        return {'TRANSPORT_CAR': floor} if floor else {}
    return {}

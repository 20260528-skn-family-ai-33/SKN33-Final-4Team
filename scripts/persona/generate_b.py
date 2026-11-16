"""시연용 페르소나 B(박민준, 용인 수지 본가 · 역삼 자차 출근) 거래 만들기.

generate_a.py 의 Builder(거래 한 줄 넣기 · 체크카드 통장 중복 · 잔액 · 요약)를 그대로 쓰고 생활 패턴만 새로 쓴다.
merchants_b.csv 를 읽어 2026-07-01 ~ 2026-11-30 거래를 data/transactions_b.csv 로 저장한다.

    python scripts/persona/generate_b.py
"""
import calendar
from datetime import date, timedelta

from generate_a import Builder, days, is_workday, main

SEED = 20261121
# 계좌·카드: A 국민 입출금(월급) · C 적금 · H 주택청약 · D 국민 체크카드(결제 계좌 A)
OPENING = {'A': 13_000_000, 'C': 6_000_000, 'H': 2_400_000}
SALARY, SALARY_NAME, PAYDAY = 2_900_000, '(주)어리둥절', 10
NICKNAME = '박민준'  # 내 계좌끼리 이체는 양쪽 적요에 가입 이름이 찍힌다

# role, 적요(역할 가게를 쓰면 None), 금액, 날짜, 카테고리, 수단
FIXED = [
    ('parking_pass', '하이파킹 역삼센터 월정기권', 150_000, 1, 'TRANSPORT_CAR', 'D'),
    ('youtube', '유튜브프리미엄', 14_900, 3, 'CULTURE_LEISURE', 'D'),
    ('parents', '이경숙', 300_000, 11, 'ETC', 'A'),  # 부모님 생활비 (엄마에게 이체)
    ('gym', None, 59_000, 15, 'CULTURE_LEISURE', 'D'),
    ('telecom', 'KT 통신요금 자동납부', 69_000, 20, 'HOUSING_TELECOM', 'A'),
]
SAVINGS = [('saving', 'C', 500_000, '09:10'), ('housing_saving', 'H', 100_000, '09:20')]  # 11일, 월급 다음 날

# 친구 송금: (날짜, 시각, 이름, 금액, 방향, 정답 카테고리, 메모)
# 받은 1/n 은 B안 기준 달(8~10월) 밖에 둔다 (generate_a.py 설명 참고)
FRIENDS = [
    (date(2026, 7, 25), '23:30', '김나은', 24_000, 'in', '', '판교 술자리 1/n 받음 (내가 96,000원 결제)'),
    (date(2026, 7, 25), '23:41', '김혜리', 24_000, 'in', '', '판교 술자리 1/n 받음'),
    (date(2026, 7, 26), '10:05', '권세진', 24_000, 'in', '', '판교 술자리 1/n 받음'),
    (date(2026, 10, 16), '23:20', '백동현', 18_000, 'out', 'FOOD', '회식 2차 1/n 보냄'),
    (date(2026, 11, 7), '21:10', '김혜리', 12_000, 'out', 'CULTURE_LEISURE', '볼링 1/n 보냄'),
    (date(2026, 11, 13), '22:40', '권세진', 37_500, 'in', '', '팀 저녁 1/n 받음 (내가 150,000원 결제)'),
    (date(2026, 11, 13), '22:52', '백동현', 37_500, 'in', '', '팀 저녁 1/n 받음'),
]
FRIEND_PAID = [(date(2026, 7, 25), '22:40', 'pangyo_bar', 'pangyo', 96_000),
               (date(2026, 11, 13), '21:30', 'team_dinner', 'office', 150_000)]
# 확인 필요 · 페이: (날짜, 시각, 적요, 금액, 카테고리, 수단, 정답, 메모)
SPECIAL = [
    (date(2026, 7, 19), '15:40', '현대백화점 판교점', 89_000, 'UNCLASSIFIED', 'D', 'FASHION_SHOPPING',
     '확인 필요(백화점) · 셔츠'),
    (date(2026, 8, 5), '21:30', '쿠팡', 23_800, 'UNCLASSIFIED', 'D', 'TRANSPORT_CAR', '확인 필요(쿠팡) · 세차용품'),
    (date(2026, 9, 15), '22:10', '쿠팡', 41_200, 'UNCLASSIFIED', 'D', 'FASHION_SHOPPING', '확인 필요(쿠팡) · 바람막이'),
    (date(2026, 11, 4), '20:40', '쿠팡', 16_900, 'UNCLASSIFIED', 'D', 'HOUSEHOLD', '확인 필요(쿠팡) · 생필품'),
    (date(2026, 8, 29), '01:20', 'STEAM PURCHASE', 32_450, 'UNCLASSIFIED', 'D', 'CULTURE_LEISURE',
     '확인 필요(해외 결제) · 게임'),
    (date(2026, 10, 25), '23:50', 'STEAM PURCHASE', 18_730, 'UNCLASSIFIED', 'D', 'CULTURE_LEISURE',
     '확인 필요(해외 결제) · 게임 · 같은 적요 다른 금액'),
    (date(2026, 9, 30), '19:20', '네이버페이충전', 50_000, 'UNCLASSIFIED', 'A', 'FOOD',
     '확인 필요(페이 충전) · 충전 뒤 추석 선물 세트 구매'),
    (date(2026, 7, 26), '19:10', '카카오페이 - 배달의민족', 32_000, 'FOOD', 'D', '', '가족 야식 배달 (카카오페이)'),
    (date(2026, 10, 3), '14:20', '네이버페이 - 무신사', 59_000, 'FASHION_SHOPPING', 'D', '', '맨투맨 (네이버페이)'),
]
# 한 번씩: (날짜, 시각, 역할 또는 None, 적요, 금액, 카테고리, 메모)
ONE_OFF = [
    (date(2026, 8, 14), '11:20', None, 'DB손해보험 다이렉트', 962_000, 'TRANSPORT_CAR', '자동차보험 1년 갱신 (한 번만 나간 큰돈)'),
    (date(2026, 10, 17), '10:40', 'car_repair', None, 320_000, 'TRANSPORT_CAR', '엔진오일 · 타이어 2짝 교체'),
    (date(2026, 9, 16), '18:50', 'clinic', None, 35_000, 'MEDICAL_HEALTH', '치과 스케일링'),
    (date(2026, 9, 16), '19:20', 'pharmacy', None, 6_500, 'MEDICAL_HEALTH', ''),
    (date(2026, 11, 3), '19:30', 'pharmacy', None, 8_200, 'MEDICAL_HEALTH', '감기약'),
    (date(2026, 8, 2), '20:00', None, 'CGV 온라인 예매', 30_000, 'CULTURE_LEISURE', '데이트 영화'),
    (date(2026, 10, 18), '19:30', None, '메가박스 온라인 예매', 28_000, 'CULTURE_LEISURE', '데이트 영화'),
    (date(2026, 9, 26), '14:00', None, '여기어때 강릉 씨마크호텔', 280_000, 'TRAVEL_LODGING', '추석 연휴 여행 (여윳돈)'),
]


def pay_date(year, month):
    d = date(year, month, min(PAYDAY, calendar.monthrange(year, month)[1]))
    while not is_workday(d):
        d -= timedelta(days=1)
    return d


class BuilderB(Builder):
    persona, seed, opening = 'b', SEED, OPENING

    def build(self):
        rng = self.rng
        outings, turn = {}, 0
        for d in days():
            if d.weekday() == 5:
                outings[d if rng.random() < 0.5 else d + timedelta(days=1)] = ('friend', 'date')[turn % 2]
                turn += 1
        paid_days = {p[0] for p in FRIEND_PAID}
        fuel_next = date(2026, 7, 3)
        family_turn = 0

        for d in days():
            last_day = calendar.monthrange(d.year, d.month)[1]
            # ----- 월급 · 고정지출 · 저축 -----
            if d == pay_date(d.year, d.month):
                self.add(d, '09:00', 'salary', SALARY, name=SALARY_NAME, kind='income', instrument='A', direction='in')
            for role, name, amount, day, category, instrument in FIXED:
                if d.day == min(day, last_day):
                    if name is None:
                        self.add(d, '07:10', role, amount, merchant=self.pick(role, regular_share=1.0),
                                 category=category, instrument=instrument, funding='fixed')
                    else:
                        self.add(d, '10:00', role, amount, name=name, category=category, instrument=instrument,
                                 funding='fixed', kind='spending')
            if d.day == 11:
                for role, account, amount, t in SAVINGS:
                    for instrument, direction in (('A', 'out'), (account, 'in')):
                        r = self.add(d, t, role, amount, name=NICKNAME, kind='saving', instrument=instrument,
                                     direction=direction)
                        r['pair_key'] = f'{role}:{d.month}'
            # ----- 차: 주유 (8~10일마다) -----
            if d >= fuel_next:
                zone = 'home' if rng.random() < 0.6 else 'home_car'
                self.spend(d, '07:40' if is_workday(d) else '11:20', 'fuel', 50_000, 66_000, zone=zone,
                           regular=0.8, step=1_000)
                fuel_next = d + timedelta(days=rng.choice((8, 9, 10)))
            # ----- 평일 -----
            if is_workday(d):
                for t in ('07:55', '19:05'):
                    self.add(d, t, 'hipass', 1_400, name='한국도로공사 하이패스', category='TRANSPORT_CAR')
                if rng.random() < 0.65:
                    self.spend(d, f'12:{rng.randrange(5, 40):02d}', 'lunch', 9_000, 13_000, zone='office',
                               regular=0.7, step=500)
                    if rng.random() < 0.45:
                        self.spend(d, f'12:{rng.randrange(45, 59):02d}', 'cafe', 4_000, 6_500, zone='office',
                                   regular=0.8)
                if rng.random() < 0.15:
                    self.spend(d, '15:30', 'convenience', 1_500, 5_000, zone='office', regular=0.9)
                if d.weekday() == 3 and rng.random() < 0.3 and d not in paid_days:  # 목요일 회식 · 동기 저녁
                    self.spend(d, '20:10', 'team_dinner', 20_000, 32_000, zone='office', step=1_000)
            # ----- 주말 -----
            elif d.weekday() >= 5:
                if d in outings:
                    self.outing(d, outings[d], skip=d in paid_days)
                else:
                    if rng.random() < 0.4:
                        self.spend(d, '10:30', 'bakery', 6_000, 14_000, zone='home', regular=0.8)
                    if rng.random() < 0.5:
                        self.spend(d, '15:00', 'cafe', 4_500, 7_000, zone='home', regular=0.8)
                    if rng.random() < 0.5:
                        self.spend(d, '17:30', 'grocery', 6_000, 18_000, zone='home', regular=0.8)
                    if 15 <= d.day <= 21 and family_turn < (d.month - 6):  # 한 달 한 번 부모님과 외식 · 장보기 번갈아
                        if family_turn % 2 == 0:
                            self.spend(d, '18:30', 'family_meal', 45_000, 72_000, zone='home', regular=0.7,
                                       step=1_000, note='부모님과 외식 (내가 냄)')
                        else:
                            self.add(d, '16:10', 'family_mart', self.won(55_000, 95_000, 100), name='이마트 죽전점',
                                     category='FOOD', note='부모님과 장보기 (내가 냄)')
                        family_turn += 1
                    if d.weekday() == 6 and d.day in range(8, 15):
                        self.spend(d, '09:40', 'carwash', 8_000, 15_000, regular=1.0, step=1_000)
            # ----- 한 달에 몇 번 -----
            if d.day == 6 and d.month % 2 == 0 or d.day == 22 and d.month % 2 == 1:
                self.spend(d, '13:00' if d.weekday() >= 5 else '19:40', 'hair', 15_000, 22_000, zone='home',
                           regular=1.0, step=1_000)
        self.extras()
        self.balances()

    def outing(self, d, kind, skip=False):
        rng = self.rng
        if kind == 'friend':
            if not skip:
                self.spend(d, '13:00', 'pangyo_meal', 13_000, 24_000, zone='pangyo', step=500, note=kind)
            self.spend(d, '15:00', 'weekend_cafe', 5_000, 9_000, zone=rng.choice(['pangyo', 'jeongja']), note=kind)
            extra = rng.random()
            if extra < 0.3:
                self.spend(d, '17:00', 'fun', 10_000, 25_000, zone=rng.choice(['pangyo', 'jeongja']), step=1_000,
                           note=kind)
            elif extra < 0.45:
                self.spend(d, '16:30', 'fashion', 39_000, 89_000, zone='pangyo', step=1_000, note=kind)
            if not skip and rng.random() < 0.3:
                self.spend(d, '20:30', 'pangyo_bar', 18_000, 32_000, zone=rng.choice(['pangyo', 'jeongja']),
                           step=1_000, note=kind)
        else:
            if rng.random() < 0.3:
                self.spend(d, '11:30', 'brunch', 18_000, 32_000, zone='jeongja', step=500, note=kind)
            else:
                self.spend(d, '13:10', 'date_meal', 32_000, 55_000, zone='hannam', step=1_000, note=kind)
                self.add(d, '18:20', 'parking', self.won(4_000, 12_000, 1_000), name='하이파킹 한남점',
                         category='TRANSPORT_CAR', note=kind)
            if rng.random() < 0.6:  # 데이트 카페는 번갈아 냄
                self.spend(d, '15:20', 'weekend_cafe', 11_000, 19_000, zone=rng.choice(['hannam', 'jeongja']),
                           note=kind)
            extra = rng.random()
            if extra < 0.25:
                self.spend(d, '16:40', 'dessert', 7_000, 16_000, zone=rng.choice(['hannam', 'jeongja']), note=kind)
            elif extra < 0.35:
                self.spend(d, '17:00', 'gift', 20_000, 40_000, zone='hannam', step=1_000, note=kind)

    def extras(self):
        for d, t, role, zone, amount in FRIEND_PAID:
            self.spend(d, t, role, zone=zone, amount=amount, note='친구 몫까지 내가 결제 → 1/n 받음')
        for d, t, name, amount, direction, ans, note in FRIENDS:
            self.add(d, t, 'friend', amount, name=name, kind='income' if direction == 'in' else 'transfer',
                     instrument='A', direction=direction, ans=ans, note=note)
        for d, t, name, amount, category, instrument, ans, note in SPECIAL:
            role = 'pay' if '페이' in name else 'review'
            self.add(d, t, role, amount, name=name, category=category, instrument=instrument, ans=ans, note=note)
        for d, t, role, name, amount, category, note in ONE_OFF:
            if role:
                self.spend(d, t, role, regular=1.0, amount=amount, note=note)
            else:
                self.add(d, t, 'one_off', amount, name=name, category=category, note=note,
                         funding='free' if category == 'TRAVEL_LODGING' else 'living')


if __name__ == '__main__':
    main(BuilderB)

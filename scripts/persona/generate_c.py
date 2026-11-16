"""시연용 페르소나 C(이서연, 인천 부평 본가 · 가산 출근) 거래 만들기.

generate_a.py 의 Builder(거래 한 줄 넣기 · 체크카드 통장 중복 · 잔액 · 요약)를 그대로 쓰고 생활 패턴만 새로 쓴다.
9/14 (주)sejin tech 입사 때 하나은행 월급통장 · 체크카드를 새로 만들어서 거래는 9/14부터 있다.
9~10월은 수습 · 본가 생활이라 거의 안 쓴다 → 끝난 석 달(8~10월) 동안 어느 카테고리도 10건을 넘지 않아
예산 설계가 A안(sparse_transactions)으로 간다. 11월부터는 평소처럼 쓴다 (아직 안 끝난 달이라 A/B 판단에 안 들어감).

    python scripts/persona/generate_c.py
"""
from datetime import date, timedelta

from generate_a import Builder, is_workday, main, pay_date

SEED = 20261122
START, END = date(2026, 9, 14), date(2026, 11, 30)
NORMAL_FROM = date(2026, 11, 1)  # 이날부터 평소 소비
# 계좌·카드: A 하나 월급통장(9/14 개설) · C 청년도약계좌(10/26 개설) · D 하나 체크카드(결제 계좌 A)
OPENING = {'A': 0, 'C': 0}
NICKNAME = '이서연'
SALARY, SALARY_NAME = 2_500_000, '(주)sejin tech'
FIRST_SALARY = 1_350_000  # 9월은 9/14 입사라 일한 날만큼 (25일이 추석이라 9/23에 들어옴)
TRANSIT_FARE = 1_950  # 부평구청 → 가산디지털단지 (7호선) 편도

# role, 적요, 금액, 날짜, 카테고리, 수단, 첫 달
FIXED = [
    ('telecom', 'LG유플러스 자동납부', 52_000, 22, 'HOUSING_TELECOM', 'A', 9),  # 통장 만들고 바로 자동납부 바꿈
    ('ott', '티빙', 13_900, 5, 'CULTURE_LEISURE', 'D', 10),
    ('parents', '최미경', 200_000, 26, 'ETC', 'A', 10),  # 부모님 생활비 (엄마에게 이체), 첫 월급 다 받고부터
]
SAVING = ('saving', 'C', 300_000, 26, 10)  # 청년도약계좌, 10월 첫 정식 월급부터

# 9~10월 (수습 · 본가라 거의 안 씀): (날짜, 시각, 역할, 구역, 최소, 최대, 메모)
EARLY = [
    (date(2026, 9, 19), '13:00', 'weekend_meal', 'bupyeong', 15_000, 20_000, '동네 친구'),
    (date(2026, 9, 19), '14:40', 'weekend_cafe', 'bupyeong', 5_000, 7_000, '동네 친구'),
    (date(2026, 9, 20), '15:00', 'books', 'office', 16_000, 22_000, '업무 공부 책'),
    (date(2026, 9, 26), '15:00', 'fashion', 'bupyeong', 35_000, 49_000, '출근룩 (부평 지하상가)'),
    (date(2026, 9, 27), '17:00', 'cosmetics', 'home', 12_000, 20_000, ''),
    (date(2026, 9, 30), '12:50', 'cafe', 'office', 4_000, 5_000, '입사 동기랑 커피'),
    (date(2026, 10, 3), '12:30', 'weekend_meal', 'hongdae', 16_000, 24_000, '친구'),
    (date(2026, 10, 3), '14:30', 'weekend_cafe', 'hongdae', 6_000, 8_000, '친구'),
    (date(2026, 10, 3), '16:30', 'fun', 'hongdae', 6_000, 10_000, '노래방'),
    (date(2026, 10, 11), '14:00', 'hair', 'home', 30_000, 35_000, ''),
    (date(2026, 10, 15), '12:20', 'lunch', 'office', 9_000, 11_000, '입사 동기랑 점심'),
    (date(2026, 10, 23), '19:30', 'delivery', 'home', 24_000, 28_000, '첫 정식 월급날 가족 치킨'),
    (date(2026, 10, 24), '13:00', 'weekend_meal', 'songdo', 20_000, 28_000, '친구'),
    (date(2026, 10, 24), '15:30', 'weekend_cafe', 'songdo', 6_000, 8_000, '친구'),
]
# 도수치료: 출근하고 거북목 · 허리가 아파서 10/7 처음 진료, 이후 토요일 오전마다 (비급여, 한 번 9만 원)
FIRST_VISIT = (date(2026, 10, 7), 38_500)  # 진찰 + 엑스레이
DOSU_FROM, DOSU_PRICE = date(2026, 10, 10), 90_000
# 실손보험 청구 → 2주마다 보험금 입금 (비급여 자기부담 30%, 보험료는 부모님이 냄): 입금일, 그 전까지 받은 진료
CLAIM_DAYS = [date(2026, 10, 21), date(2026, 11, 4), date(2026, 11, 18), date(2026, 12, 2)]

# 친구 송금: (날짜, 시각, 이름, 금액, 방향, 정답 카테고리, 메모)
FRIENDS = [
    (date(2026, 11, 7), '23:10', '권세진', 20_000, 'in', '', '부평 술자리 1/n 받음 (내가 60,000원 결제)'),
    (date(2026, 11, 7), '23:25', '백동현', 20_000, 'in', '', '부평 술자리 1/n 받음'),
    (date(2026, 11, 19), '21:00', '김혜리', 15_000, 'out', 'ETC', '친구 생일 선물 모아서 보냄'),
]
FRIEND_PAID = [(date(2026, 11, 7), '21:40', 'bar', 'bupyeong', 60_000)]
# 확인 필요 · 페이: (날짜, 시각, 적요, 금액, 카테고리, 수단, 정답, 메모)
SPECIAL = [
    (date(2026, 9, 16), '21:40', '쿠팡', 23_800, 'UNCLASSIFIED', 'D', 'HOUSEHOLD', '확인 필요(쿠팡) · 출근용 텀블러 · 슬리퍼'),
    (date(2026, 10, 12), '20:10', '네이버페이충전', 30_000, 'UNCLASSIFIED', 'A', 'FASHION_SHOPPING',
     '확인 필요(페이 충전) · 충전 뒤 가디건 구매'),
    (date(2026, 11, 6), '22:30', '쿠팡', 32_900, 'UNCLASSIFIED', 'D', 'FASHION_SHOPPING', '확인 필요(쿠팡) · 니트 · 같은 적요 다른 금액'),
    (date(2026, 11, 10), '21:20', '카카오페이 - 무신사', 45_900, 'FASHION_SHOPPING', 'D', '', '코트 (카카오페이)'),
]


def days():
    d = START
    while d <= END:
        yield d
        d += timedelta(days=1)


class BuilderC(Builder):
    persona, seed, opening = 'c', SEED, OPENING
    STATEMENT_STYLES = [
        (lambda r: r['role'] == 'delivery', 1.0, '쿠팡이츠 - 쿠팡', 'PG쇼핑몰', False),
        (lambda r: r['merchant_name'] == '쿠팡', 1.0, ['쿠팡_쿠페이 - 쿠팡', 'KCP-쿠팡'], 'PG쇼핑몰', False),
    ]

    def build(self):
        rng = self.rng
        paid_days = {p[0] for p in FRIEND_PAID}
        transit = {}  # 달별 후불교통 이용액 → 다음 달 2일쯤 카드로 한 번에 나간다
        sessions = []  # (날짜, 금액) 실손 청구 대상
        early = {}
        for row in EARLY:
            early.setdefault(row[0], []).append(row)

        self.add(START, '11:00', 'own_deposit', 800_000, name=NICKNAME, kind='income', instrument='A',
                 direction='in', note='예전 통장에서 모아 둔 돈 옮김')
        for d in days():
            month_key = (d.year, d.month)
            # ----- 월급 · 고정지출 · 저축 -----
            if d == pay_date(d.year, d.month):
                self.add(d, '09:00', 'salary', FIRST_SALARY if d.month == 9 else SALARY, name=SALARY_NAME,
                         kind='income', instrument='A', direction='in')
            for role, name, amount, day, category, instrument, first_month in FIXED:
                if d.day == day and d.month >= first_month:
                    self.add(d, '10:00', role, amount, name=name, category=category, instrument=instrument,
                             funding='fixed')
            if d.day == SAVING[3] and d.month >= SAVING[4]:
                for instrument, direction in (('A', 'out'), (SAVING[1], 'in')):
                    r = self.add(d, '09:10', SAVING[0], SAVING[2], name=NICKNAME, kind='saving',
                                 instrument=instrument, direction=direction)
                    r['pair_key'] = f'saving:{d.month}'
            # ----- 교통: 지난달 후불교통 이용액이 2일(주말이면 다음 평일)에 카드로 나감 -----
            if d.day <= 5 and is_workday(d):
                prev = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
                if transit.get(prev):
                    self.add(d, '06:10', 'transit', transit.pop(prev), name='후불교통대금', category='TRANSPORT_CAR',
                             note='지난달 지하철 · 버스')
            if is_workday(d):
                transit[month_key] = transit.get(month_key, 0) + TRANSIT_FARE * 2
            # ----- 도수치료 · 보험금 -----
            if d == FIRST_VISIT[0]:
                self.spend(d, '19:20', 'dosu', amount=FIRST_VISIT[1], regular=1.0, zone='home_wide', note='첫 진료 (진찰 + 엑스레이)')
                self.spend(d, '19:50', 'pharmacy', 4_000, 6_000, zone='home', regular=1.0, note='소염진통제')
                sessions.append((d, FIRST_VISIT[1]))
            if d >= DOSU_FROM and d.weekday() == 5:
                self.spend(d, '10:00', 'dosu', amount=DOSU_PRICE, regular=1.0, zone='home_wide', note='도수치료')
                sessions.append((d, DOSU_PRICE))
            if d in CLAIM_DAYS:
                claimed = [s for s in sessions if s[0] < d]
                if claimed:
                    payout = sum(a for _, a in claimed) * 7 // 10 // 10 * 10
                    self.add(d, '14:00', 'insurance_claim', payout, name='삼성화재 보험금', kind='income',
                             instrument='A', direction='in', note=f'실손보험 도수치료 {len(claimed)}회분')
                    sessions = [s for s in sessions if s[0] >= d]
            # ----- 9~10월: 정해 둔 몇 번만 -----
            if d < NORMAL_FROM:
                for _, t, role, zone, low, high, note in early.get(d, []):
                    self.spend(d, t, role, low, high, zone=zone, regular=0.8, note=note)
                    if zone in ('hongdae', 'songdo'):
                        transit[month_key] += 3_000  # 주말 나들이 교통
                continue
            # ----- 11월부터 평소 -----
            if is_workday(d):
                if rng.random() < 0.25:  # 구내식당이 있어서 밖에서 먹는 날은 가끔
                    self.spend(d, f'12:{rng.randrange(5, 40):02d}', 'lunch', 8_500, 12_000, zone='office',
                               regular=0.7, step=500)
                if rng.random() < 0.45:
                    self.spend(d, f'12:{rng.randrange(45, 59):02d}', 'cafe', 2_000, 5_000, zone='office',
                               regular=0.8)
                if rng.random() < 0.2:
                    self.spend(d, '15:40', 'convenience', 1_500, 5_000, zone='office', regular=0.9)
                if rng.random() < 0.12:
                    self.spend(d, f'21:{rng.randrange(0, 50):02d}', 'convenience', 2_500, 9_000, zone='home',
                               regular=0.9)
                if d.weekday() == 4 and rng.random() < 0.3:
                    self.spend(d, '20:30', 'delivery', 22_000, 30_000, zone='home', regular=0.6, step=500,
                               note='가족이랑 야식')
            elif d.weekday() == 5 and d not in paid_days:
                self.outing(d, rng.choice(['bupyeong', 'hongdae', 'yeongdeungpo', 'songdo']))
                transit[month_key] += 3_000
            elif d.weekday() == 6:
                if rng.random() < 0.4:
                    self.spend(d, '11:00', 'bakery', 4_000, 9_000, zone='home', regular=0.8)
                if rng.random() < 0.35:
                    self.spend(d, '15:00', 'cafe', 4_500, 7_000, zone='home', regular=0.8)
            if d == date(2026, 11, 20):
                self.spend(d, '19:40', 'cosmetics', 18_000, 32_000, zone='home', regular=0.8)
            if d == date(2026, 11, 12):
                self.spend(d, '20:10', 'pharmacy', 4_000, 8_000, zone='home', regular=1.0, note='파스')
        self.extras()
        self.restyle()
        self.balances()

    def outing(self, d, zone):
        rng = self.rng
        self.spend(d, '13:00', 'weekend_meal', 15_000, 26_000, zone=zone, step=500, note='친구')
        self.spend(d, '15:00', 'weekend_cafe', 5_500, 9_000, zone=zone, note='친구')
        extra = rng.random()
        if extra < 0.3 and zone in ('bupyeong', 'hongdae'):
            self.spend(d, '16:30', rng.choice(['dessert', 'fun']), 5_000, 12_000, zone=zone, step=1_000, note='친구')
        elif extra < 0.55 and zone != 'songdo':
            self.spend(d, '16:40', 'fashion', 25_000, 55_000, zone=zone if zone != 'songdo' else 'bupyeong',
                       step=1_000, note='친구')

    def extras(self):
        for d, t, role, zone, amount in FRIEND_PAID:
            self.spend(d, t, role, zone=zone, amount=amount, note='친구 몫까지 내가 결제 → 1/n 받음')
        for d, t, name, amount, direction, ans, note in FRIENDS:
            self.add(d, t, 'friend', amount, name=name, kind='income' if direction == 'in' else 'transfer',
                     instrument='A', direction=direction, ans=ans, note=note)
        for d, t, name, amount, category, instrument, ans, note in SPECIAL:
            role = 'pay' if '페이' in name else 'review'
            self.add(d, t, role, amount, name=name, category=category, instrument=instrument, ans=ans, note=note)


if __name__ == '__main__':
    main(BuilderC)

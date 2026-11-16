"""시연용 페르소나 A(김지우, 신림 자취 · 강남 출근) 거래 만들기 (3단계).

merchants_a.csv(가게) · special_a.csv(확인 필요 거래)를 읽어 2026-07-01 ~ 2026-11-30 거래를 만든다.
DB에 넣을 때는 시연 기준일까지만 넣는다(시드 명령이 거른다). seed 가 고정이라 다시 돌려도 같다.
열은 기존 알파 거래 CSV(local_workspace/data/alpha_test/transactions.csv)를 따르고 note 칸만 더했다.

    python scripts/persona/generate_a.py            # data/transactions_a.csv 저장 + 달별 요약
"""
import calendar
import csv
import random
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

DATA = Path(__file__).with_name('data')
SEED = 20261120
START, END = date(2026, 7, 1), date(2026, 11, 30)
# 2026 평일 공휴일 (8/17 광복절 대체, 9/24~25 추석, 10/5 개천절 대체, 10/9 한글날)
HOLIDAYS = {date(2026, 8, 17), date(2026, 9, 24), date(2026, 9, 25), date(2026, 10, 5), date(2026, 10, 9)}

# 계좌·카드: A 월급통장(입출금) · C 적금 · D 체크카드(결제 계좌 A)
OPENING = {'A': 1_650_000, 'C': 1_200_000}
SALARY = 2_600_000
SALARY_NAME = '(주)와와컴퍼니'
PAYDAY = 25  # 주말·공휴일이면 그 전 평일에 들어온다
# role, 적요, 금액, 날짜, 거래 종류, 카테고리, 수단
# 월세·관리비는 실제 은행 내역처럼 받는 사람 이름만 찍힌다 (집주인 · 원룸 관리 사무소)
FIXED = [
    ('rent', '박정숙', 500_000, 1, 'spending', 'HOUSING_TELECOM', 'A'),
    ('ott', '넷플릭스', 17_000, 5, 'spending', 'CULTURE_LEISURE', 'D'),
    ('maintenance', '신림해오름원룸', 70_000, 10, 'spending', 'HOUSING_TELECOM', 'A'),
    ('telecom', 'SK텔레콤 자동납부', 55_000, 15, 'spending', 'HOUSING_TELECOM', 'A'),
    ('student_loan', '한국장학재단 학자금상환', 150_000, 27, 'loan_repayment', '', 'A'),
]
SAVING = ('saving', '청년도약 적금 자동이체', 300_000, 26)

# 친구 송금: (날짜, 시각, 이름, 금액, 방향, 정답 카테고리, 메모)
# 받은 돈은 income, 보낸 돈은 transfer (처음엔 소비 아님, 사용자가 카테고리를 고르면 소비)
FRIENDS = [
    (date(2026, 8, 8), '23:40', '김혜리', 24_000, 'in', '', '연남 술자리 1/n 받음 (내가 72,000원 결제)'),
    (date(2026, 8, 8), '23:52', '권세진', 24_000, 'in', '', '연남 술자리 1/n 받음'),
    (date(2026, 9, 12), '21:15', '김나은', 18_500, 'out', 'FOOD', '친구가 낸 저녁 1/n 보냄'),
    (date(2026, 10, 2), '12:30', '백동현', 20_000, 'out', 'ETC', '친구 생일 선물 모아서 보냄'),
    (date(2026, 10, 24), '23:10', '권세진', 9_000, 'out', 'CULTURE_LEISURE', '노래방 1/n 보냄'),
    (date(2026, 11, 14), '22:05', '김나은', 30_000, 'in', '', '생일 저녁 1/n 받음 (내가 120,000원 결제)'),
    (date(2026, 11, 14), '22:11', '김혜리', 30_000, 'in', '', '생일 저녁 1/n 받음'),
    (date(2026, 11, 15), '10:20', '백동현', 30_000, 'in', '', '생일 저녁 1/n 받음'),
]
# 친구 송금과 짝인 내 결제 (체크카드): (날짜, 시각, 역할, 구역, 금액)
FRIEND_PAID = [
    (date(2026, 8, 8), '22:50', 'bar', 'yeonnam', 72_000),
    (date(2026, 11, 14), '20:40', 'weekend_meal', 'yeonnam', 120_000),
]
# 카카오페이·네이버페이: (날짜, 시각, 적요, 금액, 카테고리, 수단, 정답, 메모)
PAY = [
    (date(2026, 7, 22), '21:30', '카카오페이 - 배달의민족', 21_500, 'FOOD', 'D', '', '배달 (카카오페이 결제)'),
    (date(2026, 9, 18), '22:40', '네이버페이 - 무신사', 46_900, 'FASHION_SHOPPING', 'D', '', '바지 (네이버페이 결제)'),
    (date(2026, 10, 13), '20:10', '카카오페이 - 배달의민족', 24_000, 'FOOD', 'D', '', '배달 (카카오페이 결제)'),
    (date(2026, 11, 9), '21:50', '네이버페이 - 무신사', 39_800, 'FASHION_SHOPPING', 'D', '', '니트 (네이버페이 결제)'),
    (date(2026, 8, 29), '19:05', '카카오페이충전', 30_000, 'UNCLASSIFIED', 'A', 'CULTURE_LEISURE',
     '확인 필요(페이 충전) · 충전 뒤 친구에게 공연 표값 보냄'),
    (date(2026, 10, 30), '18:20', '카카오페이충전', 20_000, 'UNCLASSIFIED', 'A', 'FOOD',
     '확인 필요(페이 충전) · 충전 뒤 회식 2차 1/n 보냄'),
]
# 한 번씩: (날짜, 시각, 역할 또는 None, 적요(역할 없을 때), 금액, 카테고리)
ONE_OFF = [
    (date(2026, 7, 11), '14:30', 'hair', None, 38_000, 'BEAUTY'),
    (date(2026, 9, 5), '14:30', 'hair', None, 35_000, 'BEAUTY'),
    (date(2026, 11, 7), '14:30', 'hair', None, 42_000, 'BEAUTY'),
    (date(2026, 8, 19), '19:10', 'clinic', None, 6_800, 'MEDICAL_HEALTH'),
    (date(2026, 8, 19), '19:30', 'pharmacy', None, 4_500, 'MEDICAL_HEALTH'),
    (date(2026, 11, 12), '19:05', 'clinic', None, 7_200, 'MEDICAL_HEALTH'),
    (date(2026, 11, 12), '19:25', 'pharmacy', None, 5_800, 'MEDICAL_HEALTH'),
    (date(2026, 7, 26), '15:00', 'books', None, 16_800, 'EDUCATION'),
    (date(2026, 9, 27), '15:00', 'books', None, 14_400, 'EDUCATION'),
    (date(2026, 8, 1), '19:50', None, 'CGV 온라인 예매', 28_000, 'CULTURE_LEISURE'),
    (date(2026, 9, 19), '19:50', None, 'CGV 온라인 예매', 14_000, 'CULTURE_LEISURE'),
    (date(2026, 10, 31), '19:50', None, '롯데시네마 온라인 예매', 30_000, 'CULTURE_LEISURE'),
    (date(2026, 8, 14), '23:20', None, '카카오_법인택시6', 16_400, 'TRANSPORT_CAR'),
    (date(2026, 9, 26), '00:40', None, '카카오_개인택시', 13_800, 'TRANSPORT_CAR'),
    (date(2026, 10, 24), '23:50', None, '카카오_법인택시6', 15_200, 'TRANSPORT_CAR'),
    (date(2026, 7, 18), '16:00', None, '여기어때 강릉 오션뷰호텔', 150_000, 'TRAVEL_LODGING'),
]

COLUMNS = ['source_key', 'occurred_on', 'occurred_time', 'weekday', 'role', 'merchant_name', 'amount', 'direction',
           'transaction_type', 'confirmed_category', 'funding_bucket', 'instrument', 'source_kind', 'pair_key',
           'is_duplicate', 'ans_category_if_user_set', 'is_fixed', 'fixed_role', 'metadata_source',
           'source_description', 'store_id', 'branch_name', 'industry_small', 'sigungu', 'dong', 'balance_after', 'note']


def days():
    d = START
    while d <= END:
        yield d
        d += timedelta(days=1)


def is_workday(d):
    return d.weekday() < 5 and d not in HOLIDAYS


def pay_date(year, month):
    d = date(year, month, min(PAYDAY, calendar.monthrange(year, month)[1]))
    while not is_workday(d):
        d -= timedelta(days=1)
    return d


class Builder:
    """페르소나 공통 거래 만들기 도구. 다른 페르소나는 persona · seed · opening 을 바꾸고 build 를 새로 쓴다"""
    persona, seed, opening = 'a', SEED, OPENING

    def __init__(self):
        self.rng = random.Random(self.seed)
        with (DATA / f'merchants_{self.persona}.csv').open(encoding='utf-8-sig', newline='') as f:
            self.merchants = list(csv.DictReader(f))
        self.rows = []

    def won(self, low, high, step=100):
        return self.rng.randrange(low // step, high // step + 1) * step

    def pick(self, role, zone=None, regular_share=0.0):
        cands = [m for m in self.merchants if m['role'] == role and (zone is None or m['zone'] == zone)]
        regulars = [m for m in cands if m['regular'] == 'Y']
        if regulars and self.rng.random() < regular_share:
            return self.rng.choice(regulars)
        return self.rng.choice([m for m in cands if m['regular'] == 'N'] or cands)

    card_instruments = ('D',)  # 카드 결제 수단 (나머지는 계좌)
    card_account = 'A'         # 체크카드 결제가 함께 찍히는 계좌

    def add(self, d, t, role, amount, *, merchant=None, name='', kind='spending', category='', instrument='D',
            direction='out', funding='living', ans='', note='', duplicate_of=''):
        key = f'persona-{self.persona}:{len(self.rows) + 1:04d}'
        m = merchant or {}
        merchant_name = (m['merchant_name'] + (' ' + m['branch_name'] if m.get('branch_name') else '')) if m else name
        row = dict(source_key=key, occurred_on=d.isoformat(), occurred_time=t, weekday=d.weekday(), role=role,
                   merchant_name=merchant_name, amount=amount, direction=direction, transaction_type=kind,
                   confirmed_category=category, funding_bucket=funding if kind == 'spending' else '',
                   instrument=instrument, source_kind='card' if instrument in self.card_instruments else 'bank',
                   pair_key=duplicate_of, is_duplicate=int(bool(duplicate_of)), ans_category_if_user_set=ans,
                   is_fixed=int(kind == 'spending' and funding == 'fixed'),
                   fixed_role=role if kind == 'spending' and funding == 'fixed' else '',
                   metadata_source='store_snapshot' if m else 'non_store', source_description=merchant_name,
                   store_id=m.get('store_id', ''), branch_name=m.get('branch_name', ''),
                   industry_small=m.get('industry_small', ''), sigungu=m.get('sigungu', ''), dong=m.get('dong', ''),
                   balance_after='', note=note)
        self.rows.append(row)
        if instrument in self.card_instruments:
            # 체크카드 결제는 월급통장 출금으로도 한 번 더 찍힌다 (같은 구매라 소비는 한 번만)
            row['pair_key'] = key
            self.add(d, t, role, amount, merchant=merchant, name=name, kind=kind, category=category,
                     instrument=self.card_account, direction=direction, funding=funding, ans=ans, note=note,
                     duplicate_of=key)
        return row

    def spend(self, d, t, role, low=0, high=0, *, zone=None, regular=0.0, amount=None, step=100, note=''):
        m = self.pick(role, zone, regular)
        return self.add(d, t, role, amount or self.won(low, high, step), merchant=m,
                        category=m['category_code'], note=note)

    def build(self):
        rng = self.rng
        # 주말마다 토·일 중 하루 나들이, 친구·데이트 번갈아
        outings, turn = {}, 0
        for d in days():
            if d.weekday() == 5:
                outings[d if rng.random() < 0.5 else d + timedelta(days=1)] = ('friend', 'date')[turn % 2]
                turn += 1
        paid_days = {p[0] for p in FRIEND_PAID}
        tmoney_next = START + timedelta(days=1)

        for d in days():
            last_day = calendar.monthrange(d.year, d.month)[1]
            # ----- 월급 · 고정지출 · 저축 -----
            if d == pay_date(d.year, d.month):
                self.add(d, '09:00', 'salary', SALARY, name=SALARY_NAME, kind='income', instrument='A',
                         direction='in')
            for role, name, amount, day, kind, category, instrument in FIXED:
                if d.day == min(day, last_day):
                    self.add(d, '10:00', role, amount, name=name, kind=kind, category=category,
                             instrument=instrument, funding='fixed' if kind == 'spending' else '')
            if d.day == SAVING[3]:
                for instrument, direction in (('A', 'out'), ('C', 'in')):
                    r = self.add(d, '09:10', SAVING[0], SAVING[2], name=SAVING[1], kind='saving',
                                 instrument=instrument, direction=direction)
                    r['pair_key'] = f'saving:{d.month}'
            # ----- 교통: 모바일티머니 충전 (2만 원 = 약 1주) -----
            if d >= tmoney_next:
                amount = rng.choice((20_000, 20_000, 20_000, 10_000, 30_000))
                self.add(d, '08:05', 'tmoney', amount, name='모바일티머니선불형', category='TRANSPORT_CAR')
                tmoney_next = d + timedelta(days=max(3, round(amount / 20_000 * 7) + rng.choice((-1, 0, 1))))
            # 11/1~18은 배달을 늘려서 시연 무렵 식비 예산을 넘기게 한다
            heavy = date(2026, 11, 1) <= d <= date(2026, 11, 18)
            # ----- 평일 -----
            if is_workday(d):
                if rng.random() < 0.68:
                    self.spend(d, f'12:{rng.randrange(5, 40):02d}', 'lunch', 8_000, 12_000, zone='office',
                               regular=0.7, step=500)
                    if rng.random() < 0.5:
                        self.spend(d, f'12:{rng.randrange(45, 59):02d}', 'cafe', 2_500, 5_500, zone='office',
                                   regular=0.8)
                if rng.random() < 0.2:
                    self.spend(d, '15:20', 'convenience', 1_500, 5_000, zone='office', regular=0.9)
                evening = rng.random()
                if evening < (0.3 if heavy else 0.12):
                    self.spend(d, f'{rng.choice((19, 20, 21))}:{rng.randrange(0, 59):02d}', 'delivery',
                               17_000, 27_000, zone='home', regular=0.5, step=500)
                elif evening < (0.42 if heavy else 0.27):
                    self.spend(d, f'19:{rng.randrange(10, 50):02d}', 'home_meal', 8_000, 12_000, zone='home',
                               regular=0.6, step=500)
                if rng.random() < 0.15:
                    self.spend(d, f'22:{rng.randrange(0, 50):02d}', 'convenience', 3_000, 11_000, zone='home',
                               regular=0.9)
            # ----- 주말 -----
            elif d.weekday() >= 5:
                if d in outings:
                    self.outing(d, outings[d], skip_meal=d in paid_days)
                else:
                    if d.weekday() == 6:
                        self.spend(d, '16:30', 'grocery', 15_000, 35_000, zone='home', regular=0.8)
                    if rng.random() < (0.6 if heavy else 0.3):
                        self.spend(d, '19:30', 'delivery', 18_000, 28_000, zone='home', regular=0.5, step=500)
                    if rng.random() < 0.35:
                        self.spend(d, '11:30', 'bakery', 4_000, 9_000, zone='home', regular=0.7)
            # ----- 한 달에 몇 번 -----
            if d.weekday() == 6 and d.day <= 14 and d.day > 7 or d.weekday() == 6 and 21 < d.day <= 28:
                self.spend(d, '10:30', 'laundry', 6_000, 12_000, zone='home', regular=1.0, step=1_000)
            if d.day == 13:
                self.spend(d, '20:30', 'household', 8_000, 25_000, zone='home')
            if d.day == 23:
                self.spend(d, '18:40', 'cosmetics', 12_000, 30_000, regular=0.5)
        self.extras()
        self.balances()

    def outing(self, d, kind, skip_meal=False):
        rng = self.rng
        zone = rng.choice(['seongsu', 'yeonnam', 'jamsil', 'yeouido'])
        meal = (13_000, 22_000) if kind == 'friend' else (24_000, 42_000)  # 데이트는 둘 몫을 낼 때가 많음
        if not skip_meal:
            self.spend(d, '13:10', 'weekend_meal', *meal, zone=zone, step=500, note=kind)
        if kind == 'friend' or rng.random() < 0.5:  # 데이트 카페는 번갈아 냄 (상대가 내면 거래 없음)
            self.spend(d, '15:00', 'weekend_cafe', 5_500, 13_000, zone=zone, note=kind)
        extra = rng.random()
        if extra < 0.25:
            self.spend(d, '16:30', 'dessert', 4_500, 12_000, zone=zone, note=kind)
        elif extra < 0.4:
            self.spend(d, '16:40', 'fashion', 25_000, 60_000, zone=zone, step=1_000, note=kind)
        elif extra < 0.5 and kind == 'date':
            self.spend(d, '17:00', 'gift', 15_000, 30_000, zone=zone, step=1_000, note=kind)
        elif extra < 0.6:
            self.spend(d, '18:00', 'fun', 6_000, 20_000, zone=zone if zone != 'yeouido' else 'yeonnam',
                       step=1_000, note=kind)

    def extras(self):
        for d, t, role, zone, amount in FRIEND_PAID:
            self.spend(d, t, role, zone=zone, amount=amount, note='친구들 몫까지 내가 결제 → 1/n 받음')
        for d, t, name, amount, direction, ans, note in FRIENDS:
            self.add(d, t, 'friend', amount, name=name, kind='income' if direction == 'in' else 'transfer',
                     instrument='A', direction=direction, ans=ans, note=note)
        for d, t, name, amount, category, instrument, ans, note in PAY:
            self.add(d, t, 'pay', amount, name=name, category=category, instrument=instrument, ans=ans, note=note)
        for d, t, role, name, amount, category in ONE_OFF:
            if role:
                self.spend(d, t, role, zone=None if role == 'books' else 'home', regular=1.0, amount=amount)
            else:
                self.add(d, t, 'one_off', amount, name=name, category=category,
                         funding='free' if category == 'TRAVEL_LODGING' else 'living')
        with (DATA / 'special_a.csv').open(encoding='utf-8-sig', newline='') as f:
            for s in csv.DictReader(f):
                self.add(date.fromisoformat(s['date']), s['time'], 'review_' + s['key'], int(s['amount']),
                         name=s['merchant_name'], category='UNCLASSIFIED', ans=s['true_category'],
                         note=f"{s['expected_reason']} · {s['note']}")

    def balances(self):
        self.rows.sort(key=lambda r: (r['occurred_on'], r['occurred_time'], r['source_key']))
        bal = dict(self.opening)
        for r in self.rows:
            if r['source_kind'] == 'bank':
                bal[r['instrument']] += r['amount'] if r['direction'] == 'in' else -r['amount']
                r['balance_after'] = bal[r['instrument']]
                assert r['balance_after'] >= 0, (r['occurred_on'], r['instrument'], r['balance_after'])
        self.final = bal


def summary(rows, final):
    spending = [r for r in rows if r['transaction_type'] == 'spending' and not r['is_duplicate']]
    months = sorted({r['occurred_on'][:7] for r in rows})
    print('달별 건수: 전체 행 / 통장 중복 뺀 거래 / 그중 소비')
    for m in months:
        in_month = [r for r in rows if r['occurred_on'][:7] == m]
        uniq = [r for r in in_month if not r['is_duplicate']]
        print(f"  {m}: {len(in_month)} / {len(uniq)} / {sum(r['transaction_type'] == 'spending' for r in uniq)}")
    print('달별 카테고리 소비 (고정지출 제외, *는 확인 필요 거래의 정답)')
    table = defaultdict(Counter)
    for r in spending:
        if r['funding_bucket'] == 'fixed':
            continue
        cat = r['confirmed_category']
        if cat == 'UNCLASSIFIED':
            cat = r['ans_category_if_user_set'] + '*'
        table[cat][r['occurred_on'][:7]] += r['amount']
    print(f"  {'':18}", ' '.join(f'{m[5:]:>8}' for m in months))
    for cat in sorted(table):
        print(f'  {cat:18}', ' '.join(f'{table[cat][m]:>8,}' for m in months))
    totals = Counter()
    for cat in table:
        for m in months:
            totals[m] += table[cat][m]
    print(f"  {'합계':16}", ' '.join(f'{totals[m]:>8,}' for m in months))
    counts = Counter(r['confirmed_category'] for r in spending if '2026-08' <= r['occurred_on'][:7] <= '2026-10')
    print('8~10월 카테고리별 소비 건수:', dict(counts.most_common()))
    print('11/30 잔액:', final)


def main(builder=Builder):
    b = builder()
    b.build()
    out = DATA / f'transactions_{b.persona}.csv'
    with out.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator='\n')
        w.writeheader()
        w.writerows(b.rows)
    print(f'{out} 저장: {len(b.rows)}행')
    summary(b.rows, b.final)


if __name__ == '__main__':
    main()

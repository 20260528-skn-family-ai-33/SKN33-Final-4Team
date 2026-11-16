"""시연용 페르소나 가게 풀 뽑기 (1단계).

상가정보 라벨 파일(stores_all_categories.csv, 약 2GB)에서 페르소나 생활 구역 반경 안 가게만 잘라낸다.
원본은 Git에 올리지 않고, 결과 CSV(공개 상가정보 발췌)만 scripts/persona/data/ 에 둔다.

    python scripts/persona/extract_merchants.py <상가정보 CSV 경로> --persona a

원본이 커서 서울 줄만 먼저 잘라 두면 훨씬 빠르다 (2GB → 약 430MB, 20초):

    head -1 stores_all_categories.csv > seoul.csv
    LC_ALL=C grep -F ',서울특별시,' stores_all_categories.csv >> seoul.csv

경기도가 들어가는 페르소나(B)는 서울 · 경기 줄을 같이 자른다:

    LC_ALL=C grep -E ',(서울특별시|경기도),' stores_all_categories.csv >> seoul_gg.csv

인천 본가 페르소나(C)는 서울 · 인천 줄:

    LC_ALL=C grep -E ',(서울특별시|인천광역시),' stores_all_categories.csv >> seoul_ic.csv
"""
import argparse
import csv
import math
from pathlib import Path

# 페르소나별 생활 구역: 이름, 위도, 경도, 반경(m), 용도
ZONES = {
    'a': [
        ('home', '신림역', 37.4842, 126.9297, 500, '집 근처'),
        ('office', '강남역', 37.4979, 127.0276, 300, '회사 근처'),
        ('seongsu', '성수', 37.5445, 127.0560, 500, '주말'),
        ('yeonnam', '연남', 37.5600, 126.9235, 500, '주말'),
        ('jamsil', '잠실', 37.5133, 127.1001, 500, '주말'),
        ('yeouido', '여의도', 37.5250, 126.9270, 500, '주말'),
    ],
    'b': [
        ('home', '수지구청역', 37.3222, 127.0950, 600, '집 근처'),
        ('home_car', '수지 차량권', 37.3222, 127.0950, 2500, '주유 · 세차 · 정비 (차로 가는 곳)'),
        ('office', '역삼역', 37.5006, 127.0364, 300, '회사 근처'),
        ('pangyo', '판교', 37.3925, 127.1120, 500, '주말 친구'),
        ('jeongja', '정자', 37.3664, 127.1063, 400, '주말 카페'),
        ('hannam', '한남·이태원', 37.5345, 126.9946, 500, '주말 데이트'),
    ],
    'c': [
        ('home', '부평구청역', 37.5084, 126.7214, 500, '집 근처'),
        ('home_wide', '부평구청역 넓게', 37.5084, 126.7214, 1200, '병원 (도수치료)'),
        ('office', '가산디지털단지역', 37.4815, 126.8827, 400, '회사 근처'),
        ('bupyeong', '부평역', 37.4899, 126.7234, 500, '주말 동네 친구'),
        ('songdo', '송도 센트럴파크', 37.3925, 126.6390, 600, '주말'),
        ('yeongdeungpo', '영등포', 37.5172, 126.9035, 400, '주말 서울'),
        ('hongdae', '홍대입구', 37.5571, 126.9245, 500, '주말 서울'),
    ],
}

CATEGORY_CODES = {
    '식비': 'FOOD', '주거·통신': 'HOUSING_TELECOM', '교통·차량': 'TRANSPORT_CAR', '생활용품': 'HOUSEHOLD',
    '패션·쇼핑': 'FASHION_SHOPPING', '의료·건강': 'MEDICAL_HEALTH', '뷰티·미용': 'BEAUTY', '교육': 'EDUCATION',
    '문화·여가': 'CULTURE_LEISURE', '여행·숙박': 'TRAVEL_LODGING', '기타': 'ETC', '미분류': 'UNCLASSIFIED',
}

KEEP = ['상가업소번호', '상호명', '지점명', '상권업종대분류명', '상권업종중분류명', '상권업종소분류코드',
        '상권업종소분류명', '시군구명', '행정동명', '법정동명', '도로명주소', '층정보', '경도', '위도',
        '정제상태', '제외사유', '소비카테고리', '라벨상태']


def distance_m(lat1, lon1, lat2, lon2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source')
    parser.add_argument('--persona', default='a', choices=sorted(ZONES))
    args = parser.parse_args()
    zones = ZONES[args.persona]
    out = Path(__file__).with_name('data') / f'merchants_{args.persona}_pool.csv'
    count = 0
    with open(args.source, encoding='utf-8-sig', newline='') as src, out.open('w', encoding='utf-8-sig', newline='') as dst:
        writer = csv.DictWriter(dst, fieldnames=['zone', 'zone_name', 'distance_m', 'category_code', *KEEP], lineterminator='\n')
        writer.writeheader()
        for row in csv.DictReader(src):
            try:
                lat, lon = float(row['위도']), float(row['경도'])
            except ValueError:
                continue
            for zone, name, zlat, zlon, radius, _ in zones:
                # 빠른 사각형 거르기 뒤 실제 거리 계산 (반경에 맞춘 위도 · 경도 폭)
                if abs(lat - zlat) > radius / 110_000 or abs(lon - zlon) > radius / 88_000:
                    continue
                d = distance_m(lat, lon, zlat, zlon)
                if d <= radius:
                    writer.writerow({'zone': zone, 'zone_name': name, 'distance_m': round(d),
                                     'category_code': CATEGORY_CODES.get(row['소비카테고리'], ''),
                                     **{k: row[k] for k in KEEP}})
                    count += 1
                    break
    print(f'{out} 저장: {count}곳')


if __name__ == '__main__':
    main()

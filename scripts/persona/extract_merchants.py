"""시연용 페르소나 가게 풀 뽑기 (1단계).

상가정보 라벨 파일(stores_all_categories.csv, 약 2GB)에서 페르소나 생활 구역 반경 안 가게만 잘라낸다.
원본은 Git에 올리지 않고, 결과 CSV(공개 상가정보 발췌)만 scripts/persona/data/ 에 둔다.

    python scripts/persona/extract_merchants.py <상가정보 CSV 경로> --persona a

원본이 커서 서울 줄만 먼저 잘라 두면 훨씬 빠르다 (2GB → 약 430MB, 20초):

    head -1 stores_all_categories.csv > seoul.csv
    LC_ALL=C grep -F ',서울특별시,' stores_all_categories.csv >> seoul.csv
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
            if row['시도명'] != '서울특별시':
                continue
            try:
                lat, lon = float(row['위도']), float(row['경도'])
            except ValueError:
                continue
            for zone, name, zlat, zlon, radius, _ in zones:
                # 빠른 사각형 거르기 뒤 실제 거리 계산
                if abs(lat - zlat) > 0.01 or abs(lon - zlon) > 0.012:
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

"""시연용 페르소나 가게 고르기 (2단계).

extract_merchants.py 가 만든 merchants_<p>_pool.csv 에서 역할(점심 · 카페 · 배달 …)별 후보를 고르고,
단골 가게를 고정 seed 로 정한다. 다시 돌려도 결과가 같다.

    python scripts/persona/select_merchants.py --persona a
"""
import argparse
import csv
import random
from pathlib import Path

DATA = Path(__file__).with_name('data')
WEEKEND = ['seongsu', 'yeonnam', 'jamsil', 'yeouido']

# 역할: (구역들, 업종 소분류들, 구역별 단골 수, 구역별 최대 후보 수)
ROLES = {
    'a': {
        'lunch': (['office'], ['백반/한정식', '국/탕/찌개류', '김밥/만두/분식', '국수/칼국수', '중국집',
                               '일식 카레/돈가스/덮밥', '일식 면 요리', '버거', '베트남식 전문', '마라탕/훠궈',
                               '토스트/샌드위치/샐러드'], {'office': 5}, 40),
        'cafe': (['office', 'home'], ['카페'], {'office': 2, 'home': 1}, 20),
        'convenience': (['office', 'home'], ['편의점'], {'office': 1, 'home': 1}, 10),
        'delivery': (['home'], ['치킨', '피자', '족발/보쌈', '중국집', '김밥/만두/분식', '마라탕/훠궈',
                                '돼지고기 구이/찜'], {'home': 3}, 25),
        'home_meal': (['home'], ['백반/한정식', '국/탕/찌개류', '김밥/만두/분식', '국수/칼국수', '버거'],
                      {'home': 2}, 15),
        'bakery': (['home'], ['빵/도넛'], {'home': 1}, 8),
        'grocery': (['home'], ['슈퍼마켓', '채소/과일 소매업', '반찬/식료품 소매업'], {'home': 1}, 8),
        'household': (['home'], ['주방/가정용품 소매업', '침구류/커튼 소매업', '전기용품/조명장치 소매업'], {}, 8),
        'pharmacy': (['home'], ['약국'], {'home': 1}, 5),
        'clinic': (['home'], ['내과/소아과 의원', '이비인후과 의원'], {'home': 1}, 5),
        'hair': (['home'], ['미용실'], {'home': 1}, 5),
        'cosmetics': (['office', 'home'], ['화장품 소매업'], {'office': 1}, 8),
        'laundry': (['home'], ['세탁소', '셀프 빨래방'], {'home': 1}, 3),
        'books': (['office', 'home'], ['서점', '문구/회화용품 소매업'], {}, 5),
        'weekend_meal': (WEEKEND, ['경양식', '파스타/스테이크', '일식 회/초밥', '일식 면 요리', '베트남식 전문',
                                   '기타 서양식 음식점', '돼지고기 구이/찜', '버거', '마라탕/훠궈'], {}, 15),
        'weekend_cafe': (WEEKEND, ['카페'], {}, 15),
        'dessert': (WEEKEND, ['빵/도넛', '아이스크림/빙수'], {}, 8),
        'bar': (['yeonnam', 'home'], ['요리 주점', '생맥주 전문'], {}, 10),
        'fun': (['yeonnam', 'jamsil', 'seongsu'], ['노래방', '볼링장', '기타 오락장', '전자 게임장'], {}, 6),
        'fashion': (WEEKEND, ['여성 의류 소매업', '기타 의류 소매업', '신발 소매업', '액세서리/잡화 소매업'], {}, 8),
        'gift': (WEEKEND, ['꽃집', '기념품점'], {}, 4),
    },
    'b': {
        'lunch': (['office'], ['백반/한정식', '국/탕/찌개류', '김밥/만두/분식', '국수/칼국수', '중국집',
                               '일식 카레/돈가스/덮밥', '일식 면 요리', '버거', '베트남식 전문', '돼지고기 구이/찜',
                               '토스트/샌드위치/샐러드'], {'office': 5}, 40),
        'cafe': (['office', 'home'], ['카페'], {'office': 2, 'home': 1}, 20),
        'convenience': (['office', 'home'], ['편의점'], {'office': 1, 'home': 1}, 10),
        'team_dinner': (['office'], ['요리 주점', '돼지고기 구이/찜', '일식 회/초밥', '생맥주 전문', '소고기 구이/찜'],
                        {}, 15),
        'family_meal': (['home'], ['백반/한정식', '돼지고기 구이/찜', '횟집', '중국집', '국/탕/찌개류'], {'home': 1}, 12),
        'bakery': (['home'], ['빵/도넛'], {'home': 1}, 8),
        'grocery': (['home', 'home_car'], ['슈퍼마켓', '채소/과일 소매업', '반찬/식료품 소매업', '정육점'],
                    {'home': 1}, 8),
        'fuel': (['home', 'home_car'], ['주유소'], {'home': 1, 'home_car': 1}, 8),
        'carwash': (['home_car'], ['자동차 세차장'], {'home_car': 1}, 6),
        'car_repair': (['home_car'], ['자동차 정비소', '자동차 부품 소매업'], {'home_car': 1}, 6),
        'gym': (['home', 'home_car'], ['헬스장'], {'home': 1}, 4),
        'pharmacy': (['home'], ['약국'], {'home': 1}, 5),
        'clinic': (['home'], ['내과/소아과 의원', '이비인후과 의원', '치과의원'], {'home': 1}, 5),
        'hair': (['home'], ['미용실'], {'home': 1}, 5),
        'pangyo_meal': (['pangyo'], ['경양식', '일식 회/초밥', '백반/한정식', '파스타/스테이크', '버거', '베트남식 전문'],
                        {}, 15),
        'pangyo_bar': (['pangyo', 'jeongja'], ['요리 주점', '생맥주 전문'], {}, 10),
        'weekend_cafe': (['pangyo', 'jeongja', 'hannam'], ['카페'], {}, 12),
        'brunch': (['jeongja'], ['경양식', '토스트/샌드위치/샐러드', '빵/도넛'], {}, 10),
        'date_meal': (['hannam'], ['경양식', '파스타/스테이크', '일식 회/초밥', '기타 서양식 음식점', '베트남식 전문',
                                   '기타 동남아식 전문'], {}, 20),
        'dessert': (['hannam', 'jeongja'], ['빵/도넛', '아이스크림/빙수'], {}, 8),
        'fashion': (['pangyo', 'hannam'], ['남성 의류 소매업', '기타 의류 소매업', '신발 소매업', '가방 소매업'], {}, 8),
        'gift': (['hannam'], ['꽃집', '기념품점'], {}, 4),
        'fun': (['pangyo', 'jeongja', 'home'], ['노래방', '볼링장', '당구장', 'PC방', '골프 연습장'], {}, 6),
        # 11/16 추가 (앞 역할의 단골 선택이 바뀌지 않게 맨 끝에 둔다): 남성 커트 · 올리브영 · 퇴근 후 스크린골프
        'mens_hair': (['home'], ['미용실'], {'home': 1}, 5),
        'olive': (['home', 'office'], ['그 외 기타 상품 전문 소매업', '화장품 소매업'], {'home': 1}, 3),
        'screen_golf': (['office'], ['골프 연습장'], {'office': 1}, 4),
    },
}
SEEDS = {'a': 20261120, 'b': 20261121}
# 업종 라벨이 틀린 가게(예: 옷가게가 슈퍼마켓)를 거르려고 이름에 이 말이 있어야 하는 역할
NAME_KEYWORDS = {'grocery': ('마트', '슈퍼', '식자재', '청과', '야채', '반찬', '정육'),
                 'mens_hair': ('바버', '맨즈', '남성'), 'olive': ('올리브영',), 'screen_golf': ('스크린', '골프존')}
EXCLUDE_NAME = ('유흥',)
# 상가정보 라벨(v3: 다품목 미분류)과 서비스 분류 규칙이 다른 역할은 서비스 규칙을 정답으로 쓴다
# (backend/finance/classifier.py — 11/05 결정: 편의점·슈퍼·마트는 식비, 세탁·수선은 기타)
CATEGORY_OVERRIDE = {'convenience': 'FOOD', 'grocery': 'FOOD', 'laundry': 'ETC', 'mens_hair': 'BEAUTY', 'olive': 'BEAUTY',
                     'screen_golf': 'CULTURE_LEISURE'}
SOURCE = '상가정보_20260630/상가라벨_v3.2_20261020'  # alpha_home_merchants.csv 와 같은 원본


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--persona', default='a', choices=sorted(ROLES))
    args = parser.parse_args()
    rng = random.Random(SEEDS[args.persona])
    pool_path = DATA / f'merchants_{args.persona}_pool.csv'
    with pool_path.open(encoding='utf-8-sig', newline='') as f:
        pool = [r for r in csv.DictReader(f) if r['정제상태'] == '기본검증통과'
                and not any(w in r['상호명'] for w in EXCLUDE_NAME)]
    out_rows, used = [], set()
    for role, (zones, smalls, regulars, cap) in ROLES[args.persona].items():
        for zone in zones:
            keywords = NAME_KEYWORDS.get(role)
            candidates = sorted((r for r in pool if r['zone'] == zone and r['상권업종소분류명'] in smalls
                                 and (not keywords or any(k in r['상호명'] for k in keywords))),
                                key=lambda r: (int(r['distance_m']), r['상가업소번호']))[:cap]
            n_regular = min(regulars.get(zone, 0), len(candidates))
            # 단골은 가까운 절반 안에서 고른다
            near = candidates[:max(n_regular, len(candidates) // 2)]
            regular_ids = {r['상가업소번호'] for r in rng.sample(near, n_regular)}
            for r in candidates:
                key = (role, r['상가업소번호'])
                if key in used:
                    continue
                used.add(key)
                out_rows.append({
                    'role': role, 'regular': 'Y' if r['상가업소번호'] in regular_ids else 'N',
                    'zone': zone, 'zone_name': r['zone_name'], 'distance_m': r['distance_m'],
                    'store_id': r['상가업소번호'], 'merchant_name': r['상호명'], 'branch_name': r['지점명'],
                    'industry_small': r['상권업종소분류명'], 'category_code': CATEGORY_OVERRIDE.get(role) or r['category_code'] or 'UNCLASSIFIED',
                    'sigungu': r['시군구명'], 'dong': r['행정동명'], 'address': r['도로명주소'],
                    'lat': r['위도'], 'lon': r['경도'], 'source': SOURCE,
                })
    out = DATA / f'merchants_{args.persona}.csv'
    with out.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(out_rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(out_rows)
    print(f'{out} 저장: {len(out_rows)}곳 (단골 {sum(r["regular"] == "Y" for r in out_rows)}곳)')


if __name__ == '__main__':
    main()

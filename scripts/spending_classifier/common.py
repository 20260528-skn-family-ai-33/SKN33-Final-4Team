"""학습과 추론에서 공유하는 정규화·카테고리·고정 기준선."""
import hashlib
import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "local_workspace"
CATEGORIES = {
    "FOOD": "식비", "HOUSING_TELECOM": "주거·통신", "TRANSPORT_CAR": "교통·차량",
    "HOUSEHOLD": "생활용품", "FASHION_SHOPPING": "패션·쇼핑", "MEDICAL_HEALTH": "의료·건강",
    "BEAUTY": "뷰티·미용", "EDUCATION": "교육", "CULTURE_LEISURE": "문화·여가",
    "TRAVEL_LODGING": "여행·숙박",
}
CODES = list(CATEGORIES)
NAME_TO_CODE = {v: k for k, v in CATEGORIES.items()}
NORMALIZATION_VERSION = "v5_preserve_parenthesis_content_and_branches_1"
CORPORATE = re.compile(r"\(\s*(?:주|유|사|재)\s*\)|주식회사|유한책임회사|유한회사|사단법인|재단법인")


def normalize_name(name):
    if not isinstance(name, str):
        raise TypeError("상호는 문자열이어야 합니다")
    name = CORPORATE.sub("", unicodedata.normalize("NFKC", name).lower())
    # 괄호 기호만 제거하며, 내용과 상호에 포함된 지점명은 보존한다.
    return "".join(c for c in name if not c.isspace() and not unicodedata.category(c).startswith("P"))


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def work_path(path):
    path = Path(path).resolve()
    if path.is_relative_to(ROOT / "output"):
        raise ValueError("output은 문서 전용입니다. 작업 데이터·평가 결과는 data 또는 reports에 저장하세요")
    return path


def write_json(path, value):
    path = work_path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


# 테스트 이름을 보지 않고 확정한 B1 끝말 규칙. 겹칠 때 긴 끝말을 우선한다.
# 정책 예외(운전학원, 마사지, 동물병원 등)를 일반 규칙보다 구체적으로 적는다.
SUFFIXES = {
    "FOOD": "치킨 카페 커피 식당 음식점 분식 김밥 베이커리 빵집 정육점 반찬가게 횟집 피자 주점 족발 국밥",
    "HOUSING_TELECOM": "공인중개사사무소 공인중개사 부동산 인테리어 고시원 청소",
    "TRANSPORT_CAR": "주유소 세차장 카센터 자동차정비 렌터카 타이어 자전거",
    "HOUSEHOLD": "가구 가전 컴퓨터수리 휴대폰수리 주방용품",
    "FASHION_SHOPPING": "의류 옷가게 양복점 신발 귀금속 시계수리",
    "MEDICAL_HEALTH": "동물병원 성형외과 피부과 치과 한의원 의원 병원 약국 안경",
    "BEAUTY": "미용실 헤어 네일 피부관리 화장품",
    "EDUCATION": "운전학원 학원 독서실 스터디카페 서점 문구",
    "CULTURE_LEISURE": "필라테스학원 요가학원 노래방 pc방 헬스장 필라테스 골프연습장 당구장 볼링장 사우나 마사지 사진관 꽃집",
    "TRAVEL_LODGING": "호텔 모텔 펜션 여행사 캠핑장 글램핑",
}
RULES = sorted(((suffix, code) for code, words in SUFFIXES.items() for suffix in words.split()),
               key=lambda x: (-len(x[0]), x[0]))


def keyword_prediction(name):
    for suffix, code in RULES:
        if name.endswith(suffix):
            return code, True
    return "FOOD", False

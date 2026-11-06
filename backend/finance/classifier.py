"""
거래 분류 에이전트 (pipeline.classify_spending 에서 부른다). 메모: docs/agents/거래분류.md

순서: 결제대행 접두어 떼기 → 1 다품목 → 2 사용자 기억 → 3 규칙·사전 → 4 SVM → 5 검색+Jev → 6 확인 필요
- 분류 기준: docs/소비카테고리_분류기준_20261105.md
- 모델 파일은 Git에 없다. 기본 위치: local_workspace/models/spending_classifier/artifacts/content_policy_svm/
  (docs/ML_MODELS.md). 다른 곳이면 .env 의 SPENDING_MODEL_PATH
- 모델 파일이 없으면 예외 → 파이프라인이 실패로 기록하고 다음 동기화 때 다시 시도한다
- 5단계는 .env 에 TYPESAFE_API_KEY · JEV_MODEL 이 있을 때만 돈다. 없으면 4단계 결과로 '확인 필요'
"""
import csv
import functools
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from decimal import Decimal
from pathlib import Path

import joblib
import numpy as np
from django.conf import settings

from scripts.spending_classifier import common
from scripts.spending_classifier.transport_rules import transport_rule

from .merchant_rules import SEARCHED_MULTI_ITEM, last_user_choice, seen_multi_item_industry
from .models import FinancialTransaction as Tx

logger = logging.getLogger(__name__)

ROOT = Path(settings.BASE_DIR).parent
# shortcut: 경로를 settings 가 아니라 여기서 읽는다. 통합 검수 때 settings 로 옮길지 정함
MODEL_PATH = Path(os.getenv("SPENDING_MODEL_PATH") or ROOT / "local_workspace/models/spending_classifier/artifacts/content_policy_svm/spending_category_svm.joblib")
THRESHOLD = 0.85  # 임시: 상가정보 검증에서 자동 확정 정확도 98.5%. 실거래 표본으로 다시 정함
JEV_THRESHOLD = 0.9  # 임시: Jev confidence 기준. 정답 붙인 실거래 표본으로 다시 정함
# 동명 충돌 목록 (분류 워크플로 10/26 4장): 상가 데이터에서 같은 이름에 다른 카테고리가 붙은 상호.
# 혜리 님 prepare_data.py 가 만드는 label_conflicts.csv (정리한 이름 기준). 모델처럼 Git 에 없다. 다른 곳이면 .env 의 SPENDING_CONFLICTS_PATH
CONFLICTS_PATH = Path(os.getenv("SPENDING_CONFLICTS_PATH") or ROOT / "local_workspace/models/spending_classifier/label_conflicts.csv")

# '확인 필요' 이유 문장 (사유별 템플릿)
REVIEW_REASON = "가게 이름만으로는 카테고리를 정하기 어려워요."
MULTI_ITEM_REASON = "여러 종류의 물건을 파는 곳이라 무엇을 샀는지 골라 주세요."
NOT_FOUND_REASON = "가게 정보를 찾지 못해 카테고리를 정하지 못했어요."
UNSURE_REASON = "가게 정보를 찾았지만 카테고리를 확실히 정하지 못했어요."
LOOKUP_FAILED_REASON = "가게 정보를 확인하지 못했어요. 직접 골라 주세요."
PAY_CHARGE_REASON = "페이 충전이라 무엇에 썼는지 골라 주세요."
CONFLICT_REASON = "같은 이름의 다른 업종 가게가 있어 카테고리를 정하지 못했어요."
USER_DIFFERS_REASON = "전에 이 가게를 다른 카테고리로 고르셨어요. 이번 거래도 확인해 주세요."

# 토스·네이버페이 등 충전 출금 (10/30): 충전 뒤 무엇을 샀는지는 페이 안에만 있어서 사용자에게 묻는다
# link_basics가 충전을 소비로 넘겨줄 때부터 동작한다. 아직 쓰지 않았거나 송금이면 화면의 [이체로 되돌리기]
PAY_CHARGE = re.compile(r"(페이|머니|pay)충전")

# 상호처럼 안 생긴 이름: SVM 은 뜻 없는 이름도 식비로 확신해서(11/02 실거래 비식별거래처 5건 식비 0.85~0.91) SVM·검색 전에 거른다
# shortcut: 아는 표기만 (비식별 처리된 이름, 숫자뿐인 이름, 한 글자). 실거래에서 새 표기가 보이면 추가
UNKNOWN_STORE = re.compile(r"비식별|거래처\d+$|^\d*$")
UNKNOWN_STORE_REASON = "가게 이름을 알 수 없어 카테고리를 정하지 못했어요."

# 결제대행(PG) 접두어: 'NICE*쿠팡' → '쿠팡'
PG_PREFIX = re.compile(r"^[^*\s]{1,20}\*\s*")
# 결제수단 꼬리표: '멜론_카카오페이' → '멜론', 'Apple_KCP' → 'Apple' (11/02 실거래 명세)
# shortcut: 확인된 결제수단만. 실거래에서 새 꼬리표가 보이면 추가
PAY_SUFFIX = re.compile(r"_(카카오페이|네이버페이|토스페이|kcp)$", re.IGNORECASE)
# 결제대행 이름이 '-'·'_'로 붙는 표기: 'KCP-쿠팡_쿠팡' → '쿠팡_쿠팡', '이니시스(빌링)_3_레벨업 피시방' → '레벨업 피시방' (11/02 카드 명세)
# shortcut: 확인된 결제대행사만. 실거래에서 새 표기가 보이면 추가
PG_NAMED_PREFIX = re.compile(r"^(kcp|kg이니시스|이니시스|nice|ksnet|토스페이먼츠)(\([^)]*\))?[-_](\d+_)?", re.IGNORECASE)
# 'A - B' 표기 (11/05 실거래): A 가 결제 경로면 B 가 가맹점('네이버페이 - 봄툰'), 아니면 A 가 가맹점이고 B 는 결제대행사('메가MGC_인증_3 - 메가 MGC 커피')
# shortcut: 확인된 결제 경로만. 실거래에서 새 경로가 보이면 추가
PAY_ROUTES = ("네이버페이", "카카오페이", "토스페이", "페이코", "payco", "페이레터", "대표인증", "대표일반", "인터넷상거래", "다우데이타", "한국정보통신")
ROUTE_TAIL = re.compile(r"(_구)?_(nice|인증|일반)(_\d+)?$|_\d+$", re.IGNORECASE)

# ----- 1. 다품목 -----
# 다품목 = 상호만으로 품목을 모르는 곳 (분류 기준 D1, D16). 대부분 사용자에게 묻고, 과거 선택은 후보로만 보여 준다 (분류 워크플로 10/26 단계 2)
# 11/05 결정: 편의점·슈퍼·대형마트는 기본 식비, 다이소는 기본 생활용품. 11/06 결정: 사용자가 '앞으로도 적용'을 고르면 다품목도 그 선택이 먼저
# 브랜드는 혜리 님 multi_category_brand_candidates.csv(10/30) 기준 (한글 표기 지에스25·씨유 포함)
# shortcut: 상가 업종이 아니라 가맹점 이름으로 판별한다 (정리된 이름 기준). 5단계에서는 검색 업종으로도 판별
FOOD_RETAIL_PREFIXES = (
    "gs25", "지에스25", "세븐일레븐", "이마트", "미니스톱", "홈플러스", "롯데마트", "롯데슈퍼", "노브랜드", "코스트코", "트레이더스",
    "하나로마트", "농협하나로마트",
)
ASK_RETAIL_PREFIXES = (
    "쿠팡", "11번가", "g마켓", "지마켓", "옥션", "ssg", "위메프", "티몬", "롯데온", "카카오쇼핑", "갤러리아", "번개장터", "중고나라", "당근마켓",
)
# CSV: cu·씨유·매점은 단순 접두어 금지 → 이름 그대로이거나 '지점명+점'일 때만. 나머지는 이름이 정확히 같을 때만
FOOD_RETAIL_PATTERNS = tuple(re.compile(p) for p in (
    r"^cu(?:$|[가-힣0-9]+(?:지점|점)$)", r"^씨유(?:$|[가-힣0-9]+(?:지점|점)$)", r"^매점(?:$|[가-힣0-9]+(?:지점|점)$)", r"^씨유대전$",
))
ASK_RETAIL_PATTERNS = tuple(re.compile(p) for p in (r"^신앙촌상회$", r"^중국식품$"))
FOOD_RETAIL_KEYWORDS = ("b마트", "비마트", "배민스토어")  # 이름 어디에 있어도 (예: 배민B마트 강남점)
FOOD_RETAIL_SUFFIXES = ("마트", "편의점", "슈퍼", "슈퍼마켓")
# 배달 = 식비, 전자제품 = 생활용품, 버거 = 식비, OTT = 문화·여가. CSV의 아이스크림 할인점류·올리브영은 분류 기준상 식비·뷰티라 넣지 않음
NOT_MULTI_ITEM_PREFIXES = ("쿠팡이츠", "쿠팡플레이", "롯데하이마트", "하이마트", "노브랜드버거")

# ----- 3. 규칙·사전 (분류 기준 문서) -----
# OTT·음악 스트리밍 구독 = 문화·여가 (10/21 결정)
# 다른 가게 이름에 들어가기 어려운 서비스명만 앞부분으로 (예: 넷플릭스서비시스코리아)
SUBSCRIPTION_PREFIXES = (
    "넷플릭스", "netflix", "유튜브프리미엄", "youtubepremium", "디즈니플러스", "쿠팡플레이",
    "지니뮤직", "스포티파이", "spotify", "애플뮤직",
)
# 짧은 이름은 일반 가게(웨이브헤어·멜론카페·플로렌스치과)와 겹쳐서 이름이 정확히 같을 때만 (PR #64 리뷰)
SUBSCRIPTION_NAMES = {"티빙", "웨이브", "왓챠", "멜론", "벅스", "플로"}
# 콘텐츠 플랫폼 = 문화·여가 (D11 예외). 동명 상호에 번지지 않게 이름이 정확히 같을 때만 (분류 기준 3장)
CONTENT_PLATFORMS = {"리디", "리디북스", "레진코믹스", "레진엔터테인먼트", "봄툰", "미스터블루"}
# 배달 앱 서비스명·운영사 법인명 (검색하면 본사가 나와서 틀림). 이름이 정확히 같을 때만. B마트·배민스토어는 다품목(위)
# shortcut: 이름이 그냥 "배달의민족"이면 B마트 장보기도 식비로 들어감. 카드 명세에서 구분되는 표기가 확인되면 다품목 키워드에 추가
CORPORATE_NAMES = {
    "우아한형제들": "FOOD", "위대한상상": "FOOD", "배달의민족": "FOOD", "배민": "FOOD", "요기요": "FOOD", "쿠팡이츠": "FOOD",
    "cj올리브네트웍스": "BEAUTY",  # 올리브영 운영사. 검색하면 '기업'으로 나옴
    # 온라인 여행 예약: 지도 검색에 서비스가 아니라 같은 이름의 다른 장소가 나온다 (트립닷컴 → 에버랜드 편의시설)
    "트립닷컴": "TRAVEL_LODGING", "아고다": "TRAVEL_LODGING", "야놀자": "TRAVEL_LODGING", "여기어때": "TRAVEL_LODGING",
    "익스피디아": "TRAVEL_LODGING", "에어비앤비": "TRAVEL_LODGING", "호텔스닷컴": "TRAVEL_LODGING", "부킹닷컴": "TRAVEL_LODGING",
}
# '기타' = 10개 밖 소비 (분류 기준 2장). shortcut: 이름 끝·이름 안의 낱말로 판별. 5단계에서는 Jev 가 업종으로 판단
ETC_SUFFIXES = (
    "세탁소", "세탁", "빨래방", "수선", "수선실", "담배", "복권", "로또", "예식장", "웨딩홀", "장례식장", "화장장", "납골당",
    "펫샵", "애견용품", "원단",
)
ETC_KEYWORDS = ("연회비", "보험")
# 택시 (11/02): 이름에 '택시'만 들어 있으면 택시파스타·피자택시 같은 식당도 걸려서(카카오 장소 193곳 중 식당 6곳),
# '개인택시'·'일반택시'가 들어 있거나 '택시'가 띄어 쓴 한 낱말일 때만. 택시조합·조합 금고는 소비처가 아니라 뺀다
TAXI_NAMES = ("개인택시", "일반택시")
TAXI_WORD = re.compile(r"(^|[\s_\-])택시($|[\s_\-])")
NOT_TAXI_WORDS = ("조합", "금고", "공제", "충전", "취소", "환불")
# 교통카드 (11/05): 가게 이름이 아니라 SVM 이 엉뚱한 카테고리를 확신한다 (모바일티머니선불형 생활용품 0.94). 충전도 교통비라 페이 충전으로 묻지 않는다 (11/06)
TRANSIT_CARDS = ("티머니", "캐시비", "레일플러스")
# PC방 = 문화·여가 (분류 기준 2장). 표기가 피시방·PC방 둘 다
CULTURE_KEYWORDS = ("피시방", "pc방")
# 검증된 브랜드 = 식비 (11/02): SVM 확신이 낮고(0.81~0.84) 검색하면 다른 지점·본사가 자주 나와서(7건 중 5건) 이름으로 정한다
# 앱 충전은 '스타벅스계좌'로 찍힘. '들어 있으면'이 아니라 '시작하면' (카카오 장소 256곳 중 '홍단헤어 스타벅스점' 같은 미용실 제외)
FOOD_BRANDS = ("스타벅스", "starbucks")
# 검색 근거로는 구별되지 않는 브랜드만 이름으로 정한다 (11/05 세진 결정). 이름이 이렇게 시작할 때만
# 아트박스·카카오프렌즈·펀앤펀은 업종이 일반 문구점과 같고, 토피코는 네이버가 자동판매기로 알려 주고 지점명을 붙이면 카카오에 없다
# shortcut: 일반 원인을 못 고친 예외만 둔다. 검색 개선(메모의 5단계 개선 계획) 뒤 없앨 수 있는지 다시 본다
BRAND_CATEGORIES = {
    "아트박스": "FASHION_SHOPPING", "카카오프렌즈": "FASHION_SHOPPING", "펀앤펀": "CULTURE_LEISURE", "토피코": "CULTURE_LEISURE",
}
# 한 이름으로 여러 서비스가 찍히는 곳 (애플: 음악·iCloud·앱·TV+). 무엇에 썼는지 몰라 '확인 필요' + 문화·여가 후보 (11/02 결정)
# 다품목과 달리 사용자가 '앞으로도 적용'을 고르면 다음부터 그 카테고리로 자동 분류
MIXED_SERVICE_NAMES = {"apple", "appleservices"}
MIXED_SERVICE_REASON = "애플 결제는 음악·저장공간·앱이 같은 이름으로 찍혀서 무엇에 썼는지 골라 주세요."

# ----- 5. 검색 + Jev -----
# Jev 선택지 = 10개 + 기타 + 판단 보류 (10/30 결정). 설명은 분류 기준 문서 2장
UNKNOWN = "UNKNOWN"
JEV_CRITERIA = {
    "FOOD": "식비: 식사, 배달음식, 식재료, 카페·간식, 주점, 주류 구입",
    "HOUSING_TELECOM": "주거·통신: 월세, 관리비, 공과금, 통신요금, 부동산 중개, 인테리어·청소, 고시원",
    "TRANSPORT_CAR": "교통·차량: 대중교통, 택시, 주유, 주차, 차량 정비·세차·대여, 자전거",
    "HOUSEHOLD": "생활용품: 생필품, 주방용품, 가구·가전, 전자기기·휴대폰, 기기 수리, 문구",
    "FASHION_SHOPPING": "패션·쇼핑: 의류, 신발, 가방, 액세서리, 시계·귀금속, 팬시·캐릭터 상품",
    "MEDICAL_HEALTH": "의료·건강: 병의원 전체(피부과·성형외과 포함), 약국, 건강보조식품, 안경, 동물병원",
    "BEAUTY": "뷰티·미용: 화장품, 미용실, 네일, 피부관리실, 가발",
    "EDUCATION": "교육: 학원(입시·외국어·음악·미술·운전 등), 독서실·스터디카페, 서점, 강의, 자격증",
    "CULTURE_LEISURE": "문화·여가: 영화, 공연, 게임, 노래방·PC방, 운동시설·운동용품, 목욕탕·사우나, 마사지, 꽃·기념품·사진촬영, 장난감·피규어·뽑기, OTT·음악 구독",
    "TRAVEL_LODGING": "여행·숙박: 여행상품, 호텔, 펜션, 모텔, 캠핑·글램핑",
    "ETC": "기타(10개 밖 소비): 송금·경조사(예식장·장례식장), 담배·복권, 세탁·수선, 실·원단, 반려동물 분양·용품, 보험료·카드 연회비",
    UNKNOWN: "판단 보류: 이름과 업종만으로는 어느 카테고리인지 정할 수 없음",
}
JEV_QUESTION = "이 가맹점에서 결제한 소비의 카테고리는? 가게 업종 기준으로 고르고, 정보가 부족하면 판단 보류를 고른다."
# 검색 끝 칸 업종이 이런 곳이면 다품목 → Jev 를 부르지 않고 '확인 필요'
MULTI_ITEM_INDUSTRIES = ("편의점", "슈퍼마켓", "대형마트", "백화점", "종합쇼핑몰", "마트")
FOOD_RETAIL_INDUSTRIES = ("편의점", "슈퍼마켓")  # 이 둘과 '○○마트'는 묻지 않고 식비 (11/05 결정)
# 가게가 아닌 업종(법인명 검색 시 본사가 나옴)은 근거로 넘기지 않는다
NON_STORE_INDUSTRIES = ("소프트웨어", "애플리케이션", "금융", "은행", "증권", "기업", "회사", "사무소", "협회")


def store_name(name):
    """결제대행 접두어·결제수단 꼬리표를 뗀 가맹점명. 없으면 None"""
    left, dash, right = (name or "").partition(" - ")
    if dash and left.strip() and right.strip():
        name = right if left.lower().startswith(PAY_ROUTES) else ROUTE_TAIL.sub("", left)
    name = PG_NAMED_PREFIX.sub("", PG_PREFIX.sub("", name or "").strip())
    return PAY_SUFFIX.sub("", name).strip() or None


@functools.cache
def _estimator():
    bundle = joblib.load(MODEL_PATH)  # pickle 이라 팀이 공유한 파일만 둔다
    if bundle["normalization_version"] != common.NORMALIZATION_VERSION:
        raise RuntimeError("모델과 이름 정리 규칙 버전이 다릅니다")
    return bundle["estimator"]


@functools.cache
def conflict_names():
    """동명 충돌 상호(정리한 이름) 집합. 파일이 없으면 빈 집합 — 충돌 차단 없이 지금처럼 동작한다
    shortcut: 파일에 정리 규칙 버전이 없어 모델과 같은 버전인지 확인하지 못한다. 혜리 님 파일에 버전이 생기면 모델처럼 검사"""
    if not CONFLICTS_PATH.exists():
        logger.warning("동명 충돌 목록이 없어 충돌 차단을 건너뜀: %s", CONFLICTS_PATH.name)
        return frozenset()
    with CONFLICTS_PATH.open(encoding="utf-8-sig", newline="") as f:
        return frozenset(row["name"] for row in csv.DictReader(f))


def predict(names):
    """이름마다 [(카테고리 코드, 확률), ...] 확률 높은 순 3개. 정리 후 빈 이름은 []"""
    cleaned = [common.normalize_name(name) for name in names]
    rows = [[] for _ in names]
    idx = [i for i, name in enumerate(cleaned) if name]
    if idx:
        estimator = _estimator()
        for i, p in zip(idx, estimator.predict_proba([cleaned[i] for i in idx])):
            rows[i] = [(str(estimator.classes_[k]), float(p[k])) for k in np.argsort(-p)[:3]]
    return rows


def is_multi_item(name):
    name = common.normalize_name(store_name(name) or "")
    if not name or name.startswith(NOT_MULTI_ITEM_PREFIXES):
        return False
    return (name.startswith(ASK_RETAIL_PREFIXES) or "백화점" in name or "다이소" in name
            or any(pattern.match(name) for pattern in ASK_RETAIL_PATTERNS) or _food_retail(name))


def _food_retail(name):
    return (name.startswith(FOOD_RETAIL_PREFIXES) or name.endswith(FOOD_RETAIL_SUFFIXES)
            or any(keyword in name for keyword in FOOD_RETAIL_KEYWORDS)
            or any(pattern.match(name) for pattern in FOOD_RETAIL_PATTERNS))


def retail_category(name):
    """다품목 중 묻지 않고 정하는 곳의 카테고리 코드 (11/05 결정). 없으면 None"""
    name = common.normalize_name(store_name(name) or "")
    if not name or name.startswith(NOT_MULTI_ITEM_PREFIXES) or name.startswith(ASK_RETAIL_PREFIXES) or "백화점" in name:
        return None
    if "다이소" in name:
        return "HOUSEHOLD"
    return "FOOD" if _food_retail(name) else None


def food_retail_industry(industry):
    return industry in FOOD_RETAIL_INDUSTRIES or industry.endswith("마트")


def is_unknown_store(name):
    normalized = common.normalize_name(store_name(name) or "")
    return len(normalized) < 2 or bool(UNKNOWN_STORE.search(normalized))


def is_pay_charge(name):
    normalized = common.normalize_name(store_name(name) or "")
    return bool(PAY_CHARGE.search(normalized)) and not any(card in normalized for card in TRANSIT_CARDS)


def rule_category(name):
    """3단계 규칙·사전으로 정해지는 카테고리 코드. 없으면 None"""
    normalized = common.normalize_name(name)
    if transport_rule(name, "N"):  # 혜리 님 교통 적요 규칙. 'N' = 소비로 정해진 거래 (분류 대상은 이미 소비)
        return "TRANSPORT_CAR"
    if (any(w in normalized for w in TAXI_NAMES) or TAXI_WORD.search(name)) and not any(w in normalized for w in NOT_TAXI_WORDS):
        return "TRANSPORT_CAR"
    if any(card in normalized for card in TRANSIT_CARDS):
        return "TRANSPORT_CAR"
    if any(keyword in normalized for keyword in CULTURE_KEYWORDS):
        return "CULTURE_LEISURE"
    if normalized.startswith(FOOD_BRANDS):
        return "FOOD"
    if brand := next((code for prefix, code in BRAND_CATEGORIES.items() if normalized.startswith(prefix)), None):
        return brand
    if normalized in CORPORATE_NAMES:
        return CORPORATE_NAMES[normalized]
    if normalized in CONTENT_PLATFORMS or normalized in SUBSCRIPTION_NAMES or normalized.startswith(SUBSCRIPTION_PREFIXES):
        return "CULTURE_LEISURE"
    if normalized.endswith(ETC_SUFFIXES) or any(keyword in normalized for keyword in ETC_KEYWORDS):
        return "ETC"
    return None


def _get_json(url, headers):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=5) as res:
        return json.loads(res.read())


def same_store(query, found_name):
    """검색된 가게가 거래 가맹점과 같은 가게로 보이는지. 다른 가게면 그 업종을 근거로 쓰지 않는다 (PR #64 리뷰)
    정리한 전체 이름이 한쪽에 다른 쪽이 들어 있을 때만 같은 가게. 첫 단어만 같으면 다른 가게일 수 있어 인정하지 않는다 (PR #64 재리뷰: 새봄 동물 ↔ 새봄식당)
    shortcut: 이름만 비교. 프랜차이즈 다른 지점도 불일치('확인 필요')가 된다. 지점·주소 대조는 카드 응답에 주소가 생기면"""
    q, f = common.normalize_name(query), common.normalize_name(found_name or "")
    return bool(q and f) and (q in f or f in q)


def search_store(query):
    """(출처, 가게 이름, 끝 칸 업종) 또는 None. 네이버(NCP API HUB) 먼저, 0건이면 카카오. 보내는 건 상호뿐"""
    q = urllib.parse.quote(query)
    if os.getenv("NAVER_CLIENT_ID"):
        items = _get_json(f"https://naverapihub.apigw.ntruss.com/search/v1/local?query={q}&display=1", {
            "X-NCP-APIGW-API-KEY-ID": os.getenv("NAVER_CLIENT_ID", ""),
            "X-NCP-APIGW-API-KEY": os.getenv("NAVER_CLIENT_SECRET", ""),
        })["items"]
        if items:
            return "naver", re.sub(r"<[^>]+>", "", items[0]["title"]), items[0]["category"].split(">")[-1]
    if os.getenv("KAKAO_REST_API_KEY"):
        docs = _get_json(f"https://dapi.kakao.com/v2/local/search/keyword.json?query={q}&size=1",
                         {"Authorization": f"KakaoAK {os.getenv('KAKAO_REST_API_KEY')}"})["documents"]
        if docs:
            parts = docs[0]["category_name"].split(" > ")
            # 프랜차이즈는 카카오 끝 칸이 업종이 아니라 브랜드명 (스타벅스 → '스타벅스') → 한 칸 앞
            leaf = parts[-2] if len(parts) > 1 and parts[-1] in docs[0]["place_name"] else parts[-1]
            return "kakao", docs[0]["place_name"], leaf
    return None


@functools.cache
def _jev():
    from typesafe_sdk import TypeSafeClient  # 5단계를 쓸 때만 필요
    return TypeSafeClient(model=os.getenv("JEV_MODEL"))  # SDK 기본값 이름(TYPESAFE_DEFAULT_MODEL)과 달라 직접 넘김


def ask_jev(state):
    """Jev 답 (choice, confidence, probabilities, model)"""
    from typesafe_sdk import Choice
    res = _jev().system_one(state=state, questions={"category": Choice(instructions=JEV_QUESTION, criteria=JEV_CRITERIA)})
    answer = res.answers["category"]
    return answer.choice, answer.confidence, dict(answer.probabilities), res.model


def judge(name):
    """5단계: 검색 업종을 근거로 Jev 가 판단. 키가 없으면 None.
    {"apply": (코드, 확신도)} 또는 {"review": ([(코드, 확률), ...], 이유)} + "record"(field_states 에 남길 근거)"""
    if not (os.getenv("TYPESAFE_API_KEY") and os.getenv("JEV_MODEL")):
        return None
    started = time.monotonic()
    query = common.CORPORATE.sub("", name).strip()  # 법인 표기만 빼고 상호·지점명 그대로 검색
    record = {"step": "search_jev", "source": None, "industry": None}
    try:
        found = search_store(query)
        industry = found[2] if found else None
        record.update(source=found and found[0], industry=industry)
        if found and not same_store(query, found[1]):
            industry = None  # 첫 결과가 다른 가게 (예: 새봄동물 → 새봄식당). 그 업종은 이 거래의 근거가 아님
            record["store_mismatch"] = True
        if industry and (industry in MULTI_ITEM_INDUSTRIES or industry.endswith("마트")):
            record[SEARCHED_MULTI_ITEM] = True  # 같은 가게의 다음 거래도 다품목으로 처리하는 근거 (PR #67 리뷰)
            if food_retail_industry(industry):
                return {"retail": "FOOD", "record": record}
            return {"review": ([], MULTI_ITEM_REASON), "record": record}
        if industry and any(word in industry for word in NON_STORE_INDUSTRIES):
            industry = None  # 본사·사무소가 검색된 경우. 근거로 쓰면 확신하며 틀림 (10/29 테스트)
            record["dropped_industry"] = True
        state = f"가맹점 이름: {query}" + (f"\n검색 업종: {industry}" if industry else "")
        choice, confidence, probabilities, model = ask_jev(state)
    except Exception as exc:  # 외부 API 장애는 '확인 필요'로 끝낸다. 거래 내용이 담길 수 있어 원문은 남기지 않음
        logger.warning("5단계 검색·Jev 실패: %s", type(exc).__name__)
        record["error"] = type(exc).__name__
        return {"review": ([], LOOKUP_FAILED_REASON), "record": record}
    finally:
        record["ms"] = round((time.monotonic() - started) * 1000)
    record.update(jev_model=model, choice=choice, confidence=round(confidence, 4),
                  probability=round(probabilities.get(choice, 0), 4))
    # Jev 점수만으로는 확정하지 않는다: 같은 가게로 확인된 검색 업종이 있을 때만 자동 적용 (PR #64 리뷰)
    if industry and choice != UNKNOWN and confidence >= JEV_THRESHOLD:
        return {"apply": (choice, confidence), "record": record}
    top = sorted(((c, p) for c, p in probabilities.items() if c != UNKNOWN), key=lambda x: -x[1])[:3]
    return {"review": (top, UNSURE_REASON if industry else NOT_FOUND_REASON), "record": record}


FIELDS = [
    "confirmed_category", "confirmation_state", "classification_source", "suggested_category",
    "suggestion_source", "suggestion_confidence", "suggestion_candidates", "suggestion_reason", "field_states",
]


def classify(user, txs):
    """사용자가 확정하지 않은 소비 거래에 카테고리를 제안한다. 가맹점명이 없는 거래는 그대로 둔다"""
    todo = [
        tx for tx in txs
        if tx.transaction_type == Tx.TransactionType.SPENDING and tx.confirmation_state != Tx.ConfirmationState.CONFIRMED
    ]
    changed, for_ml = [], []
    for tx in todo:
        name = store_name(tx.merchant_name)
        # 사용자의 마지막 선택. 자동 적용은 '앞으로도 적용'(future)일 때만, 다품목·페이 충전은 후보로만 (10/30)
        code, future = last_user_choice(user, tx.merchant_name)
        seen = None if is_multi_item(name) else seen_multi_item_industry(user, tx.merchant_name)
        seen_food = bool(seen) and food_retail_industry(seen)
        # 동명 충돌이면 저장된 검색 업종이 이 가게의 근거가 아니다. 첫 분류처럼 묻는다 (PR #92 리뷰)
        seen_conflict = seen_food and common.normalize_name(name or "") in conflict_names()
        if code and future and not is_pay_charge(name):
            # 임시 결정: 사용자 기억으로 정한 건 이 거래를 사용자가 고른 게 아니라서 rule 로 둔다
            _apply(tx, code, Tx.ClassificationSource.RULE, None)
        elif retail := retail_category(name) or ("FOOD" if seen_food and not seen_conflict else None):
            # '앞으로도 적용'이 없으면 지난 선택과 상관없이 정해진 카테고리
            _apply(tx, retail, Tx.ClassificationSource.RULE, None)
        elif is_multi_item(name) or seen:
            # 다품목(이름 사전 또는 같은 가게의 지난 검색 업종)은 SVM 도 부르지 않는다. 과거 선택이 있으면 후보로만
            _review(tx, [(code, None)] if code else [], CONFLICT_REASON if seen_conflict else MULTI_ITEM_REASON,
                    Tx.ClassificationSource.RULE if code else None)
        elif is_pay_charge(name):
            # 충전도 매번 쓰임이 달라서 다품목처럼 과거 선택은 후보로만
            _review(tx, [(code, None)] if code else [], PAY_CHARGE_REASON, Tx.ClassificationSource.RULE if code else None)
        elif common.normalize_name(name or "") in MIXED_SERVICE_NAMES:
            # 과거 선택(앞으로도 적용 아님)이 있으면 첫 후보로
            candidates = [c for c in dict.fromkeys((code, "CULTURE_LEISURE")) if c]
            _review(tx, [(c, None) for c in candidates], MIXED_SERVICE_REASON, Tx.ClassificationSource.RULE)
        elif name and (rule := rule_category(name)):
            _apply(tx, rule, Tx.ClassificationSource.RULE, None)
        elif name and is_unknown_store(name):
            _review(tx, [], UNKNOWN_STORE_REASON, None)
        else:
            if name:
                for_ml.append((tx, name, code))
            continue
        changed.append(tx)

    for (tx, name, past), top in zip(for_ml, predict([name for _, name, _ in for_ml])):
        if not top:
            continue
        code, confidence = top[0]
        # 동명 충돌이면 SVM 이 확신해도 확정하지 않고 검색+Jev 로 (분류 워크플로 10/26 3단계·4장)
        conflict = common.normalize_name(name) in conflict_names()
        if confidence >= THRESHOLD and not conflict:
            _apply_unless_user_differs(tx, code, Tx.ClassificationSource.ML, confidence, past, top)
        else:
            # shortcut: 거래 잠금 안에서 외부 API 를 부른다. record 의 ms 로 시간을 재고 길면 잠금 밖으로 옮길지 팀 회의 (메모 참고)
            result = judge(name)
            if result is None:
                _review(tx, top, CONFLICT_REASON if conflict else REVIEW_REASON, Tx.ClassificationSource.ML)
            elif "retail" in result and conflict:
                _review(tx, [(result["retail"], None)], CONFLICT_REASON, Tx.ClassificationSource.RULE)
            elif "retail" in result:
                _apply(tx, result["retail"], Tx.ClassificationSource.RULE, None)
            elif "apply" in result and conflict:
                # 동명 충돌: 검색 이름이 같아도 동명 가게 중 어느 곳의 거래인지 모른다 → Jev 답은 후보로만 (PR #68 리뷰)
                # shortcut: 지금 입력(이름뿐)으로는 구별 근거가 없다. 카드 응답에 주소·업종 코드가 생기면 그걸로 풀기
                code, confidence = result["apply"]
                candidates = [(code, confidence)] + [(c, p) for c, p in top if c != code]
                if past:
                    candidates = [(past, None)] + [(c, p) for c, p in candidates if c != past]
                _review(tx, candidates[:3], CONFLICT_REASON, Tx.ClassificationSource.LLM)
            elif "apply" in result:
                code, confidence = result["apply"]
                _apply_unless_user_differs(tx, code, Tx.ClassificationSource.LLM, confidence, past, top)
            else:
                _review(tx, *result["review"], Tx.ClassificationSource.LLM)
            if result is not None:
                record = {**result["record"], "name_conflict": True} if conflict else result["record"]
                tx.field_states = {**tx.field_states, "classifier": record}
        changed.append(tx)

    if changed:
        Tx.objects.bulk_update(changed, FIELDS, batch_size=500)


def _apply(tx, code, source, confidence):
    tx.confirmed_category_id = tx.suggested_category_id = code
    tx.confirmation_state, tx.classification_source, tx.suggestion_source = Tx.ConfirmationState.SUGGESTED, source, source
    tx.suggestion_confidence = None if confidence is None else Decimal(f"{confidence:.4f}")
    tx.suggestion_candidates = tx.suggestion_reason = None


def _apply_unless_user_differs(tx, code, source, confidence, past, top):
    """사용자가 같은 가게를 전에 다른 카테고리로 골랐으면('앞으로도 적용' 아님) 자동 적용하지 않고 묻는다.
    사용자 수정이 반복되는 가게 = 동명 충돌 후보 (분류 워크플로 10/26 4장). 지난 선택을 첫 후보로"""
    if past and past != code:
        candidates = [(past, None)] + [(c, p) for c, p in top if c != past]
        _review(tx, candidates[:3], USER_DIFFERS_REASON, source)
    else:
        _apply(tx, code, source, confidence)


def _review(tx, top, reason, source):
    """'확인 필요'로 두고 후보를 저장한다. top = [(코드, 확률 또는 None), ...]"""
    tx.confirmed_category_id, tx.confirmation_state, tx.classification_source = "UNCLASSIFIED", Tx.ConfirmationState.NEEDS_REVIEW, None
    tx.suggested_category_id, tx.suggestion_source = (top[0][0], source) if top else (None, None)
    tx.suggestion_confidence = Decimal(f"{top[0][1]:.4f}") if top and top[0][1] is not None else None
    tx.suggestion_candidates = [{"category": c, "confidence": None if p is None else round(p, 4)} for c, p in top] or None
    tx.suggestion_reason = reason

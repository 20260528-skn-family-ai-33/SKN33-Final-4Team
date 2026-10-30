"""
거래 분류 에이전트 (pipeline.classify_spending 에서 부른다). 메모: docs/agents/거래분류.md

순서: 결제대행 접두어 떼기 → 1 다품목 → 2 사용자 기억 → 3 규칙·사전 → 4 SVM → 5 검색+Jev → 6 확인 필요
- 분류 기준: docs/소비카테고리_분류기준_20261020.md
- 모델 파일은 Git에 없다. 기본 위치: local_workspace/models/spending_classifier/artifacts/content_policy_svm/
  (docs/ML_MODELS.md). 다른 곳이면 .env 의 SPENDING_MODEL_PATH
- 모델 파일이 없으면 예외 → 파이프라인이 실패로 기록하고 다음 동기화 때 다시 시도한다
- 5단계는 .env 에 TYPESAFE_API_KEY · JEV_MODEL 이 있을 때만 돈다. 없으면 4단계 결과로 '확인 필요'
"""
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

from .merchant_rules import remembered_category
from .models import FinancialTransaction as Tx

logger = logging.getLogger(__name__)

ROOT = Path(settings.BASE_DIR).parent
# shortcut: 경로를 settings 가 아니라 여기서 읽는다. 통합 검수 때 settings 로 옮길지 정함
MODEL_PATH = Path(os.getenv("SPENDING_MODEL_PATH") or ROOT / "local_workspace/models/spending_classifier/artifacts/content_policy_svm/spending_category_svm.joblib")
THRESHOLD = 0.85  # 임시: 상가정보 검증에서 자동 확정 정확도 98.5%. 실거래 표본으로 다시 정함
JEV_THRESHOLD = 0.9  # 임시: Jev confidence 기준. 정답 붙인 실거래 표본으로 다시 정함

# '확인 필요' 이유 문장 (사유별 템플릿)
REVIEW_REASON = "가게 이름만으로는 카테고리를 정하기 어려워요."
MULTI_ITEM_REASON = "여러 종류의 물건을 파는 곳이라 무엇을 샀는지 골라 주세요."
NOT_FOUND_REASON = "가게 정보를 찾지 못해 카테고리를 정하지 못했어요."
UNSURE_REASON = "가게 정보를 찾았지만 카테고리를 확실히 정하지 못했어요."
LOOKUP_FAILED_REASON = "가게 정보를 확인하지 못했어요. 직접 골라 주세요."

# 결제대행(PG) 접두어: 'NICE*쿠팡' → '쿠팡'
PG_PREFIX = re.compile(r"^[^*\s]{1,20}\*\s*")

# ----- 1. 다품목 -----
# 다품목(편의점·마트·종합몰)은 정책상 사용자 선택. 과거 선택은 후보로만 보여 주고 자동 적용하지 않는다 (분류 워크플로 10/26 단계 2)
# 대상 = 분류 기준 10/20의 미분류: 편의점·슈퍼마켓·대형마트·백화점·다이소·중고·종합 온라인몰 (D1, D16)
# 브랜드는 혜리 님 multi_category_brand_candidates.csv(10/30) 기준 (한글 표기 지에스25·씨유 포함)
# shortcut: 상가 업종이 아니라 가맹점 이름으로 판별한다 (정리된 이름 기준). 5단계에서는 검색 업종으로도 판별
MULTI_ITEM_PREFIXES = (
    "gs25", "지에스25", "세븐일레븐", "이마트", "미니스톱", "홈플러스", "롯데마트", "롯데슈퍼", "노브랜드", "코스트코", "트레이더스",
    "하나로마트", "농협하나로마트", "쿠팡", "11번가", "g마켓", "지마켓", "옥션", "ssg", "위메프", "티몬", "롯데온",
    "카카오쇼핑", "갤러리아", "번개장터", "중고나라", "당근마켓",
)
# CSV: cu·씨유·매점은 단순 접두어 금지 → 이름 그대로이거나 '지점명+점'일 때만. 나머지는 이름이 정확히 같을 때만
MULTI_ITEM_PATTERNS = tuple(re.compile(p) for p in (
    r"^cu(?:$|[가-힣0-9]+(?:지점|점)$)", r"^씨유(?:$|[가-힣0-9]+(?:지점|점)$)", r"^매점(?:$|[가-힣0-9]+(?:지점|점)$)",
    r"^씨유대전$", r"^신앙촌상회$", r"^중국식품$",
))
MULTI_ITEM_KEYWORDS = ("다이소", "백화점", "b마트", "비마트", "배민스토어")  # 이름 어디에 있어도 (예: (주)아성다이소, 배민B마트 강남점)
MULTI_ITEM_SUFFIXES = ("마트", "편의점", "슈퍼", "슈퍼마켓")
# 배달 = 식비, 전자제품 = 생활용품, 버거 = 식비, OTT = 문화·여가. CSV의 아이스크림 할인점류·올리브영은 분류 기준상 식비·뷰티라 넣지 않음
NOT_MULTI_ITEM_PREFIXES = ("쿠팡이츠", "쿠팡플레이", "롯데하이마트", "하이마트", "노브랜드버거")

# ----- 3. 규칙·사전 (분류 기준 문서) -----
# OTT·음악 스트리밍 구독 = 문화·여가 (10/21 결정). 브랜드 이름으로 시작하면
SUBSCRIPTION_PREFIXES = (
    "넷플릭스", "netflix", "유튜브프리미엄", "youtubepremium", "티빙", "웨이브", "왓챠", "디즈니플러스", "쿠팡플레이",
    "멜론", "지니뮤직", "벅스", "플로", "스포티파이", "spotify", "애플뮤직",
)
# 콘텐츠 플랫폼 = 문화·여가 (D11 예외). 동명 상호에 번지지 않게 이름이 정확히 같을 때만 (분류 기준 3장)
CONTENT_PLATFORMS = {"리디", "리디북스", "레진코믹스", "레진엔터테인먼트", "봄툰", "미스터블루"}
# 배달 앱 서비스명·운영사 법인명 (검색하면 본사가 나와서 틀림). 이름이 정확히 같을 때만. B마트·배민스토어는 다품목(위)
# shortcut: 이름이 그냥 "배달의민족"이면 B마트 장보기도 식비로 들어감. 카드 명세에서 구분되는 표기가 확인되면 다품목 키워드에 추가
CORPORATE_NAMES = {"우아한형제들": "FOOD", "위대한상상": "FOOD", "배달의민족": "FOOD", "배민": "FOOD", "요기요": "FOOD"}
# '기타' = 10개 밖 소비 (분류 기준 2장). shortcut: 이름 끝·이름 안의 낱말로 판별. 5단계에서는 Jev 가 업종으로 판단
ETC_SUFFIXES = (
    "세탁소", "세탁", "빨래방", "수선", "수선실", "담배", "복권", "로또", "예식장", "웨딩홀", "장례식장", "화장장", "납골당",
    "펫샵", "애견용품", "원단",
)
ETC_KEYWORDS = ("연회비", "보험")

# ----- 5. 검색 + Jev -----
# Jev 선택지 = 10개 + 기타 + 판단 보류 (10/30 결정). 설명은 분류 기준 문서 2장
UNKNOWN = "UNKNOWN"
JEV_CRITERIA = {
    "FOOD": "식비: 식사, 배달음식, 식재료, 카페·간식, 주점, 주류 구입",
    "HOUSING_TELECOM": "주거·통신: 월세, 관리비, 공과금, 통신요금, 부동산 중개, 인테리어·청소, 고시원",
    "TRANSPORT_CAR": "교통·차량: 대중교통, 택시, 주유, 주차, 차량 정비·세차·대여, 자전거",
    "HOUSEHOLD": "생활용품: 생필품, 주방용품, 가구·가전, 전자기기·휴대폰, 기기 수리",
    "FASHION_SHOPPING": "패션·쇼핑: 의류, 신발, 가방, 액세서리, 시계·귀금속",
    "MEDICAL_HEALTH": "의료·건강: 병의원 전체(피부과·성형외과 포함), 약국, 건강보조식품, 안경, 동물병원",
    "BEAUTY": "뷰티·미용: 화장품, 미용실, 네일, 피부관리실, 가발",
    "EDUCATION": "교육: 학원(입시·외국어·음악·미술·운전 등), 독서실·스터디카페, 서점, 문구, 강의, 자격증",
    "CULTURE_LEISURE": "문화·여가: 영화, 공연, 게임, 노래방·PC방, 운동시설·운동용품, 목욕탕·사우나, 마사지, 꽃·기념품·사진촬영, OTT·음악 구독",
    "TRAVEL_LODGING": "여행·숙박: 여행상품, 호텔, 펜션, 모텔, 캠핑·글램핑",
    "ETC": "기타(10개 밖 소비): 송금·경조사(예식장·장례식장), 담배·복권, 세탁·수선, 실·원단, 반려동물 용품, 보험료·카드 연회비",
    UNKNOWN: "판단 보류: 이름과 업종만으로는 어느 카테고리인지 정할 수 없음",
}
JEV_QUESTION = "이 가맹점에서 결제한 소비의 카테고리는? 가게 업종 기준으로 고르고, 정보가 부족하면 판단 보류를 고른다."
# 검색 끝 칸 업종이 이런 곳이면 다품목 → Jev 를 부르지 않고 '확인 필요'
MULTI_ITEM_INDUSTRIES = ("편의점", "슈퍼마켓", "대형마트", "백화점", "종합쇼핑몰", "마트")
# 가게가 아닌 업종(법인명 검색 시 본사가 나옴)은 근거로 넘기지 않는다
NON_STORE_INDUSTRIES = ("소프트웨어", "애플리케이션", "금융", "은행", "증권", "기업", "회사", "사무소", "협회")


def store_name(name):
    """결제대행 접두어를 뗀 가맹점명. 없으면 None"""
    return PG_PREFIX.sub("", name or "").strip() or None


@functools.cache
def _estimator():
    bundle = joblib.load(MODEL_PATH)  # pickle 이라 팀이 공유한 파일만 둔다
    if bundle["normalization_version"] != common.NORMALIZATION_VERSION:
        raise RuntimeError("모델과 이름 정리 규칙 버전이 다릅니다")
    return bundle["estimator"]


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
    return (name.startswith(MULTI_ITEM_PREFIXES) or name.endswith(MULTI_ITEM_SUFFIXES)
            or any(keyword in name for keyword in MULTI_ITEM_KEYWORDS)
            or any(pattern.match(name) for pattern in MULTI_ITEM_PATTERNS))


def rule_category(name):
    """3단계 규칙·사전으로 정해지는 카테고리 코드. 없으면 None"""
    normalized = common.normalize_name(name)
    if transport_rule(name, "N"):  # 혜리 님 교통 적요 규칙. 'N' = 소비로 정해진 거래 (분류 대상은 이미 소비)
        return "TRANSPORT_CAR"
    if normalized in CORPORATE_NAMES:
        return CORPORATE_NAMES[normalized]
    if normalized in CONTENT_PLATFORMS or normalized.startswith(SUBSCRIPTION_PREFIXES):
        return "CULTURE_LEISURE"
    if normalized.endswith(ETC_SUFFIXES) or any(keyword in normalized for keyword in ETC_KEYWORDS):
        return "ETC"
    return None


def _get_json(url, headers):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=5) as res:
        return json.loads(res.read())


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
        if industry and (industry in MULTI_ITEM_INDUSTRIES or industry.endswith("마트")):
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
    if choice != UNKNOWN and confidence >= JEV_THRESHOLD:
        return {"apply": (choice, confidence), "record": record}
    top = sorted(((c, p) for c, p in probabilities.items() if c != UNKNOWN), key=lambda x: -x[1])[:3]
    return {"review": (top, UNSURE_REASON if found else NOT_FOUND_REASON), "record": record}


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
        code = remembered_category(user, tx.merchant_name)
        if is_multi_item(name):
            # 다품목은 SVM 도 부르지 않는다. 과거 선택이 있으면 후보로만
            _review(tx, [(code, None)] if code else [], MULTI_ITEM_REASON, Tx.ClassificationSource.RULE if code else None)
        elif code:
            # 임시 결정: 사용자 기억으로 정한 건 이 거래를 사용자가 고른 게 아니라서 rule 로 둔다
            _apply(tx, code, Tx.ClassificationSource.RULE, None)
        elif name and (rule := rule_category(name)):
            _apply(tx, rule, Tx.ClassificationSource.RULE, None)
        else:
            if name:
                for_ml.append((tx, name))
            continue
        changed.append(tx)

    for (tx, name), top in zip(for_ml, predict([name for _, name in for_ml])):
        if not top:
            continue
        code, confidence = top[0]
        if confidence >= THRESHOLD:
            _apply(tx, code, Tx.ClassificationSource.ML, confidence)
        else:
            # shortcut: 거래 잠금 안에서 외부 API 를 부른다. record 의 ms 로 시간을 재고 길면 잠금 밖으로 옮길지 팀 회의 (메모 참고)
            result = judge(name)
            if result is None:
                _review(tx, top, REVIEW_REASON, Tx.ClassificationSource.ML)
            elif "apply" in result:
                code, confidence = result["apply"]
                _apply(tx, code, Tx.ClassificationSource.LLM, confidence)
            else:
                _review(tx, *result["review"], Tx.ClassificationSource.LLM)
            if result is not None:
                tx.field_states = {**tx.field_states, "classifier": result["record"]}
        changed.append(tx)

    if changed:
        Tx.objects.bulk_update(changed, FIELDS, batch_size=500)


def _apply(tx, code, source, confidence):
    tx.confirmed_category_id = tx.suggested_category_id = code
    tx.confirmation_state, tx.classification_source, tx.suggestion_source = Tx.ConfirmationState.SUGGESTED, source, source
    tx.suggestion_confidence = None if confidence is None else Decimal(f"{confidence:.4f}")
    tx.suggestion_candidates = tx.suggestion_reason = None


def _review(tx, top, reason, source):
    """'확인 필요'로 두고 후보를 저장한다. top = [(코드, 확률 또는 None), ...]"""
    tx.confirmed_category_id, tx.confirmation_state, tx.classification_source = "UNCLASSIFIED", Tx.ConfirmationState.NEEDS_REVIEW, None
    tx.suggested_category_id, tx.suggestion_source = (top[0][0], source) if top else (None, None)
    tx.suggestion_confidence = Decimal(f"{top[0][1]:.4f}") if top and top[0][1] is not None else None
    tx.suggestion_candidates = [{"category": c, "confidence": None if p is None else round(p, 4)} for c, p in top] or None
    tx.suggestion_reason = reason

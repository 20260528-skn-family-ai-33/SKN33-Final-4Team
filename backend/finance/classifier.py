"""
거래 분류 에이전트 (pipeline.classify_spending 에서 부른다). 메모: docs/agents/거래분류.md

지금 단계: 사용자 기억 → ML(혜리 SVM). 규칙 · 검색 · Jev 는 다음 단계에서 붙인다.
- 모델 파일은 Git에 없다. 기본 위치: local_workspace/models/spending_classifier/artifacts/content_policy_svm/
  (docs/ML_MODELS.md). 다른 곳이면 .env 의 SPENDING_MODEL_PATH
- 모델 파일이 없으면 예외 → 파이프라인이 실패로 기록하고 다음 동기화 때 다시 시도한다
"""
import functools
import importlib.util
import os
from decimal import Decimal
from pathlib import Path

import joblib
import numpy as np
from django.conf import settings

from .merchant_rules import remembered_category
from .models import FinancialTransaction as Tx

ROOT = Path(settings.BASE_DIR).parent
# shortcut: 경로를 settings 가 아니라 여기서 읽는다 (config/ 공통 코드를 안 건드리려고). 통합 검수 때 settings 로 옮길지 정함
MODEL_PATH = Path(os.getenv("SPENDING_MODEL_PATH") or ROOT / "local_workspace/models/spending_classifier/artifacts/content_policy_svm/spending_category_svm.joblib")
THRESHOLD = 0.85  # 임시: 상가정보 검증에서 자동 확정 정확도 98.5%. 실거래 표본으로 다시 정함
REVIEW_REASON = "가게 이름만으로는 카테고리를 정하기 어려워요."
MULTI_ITEM_REASON = "여러 종류의 물건을 파는 곳이라 무엇을 샀는지 골라 주세요."
# 다품목(편의점·마트·종합몰)은 정책상 사용자 선택. 과거 선택은 후보로만 보여 주고 자동 적용하지 않는다 (분류 워크플로 10/26 단계 2)
# 대상 = 분류 기준 10/20의 미분류: 편의점·슈퍼마켓·대형마트·백화점·다이소·중고·종합 온라인몰 (D1, D16)
# shortcut: 상가 업종이 아니라 가맹점 이름으로 판별한다 (정리된 이름 기준). 검색 업종 연결 때 업종으로도 판별
MULTI_ITEM_PREFIXES = (
    "gs25", "cu", "세븐일레븐", "이마트", "미니스톱", "홈플러스", "롯데마트", "코스트코", "트레이더스", "하나로마트",
    "쿠팡", "11번가", "g마켓", "지마켓", "옥션", "ssg", "위메프", "티몬", "롯데온", "카카오쇼핑",
    "갤러리아", "번개장터", "중고나라", "당근마켓",
)
MULTI_ITEM_KEYWORDS = ("다이소", "백화점")  # 법인명이 붙어도 잡히게 이름 어디에 있어도 (예: (주)아성다이소)
MULTI_ITEM_SUFFIXES = ("마트", "편의점", "슈퍼", "슈퍼마켓")
NOT_MULTI_ITEM_PREFIXES = ("쿠팡이츠", "롯데하이마트", "하이마트")  # 배달 = 식비, 전자제품 = 생활용품


@functools.cache
def _common():
    # scripts/ 는 backend/ 실행 경로 밖이라 파일로 직접 불러온다. predict.py 는 학습 코드(서버에 없는 psutil)까지 import 해서 쓰지 않는다
    spec = importlib.util.spec_from_file_location("spending_common", ROOT / "scripts/spending_classifier/common.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@functools.cache
def _estimator():
    bundle = joblib.load(MODEL_PATH)  # pickle 이라 팀이 공유한 파일만 둔다
    if bundle["normalization_version"] != _common().NORMALIZATION_VERSION:
        raise RuntimeError("모델과 이름 정리 규칙 버전이 다릅니다")
    return bundle["estimator"]


def predict(names):
    """이름마다 [(카테고리 코드, 확률), ...] 확률 높은 순 3개. 정리 후 빈 이름은 []"""
    cleaned = [_common().normalize_name(name) for name in names]
    rows = [[] for _ in names]
    idx = [i for i, name in enumerate(cleaned) if name]
    if idx:
        estimator = _estimator()
        for i, p in zip(idx, estimator.predict_proba([cleaned[i] for i in idx])):
            rows[i] = [(str(estimator.classes_[k]), float(p[k])) for k in np.argsort(-p)[:3]]
    return rows


def is_multi_item(name):
    name = _common().normalize_name(name or "")
    if not name or name.startswith(NOT_MULTI_ITEM_PREFIXES):
        return False
    return (name.startswith(MULTI_ITEM_PREFIXES) or name.endswith(MULTI_ITEM_SUFFIXES)
            or any(keyword in name for keyword in MULTI_ITEM_KEYWORDS))


FIELDS = [
    "confirmed_category", "confirmation_state", "classification_source", "suggested_category",
    "suggestion_source", "suggestion_confidence", "suggestion_candidates", "suggestion_reason",
]


def classify(user, txs):
    """사용자가 확정하지 않은 소비 거래에 카테고리를 제안한다. 가맹점명이 없는 거래는 그대로 둔다"""
    todo = [
        tx for tx in txs
        if tx.transaction_type == Tx.TransactionType.SPENDING and tx.confirmation_state != Tx.ConfirmationState.CONFIRMED
    ]
    changed, for_ml = [], []
    for tx in todo:
        code = remembered_category(user, tx.merchant_name)
        if is_multi_item(tx.merchant_name):
            # 다품목은 SVM 도 부르지 않는다. 과거 선택이 있으면 후보로만
            _review(tx, [(code, None)] if code else [], MULTI_ITEM_REASON, Tx.ClassificationSource.RULE if code else None)
            changed.append(tx)
        elif code:
            # 임시 결정: 사용자 기억으로 정한 건 이 거래를 사용자가 고른 게 아니라서 rule 로 둔다
            _apply(tx, code, Tx.ClassificationSource.RULE, None)
            changed.append(tx)
        elif tx.merchant_name:
            for_ml.append(tx)

    for tx, top in zip(for_ml, predict([tx.merchant_name for tx in for_ml])):
        if not top:
            continue
        code, confidence = top[0]
        if confidence >= THRESHOLD:
            _apply(tx, code, Tx.ClassificationSource.ML, confidence)
        else:
            _review(tx, top, REVIEW_REASON, Tx.ClassificationSource.ML)
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

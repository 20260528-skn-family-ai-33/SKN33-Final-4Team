"""Load the saved joblib bundle and predict without training or compare_allocation_models.py.

Import load_bundle and predict_profile in the backend. Only load trusted local
joblib files. Input disposable_income is income minus non-consumption expenses,
not automatically the same as a salary. Amounts returned are floating-point ML
draft allocations, before LLM adjustment or final won rounding.
"""

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd


FEATURES = (
    "age", "sex", "log_disposable_income", "has_vehicle",
    "fixed_expense_amount", "living_budget_amount",
)
CATEGORIES = (
    "식비", "주거·통신", "교통·차량", "생활용품", "패션·쇼핑",
    "의료·건강", "뷰티·미용", "교육", "문화·여가", "여행·숙박",
)
INPUTS = ("age", "sex", "disposable_income", "has_vehicle",
          "fixed_expense_amount", "living_budget_amount")


def require(condition, message):
    if not bool(condition):
        raise ValueError(message)


def validate_bundle(bundle):
    require(isinstance(bundle, dict) and bundle.get("format_version") == 1,
            "지원하지 않는 모델 파일 형식입니다.")
    metadata = bundle["metadata"]
    require(metadata["model_name"] == "catboost_shallow", "모델 이름 불일치")
    require(tuple(metadata["features"]) == FEATURES, "입력 열 순서 불일치")
    require(tuple(metadata["categories"]) == CATEGORIES, "카테고리 순서 불일치")
    require(tuple(bundle["model"].feature_names_) == FEATURES, "저장 모델 입력 열 불일치")
    mean = np.asarray(bundle["fallback_ratios"], dtype=float)
    require(mean.shape == (10,) and np.isfinite(mean).all() and (mean >= 0).all(),
            "대체 평균 비율 오류")
    require(np.isclose(mean.sum(), 1, rtol=0, atol=1e-12), "대체 평균 비율 합계 오류")
    return bundle


def load_bundle(path):
    """Load a trusted artifact once on application startup, then reuse it."""
    return validate_bundle(joblib.load(Path(path)))


def profile_features(profile):
    missing = set(INPUTS) - set(profile)
    require(not missing, f"필수 입력 누락: {sorted(missing)}")
    values = {key: float(profile[key]) for key in INPUTS}
    require(np.isfinite(list(values.values())).all(), "입력에 결측·비유한 숫자가 있습니다.")
    require(values["age"].is_integer() and 20 <= values["age"] <= 34, "나이는 20~34세 정수입니다.")
    require(values["sex"] in (0, 1), "성별: 0=남, 1=여 (기존 v13의 1/2 코드와 다름)")
    require(values["has_vehicle"] in (0, 1), "자동차 보유: 0=없음, 1=있음")
    require(values["disposable_income"] > 0, "처분가능소득은 양수여야 합니다.")
    require(values["living_budget_amount"] > 0, "배분 생활비는 양수여야 합니다.")
    require(values["fixed_expense_amount"] >= 0, "고정비는 0 이상이어야 합니다.")
    values["log_disposable_income"] = float(np.log(values.pop("disposable_income")))
    frame = pd.DataFrame([values], columns=list(FEATURES))
    for column in ("sex", "has_vehicle"):
        frame[column] = frame[column].astype(int).astype(str)
    return frame


def normalize(raw, fallback):
    raw = np.asarray(raw, dtype=float)
    require(raw.shape == (1, 10) and np.isfinite(raw).all(), "모델의 예측 형식 또는 값 오류")
    ratios = np.maximum(raw[0], 0)
    used_fallback = bool(ratios.sum() <= 0)
    if used_fallback:
        ratios = np.asarray(fallback, dtype=float).copy()
    ratios /= ratios.sum()
    return ratios, used_fallback


def predict_profile(bundle, profile):
    """Predict ten draft ratios/amounts; no file writes, training, or LLM calls."""
    validate_bundle(bundle)
    frame = profile_features(profile)
    raw = np.asarray(bundle["model"].predict(frame), dtype=float)
    ratios, fallback = normalize(raw, bundle["fallback_ratios"])
    budget = float(profile["living_budget_amount"])
    return {
        "model_name": bundle["metadata"]["model_name"],
        "artifact_id": bundle["metadata"]["artifact_id"],
        "living_budget_amount": budget,
        "ratios": dict(zip(CATEGORIES, ratios.tolist())),
        "draft_amounts": dict(zip(CATEGORIES, (ratios * budget).tolist())),
        "used_fallback": fallback,
        "negative_clipped": bool((raw < 0).any()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True, help="저장된 budget_allocation.joblib")
    parser.add_argument("--age", type=int, required=True)
    parser.add_argument("--sex", type=int, choices=[0, 1], required=True)
    parser.add_argument("--disposable-income", type=float, required=True)
    parser.add_argument("--has-vehicle", type=int, choices=[0, 1], required=True)
    parser.add_argument("--fixed-expense-amount", type=float, required=True)
    parser.add_argument("--living-budget-amount", type=float, required=True)
    args = parser.parse_args()
    profile = {key: getattr(args, key) for key in INPUTS}
    print(json.dumps(predict_profile(load_bundle(args.model), profile), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

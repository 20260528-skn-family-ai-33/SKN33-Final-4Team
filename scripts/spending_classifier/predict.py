"""저장 모델의 상호명 단독 점검. 서비스 규칙·사전·LLM 경로를 대신하지 않는다."""
import argparse
import json
from pathlib import Path

import joblib
import numpy as np

from scripts.spending_classifier.common import CATEGORIES, CODES, NORMALIZATION_VERSION, normalize_name
from scripts.spending_classifier.train_eval import predict_probabilities


def predict(bundle, names):
    if bundle["normalization_version"] != NORMALIZATION_VERSION:
        raise ValueError("모델과 정규화 버전이 다릅니다")
    normalized = [normalize_name(name) for name in names]
    positions = [i for i, name in enumerate(normalized) if name]
    probabilities = predict_probabilities(bundle["estimator"], np.asarray([normalized[i] for i in positions]))
    rows = [{"normalized_name": name, "category": "UNCLASSIFIED", "reason": "empty_name"} for name in normalized]
    for i, p in zip(positions, probabilities):
        order = np.argsort(-p, kind="stable")
        rows[i] = {"normalized_name": normalized[i], "category": CODES[order[0]],
                   "category_name": CATEGORIES[CODES[order[0]]], "confidence": float(p[order[0]]),
                   "margin": float(p[order[0]] - p[order[1]]),
                   "probabilities": {code: float(value) for code, value in zip(CODES, p)},
                   "passes_experimental_threshold": bool(bundle["threshold"] is not None and p[order[0]] >= bundle["threshold"]),
                   "scope": "ML 단독 실험 출력; 서비스 확정 아님"}
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--names", nargs="+", required=True)
    args = parser.parse_args()
    print(json.dumps(predict(joblib.load(args.model), args.names), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

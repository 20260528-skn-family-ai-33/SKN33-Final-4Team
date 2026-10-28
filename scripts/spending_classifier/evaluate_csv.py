"""라벨 CSV와 저장 모델의 예측 비교. 학습·라벨 수정·기준선 튜닝은 하지 않는다."""
import argparse
from collections import Counter
from datetime import datetime
import math
from pathlib import Path
import re

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import classification_report, confusion_matrix
from threadpoolctl import threadpool_limits

from scripts.spending_classifier.common import (
    CATEGORIES, CODES, NORMALIZATION_VERSION, ROOT, normalize_name, sha256, write_json, work_path,
)
from scripts.spending_classifier.predict import predict


SOURCE_COLUMNS = ["가맹점명", "등장횟수", "비소비여부", "category", "categoryName", "확신", "근거", "검토필요"]
DEFAULT_EXPERIMENT = "baseline_svm"
DEFAULT_MODEL = ROOT / "models/spending_classifier/artifacts" / DEFAULT_EXPERIMENT / "spending_category_svm.joblib"
DEFAULT_PREPARED = ROOT / "data/spending_classifier/prepared" / DEFAULT_EXPERIMENT / "prepared.csv"


def read_labels(path, encoding="utf-8-sig"):
    frame = pd.read_csv(path, encoding=encoding, dtype=str, keep_default_na=False)
    missing = set(SOURCE_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"필수 열 누락: {sorted(missing)}")
    if frame.empty:
        raise ValueError("입력 CSV에 데이터 행이 없습니다")
    frame = frame[SOURCE_COLUMNS].copy()
    frame["입력행번호"] = np.arange(2, len(frame) + 2)
    # 원본 열은 보존하고 비교용 값만 정리한다.
    frame["label"] = frame["category"].str.strip()
    frame["non_consumption"] = frame["비소비여부"].str.strip()
    frame["review"] = frame["검토필요"].str.strip()
    for field, allowed in (("non_consumption", {"Y", "N", "혼재", "확인필요"}),
                           ("review", {"Y", "N"})):
        invalid = ~frame[field].isin(allowed)
        if invalid.any():
            raise ValueError(f"{field} 값 오류, 입력 행 번호: {frame.loc[invalid, '입력행번호'].tolist()}")
    invalid = ~frame["label"].isin([*CODES, "ETC", "UNCLASSIFIED", ""])
    invalid |= frame["non_consumption"].eq("N") & frame["label"].eq("")
    for code, label in CATEGORIES.items():
        invalid |= frame["label"].eq(code) & frame["categoryName"].str.strip().ne(label)
    if invalid.any():
        raise ValueError(f"라벨 코드/이름 오류, 입력 행 번호: {frame.loc[invalid, '입력행번호'].tolist()}")
    counts = frame["등장횟수"].str.strip()
    invalid = ~counts.str.fullmatch(r"[1-9][0-9]*")
    if invalid.any():
        raise ValueError(f"등장횟수는 양의 정수여야 합니다. 입력 행 번호: {frame.loc[invalid, '입력행번호'].tolist()}")
    frame["weight"] = counts.astype("int64")
    frame["normalized"] = frame["가맹점명"].map(normalize_name)
    frame["group"] = frame.apply(evaluation_group, axis=1)
    return frame


def evaluation_group(row):
    if row["non_consumption"] == "Y":
        return "excluded_non_consumption"
    if row["non_consumption"] != "N":
        return "excluded_consumption_uncertain"
    if not row["normalized"]:
        return "excluded_empty_name"
    if row["review"] == "Y":
        return "review_required"
    if row["label"] in CODES:
        return "primary_10_categories"
    return "confirmed_outside_10_categories"


def validate_bundle(bundle):
    if bundle.get("normalization_version") != NORMALIZATION_VERSION:
        raise ValueError("모델과 코드의 정규화 버전이 다릅니다")
    if bundle.get("categories") != CATEGORIES or set(bundle["estimator"].classes_) != set(CODES):
        raise ValueError("모델의 10개 카테고리가 현행 코드와 다릅니다")
    threshold = bundle.get("threshold")
    if threshold is not None and (not math.isfinite(threshold) or not 0 <= threshold <= 1):
        raise ValueError("모델에 저장된 기준선이 유효하지 않습니다")


def add_predictions(frame, bundle):
    result = frame.copy()
    result["prediction"] = ""
    result["second_prediction"] = ""
    result["confidence"] = np.nan
    result["margin"] = np.nan
    result["accepted"] = False
    eligible = result["non_consumption"].eq("N") & result["normalized"].ne("")
    names = result.loc[eligible, "가맹점명"].tolist()
    predictions = predict(bundle, names) if names else []
    for index, prediction in zip(result.index[eligible], predictions):
        order = sorted(CODES, key=lambda code: -prediction["probabilities"][code])
        result.loc[index, ["prediction", "second_prediction", "confidence", "margin", "accepted"]] = [
            prediction["category"], order[1], prediction["confidence"], prediction["margin"],
            prediction["passes_experimental_threshold"],
        ]
    return result


def add_overlap(frame, bundle, prepared):
    result = frame.copy()
    result["reference_split"] = "not_checked"
    result["training_overlap"] = "not_checked"
    if prepared is None:
        return result, {"status": "not_checked"}
    digest = sha256(prepared)
    if digest != bundle.get("prepared_sha256"):
        raise ValueError("중복 확인용 prepared.csv가 모델의 학습 데이터와 다릅니다")
    candidate = bundle.get("candidate_id", "")
    if not candidate.endswith(("exclude_brands", "include_brands")):
        raise ValueError("최종 모델의 브랜드 제외 조건을 확인할 수 없습니다")
    exclude_brands = candidate.endswith("exclude_brands")
    target_names = set(result["normalized"]) - {""}
    matches = {}
    for chunk in pd.read_csv(prepared, dtype=str, keep_default_na=False, chunksize=100000,
                             usecols=["name", "split", "brand_candidate"]):
        matched = chunk.loc[chunk["name"].isin(target_names), ["name", "split", "brand_candidate"]]
        for name, split, brand in matched.itertuples(index=False, name=None):
            if name in matches:
                raise ValueError("prepared.csv에 중복 정규화 이름이 있습니다")
            if split not in {"train", "valid", "test"} or brand.lower() not in {"true", "false", "1", "0"}:
                raise ValueError("prepared.csv의 분할/브랜드 표기가 유효하지 않습니다")
            used = split == "train" and not (exclude_brands and brand.lower() in {"true", "1"})
            matches[name] = (split, "seen_in_final_train" if used else "not_in_final_train")
    result["reference_split"] = result["normalized"].map(lambda name: matches.get(name, ("absent", "not_in_final_train"))[0])
    result["training_overlap"] = result["normalized"].map(lambda name: matches.get(name, ("absent", "not_in_final_train"))[1])
    result.loc[result["normalized"].eq(""), ["reference_split", "training_overlap"]] = "not_applicable"
    return result, {"status": "checked", "path": str(prepared.resolve()), "sha256": digest,
                    "method": "동일 정규화 이름의 일치만 확인. 같은 브랜드의 지점·변형 이름은 판별하지 않음"}


def scores(frame, threshold):
    count = len(frame)
    result = {"rows": count, "occurrences": int(frame["weight"].sum()),
              "normalized_unique_names": int(frame["normalized"].nunique())}
    if not count:
        return {**result, "accuracy": None, "macro_f1_10": None, "macro_f1_supported": None,
                "top2_accuracy": None, "occurrence_weighted_accuracy": None,
                "at_saved_threshold": {"threshold": threshold, "accepted_rows": 0,
                                       "coverage": None, "accuracy": None}, "per_category": {}}
    truth = frame["label"]
    predicted = frame["prediction"]
    report = classification_report(truth, predicted, labels=CODES, output_dict=True, zero_division=0)
    supported = [code for code in CODES if report[code]["support"] > 0]
    correct = truth.eq(predicted)
    accepted = frame["accepted"]
    result.update({
        "accuracy": float(correct.mean()),
        "macro_f1_10": float(np.mean([report[code]["f1-score"] for code in CODES])),
        "macro_f1_supported": float(np.mean([report[code]["f1-score"] for code in supported])),
        "supported_category_count": len(supported),
        "top2_accuracy": float((correct | truth.eq(frame["second_prediction"])).mean()),
        "occurrence_weighted_accuracy": float(np.average(correct, weights=frame["weight"])),
        "at_saved_threshold": {
            "threshold": threshold, "accepted_rows": int(accepted.sum()),
            "coverage": float(accepted.mean()) if threshold is not None else None,
            "accuracy": float(correct[accepted].mean()) if accepted.any() else None,
        },
        "per_category": {code: report[code] for code in CODES},
        "confusion_matrix": confusion_matrix(truth, predicted, labels=CODES).tolist(),
        "confusion_matrix_label_order": CODES,
    })
    return result


def outside_scores(frame, threshold):
    count = len(frame)
    return {"rows": count, "threshold": threshold,
            "false_accept_count": int(frame["accepted"].sum()) if threshold is not None else None,
            "false_accept_rate": float(frame["accepted"].mean()) if count and threshold is not None else None}


def summarize(frame, bundle):
    primary = frame.loc[frame["group"].eq("primary_10_categories")]
    outside = frame.loc[frame["group"].eq("confirmed_outside_10_categories")]
    threshold = bundle["threshold"]
    summary = {
        "input_rows": len(frame), "groups": dict(Counter(frame["group"])),
        "primary": scores(primary, threshold),
        "primary_by_training_overlap": {
            key: scores(primary.loc[primary["training_overlap"].eq(key)], threshold)
            for key in ("seen_in_final_train", "not_in_final_train", "not_checked")
        },
        "primary_absent_from_original_train_valid_test": scores(
            primary.loc[primary["reference_split"].eq("absent")], threshold),
        "confirmed_outside_10": {
            code: outside_scores(outside.loc[outside["label"].eq(code)], threshold)
            for code in ("ETC", "UNCLASSIFIED")
        },
        "notes": [
            "주지표: 비소비여부=N, 검토필요=N, 유효한 상호명과 10개 라벨인 CSV 행 기준.",
            "Codex가 부여한 잠정 라벨과의 일치율이며 사람의 독립 검수가 완료된 정답 성능이 아님.",
            "검토필요=Y와 비소비·소비여부 불명 행은 주지표에서 제외. 검토필요=Y인 미분류도 오확정률 분모에서 제외.",
            "등장횟수 가중 정확도는 해당 횟수가 같은 소비 거래를 대표한다는 가정의 참고값. 금액 가중 성능이 아님.",
            "Macro-F1(10개 고정)은 정답·예측이 모두 없는 카테고리를 0으로 포함. 정답 표본 있는 카테고리 평균도 별도 제공.",
            "기준선은 모델에 저장된 값을 사용. 이 CSV로 기준선을 선택하거나 모델을 학습하지 않음.",
            "기타·미분류 오확정률은 검토불필요로 표시된 해당 라벨 행 중 10개 예측이 기준선을 넘는 비율.",
            "규칙·상가사전·LLM이 없는 ML 단독 평가. 기준선 통과율은 서비스 전체 자동 처리율이 아님.",
        ],
    }
    return summary


def excel_safe(value):
    if isinstance(value, str) and re.match(r"^[\s]*[=+@-]", value):
        return "'" + value
    return value


def write_outputs(directory, frame, summary):
    directory = work_path(directory)
    # 기존 실행 폴더를 덮어쓰지 않으며 원본 CSV는 읽기 전용으로 사용한다.
    directory.mkdir(parents=True, exist_ok=False)
    details = frame[SOURCE_COLUMNS + ["입력행번호"]].copy()
    mapping = {"normalized": "상호명_정규화", "group": "평가구분", "prediction": "ML예측코드",
               "second_prediction": "ML2순위코드", "confidence": "ML확신도", "margin": "ML1_2순위차이",
               "accepted": "저장기준선통과", "reference_split": "기존데이터분할", "training_overlap": "최종학습중복"}
    for field, title in mapping.items():
        details[title] = frame[field]
    details["ML예측카테고리"] = frame["prediction"].map(CATEGORIES).fillna("")
    details["주평가정답일치"] = [str(label == prediction) if group == "primary_10_categories" else ""
                               for label, prediction, group in zip(frame["label"], frame["prediction"], frame["group"])]
    details = details.map(excel_safe)
    details.to_csv(directory / "predictions.csv", index=False, encoding="utf-8-sig")
    error_mask = frame["group"].eq("primary_10_categories") & frame["label"].ne(frame["prediction"])
    details.loc[error_mask].to_csv(directory / "errors.csv", index=False, encoding="utf-8-sig")
    per_category = summary["primary"]["per_category"]
    pd.DataFrame([{"category": code, "categoryName": CATEGORIES[code], **per_category[code]}
                  for code in CODES if code in per_category],
                 columns=["category", "categoryName", "precision", "recall", "f1-score", "support"]
                 ).to_csv(directory / "category_metrics.csv", index=False, encoding="utf-8-sig")
    write_json(directory / "metrics.json", summary)


def format_score(value):
    return "평가 불가(해당 표본/기준선 없음)" if value is None else f"{value:.2%}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data/spending_classifier/evaluation/transaction_labels_v1.csv")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--prepared", type=Path, help="중복 확인용 prepared.csv. 생략하면 기존 1차 실험 파일을 사용")
    parser.add_argument("--output", type=Path, help="새 결과 폴더. 기본: reports/spending_classifier/baseline_svm_transaction_evaluation")
    parser.add_argument("--encoding", default="utf-8-sig")
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    if args.threads < 1:
        parser.error("threads는 1 이상이어야 합니다")
    output = work_path(args.output or ROOT / "reports/spending_classifier/baseline_svm_transaction_evaluation")
    if output.exists():
        parser.error("결과 폴더가 이미 있습니다. 새 --output 경로를 지정하세요")
    print("라벨 CSV와 저장 모델을 읽습니다.", flush=True)
    input_digest = sha256(args.input)
    model_digest = sha256(args.model)
    frame = read_labels(args.input, args.encoding)
    bundle = joblib.load(args.model)
    validate_bundle(bundle)
    print("상호명만 입력해 예측합니다. 기준선은 저장된 값을 사용합니다.", flush=True)
    with threadpool_limits(limits=args.threads):
        frame = add_predictions(frame, bundle)
    prepared = args.prepared or (DEFAULT_PREPARED if DEFAULT_PREPARED.exists() else None)
    print("원래 학습·검증·테스트 데이터와 이름 중복을 확인합니다.", flush=True)
    frame, overlap = add_overlap(frame, bundle, prepared)
    summary = summarize(frame, bundle)
    if sha256(args.input) != input_digest or sha256(args.model) != model_digest:
        raise RuntimeError("평가 중 입력 CSV 또는 모델이 변경됐습니다. 결과를 저장하지 않습니다")
    summary.update({"input_path": str(args.input.resolve()), "input_sha256": input_digest,
                    "model_path": str(args.model.resolve()), "model_sha256": model_digest,
                    "candidate_id": bundle["candidate_id"], "normalization_version": NORMALIZATION_VERSION,
                    "overlap_check": overlap, "finished_at": datetime.now().astimezone().isoformat()})
    write_outputs(output, frame, summary)
    primary = summary["primary"]
    print(f"주평가 대상: {primary['rows']}행 / 전체 {len(frame)}행")
    print(f"정확도: {format_score(primary['accuracy'])}")
    print(f"Macro-F1 (10개 고정): {format_score(primary['macro_f1_10'])}")
    print(f"Macro-F1 (정답 표본 있는 카테고리): {format_score(primary['macro_f1_supported'])}")
    print(f"Top-2 정확도: {format_score(primary['top2_accuracy'])}")
    print(f"등장횟수 가중 정확도: {format_score(primary['occurrence_weighted_accuracy'])}")
    at_threshold = primary["at_saved_threshold"]
    print(f"저장 기준선: {at_threshold['threshold']} / 통과율: {format_score(at_threshold['coverage'])}")
    print(f"통과 행 정확도: {format_score(at_threshold['accuracy'])}")
    for code, item in summary["confirmed_outside_10"].items():
        print(f"{code} 오확정률: {format_score(item['false_accept_rate'])} / 분모 {item['rows']}행")
    print("참고: Codex 잠정 라벨과의 일치율입니다. 검토필요 행은 주평가에서 제외했습니다.")
    print(f"결과: {output.resolve()}")


if __name__ == "__main__":
    main()

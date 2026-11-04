"""기존 전체 SVM/BERT 저장 예측에 동일 교통 규칙 적용. 모델 호출·학습·LLM 호출 없음."""
import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.spending_classifier.common import CATEGORIES, CODES, NORMALIZATION_VERSION, ROOT, sha256, write_json, work_path
from scripts.spending_classifier.evaluate_csv import SOURCE_COLUMNS, excel_safe, read_labels, scores
from scripts.spending_classifier.transport_rules import EXACT_NAMES, RULE_VERSION, transport_rule


DEFAULT_SVM_RESULTS = ROOT / "reports/spending_classifier/baseline_svm_transaction_evaluation"
DEFAULT_BERT_RESULTS = ROOT / "reports/spending_classifier/comparisons/svm_bert_comparison/seed_42/bert_base_data"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def checked_threshold(value):
    if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)
                              or not math.isfinite(value) or not 0 <= value <= 1):
        raise ValueError("저장 기준선 오류")
    return value


def read_saved_predictions(frame, path, mapping, threshold):
    saved = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    needed = set(SOURCE_COLUMNS + ["입력행번호", *mapping.values()])
    if not needed.issubset(saved.columns) or len(saved) != len(frame):
        raise ValueError(f"저장 예측의 열/행 수 불일치: {path}")
    # 결과 CSV의 Excel 수식 방지 이스케이프까지 고려하여 입력 행을 정확히 대조한다.
    columns = SOURCE_COLUMNS + ["입력행번호"]
    expected = frame[columns].map(excel_safe).astype(str).reset_index(drop=True)
    if not saved[columns].reset_index(drop=True).equals(expected):
        raise ValueError(f"저장 예측과 원본 입력의 내용/순서 불일치: {path}")
    result = frame.copy()
    for field in ("prediction", "second_prediction"):
        result[field] = saved[mapping[field]].to_numpy()
    result["confidence"] = pd.to_numeric(saved[mapping["confidence"]].replace("", np.nan), errors="raise")
    flags = saved[mapping["accepted"]]
    if not flags.isin(["True", "False"]).all():
        raise ValueError("저장된 기준선 통과 표기 오류")
    result["accepted"] = flags.eq("True")
    eligible = result["non_consumption"].eq("N") & result["normalized"].ne("")
    if not result.loc[eligible, "prediction"].isin(CODES).all() or not result.loc[eligible, "second_prediction"].isin(CODES).all():
        raise ValueError("저장 예측에 유효한 10개 라벨이 없습니다")
    confidence = result.loc[eligible, "confidence"]
    if not np.isfinite(confidence).all() or not confidence.between(0, 1).all():
        raise ValueError("저장 확신도 오류")
    expected_flags = eligible & (result["confidence"].ge(threshold) if threshold is not None else False)
    if not result["accepted"].equals(expected_flags):
        raise ValueError("저장 예측의 통과 여부가 고정 기준선과 다릅니다")
    return result


def verify_baseline(frame, threshold, recorded):
    actual = scores(frame.loc[frame["group"].eq("primary_10_categories")], threshold)
    for key in ("rows", "accuracy", "macro_f1_10", "macro_f1_supported"):
        left, right = actual[key], recorded[key]
        if left is None or right is None:
            same = left is None and right is None
        else:
            same = math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-10)
        if not same:
            raise ValueError(f"저장 예측과 기존 metrics 불일치: {key}")
    for key in ("accepted_rows", "coverage", "accuracy"):
        left, right = actual["at_saved_threshold"][key], recorded["at_saved_threshold"][key]
        if (left is None) != (right is None) or (left is not None and not math.isclose(left, right, abs_tol=1e-10)):
            raise ValueError(f"저장 통과 지표 불일치: {key}")


def apply_rules(frame):
    result = frame.copy()
    for field in ("prediction", "second_prediction", "confidence", "accepted"):
        result[f"ml_{field}"] = result[field]
    result["rule_id"] = [transport_rule(name, state) for name, state in
                         zip(result["가맹점명"], result["non_consumption"])]
    matched = result["rule_id"].ne("")
    result.loc[matched, "prediction"] = "TRANSPORT_CAR"
    result.loc[matched, "accepted"] = True
    # 규칙에는 모델 확률을 붙이지 않는다. 원래 확률은 ml_confidence에만 남긴다.
    result.loc[matched, "confidence"] = np.nan
    result.loc[matched, "second_prediction"] = ""
    eligible = result["non_consumption"].eq("N") & result["normalized"].ne("")
    result["route"] = "excluded"
    result.loc[eligible & ~result["accepted"], "route"] = "llm_candidate"
    result.loc[eligible & result["accepted"], "route"] = "ml_auto"
    result.loc[matched, "route"] = "transport_rule"
    return result


def measure(frame, threshold, with_rules=False):
    metric = scores(frame, threshold)
    # 규칙 자동 확정과 ML 기준선 통과를 합친 값이므로 순수 ML 기준선 성능과 구분한다.
    metric.pop("at_saved_threshold")
    if with_rules:
        # 규칙에는 2순위가 없으므로 결합 방식의 Top-2 정확도는 보고하지 않는다.
        metric.pop("top2_accuracy", None)
    accepted = frame["accepted"]
    correct = frame["prediction"].eq(frame["label"])
    rule = frame["rule_id"].ne("") if "rule_id" in frame else pd.Series(False, index=frame.index)
    metric["decision"] = {
        "ml_threshold": threshold, "auto_rows": int(accepted.sum()),
        "auto_coverage": float(accepted.mean()) if len(frame) else None,
        "auto_accuracy": float(correct[accepted].mean()) if accepted.any() else None,
        "auto_wrong_rows": int((accepted & ~correct).sum()),
        "llm_candidate_rows": int((~accepted).sum()),
        "llm_candidate_rate": float((~accepted).mean()) if len(frame) else None,
        "rule_rows": int(rule.sum()), "rule_wrong_rows": int((rule & ~correct).sum()),
    }
    return metric


def evaluate_stages(before, after, threshold, shared_unseen):
    primary = before["group"].eq("primary_10_categories")
    unmatched = primary & after["rule_id"].eq("")
    result = {}
    for stage, frame in (("ml_only", before), ("transport_rules_plus_ml", after)):
        result[stage] = {"primary": measure(frame.loc[primary], threshold, stage != "ml_only"),
                         "same_rule_unmatched_primary": measure(frame.loc[unmatched], threshold, stage != "ml_only"),
                         "shared_absent_from_old_data_primary": measure(frame.loc[primary & shared_unseen], threshold, stage != "ml_only")}
        outside = frame["group"].eq("confirmed_outside_10_categories")
        result[stage]["outside_10"] = {}
        for code in ("ETC", "UNCLASSIFIED"):
            subset = frame.loc[outside & frame["label"].eq(code)]
            result[stage]["outside_10"][code] = {
                "rows": len(subset), "false_auto_rows": int(subset["accepted"].sum()),
                "false_auto_rate": float(subset["accepted"].mean()) if len(subset) else None,
            }
    was_right = before["prediction"].eq(before["label"])
    now_right = after["prediction"].eq(after["label"])
    result["rule_changes"] = {
        "fixed_primary_rows": int((primary & ~was_right & now_right).sum()),
        "harmed_primary_rows": int((primary & was_right & ~now_right).sum()),
        "matched_all_rows": int(after["rule_id"].ne("").sum()),
        "rules_by_id": after.loc[after["rule_id"].ne(""), "rule_id"].value_counts().to_dict(),
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data/spending_classifier/evaluation/transaction_labels_v1.csv")
    parser.add_argument("--svm-results", type=Path, default=DEFAULT_SVM_RESULTS)
    parser.add_argument("--bert-results", type=Path, default=DEFAULT_BERT_RESULTS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = work_path(args.output or ROOT / "reports/spending_classifier/svm_bert_transport_rule_comparison")
    if output.exists():
        parser.error("결과 폴더가 이미 있습니다. 새 --output 경로를 지정하세요")
    svm_metric_path = args.svm_results / "metrics.json"
    bert_metric_path = args.bert_results / "metrics.json"
    svm_meta, bert_meta = map(read_json, [svm_metric_path, bert_metric_path])
    manifest_path = Path(bert_meta["config"]["data"]) / "manifest.json"
    svm_pred = args.svm_results / "predictions.csv"
    bert_pred = args.bert_results / "predictions.csv"
    inputs = {str(path.resolve()): sha256(path) for path in
              [args.input, svm_metric_path, bert_metric_path, manifest_path, svm_pred, bert_pred]}
    manifest = read_json(manifest_path)
    digest = inputs[str(args.input.resolve())]
    if svm_meta["input_sha256"] != digest or digest not in manifest["inputs"].values():
        raise ValueError("동일한 입력으로 평가한 저장 결과가 아닙니다. 원래 CSV와 결과 경로를 확인하세요")
    if bert_meta["status"] != "complete" or bert_meta["arm"] != "bert_base_data":
        raise ValueError("완료된 BERT C 결과가 필요합니다")
    if bert_meta["config"]["manifest_sha256"] != sha256(manifest_path):
        raise ValueError("BERT 비교 데이터 manifest가 변경됐습니다")
    if svm_meta["normalization_version"] != NORMALIZATION_VERSION or manifest["normalization_version"] != NORMALIZATION_VERSION:
        raise ValueError("정규화 버전 불일치")
    if manifest["categories"] != CATEGORIES or bert_meta["fit"]["categories"] != CATEGORIES:
        raise ValueError("카테고리 기준 불일치")
    frame = read_labels(args.input)
    svm_threshold = checked_threshold(svm_meta["primary"]["at_saved_threshold"]["threshold"])
    bert_threshold = checked_threshold(bert_meta["evaluation"]["primary"]["at_saved_threshold"]["threshold"])
    svm = read_saved_predictions(frame, svm_pred, {
        "prediction": "ML예측코드", "second_prediction": "ML2순위코드", "confidence": "ML확신도", "accepted": "저장기준선통과"}, svm_threshold)
    bert = read_saved_predictions(frame, bert_pred, {key: key for key in
                                  ("prediction", "second_prediction", "confidence", "accepted")}, bert_threshold)
    verify_baseline(svm, svm_threshold, svm_meta["primary"])
    verify_baseline(bert, bert_threshold, bert_meta["evaluation"]["primary"])
    svm_saved = pd.read_csv(svm_pred, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    bert_saved = pd.read_csv(bert_pred, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    if not bert_saved["old_data_overlap"].isin(["True", "False"]).all():
        raise ValueError("BERT 기존 데이터 중복 표기 오류")
    shared_unseen = svm_saved["기존데이터분할"].eq("absent") & bert_saved["old_data_overlap"].eq("False")
    metrics, details, table, category_table = {}, {}, [], []
    for name, before, threshold in (("existing_full_svm", svm, svm_threshold), ("bert_C", bert, bert_threshold)):
        after = apply_rules(before)
        details[name] = after
        metrics[name] = evaluate_stages(before, after, threshold, shared_unseen)
        for stage in ("ml_only", "transport_rules_plus_ml"):
            for scope in ("primary", "same_rule_unmatched_primary", "shared_absent_from_old_data_primary"):
                value = metrics[name][stage][scope]
                table.append({"model": name, "stage": stage, "scope": scope,
                              **{key: value[key] for key in ("rows", "accuracy", "macro_f1_10", "macro_f1_supported")},
                              **value["decision"]})
            for code, value in metrics[name][stage]["primary"]["per_category"].items():
                category_table.append({"model": name, "stage": stage, "category": code,
                                       "category_name": CATEGORIES[code], **value})
    if not details["existing_full_svm"]["rule_id"].equals(details["bert_C"]["rule_id"]):
        raise ValueError("두 모델의 교통 규칙 적용 행이 다릅니다")
    summary = {
        "scope": "development_posthoc_rules", "input_rows": len(frame),
        "evaluation_groups": frame["group"].value_counts().to_dict(),
        "rule_version": RULE_VERSION, "rule_source_sha256": sha256(Path(__file__).with_name("transport_rules.py")),
        "comparison_source_sha256": sha256(Path(__file__)), "rule_exact_names": EXACT_NAMES,
        "inputs": inputs, "models": metrics,
        "source_models": {"existing_full_svm": {"path": svm_meta["model_path"], "sha256": svm_meta["model_sha256"]},
                          "bert_C": {"files": bert_meta["model_files"], "train_rows": bert_meta["fit"]["train_rows"]}},
        "notes": [
            "기존 전체 SVM과 2만 행 BERT의 저장 예측 비교. 학습량·보정 방식이 같지 않아 순수 모델 구조 효과로 해석하지 않음.",
            "개발 평가 자료를 보고 만든 규칙이므로 독립 테스트 성능이 아님. 잠정 라벨 기준.",
            "모델 재학습·재추론·LLM 호출·기준선 재선정 없음. 기존 저장 기준선 그대로 사용.",
            "전체 정확도/F1은 규칙 우선 + 미매칭 ML 1순위 후보의 성능. 최종 자동 확정 성능은 decision 참조.",
            "규칙 미매칭 부분집합은 두 모델에 동일. 교통 정답 전체를 제외한 지표와 다름.",
            "LLM 전달 후보 수는 채점 대상 중 자동 확정되지 않은 수. 서비스 전체 호출량이 아님.",
            "검토필요는 평가 제외에만 사용. 규칙에는 상호명과 소비 여부만 입력.",
            "비소비/소비불명 제외는 입력 파일의 표기를 사용. 실제 서비스 거래 상태 판별·중복 제거는 구현하지 않음.",
            "티머니·카드사 이름만 있는 적요는 교통 규칙으로 확정하지 않음. 서비스 보류 정책 전체를 구현한 것은 아님.",
            "평가 원본 및 기존 예측은 수정하지 않음. 모델 속도 비교는 재측정하지 않음.",
        ],
    }
    for path, expected in inputs.items():
        if sha256(Path(path)) != expected:
            raise ValueError("집계 도중 입력 파일이 변경됐습니다")
    output.mkdir(parents=True, exist_ok=False)
    pd.DataFrame(table).to_csv(output / "comparison.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(category_table).to_csv(output / "category_metrics.csv", index=False, encoding="utf-8-sig")
    for name, after in details.items():
        after["shared_absent_from_old_data"] = shared_unseen
        after["prediction_name"] = after["prediction"].map(CATEGORIES).fillna("")
        after.map(excel_safe).to_csv(output / f"{name}_predictions.csv", index=False, encoding="utf-8-sig")
    write_json(output / "metrics.json", summary)
    display = pd.DataFrame(table).query("scope == 'primary'")
    print(display[["model", "stage", "accuracy", "macro_f1_10", "auto_accuracy", "auto_wrong_rows", "llm_candidate_rows"]].to_string(index=False))
    print(f"교통 규칙 비교 완료: {output.resolve()}")


if __name__ == "__main__":
    main()

"""SVM 저장 예측에 교통 규칙을 적용하여 전후 평가. 학습·추론·LLM 호출 없음."""
import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

from scripts.spending_classifier.common import CATEGORIES, NORMALIZATION_VERSION, sha256, write_json, work_path
from scripts.spending_classifier.compare_transport_rules import (
    apply_rules, checked_threshold, evaluate_stages, read_json, read_saved_predictions, verify_baseline,
)
from scripts.spending_classifier.evaluate_csv import excel_safe, read_labels
from scripts.spending_classifier.transport_rules import EXACT_NAMES, RULE_VERSION


def evaluate_saved(input_path, results, output):
    output = work_path(output)
    if output.exists():
        raise FileExistsError(f"기존 결과를 덮어쓰지 않습니다: {output}")
    metric_path = results / "metrics.json"
    prediction_path = results / "predictions.csv"
    source_paths = [input_path, metric_path, prediction_path,
                    Path(__file__), Path(__file__).with_name("transport_rules.py"),
                    Path(__file__).with_name("compare_transport_rules.py"),
                    Path(__file__).with_name("evaluate_csv.py")]
    hashes = {str(path.resolve()): sha256(path) for path in source_paths}
    meta = read_json(metric_path)
    if meta["input_sha256"] != hashes[str(input_path.resolve())]:
        raise ValueError("평가 CSV와 저장 예측의 입력 해시가 다릅니다. 보정된 평가 CSV를 지정하세요")
    if meta["normalization_version"] != NORMALIZATION_VERSION:
        raise ValueError("정규화 버전 불일치")
    if not meta.get("candidate_id", "").startswith("M2_"):
        raise ValueError("evaluate_csv로 생성한 SVM(M2) 평가 결과가 필요합니다")
    if meta.get("overlap_check", {}).get("status") != "checked":
        raise ValueError("prepared.csv와 이름 중복 확인을 완료한 평가 결과가 필요합니다")
    labels = read_labels(input_path)
    threshold = checked_threshold(meta["primary"]["at_saved_threshold"]["threshold"])
    before = read_saved_predictions(labels, prediction_path, {
        "prediction": "ML예측코드", "second_prediction": "ML2순위코드",
        "confidence": "ML확신도", "accepted": "저장기준선통과",
    }, threshold)
    verify_baseline(before, threshold, meta["primary"])
    saved = pd.read_csv(prediction_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    if "기존데이터분할" not in saved or not saved["기존데이터분할"].isin(["train", "valid", "test", "absent"]).all():
        raise ValueError("저장된 기존 데이터 분할 표기 오류")
    unseen = saved["기존데이터분할"].eq("absent")
    verify_baseline(before.loc[unseen], threshold, meta["primary_absent_from_original_train_valid_test"])
    after = apply_rules(before)
    stages = evaluate_stages(before, after, threshold, unseen)
    table, categories = [], []
    for stage in ("ml_only", "transport_rules_plus_ml"):
        # 단일 모델 평가이므로 두 모델의 공통 부분집합이라는 표현을 사용하지 않는다.
        stages[stage]["absent_from_prepared_primary"] = stages[stage].pop("shared_absent_from_old_data_primary")
        for scope in ("primary", "same_rule_unmatched_primary", "absent_from_prepared_primary"):
            value = stages[stage][scope]
            table.append({"stage": stage, "scope": scope,
                          **{key: value[key] for key in ("rows", "accuracy", "macro_f1_10", "macro_f1_supported")},
                          **value["decision"]})
        for code, value in stages[stage]["primary"]["per_category"].items():
            categories.append({"stage": stage, "category": code, "category_name": CATEGORIES[code], **value})
    after["reference_split"] = saved["기존데이터분할"]
    after["prediction_name"] = after["prediction"].map(CATEGORIES).fillna("")
    wrong = after["group"].eq("primary_10_categories") & after["prediction"].ne(after["label"])
    summary = {
        "scope": "development_posthoc_rules", "input_rows": len(labels),
        "evaluation_groups": labels["group"].value_counts().to_dict(),
        "source_model": {"path": meta["model_path"], "sha256": meta["model_sha256"],
                         "candidate_id": meta["candidate_id"]},
        "rule_version": RULE_VERSION, "rule_exact_names": EXACT_NAMES,
        "inputs": hashes, "stages": stages,
        "notes": [
            "같은 SVM 저장 예측에 기존 교통 규칙을 적용한 전후 비교. 모델 로딩·학습·추론·LLM 호출 없음.",
            "저장된 확신도 기준 유지. 규칙에는 확률을 부여하지 않으며 원래 ML 확신도는 ml_confidence에 보존.",
            "전체 정확도/F1은 미확정 ML 후보도 포함. 자동 확정 성능은 decision의 auto_accuracy/auto_wrong_rows 참조.",
            "교통 정답을 제외한 평가가 아니라 동일한 규칙 미매칭 부분집합도 별도로 평가.",
            "10개 고정 Macro-F1은 표본 없는 카테고리도 0점 포함. 정답 표본 있는 카테고리 평균 병기.",
            "이미 확인한 거래/잠정 라벨을 사용하는 개발 평가. 독립 최종 평가나 LLM 포함 서비스 성능이 아님.",
            "동명 상호 충돌 처리·콘텐츠 플랫폼 사전·결제 시각/요일/금액 판단은 적용하지 않음.",
        ],
    }
    for path, digest in hashes.items():
        if sha256(Path(path)) != digest:
            raise ValueError("집계 도중 입력 또는 평가 코드가 변경됐습니다")
    output.mkdir(parents=True, exist_ok=False)
    pd.DataFrame(table).to_csv(output / "comparison.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(categories).to_csv(output / "category_metrics.csv", index=False, encoding="utf-8-sig")
    for filename, frame in (("predictions.csv", after), ("errors.csv", after.loc[wrong]),
                            ("auto_errors.csv", after.loc[wrong & after["accepted"]])):
        frame.map(excel_safe).to_csv(output / filename, index=False, encoding="utf-8-sig")
    summary["finished_at"] = datetime.now().astimezone().isoformat()
    write_json(output / "metrics.json", summary)
    for stage, title in (("ml_only", "SVM 단독"), ("transport_rules_plus_ml", "교통 규칙 + SVM")):
        value = stages[stage]["primary"]
        decision = value["decision"]
        fmt = lambda score: "평가 불가" if score is None else f"{score:.2%}"
        print(f"{title}: 정확도 {fmt(value['accuracy'])}, Macro-F1(10개) {fmt(value['macro_f1_10'])}, "
              f"Macro-F1(표본 있는 카테고리) {fmt(value['macro_f1_supported'])}")
        print(f"  자동 확정 {decision['auto_rows']}행 / 정확도 {fmt(decision['auto_accuracy'])} / "
              f"오답 {decision['auto_wrong_rows']}행 / LLM 전달 후보 {decision['llm_candidate_rows']}행")
    changes = stages["rule_changes"]
    print(f"규칙으로 수정된 오답 {changes['fixed_primary_rows']}행 / 새로 발생한 오답 {changes['harmed_primary_rows']}행")
    print(f"교통 규칙 평가 완료: {output.resolve()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="저장 평가에 사용한 라벨 CSV")
    parser.add_argument("--results", type=Path, required=True, help="evaluate_csv의 결과 폴더")
    parser.add_argument("--output", type=Path, help="기본: 결과 폴더 옆에 실행 시각을 포함한 새 폴더")
    args = parser.parse_args()
    output = args.output or args.results.parent / "transport_rule_evaluation"
    evaluate_saved(args.input, args.results, output)


if __name__ == "__main__":
    main()

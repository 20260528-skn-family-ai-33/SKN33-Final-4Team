"""정책 보정본으로 기존 M2_C1_exclude_brands SVM을 재학습한다."""
import argparse
from datetime import datetime, timezone
import importlib.metadata
from pathlib import Path
import platform

import joblib
from threadpoolctl import threadpool_limits

from scripts.spending_classifier.common import CATEGORIES, NORMALIZATION_VERSION, ROOT, sha256, write_json, work_path
from scripts.spending_classifier.correct_policy_data import POLICY
from scripts.spending_classifier.train_eval import (
    evaluate, fit_model, load_prepared, log, predict_probabilities, select_threshold,
)


def train(args):
    directory = args.data.resolve()
    model_path = (args.model_output or ROOT / "models/spending_classifier/artifacts" / directory.name / "spending_category_svm.joblib").resolve()
    if not model_path.is_relative_to((ROOT / "models").resolve()):
        raise ValueError("모델 저장 경로는 프로젝트 models 폴더 안이어야 합니다")
    result_directory = work_path(args.output or ROOT / "reports/spending_classifier" / directory.name)
    metrics_path = result_directory / "retraining_metrics.json"
    if model_path.exists() or metrics_path.exists():
        raise FileExistsError("기존 모델/재학습 기록을 덮어쓰지 않습니다. 새 보정 출력 폴더를 사용하세요")
    frame, prep = load_prepared(directory)
    if prep.get("policy_revision") != POLICY:
        raise ValueError("correct_policy_data로 생성한 현행 정책 보정본이 필요합니다")
    for filename, key in (("evaluation_labels.csv", "evaluation_sha256"), ("policy_review.csv", "policy_review_sha256")):
        if sha256(directory / filename) != prep[key]:
            raise ValueError(f"보정 이후 파일이 바뀌었습니다: {filename}")
    if not prep.get("changed_names"):
        raise ValueError("학습 자료의 라벨 변경이 없어 재학습을 시작하지 않습니다")
    train_frame = frame.loc[frame["split"].eq("train") & ~frame["brand_candidate"]].copy()
    valid = frame.loc[frame["split"].eq("valid")].copy()
    del frame
    versions = {name: importlib.metadata.version(name) for name in ("numpy", "pandas", "scikit-learn", "joblib")}
    versions["python"] = platform.python_version()
    config = {key: getattr(args, key) for key in (
        "folds", "seed", "max_iter", "threads", "cv_jobs", "target_accuracy", "min_accepted",
    )}
    config.update({"kind": "M2", "C": 1.0, "exclude_brands": True, "sampling": "none; all eligible train"})
    metrics = {"status": "training", "started_at": datetime.now(timezone.utc).isoformat(),
               "policy_revision": POLICY, "config": config, "versions": versions,
               "prepared_sha256": prep["prepared_sha256"], "split_ids_sha256": prep["split_ids_sha256"],
               "evaluation_scope": "development; previously inspected data",
               "external_evaluation": "not_run", "test_evaluation": "not_run"}
    model_path.parent.mkdir(parents=True, exist_ok=True)
    result_directory.mkdir(parents=True, exist_ok=True)
    write_json(metrics_path, metrics)
    log(f"정책 보정 SVM 재학습: train={len(train_frame):,}, valid={len(valid):,}")
    model, fit = fit_model(train_frame, "M2", 1.0, args)
    with threadpool_limits(limits=args.threads):
        probabilities = predict_probabilities(model, valid["name"].to_numpy())
    validation = evaluate(valid, probabilities)
    threshold = select_threshold(validation["thresholds"], args.target_accuracy, args.min_accepted)
    if sha256(directory / "prepared.csv") != prep["prepared_sha256"] or sha256(directory / "split_ids.csv") != prep["split_ids_sha256"]:
        raise RuntimeError("학습 도중 준비 데이터가 변경되어 모델을 저장하지 않습니다")
    bundle = {"estimator": model, "categories": CATEGORIES, "normalization_version": NORMALIZATION_VERSION,
              "threshold": threshold, "threshold_scope": "weak_label_validation_only_not_production",
              "candidate_id": "M2_C1_exclude_brands", "prepared_sha256": prep["prepared_sha256"],
              "versions": versions, "policy_revision": POLICY}
    # 독점 생성으로 다른 실행이 만든 모델도 덮어쓰지 않는다.
    with model_path.open("xb") as stream:
        joblib.dump(bundle, stream, compress=3)
    metrics.update({"status": "complete", "finished_at": datetime.now(timezone.utc).isoformat(),
                    "fit": fit, "valid": validation, "selected_threshold": threshold,
                    "model_path": str(model_path), "model_sha256": sha256(model_path)})
    write_json(metrics_path, metrics)
    log(f"SVM 재학습 완료: {model_path}")
    log(f"검증 Macro-F1={validation['macro_f1']:.4f}, 선택 기준선={threshold}")
    if threshold is None:
        log("목표 정확도/최소 표본 조건을 충족한 기준선이 없습니다. 자동 확정은 비활성입니다.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="평가 결과 폴더. 기본: reports/spending_classifier/<데이터 폴더명>")
    parser.add_argument("--model-output", type=Path)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-iter", type=int, default=500)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--cv-jobs", type=int, default=1)
    parser.add_argument("--target-accuracy", type=float, default=0.9)
    parser.add_argument("--min-accepted", type=int, default=30)
    args = parser.parse_args()
    if min(args.max_iter, args.threads, args.cv_jobs, args.min_accepted) < 1 or args.folds < 2 or not 0 < args.target_accuracy <= 1:
        parser.error("학습/검증 인자 범위 오류")
    train(args)


if __name__ == "__main__":
    main()

"""Train the selected shallow model and save a portable joblib bundle.

Run from repository root: python -m scripts.budget_allocation.train_allocation --threads 2
Only 2019..2024 canonical data are used in fitting. No CV search or A/B/C
training is performed. No evaluation-year rows are added to training.

The default output is local_workspace/models/budget_allocation/catboost_shallow/ without overwriting
existing models/results. Saves budget_allocation.joblib, metadata.json, and
predict_allocation.py plus pinned inference requirements. The joblib contains a
plain dict with a native CatBoost estimator, metadata, and fallback ratios;
it does NOT pickle FittedBase or a class from this training script.

Before publishing the artifact name, reload and check raw predictions on all
training rows and the complete inference path on deterministic sample rows.
These are serialization/compatibility checks, not new accuracy evaluations.
This script is provided for the user to run; authoring it does not train a model.
"""

import argparse
from datetime import datetime, timezone
from importlib.metadata import version
import json
from pathlib import Path
import platform
import shutil
import uuid

import joblib
import numpy as np

from scripts.budget_allocation.compare_allocation_models import (
    CATEGORIES, DEFAULT_DATA_DIR, FEATURES, HALF_LIFE_YEARS, HERE, WORKSPACE, MAIN_FILE,
    MAPPING_FILE, SEED, catboost_features, fit_base, make_candidates,
    prepare_data, read_verified_csv, require, sha256, write_json,
)
from scripts.budget_allocation.predict_allocation import (
    CATEGORIES as INFERENCE_CATEGORIES, FEATURES as INFERENCE_FEATURES,
    load_bundle, predict_profile,
)


def verify_roundtrip(path, fitted, train):
    restored = load_bundle(path)
    x = catboost_features(train)
    np.testing.assert_allclose(restored["model"].predict(x), fitted.estimator.predict(x),
                               rtol=0, atol=1e-12, err_msg="저장 전후 예측 불일치")
    np.testing.assert_array_equal(restored["fallback_ratios"], fitted.mean)
    expected, _ = fitted.predict(train)
    indices = np.unique(np.linspace(0, len(train) - 1, min(64, len(train)), dtype=int))
    for index in indices:
        row = train.iloc[index]
        profile = {
            "age": float(row.age), "sex": int(row.sex),
            "disposable_income": float(np.exp(row.log_disposable_income)),
            "has_vehicle": int(row.has_vehicle),
            "fixed_expense_amount": float(row.fixed_expense_amount),
            "living_budget_amount": float(row.living_budget_amount),
        }
        predicted = predict_profile(restored, profile)
        ratios = np.asarray([predicted["ratios"][c] for c in CATEGORIES])
        amounts = np.asarray([predicted["draft_amounts"][c] for c in CATEGORIES])
        np.testing.assert_allclose(ratios, expected[index], rtol=0, atol=1e-12)
        np.testing.assert_allclose(amounts.sum(), profile["living_budget_amount"], rtol=1e-12, atol=1e-6)
    return {"raw_prediction_rows": len(train), "full_inference_rows": len(indices),
            "max_allowed_ratio_difference": 1e-12}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output", type=Path, default=WORKSPACE / "models/budget_allocation/catboost_shallow")
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    require(args.threads > 0, "--threads는 양수여야 합니다.")
    output = args.output.resolve()
    require(output.is_relative_to(WORKSPACE / "models"), "모델 저장 경로는 local_workspace/models 안이어야 합니다.")
    require(not output.exists(), "기존 모델을 덮어쓰지 않습니다. --output에 내용을 설명하는 새 폴더명을 지정하세요.")
    require(FEATURES == INFERENCE_FEATURES and CATEGORIES == INFERENCE_CATEGORIES,
            "학습·예측 코드의 입력/카테고리 순서가 다릅니다.")
    data_dir = args.data_dir.resolve()
    provenance = json.loads((data_dir / "processing_records.json").read_text(encoding="utf-8-sig"))
    read_verified_csv(data_dir, MAPPING_FILE, provenance)
    source = read_verified_csv(data_dir, MAIN_FILE, provenance)
    all_data = prepare_data(source, range(2019, 2026))
    train = all_data.loc[all_data.year.between(2019, 2024)].copy()
    require(set(train.year) == set(range(2019, 2025)), "2019~2024년 학습 자료가 모두 필요합니다.")
    specs, _ = make_candidates()
    specification = specs["catboost_shallow"]
    require(specification == {"kind": "catboost", "params": {
        "iterations": 200, "depth": 4, "learning_rate": 0.05,
    }}, "shallow 설정이 변경되었습니다. 선택된 모델 조건을 확인하세요.")
    print(f"Training catboost_shallow: 2019-2024, rows={len(train)}, threads={args.threads}", flush=True)
    fitted = fit_base(train, specification, args.threads)
    artifact_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    output.mkdir(parents=True, exist_ok=False)
    inference_path = HERE / "predict_allocation.py"
    metadata = {
        "model_name": "catboost_shallow", "artifact_id": artifact_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "features": list(FEATURES), "categorical_features": ["sex", "has_vehicle"],
        "categories": list(CATEGORIES), "params": fitted.estimator.get_params(),
        "training_years": list(range(2019, 2025)), "training_rows": len(train),
        "training_rows_by_year": {str(y): int(n) for y, n in train.groupby("year").size().items()},
        "training_weights": {"survey_weighted": True, "half_life_years": HALF_LIFE_YEARS,
                             "normalization": "mean_one"},
        "seed": SEED, "fallback_ratios": fitted.mean.tolist(),
        "input_contract": {
            "population": "single-person worker households, ages 20-34",
            "sex": "0=male, 1=female; converted to categorical strings",
            "has_vehicle": "0=no, 1=yes; converted to categorical strings",
            "disposable_income": "income minus non-consumption expenses, positive KRW; natural log once",
            "fixed_expense_amount": "canonical rent/water/management/fuel/telecom, nonnegative KRW",
            "living_budget_amount": "known positive living allocation total, excluding canonical fixed costs",
        },
        "postprocessing": "clip negative ratios to zero; zero sum uses weighted mean; normalize sum to one",
        "limitations": [
            "Known-total allocation model, not a predictor of total living expenses.",
            "No claim of validation for reduced budgets, LLM adjustments, or users outside the training population.",
            "2025/2026 were previously inspected retrospective evaluation data and are not included in this fit.",
            "Upstream category aggregation and 2024 missing vehicle values filled as zero are unchanged.",
        ],
        "python": platform.python_version(),
        "packages": {name: version(name) for name in ("numpy", "pandas", "catboost", "joblib", "scikit-learn")},
        "source_hashes": {str(path): sha256(path) for path in (
            data_dir / MAIN_FILE, data_dir / MAPPING_FILE, data_dir / "processing_records.json",
            HERE / "compare_allocation_models.py", Path(__file__), inference_path,
        )},
    }
    bundle = {"format_version": 1, "model": fitted.estimator,
              "metadata": metadata, "fallback_ratios": fitted.mean.tolist()}
    pending = output / "budget_allocation.pending.joblib"
    joblib.dump(bundle, pending, compress=3)
    print("Checking saved model reload and inference parity...", flush=True)
    verification = verify_roundtrip(pending, fitted, train)
    artifact = output / "budget_allocation.joblib"
    # The folder is new and the final name appears only after successful checks.
    pending.rename(artifact)
    write_json(output / "metadata.json", {
        **metadata, "serialization_verification": verification,
        "artifact_sha256": sha256(artifact), "status": "saved_and_reload_verified",
    })
    shutil.copyfile(inference_path, output / "predict_allocation.py")
    requirements = "\n".join(f"{name}=={metadata['packages'][name]}"
                             for name in ("numpy", "pandas", "catboost", "joblib")) + "\n"
    (output / "requirements-inference.txt").write_text(requirements, encoding="utf-8")
    print(f"\nSaved and verified: {artifact}")
    print(f"Metadata and inference helper: {output}")
    print("Existing models are unchanged. No backend configuration was modified.")


if __name__ == "__main__":
    main()

"""Compare adapted A/B methods with catboost_shallow on canonical ten targets.

Run: python -m scripts.budget_allocation.compare_sampling_strategies --threads 2
Uses the same environment/requirements as compare_allocation_models.py. Nothing runs on import.

A_10: permanent employees, original CatBoost settings (85 trees, depth 6).
B_10: A_10 plus canonical fixed + living expenses <= disposable income.
catboost_shallow: all canonical workers, 200 trees, depth 4.

All three use compare_allocation_models.py's six features, ten targets, training weights,
normalization and metrics. This preserves A/B's sampling strategies and model
settings, NOT the old eleven-target preprocessing: no P95 removal; income is
income minus non-consumption spending; fixed costs follow the canonical CSV.
The B filter therefore is adapted to those definitions, not old v13 net_income.
Employment codes are used ONLY to select training rows, never as model inputs.

CV validates 2022/2023/2024 after fitting earlier years. Final training uses
2019..2024. Every model sees identical unfiltered validation/evaluation rows.
2025/2026 are retrospective checks, never used to choose the winner. Existing
models and previous results are not touched. Only a fresh run directory in
reports/budget_prediction/sampling_strategy_comparison/
is written; no deployment model is saved. This file does not reuse or overwrite
the existing A/B model artifacts.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
from importlib.metadata import version
import io
import json
from pathlib import Path
import platform

import numpy as np
import pandas as pd

from .compare_allocation_models import (
    AMOUNTS, CATEGORIES, CV_YEARS, DEFAULT_DATA_DIR, FEATURES, HALF_LIFE_YEARS,
    HERE, WORKSPACE, HOLDOUT_FILE, MAIN_FILE, MAPPING_FILE, PRIMARY, SEED, fit_base,
    make_candidates, metrics, prepare_data, read_verified_csv, require, sha256,
    target_ratios, temporal_folds, write_json,
)


PACKAGE_ROOT = WORKSPACE / "models/living_expense_prediction"
MODEL_PARAMS = HERE.parent / "living_expense_prediction/config/model_params.json"
NAMES = ("A_10", "B_10", "catboost_shallow")
RAW_COLUMNS = (
    "조사연월", "가구원수", "가구구분코드", "가구주_연령", "가구주_성별코드",
    "가중값", "가구주_취업여부", "가구주_종사상지위코드",
)
LIMITATIONS = [
    "A/B 표본 선정 전략과 모델 설정을 10개 정제 자료에 적용한 비교이며, 기존 v13 전처리 전체의 재현은 아닙니다.",
    "세 모델 모두 로그 처분가능소득·자동차 보유 여부·현재 고정비 정의를 사용하며 P95 지출 제거를 하지 않습니다.",
    "B 조건의 소득은 소득−비소비지출, 생활비는 기타·자동차구입·고정비를 제외한 10개 합계입니다.",
    "2025/2026 자료는 이전에도 확인한 재평가 자료이며 독립적인 신규 테스트가 아닙니다.",
    "CV 최저 점수는 선택용 점수이며 통계적 유의성이나 실제 사용자에 대한 우위를 보장하지 않습니다.",
    "관측된 실제 생활비 총액을 입력하므로 총액 예측이나 절약 예산의 적합성을 평가하지 않습니다.",
    "가계동향 묶음 분류·기간 단위·반복 가구의 한계와 2024 자동차 결측의 0 대체가 유지됩니다.",
    "A/B와 shallow의 차이는 학습 표본과 모델 설정이 함께 달라진 결과입니다.",
]


def specifications():
    # Read the original configuration, but never import its preprocessing/trainer.
    path = MODEL_PARAMS
    original = json.loads(path.read_text(encoding="utf-8-sig"))
    expected = {
        "iterations": 85, "depth": 6, "learning_rate": 0.05,
        "loss_function": "MultiRMSE", "random_seed": SEED,
        "allow_writing_files": False,
    }
    require(all(original.get(k) == v for k, v in expected.items()),
            "기존 A/B 설정이 바뀌었습니다. 비교 조건을 검토하세요.")
    # fit_base supplies these shared parameters; the CLI controls thread count.
    shared = {"loss_function", "random_seed", "thread_count", "verbose", "allow_writing_files"}
    legacy = {"kind": "catboost", "params": {k: v for k, v in original.items() if k not in shared}}
    existing, _ = make_candidates()
    return {"A_10": legacy, "B_10": legacy, "catboost_shallow": existing["catboost_shallow"]}


def align_employment(source, raw):
    """Canonical row 2 is raw iloc[0], as specified by its preprocessing code."""
    row_numbers = pd.to_numeric(source["원본행번호"], errors="raise").to_numpy(float)
    require(np.isfinite(row_numbers).all() and (row_numbers % 1 == 0).all(), "원본 행 번호 오류")
    positions = row_numbers.astype(int) - 2
    require(((positions >= 0) & (positions < len(raw))).all(), "원본 행 번호 범위 오류")
    selected = raw.iloc[positions].reset_index(drop=True)
    source = source.reset_index(drop=True)
    selected = selected.apply(pd.to_numeric, errors="raise")
    for raw_column, values in (
        ("조사연월", source["조사연월"].to_numpy(float)),
        ("가구주_연령", source["나이"].to_numpy(float)),
        ("가구주_성별코드", source["성별"].map({"남": 1, "여": 2}).to_numpy(float)),
        ("가중값", source["가중값"].to_numpy(float)),
    ):
        np.testing.assert_allclose(selected[raw_column], values, rtol=0, atol=1e-8,
                                   err_msg=f"원본 행 연결 불일치: {raw_column}")
    require(selected["가구원수"].eq(1).all(), "원본 행 연결 불일치: 1인 가구")
    require(selected["가구구분코드"].eq(1).all(), "원본 행 연결 불일치: 근로자 가구")
    employed = selected["가구주_취업여부"]
    status = selected["가구주_종사상지위코드"]
    require(employed.isin([1, 2]).all(), "취업 여부 코드 누락 또는 오류")
    require(status.loc[employed.eq(1)].notna().all(), "취업자의 종사상지위 코드 누락")
    present = status.dropna().to_numpy(float)
    require(np.isfinite(present).all() and (present % 1 == 0).all() and (present >= 0).all(),
            "종사상지위 코드는 비음수 정수여야 합니다.")
    return employed.to_numpy(float), status.to_numpy(float)


def attach_training_fields(development, source, provenance):
    """Read only 2019..2024 raw metadata; verify both bytes and row alignment."""
    manifest = json.loads((PACKAGE_ROOT / "config" / "raw_manifest.json").read_text(encoding="utf-8-sig"))
    entries = {int(item["year"]): item for item in manifest["files"]}
    source_hashes = {item["file"]: item["sha256"] for item in provenance["source_files"]}
    result = development.copy()
    source = source.loc[source["연도"].le(2024)].reset_index(drop=True)
    ids = source["원본파일"].astype(str) + ":" + source["원본행번호"].astype(int).astype(str)
    require(result.row_id.tolist() == ids.tolist(), "정제 자료와 학습 자료 행 순서 불일치")
    result = result.reset_index(drop=True)
    result["disposable_income"] = source["처분가능소득"].to_numpy(float)
    result["employed_code"] = np.nan
    result["employment_status_code"] = np.nan
    raw_audit = []
    for year in sorted(result.year.unique()):
        entry = entries[int(year)]
        path = (PACKAGE_ROOT / entry["path"]).resolve()
        require(path.is_relative_to(PACKAGE_ROOT.resolve()), "원본 경로가 패키지 밖입니다.")
        require(sha256(path) == entry["compressed_sha256"], f"압축 원본 해시 불일치: {year}")
        with gzip.open(path, "rb") as stream:
            contents = stream.read()
        digest = hashlib.sha256(contents).hexdigest()
        require(digest == entry["source_sha256"], f"압축 해제 원본 해시 불일치: {year}")
        mask = result.year.eq(year)
        filenames = source.loc[mask, "원본파일"].unique()
        require(len(filenames) == 1 and source_hashes.get(filenames[0]) == digest,
                f"정제 자료와 패키지 원본이 다릅니다: {year}")
        raw = pd.read_csv(io.BytesIO(contents), encoding=manifest["encoding"],
                          usecols=list(RAW_COLUMNS), low_memory=False)
        employed, status = align_employment(source.loc[mask], raw)
        result.loc[mask, "employed_code"] = employed
        result.loc[mask, "employment_status_code"] = status
        raw_audit.append({"year": int(year), "file": str(filenames[0]), "sha256": digest})
        print(f"Verified employment metadata: {year}, rows={int(mask.sum())}", flush=True)
    return result, raw_audit


def training_subset(frame, name):
    require(name in NAMES, f"알 수 없는 후보: {name}")
    require(frame.year.between(2019, 2024).all(), "학습에는 2019~2024년만 허용합니다.")
    keep = pd.Series(True, index=frame.index)
    if name in ("A_10", "B_10"):
        keep &= frame.employed_code.eq(1) & frame.employment_status_code.eq(1)
    if name == "B_10":
        keep &= (frame.fixed_expense_amount + frame.living_budget_amount) <= frame.disposable_income
    selected = frame.loc[keep].copy()
    require(not selected.empty, f"{name} 학습 표본이 없습니다.")
    # Keep the same latest year for all candidates' recency weights.
    require(set(selected.year) == set(frame.year), f"{name}에서 학습 연도 전체가 제외되었습니다.")
    return selected


def run_cv(development, specs, threads):
    rows = []
    for year, full_train, validation in temporal_folds(development):
        for name in NAMES:
            train = training_subset(full_train, name)
            print(f"CV {year}: {name}, train={len(train)}, validation={len(validation)}", flush=True)
            fitted = fit_base(train, specs[name], threads)
            prediction, diagnostics = fitted.predict(validation)
            scores, _ = metrics(validation, prediction, validation.survey_weight.to_numpy(float))
            rows.append({"name": name, "validation_year": year, "train_n": len(train),
                         "available_train_n": len(full_train), **scores, **diagnostics})
    scores = pd.DataFrame(rows)
    ranking = scores.groupby("name", as_index=False).agg(
        cv_ratio_mae_pp=(PRIMARY, "mean"), cv_ratio_mae_year_std_pp=(PRIMARY, "std"),
        cv_category_macro_mae_won=("category_macro_mae_won", "mean"),
        folds=("validation_year", "nunique"),
    )
    require(ranking.folds.eq(len(CV_YEARS)).all(), "CV 연도 누락")
    baseline = float(ranking.loc[ranking.name.eq("catboost_shallow"), "cv_ratio_mae_pp"].iloc[0])
    ranking["delta_vs_shallow_pp"] = ranking.cv_ratio_mae_pp - baseline
    ranking = ranking.sort_values(["cv_ratio_mae_pp", "name"], kind="stable").reset_index(drop=True)
    ranking.insert(0, "cv_rank", np.arange(1, len(ranking) + 1))
    return scores, ranking


def evaluate(development, evaluation_sets, specs, threads, output):
    rows, category_rows, predictions, counts = [], [], [], []
    for name in NAMES:
        train = training_subset(development, name)
        for year, group in development.groupby("year"):
            counts.append({"name": name, "year": int(year), "available_n": len(group),
                           "train_n": int(train.year.eq(year).sum())})
        print(f"Final fit 2019-2024: {name}, train={len(train)}", flush=True)
        fitted = fit_base(train, specs[name], threads)
        for year, frame in evaluation_sets.items():
            require(frame.year.eq(year).all() and year in (2025, 2026), "평가 연도 오류")
            require(set(train.row_id).isdisjoint(frame.row_id), "학습·평가 행 중복")
            # Do not apply A/B training filters to this common evaluation frame.
            prediction, diagnostics = fitted.predict(frame)
            for weighting, weights in (
                ("survey_weighted", frame.survey_weight.to_numpy(float)),
                ("unweighted", np.ones(len(frame))),
            ):
                scores, categories = metrics(frame, prediction, weights)
                context = {"name": name, "year": year, "weighting": weighting}
                rows.append({**context, "train_n": len(train), **scores, **diagnostics})
                category_rows.extend({**context, **row} for row in categories)
            detail = frame[["row_id", "year", "survey_weight", "living_budget_amount"]].copy()
            detail.insert(0, "name", name)
            actual_ratio = target_ratios(frame)
            for i, category in enumerate(CATEGORIES):
                detail[f"actual_ratio_{category}"] = actual_ratio[:, i]
                detail[f"predicted_ratio_{category}"] = prediction[:, i]
                detail[f"actual_amount_{category}"] = frame[AMOUNTS[i]].to_numpy(float)
                detail[f"predicted_amount_{category}"] = prediction[:, i] * frame.living_budget_amount.to_numpy(float)
            predictions.append(detail)
    results = pd.DataFrame(rows)
    reference = results.loc[results.name.eq("catboost_shallow"),
                            ["year", "weighting", PRIMARY, "category_macro_mae_won"]].rename(columns={
                                PRIMARY: "shallow_ratio_mae_pp", "category_macro_mae_won": "shallow_mae_won"})
    results = results.merge(reference, on=["year", "weighting"], how="left", validate="many_to_one")
    results["delta_vs_shallow_pp"] = results[PRIMARY] - results.shallow_ratio_mae_pp
    results["delta_vs_shallow_won"] = results.category_macro_mae_won - results.shallow_mae_won
    for filename, table in (
        ("comparison.csv", results), ("category_metrics.csv", pd.DataFrame(category_rows)),
        ("predictions.csv", pd.concat(predictions, ignore_index=True)),
        ("training_counts.csv", pd.DataFrame(counts)),
    ):
        table.to_csv(output / filename, index=False, encoding="utf-8-sig")
    return results


def main():
    global PACKAGE_ROOT
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--package-root", type=Path, default=PACKAGE_ROOT,
                        help="External v13 data package containing config/raw_manifest.json")
    parser.add_argument("--output", type=Path, default=WORKSPACE / "reports/budget_prediction/sampling_strategy_comparison")
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    PACKAGE_ROOT = args.package_root.resolve()
    require(args.threads > 0, "--threads는 양수여야 합니다.")
    data_dir = args.data_dir.resolve()
    provenance = json.loads((data_dir / "processing_records.json").read_text(encoding="utf-8-sig"))
    read_verified_csv(data_dir, MAPPING_FILE, provenance)
    source = read_verified_csv(data_dir, MAIN_FILE, provenance)
    main_data = prepare_data(source, range(2019, 2026))
    require(set(main_data.year) == set(range(2019, 2026)), "2019~2025년 자료가 모두 필요합니다.")
    development, raw_audit = attach_training_fields(main_data.loc[main_data.year.le(2024)], source, provenance)
    specs = specifications()
    output = args.output.resolve()
    require(not output.is_relative_to(WORKSPACE / "output"), "output은 문서 전용입니다. 평가 결과는 reports에 저장하세요.")
    output.mkdir(parents=True, exist_ok=False)
    print(f"Output: {output}", flush=True)
    protocol = {
        "categories": list(CATEGORIES), "features": list(FEATURES), "specifications": specs,
        "sampling": {"A_10": "employed=1 and status=1", "B_10": "A_10 and fixed+living<=disposable_income",
                     "catboost_shallow": "all canonical workers"},
        "selection_metric": "equal-year mean of survey-weighted ten-category ratio MAE (%p)",
        "validation_years": list(CV_YEARS), "final_fit_years": list(range(2019, 2025)),
        "evaluation_years": [2025, 2026], "evaluation_role": "retrospective_only_not_for_selection",
        "training_half_life_years": HALF_LIFE_YEARS, "seed": SEED, "threads": args.threads,
        "raw_sources": raw_audit, "limitations": LIMITATIONS,
        "hashes": {str(path): sha256(path) for path in (
            Path(__file__), HERE / "compare_allocation_models.py", MODEL_PARAMS,
            PACKAGE_ROOT / "config" / "raw_manifest.json", data_dir / MAIN_FILE,
            data_dir / MAPPING_FILE, data_dir / "processing_records.json",
        )},
        "python": platform.python_version(),
        "packages": {name: version(name) for name in ("numpy", "pandas", "scikit-learn", "catboost")},
    }
    write_json(output / "protocol.json", protocol)
    scores, ranking = run_cv(development, specs, args.threads)
    scores.to_csv(output / "cv_scores.csv", index=False, encoding="utf-8-sig")
    ranking.to_csv(output / "cv_ranking.csv", index=False, encoding="utf-8-sig")
    selected = str(ranking.iloc[0]["name"])
    # Freeze the CV choice before reading 2026 or scoring either final period.
    write_json(output / "selection.json", {"selected_by_cv": selected, "ranking": ranking.to_dict(orient="records")})
    holdout = prepare_data(read_verified_csv(data_dir, HOLDOUT_FILE, provenance), {2026})
    require(set(main_data.row_id).isdisjoint(holdout.row_id), "2026 원본 행 ID 중복")
    evaluation_sets = {2025: main_data.loc[main_data.year.eq(2025)].copy(), 2026: holdout}
    results = evaluate(development, evaluation_sets, specs, args.threads, output)
    weighted = results.loc[results.weighting.eq("survey_weighted")]
    write_json(output / "summary.json", {
        "status": "completed", "selected_by_cv": selected,
        "evaluation_role": "retrospective_only_not_for_selection",
        "evaluation_rows": {str(y): len(frame) for y, frame in evaluation_sets.items()},
        "holdout_data_sha256": sha256(data_dir / HOLDOUT_FILE),
        "weighted_evaluation": weighted.to_dict(orient="records"), "limitations": LIMITATIONS,
        "delta_interpretation": "delta_vs_shallow가 음수이면 shallow보다 오차가 작습니다. 통계적 유의성을 뜻하지 않습니다.",
    })
    print("\nCV selection (lower is better):", selected)
    print(ranking.to_string(index=False))
    print("\nRetrospective evaluation (do not reselect):")
    print(weighted[["name", "year", "train_n", "n", PRIMARY, "category_macro_mae_won",
                    "delta_vs_shallow_pp"]].to_string(index=False))
    print(f"\nCompleted: {output}")
    print("Read cv_ranking.csv, comparison.csv, category_metrics.csv and summary.json.")


if __name__ == "__main__":
    main()

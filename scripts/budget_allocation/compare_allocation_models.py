"""Compare ten-category budget allocations without modifying the v13 package.

Run from repository root: python -m scripts.budget_allocation.compare_allocation_models
Dependencies: python -m pip install -r scripts/budget_allocation/requirements.txt

Input is the canonical data/household_budget CSVs, NOT v13 household_budget.csv.
No P95 removal, permanent-worker filter, or within-income filter is added.
Features: age, sex, log disposable income, vehicle ownership, fixed expenses,
and the known living budget. Evaluation supplies the observed living total;
this measures conditional allocation, not the quality of a reduced budget.

Selection: expanding-year CV, train 2019..(year-1), validate 2022/2023/2024.
Primary score: survey-weighted macro ratio MAE (%p), averaged equally by year.
Training weights: survey weights times a two-year half-life, mean-normalized.
Scaler/means/models are fit on each training fold only. All targets are ratios.
Five families: mean, Ridge, CatBoost, mean+Ridge, mean+CatBoost. Mixture endpoints
are the pure mean/model candidates; mixtures use 0.25/0.50/0.75 model weight.

After CV selection is frozen, fit finalists on 2019..2024 and evaluate 2025 and
2026 separately. Both periods were inspected in prior work: RETROSPECTIVE tests,
not untouched external validation. Never choose settings from these scores.
Outputs go to reports/budget_prediction/allocation_model_comparison/ by default.
Use --output with a descriptive new directory for another experiment.
No existing model, source CSV, report, or notebook is overwritten. No deployment
model is saved; this script is a comparison experiment.

Known source limitations: 2024 vehicle blanks were filled with zero upstream;
mixed public-statistics categories and period-unit assumptions remain. Review
flags are not features or exclusion rules. Household independence is not proven.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler


HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[1] / "local_workspace"
DEFAULT_DATA_DIR = WORKSPACE / "data" / "household_budget"
MAIN_FILE = "household_single_20_34_2019_2025.csv"
HOLDOUT_FILE = "household_single_20_34_2026_holdout.csv"
MAPPING_FILE = "category_mapping_v2.csv"
CATEGORIES = (
    "식비", "주거·통신", "교통·차량", "생활용품", "패션·쇼핑",
    "의료·건강", "뷰티·미용", "교육", "문화·여가", "여행·숙박",
)
AMOUNTS = tuple("amount_" + category for category in CATEGORIES)
FEATURES = (
    "age", "sex", "log_disposable_income", "has_vehicle",
    "fixed_expense_amount", "living_budget_amount",
)
CV_YEARS = (2022, 2023, 2024)
HALF_LIFE_YEARS = 2.0
PRIMARY = "ratio_mae_pp"
SEED = 42


def require(condition, message):
    if not bool(condition):
        raise ValueError(message)


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    with Path(path).open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def read_verified_csv(data_dir, filename, provenance):
    path = data_dir / filename
    expected = provenance["outputs"][filename]["sha256"]
    require(sha256(path) == expected, f"처리기록과 파일 해시 불일치: {filename}")
    return pd.read_csv(path, encoding="utf-8-sig", float_precision="round_trip")


def prepare_data(source, allowed_years):
    """Rebuild ten targets from amounts, checking the canonical CSV contract."""
    required = {
        "원본파일", "원본행번호", "연도", "가중값", "나이", "성별",
        "근로자가구", "자동차보유", "소득", "비소비지출", "처분가능소득",
        "로그처분가능소득", "고정비", "생활비", "생활비_주거·통신",
        *CATEGORIES, *("비율_" + c for c in CATEGORIES),
    }
    missing = required - set(source.columns)
    require(not missing, f"정제 파일의 필수 열 누락: {sorted(missing)}")
    require(len(source) > 0, "빈 데이터입니다.")
    source = source.copy().reset_index(drop=True)
    numeric = sorted(required - {"원본파일", "성별", "근로자가구", "자동차보유"})
    source[numeric] = source[numeric].apply(pd.to_numeric, errors="raise")
    require(np.isfinite(source[numeric].to_numpy(float)).all(), "비유한 숫자/결측이 있습니다.")
    for column in ("원본행번호", "연도", "나이"):
        require(source[column].mod(1).eq(0).all(), f"정수여야 합니다: {column}")
    require(source["원본행번호"].ge(2).all(), "원본 행 번호가 잘못되었습니다.")
    require(source["연도"].isin(allowed_years).all(), "허용하지 않은 연도가 섞여 있습니다.")
    require(source["나이"].between(20, 34).all(), "20~34세 자료만 허용합니다.")
    require(source["근로자가구"].eq("Y").all(), "근로자 가구 자료만 허용합니다.")
    require(source["성별"].isin(["남", "여"]).all(), "성별 코드가 잘못되었습니다.")
    require(source["자동차보유"].isin(["Y", "N"]).all(), "자동차 보유 코드가 잘못되었습니다.")
    require(source["원본파일"].notna().all(), "원본 파일 식별자가 없습니다.")
    require(source["원본파일"].astype(str).str.strip().ne("").all(), "빈 원본 파일 식별자입니다.")
    for column in ("가중값", "소득", "처분가능소득", "생활비"):
        require(source[column].gt(0).all(), f"양수여야 합니다: {column}")
    require(source["고정비"].ge(0).all(), "음수 고정비가 있습니다.")
    require(source[list(CATEGORIES)].ge(0).all().all(), "음수 카테고리 금액이 있습니다.")
    np.testing.assert_allclose(
        source["처분가능소득"], source["소득"] - source["비소비지출"], rtol=0, atol=1e-6,
        err_msg="처분가능소득 정의 불일치",
    )
    log_income = np.log(source["처분가능소득"].to_numpy(float))
    np.testing.assert_allclose(source["로그처분가능소득"], log_income, rtol=0, atol=1e-10)
    housing = source["주거·통신"] - source["고정비"]
    require(housing.ge(-5).all(), "주거·통신에서 고정비 차감 후 -5원 미만입니다.")
    housing = housing.clip(lower=0)  # Match the canonical rounding policy only.
    np.testing.assert_allclose(source["생활비_주거·통신"], housing, rtol=0, atol=1e-6)
    amounts = source[list(CATEGORIES)].to_numpy(float).copy()
    amounts[:, 1] = housing.to_numpy(float)
    budget = amounts.sum(axis=1)
    require((budget > 0).all(), "10개 생활비 합계가 0 이하입니다.")
    np.testing.assert_allclose(source["생활비"], budget, rtol=0, atol=1e-6)
    ratios = amounts / budget[:, None]
    np.testing.assert_allclose(
        source[["비율_" + c for c in CATEGORIES]], ratios, rtol=0, atol=1e-10,
        err_msg="저장 비율과 재계산한 10개 비율이 다릅니다.",
    )
    frame = pd.DataFrame({
        "row_id": source["원본파일"].astype(str) + ":" + source["원본행번호"].astype(int).astype(str),
        "year": source["연도"].astype(int),
        "survey_weight": source["가중값"].to_numpy(float),
        "age": source["나이"].to_numpy(float),
        "sex": source["성별"].map({"남": 0, "여": 1}).to_numpy(float),
        "log_disposable_income": log_income,
        "has_vehicle": source["자동차보유"].map({"N": 0, "Y": 1}).to_numpy(float),
        "fixed_expense_amount": source["고정비"].to_numpy(float),
        "living_budget_amount": budget,
    })
    frame[list(AMOUNTS)] = amounts
    require(frame.row_id.is_unique, "동일 원본 행이 중복되어 있습니다.")
    return frame


def target_ratios(frame):
    return frame[list(AMOUNTS)].to_numpy(float) / frame.living_budget_amount.to_numpy()[:, None]


def training_weights(frame):
    weights = frame.survey_weight.to_numpy(float) * 0.5 ** (
        (frame.year.max() - frame.year.to_numpy()) / HALF_LIFE_YEARS
    )
    return weights / weights.mean()


def normalize_ratios(raw, fallback):
    raw = np.asarray(raw, dtype=float)
    require(raw.ndim == 2 and raw.shape[1] == len(CATEGORIES), "출력은 (행, 10)이어야 합니다.")
    require(np.isfinite(raw).all(), "모델이 비유한 값을 반환했습니다.")
    fallback = np.asarray(fallback, dtype=float)
    require(fallback.shape == (10,) and np.isfinite(fallback).all(), "평균 비율 형식 오류")
    require((fallback >= 0).all() and fallback.sum() > 0, "평균 비율이 유효하지 않습니다.")
    negative_rows = int((raw < 0).any(axis=1).sum())
    ratios = np.maximum(raw, 0)
    zero_rows = ratios.sum(axis=1) <= 0
    ratios[zero_rows] = fallback / fallback.sum()
    ratios /= ratios.sum(axis=1, keepdims=True)
    return ratios, {"negative_clipped_rows": negative_rows, "fallback_rows": int(zero_rows.sum())}


def catboost_features(frame):
    x = frame[list(FEATURES)].copy()
    for column in ("sex", "has_vehicle"):
        x[column] = x[column].astype(int).astype(str)
    return x


@dataclass(frozen=True)
class Candidate:
    name: str
    family: str
    base_key: str
    model_weight: float


def make_candidates():
    specifications = {"weighted_mean": {"kind": "weighted_mean", "params": {}}}
    for alpha in (0.1, 1.0, 10.0, 100.0, 1000.0):
        specifications[f"ridge_alpha_{alpha:g}"] = {"kind": "ridge", "params": {"alpha": alpha}}
    for label, iterations, depth in (("legacy_shape", 85, 6), ("shallow", 200, 4), ("deeper", 300, 6)):
        specifications[f"catboost_{label}"] = {
            "kind": "catboost",
            "params": {"iterations": iterations, "depth": depth, "learning_rate": 0.05},
        }
    candidates = [Candidate("weighted_mean", "weighted_mean", "weighted_mean", 0.0)]
    for key, spec in specifications.items():
        if key == "weighted_mean":
            continue
        candidates.append(Candidate(key, spec["kind"], key, 1.0))
        for fraction in (0.25, 0.5, 0.75):
            candidates.append(Candidate(
                f"mean_plus_{key}_weight_{fraction:g}", "mean_" + spec["kind"], key, fraction,
            ))
    return specifications, {candidate.name: candidate for candidate in candidates}


@dataclass
class FittedBase:
    kind: str
    mean: np.ndarray
    estimator: object = None
    scaler: object = None

    def predict(self, frame):
        if self.kind == "weighted_mean":
            raw = np.tile(self.mean, (len(frame), 1))
        elif self.kind == "ridge":
            raw = self.estimator.predict(self.scaler.transform(frame[list(FEATURES)]))
        else:
            raw = self.estimator.predict(catboost_features(frame))
        return normalize_ratios(raw, self.mean)


def fit_base(train, specification, threads):
    weights = training_weights(train)
    y = target_ratios(train)
    mean = np.average(y, axis=0, weights=weights)
    result = FittedBase(specification["kind"], mean / mean.sum())
    if result.kind == "ridge":
        result.scaler = StandardScaler().fit(train[list(FEATURES)], sample_weight=weights)
        x = result.scaler.transform(train[list(FEATURES)])
        result.estimator = Ridge(**specification["params"], solver="svd")
        result.estimator.fit(x, y, sample_weight=weights)
    elif result.kind == "catboost":
        result.estimator = CatBoostRegressor(
            **specification["params"], loss_function="MultiRMSE", random_seed=SEED,
            thread_count=threads, verbose=False, allow_writing_files=False,
        )
        result.estimator.fit(Pool(
            catboost_features(train), y, weight=weights, cat_features=["sex", "has_vehicle"],
        ))
    return result


def mix_ratios(mean, prediction, fraction):
    require(0 <= fraction <= 1, "혼합 비중은 0~1이어야 합니다.")
    return (1 - fraction) * np.asarray(mean) + fraction * prediction


def metrics(frame, prediction, weights):
    actual = frame[list(AMOUNTS)].to_numpy(float)
    budget = frame.living_budget_amount.to_numpy(float)
    require(prediction.shape == actual.shape, "예측 행 또는 카테고리 순서 오류")
    require(np.isfinite(prediction).all() and (prediction >= 0).all(), "유효하지 않은 예측 비율")
    np.testing.assert_allclose(prediction.sum(axis=1), 1, rtol=0, atol=1e-12)
    predicted_amount = prediction * budget[:, None]
    error = predicted_amount - actual
    ratio_error = prediction - target_ratios(frame)
    amount_mae = np.average(np.abs(error), axis=0, weights=weights)
    ratio_mae = np.average(np.abs(ratio_error), axis=0, weights=weights) * 100
    rmse = np.sqrt(np.average(error ** 2, axis=0, weights=weights))
    actual_mean = np.average(actual, axis=0, weights=weights)
    predicted_mean = np.average(predicted_amount, axis=0, weights=weights)
    summary = {
        "n": len(frame),
        "ratio_mae_pp": float(ratio_mae.mean()),
        "category_macro_mae_won": float(amount_mae.mean()),
        "category_macro_rmse_won": float(rmse.mean()),
        "category_abs_error_sum_mean_won": float(amount_mae.sum()),
        "allocation_distance_percent": float(np.average(np.abs(ratio_error).sum(axis=1) * 50, weights=weights)),
        "actual_total_mean_won": float(np.average(budget, weights=weights)),
        # This identity check is deliberately not used for model selection.
        "known_total_max_error_won": float(np.abs(predicted_amount.sum(axis=1) - budget).max()),
    }
    categories = [{
        "category": category, "ratio_mae_pp": float(ratio_mae[i]),
        "amount_mae_won": float(amount_mae[i]), "amount_rmse_won": float(rmse[i]),
        "actual_mean_won": float(actual_mean[i]), "predicted_mean_won": float(predicted_mean[i]),
        "bias_won": float(predicted_mean[i] - actual_mean[i]),
    } for i, category in enumerate(CATEGORIES)]
    return summary, categories


def temporal_folds(development):
    require(development.year.le(2024).all(), "CV에는 2025/2026 자료를 넣을 수 없습니다.")
    for year in CV_YEARS:
        train = development.loc[development.year.lt(year)].copy()
        validation = development.loc[development.year.eq(year)].copy()
        require(len(train) > 0 and len(validation) > 0, f"{year} 검증 또는 학습 표본이 없습니다.")
        require(train.year.max() < validation.year.min(), "시간 분할 오류")
        require(set(train.row_id).isdisjoint(validation.row_id), "학습·검증 행 중복")
        yield year, train, validation


def run_cv(development, specifications, candidates, threads):
    rows = []
    for year, train, validation in temporal_folds(development):
        cache = {}
        for key, specification in specifications.items():
            print(f"CV {year}: {key}, train={len(train)}, validation={len(validation)}", flush=True)
            fitted = fit_base(train, specification, threads)
            prediction, diagnostics = fitted.predict(validation)
            cache[key] = (fitted.mean, prediction, diagnostics)
        for candidate in candidates.values():
            mean, prediction, diagnostics = cache[candidate.base_key]
            ratios = mix_ratios(mean, prediction, candidate.model_weight)
            scores, _ = metrics(validation, ratios, validation.survey_weight.to_numpy(float))
            rows.append({
                **asdict(candidate), "validation_year": year, "train_n": len(train),
                **scores, **diagnostics,
            })
    scores = pd.DataFrame(rows)
    ranking = scores.groupby(["name", "family", "base_key", "model_weight"], as_index=False).agg(
        cv_ratio_mae_pp=(PRIMARY, "mean"),
        cv_ratio_mae_year_std_pp=(PRIMARY, "std"),
        cv_category_macro_mae_won=("category_macro_mae_won", "mean"),
        folds=("validation_year", "nunique"),
    )
    require(ranking.folds.eq(len(CV_YEARS)).all(), "일부 후보의 CV 구간이 누락되었습니다.")
    baseline = float(ranking.loc[ranking.name.eq("weighted_mean"), "cv_ratio_mae_pp"].iloc[0])
    require(baseline > 0, "평균 기준선 오차가 0입니다. 비교 자료를 확인하세요.")
    ranking["improvement_vs_mean_percent"] = (1 - ranking.cv_ratio_mae_pp / baseline) * 100
    # Exact ties prefer the mean, then a fixed family/name order.
    family_order = {"weighted_mean": 0, "ridge": 1, "mean_ridge": 2, "catboost": 3, "mean_catboost": 4}
    ranking["tie_order"] = ranking.family.map(family_order)
    ranking = ranking.sort_values(["cv_ratio_mae_pp", "tie_order", "name"], kind="stable").reset_index(drop=True)
    ranking.insert(0, "cv_rank", np.arange(1, len(ranking) + 1))
    return scores, ranking.drop(columns="tie_order")


def evaluate_finalists(development, evaluation_sets, finalists, specifications, candidates, threads, output):
    cache = {}
    comparison, category_rows, prediction_tables, slice_rows = [], [], [], []
    for name in finalists:
        candidate = candidates[name]
        if candidate.base_key not in cache:
            print(f"Final fit 2019-2024: {candidate.base_key}", flush=True)
            cache[candidate.base_key] = fit_base(development, specifications[candidate.base_key], threads)
        fitted = cache[candidate.base_key]
        for year, frame in evaluation_sets.items():
            prediction, diagnostics = fitted.predict(frame)
            ratios = mix_ratios(fitted.mean, prediction, candidate.model_weight)
            for weighting, weights in (
                ("survey_weighted", frame.survey_weight.to_numpy(float)),
                ("unweighted", np.ones(len(frame))),
            ):
                scores, categories = metrics(frame, ratios, weights)
                context = {"name": name, "family": candidate.family, "year": year, "weighting": weighting}
                comparison.append({**context, **scores, **diagnostics})
                category_rows.extend({**context, **row} for row in categories)
            # Fixed diagnostic slices; no thresholds are learned from evaluation data.
            slices = {
                "budget_below_1m": frame.living_budget_amount.lt(1_000_000),
                "budget_1m_to_2m": frame.living_budget_amount.ge(1_000_000) & frame.living_budget_amount.lt(2_000_000),
                "budget_at_least_2m": frame.living_budget_amount.ge(2_000_000),
                "vehicle_no": frame.has_vehicle.eq(0), "vehicle_yes": frame.has_vehicle.eq(1),
            }
            for label, mask in slices.items():
                if mask.any():
                    block = frame.loc[mask]
                    scores, _ = metrics(block, ratios[mask.to_numpy()], block.survey_weight.to_numpy(float))
                    slice_rows.append({"name": name, "year": year, "slice": label, **scores})
            table = frame[["row_id", "year", "living_budget_amount"]].reset_index(drop=True).copy()
            table.insert(0, "name", name)
            actual_ratios = target_ratios(frame)
            for i, category in enumerate(CATEGORIES):
                table["actual_ratio_" + category] = actual_ratios[:, i]
                table["predicted_ratio_" + category] = ratios[:, i]
            prediction_tables.append(table)
    results = pd.DataFrame(comparison)
    results.to_csv(output / "comparison.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(category_rows).to_csv(output / "category_metrics.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(slice_rows).to_csv(output / "slice_metrics.csv", index=False, encoding="utf-8-sig")
    pd.concat(prediction_tables, ignore_index=True).to_csv(output / "predictions.csv", index=False, encoding="utf-8-sig")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="공식 10개 정제 CSV와 processing_records.json 폴더")
    parser.add_argument("--output", type=Path, default=WORKSPACE / "reports/budget_prediction/allocation_model_comparison")
    parser.add_argument("--threads", type=int, default=2, help="CatBoost CPU 스레드 수 (기본 2)")
    args = parser.parse_args()
    require(args.threads > 0, "--threads는 양수여야 합니다.")
    data_dir = args.data_dir.resolve()
    with (data_dir / "processing_records.json").open(encoding="utf-8-sig") as stream:
        provenance = json.load(stream)
    read_verified_csv(data_dir, MAPPING_FILE, provenance)
    main_data = prepare_data(read_verified_csv(data_dir, MAIN_FILE, provenance), range(2019, 2026))
    require(set(main_data.year) == set(range(2019, 2026)), "2019~2025년 자료가 모두 필요합니다.")
    development = main_data.loc[main_data.year.le(2024)].copy()
    specifications, candidates = make_candidates()
    output = args.output.resolve()
    require(not output.is_relative_to(WORKSPACE / "output"), "output은 문서 전용입니다. 평가 결과는 reports에 저장하세요.")
    output.mkdir(parents=True, exist_ok=False)
    print(f"Output: {output}", flush=True)
    print("Selecting on 2022/2023/2024 CV only; 2025/2026 are retrospective evaluation.", flush=True)
    scores, ranking = run_cv(development, specifications, candidates, args.threads)
    scores.to_csv(output / "cv_scores.csv", index=False, encoding="utf-8-sig")
    ranking.to_csv(output / "cv_ranking.csv", index=False, encoding="utf-8-sig")
    finalists = ranking.drop_duplicates("family")["name"].tolist()
    selected = str(ranking.iloc[0]["name"])
    protocol = {
        "selected_by_cv": selected,
        "finalists": finalists,
        "selection_metric": "equal-year mean of survey-weighted macro ratio MAE (%p)",
        "validation_years": list(CV_YEARS), "final_fit_years": list(range(2019, 2025)),
        "categories": list(CATEGORIES), "features": list(FEATURES),
        "training_half_life_years": HALF_LIFE_YEARS,
        "seed": SEED, "threads": args.threads,
        "base_specifications": specifications,
        "candidate_specifications": [asdict(candidate) for candidate in candidates.values()],
        "development_rows_by_year": {str(y): int(n) for y, n in development.groupby("year").size().items()},
        "main_data_sha256": sha256(data_dir / MAIN_FILE),
        "mapping_sha256": sha256(data_dir / MAPPING_FILE),
        "source_provenance_sha256": sha256(data_dir / "processing_records.json"),
        "comparison_code_sha256": sha256(Path(__file__)),
        "python": platform.python_version(),
        "packages": {name: version(name) for name in ("numpy", "pandas", "scikit-learn", "catboost")},
        "limitations": [
            "2025/2026 were inspected in prior work; these are retrospective evaluations, not untouched tests.",
            "CV minimum is a selection score, not an unbiased performance estimate or statistical significance claim.",
            "Evaluation uses observed living totals; reduced-budget suitability is not measured.",
            "Canonical 10-category targets/population differ from v13; old 11-category scores are not comparable.",
            "No P95 expense removal or within-income/permanent-worker filtering is added.",
            "2024 vehicle missing values were filled with zero by upstream preprocessing.",
            "Public-statistics mixed-category, period-unit, and repeated-household limitations remain.",
        ],
    }
    # Selection is persisted BEFORE reading 2026 or computing any final-period score.
    write_json(output / "selection.json", protocol)
    evaluation_2026 = prepare_data(read_verified_csv(data_dir, HOLDOUT_FILE, provenance), {2026})
    require(set(main_data.row_id).isdisjoint(evaluation_2026.row_id), "2026 원본 행 ID 중복")
    evaluation_sets = {2025: main_data.loc[main_data.year.eq(2025)].copy(), 2026: evaluation_2026}
    results = evaluate_finalists(
        development, evaluation_sets, finalists, specifications, candidates, args.threads, output,
    )
    summary = {
        "status": "completed", "selected_by_cv": selected,
        "selected_cv_ratio_mae_pp": float(ranking.iloc[0]["cv_ratio_mae_pp"]),
        "selected_cv_improvement_vs_mean_percent": float(ranking.iloc[0]["improvement_vs_mean_percent"]),
        "evaluation_role": "retrospective_confirmation_only_not_used_for_selection",
        "evaluation_rows": {str(year): len(frame) for year, frame in evaluation_sets.items()},
        "holdout_data_sha256": sha256(data_dir / HOLDOUT_FILE),
        "selected_evaluation": results.loc[
            results.name.eq(selected) & results.weighting.eq("survey_weighted")
        ].to_dict(orient="records"),
        "interpretation": "CV 평균 오차 최소 후보입니다. 통계적 유의성이나 최적 예산을 보장하지 않습니다.",
    }
    write_json(output / "summary.json", summary)
    print("\nCV selection (lower is better):", selected)
    print(ranking[["name", "cv_ratio_mae_pp", "improvement_vs_mean_percent"]].head(10).to_string(index=False))
    print("\nRetrospective evaluation (do not reselect from these scores):")
    print(results.loc[results.weighting.eq("survey_weighted"), [
        "name", "year", "ratio_mae_pp", "category_macro_mae_won",
    ]].to_string(index=False))
    print(f"\nCompleted. Read summary.json, cv_ranking.csv, comparison.csv in: {output}")


if __name__ == "__main__":
    main()

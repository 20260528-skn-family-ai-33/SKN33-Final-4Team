"""v5 후보 비교·전체 train 재학습·잠근 test 1회 평가. 모듈로 실행한다."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import gc
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import threading
import time
import warnings

import joblib
import numpy as np
import pandas as pd
import psutil
from sklearn.calibration import CalibratedClassifierCV
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression, SGDClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score, log_loss
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC
from threadpoolctl import threadpool_limits

from scripts.spending_classifier.common import (
    CATEGORIES, CODES, NORMALIZATION_VERSION, ROOT, keyword_prediction, sha256, write_json, work_path,
)


def log(message):
    print(f"{datetime.now().isoformat(timespec='seconds')} {message}", flush=True)


def sample_train(frame, cap, exclude_brands, seed=42):
    if not frame["split"].eq("train").all():
        raise ValueError("학습 표본에 valid/test가 포함되어 있습니다")
    eligible = frame.loc[~frame["brand_candidate"]] if exclude_brands else frame
    # 후보 제외로 다른 이름까지 다시 추출되지 않도록 이름별 고정 난수를 쓴다.
    ranked = eligible.assign(_rank=pd.util.hash_pandas_object(eligible["name"], index=False).to_numpy() ^ np.uint64(seed))
    return ranked.sort_values(["_rank", "name"]).groupby("category", sort=True).head(cap).drop(columns="_rank")


def make_model(kind, c, folds, seed, max_iter, cv_jobs=1):
    if kind == "M1":
        classifier = LogisticRegression(C=c, class_weight="balanced", max_iter=max_iter,
                                        solver="newton-cg" if c >= 4 else "saga", random_state=seed, tol=1e-4)
    elif kind == "M2":
        classifier = LinearSVC(C=c, class_weight="balanced", max_iter=max_iter,
                               dual="auto", random_state=seed, tol=1e-4)
    elif kind == "M3":
        classifier = SGDClassifier(loss="log_loss", alpha=1e-5, class_weight="balanced",
                                   max_iter=max_iter, tol=1e-4, random_state=seed)
    else:
        raise ValueError(kind)
    pipeline = Pipeline([
        ("tfidf", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4) if kind == "M3" else (1, 3),
                                 dtype=np.float32)),
        ("classifier", classifier),
    ], verbose=True)
    return CalibratedClassifierCV(pipeline, method="sigmoid", ensemble=False,
                                  cv=StratifiedKFold(folds, shuffle=True, random_state=seed), n_jobs=cv_jobs)


def fit_model(frame, kind, c, args):
    if not frame["split"].eq("train").all():
        raise ValueError("학습/보정에 valid/test가 포함되어 있습니다")
    counts = frame["category"].value_counts()
    if set(counts.index) != set(CODES) or counts.min() < args.folds:
        raise ValueError("교차검증 각 구간에 10개 카테고리를 배정할 표본이 부족합니다")
    peak = [psutil.Process().memory_info().rss]
    stop = threading.Event()

    def monitor():
        last_log = time.perf_counter()
        while not stop.wait(0.5):
            process = psutil.Process()
            rss = process.memory_info().rss
            peak[0] = max(peak[0], rss)
            if time.perf_counter() - last_log >= 45:
                log(f"fit active: elapsed={time.perf_counter() - started:.0f}s rss={rss / 2**20:.0f}MiB cpu={sum(process.cpu_times()[:2]):.0f}s")
                last_log = time.perf_counter()

    worker = threading.Thread(target=monitor, daemon=True)
    started = time.perf_counter()
    worker.start()
    attempts = []
    cv_jobs = getattr(args, "cv_jobs", 1)
    try:
        for max_iter in (args.max_iter, args.max_iter * 4):
            log(f"fit {kind} C={c} rows={len(frame)} cv={args.folds} max_iter={max_iter}")
            model = make_model(kind, c, args.folds, args.seed, max_iter, cv_jobs)
            with warnings.catch_warnings(record=True) as caught, threadpool_limits(limits=args.threads), \
                 joblib.parallel_backend("threading", n_jobs=cv_jobs):
                warnings.simplefilter("always")
                model.fit(frame["name"].to_numpy(), frame["category"].to_numpy())
            messages = [str(w.message) for w in caught]
            converged = not any(issubclass(w.category, ConvergenceWarning)
                                or "Line Search failed" in str(w.message) for w in caught)
            attempts.append({"max_iter": max_iter, "warnings": messages, "converged": converged})
            if converged:
                break
            log("Optimizer did not converge: retrying with a larger iteration limit")
            del model
            gc.collect()
        else:
            raise RuntimeError(f"{kind}: 반복 상향 후에도 수렴하지 않아 후보 평가를 중단합니다")
    finally:
        stop.set()
        worker.join()
    elapsed = time.perf_counter() - started
    base = model.calibrated_classifiers_[0].estimator
    return model, {"train_rows": len(frame), "train_category_counts": counts.to_dict(), "cv_jobs": cv_jobs,
                   "solver": base.named_steps["classifier"].get_params().get("solver", "liblinear" if kind == "M2" else "sgd"),
                   "final_estimator_iterations": np.atleast_1d(base.named_steps["classifier"].n_iter_).tolist(),
                   "fit_seconds": elapsed, "peak_process_rss_mb": round(peak[0] / 2**20, 1),
                   "vocabulary_size": len(base.named_steps["tfidf"].vocabulary_), "attempts": attempts}


def predict_probabilities(model, names, batch_size=10000):
    order = [list(model.classes_).index(code) for code in CODES]
    parts = [model.predict_proba(names[start:start + batch_size])[:, order]
             for start in range(0, len(names), batch_size)]
    probabilities = np.concatenate(parts) if parts else np.empty((0, len(CODES)))
    if not np.isfinite(probabilities).all() or not np.allclose(probabilities.sum(axis=1), 1, atol=1e-5):
        raise ValueError("유효하지 않은 확률 출력")
    return probabilities


def threshold_metrics(y, probabilities, thresholds=None):
    if thresholds is None:
        thresholds = np.round(np.arange(0.5, 0.951, 0.05), 2)
    prediction = np.asarray(CODES)[probabilities.argmax(axis=1)]
    confidence = probabilities.max(axis=1)
    result = []
    for threshold in thresholds:
        mask = confidence >= threshold
        n = int(mask.sum())
        result.append({"threshold": float(threshold), "accepted": n, "total": len(y),
                       "coverage": n / len(y) if len(y) else None,
                       "accuracy": float(np.mean(prediction[mask] == np.asarray(y)[mask])) if n else None})
    return result


def select_threshold(rows, target_accuracy, min_accepted=30):
    eligible = [r for r in rows if r["accepted"] >= min_accepted and r["accuracy"] >= target_accuracy]
    return min(eligible, key=lambda r: r["threshold"])["threshold"] if eligible else None


def basic_metrics(y, prediction):
    if not len(y):
        return {"count": 0, "accuracy": None, "macro_f1": None}
    return {"count": len(y), "accuracy": float(accuracy_score(y, prediction)),
            "macro_f1": float(f1_score(y, prediction, labels=CODES, average="macro", zero_division=0)),
            "classification_report": classification_report(y, prediction, labels=CODES,
                                                           output_dict=True, zero_division=0),
            "confusion_matrix": confusion_matrix(y, prediction, labels=CODES).tolist()}


def evaluate(frame, probabilities):
    y = frame["category"].to_numpy()
    prediction = np.asarray(CODES)[probabilities.argmax(axis=1)]
    result = basic_metrics(y, prediction)
    result["top2_accuracy"] = float(np.mean(np.any(np.asarray(CODES)[np.argsort(-probabilities, axis=1, kind="stable")[:, :2]] == y[:, None], axis=1)))
    # log_loss expects alphabetically sorted label columns.
    alphabetical = sorted(CODES)
    result["log_loss"] = float(log_loss(y, probabilities[:, [CODES.index(c) for c in alphabetical]], labels=alphabetical))
    result["thresholds"] = threshold_metrics(y, probabilities)
    confidence = probabilities.max(axis=1)
    result["calibration_bins"] = []
    for lo in np.arange(0, 1, 0.1):
        mask = (confidence >= lo) & (confidence < lo + 0.1 if lo < 0.9 else confidence <= 1)
        result["calibration_bins"].append({"lower": round(float(lo), 1), "count": int(mask.sum()),
                                            "mean_confidence": float(confidence[mask].mean()) if mask.any() else None,
                                            "accuracy": float((prediction[mask] == y[mask]).mean()) if mask.any() else None})
    result["subsets"] = {}
    masks = {"store": (frame["source_mask"].to_numpy() & 1) != 0,
             "mail": (frame["source_mask"].to_numpy() & 2) != 0,
             "brand_candidates": frame["brand_candidate"].to_numpy(),
             "non_brand_candidates": ~frame["brand_candidate"].to_numpy()}
    for label, mask in masks.items():
        sub = basic_metrics(y[mask], prediction[mask])
        sub["support_by_category"] = dict(Counter(y[mask]))
        sub["thresholds"] = threshold_metrics(y[mask], probabilities[mask]) if mask.any() else []
        result["subsets"][label] = sub
    return result


def baseline_metrics(frame):
    pairs = [keyword_prediction(name) for name in frame["name"]]
    result = {"B0": basic_metrics(frame["category"], np.full(len(frame), "FOOD")),
              "B1": basic_metrics(frame["category"], np.array([p[0] for p in pairs]))}
    result["B1"]["rule_coverage"] = sum(p[1] for p in pairs) / len(pairs)
    return result


def load_prepared(directory):
    report = json.loads((directory / "preparation.json").read_text(encoding="utf-8"))
    if report["normalization_version"] != NORMALIZATION_VERSION:
        raise ValueError("전처리와 학습 코드의 정규화 버전이 다릅니다")
    if report["status"] != "complete" or sha256(directory / "prepared.csv") != report["prepared_sha256"]:
        raise ValueError("전처리가 완료되지 않았거나 입력이 변경됐습니다")
    if sha256(directory / "split_ids.csv") != report["split_ids_sha256"]:
        raise ValueError("고정한 분할 파일이 변경됐습니다")
    frame = pd.read_csv(directory / "prepared.csv", keep_default_na=False,
                        usecols=["name", "category", "source_mask", "brand_candidate", "split"])
    if not frame["name"].is_unique or frame["name"].eq("").any():
        raise ValueError("중복/빈 정규화 이름")
    if set(frame["split"]) != {"train", "valid", "test"} or set(frame["category"]) != set(CODES):
        raise ValueError("분할/라벨 오류")
    return frame, report


def error_samples(frame, probabilities):
    prediction = np.asarray(CODES)[probabilities.argmax(axis=1)]
    top2 = np.argsort(-probabilities, axis=1, kind="stable")[:, :2]
    errors = frame.loc[prediction != frame["category"].to_numpy()].copy()
    mask = prediction != frame["category"].to_numpy()
    errors["prediction"] = prediction[mask]
    errors["confidence"] = probabilities.max(axis=1)[mask]
    errors["runner_up"] = np.asarray(CODES)[top2[mask, 1]]
    errors["margin"] = (np.take_along_axis(probabilities, top2, axis=1)[:, 0] -
                        np.take_along_axis(probabilities, top2, axis=1)[:, 1])[mask]
    errors = errors.sort_values(["confidence", "name"], ascending=[False, True])
    errors = errors.groupby(["category", "prediction"], sort=True).head(30)
    records = []
    for row in errors.to_dict("records"):
        records.append({"상호명_정규화": row["name"], "약지도라벨": CATEGORIES[row["category"]],
                        "예측": CATEGORIES[row["prediction"]], "확신도": row["confidence"],
                        "2순위": CATEGORIES[row["runner_up"]], "1_2순위차이": row["margin"],
                        "출처": {1: "상가", 2: "통판", 3: "상가+통판"}[row["source_mask"]],
                        "다품목후보": bool(row["brand_candidate"]), "검토판정": "", "검토메모": ""})
    return records


def make_plots(directory, test):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    if "Malgun Gothic" in {font.name for font in font_manager.fontManager.ttflist}:
        plt.rcParams["font.family"] = "Malgun Gothic"
    plt.rcParams["axes.unicode_minus"] = False
    matrix = np.array(test["confusion_matrix"])
    normalized = np.divide(matrix, matrix.sum(axis=1, keepdims=True), out=np.zeros_like(matrix, dtype=float), where=matrix.sum(axis=1, keepdims=True) != 0)
    fig, ax = plt.subplots(figsize=(11, 9))
    im = ax.imshow(normalized, cmap="Blues", vmin=0, vmax=1)
    ax.set(xticks=np.arange(10), yticks=np.arange(10), xticklabels=list(CATEGORIES.values()),
           yticklabels=list(CATEGORIES.values()), xlabel="예측", ylabel="약지도 라벨", title="소비 분류 1차 — 테스트 혼동행렬 (행별 비율)")
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    for i in range(10):
        for j in range(10):
            ax.text(j, i, f"{normalized[i, j]:.0%}", ha="center", va="center", color="white" if normalized[i, j] > 0.5 else "black", fontsize=8)
    fig.colorbar(im, ax=ax)
    fig.tight_layout()
    fig.savefig(directory / "confusion_matrix.png", dpi=150)
    plt.close(fig)
    rows = test["thresholds"]
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot([r["threshold"] for r in rows], [r["coverage"] for r in rows], "o-", label="평가셋 기준선 통과율")
    ax.plot([r["threshold"] for r in rows], [r["accuracy"] for r in rows], "o-", label="통과 이름의 라벨 일치율")
    ax.set(xlabel="확신도 기준선", ylabel="비율", ylim=(0, 1.02), title="테스트 기준선 곡선 — 서비스 처리율 아님")
    ax.grid(alpha=0.2)
    ax.legend()
    fig.tight_layout()
    fig.savefig(directory / "threshold_curve.png", dpi=150)
    plt.close(fig)


def write_summary(directory, metrics, preparation_directory=None):
    final = metrics["final"]
    test = final["test"]
    lines = ["# 소비 카테고리 분류 1차 평가", "", "약지도 업종 라벨 기준의 고유 상호명 평가이며 실제 거래 성능·서비스 ML 처리율이 아닙니다.", "",
             f"- 선택: {final['candidate_id']}", f"- 테스트 이름: {test['count']:,}개",
             f"- macro-F1: {test['macro_f1']:.4f}", f"- 정확도: {test['accuracy']:.4f}",
             f"- top-2 정확도: {test['top2_accuracy']:.4f}",
             f"- 검증셋에서 고정한 참고 기준선: {final['selected_threshold']}",
             f"- 참고 목표 정확도: {metrics['config']['target_accuracy']:.0%} (서비스 합격 기준 아님)", "",
             "## 검증셋 후보 비교", "", "| 후보 | 최적화 방식 | 학습 이름 | macro-F1 | 정확도 | 학습 초 |", "|---|---|---:|---:|---:|---:|"]
    prep_path = (preparation_directory or directory) / "preparation.json"
    if prep_path.exists():
        prep = json.loads(prep_path.read_text(encoding="utf-8"))
        splits = prep["split_counts"]
        empty = sum(source.get("empty_normalized_rows", 0) for source in prep["sources"].values())
        lines[4:4] = [f"- 정제 후 고유 상호명: {prep['deduplicated_names']:,}개",
                      f"- 분할: 학습 {splits['train']:,} / 검증 {splits['valid']:,} / 테스트 {splits['test']:,}",
                      f"- 공통 정규화 후 라벨 충돌: {prep.get('conflicting_names', 0):,}개 중 {prep.get('kept_conflicting_names', 0):,}개 유지, {prep.get('excluded_conflicting_names', 0):,}개 제외",
                      f"- 정규화 후 빈 이름: {empty:,}행 제외", ""]
    for key, result in metrics["candidates"].items():
        lines.append(f"| {key} | {result['fit']['solver']} | {result['fit']['train_rows']:,} | {result['valid']['macro_f1']:.4f} | {result['valid']['accuracy']:.4f} | {result['fit']['fit_seconds']:.1f} |")
    lines += ["", "## 기준 모델과 비교", "", "| 모델 | 검증 macro-F1 | 테스트 macro-F1 | 테스트 정확도 |", "|---|---:|---:|---:|"]
    for key in ("B0", "B1"):
        baseline = metrics["test_baselines"][key]
        lines.append(f"| {key} | {metrics['valid_baselines'][key]['macro_f1']:.4f} | {baseline['macro_f1']:.4f} | {baseline['accuracy']:.4f} |")
    lines += [f"| 전체 train 재학습 모델 | {final['valid']['macro_f1']:.4f} | {test['macro_f1']:.4f} | {test['accuracy']:.4f} |",
              "", f"B1의 테스트 규칙 적용률은 {metrics['test_baselines']['B1']['rule_coverage']:.2%}입니다. 미매칭은 식비로 채점했습니다.",
              "", "## 출처별 테스트 결과", "", "| 출처 | 표본 | macro-F1 | 정확도 |", "|---|---:|---:|---:|"]
    for key, label in (("store", "상가"), ("mail", "통판")):
        subset = test["subsets"][key]
        score = f"{subset['macro_f1']:.4f}" if subset["count"] else "해당 없음"
        accuracy = f"{subset['accuracy']:.4f}" if subset["count"] else "해당 없음"
        lines.append(f"| {label} | {subset['count']:,} | {score} | {accuracy} |")
    lines += ["", f"최종 학습은 {final['fit']['train_rows']:,}개 이름을 사용했습니다. 보정 포함 학습 시간은 {final['fit']['fit_seconds'] / 60:.1f}분, 측정된 최대 프로세스 메모리는 {final['fit']['peak_process_rss_mb']:,.1f} MiB입니다."]
    lines += ["", "## 테스트 카테고리별 결과", "", "| 카테고리 | 표본 | 정밀도 | 재현율 | F1 |", "|---|---:|---:|---:|---:|"]
    for code in CODES:
        row = test["classification_report"][code]
        lines.append(f"| {CATEGORIES[code]} | {int(row['support']):,} | {row['precision']:.4f} | {row['recall']:.4f} | {row['f1-score']:.4f} |")
    lines += ["", "## 확률 보정 확인 — 최종 모델의 검증셋", "", "| 확신도 구간 | 이름 수 | 평균 확신도 | 실제 라벨 일치율 |", "|---|---:|---:|---:|"]
    for row in final["valid"]["calibration_bins"]:
        if row["count"]:
            lines.append(f"| {row['lower']:.1f}~{row['lower'] + 0.1:.1f} | {row['count']:,} | {row['mean_confidence']:.2%} | {row['accuracy']:.2%} |")
    lines += ["", "## 기준선별 테스트 결과", "", "| 기준선 | 통과 이름 | 통과율 | 라벨 일치율 |", "|---|---:|---:|---:|"]
    for row in test["thresholds"]:
        accuracy = f"{row['accuracy']:.2%}" if row["accuracy"] is not None else "해당 없음"
        lines.append(f"| {row['threshold']:.2f} | {row['accepted']:,} | {row['coverage']:.2%} | {accuracy} |")
    lines += ["", f"![테스트 혼동행렬](<{(directory / 'confusion_matrix.png').as_posix()}>)", "",
              f"![테스트 기준선 곡선](<{(directory / 'threshold_curve.png').as_posix()}>)"]
    lines += ["", "## 해석과 제한", "", "- 같은 정규화 이름은 분할 간 중복이 없고 TF-IDF·보정기는 train 내부에서만 학습했습니다.",
              "- M1은 낮은 C에서 SAGA, C=4에서 Newton-CG를 사용했습니다. 최적화 방식이 달라 C 간 차이를 C 하나의 효과로 해석하지 않으며, 기록된 설정 전체의 점수를 비교합니다.",
              "- 같은 브랜드의 다른 지점·변형 이름은 하나의 그룹으로 묶지 않았으므로 학습과 평가에 나뉘어 들어갈 수 있습니다. 새 브랜드에 대한 일반화 성능을 뜻하지 않습니다.",
              "- 이름을 합칠 때 두 출처 정보는 모두 보존했습니다. 출처가 겹치는 이름은 양쪽 평가에 포함하므로 출처별 건수를 단순 합산하지 않습니다.",
              "- 다품목 후보는 미확정 목록의 정규식에 의한 실험용 표시입니다. 그 부분의 약지도 라벨 점수는 정책 준수 정확도가 아닙니다.",
              "- 이름이 모호하거나 충돌 비율이 80% 미만인 경우는 학습·평가에서 제외되어 실제 거래보다 쉬운 분포일 수 있습니다.",
              "- 통판 일부 카테고리는 표본이 적거나 없습니다. 출처별 macro-F1은 10개 고정 라벨 평균이고 지지 건수도 함께 확인해야 합니다.",
              "- 오답샘플은 각 정답→예측 쌍에서 확신도가 높은 순서로 최대 30개입니다. 검토판정은 사람 검토 전 공란입니다.",
              "- 최종 모델의 기준선은 valid에서 선택한 후 test를 한 번 평가했습니다. 테스트 곡선을 보고 기준선을 바꾸지 않습니다.",
              "- 실제 기타·미분류의 오확정 평가는 별도 실거래 평가셋이 필요하며 이번 10개 카테고리 테스트에는 포함되지 않습니다."]
    (directory / "평가요약.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args):
    data_directory = args.data.resolve()
    directory = work_path(getattr(args, "output", None) or ROOT / "reports/spending_classifier" / data_directory.name)
    metrics_path = directory / "metrics.json"
    config = {key: getattr(args, key) for key in ("sample_cap", "c_values", "models", "folds", "seed", "max_iter", "threads", "target_accuracy", "min_accepted")}
    config["m1_solver_policy"] = {"C_below_4": "saga", "C_at_least_4": "newton-cg"}
    frame, prep = load_prepared(data_directory)
    directory.mkdir(parents=True, exist_ok=True)
    metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {
        "status": "running", "config": config, "prepared_sha256": prep["prepared_sha256"],
        "versions": {name: importlib.metadata.version(name) for name in ("numpy", "pandas", "scikit-learn", "scipy", "joblib", "matplotlib")},
        "python": platform.python_version(), "candidates": {},
        "limits": "약지도 고유 상호명 평가. 실제 거래/기타/미분류 성능 미측정"}
    if metrics["config"] != config or metrics["prepared_sha256"] != prep["prepared_sha256"]:
        raise ValueError("이전 실행과 설정/입력이 다릅니다. 다른 출력 디렉터리를 사용하세요")
    if metrics["status"] == "complete":
        log("Already complete; test is not reopened")
        return
    requested_model = getattr(args, "model_output", None)
    if "final" in metrics:
        # 이전 실험은 data 폴더에 저장했으므로 해당 기록도 계속 읽을 수 있다.
        model_path = (directory / metrics["final"].get("model_path", "model.joblib")).resolve()
        if requested_model is not None and requested_model.resolve() != model_path:
            raise ValueError("이미 잠근 모델의 저장 경로와 --model-output이 다릅니다")
    else:
        model_path = (requested_model or ROOT / "models" / "spending_classifier" / "artifacts" / directory.name / "spending_category_svm.joblib").resolve()
    model_path = work_path(model_path)
    train = frame.loc[frame["split"] == "train"].copy()
    valid = frame.loc[frame["split"] == "valid"].copy()
    if "valid_baselines" not in metrics:
        metrics["valid_baselines"] = baseline_metrics(valid)
        write_json(metrics_path, metrics)
    if args.phase in {"all", "compare"}:
        for kind in args.models:
            for c in (args.c_values if kind == "M1" else [1.0]):
                for exclude in (False, True):
                    key = f"{kind}_C{c:g}_{'exclude' if exclude else 'include'}_brands"
                    if key in metrics["candidates"]:
                        continue
                    sample = sample_train(train, args.sample_cap, exclude, args.seed)
                    model, fit = fit_model(sample, kind, c, args)
                    with threadpool_limits(limits=args.threads):
                        probabilities = predict_probabilities(model, valid["name"].to_numpy())
                    result = evaluate(valid, probabilities)
                    metrics["candidates"][key] = {"kind": kind, "C": c, "exclude_brands": exclude,
                                                  "fit": fit, "valid": result}
                    write_json(metrics_path, metrics)
                    log(f"{key}: valid macro-F1={result['macro_f1']:.5f}, accuracy={result['accuracy']:.5f}")
                    del sample, model, probabilities
                    gc.collect()
        if args.phase == "compare":
            return
    if not metrics["candidates"]:
        raise ValueError("후보 비교를 먼저 실행하세요")
    expected = {f"{kind}_C{c:g}_{choice}_brands"
                for kind in args.models
                for c in (args.c_values if kind == "M1" else [1.0])
                for choice in ("include", "exclude")}
    missing = expected - set(metrics["candidates"])
    if missing:
        raise ValueError(f"후보 비교가 끝나지 않아 최종 모델을 선택할 수 없습니다: {sorted(missing)}")
    if "final" not in metrics:
        key = max(metrics["candidates"], key=lambda key: metrics["candidates"][key]["valid"]["macro_f1"])
        winner = metrics["candidates"][key]
        final_train = train.loc[~train["brand_candidate"]] if winner["exclude_brands"] else train
        # Read both the original C key and the renamed key in older local runs.
        c = winner["C"] if "C" in winner else winner["bert_base_data"]
        model, fit = fit_model(final_train, winner["kind"], c, args)
        with threadpool_limits(limits=args.threads):
            probabilities = predict_probabilities(model, valid["name"].to_numpy())
        validation = evaluate(valid, probabilities)
        threshold = select_threshold(validation["thresholds"], args.target_accuracy, args.min_accepted)
        bundle = {"estimator": model, "categories": CATEGORIES, "normalization_version": NORMALIZATION_VERSION,
                  "threshold": threshold, "threshold_scope": "weak_label_validation_only_not_production",
                  "candidate_id": key, "prepared_sha256": prep["prepared_sha256"], "versions": metrics["versions"]}
        if model_path.exists():
            raise FileExistsError(f"기존 모델을 덮어쓰지 않습니다: {model_path}")
        model_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(bundle, model_path, compress=3)
        metrics["final"] = {"candidate_id": key, "fit": fit, "valid": validation,
                            "selected_threshold": threshold, "model_sha256": sha256(model_path),
                            "model_path": Path(os.path.relpath(model_path, directory)).as_posix()}
        metrics["status"] = "final_model_and_threshold_locked"
        write_json(metrics_path, metrics)
        del model, probabilities, final_train
    del train, valid
    gc.collect()
    if args.phase == "fit-final":
        return
    if metrics["status"] == "test_started":
        raise RuntimeError("테스트가 이미 시작됐습니다. 잠긴 모델의 중단 복구 여부를 확인해야 합니다")
    if sha256(model_path) != metrics["final"]["model_sha256"]:
        raise ValueError("잠근 최종 모델이 변경됐습니다")
    bundle = joblib.load(model_path)
    metrics["status"] = "test_started"
    write_json(metrics_path, metrics)
    log("Model and threshold locked. Opening test once")
    test_frame = frame.loc[frame["split"] == "test"].copy()
    del frame
    with threadpool_limits(limits=args.threads):
        probabilities = predict_probabilities(bundle["estimator"], test_frame["name"].to_numpy())
    test = evaluate(test_frame, probabilities)
    metrics["final"]["test"] = test
    metrics["test_baselines"] = baseline_metrics(test_frame)
    threshold = metrics["final"]["selected_threshold"]
    metrics["final"]["test_at_locked_threshold"] = threshold_metrics(test_frame["category"].to_numpy(), probabilities, [threshold])[0] if threshold is not None else None
    write_json(directory / "error_samples.json", error_samples(test_frame, probabilities))
    write_json(metrics_path, metrics)
    make_plots(directory, test)
    write_summary(directory, metrics, data_directory)
    metrics["status"] = "complete"
    metrics["finished_at"] = datetime.now(timezone.utc).isoformat()
    metrics["human_error_review"] = "pending"
    write_json(metrics_path, metrics)
    log(f"Complete: test macro-F1={test['macro_f1']:.5f}, accuracy={test['accuracy']:.5f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="평가 결과 폴더. 기본: reports/spending_classifier/<데이터 폴더명>")
    parser.add_argument("--model-output", type=Path, help="기본: models/spending_classifier/artifacts/<실험 폴더명>/spending_category_svm.joblib")
    parser.add_argument("--phase", choices=["all", "compare", "fit-final", "evaluate"], default="all")
    parser.add_argument("--sample-cap", type=int, default=60000)
    parser.add_argument("--c-values", type=float, nargs="+", default=[0.5, 1.0, 4.0])
    parser.add_argument("--models", nargs="+", choices=["M1", "M2", "M3"], default=["M1", "M2"])
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-iter", type=int, default=500)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--cv-jobs", type=int, default=1, help="동시 교차검증 구간 수. 모델 설정과 별도로 fit 기록에 저장")
    parser.add_argument("--target-accuracy", type=float, default=0.9)
    parser.add_argument("--min-accepted", type=int, default=30)
    args = parser.parse_args()
    if args.sample_cap < args.folds or args.folds < 2 or not 0 < args.target_accuracy <= 1 or args.threads < 1 or args.cv_jobs < 1:
        parser.error("학습/평가 인자 범위 오류")
    run(args)


if __name__ == "__main__":
    main()

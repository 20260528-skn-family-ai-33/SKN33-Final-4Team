"""고정 데이터로 A/B(SVM), C/D(KLUE-BERT) 비교. 명시적으로 실행할 때만 학습한다."""
import argparse
import gc
import importlib.metadata
import importlib.util
import json
import time
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import psutil
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from sklearn.pipeline import make_pipeline
from sklearn.svm import LinearSVC
from threadpoolctl import threadpool_limits

from scripts.spending_classifier.common import CATEGORIES, CODES, NORMALIZATION_VERSION, ROOT, sha256, write_json
from scripts.spending_classifier.evaluate_csv import excel_safe, outside_scores, scores


ARMS = {"svm_base_data": ("svm", False), "B": ("svm", True), "bert_base_data": ("bert", False), "D": ("bert", True)}
ARM_DIRECTORIES = {"svm_base_data": "svm_base_data", "B": "svm_augmented_data", "bert_base_data": "bert_base_data", "D": "bert_augmented_data"}


def comparison_results(directory, seed):
    return ROOT / "reports/spending_classifier/comparisons" / directory.name / f"seed_{seed}"


def log(message):
    print(message, flush=True)


def load_frame(directory, name):
    return pd.read_json(directory / f"{name}.jsonl", orient="records", lines=True, dtype=False)


def training_rows(frame, arm):
    include = ARMS[arm][1]
    selected = frame if include else frame.loc[frame["source"].eq("base")]
    if include and not selected["source"].eq("augmentation").any():
        raise ValueError(f"{arm}에는 보강 데이터가 필요합니다. comparison_data --augmentation으로 새 실험을 준비하세요")
    if set(selected["category"]) != set(CODES):
        raise ValueError("학습 표본에 10개 카테고리가 모두 필요합니다")
    return selected.reset_index(drop=True)


def fit_calibrators(raw, labels):
    if set(labels) != set(CODES):
        raise ValueError("독립 확률 보정셋에 10개 카테고리가 모두 필요합니다")
    calibrators = []
    for i, code in enumerate(CODES):
        # 두 모델 모두 독립 보정셋에 같은 one-vs-rest sigmoid 방식을 적용한다.
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            calibrator = LogisticRegression(C=1e6, max_iter=1000).fit(raw[:, [i]], np.asarray(labels) == code)
        calibrators.append(calibrator)
    return calibrators


def calibrated_probabilities(raw, calibrators):
    if raw.ndim != 2 or raw.shape[1] != len(CODES) or not np.isfinite(raw).all():
        raise ValueError("유효하지 않은 모델 점수")
    if len(raw) == 0:
        return np.empty((0, len(CODES)))
    probability = np.column_stack([model.predict_proba(raw[:, [i]])[:, list(model.classes_).index(True)]
                                   for i, model in enumerate(calibrators)])
    probability = np.maximum(probability, np.finfo(float).tiny)
    return probability / probability.sum(axis=1, keepdims=True)


def select_threshold(labels, probability, target, minimum):
    prediction = np.asarray(CODES)[probability.argmax(axis=1)]
    confidence = probability.max(axis=1)
    rows = []
    for threshold in np.round(np.arange(0.50, 1.00, 0.05), 2):
        mask = confidence >= threshold
        rows.append({"threshold": float(threshold), "count": int(mask.sum()),
                     "accuracy": float((prediction[mask] == np.asarray(labels)[mask]).mean()) if mask.any() else None})
    candidates = [row for row in rows if row["count"] >= minimum and row["accuracy"] >= target]
    return (candidates[0]["threshold"] if candidates else None), rows


def score_frame(frame, probability, threshold):
    output = frame.copy()
    order = np.argsort(-probability, axis=1, kind="stable")
    output["prediction"] = np.asarray(CODES)[order[:, 0]]
    output["second_prediction"] = np.asarray(CODES)[order[:, 1]]
    output["confidence"] = probability.max(axis=1) if len(probability) else np.array([])
    output["accepted"] = output["confidence"].ge(threshold) if threshold is not None else False
    if "label" not in output:
        output["label"] = output["category"]
    if "normalized" not in output:
        output["normalized"] = output["name"]
    if "weight" not in output:
        output["weight"] = 1
    return output


def evaluation_metrics(frame, threshold):
    primary = frame.loc[frame["group"].eq("primary_10_categories")]
    non_transport = scores(primary.loc[primary["label"].ne("TRANSPORT_CAR")], threshold)
    report = non_transport["per_category"]
    non_transport["macro_f1_9"] = (float(np.mean([report[code]["f1-score"] for code in CODES
                                                 if code != "TRANSPORT_CAR"])) if report else None)
    return {"primary": scores(primary, threshold),
            "non_transport": non_transport,
            "absent_from_old_data": scores(primary.loc[~primary["old_data_overlap"]], threshold),
            "confirmed_outside_10": {
                code: outside_scores(frame.loc[frame["group"].eq("confirmed_outside_10_categories") & frame["label"].eq(code)], threshold)
                for code in ("ETC", "UNCLASSIFIED")},
            "evaluation_groups": frame["group"].value_counts().to_dict()}


class SvmRunner:
    def __init__(self, args):
        self.args = args

    def fit(self, train, validation, directory):
        self.model = make_pipeline(TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 3), dtype=np.float32),
                                   LinearSVC(C=1, class_weight="balanced", dual="auto", max_iter=5000,
                                             random_state=self.args.seed))
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            self.model.fit(train["name"].tolist(), train["category"].tolist())
        joblib.dump(self.model, directory / "classifier.joblib", compress=3)
        return {"kind": "LinearSVC", "bert_base_data": 1, "class_weight": "balanced"}

    def raw_scores(self, names):
        if not len(names):
            return np.empty((0, len(CODES)))
        order = [list(self.model.classes_).index(code) for code in CODES]
        return self.model.decision_function(list(names))[:, order]


class BertRunner:
    def __init__(self, args):
        # SVM 실험은 torch/transformers 설치 없이 실행할 수 있다.
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer, set_seed
        from huggingface_hub import HfApi
        self.torch = torch
        self.args = args
        torch.set_num_threads(args.threads)
        set_seed(args.seed)
        self.device = "cuda" if args.device == "auto" and torch.cuda.is_available() else (
            "cpu" if args.device == "auto" else args.device)
        if self.device == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA를 사용할 수 없습니다. GPU 환경을 준비하거나 --device cpu를 지정하세요")
        source_path = args.data / "bert_source.json"
        if source_path.exists():
            source = json.loads(source_path.read_text(encoding="utf-8"))
            if source["model"] != args.bert_model or source["requested_revision"] != args.bert_revision:
                raise ValueError("이미 고정한 BERT 사전학습 모델과 설정이 다릅니다")
        else:
            source = {"model": args.bert_model, "requested_revision": args.bert_revision,
                      "revision": HfApi().model_info(args.bert_model, revision=args.bert_revision).sha}
            write_json(source_path, source)
        self.revision = source["revision"]
        self.tokenizer = AutoTokenizer.from_pretrained(args.bert_model, revision=self.revision, trust_remote_code=False)
        self.model = AutoModelForSequenceClassification.from_pretrained(
            args.bert_model, revision=self.revision, num_labels=len(CODES),
            id2label=dict(enumerate(CODES)), label2id={code: i for i, code in enumerate(CODES)},
            trust_remote_code=False, use_safetensors=True).to(self.device)

    def raw_scores(self, names):
        torch = self.torch
        self.model.eval()
        parts = []
        with torch.inference_mode():
            for start in range(0, len(names), self.args.batch_size):
                tokens = self.tokenizer(list(names[start:start + self.args.batch_size]), padding=True,
                                        truncation=True, max_length=self.args.max_length, return_tensors="pt")
                tokens = {key: value.to(self.device) for key, value in tokens.items()}
                parts.append(self.model(**tokens).logits.cpu().numpy())
        return np.concatenate(parts) if parts else np.empty((0, len(CODES)))

    def fit(self, train, validation, directory):
        torch = self.torch
        args = self.args
        target = np.array([CODES.index(code) for code in train["category"]])
        counts = np.bincount(target, minlength=len(CODES))
        weights = torch.tensor(len(target) / (len(CODES) * counts), dtype=torch.float32, device=self.device)
        loss_function = torch.nn.CrossEntropyLoss(weight=weights)
        optimizer = torch.optim.AdamW(self.model.parameters(), lr=args.learning_rate, weight_decay=0.01)
        rng = np.random.default_rng(args.seed)
        best, history = -1.0, []
        for epoch in range(args.epochs):
            self.model.train()
            order = rng.permutation(len(train))
            losses = []
            for step, start in enumerate(range(0, len(order), args.batch_size), 1):
                indices = order[start:start + args.batch_size]
                tokens = self.tokenizer(train.iloc[indices]["name"].tolist(), padding=True, truncation=True,
                                        max_length=args.max_length, return_tensors="pt")
                tokens = {key: value.to(self.device) for key, value in tokens.items()}
                labels = torch.tensor(target[indices], dtype=torch.long, device=self.device)
                optimizer.zero_grad(set_to_none=True)
                loss = loss_function(self.model(**tokens).logits, labels)
                if not torch.isfinite(loss):
                    raise ValueError("BERT 학습 손실이 유효하지 않습니다")
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                optimizer.step()
                losses.append(loss.item())
                if step % 50 == 0:
                    log(f"BERT epoch={epoch + 1}/{args.epochs} step={step} loss={np.mean(losses[-50:]):.4f}")
            logits = self.raw_scores(validation["name"].tolist())
            score = float(f1_score(validation["category"], np.asarray(CODES)[logits.argmax(axis=1)],
                                   labels=CODES, average="macro", zero_division=0))
            history.append({"epoch": epoch + 1, "loss": float(np.mean(losses)), "validation_macro_f1": score})
            log(f"BERT epoch={epoch + 1} validation macro-F1={score:.5f}")
            if score > best:
                best = score
                self.model.save_pretrained(directory / "bert", safe_serialization=True)
                self.tokenizer.save_pretrained(directory / "bert")
        del optimizer, loss, tokens, labels, weights, loss_function
        from transformers import AutoModelForSequenceClassification
        del self.model
        gc.collect()
        if self.device == "cuda":
            torch.cuda.empty_cache()
        self.model = AutoModelForSequenceClassification.from_pretrained(directory / "bert", use_safetensors=True).to(self.device)
        return {"kind": "KLUE-BERT", "device": self.device, "history": history,
                "pretrained_revision": self.revision}


def run_arm(args, arm, manifest, model_root):
    result_dir = comparison_results(args.data, args.seed) / ARM_DIRECTORIES[arm]
    model_dir = model_root / f"seed_{args.seed}" / ARM_DIRECTORIES[arm]
    result_path = result_dir / "metrics.json"
    config = {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items() if key != "arms"}
    config["manifest_sha256"] = sha256(args.data / "manifest.json")
    if result_path.exists():
        saved = json.loads(result_path.read_text(encoding="utf-8"))
        if saved["config"] != config or saved["status"] != "complete":
            raise ValueError(f"{arm}: 기존 실행과 설정이 다릅니다. 새 비교 폴더를 사용하세요")
        for relative, digest in saved["model_files"].items():
            if sha256(model_dir / relative) != digest:
                raise ValueError("저장된 비교 모델 파일이 변경됐습니다")
        log(f"{arm}: 완료된 결과 사용(재학습/재평가 없음)")
        return saved
    if result_dir.exists() or model_dir.exists():
        raise FileExistsError(f"{arm}: 중단된 실행 폴더가 있습니다. 보존 후 새 --model-root와 새 비교 폴더를 사용하세요")
    train = training_rows(load_frame(args.data, "train"), arm)
    calibration = load_frame(args.data, "calibration")
    # 보강 효과를 분리하기 위해 확률 보정은 4조건 모두 동일한 기존 데이터에만 맞춘다.
    calibration = calibration.loc[calibration["source"].eq("base")]
    validation = load_frame(args.data, "validation")
    result_dir.mkdir(parents=True)
    model_dir.mkdir(parents=True)
    write_json(result_dir / "state.json", {"status": "training", "config": config})
    log(f"{arm}: {ARMS[arm][0]} 학습 {len(train):,}행, 보강 {int(train['source'].eq('augmentation').sum()):,}행")
    started = time.perf_counter()
    runner = SvmRunner(args) if ARMS[arm][0] == "svm" else BertRunner(args)
    with threadpool_limits(limits=args.threads):
        fit_info = runner.fit(train, validation, model_dir)
        calibrators = fit_calibrators(runner.raw_scores(calibration["name"].tolist()), calibration["category"].tolist())
        valid_probability = calibrated_probabilities(runner.raw_scores(validation["name"].tolist()), calibrators)
    threshold, threshold_curve = select_threshold(validation["category"], valid_probability, args.target_accuracy, args.min_accepted)
    valid_scored = score_frame(validation, valid_probability, threshold)
    metadata = {"arm": arm, "categories": CATEGORIES, "normalization_version": NORMALIZATION_VERSION,
                "threshold": threshold, "config": config, "fit": fit_info,
                "train_rows": len(train), "train_augmentation_rows": int(train["source"].eq("augmentation").sum()),
                "calibration": "같은 독립 base 보정셋의 one-vs-rest sigmoid + 합계 정규화",
                "training_and_calibration_seconds": time.perf_counter() - started,
                "process_rss_after_fit_mb": psutil.Process().memory_info().rss / 2**20}
    joblib.dump(calibrators, model_dir / "calibrators.joblib")
    write_json(model_dir / "metadata.json", metadata)
    model_files = {path.relative_to(model_dir).as_posix(): sha256(path) for path in model_dir.rglob("*") if path.is_file()}
    # 모델/기준선을 저장한 뒤에만 외부 평가 자료를 읽는다.
    write_json(result_dir / "state.json", {"status": "evaluation_started", "model_files": model_files})
    if sha256(args.data / "evaluation.jsonl") != manifest["files"]["evaluation.jsonl"]:
        raise ValueError("학습 중 고정 평가 데이터가 변경됐습니다")
    external = load_frame(args.data, "evaluation")
    eligible = external["non_consumption"].eq("N") & external["normalized"].ne("")
    subset = external.loc[eligible].copy()
    started = time.perf_counter()
    with threadpool_limits(limits=args.threads):
        probability = calibrated_probabilities(runner.raw_scores(subset["name"].tolist()), calibrators)
    inference_seconds = time.perf_counter() - started
    scored = score_frame(subset, probability, threshold)
    all_rows = external.copy()
    for col in ["prediction", "second_prediction", "confidence", "accepted"]:
        all_rows[col] = scored[col].reindex(all_rows.index)
    all_rows["accepted"] = all_rows["accepted"].eq(True)
    result = {"status": "complete", "arm": arm, "scope": manifest["scope"], "config": config,
              "model_files": model_files, "fit": metadata,
              "validation": scores(valid_scored, threshold),
              "validation_by_source": {source: scores(valid_scored.loc[valid_scored["source"].eq(source)], threshold)
                                       for source in ("base", "augmentation")},
              "validation_threshold_curve": threshold_curve,
              "evaluation": evaluation_metrics(all_rows, threshold),
              "inference_seconds": inference_seconds, "inference_rows": len(subset),
              "inference_ms_per_row": inference_seconds * 1000 / len(subset) if len(subset) else None,
              "versions": {package: importlib.metadata.version(package) for package in
                           (["numpy", "pandas", "scikit-learn", "scipy", "joblib"] +
                            (["torch", "transformers"] if ARMS[arm][0] == "bert" else []))},
              "notes": ["개발 평가 결과를 신규 테스트 성능으로 해석하지 않음",
                        "규칙·사전·LLM 없는 ML 단독 비교. 라벨 확정 여부는 입력 검수 기록에 의존",
                        "기존 전체 학습 모델과 표본·보정 방식이 다름. A~D 안에서 효과를 비교"]}
    all_rows.map(excel_safe).to_csv(result_dir / "predictions.csv", index=False, encoding="utf-8-sig")
    write_json(result_path, result)
    write_json(result_dir / "state.json", {"status": "complete"})
    del runner
    gc.collect()
    log(f"{arm}: 완료. 결과 {result_path}")
    return result


def write_comparison(directory, seed):
    rows, results = [], {}
    for arm in ARMS:
        path = comparison_results(directory, seed) / ARM_DIRECTORIES[arm] / "metrics.json"
        if not path.exists():
            continue
        result = json.loads(path.read_text(encoding="utf-8"))
        results[arm] = result
        primary = result["evaluation"]["primary"]
        transport = primary["per_category"].get("TRANSPORT_CAR", {})
        rows.append({"arm": arm, "scope": result["scope"], "rows": primary["rows"],
                     "accuracy": primary["accuracy"], "macro_f1_10": primary["macro_f1_10"],
                     "macro_f1_supported": primary["macro_f1_supported"],
                     "transport_f1": transport.get("f1-score"), "transport_recall": transport.get("recall"),
                     "non_transport_macro_f1_9": result["evaluation"]["non_transport"]["macro_f1_9"],
                     "threshold": primary["at_saved_threshold"]["threshold"],
                     "coverage": primary["at_saved_threshold"]["coverage"],
                     "accepted_accuracy": primary["at_saved_threshold"]["accuracy"],
                     "fit_seconds": result["fit"]["training_and_calibration_seconds"],
                     "inference_ms_per_row": result["inference_ms_per_row"]})
    output = comparison_results(directory, seed)
    pd.DataFrame(rows).to_csv(output / "comparison.csv", index=False, encoding="utf-8-sig")
    differences = {}
    for left, right, description in [("svm_base_data", "B", "SVM 데이터 보강"), ("svm_base_data", "bert_base_data", "기존 데이터에서 모델 교체"),
                                      ("B", "D", "보강 데이터에서 모델 교체"), ("bert_base_data", "D", "BERT 데이터 보강")]:
        if left in results and right in results:
            a = results[left]["evaluation"]["primary"]["macro_f1_10"]
            b = results[right]["evaluation"]["primary"]["macro_f1_10"]
            differences[f"{left}_to_{right}"] = {"meaning": description, "macro_f1_10_delta": b - a if a is not None and b is not None else None}
    write_json(output / "differences.json", differences)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--arms", nargs="+", choices=list(ARMS), default=["svm_base_data", "B", "bert_base_data", "D"])
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--target-accuracy", type=float, default=0.9)
    parser.add_argument("--min-accepted", type=int, default=30)
    parser.add_argument("--bert-model", default="klue/bert-base")
    parser.add_argument("--bert-revision", default="main", help="첫 실행에서 실제 commit SHA를 고정해 C/D가 공유")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--max-length", type=int, default=64)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    args = parser.parse_args()
    if min(args.threads, args.epochs, args.batch_size, args.max_length, args.min_accepted) < 1 or not 0 < args.target_accuracy <= 1 or args.learning_rate <= 0:
        parser.error("실험 설정 범위 오류")
    args.data = args.data.resolve()
    if any(arm in {"bert_base_data", "D"} for arm in args.arms):
        missing = [module for module in ("torch", "transformers", "safetensors", "huggingface_hub")
                   if importlib.util.find_spec(module) is None]
        if missing:
            parser.error(f"BERT 의존성 누락: {missing}. requirements-bert.txt를 먼저 설치하세요")
    manifest = json.loads((args.data / "manifest.json").read_text(encoding="utf-8"))
    if manifest["categories"] != CATEGORIES or manifest["normalization_version"] != NORMALIZATION_VERSION:
        raise ValueError("카테고리 기준 불일치")
    for filename, digest in manifest["files"].items():
        if sha256(args.data / filename) != digest:
            raise ValueError(f"고정한 비교 데이터가 변경됐습니다: {filename}")
    if any(arm in {"B", "D"} for arm in args.arms):
        training_rows(load_frame(args.data, "train"), "B")
    # A/B 실행 뒤 C/D를 실행해도 비교 설정은 동일해야 한다.
    settings = {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items() if key != "arms"}
    settings_path = args.data / f"settings_seed_{args.seed}.json"
    if settings_path.exists() and json.loads(settings_path.read_text(encoding="utf-8")) != settings:
        raise ValueError("같은 seed의 비교 설정이 바뀌었습니다. 별도 비교 폴더를 사용하세요")
    write_json(settings_path, settings)
    model_root = args.model_root or ROOT / "models/spending_classifier/artifacts/comparisons" / args.data.name
    for arm in args.arms:
        run_arm(args, arm, manifest, model_root)
        write_comparison(args.data, args.seed)


if __name__ == "__main__":
    main()

from contextlib import redirect_stdout
import csv
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import warnings

import numpy as np
import pandas as pd

from scripts.spending_classifier.common import CATEGORIES, NORMALIZATION_VERSION, normalize_name, keyword_prediction
from scripts.spending_classifier.predict import predict
from scripts.spending_classifier.prepare_data import assign_splits, prepare
from scripts.spending_classifier.train_eval import (
    make_model, predict_probabilities, sample_train, select_threshold, threshold_metrics, run, load_prepared, fit_model, evaluate, error_samples,
)
from scripts.spending_classifier.common import CODES


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(fields)
        writer.writerows(rows)


class PreparationTests(unittest.TestCase):
    def test_normalization_preserves_meaning_and_branches(self):
        self.assertEqual(normalize_name("㈜ 청연(호텔비즈인) 강남점"), "청연호텔비즈인강남점")
        self.assertEqual(normalize_name("ＡＢＣ (유)"), "abc")
        self.assertEqual(normalize_name("정육점"), "정육점")
        self.assertEqual(normalize_name("(주) - ()"), "")
        with self.assertRaises(TypeError):
            normalize_name(None)

    def test_policy_exceptions_in_baseline(self):
        for name, label in [("새봄성형외과", "MEDICAL_HEALTH"), ("새봄운전학원", "EDUCATION"),
                            ("새봄마사지", "CULTURE_LEISURE"), ("새봄동물병원", "MEDICAL_HEALTH"),
                            ("새봄스터디카페", "EDUCATION")]:
            self.assertEqual(keyword_prediction(name), (label, True))
        self.assertEqual(keyword_prediction("아지트"), ("FOOD", False))

    def test_group_splits_are_disjoint_and_reproducible(self):
        names = np.array([f"name{i}" for i in range(1000)])
        first = assign_splits(names)
        np.testing.assert_array_equal(first, assign_splits(names))
        self.assertEqual(dict(zip(*np.unique(first, return_counts=True))), {"test": 100, "train": 800, "valid": 100})
        with self.assertRaises(ValueError):
            assign_splits(["duplicate", "duplicate"])

    def test_real_preparation_preserves_counts_filters_sources_and_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, mail, brands = [root / n for n in ("store.csv", "mail.csv", "brands.csv")]
            rows = [[f"{code}가게{i}", label, "Y"] for code, label in CATEGORIES.items() for i in range(100)]
            rows += [["(주)다수상호", "식비", "Y"] for _ in range(8)]
            rows += [["다수상호", "교육", "Y"] for _ in range(2)]
            rows += [["갈림", "식비", "Y"], ["갈림", "교육", "Y"], ["제외유지", "식비", "N"], ["(주)", "식비", "Y"]]
            write_csv(store, ["상호명", "소비카테고리", "학습대상여부"], rows)
            write_csv(mail, ["상호", "라벨", "학습사용"], [["다수상호", "식비", "Y"], ["제외유지", "기타", "N"]])
            write_csv(brands, ["브랜드명", "검색키_정규식"], [["테스트후보", "^다수상호$"]])
            before = [p.read_bytes() for p in (store, mail, brands)]
            with redirect_stdout(io.StringIO()):
                prepare(store, mail, brands, root / "out")
            frame = pd.read_csv(root / "out/prepared.csv", keep_default_na=False).set_index("name")
            self.assertEqual(frame.loc["다수상호", "category"], "FOOD")
            self.assertEqual(frame.loc["다수상호", "original_count"], 11)
            self.assertEqual(frame.loc["다수상호", "majority_count"], 9)
            self.assertEqual(frame.loc["다수상호", "source_mask"], 3)
            self.assertTrue(frame.loc["다수상호", "brand_candidate"])
            self.assertNotIn("갈림", frame.index)
            self.assertNotIn("제외유지", frame.index)
            self.assertNotIn("", frame.index)
            self.assertTrue(frame.index.is_unique)
            self.assertEqual(before, [p.read_bytes() for p in (store, mail, brands)])
            report = json.loads((root / "out/preparation.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["sources"]["store"]["empty_normalized_rows"], 1)
            with self.assertRaises(FileExistsError):
                prepare(store, mail, brands, root / "out")
            args = SimpleNamespace(data=root / "out", phase="all", sample_cap=20, c_values=[1.0],
                                   models=["M1", "M2"], folds=2, seed=42, max_iter=500, threads=1,
                                   target_accuracy=0.9, min_accepted=1, model_output=root / "models/model.joblib",
                                   output=root / "evaluation_results")
            with redirect_stdout(io.StringIO()):
                run(args)
            metrics_path = args.output / "metrics.json"
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            self.assertEqual(metrics["status"], "complete")
            self.assertEqual(len(metrics["candidates"]), 4)
            self.assertEqual(metrics["final"]["test"]["count"], int((frame["split"] == "test").sum()))
            self.assertGreater(args.model_output.stat().st_size, 0)
            self.assertFalse((root / "out/model.joblib").exists())
            self.assertEqual((args.output / metrics["final"]["model_path"]).resolve(), args.model_output.resolve())
            self.assertFalse((args.data / "metrics.json").exists())
            for filename in ("confusion_matrix.png", "threshold_curve.png", "평가요약.md", "error_samples.json"):
                self.assertGreater((args.output / filename).stat().st_size, 0)
            locked = metrics_path.read_bytes()
            with redirect_stdout(io.StringIO()):
                run(args)
            self.assertEqual(metrics_path.read_bytes(), locked)
            self.assertTrue(all("C" in candidate for candidate in metrics["candidates"].values()))
            # Resume local runs whose regularization key was renamed during file organization.
            legacy = json.loads(locked)
            legacy.pop("final")
            legacy["status"] = "running"
            for candidate in legacy["candidates"].values():
                candidate["bert_base_data"] = candidate.pop("C")
            metrics_path.write_text(json.dumps(legacy), encoding="utf-8")
            args.phase = "fit-final"
            args.model_output = root / "models/resumed.joblib"
            with redirect_stdout(io.StringIO()):
                run(args)
            resumed = json.loads(metrics_path.read_text(encoding="utf-8"))
            self.assertEqual(resumed["final"]["candidate_id"], metrics["final"]["candidate_id"])
            self.assertTrue(args.model_output.is_file())
            # Interrupted comparisons must not silently choose from a partial candidate set.
            args.model_output = root / "models/model.joblib"
            metrics["status"] = "running"
            metrics["candidates"].pop(next(iter(metrics["candidates"])))
            metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
            args.phase = "fit-final"
            with self.assertRaisesRegex(ValueError, "후보 비교가 끝나지"):
                run(args)


class TrainingTests(unittest.TestCase):
    def test_probability_ties_are_consistent_in_evaluation_and_prediction(self):
        frame = pd.DataFrame({"name": ["a", "b"], "category": CODES[:2], "source_mask": 1, "brand_candidate": False})
        probabilities = np.full((2, 10), 0.1)
        result = evaluate(frame, probabilities)
        self.assertEqual(result["accuracy"], 0.5)
        self.assertEqual(result["top2_accuracy"], 1.0)
        self.assertEqual(error_samples(frame, probabilities)[0]["2순위"], CATEGORIES[CODES[1]])
        estimator = Mock()
        estimator.classes_ = np.array(CODES)
        estimator.predict_proba.return_value = probabilities[:1]
        bundle = {"estimator": estimator, "normalization_version": NORMALIZATION_VERSION, "threshold": 0.5}
        self.assertEqual(predict(bundle, ["a"])[0]["category"], CODES[0])

    def test_line_search_failure_is_not_reported_as_convergence(self):
        frame = pd.DataFrame({"name": [f"n{i}" for i in range(20)], "category": CODES * 2, "split": "train"})
        args = SimpleNamespace(folds=2, seed=42, max_iter=5, threads=1, cv_jobs=1)
        model = Mock()
        model.fit.side_effect = lambda *a: warnings.warn("Line Search failed")
        with patch("scripts.spending_classifier.train_eval.make_model", return_value=model), \
             redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "수렴하지"):
            fit_model(frame, "M1", 4, args)
        self.assertEqual(model.fit.call_count, 2)

    def test_incompatible_normalization_is_rejected_before_training(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "preparation.json").write_text(json.dumps({"normalization_version": "old"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "정규화 버전"):
                load_prepared(root)

    def test_only_train_is_sampled_and_brands_are_optional(self):
        frame = pd.DataFrame({"name": [f"n{i}" for i in range(12)], "category": ["FOOD"] * 6 + ["EDUCATION"] * 6,
                              "split": ["train"] * 12, "brand_candidate": [True, False] * 6})
        full = sample_train(frame, 4, False)
        excluded = sample_train(frame, 4, True)
        self.assertEqual(full.groupby("category").size().tolist(), [4, 4])
        self.assertFalse(excluded["brand_candidate"].any())
        self.assertEqual(set(excluded["name"]), set(frame.loc[~frame["brand_candidate"], "name"]))
        frame.loc[0, "split"] = "test"
        with self.assertRaises(ValueError):
            sample_train(frame, 4, False)

    def test_threshold_denominator_and_empty_selection(self):
        p = np.full((4, 10), 0.01)
        p[:, 0] = [0.91, 0.81, 0.61, 0.41]
        p[:, 1] = 1 - p[:, 0] - 0.08
        y = np.array([CODES[0], CODES[1], CODES[0], CODES[0]])
        rows = threshold_metrics(y, p, [0.8, 0.9, 0.95])
        self.assertEqual(rows[0]["accepted"], 2)
        self.assertEqual(rows[0]["coverage"], 0.5)
        self.assertEqual(rows[0]["accuracy"], 0.5)
        self.assertIsNone(rows[2]["accuracy"])
        self.assertEqual(select_threshold(rows, 0.9, min_accepted=1), 0.9)
        self.assertIsNone(select_threshold(rows, 0.9, min_accepted=2))

    def test_calibration_wraps_pipeline_and_output_order_is_explicit(self):
        names = np.array([f"{code.lower()}shop{i}" for code in CODES for i in range(10)])
        y = np.repeat(CODES, 10)
        model = make_model("M1", 1, 2, 42, 500)
        model.fit(names, y)
        self.assertFalse(model.ensemble)
        self.assertEqual(len(model.calibrated_classifiers_), 1)
        self.assertEqual(list(model.estimator.named_steps), ["tfidf", "classifier"])
        p = predict_probabilities(model, names[:5], batch_size=2)
        np.testing.assert_allclose(p.sum(axis=1), 1)
        expected = model.predict_proba(names[:5])[:, [list(model.classes_).index(code) for code in CODES]]
        np.testing.assert_allclose(p, expected)
        parallel = make_model("M1", 1, 2, 42, 500, cv_jobs=2)
        import joblib
        with joblib.parallel_backend("threading", n_jobs=2):
            parallel.fit(names, y)
        np.testing.assert_allclose(predict_probabilities(parallel, names[:5]), p, atol=1e-6)
        high_c = make_model("M1", 4, 2, 42, 500)
        self.assertEqual(high_c.estimator.named_steps["classifier"].solver, "newton-cg")
        high_c.fit(names, y)
        np.testing.assert_allclose(predict_probabilities(high_c, names[:5]).sum(axis=1), 1)


if __name__ == "__main__":
    unittest.main()

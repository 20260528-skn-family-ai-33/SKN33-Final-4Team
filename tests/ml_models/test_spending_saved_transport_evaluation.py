"""저장 SVM 예측의 규칙 평가 검증용 합성 테스트. 실제 모델은 사용하지 않는다."""
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from scripts.spending_classifier.common import NORMALIZATION_VERSION, sha256, write_json
from scripts.spending_classifier.evaluate_csv import SOURCE_COLUMNS, excel_safe, read_labels, scores
from scripts.spending_classifier.evaluate_transport_rules import evaluate_saved


class SavedTransportEvaluationTests(unittest.TestCase):
    def fixture(self, root):
        source = root / "labels.csv"
        results = root / "results"
        results.mkdir()
        rows = [
            ["1월교통카드이용", 1, "N", "TRANSPORT_CAR", "교통·차량", "높음", "합성 테스트", "N"],
            ["식당", 1, "N", "FOOD", "식비", "높음", "합성 테스트", "N"],
            ["문화시설", 1, "N", "CULTURE_LEISURE", "문화·여가", "높음", "합성 테스트", "N"],
        ]
        pd.DataFrame(rows, columns=SOURCE_COLUMNS).to_csv(source, index=False, encoding="utf-8-sig")
        frame = read_labels(source)
        frame["prediction"] = ["BEAUTY", "FOOD", "EDUCATION"]
        frame["second_prediction"] = ["FOOD", "BEAUTY", "CULTURE_LEISURE"]
        frame["confidence"] = [0.8, 0.8, 0.4]
        frame["accepted"] = [True, True, False]
        mapping = {"prediction": "ML예측코드", "second_prediction": "ML2순위코드",
                   "confidence": "ML확신도", "accepted": "저장기준선통과"}
        saved = frame[SOURCE_COLUMNS + ["입력행번호", *mapping]].rename(columns=mapping)
        saved["기존데이터분할"] = "absent"
        saved.map(excel_safe).to_csv(results / "predictions.csv", index=False, encoding="utf-8-sig")
        metric = scores(frame, 0.5)
        write_json(results / "metrics.json", {
            "input_sha256": sha256(source), "normalization_version": NORMALIZATION_VERSION,
            "candidate_id": "M2_C1_exclude_brands", "model_path": "not_loaded.joblib", "model_sha256": "synthetic",
            "overlap_check": {"status": "checked"}, "primary": metric,
            "primary_absent_from_original_train_valid_test": metric,
        })
        return source, results

    def test_rules_metrics_errors_and_original_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, results = self.fixture(root)
            originals = {p: sha256(p) for p in (source, results / "metrics.json", results / "predictions.csv")}
            output = root / "output"
            evaluate_saved(source, results, output)
            report = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
            stages = report["stages"]
            self.assertEqual(stages["rule_changes"]["fixed_primary_rows"], 1)
            self.assertEqual(stages["rule_changes"]["harmed_primary_rows"], 0)
            decision = stages["transport_rules_plus_ml"]["primary"]["decision"]
            self.assertEqual(decision["auto_wrong_rows"], 0)
            self.assertEqual(decision["llm_candidate_rows"], 1)
            after = pd.read_csv(output / "predictions.csv")
            self.assertTrue(pd.isna(after.loc[0, "confidence"]))
            self.assertEqual(after.loc[0, "ml_confidence"], 0.8)
            self.assertEqual(len(pd.read_csv(output / "errors.csv")), 1)
            self.assertEqual(len(pd.read_csv(output / "auto_errors.csv")), 0)
            self.assertEqual(originals, {p: sha256(p) for p in originals})
            with self.assertRaises(FileExistsError):
                evaluate_saved(source, results, output)

    def test_changed_label_input_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, results = self.fixture(root)
            with source.open("a", encoding="utf-8") as stream:
                stream.write("\n")
            with self.assertRaisesRegex(ValueError, "해시"):
                evaluate_saved(source, results, root / "output")
            self.assertFalse((root / "output").exists())


if __name__ == "__main__":
    unittest.main()

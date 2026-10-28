"""실데이터 없이 평가 분모·제외 정책·학습 중복 판정을 확인하는 테스트."""
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import pandas as pd

from scripts.spending_classifier.common import CODES, sha256
from scripts.spending_classifier.evaluate_csv import (
    SOURCE_COLUMNS, add_overlap, add_predictions, read_labels, scores, summarize,
)


class CsvEvaluationTests(TestCase):
    def make_frame(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "labels.csv"
            pd.DataFrame(rows, columns=SOURCE_COLUMNS).to_csv(path, index=False, encoding="utf-8-sig")
            return read_labels(path)

    def test_exclusions_and_unknown_labels(self):
        frame = self.make_frame([
            ["입금", "1", "Y", "", "비소비", "높음", "", "N"],
            ["혼재", "2", "혼재", "FOOD", "식비", "중간", "", "Y"],
            ["확인전", "3", "확인필요", "UNCLASSIFIED", "확인 필요 (미분류)", "낮음", "", "Y"],
            ["리뷰", "1", "N", "UNCLASSIFIED", "확인 필요 (미분류)", "낮음", "", "Y"],
            ["가게", "4", "N", "FOOD", "식비", "높음", "", "N"],
            ["세탁", "2", "N", "ETC", "기타", "높음", "", "N"],
            ["(주)", "1", "N", "FOOD", "식비", "높음", "", "N"],
        ])
        self.assertEqual(frame["group"].tolist(), [
            "excluded_non_consumption", "excluded_consumption_uncertain", "excluded_consumption_uncertain",
            "review_required", "primary_10_categories", "confirmed_outside_10_categories", "excluded_empty_name",
        ])
        with self.assertRaisesRegex(ValueError, "라벨 코드/이름 오류"):
            self.make_frame([["가게", "1", "N", "UNKNOWN", "식비", "높음", "", "N"]])
        with self.assertRaisesRegex(ValueError, "라벨 코드/이름 오류"):
            self.make_frame([["가게", "1", "N", "FOOD", "교육", "높음", "", "N"]])
        with self.assertRaisesRegex(ValueError, "등장횟수"):
            self.make_frame([["가게", "0", "N", "FOOD", "식비", "높음", "", "N"]])

    def test_predictions_use_names_only_and_skip_non_consumption(self):
        frame = self.make_frame([
            ["입금", "1", "Y", "", "비소비", "높음", "", "N"],
            ["가게", "5", "N", "FOOD", "식비", "높음", "정답 근거", "N"],
        ])
        probabilities = {code: 0.01 for code in CODES}
        probabilities["FOOD"] = 0.91
        prediction = {"category": "FOOD", "confidence": 0.91, "margin": 0.9,
                      "passes_experimental_threshold": True, "probabilities": probabilities}
        bundle = {"threshold": 0.5}
        with patch("scripts.spending_classifier.evaluate_csv.predict", return_value=[prediction]) as mocked:
            result = add_predictions(frame, bundle)
        mocked.assert_called_once_with(bundle, ["가게"])
        self.assertEqual(result["prediction"].tolist(), ["", "FOOD"])

    def test_weighted_accuracy_and_threshold_denominators(self):
        frame = pd.DataFrame({
            "label": ["FOOD", "EDUCATION"], "prediction": ["FOOD", "FOOD"],
            "second_prediction": ["EDUCATION", "EDUCATION"], "accepted": [True, False],
            "normalized": ["a", "b"], "weight": [9, 1],
        })
        result = scores(frame, 0.5)
        self.assertEqual(result["accuracy"], 0.5)
        self.assertEqual(result["occurrence_weighted_accuracy"], 0.9)
        self.assertEqual(result["top2_accuracy"], 1.0)
        self.assertEqual(result["at_saved_threshold"]["coverage"], 0.5)
        self.assertEqual(result["at_saved_threshold"]["accuracy"], 1.0)
        self.assertEqual(result["supported_category_count"], 2)
        self.assertEqual(scores(frame.iloc[:0], 0.5)["accuracy"], None)

    def test_review_required_unknown_is_not_false_accept_ground_truth(self):
        frame = pd.DataFrame({
            "group": ["review_required", "confirmed_outside_10_categories"],
            "label": ["UNCLASSIFIED", "ETC"], "prediction": ["FOOD", "FOOD"],
            "second_prediction": ["EDUCATION", "EDUCATION"], "accepted": [True, True],
            "normalized": ["a", "b"], "weight": [1, 1],
            "reference_split": ["absent", "absent"], "training_overlap": ["not_in_final_train"] * 2,
        })
        result = summarize(frame, {"threshold": 0.5})
        self.assertEqual(result["primary"]["rows"], 0)
        self.assertIsNone(result["confirmed_outside_10"]["UNCLASSIFIED"]["false_accept_rate"])
        self.assertEqual(result["confirmed_outside_10"]["ETC"]["false_accept_rate"], 1.0)

    def test_overlap_uses_final_train_brand_exclusion_and_checks_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prepared.csv"
            pd.DataFrame({"name": ["a", "b", "c"], "brand_candidate": [False, True, False],
                          "split": ["train", "train", "valid"]}).to_csv(path, index=False)
            bundle = {"prepared_sha256": sha256(path), "candidate_id": "M2_C1_exclude_brands"}
            frame = pd.DataFrame({"normalized": ["a", "b", "c", "d"]})
            result, metadata = add_overlap(frame, bundle, path)
            self.assertEqual(metadata["status"], "checked")
            self.assertEqual(result["training_overlap"].tolist(),
                             ["seen_in_final_train", "not_in_final_train", "not_in_final_train", "not_in_final_train"])
            self.assertEqual(result["reference_split"].tolist(), ["train", "train", "valid", "absent"])
            bundle["prepared_sha256"] = "changed"
            with self.assertRaisesRegex(ValueError, "학습 데이터와 다릅니다"):
                add_overlap(frame, bundle, path)

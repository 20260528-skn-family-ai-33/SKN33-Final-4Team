"""비교 실험의 분할·누수 차단·지표 정책 테스트. 실데이터/모델 다운로드 없음."""
import tempfile
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from scripts.spending_classifier.common import CODES
from scripts.spending_classifier.comparison_data import (
    assign_partition, group_resolver, read_augmentation, sample_by_category, signature, verify_splits,
)
from scripts.spending_classifier.compare_experiments import (
    evaluation_metrics, score_frame, select_threshold, training_rows,
)


class ComparisonTests(unittest.TestCase):
    def test_month_variants_and_manual_groups_cannot_split(self):
        frame = pd.DataFrame({"name": ["1월교통카드이용", "12월교통카드이용", "가게강남점", "가게홍대점"],
                              "group_id": ["교통카드", "", "같은브랜드", "같은브랜드"]})
        resolve = group_resolver(frame)
        self.assertEqual(signature("1월교통카드이용"), signature("12월교통카드이용"))
        self.assertEqual(resolve("1월교통카드이용"), resolve("12월교통카드이용"))
        self.assertEqual(resolve("가게강남점"), resolve("가게홍대점"))
        self.assertEqual(assign_partition(resolve("가게강남점"), 42), assign_partition(resolve("가게홍대점"), 42))
        reverse = group_resolver(frame.iloc[::-1])
        self.assertEqual([resolve(name) for name in frame.name], [reverse(name) for name in frame.name])

    def test_a_c_and_b_d_use_identical_training_rows(self):
        base = pd.DataFrame({"name": CODES, "category": CODES, "source": "base"})
        added = pd.DataFrame({"name": ["교통적요"], "category": ["TRANSPORT_CAR"], "source": "augmentation"})
        frame = pd.concat([base, added], ignore_index=True)
        pd.testing.assert_frame_equal(training_rows(frame, "svm_base_data"), training_rows(frame, "bert_base_data"))
        pd.testing.assert_frame_equal(training_rows(frame, "B"), training_rows(frame, "D"))
        self.assertEqual(len(training_rows(frame, "B")) - len(training_rows(frame, "svm_base_data")), 1)
        with self.assertRaisesRegex(ValueError, "보강 데이터"):
            training_rows(base, "B")

    def test_group_leakage_is_rejected(self):
        frames = {split: pd.DataFrame({"group_id": [f"{split}:{code}" for code in CODES],
                                        "source": "base", "category": CODES})
                  for split in ("train", "calibration", "validation", "evaluation")}
        verify_splits(frames)
        frames["evaluation"].loc[0, "group_id"] = frames["train"].loc[0, "group_id"]
        with self.assertRaisesRegex(ValueError, "그룹 누수"):
            verify_splits(frames)

    def test_sampling_is_stable_under_row_reordering(self):
        frame = pd.DataFrame({"name": [f"n{i}" for i in range(40)], "category": CODES * 4})
        first = sample_by_category(frame, 2, 42)
        second = sample_by_category(frame.iloc[::-1], 2, 42)
        pd.testing.assert_frame_equal(first, second)
        self.assertTrue(first.groupby("category").size().eq(2).all())

    def test_unverified_augmentation_is_not_silently_used(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "augmentation.csv"
            pd.DataFrame({"가맹점명": ["버스", "택시"], "category": ["교통·차량", "TRANSPORT_CAR"],
                          "group_id": ["버스", "택시"], "human_verified": ["N", "Y"]}).to_csv(path, index=False)
            frame, audit = read_augmentation(path)
            self.assertEqual(frame["name"].tolist(), ["택시"])
            self.assertEqual(audit["excluded_rows"], 1)

    def test_threshold_and_empty_outside_denominator(self):
        probability = np.full((2, 10), 0.01)
        probability[0, 0] = 0.91
        probability[1, 1] = 0.91
        threshold, _ = select_threshold(CODES[:2], probability, 0.9, 2)
        self.assertEqual(threshold, 0.5)
        self.assertIsNone(select_threshold(CODES[:2], probability, 0.9, 3)[0])
        frame = pd.DataFrame({"name": ["a", "b"], "category": CODES[:2],
                              "group": "primary_10_categories", "old_data_overlap": False})
        scored = score_frame(frame, probability, threshold)
        result = evaluation_metrics(scored, threshold)
        self.assertEqual(result["primary"]["accuracy"], 1.0)
        self.assertIsNone(result["confirmed_outside_10"]["ETC"]["false_accept_rate"])
        empty = score_frame(frame.iloc[:0], np.empty((0, 10)), None)
        self.assertIsNone(evaluation_metrics(empty, None)["primary"]["accuracy"])


if __name__ == "__main__":
    unittest.main()

"""Synthetic checks for row linkage, training filters and shared evaluation populations.

Model fitting is mocked. Run with unittest discovery from the repository root.
"""

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from scripts.budget_allocation import compare_sampling_strategies as ab
from scripts.budget_allocation.compare_allocation_models import prepare_data, target_ratios
from test_allocation_models import canonical_rows


def development_rows():
    source = canonical_rows([year for year in range(2019, 2025) for _ in range(4)])
    frame = prepare_data(source, range(2019, 2025))
    frame["employed_code"] = 1
    frame["employment_status_code"] = [1, 1, 2, 3] * 6
    # One permanent employee per year is within income, the other is over.
    frame["disposable_income"] = frame.fixed_expense_amount + frame.living_budget_amount
    frame.loc[frame.index % 4 == 1, "disposable_income"] -= 1
    return frame


class PerfectFake:
    def predict(self, frame):
        return target_ratios(frame), {"negative_clipped_rows": 0, "fallback_rows": 0}


class RowLinkTests(unittest.TestCase):
    def setUp(self):
        self.source = pd.DataFrame({
            "원본행번호": [4, 2], "조사연월": [201914, 201914],
            "나이": [30, 25], "성별": ["여", "남"], "가중값": [3.0, 1.0],
        })
        self.raw = pd.DataFrame({
            "조사연월": [201914] * 3, "가구원수": [1] * 3, "가구구분코드": [1] * 3,
            "가구주_연령": [25, 27, 30], "가구주_성별코드": [1, 1, 2],
            "가중값": [1.0, 2.0, 3.0], "가구주_취업여부": [1] * 3,
            "가구주_종사상지위코드": [1, 2, 3],
        })

    def test_header_offset_and_nonconsecutive_order_are_preserved(self):
        employed, status = ab.align_employment(self.source, self.raw)
        np.testing.assert_array_equal(employed, [1, 1])
        np.testing.assert_array_equal(status, [3, 1])

    def test_wrong_row_link_and_out_of_range_are_rejected(self):
        wrong = self.source.copy()
        wrong.loc[0, "나이"] = 29
        with self.assertRaises(AssertionError):
            ab.align_employment(wrong, self.raw)
        wrong.loc[0, "원본행번호"] = 5
        with self.assertRaisesRegex(ValueError, "범위"):
            ab.align_employment(wrong, self.raw)

    def test_missing_employment_status_cannot_silently_drop_employees(self):
        self.raw.loc[0, "가구주_종사상지위코드"] = np.nan
        with self.assertRaisesRegex(ValueError, "종사상지위 코드 누락"):
            ab.align_employment(self.source, self.raw)


class SamplingAndEvaluationTests(unittest.TestCase):
    def test_a_b_masks_include_exact_income_boundary(self):
        frame = development_rows()
        self.assertEqual(len(ab.training_subset(frame, "A_10")), 12)
        self.assertEqual(len(ab.training_subset(frame, "B_10")), 6)
        self.assertEqual(len(ab.training_subset(frame, "catboost_shallow")), 24)
        selected = ab.training_subset(frame, "B_10")
        np.testing.assert_array_equal(selected.index.to_numpy() % 4, np.zeros(6))
        self.assertEqual(len(frame), 24)

    def test_future_rows_cannot_enter_training(self):
        frame = development_rows()
        frame.loc[0, "year"] = 2025
        with self.assertRaisesRegex(ValueError, "2019~2024"):
            ab.training_subset(frame, "A_10")

    def test_cv_uses_identical_unfiltered_validation_for_every_method(self):
        calls = []

        def fake_fit(train, spec, threads):
            calls.append(train.copy())
            return PerfectFake()

        with patch.object(ab, "fit_base", side_effect=fake_fit):
            scores, ranking = ab.run_cv(development_rows(), {name: {} for name in ab.NAMES}, 1)
        self.assertEqual(len(calls), 9)
        self.assertTrue(scores["n"].eq(4).all())
        self.assertTrue(ranking.folds.eq(3).all())
        for train, year in zip(calls, [2022] * 3 + [2023] * 3 + [2024] * 3):
            self.assertLess(train.year.max(), year)

    def test_final_evaluation_never_needs_employment_or_income_filter_columns(self):
        evaluation = {
            year: prepare_data(canonical_rows([year, year]), {year}) for year in (2025, 2026)
        }
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(ab, "fit_base", return_value=PerfectFake()) as fit:
                result = ab.evaluate(development_rows(), evaluation,
                                     {name: {} for name in ab.NAMES}, 1, Path(folder))
            self.assertEqual([len(call.args[0]) for call in fit.call_args_list], [12, 6, 24])
            self.assertTrue(result["n"].eq(2).all())
            self.assertEqual(len(result), 12)
            self.assertTrue(result.delta_vs_shallow_pp.eq(0).all())
            predictions = pd.read_csv(Path(folder) / "predictions.csv")
            for year in (2025, 2026):
                expected = evaluation[year].row_id.tolist()
                for name in ab.NAMES:
                    subset = predictions.loc[predictions.name.eq(name) & predictions.year.eq(year)]
                    self.assertEqual(subset.row_id.tolist(), expected)


if __name__ == "__main__":
    unittest.main()

"""Synthetic-data checks for target definitions and chronological validation."""

import unittest

import numpy as np
import pandas as pd

from scripts.budget_allocation.compare_allocation_models import (
    AMOUNTS, CATEGORIES, FEATURES, fit_base, normalize_ratios, prepare_data,
    target_ratios, temporal_folds, training_weights,
)


def canonical_rows(years):
    rows = []
    for i, year in enumerate(years):
        row = {
            "원본파일": f"{year}_household_survey.csv", "원본행번호": i + 2,
            "연도": year, "가중값": i + 1.0, "나이": 25 + i % 5,
            "성별": "남" if i % 2 == 0 else "여", "근로자가구": "Y",
            "자동차보유": "N" if i % 2 == 0 else "Y",
            "소득": 2000.0 + i * 100, "비소비지출": 500.0,
            "고정비": 100.0, "기타": 300.0, "자동차구입(별도)": 200.0,
            **{category: 100.0 for category in CATEGORIES},
        }
        row["식비"] += i * 10
        row["주거·통신"] = 150.0
        row["생활비_주거·통신"] = 50.0
        row["처분가능소득"] = row["소득"] - row["비소비지출"]
        row["로그처분가능소득"] = np.log(row["처분가능소득"])
        row["생활비"] = sum(row[c] for c in CATEGORIES) - row["고정비"]
        for category in CATEGORIES:
            amount = row["생활비_주거·통신"] if category == "주거·통신" else row[category]
            row["비율_" + category] = amount / row["생활비"]
        rows.append(row)
    return pd.DataFrame(rows)


class TargetContractTests(unittest.TestCase):
    def test_fixed_cost_is_removed_once_and_other_is_not_a_target(self):
        source = canonical_rows([2022, 2023])
        actual = prepare_data(source, {2022, 2023})
        changed = source.copy()
        changed["기타"] = 9_999_999.0
        changed["자동차구입(별도)"] = 8_888_888.0
        repeated = prepare_data(changed, {2022, 2023})
        np.testing.assert_array_equal(actual[list(AMOUNTS)], repeated[list(AMOUNTS)])
        np.testing.assert_array_equal(actual["amount_주거·통신"], [50.0, 50.0])
        np.testing.assert_array_equal(actual.living_budget_amount, [950.0, 960.0])
        np.testing.assert_allclose(target_ratios(actual).sum(axis=1), 1)

    def test_legacy_other_inclusive_denominator_is_rejected(self):
        source = canonical_rows([2022])
        source["생활비"] += source["기타"]
        with self.assertRaises(AssertionError):
            prepare_data(source, {2022})

    def test_duplicate_observations_are_rejected(self):
        source = canonical_rows([2022])
        with self.assertRaisesRegex(ValueError, "중복"):
            prepare_data(pd.concat([source, source]), {2022})

    def test_rounding_is_allowed_but_substantial_negative_housing_is_rejected(self):
        source = canonical_rows([2022])
        source["주거·통신"] = 97.0
        source["생활비_주거·통신"] = 0.0
        source["생활비"] = 900.0
        for category in CATEGORIES:
            source["비율_" + category] = 0.0 if category == "주거·통신" else 100 / 900
        result = prepare_data(source, {2022})
        self.assertEqual(result["amount_주거·통신"].iloc[0], 0)
        source["주거·통신"] = 94.0
        with self.assertRaisesRegex(ValueError, "-5원"):
            prepare_data(source, {2022})


class TemporalAndPredictionTests(unittest.TestCase):
    def setUp(self):
        self.data = prepare_data(canonical_rows(range(2019, 2025)), range(2019, 2025))

    def test_every_validation_year_is_strictly_after_training(self):
        folds = list(temporal_folds(self.data))
        self.assertEqual([year for year, _, _ in folds], [2022, 2023, 2024])
        for year, train, validation in folds:
            self.assertLess(train.year.max(), year)
            self.assertTrue(validation.year.eq(year).all())
            self.assertTrue(set(train.row_id).isdisjoint(validation.row_id))
        future = prepare_data(canonical_rows([2025]), {2025})
        with self.assertRaisesRegex(ValueError, "2025/2026"):
            list(temporal_folds(pd.concat([self.data, future])))

    def test_ridge_scaler_and_mean_use_only_training_rows(self):
        _, train, validation = list(temporal_folds(self.data))[-1]
        weights = training_weights(train)
        model = fit_base(train, {"kind": "ridge", "params": {"alpha": 10.0}}, threads=1)
        expected_mean = np.average(train[list(FEATURES)], axis=0, weights=weights)
        np.testing.assert_allclose(model.scaler.mean_, expected_mean)
        expected_ratios = np.average(target_ratios(train), axis=0, weights=weights)
        np.testing.assert_allclose(model.mean, expected_ratios)
        extreme = validation.copy()
        extreme["living_budget_amount"] = 100_000_000.0
        prediction, _ = model.predict(extreme)
        np.testing.assert_allclose(model.scaler.mean_, expected_mean)
        np.testing.assert_allclose(model.mean, expected_ratios)
        self.assertTrue(np.isfinite(prediction).all())
        self.assertTrue((prediction >= 0).all())
        np.testing.assert_allclose(prediction.sum(axis=1), 1)

    def test_all_negative_prediction_falls_back_and_nan_does_not_pass_silently(self):
        fallback = np.arange(1, 11, dtype=float)
        prediction, diagnostics = normalize_ratios(-np.ones((2, 10)), fallback)
        np.testing.assert_allclose(prediction, np.tile(fallback / fallback.sum(), (2, 1)))
        self.assertEqual(diagnostics["fallback_rows"], 2)
        with self.assertRaisesRegex(ValueError, "비유한"):
            normalize_ratios(np.full((1, 10), np.nan), fallback)


if __name__ == "__main__":
    unittest.main()

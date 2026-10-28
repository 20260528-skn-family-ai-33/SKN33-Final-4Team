"""Budget inference contract tests without private data or model artifacts."""

import unittest
from unittest.mock import Mock

import numpy as np

from scripts.budget_allocation.predict_allocation import CATEGORIES, FEATURES, predict_profile, profile_features


class PredictionContractTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "age": 27, "sex": 0, "disposable_income": 3_000_000,
            "has_vehicle": 1, "fixed_expense_amount": 700_000,
            "living_budget_amount": 1_200_000,
        }
        self.model = Mock()
        self.model.feature_names_ = list(FEATURES)
        self.bundle = {
            "format_version": 1, "model": self.model,
            "fallback_ratios": [0.1] * 10,
            "metadata": {"model_name": "catboost_shallow", "artifact_id": "test",
                         "features": list(FEATURES), "categories": list(CATEGORIES)},
        }

    def test_feature_order_encoding_and_income_log(self):
        x = profile_features(self.profile)
        self.assertEqual(list(x.columns), list(FEATURES))
        self.assertEqual(x.iloc[0]["sex"], "0")
        self.assertEqual(x.iloc[0]["has_vehicle"], "1")
        self.assertAlmostEqual(x.iloc[0]["log_disposable_income"], np.log(3_000_000))
        self.assertNotIn("log_disposable_income", self.profile)

    def test_invalid_inputs_are_rejected_before_model_call(self):
        for field, value in (("age", 27.5), ("sex", 2), ("has_vehicle", 2),
                             ("disposable_income", 0), ("living_budget_amount", -1),
                             ("fixed_expense_amount", float("nan"))):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    predict_profile(self.bundle, {**self.profile, field: value})
        self.model.predict.assert_not_called()

    def test_negative_ratios_are_clipped_and_total_is_preserved(self):
        self.model.predict.return_value = np.array([[-1, 1, 2, 3, 4, 5, 6, 7, 8, 9]], dtype=float)
        result = predict_profile(self.bundle, self.profile)
        self.assertEqual(list(result["ratios"]), list(CATEGORIES))
        self.assertEqual(result["ratios"]["식비"], 0)
        self.assertAlmostEqual(sum(result["ratios"].values()), 1)
        self.assertAlmostEqual(sum(result["draft_amounts"].values()), 1_200_000)
        self.assertTrue(result["negative_clipped"])
        self.assertFalse(result["used_fallback"])

    def test_zero_sum_uses_mean_but_nonfinite_predictions_raise(self):
        self.model.predict.return_value = -np.ones((1, 10))
        result = predict_profile(self.bundle, self.profile)
        self.assertTrue(result["used_fallback"])
        np.testing.assert_allclose(list(result["ratios"].values()), [0.1] * 10)
        self.model.predict.return_value = np.full((1, 10), np.nan)
        with self.assertRaisesRegex(ValueError, "예측 형식 또는 값 오류"):
            predict_profile(self.bundle, self.profile)

    def test_category_order_mismatch_is_rejected(self):
        self.bundle["metadata"]["categories"] = list(reversed(CATEGORIES))
        with self.assertRaisesRegex(ValueError, "카테고리 순서"):
            predict_profile(self.bundle, self.profile)


if __name__ == "__main__":
    unittest.main()

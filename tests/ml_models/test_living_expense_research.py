"""Historical eleven-output experiments; no real observations or trained weights."""
import tempfile
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from scripts.living_expense_prediction.expense_predictor.constants import AMOUNTS, PROFILE, BUDGET
from scripts.living_expense_prediction.expense_predictor.data import add_flags, training_mask
from scripts.living_expense_prediction.expense_predictor.evaluation import evaluate
from scripts.living_expense_prediction.expense_predictor.models import ModelBundle, train_model


class FixedEstimator:
    def predict(self, frame):
        return np.tile(np.arange(1, 12), (len(frame), 1))


class LivingExpenseResearchTests(unittest.TestCase):
    def profile(self):
        return dict(age=25, sex=1, net_income=3000000, vehicle_count=0,
                    fixed_expense_amount=500000, living_budget_amount=1000000)

    def test_ratio_model_conserves_total_and_rejects_invalid_input(self):
        metadata = {'features': list(PROFILE + (BUDGET,)), 'kind': 'ratio',
                    'fallback': [1 / 11] * 11}
        model = ModelBundle(FixedEstimator(), metadata)
        result = model.predict(self.profile())
        np.testing.assert_allclose(result['amounts'].sum(axis=1), [1000000])
        np.testing.assert_allclose(result['ratios'].sum(axis=1), [1])
        for field, value in [('age', 19), ('sex', 9), ('net_income', 0), (BUDGET, -1)]:
            with self.assertRaises(ValueError):
                model.predict({**self.profile(), field: value})

    def test_training_filters_and_synthetic_perfect_prediction(self):
        data = pd.DataFrame([{**self.profile(), 'year': year, 'employment_status_code': status,
                              'target_eligible': True, 'survey_weight': 1,
                              **dict.fromkeys(AMOUNTS, 1000000 / 11)}
                             for year, status in [(2024, 1), (2024, 2), (2025, 1)]])
        data = add_flags(data)
        self.assertEqual(training_mask(data, 'A').tolist(), [True, False, False])
        self.assertEqual(data.evaluation.tolist(), [False, False, True])
        amounts = data[list(AMOUNTS)].to_numpy()
        result = evaluate(data, {'amounts': amounts, 'ratios': amounts / 1000000,
                                'total': amounts.sum(axis=1), 'fallback': np.zeros(3, bool),
                                'negative_mask': np.zeros((3, 11), bool)})
        self.assertEqual(result['weighted']['total_mae_won'], 0)

    def test_tiny_training_save_reload_and_no_evaluation_rows_in_fit(self):
        data = pd.DataFrame([{**self.profile(), 'row_id': f'synthetic-{i}', 'year': 2024 if i < 5 else 2025,
                              'employment_status_code': 1, 'target_eligible': True,
                              'survey_weight': i + 1, 'age': 22 + i,
                              **dict.fromkeys(AMOUNTS, 1000000 / 11)} for i in range(6)])
        data = add_flags(data)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'config').mkdir()
            (root / 'config/model_params.json').write_text(
                '{"iterations": 2, "depth": 2, "loss_function": "MultiRMSE", '
                '"random_seed": 42, "thread_count": 1, "verbose": false, '
                '"allow_const_label": true, "allow_writing_files": false}', encoding='utf-8')
            model = train_model(data, 'A', root)
            self.assertEqual(model.metadata['train_ids'], [f'synthetic-{i}' for i in range(5)])
            expected = model.predict(self.profile())['amounts']
            model.save(root / 'model')
            np.testing.assert_allclose(ModelBundle.load(root / 'model').predict(self.profile())['amounts'], expected)


if __name__ == '__main__':
    unittest.main()

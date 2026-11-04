"""총액 오차와 항목 배분 오차를 따로 계산한다."""
import numpy as np
import pandas as pd
from .constants import CODES, AMOUNTS


def evaluate(data, pred):
    actual = data[list(AMOUNTS)].to_numpy(float)
    total = actual.sum(axis=1)
    assert len(data) and (total > 0).all()
    error = pred['amounts'] - actual
    total_error = pred['amounts'].sum(axis=1) - total
    ratio_error = (pred['ratios'] - actual / total[:, None]) * 100
    def metric(w):
        avg = lambda x: float(np.average(x, weights=w))
        cat_mae = np.average(abs(error), axis=0, weights=w)
        return {
            'actual_total_mean_won': avg(total), 'predicted_total_mean_won': avg(pred['total']),
            'total_mae_won': avg(abs(total_error)), 'total_bias_won': avg(total_error),
            'total_rmse_won': np.sqrt(avg(total_error ** 2)),
            'total_mape_percent': avg(abs(total_error) / total * 100),
            'total_wape_percent': avg(abs(total_error)) / avg(total) * 100,
            'category_macro_mae_won': float(cat_mae.mean()),
            'category_mae_won': dict(zip(CODES, map(float, cat_mae))),
            'category_rmse_won': dict(zip(CODES, map(float, np.sqrt(np.average(error**2, axis=0, weights=w))))),
            'ratio_mae_pp': float(np.average(abs(ratio_error), axis=0, weights=w).mean()),
            'category_ratio_mae_pp': dict(zip(CODES, map(float, np.average(abs(ratio_error), axis=0, weights=w)))),
            'category_abs_error_sum_mean_won': avg(abs(error).sum(axis=1)),
            'category_abs_error_rate_percent': avg(abs(error).sum(axis=1) / total * 100)}
    weights = data.survey_weight.to_numpy(float)
    assert np.isfinite(weights).all() and (weights > 0).all()
    synthetic = bool(data.get('is_synthetic', pd.Series(False, index=data.index)).any())
    return {'n': len(data), 'comparison_basis': 'synthetic_reference_not_observed_truth' if synthetic else 'observed_preprocessed_targets',
            'weighted': metric(weights), 'unweighted': metric(np.ones(len(data))),
            'fallback_rows': int(pred['fallback'].sum()),
            'negative_clipped_rows': int(pred['negative_mask'].any(axis=1).sum())}


def prediction_table(data, pred):
    cols = [c for c in ['row_id', 'case_id', 'year', 'age', 'sex', 'net_income', 'vehicle_count',
                       'fixed_expense_amount', 'living_budget_amount', 'is_synthetic', 'test_group'] if c in data]
    out = data[cols].copy().reset_index(drop=True)
    truth = data[list(AMOUNTS)].to_numpy(float)
    out['reference_total'] = truth.sum(axis=1)
    out['predicted_total'] = pred['amounts'].sum(axis=1)
    out['total_absolute_error'] = abs(out.predicted_total - out.reference_total)
    out['total_error_percent'] = out.total_absolute_error / out.reference_total * 100
    for i, code in enumerate(CODES):
        out['reference_' + code] = truth[:, i]
        out['predicted_' + code] = pred['amounts'][:, i]
        out['absolute_error_' + code] = abs(pred['amounts'][:, i] - truth[:, i])
    out['category_absolute_error_sum'] = abs(pred['amounts'] - truth).sum(axis=1)
    out['category_absolute_error_rate_percent'] = out.category_absolute_error_sum / out.reference_total * 100
    return out

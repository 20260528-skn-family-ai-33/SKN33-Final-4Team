"""공식 지표와 학습구간의 공동 분포를 이용해 비교용 가상 기준값을 생성한다.

생성 기준값은 실제 관측 정답이 아니다. 어떤 모델의 예측도 생성에 사용하지 않는다.
"""
from pathlib import Path
import numpy as np
import pandas as pd
from .constants import AMOUNTS, RATIOS, PROFILE
from .io import read_json


def generate_test_data(root, data):
    cfg = read_json(Path(root) / 'config/synthetic_sources.json')
    settings = cfg['design_assumptions']
    rng = np.random.default_rng(settings['seed'])
    ref = data.loc[data.train_B & data.year.eq(cfg['reference_microdata_year']) & data.vehicle_count.notna()].copy()
    p = ref.survey_weight.to_numpy(float)
    positions = rng.choice(len(ref), size=settings['rows'], replace=False, p=p / p.sum())
    selected = ref.iloc[positions]
    rows, donor_ids = [], []
    rejected = 0
    for i, (_, donor) in enumerate(selected.iterrows(), 1):
        for attempt in range(10000):
            income = round(donor.net_income * cfg['income_factor'] * np.exp(rng.normal(0, settings['income_log_noise_sd'])) / 1000) * 1000
            fixed = round(donor.fixed_expense_amount * cfg['expense_factor'] * np.exp(rng.normal(0, settings['fixed_log_noise_sd'])) / 1000) * 1000
            common = np.exp(rng.normal(0, settings['common_spending_log_noise_sd']))
            changes = np.exp(rng.normal(0, settings['category_spending_log_noise_sd'], 11))
            amounts = np.round(donor[list(AMOUNTS)].to_numpy(float) * cfg['expense_factor'] * common * changes / 1000) * 1000
            total = amounts.sum()
            if income > 0 and total > 0 and fixed + total <= income:
                break
            rejected += 1
        else:
            raise ValueError('500행 생성 조건에 맞는 시나리오를 만들지 못했다.')
        record = {'case_id': f'SYN-{i:04d}', 'is_synthetic': True,
                  'label_kind': 'generated_reference_not_observed', 'scenario_year': cfg['scenario_year'],
                  'test_group': 'high_fixed' if fixed / income >= .35 else 'standard',
                  'age': int(donor.age), 'sex': int(donor.sex), 'net_income': income,
                  'vehicle_count': int(donor.vehicle_count), 'fixed_expense_amount': fixed,
                  'living_budget_amount': total, 'survey_weight': 1.0}
        record.update(dict(zip(AMOUNTS, map(float, amounts))))
        record.update(dict(zip(RATIOS, map(float, amounts / total))))
        rows.append(record)
        donor_ids.append({'case_id': record['case_id'], 'reference_training_row_id': donor.row_id})
    result = pd.DataFrame(rows)
    features = list(PROFILE) + ['living_budget_amount']
    assert not result.duplicated(features).any()
    assert not result.merge(data[features].drop_duplicates(), on=features, how='inner').shape[0]
    assert not result.merge(data[list(PROFILE)].drop_duplicates(), on=list(PROFILE), how='inner').shape[0]
    np.testing.assert_allclose(result[list(AMOUNTS)].sum(axis=1), result.living_budget_amount, rtol=0, atol=0)
    assert (result.fixed_expense_amount + result.living_budget_amount <= result.net_income).all()
    assert not set(selected.row_id).intersection(data.loc[data.evaluation, 'row_id'])
    audit = {'rows': len(result), 'reference_rows': len(ref), 'reference_year': 2023,
             'reference_training_overlap': len(selected), 'reference_evaluation_overlap': 0,
             'exact_full_profile_overlap': 0, 'exact_five_input_overlap': 0,
             'generation_rejections': rejected, 'random_seed': settings['seed'],
             'official_indicators_used': ['wage', 'cpi2024', 'cpi2025'],
             'model_predictions_used': False, 'labels_are_observed': False,
             'independent_external_accuracy_test': False,
             'unique_reference_rows': int(selected.row_id.nunique()),
             'income_mean_won': float(result.net_income.mean()),
             'fixed_mean_won': float(result.fixed_expense_amount.mean()),
             'reference_living_mean_won': float(result.living_budget_amount.mean()),
             'by_sex': {str(int(k)): int(v) for k, v in result.groupby('sex').size().items()},
             'by_vehicle_count': {str(int(k)): int(v) for k, v in result.groupby('vehicle_count').size().items()}}
    return result, audit, pd.DataFrame(donor_ids)

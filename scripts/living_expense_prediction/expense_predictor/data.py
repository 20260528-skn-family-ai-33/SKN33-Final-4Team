"""원자료의 항목을 합산하고 기존 v13의 고정된 큰 지출 기준을 재현한다."""
from pathlib import Path
import gzip
import hashlib
import numpy as np
import pandas as pd
from .constants import CATEGORIES, CODES, AMOUNTS, RATIOS
from .io import read_json, sha256

META = ['조사연월', '가구일련번호', '가구원수', '가구주_연령', '가구주_성별코드',
        '가구주_취업여부', '가구주_종사상지위코드', '자동차보유대수', '입주형태코드',
        '가중값', '가계지출_소비지출금액']


def resolved_mapping(root):
    root = Path(root)
    mapping = read_json(root / 'config/category_mapping.json')
    fixed = read_json(root / 'config/fixed_exclusion_policy.json')
    issues = read_json(root / 'config/review_issues.json')
    base = fixed['base_fixed_source_columns']
    additional = fixed['additional_fixed_source_columns']
    cars = fixed['car_purchase_source_columns']
    entries = []
    for item in mapping['entries']:
        c = item['source_column']
        treatment = ('base_fixed' if c in base else 'additional_fixed' if c in additional
                     else 'car_purchase' if c in cars else 'living')
        entries.append({**item, 'treatment': treatment,
                        'recurrence_status': 'not_observed',
                        'issue_codes': ';'.join(issues['sources'][c]['issue_codes']),
                        'review_reason': issues['sources'][c]['reason']})
    assert len(entries) == len({e['source_column'] for e in entries}) == 100
    assert len(base + additional) == 7 and len(cars) == 1
    return entries


def read_raw(root):
    root = Path(root)
    config = read_json(root / 'config/preprocessing.json')
    manifest = read_json(root / 'config/raw_manifest.json')
    mapping = read_json(root / 'config/category_mapping.json')
    columns = [e['source_column'] for e in resolved_mapping(root)]
    needed = set(META + columns + [config['income_source']] + config['income_deductions'])
    def alias(c):
        for old, new in config['column_aliases'].items():
            c = c.replace(old, new)
        return c
    pieces, inventory = [], []
    for item in manifest['files']:
        path = root / item['path']
        if sha256(path) != item['compressed_sha256']:
            raise ValueError('압축 원자료 해시 불일치: ' + path.name)
        with gzip.open(path, 'rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != item['source_sha256']:
            raise ValueError('압축 해제 원자료 해시 불일치')
        header = [alias(c) for c in pd.read_csv(path, encoding='cp949', nrows=0).columns]
        assert len(header) == len(set(header)) and needed <= set(header)
        unknown = {c for c in header if c.startswith('가계지출_소비지출_')} - set(columns) - set(mapping['excluded_aggregate_columns'])
        if unknown:
            raise ValueError('미매핑 원천: ' + str(unknown))
        raw = pd.read_csv(path, encoding='cp949', dtype='string', keep_default_na=False,
                          usecols=lambda c: alias(c) in needed).rename(columns=alias)
        numeric = raw.drop(columns='가구일련번호').apply(
            lambda s: pd.to_numeric(s.str.strip().replace('', pd.NA), errors='raise')).astype(float).copy()
        numeric['household_id_text'] = raw['가구일련번호'].str.strip()
        period = numeric['조사연월'].astype(int).astype(str)
        assert period.str.fullmatch(r'\d{4}[1-4]4').all()
        numeric['year'] = period.str[:4].astype(int)
        numeric['quarter'] = period.str[4].astype(int)
        numeric['row_id'] = period + '_' + numeric.household_id_text
        assert numeric.year.eq(item['year']).all()
        selected = target_population(numeric)
        inventory.append({'year': item['year'], 'raw_rows': len(raw),
                          'selected_rows': int(selected.sum()),
                          'selected_missing_vehicle': int(numeric.loc[selected, '자동차보유대수'].isna().sum())})
        pieces.append(numeric)
    result = pd.concat(pieces, ignore_index=True)
    assert result.row_id.is_unique
    return result, pd.DataFrame(inventory)


def target_population(raw):
    return (raw['가구원수'].eq(1) & raw['가구주_연령'].between(20, 34)
            & raw['가구주_취업여부'].eq(1) & raw['가구주_종사상지위코드'].isin([1, 2, 3]))


def preprocess(raw, root):
    root = Path(root)
    source = raw.loc[target_population(raw)].reset_index(drop=True).copy()
    config = read_json(root / 'config/preprocessing.json')
    policy = read_json(root / 'config/original_target_policy.json')
    entries = resolved_mapping(root)
    columns = [e['source_column'] for e in entries]
    values = source[columns].to_numpy(float)
    assert np.isfinite(values).all() and (values >= 0).all()
    income_cols = [config['income_source']] + config['income_deductions']
    assert np.isfinite(source[income_cols].to_numpy(float)).all()
    frame = source[['row_id', 'year', 'quarter']].copy()
    for dest, origin in [('age', '가구주_연령'), ('sex', '가구주_성별코드'),
                         ('vehicle_count', '자동차보유대수'), ('tenure', '입주형태코드'),
                         ('survey_weight', '가중값'), ('employment_status_code', '가구주_종사상지위코드')]:
        frame[dest] = source[origin]
    frame['net_income'] = source[config['income_source']] - source[config['income_deductions']].sum(axis=1)
    fixed = [e['source_column'] for e in entries if e['treatment'] in ('base_fixed', 'additional_fixed')]
    cars = [e['source_column'] for e in entries if e['treatment'] == 'car_purchase']
    frame['fixed_expense_amount'] = source[fixed].sum(axis=1)
    frame['car_purchase_amount'] = source[cars].sum(axis=1)
    removed = {}
    for item in policy['items']:
        value = source[item['source_column']].to_numpy(float)
        threshold = item['threshold']
        removed[item['source_column']] = np.where(value > threshold, value, 0) if threshold is not None else np.zeros(len(source))
    for category, name in zip(CATEGORIES, AMOUNTS):
        cols = [e['source_column'] for e in entries if e['target_category'] == category and e['treatment'] == 'living']
        subtraction = sum((removed[c] for c in cols if c in removed), np.zeros(len(source)))
        frame[name] = source[cols].sum(axis=1) - subtraction
    frame['nonroutine_amount'] = sum(removed.values(), np.zeros(len(frame)))
    frame['living_budget_amount'] = frame[list(AMOUNTS)].sum(axis=1)
    frame[list(RATIOS)] = frame[list(AMOUNTS)].to_numpy(float) / frame.living_budget_amount.replace(0, np.nan).to_numpy()[:, None]
    vehicle = frame.vehicle_count
    invalid = {
        'invalid_age': ~np.isfinite(frame.age) | ~frame.age.between(20, 34) | frame.age.mod(1).ne(0),
        'invalid_sex': ~frame.sex.isin([1, 2]),
        'invalid_income': ~np.isfinite(frame.net_income) | frame.net_income.le(0),
        'invalid_weight': ~np.isfinite(frame.survey_weight) | frame.survey_weight.le(0),
        'invalid_vehicle_count': vehicle.notna() & (~np.isfinite(vehicle) | vehicle.lt(0) | vehicle.mod(1).ne(0)),
        'invalid_fixed_expense': ~np.isfinite(frame.fixed_expense_amount) | frame.fixed_expense_amount.lt(0),
    }
    frame['eligible'] = ~pd.DataFrame(invalid).any(axis=1)
    frame['target_eligible'] = frame.eligible & frame.living_budget_amount.gt(0)
    frame['exclusion_reason'] = [';'.join(k for k, mask in invalid.items() if mask.iloc[i]) for i in range(len(frame))]
    frame = add_flags(frame)
    assert np.isfinite(frame[list(AMOUNTS)].to_numpy()).all() and frame[list(AMOUNTS)].ge(0).all().all()
    total_source = source[columns].sum(axis=1)
    conserved = frame.fixed_expense_amount + frame.car_purchase_amount + frame.nonroutine_amount + frame.living_budget_amount
    np.testing.assert_allclose(total_source, conserved, atol=1e-8, rtol=0)
    rounding = total_source - source['가계지출_비소비지출_가구간이전지출금액'] - source['가계지출_비소비지출_이자비용'] - source['가계지출_소비지출금액']
    assert rounding.abs().max() <= 10
    eval_ids = set(frame.loc[frame.evaluation, 'row_id'])
    assert not eval_ids.intersection(policy['fit_ids'])
    audit = {'rows': len(frame), 'eligible_rows': int(frame.target_eligible.sum()),
             'train_counts': {k: int(training_mask(frame, k).sum()) for k in 'ABC'},
             'evaluation_rows': int(frame.evaluation.sum()), 'source_items': len(entries),
             'fixed_items': len(fixed), 'car_purchase_items': len(cars),
             'living_source_items_before_P95': sum(e['treatment'] == 'living' for e in entries),
             'max_conservation_error_won': float(abs(total_source - conserved).max()),
             'max_consumption_rounding_error_won': float(rounding.abs().max()),
             'vehicle_missing_rows': int(frame.vehicle_count.isna().sum()),
             'policy_fit_rows': len(policy['fit_ids']), 'policy_fit_evaluation_overlap': 0,
             'regularity_is_observed': False, 'variant': 'original'}
    frame.attrs['variant'] = 'original'
    return frame, audit


def add_flags(data):
    data = data.copy()
    train = data.target_eligible & data.year.le(2024)
    data['train_A'] = train & data.employment_status_code.eq(1)
    data['train_B'] = data.train_A & ((data.fixed_expense_amount + data.living_budget_amount) <= data.net_income)
    data['train_C'] = data.train_B
    data['evaluation'] = data.target_eligible & data.year.isin([2025, 2026])
    return data


def load_processed(root):
    data = pd.read_csv(Path(root) / 'data/processed/household_budget.csv', float_precision='round_trip')
    data.attrs['variant'] = 'original'
    return data


def training_mask(data, model_key):
    if model_key not in 'ABC' or len(model_key) != 1:
        raise ValueError('모델은 A, B, C 중 하나다.')
    return data['train_' + model_key].astype(bool)

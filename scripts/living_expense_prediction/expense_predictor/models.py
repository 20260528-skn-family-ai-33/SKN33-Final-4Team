"""CatBoost native 파일과 JSON만으로 모델을 저장하고 재학습한다."""
from pathlib import Path
import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from .constants import AMOUNTS, PROFILE, BUDGET
from .io import read_json, write_json


class ModelBundle:
    def __init__(self, estimator, metadata):
        self.estimator = estimator
        self.metadata = metadata
        self.features = tuple(metadata['features'])

    def x(self, profile):
        frame = pd.DataFrame([profile]) if isinstance(profile, dict) else pd.DataFrame(profile)
        missing = set(self.features) - set(frame)
        if missing:
            raise ValueError('필수 입력 누락: ' + ', '.join(sorted(missing)))
        x = frame.loc[:, list(self.features)].apply(pd.to_numeric, errors='raise').astype(float)
        if not (np.isfinite(x.age).all() and x.age.between(20, 34).all() and x.age.mod(1).eq(0).all()):
            raise ValueError('age: 20~34세 정수')
        if not x.sex.isin([1, 2]).all():
            raise ValueError('sex: 1=남, 2=여')
        if not (np.isfinite(x.net_income).all() and x.net_income.gt(0).all()):
            raise ValueError('net_income: 원 단위 양수')
        for c in ['fixed_expense_amount'] + ([BUDGET] if BUDGET in self.features else []):
            if not (np.isfinite(x[c]).all() and x[c].ge(0).all()):
                raise ValueError(c + ': 원 단위 0 이상 유한한 금액')
        vehicle = x.vehicle_count.dropna()
        if not (np.isfinite(vehicle).all() and vehicle.ge(0).all() and vehicle.mod(1).eq(0).all()):
            raise ValueError('vehicle_count: 0 이상 정수 또는 결측')
        x['sex'] = x.sex.astype(int).astype(str)
        return x

    def predict(self, frame):
        x = self.x(frame)
        raw = np.asarray(self.estimator.predict(x), float).reshape(-1, 11)
        negative = raw < 0
        fallback = np.zeros(len(x), dtype=bool)
        if self.metadata['kind'] == 'ratio':
            ratios = np.maximum(np.where(np.isfinite(raw), raw, 0), 0)
            fallback = ~np.isfinite(raw).all(axis=1) | (ratios.sum(axis=1) <= 0)
            ratios[fallback] = np.asarray(self.metadata['fallback'])
            ratios /= ratios.sum(axis=1, keepdims=True)
            total = x[BUDGET].to_numpy(float)
            amounts = total[:, None] * ratios
        else:
            if not np.isfinite(raw).all():
                raise ValueError('모델이 유한하지 않은 금액을 반환했다.')
            amounts = np.maximum(raw * self.metadata['amount_scale'], 0)
            total = amounts.sum(axis=1)
            ratios = np.divide(amounts, total[:, None], out=np.zeros_like(amounts),
                               where=total[:, None] > 0)
        return {'amounts': amounts, 'ratios': ratios, 'total': total,
                'negative_mask': negative, 'fallback': fallback}

    def save(self, folder):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        self.estimator.save_model(str(folder / 'model.cbm'))
        write_json(folder / 'metadata.json', self.metadata)

    @classmethod
    def load(cls, folder):
        folder = Path(folder)
        estimator = CatBoostRegressor()
        estimator.load_model(str(folder / 'model.cbm'))
        return cls(estimator, read_json(folder / 'metadata.json'))


def load_model(root, model_key, variant='original'):
    return ModelBundle.load(Path(root) / 'models' / variant / model_key)


def train_model(data, model_key, root, variant=None):
    from .data import training_mask
    train = data.loc[training_mask(data, model_key)].copy()
    if train.empty:
        raise ValueError('학습 대상이 없다.')
    variant = variant or data.attrs.get('variant', 'original')
    params = read_json(Path(root) / 'config/model_params.json')
    weights = train.survey_weight.to_numpy(float) * .5 ** ((train.year.max() - train.year.to_numpy()) / 2)
    weights /= weights.mean()
    y = train[list(AMOUNTS)].to_numpy(float)
    if not (np.isfinite(y).all() and (y >= 0).all() and (y.sum(axis=1) > 0).all()):
        raise ValueError('비음수 항목과 양수 생활비 합계가 필요하다.')
    ratio_model = model_key in ('A', 'B')
    if ratio_model:
        np.testing.assert_allclose(y.sum(axis=1), train[BUDGET], rtol=0, atol=1e-7)
        y = y / y.sum(axis=1, keepdims=True)
    else:
        y = y / 10000.0
    mean = np.average(y, axis=0, weights=weights)
    metadata = {'model_key': model_key, 'version': 'v13_' + variant + '_' + model_key,
                'kind': 'ratio' if ratio_model else 'amount',
                'features': list(PROFILE + ((BUDGET,) if ratio_model else ())),
                'params': params, 'train_ids': train.row_id.tolist(), 'train_n': len(train),
                'dataset_variant': variant, 'fallback': (mean / mean.sum()).tolist() if ratio_model else None,
                'amount_scale': 1 if ratio_model else 10000}
    bundle = ModelBundle(CatBoostRegressor(**params), metadata)
    bundle.estimator.fit(Pool(bundle.x(train), y, weight=weights, cat_features=['sex']))
    return bundle

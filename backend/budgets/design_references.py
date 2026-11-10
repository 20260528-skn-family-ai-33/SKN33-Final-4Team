"""Frozen model/history summaries for later adjustment; no user transaction details."""
from fractions import Fraction
from hashlib import sha256
import json
from math import ceil

from .design_candidates import CATEGORY_NAMES, ml_profile
from .scope import OUT_OF_BUDGET

HISTORY_VERSION = 'one-off-category-mad10-dominance3-v1'


def input_fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                             allow_nan=False).encode()).hexdigest()


def model_reference(inp, prediction):
    profile = ml_profile(inp)
    checksum = prediction.get('model_checksum')
    source = prediction.get('source')
    if (source != 'model' or prediction.get('used_fallback') is not False
            or not isinstance(prediction.get('artifact_id'), str) or not prediction['artifact_id']
            or not isinstance(checksum, str) or len(checksum) != 64
            or any(c not in '0123456789abcdef' for c in checksum)):
        return {'ml_reference_status': 'unavailable'}
    ratios = {c: Fraction(str(prediction['ratios'][name])) for c, name in CATEGORY_NAMES.items()}
    total = sum(ratios.values())
    if total <= 0 or any(v < 0 for v in ratios.values()):
        raise ValueError('invalid_ml_reference_ratios')
    values = {c: ceil(inp['target'] * r / total) for c, r in ratios.items() if c not in OUT_OF_BUDGET}
    return {'ml_reference': values, 'ml_reference_status': 'ready', 'ml_reference_meta': {
        'source': source, 'model_id': prediction['artifact_id'], 'model_checksum': checksum,
        'target': inp['target'], 'input_hash': input_fingerprint(profile),
        'raw_ratios': {c: str(r) for c, r in ratios.items()},
        'normalization': {'scope': 'all_10_categories', 'denominator': str(total),
                          'active_categories_renormalized': False}, 'amount_rounding': 'ceil_1_krw'}}


def transport_reference(history, history_fingerprint):
    out = {'status': 'unavailable', 'months': history.get('months', []),
           'history_fingerprint': history_fingerprint, 'version': HISTORY_VERSION, 'baseline': history.get('baseline')}
    if history.get('status') == 'ready':
        amount = Fraction(history['categories']['TRANSPORT_CAR']['baseline_amount'])
        out.update(status='ready', baseline_numerator=amount.numerator, baseline_denominator=amount.denominator)
    return out

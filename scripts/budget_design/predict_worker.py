"""Local simulator subprocess: synthetic profile JSON in, saved ML result out."""
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    from scripts.budget_allocation.predict_allocation import load_bundle, predict_profile

    default_model = ROOT / 'local_workspace/models/living_expense_prediction/models/shallow/catboost_allocation/budget_allocation.joblib'
    model = Path(os.environ.get('BUDGET_MODEL_PATH', str(default_model)))
    checksum = hashlib.sha256(model.read_bytes()).hexdigest()
    if checksum != '2fb8d5e15a5175a1d21830bacc51a44bf4b0bf5e90cceee6c2f7bfff6e7c74ce':
        raise ValueError('model_checksum_mismatch')
    # Local one-shot tool; service integration must load once per worker instead.
    result = predict_profile(load_bundle(model), json.load(sys.stdin))
    result.update(source='model', model_checksum=checksum)
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Do not echo input, paths from exceptions, or library tracebacks.
        print(json.dumps({'error_type': type(error).__name__}))
        sys.exit(1)

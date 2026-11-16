"""Read-only model C inference. Profile stdin, aggregates stdout; no external calls."""
import json
from hashlib import sha256
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    from scripts.living_expense_prediction.expense_predictor.models import ModelBundle
    from scripts.living_expense_prediction.expense_predictor.constants import CODES, PROFILE

    root = Path(sys.argv[1])
    checks = {'models/original/C/model.cbm': '5e6b82305e759d3564a10a4c0082fe97bf62616c78ef4f31ba4e841cdf9ae42f',
              'models/original/C/metadata.json': '5b9bd52f337855c600128bc6b6ce2289b0093a840d447e41188b7771f8aae2a6',
              'reports/tables/observed_metrics.json': 'a627571f030d56d0bb18a65c0300c3e05403dae63a9a28decc0f698dc01007d3'}
    if any(sha256((root / name).read_bytes()).hexdigest() != expected for name, expected in checks.items()):
        raise ValueError('reference_artifact_mismatch')
    bundle = ModelBundle.load(root / 'models/original/C')
    if bundle.metadata['model_key'] != 'C' or bundle.metadata['kind'] != 'amount' or bundle.features != PROFILE:
        raise ValueError('reference_model_contract')
    profile = json.load(sys.stdin)
    result = bundle.predict(profile)
    report = json.loads((root / 'reports/tables/observed_metrics.json').read_text(encoding='utf-8'))['C']
    if report['comparison_basis'] != 'observed_preprocessed_targets' or report['n'] < 1:
        raise ValueError('reference_validation_missing')
    print(json.dumps(dict(amounts=dict(zip(CODES, result['amounts'][0].tolist())),
                         category_mae=report['weighted']['category_mae_won'],
                         total_mae=report['weighted']['total_mae_won'], samples=report['n'],
                         model=bundle.metadata['version']), allow_nan=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'error': type(error).__name__}))
        sys.exit(1)

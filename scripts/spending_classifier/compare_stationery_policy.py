"""문구 정책 SVM을 같은 새 정답으로 비교한다. 행별 실거래는 출력하지 않는다."""
import gc
import json
from pathlib import Path
import sys

import joblib
import pandas as pd
from threadpoolctl import threadpool_limits

# 저장소의 공유 전처리/평가 함수를 사용한다.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.spending_classifier.common import ROOT, sha256, write_json
from scripts.spending_classifier.evaluate_csv import read_labels, add_predictions, scores, validate_bundle
from scripts.spending_classifier.predict import predict
from scripts.spending_classifier.train_eval import evaluate, predict_probabilities


def main():
    data = ROOT / 'data/spending_classifier/prepared/content_stationery_policy_svm'
    result = ROOT / 'reports/spending_classifier/content_stationery_policy_svm_retry'
    output = result / 'comparison.json'
    if output.exists():
        raise FileExistsError(output)
    prep = json.loads((data / 'preparation.json').read_text(encoding='utf-8'))
    for file, key in [('prepared.csv', 'prepared_sha256'), ('evaluation_labels.csv', 'evaluation_sha256')]:
        assert sha256(data / file) == prep[key], '보정 데이터 해시 불일치'
    valid = pd.read_csv(data / 'prepared.csv', keep_default_na=False,
                        usecols=['name','category','source_mask','brand_candidate','split'])
    valid = valid.loc[valid['split'].eq('valid')].copy()
    labels = read_labels(data / 'evaluation_labels.csv')
    samples = ['알파문구역삼점','동아문구사','교보문고강남점','시대서점','강남운전학원','스터디카페강남점']
    report = {'scope':'같은 새 정답, 개발용 평가. 독립 최종 평가 아님',
              'preparation':prep, 'models':{}}
    for name in ['content_policy_svm','content_stationery_policy_svm']:
        path = ROOT / 'models/spending_classifier/artifacts' / name / 'spending_category_svm.joblib'
        bundle = joblib.load(path)
        validate_bundle(bundle)
        with threadpool_limits(limits=2):
            probabilities = predict_probabilities(bundle['estimator'], valid['name'].to_numpy())
            validation = evaluate(valid, probabilities)
            transactions = add_predictions(labels, bundle)
            primary = scores(transactions.loc[transactions['group'].eq('primary_10_categories')], bundle['threshold'])
            sample_predictions = predict(bundle, samples)
        report['models'][name] = {'model_sha256':sha256(path),'valid_on_new_policy':validation,
                                 'transactions_on_new_policy':primary,
                                 'synthetic_samples':[dict(input=n,**r) for n,r in zip(samples,sample_predictions)],
                                 'versions':bundle['versions']}
        print(name, 'valid accuracy', validation['accuracy'], 'macro-F1', validation['macro_f1'],
              'transactions', primary['rows'], primary['accuracy'], flush=True)
        del bundle, probabilities, transactions
        gc.collect()
    old = report['models']['content_policy_svm']['transactions_on_new_policy']
    new = report['models']['content_stationery_policy_svm']['transactions_on_new_policy']
    assert old['rows'] == new['rows'] == 189
    write_json(output, report)


if __name__ == '__main__':
    main()

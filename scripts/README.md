# 전처리·ML 실험 코드

저장소 루트에서 실행한다. 입력 원자료·거래 라벨·모델 가중치·행 단위 평가 결과는 Git에 넣지 않고 별도로 준비한다. 기본 작업 위치는 이 PC에서 Git 제외된 `local_workspace/`다. 기존 `scripts/spending_classifier/`와 `scripts/budget_allocation/`의 학습·추론 코드는 그대로 사용한다.

## 데이터 전처리

```powershell
python -m pip install -r scripts/requirements-preprocessing.txt

# 통신판매 정제: 기존 구조정리 파일과 manifest가 있는 입력 폴더가 필요하다.
python scripts/refine_mail_order_merchants.py --root local_workspace --output local_workspace/data/mail_order_refined_shared --as-of 2026-10-15

# 통신판매 v2 후보 → 2026-10-20 정책 v3. 검토·제외행을 복구하지 않는다.
python scripts/relabel_mail_order_candidates.py --dry-run
python scripts/relabel_mail_order_candidates.py --output local_workspace/data/ml_training_candidates/mail_order_shared.csv

# 상가 기존 v3 → v3.2. 원본 정제 CSV·기존 결과·업종 결정표가 필요하다.
python scripts/relabel_store_industries.py --output local_workspace/data/store_labels/shared_v3.2

# 가계동향 2019~2026 CSV 8개 → 10개 카테고리 정제 결과.
python -m scripts.household_budget.preprocess --raw local_workspace/raw/household_survey_2019_2026 --output local_workspace/data/household_budget_shared
```

각 명령은 **존재하지 않는 새 출력 경로**를 사용한다. 통신판매·상가의 `--source`, 상가의 `--decision`, 가계동향의 `--mapping`으로 입력 위치를 지정할 수 있다. 상가 결정표는 별도 제공하는 `store_category_decisions_20261015.xlsx`이며 기본 경로는 `local_workspace/output/data_research/category_policy/`다.

가계동향 매핑표는 `household_budget/category_mapping_v2.csv`다. 기존 정책과 계산을 유지한다: 학습용 2019~2025와 확인용 2026 분리, 근로자 1인 가구·20~34세, 생활비에서 고정비·기타·자동차구입 제외, 목표 비율 10개 유지. 결과 폴더에 매핑표와 처리 기록도 함께 저장한다. 공공용 통계의 중분류 한계, 원자료 기간 해석·이용조건 미확인 사항은 기존 한계로 남는다. 모듈을 import할 때는 처리하지 않으며 원자료는 변경하지 않는다.

## 소비 분류 비교 실험

```powershell
python -m pip install -r scripts/spending_classifier/requirements.txt
python -m pip install -r scripts/spending_classifier/requirements-bert.txt

python -m scripts.spending_classifier.comparison_data --output local_workspace/data/spending_classifier/comparisons/shared_comparison
python -m scripts.spending_classifier.compare_experiments --data local_workspace/data/spending_classifier/comparisons/shared_comparison --arms svm_base_data bert_base_data
python -m scripts.spending_classifier.compare_transport_rules --help
python -m scripts.spending_classifier.evaluate_transport_rules --help
```

기존 전처리 결과 `prepared.csv`와 별도 거래 평가 CSV가 필요하다. 위치는 `comparison_data --help`의 `--prepared`, `--evaluation`으로 바꾼다. `--augmentation`에 명시적으로 확인된 보강 라벨 CSV를 넣으면 보강 조건 `B`, `D`도 실행할 수 있다. BERT는 최초 실행에 사전학습 모델 다운로드가 필요하다. 비교 데이터 분할·seed·설정을 고정하고, 이미 검토한 데이터의 점수를 독립적인 신규 테스트 성능으로 해석하지 않는다. 저장 예측의 교통 규칙 평가는 모델을 다시 학습하지 않는다.

## 예산 모델 실험

```powershell
python -m pip install -r scripts/budget_allocation/requirements.txt
python -m scripts.budget_allocation.compare_sampling_strategies --help
```

`compare_sampling_strategies`는 기존 10개 정제 결과에서 A/B 표본 선정 방식과 shallow CatBoost를 비교한다. `--data-dir`로 정제 결과, `--package-root`로 별도 v13 원자료 패키지의 위치를 지정한다. 원자료 manifest와 해시를 검증하며 가구 식별자는 Git에 포함하지 않는다. 과거 A/B 파라미터는 `living_expense_prediction/config/model_params.json`에서 읽고, 선택은 시간순 CV로 수행한다. 2025~2026 점수는 재평가다.

`living_expense_prediction/expense_predictor/`는 **과거 v13 연구용** 전처리·학습·추론·평가·합성 사례·시각화 라이브러리다. 정식 10개와 기타를 포함한 **11개 출력**을 유지하며, 현재 서비스의 10개 예산 배분 모델을 대체하지 않는다. 필요한 통계자료·별도 설정·가중치를 함수의 `root`로 전달한다. `raw_manifest.json`, `original_target_policy.json`(학습 표본 ID 포함), `sample_ids.json`, 모델 metadata, 실행 출력이 든 노트북은 공유본에서 제외했다. 함께 올린 `config/`는 식별자가 없는 실험 설정이며 서비스 정책을 새로 확정하는 자료가 아니다.

```powershell
python -m pip install -r scripts/living_expense_prediction/requirements.txt
```

```python
from pathlib import Path
from scripts.living_expense_prediction.expense_predictor.data import read_raw, preprocess
from scripts.living_expense_prediction.expense_predictor.models import train_model

workspace = Path("local_workspace/models/living_expense_prediction")
raw, inventory = read_raw(workspace)
data, audit = preprocess(raw, workspace)
model = train_model(data, "A", workspace)  # 메모리에서 학습; 기존 가중치를 저장/교체하지 않음
```

## 합성 테스트

테스트는 원자료·기존 가중치·API 키 없이 임시 합성 데이터를 사용한다. 위 전처리·SVM·예산 모델 의존성을 설치한 환경에서 실행한다. BERT 비교 테스트는 모델 다운로드나 미세조정을 실행하지 않는다.

```powershell
python -m unittest discover -s tests/data_preprocessing -v
python -m unittest discover -s tests/ml_models -v
```

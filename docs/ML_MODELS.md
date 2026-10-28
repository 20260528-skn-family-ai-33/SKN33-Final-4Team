# 거래 분류 SVM · 생활비 배분 CatBoost

2026-10-26에 저장한 두 모델의 전처리·학습·추론 코드를 팀 저장소에 공유한다.
웹 API에 연결하거나 모델을 다시 학습한 변경은 아니다.

| 모델 | 공유 코드 | 저장된 모델 |
| --- | --- | --- |
| 상호명 소비 분류 | `scripts/spending_classifier/` | `spending_category_svm.joblib` |
| 생활비 카테고리 배분 | `scripts/budget_allocation/` | `budget_allocation.joblib` |

가중치, 원본·정제 CSV, 개별 거래 라벨·예측, 노트북 실행 출력과 가상환경은 Git에 포함하지 않는다.
가중치는 모델 담당자에게 별도로 받아 아래 로컬 경로에 두고, 공유 지표 JSON에 기록한 SHA256과 대조한다.
joblib은 신뢰한 출처의 파일만 로드한다. 데이터 없이 추론하려면 가중치 파일만 있으면 된다.

현재 저장 모델의 집계 성능·설정은 `reports/ml_models/`에 있다. 개인 경로와 행별 자료는 제거했다.
이 수치는 아래 소스 공유 과정에서 새로 산출한 성능이 아니다.

## 환경과 테스트

아래 명령은 저장소 루트에서 실행한다. Python 3.12를 사용한다.
두 모델의 학습 환경에서 pandas 버전이 달랐으므로 가상환경을 분리한다.
기존 웹 서버의 requirements.txt는 변경하지 않는다.

```powershell
python -m venv .venv-ml
& .venv-ml/Scripts/python.exe -m pip install -r scripts/spending_classifier/requirements.txt
& .venv-ml/Scripts/python.exe -m unittest discover -s tests/ml_models -p 'test_spending*.py' -v

python -m venv .venv-budget
& .venv-budget/Scripts/python.exe -m pip install -r scripts/budget_allocation/requirements.txt
& .venv-budget/Scripts/python.exe -m pip install -r scripts/budget_allocation/requirements-inference.txt
& .venv-budget/Scripts/python.exe -m unittest discover -s tests/ml_models -p 'test_allocation*.py' -v
```

예산 모델의 저장 당시 scikit-learn은 1.9.1이다. 추론만 할 때는 `requirements-inference.txt`만 필요하다.
테스트는 합성 데이터와 임시 폴더를 사용한다. 전체 데이터 재학습을 실행하지 않는다.

## 1. 거래 분류 SVM

상호명을 NFKC·소문자화하고 법인 표기·공백·기호를 정리한다. 괄호 안 내용과 지점명은 보존한다.
문자 TF-IDF(char_wb, 1~3 gram) → LinearSVC(C=1, balanced) → 학습셋 내부 5-fold sigmoid 보정 순서다.
최종 모델은 브랜드 후보 제외 학습 1,166,171행을 사용했다.

10개 출력 코드는 `common.CATEGORIES` 순서다.
식비, 주거·통신, 교통·차량, 생활용품, 패션·쇼핑, 의료·건강, 뷰티·미용, 교육, 문화·여가, 여행·숙박이며
`기타`·`미분류`는 학습 정답이 아니다.

```powershell
$svm = 'local_workspace/models/spending_classifier/artifacts/content_policy_svm/spending_category_svm.joblib'
Get-FileHash -Algorithm SHA256 $svm
& .venv-ml/Scripts/python.exe -m scripts.spending_classifier.predict --model $svm --names '새봄동물병원' '새봄운전학원'
```

Python에서는 `scripts.spending_classifier.predict.predict(bundle, names)`를 호출한다.
`bundle`은 위 파일을 `joblib.load`로 한 번 로드해 재사용한다.
출력에는 정규화 상호, 카테고리 코드·이름, 확신도, 전체 확률과 `passes_experimental_threshold`가 있다.
빈 이름은 `UNCLASSIFIED`로 반환한다. 확신도 기준은 저장된 0.5이며 서비스 자동 확정 기준으로 검증된 값은 아니다.
규칙·사전·LLM·사용자 선택을 포함한 운영 흐름은 이 추론 모듈 밖에서 처리한다.

### 전처리·학습 재현

다음 자료는 Git에 없으며 별도 준비가 필요하다. 기본 경로는 `local_workspace/` 기준이다.

| 파일 | 필요한 열 |
| --- | --- |
| `data/store_labels/v3.2_20261020/stores_ml_training.csv` | 상호명, 소비카테고리, 학습대상여부 (정책 보정에는 상권업종소분류명도 필요) |
| `data/ml_training_candidates/mail_order_training_candidates_v3_20261020.csv` | 상호, 라벨, 학습사용 (정책 보정에는 인터넷도메인도 필요) |
| `data/store_labels/multi_category_brand_candidates.csv` | 브랜드명, 검색키_정규식 |

```powershell
# 기존 출력이 있으면 내용을 설명하는 다른 폴더명을 지정한다.
& .venv-ml/Scripts/python.exe -m scripts.spending_classifier.prepare_data --output local_workspace/data/spending_classifier/prepared/shared_baseline
& .venv-ml/Scripts/python.exe -m scripts.spending_classifier.train_eval --data local_workspace/data/spending_classifier/prepared/shared_baseline

# 정책 보정에는 별도 거래 라벨 CSV도 필요하다. 원본을 보존하고 보정본을 만든다.
& .venv-ml/Scripts/python.exe -m scripts.spending_classifier.correct_policy_data --base local_workspace/data/spending_classifier/prepared/shared_baseline --evaluation local_workspace/data/spending_classifier/evaluation/transaction_labels_v1.csv --output local_workspace/data/spending_classifier/prepared/shared_content_policy
& .venv-ml/Scripts/python.exe -m scripts.spending_classifier.retrain_policy_svm --data local_workspace/data/spending_classifier/prepared/shared_content_policy
```

`prepare_data`는 원래 빈도 기준 80% 다수결, 이름 중복 정리, 그룹 단위 약 80:10:10 분할을 수행한다.
`train_eval`은 후보 비교 → 전체 train 재학습 → 검증 기준선 선택 → 잠근 테스트 평가 순서이며
`--phase compare`, `fit-final`, `evaluate`로 분리할 수 있다.
후보 비교 결과의 정규화 강도는 `C` 키로 기록하며 과거 로컬 기록의 `bert_base_data` 키도 읽는다.
`correct_policy_data`는 확인된 콘텐츠 플랫폼과 벌툰 업종 정책 보정을 재현한다. 동명 상호는 별도 근거로 구분한다.
`retrain_policy_svm`은 보정 데이터 해시를 검증한 뒤 SVM만 재학습하고 검증 결과를 남긴다.

평가 도구는 `python -m scripts.spending_classifier.evaluate_csv --help`로 확인한다.
최신 정책 모델 평가는 `--model`, `--input`, `--prepared`, `--output`을 모두 명시한다.
기본 평가 모델은 과거 `baseline_svm`이다. 거래 라벨 필수 열은
`가맹점명, 등장횟수, 비소비여부, category, categoryName, 확신, 근거, 검토필요`다.
행별 예측·오답 출력에는 거래명이 포함되므로 로컬에서 보관한다.

현재 검증 정확도는 85.82%, 검증 Macro-F1은 0.8234다.
현재 거래 189건 정확도는 87.30%, 고정 10개 Macro-F1은 0.4581이다.
거래 정답은 사람의 독립 검수 전 임시 라벨이고 개발 과정에서 확인한 자료다.
현재 정책 모델의 새 약한 라벨 테스트 점수는 없으며 이전 모델의 85.66%와 구분한다.

## 2. 생활비 배분 CatBoost

`catboost_shallow`: depth=4, iterations=200, learning_rate=0.05, MultiRMSE, seed=42.
2019~2024년 7,936행으로 학습했다. 2022·2023·2024 시간순 교차검증에서
33개 후보 중 조사 가중치 적용 비율 MAE의 연도 평균이 가장 낮은 모델을 선택했다.

| 입력 | 의미 |
| --- | --- |
| age | 20~34세 정수 |
| sex | 0=남, 1=여 |
| disposable_income | 소득−비소비지출, 양수 원 금액. 로그 변환 전 입력 |
| has_vehicle | 0=없음, 1=있음 |
| fixed_expense_amount | 월세·수도·관리비·연료·통신 고정비, 0 이상 |
| living_budget_amount | 고정비 제외 배분 대상 생활비, 양수 |

```powershell
$budget = 'local_workspace/models/living_expense_prediction/models/shallow/catboost_allocation/budget_allocation.joblib'
Get-FileHash -Algorithm SHA256 $budget
& .venv-budget/Scripts/python.exe -m scripts.budget_allocation.predict_allocation --model $budget --age 27 --sex 0 --disposable-income 3000000 --has-vehicle 1 --fixed-expense-amount 700000 --living-budget-amount 1200000
```

백엔드에서는 `scripts.budget_allocation.predict_allocation`의 `load_bundle(path)`와
`predict_profile(bundle, profile)`을 사용한다. 소득을 내부에서 한 번 로그 변환한다.
음수 비율을 0으로 제한하고 합계 1로 정규화하며, 모두 0이면 학습 가중 평균으로 대체한다.
`ratios`는 10개 비율, `draft_amounts`는 입력 생활비에 곱한 소수 원 금액이다.
정수 원 배분·LLM 조정·사용자 확정은 포함하지 않는다.

### 비교·학습 재현

`local_workspace/data/household_budget/`에 다음 파일이 필요하다.

- `household_single_20_34_2019_2025.csv`
- `household_single_20_34_2026_holdout.csv` (연도 평가용)
- `category_mapping_v2.csv`
- `processing_records.json` (출력 파일별 SHA256 기록)

```powershell
& .venv-budget/Scripts/python.exe -m scripts.budget_allocation.compare_allocation_models --output local_workspace/reports/budget_prediction/shared_allocation_comparison
& .venv-budget/Scripts/python.exe -m scripts.budget_allocation.train_allocation --output local_workspace/models/budget_allocation/catboost_shallow
```

비교 코드는 정제 CSV의 금액·10개 비율·고정비 차감 정의와 해시를 검증한다.
가중치는 조사 가중치 × 반감기 2년 시간 가중치이며 평균 1로 정규화한다.
학습 코드는 2019~2024년만 사용하고 가중치·메타데이터·추론 도구를 새 폴더에 저장한다.
저장 후 재로딩 예측 일치 검증을 통과해야 최종 모델 파일명이 생성된다.

CV 비율 MAE는 5.3250%p, 2025년 5.3173%p, 2026년 5.3073%p다.
2025·2026은 기존에 확인한 사후 평가 자료로 새 독립 테스트가 아니다.
관측 생활비 총액을 입력하는 배분 모델이므로 미래 총생활비 예측 능력이나 절감 예산의 적합성을 뜻하지 않는다.
공공용 통계 분류 단위의 한계로 일부 세부 항목이 인접 카테고리에 포함된다.
2024년 차량 보유 결측의 0 처리와 반복 조사 가구의 독립성도 기존 한계로 남아 있다.

## 파일 보관

새 작업 데이터·모델·평가 출력의 기본 위치는 `local_workspace/`다.
이 폴더는 현재 작업 PC에서 `.git/info/exclude`로 제외되어 있다. 다른 PC에서는
`git check-ignore local_workspace/`로 제외 상태를 확인한 뒤 데이터를 배치한다.
선별된 소스와 집계 지표 외에 생성되는 CSV·가중치·개인정보를 일괄 스테이징하지 않는다.

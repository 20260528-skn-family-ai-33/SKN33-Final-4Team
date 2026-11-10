# 예산 설계 A·B 비교

최초 예산 설계의 서버 계산·검수와 합성 입력 비교 창이다. 실제 서비스 API·DB·프론트 저장 연결은 후속 통합 대상이다. 정책 입력의 예시값은 확정된 서비스 수치가 아니다.

## A/B 자동 선택 (2026-11-05 사용자 확정)

- 최근 완료된 달력월 3개 이력이 준비되고, 10개 카테고리 각각의 **3개월 합산 거래 건수 중 최댓값이 11건 이상**이면 B를 선택한다. 모든 카테고리가 11건 이상일 필요는 없다.
- 이력 부족·미확인, 최댓값 10건 이하, 월 합계 입력처럼 거래 건수 확인이 불가능한 경우는 A를 선택한다. 3개월 조회가 정상 완료됐고 해당 월 소비가 없으면 0건으로 센다. 현재 진행 중인 달은 포함하지 않는다.
- 기존 B 집계의 양수 순액 변동지출 건수를 재사용한다. 고정비·이체·수입·전액 취소·ETC·미분류는 10개 카테고리의 건수에 들어가지 않는다. 일회성 후보를 예산 기준에서 제외하기 전의 거래 건수를 사용한다. 이 자동 선택 임계치 10은 일회성 MAD의 표본 수 기준과 독립된 정책이다.
- 순수 판정은 `plan_selection.select_plan(history)`, 선택된 안만 계산하는 진입점은 `compare_plans.recommend_budget(case, use_llm=False)`다. 결과에는 selection(선택 안·사유·카테고리별 합산 건수·최댓값), selected_variant, result가 있다. B의 예측 변형 여부는 명시적인 forecast 설정으로 정하며, B 계산 실패를 A로 자동 전환하지 않는다.
- 비교 창은 양쪽 계산을 유지하면서 `자동 선택: A/B`와 사유를 표시한다. comparison 결과의 selected_result는 자동 선택된 안을 가리킨다. 월 합계 모드에서도 B 실험값은 비교할 수 있지만 자동 선택은 A다.
- 선택한 안만 실행하려면 `python scripts/budget_design/compare_plans.py --auto`를 사용한다. `--llm`은 선택된 경로의 외부 호출 설정을 켜므로 합성 자료로만 사용한다. 실제 API·라우터 연결은 이번 범위 밖이다.

## B 미분류 후보 가정 재계산

거래 목록의 미분류 항목에 선택적으로 `suggestion_candidates: [{category, confidence}]`와 `suggestion_source: 'ml'`을 넘기면 B가 내부 복사본에서 최상위 후보를 가정해 이상치 제외 평균과 예산을 다시 계산한다. 실제 분류는 변경하지 않는다. 재계산 후 늘어나는 항목만 기존 배분 잔액(`unallocated`) 안에서 보완하며, 잠금·사용자 최소액·보호 설정을 유지한다. 후보 없음·잘못된 점수·혼합 출처·잔액 없음이면 추가 보정하지 않는다. `candidate_adjustment`가 보정 상태와 항목별 추가액을 제공하고, `history`의 미분류 금액은 그대로 남는다. 월 합계 입력만으로 후보를 만들지는 않는다.

최종 표시 단위는 보정 없는 B부터 정산하고, 그 뒤 남는 잔액 안에서만 보완한다. 보정과 무관한 항목의 최종 금액을 줄이지 않는다. 최종 `candidate_adjustment.corrections[].amount`는 실제 증가액이고 `proposed_amount`는 계산 단계 제안액이다. 반올림 뒤 실제 증가가 없으면 `no_effect_after_rounding`이며 보정 원인을 제거한다. `start_allocations`는 보정 전 출발액을 보존한다.

## 실행

### 서비스 정책과 배포 전 ML 점검

새 서비스는 `BUDGET_DESIGN_ENABLED=false`가 기본이다. 미지정 정책은 승인된 `2026-11-10-forecast-band`를 사용하며 A의 전체 이동 한도는 기존 코드의 생활비 20%다. 사용자 승인(2026-11-10)에 따라 서비스 B의 예측 허용 범위는 하향 20%·상향 20%로 설정했다. `BUDGET_DESIGN_POLICY` JSON 또는 Django 설정으로 덮어쓸 수 있다. 명시한 JSON이 잘못됐거나 `null`이면 기본값으로 숨겨 대체하지 않고 추천을 차단한다. 이력 verifier와 기능 활성화는 별도이며 이 설정만으로 외부 LLM을 호출하지 않는다.

### 서비스 LLM 연결 — 합성 검증 전용 (2026-11-07)

`backend/budgets/design_service.py`의 `recommend`가 기존 A `choose_budget`와 B `forecast_budget`를 호출한다. A/B 자동 선택 기준은 유지한다. 새 패키지·DB 필드·사용자 입력은 추가하지 않는다.

- 서버 내부 호출의 `allow_external=True`와 함께 합성 입력(`synthetic=True`)이거나 로그인 회원의 최신 국외이전·건강 관련 요약 별도 동의 두 건이 모두 확인돼야 LLM 경로가 실행된다. 불리언 이외 값은 거절한다. 사용자 API 본문에 이 플래그를 추가하지 않았으며 기존 `run_recommend`는 기본값으로 호출하므로 일반 사용자의 외부 전송은 계속 차단된다. 기능 스위치도 자동으로 켜지지 않는다. 동의 저장/철회 게이트는 연결했다. 예산 요청은 미국 지역 API만 사용하며 서비스 LangSmith는 OFF다. 운영 처리위탁 계약·개인정보 처리방침 확인과 활성화는 후속 작업이다.
- A는 기존 서버 미리보기를 비교해 유지·부분 증액·종료를 선택한다. 전체 생활비 이동 한도 20%, 최소금액·잠금·시스템 하한을 유지하며, 호출 실패 시 부분 조정을 폐기하고 검수된 기본안으로 돌아간다. 모델·호출 제한은 기존 `design_selection.py` 설정을 사용한다.
- B는 `BUDGET_DESIGN_POLICY`의 `b_forecast_band`에 `down_percent`, `up_percent`가 명시된 경우 기존 예측기를 사용한다. 서비스 초기 정책은 승인된 20/20을 전달한다. 별도 정책에서 band를 생략하면 규칙 B를 반환하고 `forecast_band_required`를 남긴다. 예측 함수 자체나 로컬 비교 창에 숨은 기본값을 추가하지 않는다. 예산 대상 월은 완료된 이력 다음의 현재 추천 월(`today.month`)이다. 모델은 기존 `LLM_MODEL`, 설명 길이는 기존 예측기 제한을 사용한다.
- B의 잘못된 응답은 기존과 같이 한 번 재시도하고, 재검수에도 실패하면 규칙 B로 돌아간다. 성공한 예측의 lower/upper는 최종 만 원 우선·5천 원 보조 정산에도 전달한다. 원 단위 잠금은 그대로 보존한다.
- 결과 `_design.llm`에 `completed`/`fallback`/`not_run`과 사유를 기록한다. B는 시도·재시도·검수 실패 횟수와 위반 종류도 기록한다. 키·프롬프트·원본 거래·가맹점은 이 메타데이터에 넣지 않는다. 서비스 A 호출은 LangSmith 환경 설정과 무관하게 추적을 끄며, B 예측도 LangSmith 추적을 시작하지 않는다. 기존 로컬 계산 창의 추적 설정은 변경하지 않는다.
- DB 없이 새 연결만 검증: backend에서 `python manage.py test budgets.test_design_service_llm --noinput`. 모든 입력은 합성이며 HTTP 응답을 모의 처리한다. 실제 LLM 품질·속도·비용과 저장·승인 통합은 이 테스트의 검증 범위 밖이다.

### B 이력 검증 연결 준비 (2026-11-07)

서비스는 이번 달을 제외한 완료 달력월 3개를 대상으로 수집 범위와 거래 정합성을 따로 확인한다. `BUDGET_HISTORY_VERIFIER` Django 설정이 없으면 `budgets.design_history.verify_budget_history`를 기본 사용한다. 명시적으로 `None`이면 정합성은 unknown으로 유지하고, 기존 사용자 정의 callable도 계속 사용할 수 있다. 잘못되거나 누락된 근거 값은 verified로 바꾸지 않는다. 이 연결이 `BUDGET_DESIGN_ENABLED`나 외부 LLM 호출을 켜지는 않는다.

- 공용 `finance.history_verifier`의 기본 호출은 그대로다. 예산용 연결에서만 `use_confirmed_salary=True`를 넘겨 현재 유효한 사용자 급여 확인을 사용한다. `known_deposits(user)`의 적격 원화 입출금계좌 입금 중 `state='confirmed'`, 유형 `income`인 거래만 해당하며, 확정 당시 금액·날짜·적요·계좌가 현재 값과 같아야 한다. 검증기가 먼저 읽은 거래와도 다시 비교한다.
- 이 근거는 해당 입금의 `unattributed_inflow` 판정만 제외한다. 다른 중복·취소·환불 문제를 지우지 않는다. `auto`·재확인·거절·스냅샷 누락·변경된 확정, 일반 입금과 불명확한 취소는 기존 검사를 유지한다. SalaryDecision 상태를 갱신하거나 소비 금액을 다시 차감하지 않는 읽기 전용 연결이다.
- 수집 완료와 정합성 검증을 모두 통과한 최근 3개월에서 분류된 카테고리 최대 거래 건수가 11건 이상이면 B, 10건 이하면 A다. 미분류 건수로 B 자격을 만들지 않고 미분류 금액도 이력에서 지우지 않는다. 미분류·환불 허용 비율 등의 새 정책 숫자는 추가하지 않았다.
- 2026-11-07 v2 소스에 main의 SalaryDecision과 users 연결용 마이그레이션을 통합했고, 2026-11-10에는 `.env`가 지정한 로컬 서비스 DB에 대기 15개를 적용했다. 백업 실제 복원·기존 행 보존·합성 회귀 58개·대기 0개를 확인했다. 서비스 기능 스위치는 OFF이며 활성화는 별도다. 배포/알파 fixture DB는 이관하지 않았다. 필요한 코드가 없는 다른 실행 환경에서는 모든 검수값을 unknown으로 유지한다.
- DB 없는 연결 검증: backend에서 `python manage.py test budgets.test_design_history.HistoryProofTest --noinput`.
- main과 DB 변경 기록을 정리한 뒤의 합성 DB 검증: `python manage.py test budgets.test_design_history budgets.test_design_service.DesignHistoryTest finance.test_history_verifier --noinput`. 이번에는 실제 작업 DB 대신 main 기반 별도 복사본과 자동 생성·삭제한 PostgreSQL 테스트 DB에서 실행했다.

배포 대상 OS의 Python 3.12로 전용 가상환경을 만들고 `scripts/budget_allocation/requirements-inference.txt`의 고정 버전을 설치한다. Windows 가상환경 폴더를 Linux/macOS에 복사하지 않는다. 신뢰할 수 있는 별도 저장 위치의 모델을 공급하고 아래 두 환경변수에 절대 경로를 지정한다. 모델은 Git에 넣지 않는다.

```powershell
# Windows 예시 경로. 실제 배포 위치로 바꾼다.
$env:BUDGET_INFERENCE_PYTHON='C:/budget-runtime/Scripts/python.exe'
$env:BUDGET_MODEL_PATH='C:/budget-model/budget_allocation.joblib'
python -m scripts.budget_design.inference
```

```sh
# Linux/macOS 예시 경로. 저장소 루트에서 실행한다.
export BUDGET_INFERENCE_PYTHON=/opt/budget-runtime/bin/python
export BUDGET_MODEL_PATH=/opt/budget-model/budget_allocation.joblib
python -m scripts.budget_design.inference
```

이 명령은 DB·외부 LLM 없이 고정 합성 프로필로 실제 모델을 실행한다. 성공 시 `status=ready`, 모델명/버전·카테고리 수·합계를 출력하고 종료 코드 0을 반환한다. 실패 시 종료 코드 2와 오류 종류만 출력한다. 내부적으로 기존 모델 체크섬·버전·10개 카테고리·비율 합·금액 합을 검수한다. 통과했다고 기능 스위치나 이력 검증이 자동 활성화되는 것은 아니다. 실제 배포 서버에서도 동일 명령을 통과한 뒤 연결을 검토한다.

2026-11-07 로컬 확인에서는 기존 전용 환경(Python 3.12.14, numpy 2.5.4, pandas 3.1.0, catboost 1.2.10, joblib 1.6.0)과 체크섬을 확인하고 실제 모델 실행에 성공했다. 단일 합성 100만 원 예측은 약 2.29초, 카테고리 10개·비율 합 1·초안 합 100만 원·평균 비율 대체 없음이었다. 이는 추천 품질이나 서버 평균 응답 시간 검증이 아니다.

혜리 PC의 기존 실제 경로는 아래와 같다. **2026-11-10 등록 완료:** 원본 `C:/FINAL_PRJ/SKN33-Final-4Team/.env`와 서비스 작업 폴더 `C:/FINAL_PRJ/SKN33-Final-4Team/.worktrees/budget-design-v2/.env`에 두 변수를 등록했다. 기존 원본 설정은 보존했으며 v2 파일에는 ML 경로 2개만 둔다. 공통 Django 환경변수는 기존 원본 설정 등 실행 환경에서 별도로 공급해야 한다. 비밀값 복사·DB 적용·서비스 활성화·서버 재시작은 하지 않았다. 이미 실행 중인 서버는 재시작해야 새 값을 읽는다. 이 Windows 경로를 배포 서버의 경로로 사용하지 않는다.

- `BUDGET_INFERENCE_PYTHON`: `C:/FINAL_PRJ/SKN33-Final-4Team/local_workspace/.venv-budget-inference/Scripts/python.exe`
- `BUDGET_MODEL_PATH`: `C:/FINAL_PRJ/SKN33-Final-4Team/local_workspace/models/living_expense_prediction/models/shallow/catboost_allocation/budget_allocation.joblib`

프로젝트 실행 환경을 준비하고 `.env`를 읽은 프로세스에서 저장소 루트의 `python -m unittest discover -s tests/budget_design -p 'test_budget_inference.py'`로 재현한다. 실제 ML 검사 3개와 모의 CLI 검사 2개가 있으며, 등록한 파일을 읽는 새 프로세스에서 **5개 모두 통과**했다(2026-11-10, 실패·건너뜀 없음). 실제 검사는 합성 프로필 3개·반복 예측·잘못된 입력과, 실제 모델을 사용하는 A/B 함수의 최소금액·원 단위 잠금·합계·단위를 확인한다. DB·LLM 호출은 차단한다. 모델 또는 실행 경로가 없으면 실제 검사 3개는 skip된다.

로컬 DB 적용(2026-11-10)은 별도 단계에서 완료했다. 적용 전 백업은 `C:/FINAL_PRJ/SKN33-Final-4Team/local_workspace/data/db_backups/budget_design/before_budget_design_migrations_20261110.dump`다. 현재 로컬 DB에는 회원 2명만 있고 기존 최소금액·잠금·거래는 0건이었으며, 설정값이 있는 경우의 보존은 임시 PostgreSQL 테스트 DB에서 검증했다. 기존 행 보존·대기 0개·시스템 검사를 확인했고, 실제 DB에 합성 시험 데이터를 추가하지 않았다. 세부 결과는 `docs/agents/budget_design.md`의 해당 날짜 절을 따른다. 기능 활성화·외부 전송 동의 연결은 진행하지 않았다.

Django는 기동할 때 기존 설정 로더로 `.env`를 읽는다. Django 없이 ML CLI만 확인할 때는 서비스 작업 폴더 루트(`C:/FINAL_PRJ/SKN33-Final-4Team/.worktrees/budget-design-v2`)에서 아래처럼 등록 파일을 먼저 읽는다. 여기서는 경로를 임시로 다시 지정하지 않으며 DB·LLM을 호출하지 않는다.

```powershell
python -c "from dotenv import load_dotenv; load_dotenv('.env'); from scripts.budget_design.inference import main; raise SystemExit(main())"
```

저장소 루트의 Python 환경에 `requirements.txt`를 설치한 뒤 실행한다.

```sh
python scripts/budget_design/compare_window.py
```

Windows는 `.venv`가 있으면 같은 폴더의 `A_B_예산비교_실행.cmd`를 실행할 수 있다. macOS는 Tk를 지원하는 Python이 필요하다.

A 비교에는 별도 공유하는 신뢰 가능한 `catboost_shallow` 모델과 추론 환경이 필요하다. 모델은 Git에 포함하지 않는다. `scripts/budget_allocation/requirements-inference.txt`로 전용 환경을 준비하고 다음 환경변수에 **절대 경로**를 지정한다.

- `BUDGET_INFERENCE_PYTHON`: 추론 환경의 Python 실행 파일(Windows `Scripts/python.exe`, macOS `bin/python`).
- `BUDGET_MODEL_PATH`: 공유받은 `budget_allocation.joblib`. 허용 체크섬은 `predict_worker.py`에서 검증한다.
- `OPENAI_API_KEY`, `LLM_MODEL`: B 예측 외부 호출 설정. A 모델 설정은 `backend/budgets/design_selection.py` 기준이다. 키는 루트 `.env` 또는 실행 환경에 두며 커밋하지 않는다.
- 선택적 LangSmith: `backend/budgets/requirements-observability.txt`를 설치하고 `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`를 설정한다. 외부 호출 체크를 끄면 추적도 꺼진다.

실제 개인정보·거래 내역은 입력하지 않는다. B LLM 예측은 하향/상향 범위를 직접 입력하고 체크를 켰을 때 합성 입력만 외부로 전송한다. B 규칙 열은 LLM을 호출하지 않는다. A·B 규칙·B LLM 예측의 최종 예산은 만 원 우선, 제약·합계상 필요하면 5천 원을 사용한다. 사용자 잠금액은 원액을 지킨다. 참고용 ML·소비 이력 열은 원래 값을 표시하며, 단위 정리 후 남는 배분액은 여윳돈으로 남긴다.

`기존 여윳돈`은 상위 배분에서 저축·쿠션 등을 확보한 뒤 남긴 금액이다. 감액 한도·보호 기준을 지켜도 부족할 때만 필요한 만큼 B 생활비에 충당한다. 결과의 배분 잔액 `unallocated`와 구분한다. 충당 후 B 생활비가 A보다 커질 수 있으므로 이동액과 남은 여윳돈을 함께 비교한다. 서비스에서는 이 이동에 대한 상위 배분 승인·저장이 추가로 필요하다.

## 테스트

저장소 루트에서 실행한다(모의 ML·LLM, 외부 호출 없음).

```sh
python -m unittest discover -s tests/budget_design -p 'test_plan_b_*.py'
python -m unittest discover -s tests/budget_design -p 'test_plan_selection.py'
python -m unittest discover -s tests/budget_design -p 'test_plan_ab_*.py'
python -m unittest discover -s tests/budget_design -p 'test_budget_design_simulator.py'
python -m unittest discover -s tests/budget_design -p 'test_budget_agent_runner.py'
```

서버 계산 검증은 `backend`에서 실행한다.

```sh
python manage.py test budgets.test_design_candidates budgets.test_design_explain budgets.test_design_reality budgets.test_design_selection budgets.test_design_tracing --noinput
```

가계 소비 중앙값·일회성 판정과 ML 감액 하한은 물가상 적정 생활비를 보장하지 않는다. 현재 시스템 가격 하한은 교통에 적용되며 식비·의료 등 기준은 별도 검토 대상이다. 상세 계약·미정 사항은 `docs/agents/budget_design.md`를 따른다.

교통 기준(2026-11-05 사용자 결정): 검증된 3개월 이력이 없으면 자차 없음 6만 원, 자차 있음 10만 원을 계산용 하한으로 적용한다. 검증된 교통 소비 기준이 해당 금액보다 낮으면 그 소비 기준까지만 보호하며 하한 때문에 이력 금액을 올리지 않는다. 자물쇠는 원액을 유지한다. 자차 10만 원은 휘발유 주행 가정에 따른 임시값이며 전기차 여부는 확인하지 못한다.

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

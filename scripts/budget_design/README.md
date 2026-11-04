# 예산 설계 A·B 비교

최초 예산 설계의 서버 계산·검수와 합성 입력 비교 창이다. 실제 서비스 API·DB·프론트 저장 연결은 후속 통합 대상이다. 정책 입력의 예시값은 확정된 서비스 수치가 아니다.

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

실제 개인정보·거래 내역은 입력하지 않는다. B LLM 예측은 하향/상향 범위를 직접 입력하고 체크를 켰을 때 합성 입력만 외부로 전송한다. B 규칙 열은 LLM을 호출하지 않는다. B 예측 최종 금액은 5,000원 단위이며 사용자 잠금액은 원액을 지킨다.

`기존 여윳돈`은 상위 배분에서 저축·쿠션 등을 확보한 뒤 남긴 금액이다. 감액 한도·보호 기준을 지켜도 부족할 때만 필요한 만큼 B 생활비에 충당한다. 결과의 배분 잔액 `unallocated`와 구분한다. 충당 후 B 생활비가 A보다 커질 수 있으므로 이동액과 남은 여윳돈을 함께 비교한다. 서비스에서는 이 이동에 대한 상위 배분 승인·저장이 추가로 필요하다.

## 테스트

저장소 루트에서 실행한다(모의 ML·LLM, 외부 호출 없음).

```sh
python -m unittest discover -s tests/budget_design -p 'test_plan_b_*.py'
python -m unittest discover -s tests/budget_design -p 'test_plan_ab_*.py'
python -m unittest discover -s tests/budget_design -p 'test_budget_design_simulator.py'
python -m unittest discover -s tests/budget_design -p 'test_budget_agent_runner.py'
```

서버 계산 검증은 `backend`에서 실행한다.

```sh
python manage.py test budgets.test_design_candidates budgets.test_design_explain budgets.test_design_reality budgets.test_design_selection budgets.test_design_tracing --noinput
```

가계 소비 중앙값·일회성 판정과 ML 감액 하한은 물가상 적정 생활비를 보장하지 않는다. 현재 시스템 가격 하한은 교통에 적용되며 식비·의료 등 기준은 별도 검토 대상이다. 상세 계약·미정 사항은 `docs/agents/budget_design.md`를 따른다.

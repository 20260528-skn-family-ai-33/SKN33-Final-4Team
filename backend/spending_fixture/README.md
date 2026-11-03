# 소비 분석 검증용 fixture

가상 사회초년생 3명(A 윤서 · B 민재 · C 지우)의 사용자 · 예산 · 거래 414행 · 수집 이력을 **별도 로컬 DB**에 넣고,
기존 소비 집계 함수가 독립 산술 정답(`data/expected.json`)과 맞는지 확인한다.
모든 값은 가상이다. 실제 거래 · 금융기관 · ML · LLM · CODEF 호출은 없다.

- `data/design.json` — 설계 명세(Django fixture 아님, loaddata 금지). `data/expected.json` — 서비스 코드 없이 계산한 정답.
  두 파일은 첨부 자료 사본이며 `seed.py` 가 해시로 확인한다. 고치지 말 것.
- seed 가 만든 approved 예산은 **테스트 스냅샷**이다. 실제 사용자 승인 · 엔진 검수 · ML 예측이 아니다.

## 대상 DB

| | 이름 |
|---|---|
| fixture DB | `money_cushion_router_fixture` |
| 자동 테스트 DB | `test_money_cushion_router_fixture` |

`settings.py` 가 공통 설정(`.env` 접속 정보)을 그대로 쓰고 DB 이름만 바꾼다. `.env` · `config/` 는 고치지 않는다.
호스트가 localhost 가 아니면 멈추고, 쓰기는 위 두 이름에서만 한다. PostgreSQL 17 + pgvector 필요(기존 마이그레이션 그대로).

## 사용법 (backend/ 에서)

```bash
python -m spending_fixture.seed                 # dry-run: DB 연결 · 쓰기 없이 생성 예정 건수 · 합계 · 검사
python -m spending_fixture.seed --create-db     # fixture DB 가 없으면 만든다
python manage.py migrate --settings=spending_fixture.settings
python -m spending_fixture.seed --apply         # fixture 사용자만 지우고 다시 넣기 → DB 건수 · 합계 출력
python -m spending_fixture.seed --status        # DB 건수 · 합계만
python -m spending_fixture.seed --reset         # fixture 사용자(와 그에 딸린 행)만 삭제

python manage.py test spending_fixture --settings=spending_fixture.settings
```

`--apply` 는 반복해도 ID · 시각 · 건수 · 합계가 같다(UUID5 · 고정 시각). 기존 DB flush/drop 은 하지 않는다.
공통 설정으로 `python manage.py test` 를 돌리면 이 테스트는 건너뛴다.

## 알파 페르소나 모드

기존 3인 시드와 별도로 `--alpha`를 지정하면 자취 직장인의 승인 계정
`alpha.home@example.test`와 온보딩 계정 `alpha.onboarding@example.test`를 만든다.
개인 데이터와 코드는 `local_workspace/data/alpha_test/`, `local_workspace/scripts/alpha_test/`에 있다. local_workspace는 기본적으로 Git 제외 대상이지만, 이 알파 기능에 필요한 코드·합성 자료·검증 파일만 명시적으로 추적한다. 해당 파일은 저장소를 받으면 함께 포함된다. 원자료·모델·개인 환경은 포함하지 않는다.
`generate.py`로 CSV와 독립 정답을 만들고 `seed.py --alpha`가 CSV를 검증해 모델에 넣는다.

```powershell
# 저장소 루트에서 데이터 생성·재현 검사
.venv/Scripts/python.exe local_workspace/scripts/alpha_test/generate.py
.venv/Scripts/python.exe local_workspace/scripts/alpha_test/generate.py --check

# backend/에서, 위와 같은 별도 로컬 fixture DB만 사용
../.venv/Scripts/python.exe -m spending_fixture.seed --alpha
../.venv/Scripts/python.exe -m spending_fixture.seed --alpha --apply
../.venv/Scripts/python.exe -m spending_fixture.set_login --alpha

# FIXTURE_LOGIN_PASSWORD 환경변수를 기존 방식으로 설정한 뒤 로그인 설정 명령을 실행한다.
# PowerShell에서 빈 환경변수 처리 차이를 피하려고 today를 사용한다.
$env:CHAT_TODAY = 'today'
../.venv/Scripts/python.exe manage.py runserver 127.0.0.1:8001 --settings=spending_fixture.service_settings
```

`--alpha --apply`는 알파 두 계정만 트랜잭션 안에서 재생성한다. 기존 A/B/C는 건드리지 않는다.
재생성 후 로그인 비밀번호를 다시 설정해야 한다. 다른 회원의 이메일·UUID 충돌 시 변경하지 않고 멈춘다.
알파 모드에는 `--reset`을 제공하지 않는다. 기본 모드의 초기화·해시·기준일은 유지한다.

현재 KST **날짜** 이하의 합성 전표만 적재한다. 거래 시각은 생활 패턴용 가상 시각이며 당일 전표는 포함한다.
따라서 11/3에 실행하면 11/4~5 행은 들어가지 않는다. 11/5에 `--apply` 후 비밀번호를 다시 설정하면
11/5 정답 세트가 된다. 11/5 이후의 새 거래는 생성하지 않으므로 이후 조회는 최신 자료가 아니다.

승인 계정은 7~11월 승인 예산·고정지출·납부 의무·저축·쿠션·배분을 포함한다.
온보딩 계정은 계좌·카드·거래·청구서·합성 조회 동의 및 조회 이력만 있고, 프로필·예산·고정지출 설정은 없다.
두 계정의 금융 자료가 같아도 온보딩 계정은 고정비 설정 전이라 생활비 범위 정답은 승인 계정 기준이다.
가맹점 메타데이터는 거래의 `source_detail`에 보관하고, `ans_category_if_user_set`은 CSV의 외부 정답으로만 둔다.

알려진 서비스 제약: 적금 C는 실제 유형을 `deposit`으로 유지하므로 현재 소비 분석 수집 판정이
`not_demand_account`로 부분 수집을 표시한다. 조회된 소비 합계와 확정 수집 상태를 구별한다.
현재 `home_data`의 `budget` 칸은 아직 `None`이다. 승인 예산 적재만으로 홈 기능이 구현되지는 않으며,
예산 확인은 `/api/budgets/`와 실제 모델로 검증한다. 첨부 기준은 `budgets/scope.py`에 추가했지만,
현재 `overview`·`spending_analysis.tools`의 예산 밖 사용액 연결은 별도 팀 코드 반영이 필요하다.

알파 테스트:

```powershell
../.venv/Scripts/python.exe manage.py test ../local_workspace/tests -p test_alpha_fixture.py --settings=spending_fixture.settings --keepdb --noinput
```

## 기본 적재 건수

category 12(기존 시드) · app_user 3 · financial_profile 3 · category_priority 9 · category_minimum 5 · connection 6 ·
bank_account 3 · card 3 · budget_cycle 11 · budget 12 · budget_item 174 · budget_category_setting 2 · recurring_expense 9 ·
payment_due 30 · financial_transaction 414 · payment_match 30 · transaction_link 1 · sync_run 19 · spending_allocation 0

## 사람별 핵심 사례

- A: 10월 식비 390,000 ↔ 현재 급여 주기(10/25~) 식비 60,000. 승인 v1 식비 350,000, 검수만 된 v2 식비 450,000.
- B: 같은 구매 180,000(카드 + 계좌) 한 번만, 부분 취소 후 120,000, 전액 취소 0. 현재 주기 생활비 1,362,000(62,000 초과).
- C: 10/20 첫 연결, 카드 10/25~29 조회 실패. 확인된 소비 390,000(미분류 50,000 포함). 예산은 미승인 초안뿐. 7~9월은 기록 없음(0원 아님).

## 테스트가 확인하는 것 / 확인하지 않는 것

- 확인: fixture 관계(건수 · 사용자 간 FK · 고정비 연결 · 수집 기록 · 반복 실행 · 초기화 범위) /
  `listed_transactions` · `transaction_item` · `spending_summary` · `cycle_dates` · `budget_data`(계산식) 결과와 05 정답 / 작은 손계산 자료.
- 기존 동작 기록: `budget_data` 는 validated 를 먼저 고른다 → expectedFailure 1건(고치지 않음). 소비 분석은 `spending_analysis.budget.approved_budget` 을 쓴다.
- `test_analysis.py`: `spending_analysis` 의 승인 예산 읽기 전용 조회(`approved_budget`)와 분석 기간 순수 함수.
  변형은 테스트 안에서만 만든다 — B 승인 예산 교체(`seed.variant_b_approved_v2`), 날짜 경계용 별도 사용자. 기본 414건은 그대로.
- `test_coverage.py`: 수집 완전성 판정(`spending_analysis.coverage`). 변형은 별도 사용자로 테스트 안에서만 만든다.
- `test_analyze.py`: 소비 분석 1~9번 내부 결과(`spending_analysis.analyze`)를 05 정답 · 고정 상수와 비교.
- `test_conversation.py`: 가짜 LLM 추출 → router_core.route → adapter.run(실제 analyze) → answer.compose · adapter.record 흐름.
- 없음: chat API · replies.py · 대화 상태 DB 연결, 비율 반올림 · 음수 잔액 표시 · 부분 수집 안내 계약, 나머지 변형 시나리오(C 승인 추가 · 0원 비교 · 동률 1위).
  계약 · 남은 정책은 `chat/router_core/MEMO.md` 6~10번.

## 개발용 로컬 채팅 (`local_chat.py`)

실제 서비스와 분리된 확인용 화면. fixture DB 만 읽고(시작 때 로컬 fixture DB · 회원 적재 검사), 대화는 페르소나별 메모리 저장소(서버 재시작 · 새 대화면 초기화).
예산 조정 · 청년 정책은 대역. "왜 늘었어?"는 서버가 계산한 거래 근거로 답한다 — 가짜 해석 모드는 템플릿 문장,
실제 LLM 모드는 설명 LLM 문장(검사 통과분만, 숫자는 서버 값, 같은 상한 · 장부에서 1회 더).
지난달 비교는 카테고리별 묶음, 전반 분석 아래 "카테고리별 소비 보기"(서버 금액 · 비율 막대), 예산 초과 뒤 [예산 조정하기](기능 버튼과 같은 경로).
chat API · replies.py · config · 모델 변경 없음, 추가 의존성 없음.

```bash
python -m spending_fixture.local_chat            # 가짜 해석(정해 둔 문장만) → http://127.0.0.1:8765
LOCAL_CHAT_LIVE=1 python -m spending_fixture.local_chat --live --max-calls 30 --ledger <저장소 밖>/local-chat-ledger.json
python manage.py test spending_fixture.test_local_chat --settings=spending_fixture.settings
```

실제 LLM 은 세 가지(환경변수 · --live · 상한 + 장부)가 모두 있을 때만 켜진다. 평가 도구의 역할별 호출 어댑터 · 장부를 그대로 쓰고 재시도 없음.
키 · 요청 · 응답 원문은 화면 · 콘솔에 내보내지 않는다.

## B 보완 1~3단계 확인 (2026-11-02)

이번 변경은 요청 조건 보존, 질문과 근거의 일치, 평가 기준 보완이다. 예산 설계·조정 도구를 추가하거나 답변을 자유 서술로 바꾼 단계는 아니다.

| 사례 | 기존 문제 | 변경 후 확인할 결과 |
|---|---|---|
| 지난달·지난주 식비 설명 | 설명 경로가 기간을 이번 달로 바꿀 수 있었음 | 기준일 2026-10-29에서 각각 9/1~30, 10/19~25를 조회 |
| 주말·카드·고정지출 조건 | 새 비교를 만들 때 일부 조건이 사라짐 | 현재·이전 기간에 같은 필터 적용 |
| 커피 구매 횟수 질문 | 계획에 있던 커피 대상을 버리고 식비 전체 건수로 판단 | 커피 횟수 근거가 없으면 확인 불가, 답 LLM 호출 생략 |
| 점심값 상승 질문 | 식비 전체 평균을 점심 가격 상승으로 해석 | 점심 평균 근거가 없으면 확인 불가. 전체 평균도 상품 가격으로 단정하지 않음 |
| 근거 ID 선언만 하고 문장에는 생략 | 실제로 인용하지 않은 근거가 검사 통과 | 실제 자리표시와 선언 ID가 일치해야 통과 |
| 설명 후 “그럼 교통비는?” | 처음 설명에서 조회한 조건이 후속 대화에 남지 않음 | 조회 조건을 기록해 기간·비교 조건 유지 |

숫자·방향·단위는 서버가 만든 완성 문장에 둔다. B와 기존 설명 경로가 같은 문장 검사를 사용한다.
`네/아니요`·완곡한 부정 예외, 부분 문자열 대상 매칭, 평가 전용 키워드 추측을 제거했다.
해석·계획·B 답·기존 설명의 프롬프트 합계는 9,485자 → 7,007자(약 26% 감소, 토큰 수가 아닌 문자 수; 아래 실제 호출 보완 포함).
금액 계산과 수집 완전성 검사는 유지한다.

### 자동 확인 — 저장소 루트에서

로컬 PostgreSQL이 켜져 있어야 한다. 테스트 전용 DB를 생성·정리하며 외부 LLM은 호출하지 않는다.

```bash
.venv/bin/python backend/manage.py test spending_fixture spending_analysis chat.test_engine chat.test_llm --settings=spending_fixture.settings --verbosity 1 --noinput
PYTHONPATH=backend:scripts/router_eval .venv/bin/python -m unittest discover -s scripts/router_eval -p 'test_*.py'
```

- 새 회귀: `spending_analysis/test_evidence_contract.py`, `spending_fixture/test_explanation_scope.py`.
- 실제 서비스 API 연결·후속 대화: `chat/test_engine.py`.
- 잘못된 대상의 근거를 성공으로 세지 않는 평가: `scripts/router_eval/test_explanation_contract.py`.
- 기존 `budget_data`의 validated/approved 선택 문제 1건은 expected failure로 남아 있다.

이번 실행 결과: 서비스·소비 분석 328건 중 327건 통과 + 기존 예상 실패 1건, 평가 도구 37건 모두 통과.

### 화면 확인 — 기존 더미 회원으로 로그인

더미 DB 적재와 로그인 설정이 이미 끝났다는 전제다. 기존 DB를 다시 적재하거나 비밀번호를 바꿀 필요는 없다.
백엔드 실행 터미널(저장소 루트):

```bash
FIXTURE_LIVE_LLM=0 CHAT_LLM=fake CHAT_EXPLAIN_PLAN=on .venv/bin/python backend/manage.py runserver 127.0.0.1:8001 --settings=spending_fixture.service_settings
```

다른 터미널(`frontend/`):

```bash
npm run dev -- --config vite.fixture.config.js
```

`http://localhost:5174`에서 A(윤서)로 로그인하고 다음 문장을 그대로 입력한다. 각 번호는 새 대화에서 시작한다.

1. `지난달 식비는 왜 많이 나왔어?` → `그럼 교통비는?`: 두 응답 모두 지난달 범위를 유지한다.
2. `지난주 식비는 왜 많이 나왔어?`: 10월 19일~25일 범위를 표시한다.
3. `왜 식비가 늘었어?` → `커피를 자주 사 먹어서 그래?`: 커피 비교 근거 부족을 안내한다. 식비 전체 건수로 긍정·부정하지 않는다.
4. `왜 식비가 늘었어?` → `점심값이 비싸진 거야?`: 점심 대상의 근거 부족을 안내한다. 식비 전체 평균으로 점심 가격 상승을 단정하지 않는다.
5. `이번 달 주말 식비 왜 늘었어?`: 주말 조건이 조회와 표시 범위에 남는지 확인한다.

`fake`는 위 고정 문장을 해석하는 대역이다. 실제 조회·서버 계산·검사·API 연결은 확인할 수 있지만, 실제 LLM의 자연어 이해나 문장 연결 품질을 검증하는 모드는 아니다.

### 검증 결과의 한계

기존 실제 LLM 응답 15사례를 외부 호출 없이 재생해 커피·점심 질문의 잘못된 전체 통계 대입을 차단하는 것을 확인했다.
새 기준에서 적용 가능한 답 13건 중 6건만 통과했다. 기존 답에 남은 긍정·부정 표현 등을 탈락시키는 결과이며, 새 프롬프트의 실제 LLM 성공률을 뜻하지 않는다.
1~3단계 완료 당시 실제 LLM 재평가는 자동 승인 검토에서 명시적 전송 승인 부족으로 차단됐다. 이후 사용자가 최대 30회를 승인하여 아래처럼 실행했다.
확인 불가 답을 늘리는 것만으로 서비스 가치가 완성되지는 않으며,
다음 단계에서는 필요한 세부 근거를 확보하는 도구와 예산 설계·조정으로 이어지는 행동을 연결해야 한다.

## 사용자 승인 후 실제 LLM 30회 검증 (2026-11-02)

동일 호출 장부의 상한 30회를 지켰고 추가 호출은 하지 않았다. 기존 환경의 모델·API를 사용했으며 키는 기록하지 않았다.
원본 결과는 `/private/tmp/spending-steps123-approved30/`에 저장했다. 결과를 보고 사례 기대값을 변경하지 않았다.

| 구간 | 실제 호출 | 결과 |
|---|---:|---|
| 기존 수정본 B 15사례 | 19회 | 2사례는 서버가 먼저 멈춤. 나머지 13사례의 엄격한 자동 답 채점은 1/13 |
| 소비 요청 해석 7사례 | 7회 | 기대 필드 일치 5/7 |
| 답 프롬프트 수정안 2사례 | 3회 | 1사례는 계획 대상 오류로 확인 불가, 1사례는 계획·답 모두 통과 |
| 저장된 정상 계획 + 수정안 답만 검증 | 1회 | 평균 결제액 설명 통과. 전체 흐름 재실행 성공으로 세지 않음 |

처음 B 13사례에서 호출 실패 4건, 답변 형식 거절 3건, 근거 부족 안내 6건이었다.
호출 실패는 약 9초의 계획 한도·12초의 B 한도 경계에서 발생했다. 기존 기록은 `ResponseError`까지만 남겨 상세 네트워크 원인은 확정하지 못한다.
B 구간(첫 DB 조회·입력 해석 제외) 중앙값 6.3초, 최대 12.1초였다.

자동 답 점수 1/13을 곧바로 “나머지는 모두 잘못된 답”으로 해석하지 않는다.
예를 들어 커피·점심 질문은 근거 부족을 안전하게 안내했지만, 계획 subject가 각각 `커피 결제 건수`·`점심값`으로 나와 고정 기대값 `커피`·`점심`과 불일치했다.
그럼에도 자연스러운 분석 답변이 안정적으로 생성되지 않았다는 결론은 분명하다. 검사에 막혀 서버 설명으로 대체되거나 확인 불가만 답하는 비율이 높다.

발견한 문제와 이번 반영 범위:

- 답 프롬프트: LLM이 `{C3}` 대신 `건당 평균은 올랐어요.`처럼 직접 문장을 작성해 거절됐다.
  **ID 하나당 자리표시 하나를 선택하는 역할**로 명시하고 연결어 생성 요구를 제거했다. 실제 답 생성 2건에서 형식·근거 검사를 통과했으며 서비스 코드에 반영했다.
  답 프롬프트 438→458자, 계획+답 해시 `daf102b3`→`b108f2a9`. 이 두 건만으로 전체 성공률 개선을 주장하지 않는다.
- 해석: 지난달·지난주·주말 필터·말끝 흐린 배달 가설·교통비 후속 질문은 통과했다. 커피 가설은 세부 조회 대상으로, 점심 가설은 식비 카테고리로 추출했다.
  이번에 해석 프롬프트를 추가 수정하지 않았다. 올바른 가설 분리의 회귀 사례로 남긴다.
- 계획: `weekday` 대상에 `주말` 대신 `패션·쇼핑`, `average` 대상에 `식비` 대신 `건당 금액`을 넣었다.
  자유 문자열 대상의 표현 차이·지표 혼동 때문에 쓸 수 있는 근거도 확인 불가가 된다. 사례별 단어 예외를 더하지 않았으며 구조적인 대상 구분은 후속 과제다.
- 평가: 계획 호출 실패를 “조회 0개 계획 성공”으로 세던 오류를 고쳤다. 서버가 버린 조회도 계획 실패로 센다.
  저장 응답을 재채점한 계획 결과는 **11/13**이다(이전 채점 13/13). 이를 확인하는 테스트 2건을 추가했고 평가 도구 테스트 39건이 통과했다.

수정안의 실제 출력 예:

> 건당 평균은 12,000원에서 15,000원으로 올랐어요.
> 결제 한 건당 평균의 변화예요. 구매한 품목이나 수량을 알 수 없어 상품 가격이 올랐는지는 판단할 수 없어요.

이 예는 저장된 정상 계획을 재사용한 답 생성 검사다. 계획부터 다시 호출한 동일 질문에서는 `건당 금액`을 대상으로 해석해 확인 불가가 됐다.
따라서 다음 우선순위는 **가설의 지표와 대상을 안정적으로 구분하기 → 질문과 관계없는 추가 조회 줄이기 → 시간 제한과 답변 표현 재검증**이다.

## 최신: 대상·지표 분리와 필요한 추가 조회 (1~2단계)

이 절이 위의 과거 검증 기록보다 우선한다. 점심 평균은 이제 조회할 수 있으므로, 예전의 “점심 질문은 확인 불가”가 현재 기대 결과는 아니다.

### 무엇이 달라졌나

- 해석과 계획이 같은 `hypothesis` 계약을 사용한다. `metric`(금액·건수·건당 평균·하루 평균)과 `target`(현재 범위·시간대·요일·카테고리·온라인 묶음·방문 구분)을 분리한다.
  대상은 서버 목록의 코드다. `unavailable`은 자료에서 대상 자체를 식별할 수 없을 때만 쓰며, 관측 대상 코드와 동시에 지정할 수 없다.
- 입력 해석이 읽은 가설을 계획에 전달한다. 계획이 같은 질문을 다른 대상으로 다시 해석해 덮지 않는다.
- 동일 카테고리의 금액을 원인 가설로 반복하면 일반 설명으로 정리한다. 금액·횟수·평균 비교는 원래 기간·결제수단·요일·시간대 필터 안에서만 한다.
- 첫 근거가 충분하면 추가 조회 0개다. 필요한 대상·지표가 없으면 그 대상만 비교한다. 임의의 배달·전체 시간대 조회로 대신하지 않는다.
- 일반 증가액 설명에서는 요일 하루 평균을 기여 금액으로 섞는 조회를 생략한다. 요일을 물은 가설은 해당 요일의 지표를 비교할 수 있다.
- 시간대·요일·가게·카테고리별 금액과 양수 순결제 건수를 서버에서 집계하고, 건당 평균은 금액÷건수로 계산한다.
  전액 취소는 건수에서 제외한다. 빈 기간의 평균은 0원으로 대체하지 않는다. 시간·가게 이름 누락과 조회 범위 밖 대상도 따로 안내한다.
- 상품 가격은 평균 결제액과 다르고, 결제 건수는 방문·약속 횟수와 다르다는 한계를 함께 안내한다.
- 예산 자동 조정·자유 서술 확대는 이번 범위에 포함하지 않았다.

### 바로 확인하기

저장소 루트에서 다음 명령은 **외부 호출 없이 실제 서비스 API**를 검사하고 사람이 읽는 HTML을 만든다.
기존 더미 DB·회원·비밀번호를 변경하지 않고, 테스트 전용 DB를 생성·정리한다. 다른 DB 테스트와 동시에 실행하지 않는다.

```bash
.venv/bin/python scripts/router_eval/verify_service.py --output-dir /private/tmp/spending-service-check
```

`/private/tmp/spending-service-check/report.html`을 브라우저로 열면 11개 시나리오의 질문·실제 답·통과 여부·분석 과정을 볼 수 있다.
외부 LLM 대신 고정 해석을 사용하므로 실제 모델의 자연어 이해 성능을 뜻하지 않는다.

화면에서 직접 확인하려면 기존 더미 A(윤서) 계정으로 로그인한다. 백엔드와 프론트를 아래처럼 켠다.

```bash
# 저장소 루트
FIXTURE_LIVE_LLM=0 CHAT_LLM=fake CHAT_EXPLAIN_PLAN=on .venv/bin/python backend/manage.py runserver 127.0.0.1:8001 --settings=spending_fixture.service_settings
# 별도 터미널, frontend/에서
npm run dev -- --config vite.fixture.config.js
```

`http://localhost:5174`의 챗봇 하단 **개발용 · 가짜 해석 예시 문장**에서 버튼으로 질문할 수 있다.
각 확인은 “새 대화”부터 시작한다. 2~6번은 먼저 `지난달보다 식비 더 썼어?`를 입력한다.

| 확인 | 다음 질문 | 기대 결과 |
|---|---|---|
| 1. 기간 유지 | `지난달 식비는 왜 많이 나왔어?` → `그럼 교통비는?` | 두 결과 모두 지난달(9/1~30) |
| 2. 점심 평균 | `점심값이 비싸진 거야?` | 점심 건당 평균 12,000→15,000원, 상품 가격 단정 금지, 대상 조회 1개 |
| 3. 점심 건수 | `점심을 더 자주 먹어서 그래?` | 점심 결제 건수 비교, 식비 전체 건수로 대체하지 않음 |
| 4. 기존 근거 | `건당 금액이 올라서야?` | 식비 건당 평균 비교, 추가 조회 0개 |
| 5. 커피 | `커피를 자주 사 먹어서 그래?` | 커피 품목 식별 불가, 무관한 조회 0개 |
| 6. 배달 | `배달 때문이야?` | 확인한 배달앱 이름 범위에서 두 기간 모두 결제 없음. 배달 전체 부정 금지 |

“분석 과정”에서 대상 코드·지표·실행/생략 이유·근거 상태를 확인할 수 있다. 확인 불가 결과는 “B 답”과 구분해 표시한다.

### 검증 명령

```bash
.venv/bin/python backend/manage.py test spending_fixture spending_analysis chat.test_engine chat.test_llm --settings=spending_fixture.settings --verbosity 1 --noinput
PYTHONPATH=backend:scripts/router_eval .venv/bin/python -m unittest discover -s scripts/router_eval -p 'test_*.py'
# frontend/에서
npm run lint
npm run build -- --config vite.fixture.config.js
```

새 테스트 `spending_fixture/test_investigation.py`는 작은 거래 표의 손계산 정답, 원래 범위 유지, 결제 없음/시각 누락, 불필요한 조회 생략, 입력 가설 우선권,
LLM에 금액·실제 거래 가게 이름을 보내지 않는 것을 확인한다. 과거 원문 재생용 `{axis, subject}` 읽기 호환은 남겼지만 실제 LLM 출력 스키마는 코드 기반 계약만 사용한다.

### 승인된 추가 40회 검증 기록

원본은 `/private/tmp/spending-target-plan-live40/`에 보관한다. 최초 B 15사례(22호출), 입력 해석 7사례(7호출), B 실패 사례 재검증(8호출),
입력 해석 재검증 2사례(2호출), 별도 표현 “저녁에 결제를 더 자주 해서 그런가?”(1호출)로 총 40회다. 자동 재시도·상한 초과 호출은 없다.

- 최초 B: 검사 대상 13건 중 9건 통과, 호출 실패·답 형식 거절 없음. B 구간 중앙 5.4초, 최대 8.8초(입력 해석·첫 DB 조회 제외).
- 보완 후 실패했던 B 4건 중 3건 통과. 남은 “저녁 약속이 많아서?”는 기대 지표가 금액이었는데 모델은 저녁 결제 건수를 선택했다.
  대상은 저녁으로 맞았고 실제 원인 단정도 하지 않았지만, 자동 평가의 지표 기준은 통과하지 못했다. 기대값은 결과에 맞춰 바꾸지 않았다.
- 입력 해석은 최초 5/7. 커피 빈도를 금액으로, 배달을 식비로 넓힌 두 사례를 발견했다. 지표 의미와 기존 온라인 별칭 정보를 제공한 후 해당 2건 모두 통과했다.
  별도 저녁 결제 빈도 표현 1건도 `slot:evening`+`count`로 통과했다.
- 위 결과는 **최초 전체 실행 + 실패 사례 재검증**이다. 최종 프롬프트로 모든 사례를 다시 실행한 성공률로 표시하지 않는다.
- 마지막으로 일반 질문의 불필요한 요일 조회를 서버에서 생략하고 건수의 한계를 명시했다. 이 변경은 추가 외부 호출 없이 테스트와 저장 응답 재생으로 확인한다.

최종 확인: 소비 분석·서비스 회귀 342건(341건 통과 + 기존 예상 실패 1건), 평가 도구 39건 통과, 서비스 API 보고서 11/11 통과.
프론트 lint는 기존 MoneyInput.jsx 경고 1건 외 오류 없음, fixture 빌드 통과. 마지막 서버 변경 뒤 저장 응답 재생은 12/13이며,
일반 설명 e01/e02/e12의 미사용 추가 조회는 모두 0개다. 재생의 12/13과 매우 짧은 처리 시간은 새 실제 LLM 실행의 정확도·지연으로 해석하지 않는다.


## 사용자 질문 목록 재검증 (2026-11-02)

`verify_checklist.py`는 사용자 목록 41개와 당시 막혔던 표현·범위 유지 회귀 5개를 실제 로그인/채팅 API로 확인한다.
A=윤서, B=민재, C=지우. 기준일은 2026-10-29이고 각 사례는 새 대화로 시작하며 화살표 뒤 질문만 같은 대화를 쓴다.
기간 선택·동의·오래된 선택지·담당 전환도 API로 검사한다. 사용자 화면의 카드(`view`)·표·안내를 함께 검사하며 개발 추적에 있는 값은 정답 검사에서 제외한다.

```bash
# 외부 호출 없는 검사: 질문 해석을 고정하고 서비스 조회·계산·맥락·버튼을 검증
.venv/bin/python scripts/router_eval/verify_checklist.py --output-dir /private/tmp/spending-checklist-offline
# 실제 LLM: 실행 전에 호출 한도를 승인받고, 재실행도 같은 장부를 사용한다
# .venv/bin/python scripts/router_eval/verify_checklist.py --live --max-calls <승인 횟수> --ledger <기존 장부 경로> --output-dir <보고서 경로>
# 실패 항목만 재검증할 때: --ids 0-1 0-2
```

오프라인 통과는 자유로운 문장을 LLM이 이해한다는 뜻이 아니다. 실제 LLM 결과는 별도 보고서로 남긴다.
이 목록은 ‘B 꺼짐’ 기준이므로 실제 모드에서도 해석만 LLM이 맡고 설명은 서버 템플릿으로 생성한다.
기존 `verify_service.py`의 11개 B 시나리오와 `test_investigation.py`는 별도로 유지한다.

수정한 동작:
- 배달처럼 등록된 서비스 묶음은 거래가 없어도 조회한다. 자료가 완전할 때만 0원/0건, 자료가 없으면 확인 불가로 답한다.
- 지난달 비교 뒤 새 합계·건수·평균 조회를 하면 묵시적으로 이어진 비교를 해제한다. 카테고리·요일 범위는 유지한다.
  ‘그럼 교통비는?’처럼 범위만 바꾸거나 ‘왜 늘었어?’처럼 설명을 요청하면 비교를 유지한다. 직접 지정한 기간·비교도 우선한다.
- AI 호출 상한은 ‘이해하지 못했어요’와 구별해서 안내한다. 대화 상태는 보존하며 상한을 자동으로 늘리거나 장부를 초기화하지 않는다.

수동 확인은 각 행마다 새 대화에서 시작한다. 이미 실패한 대화의 버튼 대신 새로 표시된 버튼을 사용한다.
서버를 재시작해 변경된 코드를 적용하고, 실제 모드의 호출 한도와 장부 잔여량을 먼저 확인한다.


검증 결과 (실제 API 호출 합계 140회, 초과 0회):
- 최초 46개 중 42개 통과(103호출). 생활비를 unknown 카테고리로 해석, 지난달 비교를 단독 기간과 중복,
  외부 호출 실패 1회, 후속 배달 질문의 지난달 범위 유실을 발견했다.
- 해석 지침 보완 후 실패 항목+관련 회귀 12개 중 11개 통과(31호출). 최초 실패 4개는 통과했지만
  `지난달 비교해줘`가 지난달 단독 조회로 해석되는 변동이 추가로 발견됐다.
- 비교 동사의 대상 달 의미를 명시하고 `지난달 비교해줘`, `생활비 얼마 남았어?`, `지난달보다 많이 썼어?`를
  다시 검증해 3/3 통과(6호출). 각 사례의 마지막 결과는 46개 모두 기대와 맞는다.
  **최종 프롬프트로 전체 46개를 다시 실행한 성공률은 아니다.** 입력을 고정한 테스트만으로 실제 이해 검증을 대신하지 않았다.
- 전체 자동 회귀 349건(348 통과 + 기존 예상 실패 1), 라우터 회귀 63건, 평가 도구 39건 통과.
  실제 LLM 추가 호출은 중단했고 기존/이번 호출 장부는 초기화하지 않았다.

실행별 원본: `/private/tmp/spending-checklist-live140/{first,retest,final}/results.json`.
종합 보고서: `/Users/naeun/.codex/visualizations/2026/11/02/01a199dd-2232-7333-adf1-19ab7ce7cf99/spending-checklist-review/report.html`.
보고서는 실제 서비스 응답을 표시하며 브라우저 전체 클릭 테스트나 모든 표현의 정확도 보장으로 해석하지 않는다.

## 전체 검증 기록 (2026-11-03, B 수정 없음)

사용자 승인 상한을 250회에서 500회로 늘린 뒤 공용 장부로 493회 실행했다. 자동 재시도·호출 오류·커밋은 0회다.
실행 전 지시문·사례·기대값을 고정했고, A 회귀 원인만 한 차례 수정해 영향 사례만 재검증했다. 기대값은 변경하지 않았다.

| 검사 | 최초 고정본 R0 | 수정본 R1 영향 사례 |
|---|---|---|
| 질문 목록 | 44/46 | 8/8 |
| 기존 역할별 해석 | 116/154, 처리 결정 137/154 (총 158개 중 정책 미정 4개 별도) | 13/17, 처리 결정 16/17 |
| 대상·지표 해석 | 6/8 | B 관련 실패는 수정·재검증 안 함 |
| B 서비스 연결 | 11/15 | 수정·재실행 안 함 |

기존 해석은 단일 102개 + 연속 대화 20개(56턴) 전체를 실행했다. 구 통합/과거 frozen 사례의 입력도 현행 목록에 모두 포함된다.
수정 후 전체 재실행 성공률이 아니다. R1에서 확인한 해석 실패 4개와 R0 뒤 미재검증 실패 24개를 구분해 남겼다.
B에서는 `밥값이 올라서야?`를 평균 금액 대신 시간대 증가로 답했다. 나머지 B 기준 실패는 scope 직접 인용 2개, 확인 불가 대상의 지표 불일치 1개다.
실제 답변은 성공·실패 모두 원문 그대로 별도 보고서에 보존했다.

- 백엔드 전체: 1,069건, 1,066 통과 + 건너뜀 2 + 기존 예상 실패 1. 수정 영향 테스트 144/144.
- `verify_service.py`: 11/11 (고정 해석 + 실제 API). 평가 도구: 38/39 → 평가 문장 유출 제거 후 39/39.
- 프론트 lint: 오류 없음, 기존 MoneyInput.jsx 경고 1. 기본/fixture 빌드 모두 통과.
- 건너뜀: 모델 파일 없는 실제 분류 모델 검사, 명시 실행이 필요한 외부 CODEF 샌드박스.
- 예상 실패: 기존 budget_data의 validated/approved 예산 선택. 이번 범위에서 수정하지 않음.

검증 도구: `scripts/router_eval/final_audit.py`, 보고서 생성: `scripts/router_eval/report_final_audit.py`.
원본·고정 스냅샷·장부: `/private/tmp/spending-final-audit500/`.
R0 계약 SHA-256: `1af92e4534683fd494592402ed840b9935fca76d70a6362db7d29c1c3ccc95e1`.
R1 계약 SHA-256: `beccd613349a16418130b6f3d702059ab0a39e7c9f4b077346b7225b4de901e3`.

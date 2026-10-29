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

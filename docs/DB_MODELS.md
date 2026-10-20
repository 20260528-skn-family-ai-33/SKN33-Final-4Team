# DB 모델 구조 (Django 앱 배치 · 공통 규칙 · 확인 방법)

작성일: 2026-10-19 (10-20 추가) · 기준: ERD v2 (`ERDCloud_가져오기_v2.sql`), `FK_삭제규칙.md`, `ERD_v2_수정필요사항` 10번

## 1. 현재 상태 — 26개 테이블 모델 완료 ✅

| 항목 | 결과 |
|---|---|
| 테이블 | 26 / 26 |
| 칸(컬럼) | 345 = ERD 340 (타입·길이·NULL 허용까지 ERD SQL과 자동 대조해서 전부 일치) + 10-20 추가 5 |
| FK | 82 = ERD 79 (on_delete CASCADE 32 · RESTRICT 27 · SET_NULL 20, `FK_삭제규칙.md`와 일치) + 10-20 추가 3 (RESTRICT) |
| UNIQUE | ERD 24개 중 23개 + 조건부 UNIQUE 5개 |
| CHECK | 10번 표의 "한 줄 규칙" 반영 (아래 4번) |
| 검증 | PostgreSQL 16에서 `migrate` 성공 + 규칙 54개(10-19 39개 + 10-20 15개) 실제 데이터로 시험 → 전부 통과 |

**ERD와 다른 점 (일부러 바꾼 것)**

| 무엇 | 왜 |
|---|---|
| `category.display_order` UNIQUE 제거 | 순서 바꿀 때(1↔2) 에러 나서. 2026-10-19 결정 |
| `transaction_link` 자기 자신 연결 금지 CHECK 추가 | A→A 연결 방지 |
| `budget_item`·`spending_allocation` UNIQUE 이름 | ERD 이름이 길어서 잘려 있어 짧게 바꿈 (내용은 같음) |

**2026-10-20 추가 (화면 대조 결과 · ERD SQL에도 반영 필요)**

| 무엇 | 왜 |
|---|---|
| `financial_profile.priority_4` · `priority_5` · `priority_6` (FK → category, RESTRICT) | 내 정보 화면에서 1~3위 필수, **4~6위 선택**으로 받기로 함. DB는 순서대로 채웠는지(1·2·4위 금지)·중복 없는지만 막고, 1~3위 필수는 API에서 검사 |
| `financial_transaction.suggestion_candidates` (JSON) | 확인 필요 거래에 **후보 카테고리 여러 개**를 보여줌. `[{"category": "FOOD", "confidence": 0.41}, ...]` 확신도 높은 순. JSON 안 코드는 FK 검사가 안 되므로 저장할 때 category에 있는지 확인 |
| `financial_transaction.suggestion_reason` (VARCHAR 300) | "카드 결제대행사 이름만 있어 가게를 특정하지 못했어요." 같은 **확인 필요 이유** |
| 인덱스 `ft_user_merchant_idx` (user, merchant_name, occurred_on 역순) | "같은 가맹점 분류에 반영" 기능. 별도 테이블 없이 `finance/merchant_rules.py`의 `remembered_category()`가 같은 회원·가맹점의 **사용자가 확정한 최근 거래**를 찾음. 느려지거나 규칙 관리가 필요해지면 `merchant_rule` 테이블 추가 |

## 2. 테이블 → 앱 배치

| 앱 | 테이블 (ERD 이름) | 모델 클래스 |
|---|---|---|
| `users` | app_user, financial_profile, consent | User, FinancialProfile, Consent |
| `finance` | connection, bank_account, card, loan, sync_run | Connection, BankAccount, Card, Loan, SyncRun |
| `finance` | financial_transaction, transaction_link | FinancialTransaction, TransactionLink |
| `finance` | card_bill, card_bill_item, recurring_expense, payment_due, payment_match | CardBill, CardBillItem, RecurringExpense, PaymentDue, PaymentMatch |
| `budgets` | category, budget_cycle, budget, budget_item, budget_category_setting | Category, BudgetCycle, Budget, BudgetItem, BudgetCategorySetting |
| `budgets` | spending_allocation, reserve_plan, cushion_entry | SpendingAllocation, ReservePlan, CushionEntry |
| `chat` | conversation, chat_message, async_task | Conversation, ChatMessage, AsyncTask |

합계: users 3 + finance 12 + budgets 8 + chat 3 = **26개**

## 3. 도커 PostgreSQL에서 확인하기

```bash
# 1) (전에 migrate 한 적 있으면) DB 비우기 — LOCAL_SETUP.md ⚠️ 박스 참고
docker exec money-cushion-db psql -U money_cushion -d money_cushion -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

# 2) 테이블 만들기
cd backend
python manage.py migrate
python manage.py createsuperuser      # 이메일로 만든다

# 3) 테이블 목록 보기 (ERD 26개 + Django 기본 9개 = 35개)
docker exec money-cushion-db psql -U money_cushion -d money_cushion -c "\dt"

# 4) 한 테이블 구조 보기 (칸·제약·FK)
docker exec money-cushion-db psql -U money_cushion -d money_cushion -c "\d payment_due"
```

화면으로 보고 싶으면 `python manage.py runserver` → `http://127.0.0.1:8000/admin` 에 26개 테이블이 다 등록돼 있다.

## 4. DB가 막아 주는 규칙 (CHECK · 조건부 UNIQUE)

| 테이블 | 막는 것 |
|---|---|
| financial_profile | 급여일 1~31 밖, 최소 생활비 음수, 우선순위 건너뛰기(1·2·4위), 같은 카테고리를 두 순위에 넣기 |
| connection | 같은 회원·업권·기관에서 '연결 중'이 2개 (해제 후 재연결은 OK) |
| card, loan | origin이 provider/manual 아님, 대출 status가 repaying/repaid 아님 |
| sync_run | 조회 대상(계좌·카드·대출) 2개 이상 |
| financial_transaction | source_kind와 원천 FK 짝 안 맞음, 금액 음수, 취소액이 0~원거래 금액 밖, 신뢰도 0~1 밖 |
| transaction_link | 자기 자신과 연결, relation_type 오타 |
| card_bill_item | item_type 오타, 할부 회차 0 이하, 이번 회차 > 총회차 |
| payment_due | 의무 종류와 원천 FK 짝 안 맞음, active인데 취소 시각 있음 / canceled인데 없음, payment_state 오타, 납부액 음수 |
| payment_match | 매칭 금액 0 이하 |
| budget_cycle | 시작일 ≥ 종료일 |
| budget | 상태 오타, **주기마다 approved 2개 이상**, 자기 자신을 이전 예산으로 |
| budget_item · spending_allocation | 빈칸(NULL) 포함 중복 줄 (NULLS NOT DISTINCT), reserve인데 준비금 계획 없음 |
| cushion_entry | 초기액 2번, 배분 1건에 사용 2번, 종류별 부호(initial ≥0, use <0, refund >0, 적립·정정 ≠0), 종류별 필수 연결 |

**DB가 못 막는 것 (서비스 코드에서 꼭 검사)** — 같은 회원 데이터끼리만 연결, 지출 배분의 주기 = 예산의 주기, 여러 줄 합계(예산 항목 합 ≤ 배분 자금, 매칭 합 ≤ 거래 금액), approved 교체·쿠션 증감은 트랜잭션+잠금, A→B·B→A 역방향 중복 연결, 재연결 시 기존 자산·청구서 재사용.

## 5. 코드(값 목록) 미정 칸 — 팀이 정하면 choices 추가

지금은 글자 칸으로만 만들어 둠. 값을 정해도 **DB 구조는 안 바뀐다** (선택지만 추가).

| 테이블.칸 | ERD 설명 |
|---|---|
| connection.status | 기관 연결 상태 |
| sync_run.status | 성공 · 부분완료 · 실패 |
| financial_transaction.direction | 입금 · 출금 |
| financial_transaction.transaction_type | 소비 · 이체 · 카드납부 · 저축 등 |
| *.confirmation_state (거래·거래연결·청구서·납부의무·준비금) | 확인 상태 |
| card_bill.scope_type / source_type | 카드사 전체·단일 카드 / 조회·직접 입력 |
| recurring_expense.status / source_type | 확정 · 중지 · 무시 / 사용자 등록·반복 탐지 |
| payment_due.source_type | 조회 · 직접 입력 · 추정 |
| budget_cycle.status | 주기 진행 상태 |
| async_task.task_type / status | 작업 종류 / 대기 · 실행 · 완료 · 실패 · 취소 |
| chat_message.role / content_type | 발화자 / 텍스트 · 결과 카드 |

정해진 코드(loan.status, budget.status, payment_state, obligation_state, entry_type, item_type, bucket, funding_bucket, source_kind, obligation_type, relation_type, origin, conversation.status, app_user.status)는 이미 choices로 들어가 있다.

## 6. 모델 작성 공통 규칙 (새로 추가·수정할 때)

1. 회원은 `settings.AUTH_USER_MODEL`로 가리킨다 (`from users.models import User` ❌)
2. PK는 `UUIDField(primary_key=True, default=uuid.uuid4, editable=False)` — 예외: `category`(category_code), `financial_profile`(user_id)
3. `class Meta: db_table = "ERD 이름"`
4. FK 필드 이름은 `_id`를 뺀다 (Django가 붙임). `_id`로 안 끝나는 FK는 `db_column`으로 맞춘다 (예: `confirmed_category`)
5. 다른 앱 모델은 문자열로: `"budgets.Category"`
6. on_delete는 `FK_삭제규칙.md` 표 그대로. `SET_NULL`이면 `null=True, blank=True`
7. `AUTH_USER_MODEL`은 다시 바꾸지 않는다

## 7. migration 규칙 (팀 공통)

1. 모델 바꾸기 전에 **pull 먼저**
2. `makemigrations`는 **DB 담당(김나은)** 이 만들고, 나머지는 받아서 `migrate`만
3. migration 파일은 모델 코드와 **같이 커밋**
4. 이미 커밋한 migration 파일은 고치지 않고, 바꿀 게 있으면 새로 `makemigrations`
5. 카테고리 초기 데이터(UNCLASSIFIED 포함)는 2026-10-20 목록 확정 후 data migration으로 추가

## 8. 가맹점 분류 기억 사용법 (거래 분류 담당)

```python
from finance.merchant_rules import remembered_category, normalize_merchant, USER_SOURCE

# 새 거래 분류할 때 ML보다 먼저
code = remembered_category(user, tx.merchant_name)   # 없으면 None → 원래대로 ML·검색

# 사용자가 거래 상세에서 카테고리를 고쳤을 때
tx.confirmed_category_id = "LIVING"
tx.classification_source = USER_SOURCE   # 지금은 "user" (코드 확정되면 이 값만 바꿈)
```

- 가맹점 이름은 저장할 때도 `normalize_merchant()`로 공백을 정리해 둔다. 그래야 같은 가게로 찾아진다.
- 다른 회원의 기록은 쓰지 않는다 (회원별로만 기억).

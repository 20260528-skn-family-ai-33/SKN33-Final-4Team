# DB 모델 구조 (Django 앱 배치 · 공통 규칙 · 확인 방법)

작성일: 2026-10-19 (10-20 · 10-21 추가) · 기준: ERD v2 (`ERDCloud_가져오기_v2.sql`), `FK_삭제규칙.md`, `ERD_v2_수정필요사항` 10번

## 1. 현재 상태 — 28개 테이블 모델 완료 ✅

| 항목 | 결과 |
|---|---|
| 테이블 | 28 = ERD 26 + 10-20 추가 1 (`category_priority`) + 10-21 추가 1 (`category_minimum`) |
| 칸(컬럼) | 349 = ERD 340 (타입·길이·NULL 허용까지 ERD SQL과 자동 대조해서 전부 일치) − 10-20 삭제 4 (`priority_1~3` · `living_floor`) + 10-20 추가 8 (아래 표) + 10-21 추가 4 (`category_minimum`) + 10-21 지킬 돈 A안 +1 (`living_budget`·`cushion_monthly` 추가, `cushion_floor` 삭제) |
| FK | 80 = ERD 79 (on_delete CASCADE 32 · RESTRICT 27 · SET_NULL 20, `FK_삭제규칙.md`와 일치) − 10-20 삭제 3 (`priority_1~3`, RESTRICT) + 10-20 추가 2 (`category_priority`: 회원 CASCADE · 카테고리 RESTRICT) + 10-21 추가 2 (`category_minimum`: 회원 CASCADE · 카테고리 RESTRICT) → CASCADE 34 · RESTRICT 26 · SET_NULL 20 |
| UNIQUE | ERD 24개 중 23개 + 조건부 UNIQUE 5개 + 10-20 추가 2개 (`category_priority`) + 10-21 추가 1개 (`category_minimum`) |
| CHECK | 10번 표의 "한 줄 규칙" 반영 (아래 4번) + 10-21 추가 1개 (`cm_min_amount_gte_1`) + 10-21 지킬 돈 A안 2개 (`fp_living_budget_gte_0` · `fp_cushion_monthly_gte_0`) + 10-21 고정지출 2개 (`re_amount_1000_100m` · `re_payment_day_1_31`) |
| 검증 | PostgreSQL 16에서 확인 (팀 로컬 Docker는 17. 쓰는 기능 중 가장 최근 것인 `NULLS NOT DISTINCT`가 15부터 지원이라 17에서도 같다) — `migrate` 성공 + 규칙 54개(10-19 39개 + 10-20 15개) 실제 데이터로 시험 → 전부 통과. 10-20 우선순위 표·최소 생활비 삭제(`users` 0004)도 PostgreSQL 16에서 `migrate` + 규칙 7개 시험 통과. 10-21 카테고리 최소 금액 표(`users` 0005)도 PostgreSQL 16에서 `migrate` + 규칙 8개(정상 저장 · 1원·기타 저장 · 같은 카테고리 두 번 · 0원 · 음수 · 사용 중 카테고리 삭제 · 지우고 다시 넣기 · 회원 삭제 CASCADE) 시험 통과. 10-21 지킬 돈 A안(`users` 0006)도 PostgreSQL 16에서 `migrate` + `makemigrations --check`(변경 없음) + 규칙 5개(정상 저장 · 0원 · NULL · 생활비 음수 · 쿠션 적립금 음수) 시험 통과. 10-21 고정지출 금액·납부일 CHECK(`finance` 0003)도 PostgreSQL 16에서 `migrate` + `makemigrations --check`(변경 없음) + 규칙 9개(1,000원 · 999원 · 0원 · 1억 원 · 1억 원+1 · 납부일 없음 · 31일 · 0일 · 32일) 시험 통과 |

**ERD와 다른 점 (일부러 바꾼 것)**

| 무엇 | 왜 |
|---|---|
| `category.display_order` UNIQUE 제거 | 순서 바꿀 때(1↔2) 에러 나서. 2026-10-19 결정 |
| `transaction_link` 자기 자신 연결 금지 CHECK 추가 | A→A 연결 방지 |
| `budget_item`·`spending_allocation` UNIQUE 이름 | ERD 이름이 길어서 잘려 있어 짧게 바꿈 (내용은 같음) |

**2026-10-20 추가 · 변경 (화면 대조 결과 · ERD SQL에도 반영 필요)**

| 무엇 | 왜 |
|---|---|
| `category_priority` 표 추가 (회원 · 카테고리 · 순위) | 소비 우선순위를 **1~3위 필수, 4위부터는 원하는 만큼** 받기로 함. 칸으로 두면 최대 개수가 칸 수에 묶여서 표로 뺌 (회원이 고른 수만큼 한 줄씩). DB는 같은 순위 2개 · 같은 카테고리 두 번 · 순위 0 이하만 막고, 1~3위 필수 · 순위 건너뛰기(1·2·4위)는 API에서 검사. **순위를 바꿀 땐 그 회원 순위를 다 지우고 새 목록을 한 번에 다시 넣는다**(트랜잭션 하나). 한 줄씩 1위↔2위를 바꾸면 UNIQUE(회원, 순위)에 걸린다 |
| `financial_profile.priority_1` ~ `priority_6` 삭제 | 위 `category_priority` 표로 옮김 |
| `financial_profile.living_floor`(월 최소 생활비) 삭제 | 2026-10-15 최소생활비 개념 제외 결정 반영. 생활비는 전체 생활비 안에서 카테고리별로 나눈다. 화면(지킬 돈 설정)에서도 뺌 |
| `financial_transaction.suggestion_candidates` (JSON) | 확인 필요 거래에 **후보 카테고리 여러 개**를 보여줌. `[{"category": "FOOD", "confidence": 0.41}, ...]` 확신도 높은 순. JSON 안 코드는 FK 검사가 안 되므로 저장할 때 category에 있는지 확인 |
| `financial_transaction.suggestion_reason` (VARCHAR 300) | "카드 결제대행사 이름만 있어 가게를 특정하지 못했어요." 같은 **확인 필요 이유** |
| `conversation.active_agent` (VARCHAR 40, 비어도 됨) | **대화 상태를 서버가 기억**하기로 함(2026-10-20). 지금 대화 중인 도우미(소비 분석 · 예산 조정 등). "응", "5만원으로" 같은 짧은 답을 라우터가 다시 분류하지 않고 이 도우미에게 바로 넘기려고. 라우터만 거친 상태면 비어 있음. 코드 값은 미정(아래 5번) |
| `conversation.slots` (JSON, 비어도 됨) | 지금까지 문장에서 뽑아 모은 **재료(슬롯)**. 예: `{"category": "FOOD", "amount": 50000, "direction": "up"}`. 다음 문장에서 빠진 것만 다시 물으려고. 칸 이름·단위는 API 명세 때 확정(금액은 원 단위 추천). 도우미가 바뀌거나 작업이 끝나면 비움. JSON 안 카테고리 코드는 FK 검사가 안 되므로 쓸 때 category에 있는지 확인. 대화 전체 기록은 지금처럼 `chat_message`에 저장 |
| 인덱스 `ft_user_merchant_idx` (user, merchant_name, occurred_on 역순) | "같은 가맹점 분류에 반영" 기능. 별도 테이블 없이 `finance/merchant_rules.py`의 `remembered_category()`가 같은 회원·가맹점의 **사용자가 확정한 최근 거래**를 찾음. 느려지거나 규칙 관리가 필요해지면 `merchant_rule` 테이블 추가 |

**2026-10-21 추가 (온보딩 카테고리 최소 금액 · ERD SQL에도 반영 필요)**

| 무엇 | 왜 |
|---|---|
| `category_minimum` 표 추가 (회원 · 카테고리 · `min_amount` BIGINT 원 단위) | 온보딩(내 정보 입력)에서 **원하는 카테고리에만 카테고리 최소 금액을 선택으로** 받기로 함. 우선순위와 상관없이 예산 칸이 있는 카테고리(10개 + 기타) 어디든 걸 수 있어서 `category_priority`에 칸을 붙이지 않고 따로 뺌. **설정 안 한 카테고리는 줄이 없다**(0원 저장 안 함). DB는 같은 카테고리 두 번 · 1원 미만만 막고, 합계 ≤ 생활비 총액은 예산 만들 때 엔진이 검사(넘으면 부족액 안내 후 수정 받기). 바꿀 땐 우선순위처럼 그 회원 것을 다 지우고 새 목록을 한 번에 다시 넣는다(트랜잭션 하나) |
| 자물쇠(`budget_category_setting.locked`)와 다름 | 자물쇠 = 금액 그대로 고정(주기별) / 카테고리 최소 금액 = 이 금액 밑으로만 안 내려감, 올리는 건 가능(회원 설정) |

**2026-10-21 변경 (지킬 돈 A안 · ERD SQL에도 반영 필요)**

| 무엇 | 왜 |
|---|---|
| `financial_profile.living_budget` (BIGINT, 원 단위, 비어도 됨) 추가 | 지킬 돈 설정에서 **생활비를 사용자가 직접 고치게** 됨. 그전엔 생활비가 방향 비율로 매번 계산되는 값이라 `budget_item`(bucket=`living`, 카테고리별)에만 있었음. 이제 사용자 설정값이라 저축(`savings_floor`)·쿠션과 나란히 여기에 둔다. 매 주기 예산을 나눌 때 이 값이 생활비 카테고리 합계의 기준이고, 실제 배정 결과는 여전히 `budget_item`. 10-20에 지운 `living_floor`(최소 생활비 = 보호 하한)와는 다른 칸(생활비 총액) |
| `financial_profile.cushion_floor`(쿠션 보호 하한액) 삭제 → `cushion_monthly`(월 쿠션 적립금, BIGINT, 원 단위) 추가 | 쿠션을 '이 아래로 안 쓰는 금액' 대신 **매달 적립할 금액**으로 정하기로 함. 의미가 달라 이름만 바꾸지 않고 지우고 새로 만듦(아직 실제 데이터 없음). 0원 = 이번엔 적립 안 함. 잔액 < 월급×3이면 0원 불가는 API에서 검사 |
| `recurring_expense` CHECK 2개 추가 (금액 1,000원 ~ 1억 원 · 납부일 1~31, 비워도 됨) | 10-21 C-03 결정. 0원·0을 더 친 오타를 막고, 출금일을 몰라도 등록하게 함. 고정지출 합계가 월급보다 큰지는 막지 않고 예산 만들 때 부족액으로 안내(BUD-001). 그 달에 없는 납부일(2월 31일 등)은 그 달 말일(BUD-004) |
| CHECK `fp_living_budget_gte_0` · `fp_cushion_monthly_gte_0` | 음수 금액 방지 (비어 있으면 통과) |
| ⚠️ 쿠션 **사용 한도 규칙은 미정** | 보호 하한이 없어져서 예산 조정 때 쿠션을 어디까지 쓸 수 있는지 다시 정해야 함(2차 돌발 지출·카테고리 초과 조정 구현 전). 그때 하한·비율 제한을 두면 칸을 추가하고, 승인만 받으면 쓰는 방식이면 `cushion_use_policy`로 충분 |

## 2. 테이블 → 앱 배치

| 앱 | 테이블 (ERD 이름) | 모델 클래스 |
|---|---|---|
| `users` | app_user, financial_profile, category_priority, category_minimum, consent | User, FinancialProfile, CategoryPriority, CategoryMinimum, Consent |
| `finance` | connection, bank_account, card, loan, sync_run | Connection, BankAccount, Card, Loan, SyncRun |
| `finance` | financial_transaction, transaction_link | FinancialTransaction, TransactionLink |
| `finance` | card_bill, card_bill_item, recurring_expense, payment_due, payment_match | CardBill, CardBillItem, RecurringExpense, PaymentDue, PaymentMatch |
| `budgets` | category, budget_cycle, budget, budget_item, budget_category_setting | Category, BudgetCycle, Budget, BudgetItem, BudgetCategorySetting |
| `budgets` | spending_allocation, reserve_plan, cushion_entry | SpendingAllocation, ReservePlan, CushionEntry |
| `chat` | conversation, chat_message, async_task | Conversation, ChatMessage, AsyncTask |

합계: users 5 + finance 12 + budgets 8 + chat 3 = **28개**

## 3. 도커 PostgreSQL에서 확인하기

```bash
# 1) (전에 migrate 한 적 있으면) DB 비우기 — LOCAL_SETUP.md ⚠️ 박스 참고
docker exec money-cushion-db psql -U money_cushion -d money_cushion -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

# 2) 테이블 만들기
cd backend
python manage.py migrate
python manage.py createsuperuser      # 이메일로 만든다

# 3) 테이블 목록 보기 (머니쿠션 28개 + Django 기본 9개 = 37개)
docker exec money-cushion-db psql -U money_cushion -d money_cushion -c "\dt"

# 4) 한 테이블 구조 보기 (칸·제약·FK)
docker exec money-cushion-db psql -U money_cushion -d money_cushion -c "\d payment_due"
```

화면으로 보고 싶으면 `python manage.py runserver` → `http://127.0.0.1:8000/admin` 에 28개 테이블이 다 등록돼 있다.

## 4. DB가 막아 주는 규칙 (CHECK · 조건부 UNIQUE)

| 테이블 | 막는 것 |
|---|---|
| financial_profile | 급여일 1~31 밖 |
| recurring_expense | 금액 1,000원 미만 · 1억 원 초과, 납부일 1~31 밖 (납부일을 비우는 건 OK) — 10-21 |
| category_priority | 같은 회원이 같은 순위 2개, 같은 카테고리를 두 순위에 넣기, 순위 0 이하 |
| category_minimum | 같은 회원이 같은 카테고리 두 번, 최소 금액 1원 미만 |
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

**DB가 못 막는 것 (서비스 코드에서 꼭 검사)** — 우선순위 1~3위 필수 · 순위 건너뛰기(1·2·4위), 카테고리 최소 금액 합계 ≤ 생활비 총액(예산 만들 때 검사, 넘으면 부족액 안내), 같은 회원 데이터끼리만 연결, 지출 배분의 주기 = 예산의 주기, 여러 줄 합계(예산 항목 합 ≤ 배분 자금, 매칭 합 ≤ 거래 금액), approved 교체·쿠션 증감은 트랜잭션+잠금, A→B·B→A 역방향 중복 연결, 재연결 시 기존 자산·청구서 재사용.

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
| conversation.active_agent | 대화 중인 도우미 (소비 분석 · 예산 조정 · 큰 지출 상담 등, 라우터 의도 목록과 같은 코드로) — 10-20 추가 |

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
5. 카테고리 목록은 2026-10-20 회의로 확정 (가게 업종 기준). **코드 이름(`category_code`)을 정한 뒤** 초기 데이터를 data migration으로 추가한다. 이름·순서는 `frontend/src/utils/categories.js`와 맞춘다.

   | 구분 | 카테고리 | ML 정답 | 우선순위 | 예산 칸 |
   |---|---|---|---|---|
   | 10개 | 식비 · 주거·통신 · 교통·차량 · 생활용품 · 패션·쇼핑 · 의료·건강 · 뷰티·미용 · 교육 · 문화·여가 · 여행·숙박 | ✅ | ✅ | ✅ |
   | 기타 | 10개 범위 밖 지출 (송금 · 경조사 등) | ❌ | ❌ | ✅ |
   | 미분류 | 아직 판단 못 한 지출 (편의점 · 마트 · 종합몰). 화면에는 **'확인 필요'** | ❌ | ❌ | ❌ |

## 8. 가맹점 분류 기억 사용법 (거래 분류 담당)

```python
from finance.merchant_rules import remembered_category, normalize_merchant, USER_SOURCE

# 새 거래 분류할 때 ML보다 먼저
code = remembered_category(user, tx.merchant_name)   # 없으면 None → 원래대로 ML·검색

# 사용자가 거래 상세에서 카테고리를 고쳤을 때
tx.confirmed_category_id = "FOOD"     # 예시 코드 — 카테고리 코드가 정해지면 그 값으로
tx.classification_source = USER_SOURCE   # 지금은 "user" (코드 확정되면 이 값만 바꿈)
```

- 가맹점 이름은 저장할 때도 `normalize_merchant()`로 공백을 정리해 둔다. 그래야 같은 가게로 찾아진다.
- 다른 회원의 기록은 쓰지 않는다 (회원별로만 기억).

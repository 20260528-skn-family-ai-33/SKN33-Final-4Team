# DB 모델 구조 (Django 앱 배치 · 공통 규칙 · 확인 방법)

작성일: 2026-10-19 (10-20 · 10-21 · 10-22 · 10-24 추가) · 기준: ERD v2 (`ERDCloud_가져오기_v2.sql`), `FK_삭제규칙.md`, `ERD_v2_수정필요사항` 10번

## 예산 설계 v2 추가 필드 — DB 담당 최종 검토 필요 (2026-11-10)

- `FinancialProfile.budget_category_targets`: 유지할 카테고리별 예산 목표를 담는 JSON, 기본값 `{}`. 사용자 최소액·주기별 자물쇠와 별개로 다음 추천에서 참고할 목표를 보존한다. 목표 변경 시 과거 초안을 그대로 승인하지 않도록 입력 해시에 포함한다. JSON 값의 검증은 서버 계산·요청 경계에서 수행한다.
- 기존 작업 파일 `backend/users/migrations/0011_financialprofile_budget_category_targets.py`가 필드를 추가한다. `0012_merge_budget_targets_privacy.py`는 목표 필드 분기와 main의 개인정보 삭제 분기를 연결하는 빈 merge migration이며 추가 스키마 조작은 없다.
- 사용자의 DB 정리 대행·실제 로컬 적용 요청에 따라 기존 분기를 통합·검증했으나, 이것을 DB 담당 나은님의 필드 설계 최종 승인으로 간주하지 않는다. 필드 용도와 기존 두 파일의 유지/명칭은 나은님 PR 리뷰로 확인해야 한다.
- 로컬 DB에는 이미 적용한 기록이 있어 이 검토에서 파일을 삭제·번호 재작성하거나 새 `makemigrations`를 실행하지 않는다. main에 다른 후속 migration이 추가되면 DB 담당이 최종 그래프와 새 병합 필요 여부를 정한다.
- 예산 승인만으로 `profile.living_budget`을 높이지 않는다. 여윳돈 충당은 해당 예산 버전의 항목과 메타데이터에만 반영한다. 부족 원인 분석·일회성/지속 조정·기본 생활비 설정 변경은 예산 조정 에이전트의 별도 사용자 승인 범위다.

## 1. 현재 상태 — 30개 테이블 모델 완료 ✅

| 항목 | 결과 |
|---|---|
| 테이블 | 30 = ERD 26 + 10-20 추가 1 (`category_priority`) + 10-21 추가 1 (`category_minimum`) + 10-21 추가 1 (`idempotency_record`) + 10-22 추가 1 (`recurring_saving`) |
| 칸(컬럼) | 370 = ERD 340 (타입·길이·NULL 허용까지 ERD SQL과 자동 대조해서 전부 일치) − 10-20 삭제 4 (`priority_1~3` · `living_floor`) + 10-20 추가 8 (아래 표) + 10-21 추가 4 (`category_minimum`) + 10-21 지킬 돈 A안 +1 (`living_budget`·`cushion_monthly` 추가, `cushion_floor` 삭제) + 10-21 추가 9 (`idempotency_record`) − 10-21 삭제 1 (`recurring_expense.category_detail`) + 10-21 추가 1 (`recurring_expense.created_at`) + 10-22 추가 12 (`recurring_saving`) + 10-24 추가 3 (`card_bill.items_state` · `card_bill.fetched_at` · `loan.overdue`) = **373** |
| FK | 83 = ERD 79 (on_delete CASCADE 32 · RESTRICT 27 · SET_NULL 20, `FK_삭제규칙.md`와 일치) − 10-20 삭제 3 (`priority_1~3`, RESTRICT) + 10-20 추가 2 (`category_priority`: 회원 CASCADE · 카테고리 RESTRICT) + 10-21 추가 2 (`category_minimum`: 회원 CASCADE · 카테고리 RESTRICT) + 10-21 추가 1 (`idempotency_record`: 회원 CASCADE) + 10-22 추가 3 (`recurring_saving`: 회원 CASCADE · 보내는/받는 계좌 SET_NULL) → CASCADE 36 · RESTRICT 26 · SET_NULL 22 |
| UNIQUE | ERD 24개 중 23개 + 조건부 UNIQUE 5개 + 10-20 추가 2개 (`category_priority`) + 10-21 추가 1개 (`category_minimum`) + 10-21 추가 1개 (`idempotency_record` 회원 · 요청 번호) |
| CHECK | 10번 표의 "한 줄 규칙" 반영 (아래 4번) + 10-21 추가 1개 (`cm_min_amount_gte_1`) + 10-21 지킬 돈 A안 2개 (`fp_living_budget_gte_0` · `fp_cushion_monthly_gte_0`) + 10-21 고정지출 4개 (`re_amount_1000_100m` · `re_payment_day_1_31` · `re_status_valid` · `re_source_type_valid`) + 10-22 정기 저축 5개 (`rs_amount_1000_100m` · `rs_transfer_day_1_31` · `rs_status_valid` · `rs_source_type_valid` · `rs_from_to_different`) + **10-24 정한 코드 20개** (아래 '2026-10-24 반영' 표) |
| 검증 | PostgreSQL 16에서 확인 (팀 로컬 Docker는 17. 쓰는 기능 중 가장 최근 것인 `NULLS NOT DISTINCT`가 15부터 지원이라 17에서도 같다) — `migrate` 성공 + 규칙 54개(10-19 39개 + 10-20 15개) 실제 데이터로 시험 → 전부 통과. 10-20 우선순위 표·최소 생활비 삭제(`users` 0004)도 PostgreSQL 16에서 `migrate` + 규칙 7개 시험 통과. 10-21 카테고리 최소 금액 표(`users` 0005)도 PostgreSQL 16에서 `migrate` + 규칙 8개(정상 저장 · 1원·기타 저장 · 같은 카테고리 두 번 · 0원 · 음수 · 사용 중 카테고리 삭제 · 지우고 다시 넣기 · 회원 삭제 CASCADE) 시험 통과. 10-21 지킬 돈 A안(`users` 0006)도 PostgreSQL 16에서 `migrate` + `makemigrations --check`(변경 없음) + 규칙 5개(정상 저장 · 0원 · NULL · 생활비 음수 · 쿠션 적립금 음수) 시험 통과. 10-21 고정지출 금액·납부일 CHECK(`finance` 0003)도 PostgreSQL 16에서 `migrate` + `makemigrations --check`(변경 없음) + 규칙 9개(1,000원 · 999원 · 0원 · 1억 원 · 1억 원+1 · 납부일 없음 · 31일 · 0일 · 32일) 시험 통과. 고정지출 상태·출처 코드(`finance` 0004)도 PostgreSQL 16에서 `migrate` + `makemigrations --check`(변경 없음) + 규칙 12개(상태 4 × 출처 2 정상 저장 8개 · 한글 상태 · 없는 상태 · 없는 출처 · 빈 출처) 시험 통과. 요청 번호 표(`users` 0007)도 PostgreSQL 16에서 `migrate` + `makemigrations --check`(변경 없음) + `@idempotent` 시험 8개(같은 번호 두 번 → 1개만 생성 · 같은 번호 다른 내용 409 · 이름 같아도 번호 다르면 2개 · 실패는 기록 안 함 · 다른 회원 같은 번호 · 헤더 없음 · 번호 형식 틀림 · **동시에 같은 번호 2개 → 1개만 생성**) 통과. 세부 분류 칸 삭제(`finance` 0005)도 PostgreSQL 16에서 `migrate` + `makemigrations --check`(변경 없음) 확인. 고정지출 API 시제품으로 `docs/api/fixed.md` 규칙 56개 시험 통과. 10-22 정기 저축 표(`finance` 0007)도 PostgreSQL 16에서 `migrate` + `makemigrations --check`(변경 없음) + 저축 API 시제품으로 `docs/api/savings.md` 규칙 38개 시험 통과. **10-24 정한 코드 반영(`finance` 0008 · `budgets` 0004 · `chat` 0003 · `users` 0008)도 PostgreSQL 16에서 빈 DB `migrate` · 기존 DB 위에 `migrate` · 0007로 되돌렸다 다시 올리기 · `makemigrations --check`(변경 없음) + 규칙 40개(정상 값 저장 17 · 오타 · 범위 밖 · 해제 상태와 시각 불일치 막힘 23) 시험 통과** |

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
| `recurring_expense.status` · `source_type` 코드 확정 (choices + CHECK) | 10-21 C-04 결정. 상태 `suggested`(후보) · `confirmed`(확정) · `stopped`(중지) · `ignored`(무시), 출처 `manual`(직접 등록) · `detected`(반복 탐지). 예산에는 `confirmed`만 들어간다. 직접 추가는 바로 `confirmed`, 자동 발견은 `suggested` → 사용자가 '맞아요'면 `confirmed`, '아니에요'면 `ignored`(같은 후보를 다시 묻지 않으려고 행을 남김). 고정지출은 매월만(연납은 비정기지출 준비금, 매주·매일 사는 건 생활비) |
| `idempotency_record` 표 추가 (회원 · 요청 번호 UNIQUE · 방식 · 주소 · 내용 해시 · 처음 응답) | 10-21 C-10 결정. [추가하기] · [승인]을 두 번 눌러도 한 번만 반영하려고. 화면이 폼을 열 때 만든 요청 번호(`Idempotency-Key`)를 기억하고, 같은 번호가 또 오면 처음 응답을 돌려준다. 이름이 같다고 중복으로 보지 않는다. 사용법은 `config/idempotency.py` · `docs/API_RULES.md` |
| `recurring_expense.category_detail`(세부 분류) 삭제 | 10-21 결정. 화면에서 입력하는 곳이 없고 1차에 쓰는 기능이 없어서 뺌. 거래(`financial_transaction`)·예산 항목(`budget_item`)의 같은 이름 칸은 용도가 달라 그대로 둠. '구독 모아보기' 같은 기능이 생기면 다시 검토 |
| `recurring_expense.created_at`(발견·등록 시각) 추가 | 10-21 고정지출 API 검수. 고정지출 목록에서 후보를 **최근에 찾은 순**으로 보여 주는데 기준이 될 시각 칸이 없었음(`confirmed_at`은 확정 때만 채워짐). 저장할 때 자동으로 채움(`auto_now_add`). 이미 있던 행은 마이그레이션한 시각으로 채워짐 |
| `recurring_saving`(정기 저축) 표 추가 — 30번째 표 | 10-22 S-01 결정. 저축 확인 화면의 후보 · [저축이에요] · [아니에요]를 기억할 곳이 없었음. 고정지출 표에 섞으면 고정비 합계에 저축이 들어가 두 번 빠질 위험이 있어 따로 둠. 모양은 고정지출과 같고(상태 suggested·confirmed·stopped·ignored, 출처 manual·detected, 근거 거래, 확정·발견 시각) 카테고리 대신 보내는 계좌 · 받는 계좌(둘 다 SET_NULL). 저축 최소 금액은 저장하지 않고 confirmed 합계로 계산(S-02) |
| CHECK `fp_living_budget_gte_0` · `fp_cushion_monthly_gte_0` | 음수 금액 방지 (비어 있으면 통과) |
| ⚠️ 쿠션 **사용 한도 규칙은 미정** | 보호 하한이 없어져서 예산 조정 때 쿠션을 어디까지 쓸 수 있는지 다시 정해야 함(2차 돌발 지출·카테고리 초과 조정 구현 전). 그때 하한·비율 제한을 두면 칸을 추가하고, 승인만 받으면 쓰는 방식이면 `cushion_use_policy`로 충분 |

**2026-10-27 · 28 추가 (온보딩 자취 여부 · 예산 명세 리뷰 · ERD SQL에도 반영 필요)**

| 무엇 | 왜 |
|---|---|
| `financial_profile.housing_type` (VARCHAR 10, `alone` 자취 · `family` 본가 거주, 비어도 됨) | 10-27 온보딩 변경. 예산 산정 방향(생활 안정형 · 저축 집중형) 대신 **자취 여부로 지킬 돈 비율**을 정한다 (자취 55·30·5·10 / 본가 45·40·5·10, `docs/api/budgets.md` P-02). 비어 있으면 아직 안 고름 → 지킬 돈 API가 `missing`으로 알림. `budget_direction`은 아직 지우지 않음(내 정보 API 정리 때) |
| `budget_category_setting.locked_amount` (BIGINT, 원 단위, 비어도 됨) | 10-28 예산 명세 리뷰. 자물쇠는 주기 전체가 같이 쓰는데, 초안 · 승인 예산의 금액이 다를 수 있어서 **걸 때 그 예산의 금액을 같이 저장**하고 다시 추천할 때 이 금액을 유지한다. 풀면 NULL |
| CHECK `fp_housing_type_valid` · `bcs_locked_amount_gte_0` · `bcs_unlocked_no_amount` | 거주 형태 코드 오타, 잠긴 금액 음수, 자물쇠가 풀렸는데 금액이 남아 있는 것 방지 |

**2026-10-24 반영 (정한 코드 · 결정 Z · AA · P · Y · E · L — ERD 그림 · 설계문서에도 반영 완료)**

| 무엇 | 왜 |
|---|---|
| H-07 코드 choices + CHECK — `connection.status` · `bank_account.account_type` · `financial_transaction.direction` · `transaction_type` · `confirmation_state` · `classification_source` | 10-22 H-07 확정. 오타나 CODEF 원래 코드(`11` 등)가 그대로 들어가는 걸 막음. CODEF 예금 구분은 `account_type`이 아니라 `field_states.codef_deposit_code`에 |
| CHECK `conn_status_disconnected_at` | 해제(`disconnected`)면 `disconnected_at` 필수, 연결 중(`connected` · `relogin`)이면 비어 있어야 함 (H-07 "해제하면 disconnected + 해제 시각") |
| `connection.business_type` choices + CHECK — `bank` · `card` · `loan` | 결정 Z① (연결 목록에 대출 기관도 보임) |
| `card_bill.scope_type` · `confirmation_state` **비울 수 있게** + 코드 | 결정 Z: CODEF가 안 주면 `null`. 범위 `issuer` · `card` · `unknown`, 확정 상태 `estimated` · `confirmed` · `unknown` (API 이름은 `confirmationStatus`) |
| `card_bill.items_state` 추가 (비어도 됨) | 결정 Z: 상세 항목 확보 상태 `complete` · `partial` · `unavailable` |
| `card_bill.fetched_at` 추가 (비어도 됨) | 결정 AA①: 청구 총액 · 날짜의 마지막 정상 조회 시각. 상세 항목 시각으로 대신 채우지 않음 |
| `loan.overdue` 추가 (불리언, 비어도 됨) | 결정 P: 연체 확인 true · 연체 없음 확인 false · 모름 null. 상환 상태 `status`와 다른 뜻 |
| `loan` CHECK 5개 — 상환 방식 코드 4개 · 월 상환액 1원~1억 · 납부일 1~31 · 직접 등록 잔액 1원~10억 · 금리 0~30% | 결정 P · Y (모두 비우는 건 OK) |
| `budget_cycle.status` choices + CHECK — `active` · `closed` (기본 `active`) | home.md |
| `chat_message.role` (`user` · `bot`) · `content_type` (`text` · `card`), `async_task.status` (`queued` · `running` · `succeeded` · `failed` · `canceled`, 기본 `queued`) | 결정 E |
| `consent.product_scope` choices + CHECK — `bank` · `card` · `loan` (회원가입 동의는 비움) | 결정 L |
| `reserve_plan` 표 · `budget_item` · `spending_allocation` · `payment_due`의 `reserve` 칸 — **1차 미사용** | 10-24 결정 AB: 비정기지출 준비금은 2차. 표 · 칸은 지우지 않고 비워 둔다 (`bucket` · `funding_bucket` · `obligation_type`의 `reserve` 값도 1차엔 안 씀) |
| JSON 칸에 넣기로 한 것 (DB 변경 없음) | 거래 `field_states.original_transaction_type`(원래 이체였다는 표시, AA⑥) · 대출 `field_states`의 값마다 `source` · `confirmationStatus` · `confirmedAt`(AA②) · 대출 `field_states.first_seen_on`(서비스가 대출을 처음 확인한 날 `YYYY-MM-DD`. 홈에서 처음 볼 때 첫 성공 기관 조회 시작 · 첫 납부 회차 생성 · 오늘 중 가장 이른 날로 한 번 저장하고 바꾸지 않음. 이 날보다 앞선 납부일은 새로 만들지 않는다. 조회마다 바뀌는 `fetched_at`은 쓰지 않음, 11/06 R05) |
| 아직 안 바꾼 것 | `app_user.nickname` 필수(결정 X) — 기존 빈 닉네임 회원 정리 방법이 정해지면(auth.md '확인 필요') NOT NULL로. `consent.purpose` — 금융 동의 목적 코드가 아직 '확인 필요'(finance-connect.md) |

## 2. 테이블 → 앱 배치

| 앱 | 테이블 (ERD 이름) | 모델 클래스 |
|---|---|---|
| `users` | app_user, financial_profile, category_priority, category_minimum, consent, idempotency_record, privacy_deletion | User, FinancialProfile, CategoryPriority, CategoryMinimum, Consent, IdempotencyRecord, PrivacyDeletion |
| `finance` | connection, bank_account, card, loan, sync_run | Connection, BankAccount, Card, Loan, SyncRun |
| `finance` | financial_transaction, transaction_link | FinancialTransaction, TransactionLink |
| `finance` | card_bill, card_bill_item, recurring_expense, recurring_saving, payment_due, payment_match | CardBill, CardBillItem, RecurringExpense, RecurringSaving, PaymentDue, PaymentMatch |
| `budgets` | category, budget_cycle, budget, budget_item, budget_category_setting | Category, BudgetCycle, Budget, BudgetItem, BudgetCategorySetting |
| `budgets` | spending_allocation, reserve_plan, cushion_entry | SpendingAllocation, ReservePlan, CushionEntry |
| `chat` | conversation, chat_message, async_task | Conversation, ChatMessage, AsyncTask |

합계: users 6 + finance 13 + budgets 8 + chat 3 = **30개**

## 3. 도커 PostgreSQL에서 확인하기

```bash
# 1) (전에 migrate 한 적 있으면) DB 비우기 — LOCAL_SETUP.md ⚠️ 박스 참고
docker exec money-cushion-db psql -U money_cushion -d money_cushion -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

# 2) 테이블 만들기
cd backend
python manage.py migrate
python manage.py createsuperuser      # 이메일로 만든다

# 3) 테이블 목록 보기 (머니쿠션 30개 + Django 기본 9개 = 39개)
docker exec money-cushion-db psql -U money_cushion -d money_cushion -c "\dt"

# 4) 한 테이블 구조 보기 (칸·제약·FK)
docker exec money-cushion-db psql -U money_cushion -d money_cushion -c "\d payment_due"
```


화면으로 보고 싶으면 `python manage.py runserver` → `http://127.0.0.1:8000/admin` 에 30개 테이블이 다 등록돼 있다.

## 4. DB가 막아 주는 규칙 (CHECK · 조건부 UNIQUE)

| 테이블 | 막는 것 |
|---|---|
| financial_profile | 급여일 1~31 밖, 거주 형태가 alone·family 아님 (비우는 건 OK) — 10-27 |
| budget_category_setting | 잠긴 금액 음수, 자물쇠가 풀렸는데 잠긴 금액이 있음 — 10-28 |
| recurring_expense | 금액 1,000원 미만 · 1억 원 초과, 납부일 1~31 밖 (납부일을 비우는 건 OK), 상태가 suggested·confirmed·stopped·ignored 아님, 출처가 manual·detected 아님 — 10-21 |
| recurring_saving | 금액 1,000원 미만 · 1억 원 초과, 이체일 1~31 밖 (비우는 건 OK), 상태 · 출처가 정해진 값이 아님, 보내는 계좌 = 받는 계좌 — 10-22 |
| category_priority | 같은 회원이 같은 순위 2개, 같은 카테고리를 두 순위에 넣기, 순위 0 이하 |
| category_minimum | 같은 회원이 같은 카테고리 두 번, 최소 금액 1원 미만 |
| connection | 같은 회원·업권·기관에서 '연결 중'이 2개 (해제 후 재연결은 OK), 업권 · 상태 오타, 해제인데 해제 시각 없음 / 연결 중인데 해제 시각 있음 — 10-24 |
| bank_account | 계좌 종류가 demand·deposit·other 아님 (비우는 건 OK) — 10-24 |
| card, loan | origin이 provider/manual 아님, 대출 status가 repaying/repaid 아님 |
| loan | 상환 방식 코드 오타, 월 상환액 1원 미만 · 1억 초과, 납부일 1~31 밖, 직접 등록 잔액 0원 미만 · 10억 초과(11/06 R19: 0원 허용으로 변경 — 등록은 serializer 에서 1원부터), 금리 0~30% 밖 (모두 비우는 건 OK) — 10-24 |
| sync_run | 조회 대상(계좌·카드·대출) 2개 이상 |
| financial_transaction | source_kind와 원천 FK 짝 안 맞음, 금액 음수, 취소액이 0~원거래 금액 밖, 신뢰도 0~1 밖, 방향 · 거래 유형 · 확인 상태 · 분류 출처 오타 — 10-24 |
| transaction_link | 자기 자신과 연결, relation_type 오타 |
| card_bill | 범위 · 확정 상태 · 상세 항목 상태 오타 (셋 다 비우는 건 OK) — 10-24 |
| card_bill_item | item_type 오타, 할부 회차 0 이하, 이번 회차 > 총회차 |
| payment_due | 의무 종류와 원천 FK 짝 안 맞음, active인데 취소 시각 있음 / canceled인데 없음, payment_state 오타, 납부액 음수 |
| payment_match | 매칭 금액 0 이하 |
| budget_cycle | 시작일 ≥ 종료일, 상태가 active·closed 아님 — 10-24 |
| budget | 상태 오타, **주기마다 approved 2개 이상**, 자기 자신을 이전 예산으로 |
| budget_item · spending_allocation | 빈칸(NULL) 포함 중복 줄 (NULLS NOT DISTINCT), reserve인데 준비금 계획 없음 |
| cushion_entry | 초기액 2번, 배분 1건에 사용 2번, 종류별 부호(initial ≥0, use <0, refund >0, 적립·정정 ≠0), 종류별 필수 연결 |
| chat_message · async_task | 발화자 · 내용 종류 · 작업 상태 오타 — 10-24 |
| consent | 조회 범위가 bank·card·loan 아님 (비우는 건 OK) — 10-24 |

**DB가 못 막는 것 (서비스 코드에서 꼭 검사)** — 우선순위 1~3위 필수 · 순위 건너뛰기(1·2·4위), 카테고리 최소 금액 합계 ≤ 생활비 총액(예산 만들 때 검사, 넘으면 부족액 안내), 같은 회원 데이터끼리만 연결, 지출 배분의 주기 = 예산의 주기, 여러 줄 합계(예산 항목 합 ≤ 배분 자금, 매칭 합 ≤ 거래 금액), approved 교체·쿠션 증감은 트랜잭션+잠금, A→B·B→A 역방향 중복 연결, 재연결 시 기존 자산·청구서 재사용.

## 5. 코드(값 목록) 미정 칸 — 팀이 정하면 choices 추가

지금은 글자 칸으로만 만들어 둠. 값을 정해도 **DB 구조는 안 바뀐다** (선택지만 추가).

| 테이블.칸 | ERD 설명 |
|---|---|
| sync_run.status | 성공 · 부분완료 · 실패. 11/10: `product_code = card_foreign_pending`(원화 금액을 못 채운 외화 승인 표시, 승인 한 건당 한 줄 · `requested_from` = 사용일)은 `skipped`. 다른 조회 기록 쿼리는 `product_code` · `status`로 걸러 영향 없음 (`docs/api/cards.md`) |
| transaction_link · payment_due · reserve_plan `.confirmation_state` | 확인 상태 |
| card_bill.source_type | 조회 · 직접 입력 |
| payment_due.source_type | 조회 · 직접 입력 · 추정 |
| async_task.task_type | 작업 종류 (예: `budget_adjust`, 결정 E는 예시만) |
| conversation.active_agent | 대화 중인 도우미 (소비 분석 · 예산 조정 · 큰 지출 상담 등, 라우터 의도 목록과 같은 코드로) — 10-20 추가 |
| consent.purpose | 회원가입 `terms` · `privacy`는 정해짐(L), 금융 조회 동의 목적 코드는 확인 필요 |

정해진 코드(loan.status, budget.status, payment_state, obligation_state, entry_type, item_type, bucket, funding_bucket, source_kind, obligation_type, relation_type, origin, conversation.status, app_user.status)와 **10-24에 넣은 코드**(H-07 6칸 · connection.business_type · card_bill.scope_type · confirmation_state · items_state · loan.repayment_method · budget_cycle.status · chat_message.role · content_type · async_task.status · consent.product_scope)는 choices + CHECK로 들어가 있다. 값 목록은 위 '2026-10-24 반영' 표와 결정기록 3-1 H-07 코드표.

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


## 2026-11-05: 카드 미매칭 승인 확인 (finance/0012)

- `card_approval_review`: 회원·카드사·승인 식별 해시 UNIQUE. 원문 카드번호 없이 정규화된 거래 payload와 원천 승인 identity를 보관한다. 최신 금액 정상 여부, 조회 시각, 사용자가 확정한 카드·확정 시각·금융 거래 FK를 저장한다. 거래 삭제 시 FK는 SET_NULL, 카드 삭제는 RESTRICT, 회원 삭제 시 CASCADE. 같은 번호의 다른 승인에 확인 결과를 전파하지 않는다.
- `card_approval_coverage`: sync_run 1:1(CASCADE), 실제 조회 시작·종료일, 금액/취소 불명확 여부, 확인 거래와 조회 카드 M2M, 성공 기록 생성 시각. 확인 전에는 예산 계산용 성공 기록을 만들지 않고, 모두 확인됐을 때만 실제 조회 범위를 승격한다. 기존 불완전 sync_run을 근거 없이 소급 완료하지 않는다.
- 도입 이유: 금액이 정상인 카드 미매칭 승인도 재조회·재연결에 걸쳐 보존하고, 사용자가 거래 한 건의 결제 카드를 확인한 후 중복 없이 예산 계산을 재개하기 위해서다.

## 2026-11-05 동의 철회·탈퇴 삭제 처리

스키마 변경 없이 `users/privacy.py`, `users/api_privacy.py`가 기존 모델의 삭제 범위를 관리한다. 동의 철회는 해당 범위의 조회 데이터와 자동 탐지 항목을 삭제하고 Consent의 revoked_at은 남긴다. 직접 등록 항목은 삭제 자료와의 FK·근거만 해제한다. 혼합 개인정보가 남는 예산·쿠션·주기·대화·작업·요청 응답 캐시는 초기화한다. 여러 모델을 함께 모아 RESTRICT 참조가 삭제 집합 안에 있는지 검증하며 범위 밖 참조가 있으면 트랜잭션을 중단한다. 탈퇴는 User CASCADE와 전체 세션 삭제를 사용한다. 상세 범위·실패·CODEF 인증 삭제는 api/auth.md와 api/finance-connect.md에 명시했다.

## 2026-11-06 직접 등록 대출 정정 · 전체 완납 (R19) — `finance/0014_loan_manual_balance_zero`

- CHECK `loan_manual_balance_1_1b` → `loan_manual_balance_0_1b` (0원 ~ 10억). 이유: 정정에서 잔액 0원(≠ 미입력 null)을 받아야 하고, 전체 완납 뒤 잔액을 0원으로 일관되게 표시한다. 직접 등록(POST)은 그대로 1원부터 받는다. 기존 행은 1원 이상이라 보정이 필요 없다.
- 상태는 새로 만들지 않고 기존 `loan.status = repaid` 를 쓴다.
- 완납 근거 · 복구값은 새 칸 없이 `loan.field_states.payoff` 에 둔다 (`first_seen_on` 과 같은 방식): `source: "user_report"`(CODEF 확인 아님) · `reportedAt`(처리 시각) · `reportedOn`(처리일 = 오늘) · `previous`(`status` · `manualBalance` · 그 값의 `fieldStates.manualBalance`) · `canceledDueIds`(이 처리로 취소한 미래 회차). 되돌리면 지우지 않고 `undoneAt` 을 덧붙인다. 다시 완납하면 새 기록으로 바뀐다(되돌리기 요청은 `reportedAt` 이 같아야 한다).
- 동작 상세: `backend/finance/api_loans.py` `loan_payoff` · `loan_payoff_cancel`, 명세 `docs/api/loans.md` 13번.

## 2026-11-06 삭제 진행 기록 (R21) — `users/0011_privacy_deletion`

- `privacy_deletion` 표 추가: 동의 철회 · 회원 탈퇴 한 건 = 한 줄. 외부(CODEF) 인증 삭제와 우리 DB 삭제를 한 트랜잭션으로 되돌릴 수 없어서, 단계(`status`: `provider_pending` 접수 · 외부 삭제 대기 → `local_pending` 외부 삭제 확인 → `completed`)와 실패 단계 · 안전한 오류 코드 · 실행 횟수 · 실행 권한 만료(`lease_expires_at`) · 실행 토큰(`lease_token`) · 처리 시각을 남기고 재시도한다. 토큰을 가진 실행만 상태 · 실패 · 해제를 기록한다.
- **회원 FK 없음** (`user_id` UUID 만): `purge_scope`(AsyncTask 등 삭제) · `user.delete`(CASCADE) 뒤에도 기록이 남아야 완료 · 실패를 추적할 수 있다.
- 조건부 UNIQUE `privacy_deletion_one_open` (회원 · 범위, `status <> completed`): 같은 범위의 미완료 삭제는 하나만 → 반복 요청은 기존 기록을 재사용. `scope` 는 bank · card · loan · all(탈퇴).
- `provider_kinds`: 접수 시점 동의로 정한 CODEF 업권 (null=전체, `[]`=외부 삭제 없음 — 계좌 · 대출 공유 은행 인증 보존).
- 넣지 않는 것: 계좌번호 · 비밀번호 · connectedId(회원 행의 기존 AES-GCM 암호문을 그대로 쓴다) · 외부 응답 원문 · 예외 메시지.
- 보존 · 정리: 완료 기록은 30일 보관 후 `python manage.py privacy_deletion_retry` 가 지운다. 미완료 기록은 완료될 때까지 남는다. 모든 조회 동의가 철회되고 삭제가 끝나면 회원의 connectedId 암호문 · 발급 시각을 비운다. 탈퇴는 회원 행과 함께 사라진다.
- 동작 상세: `backend/users/deletion.py` 머리말.

## 2026-11-06 작업 실행 권한 (R20) — `chat/0004_asynctask_lease_expires_at`, `finance/0013_syncrun_task`

- `async_task.lease_expires_at` (null 허용): 이 시각까지만 작업이 새 조회를 시작하고 결과를 저장할 수 있다. 작업이 **자기** 조회를 시작하거나 저장할 때만 연장한다.
- `sync_run.task_id` → `async_task` (null 허용, SET_NULL): 이 조회를 시킨 작업. 작업과 조회를 정확히 잇는 기존 칸이 없어 추가했다. 같은 회원이라는 이유만으로 다른 조회가 멈춘 작업을 연장하던 문제를 막는다. 철회 때 작업 행은 지워져도 조회 기록(조회 범위 근거)은 남아야 해서 CASCADE 가 아니라 SET_NULL.
- 필드 추가 전 기록(보수적 기준): `lease_expires_at` 이 비면 시작 시각(없으면 생성 시각) + 10분까지만 유효하고 연장하지 않는다. `task_id` 가 빈 조회는 어떤 작업도 연장하지 않는다. 일괄 보정은 하지 않는다 — 다음 새 작업 시작 · 철회 · 탈퇴 때 회원별로 정리된다.
- 동작 상세: `backend/config/tasks.py` 머리말.

## 2026-11-06 급여 판단 (R12, budgets/0006)

- `salary_decision`: 입금 거래 1:1(CASCADE) · 회원 · `pay_cycle_on`(귀속 급여 회차 = 주기 시작일, 거절이면 null) · `state`(`confirmed` 사용자 확인 / `auto` 자동 인정 / `rejected` 사용자 거절 / `recheck` 재확인 필요 — 11/06 budgets/0007: 자동 인정 거래가 취소 · 모호해졌을 때 다른 거래로 몰래 갈아 붙이지 않고 그 회차를 사용자 확인 전까지 멈추려고 상태만 추가) · `is_pattern`(자동 인식 기준, 회원당 하나 — 조건부 UNIQUE) · `decided_at`.
- CHECK: state 값 · 거절 ↔ 회차 비움 · 기준은 사용자 확인만. 인덱스 (회원, 회차).
- 도입 이유: 처음 찾은 큰 입금을 사용자 확인 없이 급여로 인정해 환급 · 송금을 오인했다. 후보와 인정을 나누고, 사람의 판단(확인 · 거절)을 재조회 · 재처리가 덮지 않게 저장해야 했다. 거래당 한 줄이라 한 거래가 여러 회차에 붙거나 동시 요청으로 두 번 더해지지 않는다. 회차는 `budget_cycle` FK 대신 날짜로 둔다 — 조기 급여의 다음 회차 주기는 아직 없고, 주기를 미리 만들면 예산 주기를 앞당기게 된다.
- `budget_cycle.confirmed_income`은 이 표의 인정 합계로 채운다. 기존 값은 일괄 보정하지 않는다(상세: api/salary.md '기존 자료').
- `finance/0013_syncrun_task`(작업 중 다른 변경)에 의존한다.

### 사용자 확정 기준값 (budgets/0008, 2026-11-06)

`salary_decision`에 nullable `snapshot_amount`(bigint), `snapshot_occurred_on`(date), `snapshot_description`(varchar 500), `snapshot_account_id`(UUID, 당시 계좌 값이므로 FK 아님)를 추가한다. 사용자 확인 시에만 원거래 값을 저장한다. 금액·거래일·적요 원문·계좌 UUID가 달라지거나 기준값이 없으면 `recheck`로 전환하고 `is_pattern`을 해제한다. 변경 없는 확정은 유지한다. 재확인 전까지 인정 합계에서 제외하며 과거 기준값과 `decided_at`은 보존한다.

기존 확정 기록에는 기준값을 채우지 않는다. 데이터 마이그레이션은 기존 `confirmed`를 `recheck`로 바꾸고 자동 인식 기준과 영향 회차의 `confirmed_income` 캐시를 비운다. 개발/운영 DB 적용은 별도이며, 회귀 테스트는 전용 테스트 DB에서 수행한다. 상세 조회 계약은 [api/salary.md](api/salary.md)의 '사용자 확정 유효성' 참고.
## 2026-11-10: 청년 정책 추천 조건 (policies/0003_policy_profile)

- `policy_profile`: 회원당 1행 (PK = `user_id`, 회원 FK **CASCADE** — 탈퇴하면 함께 삭제). 지금은 사는 지역 코드(`region_code`, `policies/data/regions.json` 의 시군구 코드)만 둔다.
- 채팅에서 사용자가 [다음에도 ○○구로 찾기]를 **눌렀을 때만** 만든다 (메모의 '동의한 경우만 회원별 저장'). 다음 대화에서 지역을 다시 묻지 않는 데 쓴다. [지역 바꾸기] 뒤 다시 누르면 바뀐다.
- 쓰기는 채팅 엔진이 대화 저장과 같은 트랜잭션에서 한다 (409 면 저장 안 됨). 지역 표에서 사라진 코드는 없는 것으로 읽는다.
- 넣지 않는 것: 생년월일 · 성별(온보딩 `financial_profile` 값을 그대로 읽는다), 특화 대상(아직 묻지 않음).
- 만든 사람: 권세진 (DB 담당 김나은에게 따로 전달).

## 2026-11-10: 저장한 청년 정책 (policies/0002_saved_policy)

- `saved_policy`: 회원이 저장한(북마크) 정책 한 건 = 한 줄. 채팅 추천의 [N번 저장] 버튼과 저장 API(`docs/api/policies.md`)가 만든다. 마이페이지 표시 방식은 아직 정하지 않았다.
- PK 는 UUID. `user_id` → 회원 FK **CASCADE** (탈퇴하면 함께 지운다). `policy_no` → `policy` FK **PROTECT** (정책은 지우지 않고 `is_active=False` 로만 두므로, 저장한 정책이 마감 · 수집 종료돼도 기록이 남는다).
- 조건 UNIQUE `saved_policy_once` (회원 · 정책): 같은 정책을 두 번 저장하지 않는다 → 저장 요청을 여러 번 보내도 한 줄 (API 는 이미 저장돼 있어도 성공).
- 인덱스 `saved_policy_user_recent` (회원 · 저장 시각 내림차순): 저장 목록을 최신순으로 읽는다.
- 넣지 않는 것: 저장 당시 정책 내용 복사본. 목록은 지금 `policy` 값을 읽고, 마감 · 수집 종료 여부를 함께 보여 준다.
- 만든 사람: 권세진 (DB 담당 김나은에게 따로 전달).

## 2026-11-06: 청년 정책 (policies/0001)

- `policy`: 온통청년 정책 한 건. PK는 공식 정책번호(`policy_no`)라서 UUID 규칙의 예외다. 원본(`raw`, 담당자 이름 제외), 정리한 조건 칸, 텍스트 검색 문장, 임베딩 문장, `embedding`(vector 1536)을 둔다. 수집에서 사라진 정책은 삭제하지 않고 `is_active=False`로 둔다.
- `policy_sync`: 수집 이력. 마지막 성공 건의 `data_date`가 데이터 기준일이다.
- 회원과 연결되지 않는 공개 데이터라 FK가 없다. pg_trgm 확장을 켠다. 상세는 `docs/agents/청년정책.md`.

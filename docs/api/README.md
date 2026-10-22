# 머니쿠션 API 명세 — 1차 취합

> 취합 · 검수: 김나은 · 2026-10-22 · 기준: `main` `7a431f0` (PR #26 ~ #32 병합 후) · **2차 검수 10/22 오후**
> 명세 마감 **10/24(금)** · 개발 시작 **10/25(토)**
> 이 문서는 **목차 + 검수 결과 + 남은 일**이다. API 하나하나의 보내는 값 · 받는 값 · 실패는 아래 표의 원본 문서를 본다.
> 공통 규칙: [API_RULES.md](../API_RULES.md) · 결정: [결정기록](../결정기록_2026-10-21.md) · 전체 목록(ML 후 포함): [API 명세 목록](../머니쿠션_API_명세_목록_2026-10-21.md)

## 0. 요약

| 항목 | 결과 |
|---|---|
| 1차 '지금 · 기본만' API | **35개** — 34개 작성 + **대출 직접 등록 1개 10/22 추가(작성 전, 세진)** |
| 공통 규칙 지킴 | 로그인 안 됨 403 `not_authenticated` · `fields`는 칸 오류 때만 · 주소 ID 틀림 404 / 본문 ID 틀림 400 · 카테고리 대문자 코드 · 계좌 · 카드 `displayName` + `status` 3가지 → **API가 있는 9개 문서 모두 맞음** |
| 1차 검수 반영 | 결정 A~F · J~M, 문서끼리 다른 것 5개 → **모두 반영** (PR #31 · #32, 4-1) |
| 2차 검수 | 고칠 것 **5개**(4-2) — 연결 목록에 계좌 · 카드 ID 없음, 재연결 규칙, 해결된 '확인 필요' 6줄, 근거 표기, 거래 목록에 월급 · 이체 추가(V) |
| '확인 필요' 108줄 | 이미 해결 6 · PM 답 12(**✅ N**) · 담당자끼리 20 · 회의 32(안건 9개 **모두 ✅ 결정 O ~ V**) · 개발 중 27 · 결정 · ML 후 11 (5번 · 6번) |
| 개발을 막는 것 | 없음 — 대출(P) · 금융 공통 에러(O) 10/22 결정. 남은 건 **대출 직접 등록 명세 작성**(세진) |
| 남은 API | 1차 **18개**(ML 후 14 · 결정 후 4) + 2 · 3차 4개 (7번) |

---

## 1. 한눈에 보기 — 35개

| # | 묶음 | 기능 | 방식 · 주소 | 문서 | 담당 | 리뷰 |
|---|---|---|---|---|---|---|
| 1 | 공통 · 인증 | 저장 전 보안값 준비 | `GET /api/csrf/` | [auth.md](auth.md) | 세진 | ✅ PR #31 (구현됨) |
| 2 | | 회원가입 | `POST /api/auth/signup/` | auth.md | 세진 | ✅ PR #31 |
| 3 | | 로그인 | `POST /api/auth/login/` | auth.md | 세진 | ✅ PR #31 |
| 4 | | 로그아웃 | `POST /api/auth/logout/` | auth.md | 세진 | ✅ PR #31 |
| 5 | | 내 계정 확인 | `GET /api/auth/me/` | auth.md | 세진 | ✅ PR #31 · 기본만 |
| 6 | 금융 동의 · 연결 | 금융 조회 동의 저장 | `POST /api/finance/consents/` | [finance-connect.md](finance-connect.md) | 혜리 | ✅ PR #32 |
| 7 | | 계좌 후보 보기 | `GET /api/finance/connect/accounts/` | finance-connect.md | 혜리 | ✅ PR #32 |
| 8 | | 계좌 선택 저장 | `PUT /api/finance/connect/accounts/` | finance-connect.md | 혜리 | ✅ PR #32 |
| 9 | | 연결 목록 보기 | `GET /api/finance/connections/` | finance-connect.md | 혜리 | ✅ PR #32 |
| 10 | | 연결 해제 | `DELETE /api/finance/connections/<id>/` | finance-connect.md | 혜리 | ✅ PR #32 |
| 11 | 카드 · 대출 | 카드 후보 보기 | `GET /api/finance/connect/cards/` | [cards.md](cards.md) | 세진 | ✅ PR #31 · 기본만 |
| 12 | | 카드 선택 저장 | `PUT /api/finance/connect/cards/` | cards.md | 세진 | ✅ PR #31 · 기본만 |
| 13 | | 카드 청구서 보기 | `GET /api/finance/card-bills/<id>/` | cards.md | 세진 | ✅ PR #31 · 기본만 |
| 14 | | 대출 정보 보기 | `GET /api/finance/loans/` | [loans.md](loans.md) | 세진 | 초안 · 기본만 |
| 15 | | 대출 정보 보완 | `PATCH /api/finance/loans/<id>/` | loans.md | 세진 | 🟡 **10/22 결정 P** — 3칸, 문서 반영 전 |
| 16 | 거래 | 거래 목록 보기 | `GET /api/finance/transactions/` | [transactions.md](transactions.md) | 혜리 | ✅ PR #32 |
| 17 | | 거래 한 건 보기 | `GET /api/finance/transactions/<id>/` | transactions.md | 혜리 | ✅ PR #32 |
| 18 | | 거래 분류 수정 | `PATCH /api/finance/transactions/<id>/` | transactions.md | 혜리 | ✅ PR #32 |
| 19 | 고정지출 | 고정지출 목록 보기 | `GET /api/budgets/fixed/` | [fixed.md](fixed.md) | 나은 | ✅ PR #27 |
| 20 | | 고정지출 직접 추가 | `POST /api/budgets/fixed/` | fixed.md | 나은 | ✅ PR #27 |
| 21 | | 고정지출 수정 | `PATCH /api/budgets/fixed/<id>/` | fixed.md | 나은 | ✅ PR #27 |
| 22 | | 고정지출 중지 | `DELETE /api/budgets/fixed/<id>/` | fixed.md | 나은 | ✅ PR #27 |
| 23 | | 후보 확정 (맞아요) | `POST /api/budgets/fixed/confirm/` | fixed.md | 나은 | ✅ PR #27 |
| 24 | | 후보 무시 (고정지출 아니에요) | `POST /api/budgets/fixed/ignore/` | fixed.md | 나은 | ✅ PR #27 |
| 25 | 정기 저축 | 정기 저축 목록 보기 | `GET /api/budgets/savings/` | [savings.md](savings.md) | 나은 | ✅ PR #29 |
| 26 | | 정기 저축 직접 추가 | `POST /api/budgets/savings/` | savings.md | 나은 | ✅ PR #29 |
| 27 | | 정기 저축 수정 | `PATCH /api/budgets/savings/<id>/` | savings.md | 나은 | ✅ PR #29 |
| 28 | | 정기 저축 중지 | `DELETE /api/budgets/savings/<id>/` | savings.md | 나은 | ✅ PR #29 |
| 29 | | 후보 확정 (저축이에요) | `POST /api/budgets/savings/confirm/` | savings.md | 나은 | ✅ PR #29 |
| 30 | | 후보 무시 (아니에요) | `POST /api/budgets/savings/ignore/` | savings.md | 나은 | ✅ PR #29 |
| 31 | 홈 | 홈 화면 보기 | `GET /api/home/` | [home.md](home.md) | 나은 | ✅ PR #26 · 기본만 |
| 32 | 통합 챗봇 | 이전 대화 보기 | `GET /api/chat/conversations/<id>/` | [chat.md](chat.md) | 혜리 | ✅ PR #32 |
| 33 | | 챗봇에 메시지 보내기 | `POST /api/chat/messages/` | chat.md (+ 소비 분석 카드 칸 [spending-analysis.md](spending-analysis.md), 세진) | 혜리 · 세진 | ✅ PR #31 · #32 (J) |
| 34 | | 오래 걸리는 작업 진행 확인 | `GET /api/tasks/<id>/` | chat.md | 혜리 | ✅ PR #32 |
| 35 | 카드 · 대출 | **대출 직접 등록** (10/22 추가) | `POST /api/finance/loans/` | loans.md | 세진 | 🟡 받는 칸 결정 Y · **명세 작성 전** |

**요청 번호(`Idempotency-Key`)를 쓰는 API**: 6 동의 저장 · 35 대출 직접 등록 · 20 고정지출 추가 · 23 · 24 고정지출 확정 · 무시 · 26 저축 추가 · 29 · 30 저축 확정 · 무시 · 33 메시지 보내기. 나머지(선택 저장 `PUT`, 수정 `PATCH`, 중지 · 해제 `DELETE`)는 두 번 보내도 결과가 같아서 안 쓴다.
**회원가입(2)**: 로그인 전이라 공통 요청 번호가 적용되지 않음 → 중복 가입 막는 방법은 5-3 '회원가입 · 로그인 기본값'.

---

## 2. 공통 코드 모음 — API 값 = DB 값, 소문자 영어 (H-07)

| 어디 | 코드 | 뜻 | 원본 |
|---|---|---|---|
| 카테고리 | `FOOD` · `HOUSING_TELECOM` · `TRANSPORT_CAR` · `HOUSEHOLD` · `FASHION_SHOPPING` · `MEDICAL_HEALTH` · `BEAUTY` · `EDUCATION` · `CULTURE_LEISURE` · `TRAVEL_LODGING` · `ETC` · `UNCLASSIFIED` | 10개 + 기타 + 확인 필요 (응답엔 `categoryName` 같이) | API_RULES |
| 계좌 · 카드 · 연결 `status` | `connected` · `relogin` · `disconnected` | 연결됨 / 다시 연결 필요 / 연결 해제. 계좌 · 카드는 따로 저장하지 않고 연결에서 가져옴 | API_RULES · finance-connect |
| 계좌 종류 `account_type` | `demand` · `deposit` · `other` | 입출금(CODEF `11`) / 예적금 / 기타 | finance-connect |
| 거래 방향 `direction` | `in` · `out` | 입금 / 출금 | transactions |
| 거래 종류 `transactionType` | `spending` · `transfer` · `own_transfer` · `card_payment` · `saving` · `loan_repayment` · `income` | 소비 / 이체 / 내 계좌끼리 / 카드값 / 저축 / 대출 상환 / 입금 | transactions |
| 분류 상태 `classifyState` | `needs_review` · `suggested` · `confirmed` | 확인 필요 / 규칙 · ML이 정함 / 사용자 확정. `UNCLASSIFIED`면 항상 `needs_review` | transactions |
| 분류 출처 `classifiedBy` | `user` · `rule` · `ml` · `llm` | | transactions |
| 돈의 출처 `funding` | `living` · `fixed` | 생활비 / 고정비로 연결됨(생활비 사용액에서 빠짐) | transactions |
| 취소 `cancel.state` | `none` · `partial` · `full` | | transactions |
| 고정지출 · 정기 저축 `status` | `suggested` · `confirmed` · `stopped` · `ignored` | 후보 / 확정 / 중지 / 무시. 목록엔 앞의 둘만 | fixed · savings |
| 고정지출 · 정기 저축 `sourceType` | `detected` · `manual` | 자동 발견 / 직접 추가 | fixed · savings |
| 납부 상태 `paymentState` | `unpaid` · `partial` · `paid` · `needs_review` | 미납 / 일부 / 냄 / 미납 확인 필요(+3일 지나도 출금 없음) | fixed · home |
| 월급 상태 `salaryState` | `received` · `waiting` · `unknown` | 들어옴 / 아직 / 실수령액 몰라서 확인 불가 | home |
| 급여 주기 `budget_cycle.status` | `active` · `closed` | (DB만) | 결정기록 3-1 |
| 대출 `status` · `origin` | `repaying` · `repaid` / `provider` · `manual` | 상환 중 · 완료 / 기관 조회 · 직접 등록 | loans |
| 챗봇 의도 `route.intent` | `spending` · `budget` · `big` · `out` · `unknown` | 소비 분석 / 예산 조정 / 큰 지출(3차) / 범위 밖 / 모름 | chat |
| 메시지 `role` · `contentType` | `user` · `bot` / `text` · `card` | **결정기록 3-3 E로 확정** | chat |
| 작업 `status` | `queued` · `running` · `succeeded` · `failed` · `canceled` | **결정기록 3-3 E로 확정**. `taskType` 예: `budget_adjust` | chat |
| 동의 범위 `productScope` | `bank` · `card` · `loan` | | finance-connect |
| 에러 `code` | `invalid` · `parse_error` · `not_authenticated` · `permission_denied` · `not_found` · `method_not_allowed` · `idempotency_conflict` · `server_error` · **`consent_required` · `relogin_required` · `provider_unavailable`** | 뒤 셋은 금융 조회용 (10/22 결정 O) | API_RULES |

---

## 3. 화면 → API

| 화면 | 쓰는 API (#) |
|---|---|
| 회원가입 · 로그인 · 마이(로그아웃) | 1 · 2 · 3 · 4 · 5 |
| 금융 연결(동의 → 계좌 → 카드) | 6 · 7 · 8 · 11 · 12 |
| 대출 확인 | 14 · 15 · 35 |
| 첫 설정: 고정지출 확인 → 저축 확인 | 19 ~ 24 → 25 ~ 30 |
| 홈 | 31 (카드 청구서 → 13은 H-04 정한 뒤) |
| 거래 | 16 · 17 · 18 |
| 마이 > 연결 관리 · 고정지출 · 정기 저축 | 9 · 10 / 19 ~ 22 / 25 ~ 28 |
| 챗봇 | 32 · 33 · 34 |

---

## 4. 검수 결과

### 4-1. 1차 검수(10/22 오전) 반영 현황 — 모두 반영 ✅

| 묶음 | 내용 | 반영 |
|---|---|---|
| 결정 A~F | 소비 분석 달력월 · 같은 날짜까지 비교 · 챗봇 코드 · 초안 만료 조건 · 같은 구매 · 연결 해제 때 같이 그만할지 | ✅ PR #31(세진) · PR #32(혜리) |
| 결정 J~M | 소비 분석 카드 칸 통합 · 선택 저장 규칙 · 동의 모양 · 해제된 것 후보에서 빼기 | ✅ PR #31 · PR #32 |
| 링크 · 문구 | `cards-loans.md` 링크, "선택할 수 없는 계좌예요." | ✅ PR #32 |
| 나은 몫 | 목록 문서 7번(A · C), chat.md 기간 설명("예산과 같은 기준" → "홈 · 예산은 급여 주기", BUD-004), savings.md 403 표기 · 연결 해제(F) 줄 추가 | ✅ |

### 4-2. 2차 검수(10/22 오후)에서 새로 찾은 것

| # | 무엇 | 왜 문제인가 | 고치는 방향 (제안) | 담당 |
|---|---|---|---|---|
| ① | **연결 목록에 계좌 · 카드 ID가 없음** | F(연결 해제 때 고정지출 · 저축 같이 그만할지 묻기)의 확인 창에 "이 연결로 나가는 고정지출 · 저축"을 띄우려면 그 연결의 계좌 · 카드 ID가 필요. 카드 후보에도 `connectionId`가 없어서 화면이 골라낼 방법이 없음 | 연결 목록 `items[]`에 **`accountIds` · `cardIds`** 추가 (finance-connect.md 확인 필요 마지막에서 두 번째 줄 해소) | 혜리 |
| ② | **다시 연결할 때 계좌 · 카드 재사용 규칙이 명세에 없음** | 해제 → 재연결 때 계좌를 새로 만들면 같은 통장이 2개가 되고, 고정지출 · 저축은 옛 계좌를 가리켜 계속 "연결 해제됨"으로 보임. DB 문서에만 "재연결 시 기존 자산 재사용" 한 줄 있음 | **새 연결 API(결정 후)를 쓸 때** "같은 계좌 · 카드면 기존 것을 새 연결에 다시 붙인다"를 넣음. 지금은 메모만 | 혜리 (결정 후) |
| ③ | **'확인 필요'에 이미 해결된 줄이 남아 있음** (6줄) | 읽는 사람이 아직 안 정해진 줄 알고 다시 물어봄 | 아래 5-1 '이미 해결'을 지우거나 결정으로 옮김 | 세진 4 · 혜리 2 |
| ④ | 세진 문서 근거 표기 "팀장 전달 J/K/L" | 결정기록에 J~M이 생겨서 찾아가기 어려움 | "결정기록 3-3 J/K/L"로 | 세진 |
| ⑤ | **거래 목록에 월급 · 이체 추가 · 이체를 소비로 바꾸기** (10/22 결정 V ① · W) | 지금 거래 목록은 "출금 중 소비 거래만", 분류 수정은 소비 거래만 받음 | ① `items[]`에 `direction` · `transactionType`을 목록에도 넣고, 월급 · 이체는 `category: null` · `funding: null`. `summary.spentTotal` · 카테고리 칩 · `needsReviewCount`에는 안 들어감. '전체'는 소비 + 월급 + 이체 ② **거래 분류 수정(PATCH)이 이체도 받음** — 카테고리를 고르면 `transactionType`이 `transfer` → `spending`, `category: null`을 보내면 다시 `transfer`(원래 이체였던 거래만). 받는 사람은 기억 안 함 ③ 거래 상세에 [소비로 분류하기] | 혜리 |

### 4-3. 잘 맞는 것 (2차 확인)

- 공통 규칙(403 · `fields` · 404/400 · 카테고리 코드 · 계좌 · 카드 표시 · 같은 요청 두 번) — 9개 문서 모두 그대로
- J~M 반영 후 **문서끼리 다른 곳 없음**: 소비 분석 카드 칸(chat.md ↔ spending-analysis.md), 선택 저장(계좌 ↔ 카드), 동의(회원가입 ↔ 금융, 둘 다 `v1`), 후보 목록의 해제 처리(계좌 ↔ 카드)
- 기간 기준: 홈 · 예산 = 급여 주기, 거래 · 소비 분석 = 달력월 — 모든 문서가 같은 말
- fixed.md · savings.md · home.md는 '확인 필요'가 H-04 · H-08 말고 없음

---

## 5. '확인 필요' 전부 정리 — 108줄

각 명세 끝의 '확인 필요'를 한 줄씩 다 봤다(fixed · savings는 0줄).

| 문서 | 줄 수 | 이미 해결 | PM 답 | 담당자끼리 | 회의 | 개발 중 | 결정 · ML 후 |
|---|---|---|---|---|---|---|---|
| finance-connect.md | 18 | 2 | 3 | 2 | 3 | 6 | 2 |
| transactions.md | 11 | — | — | — | 6 | 3 | 2 |
| chat.md | 16 | — | 5 | 1 | 1 | 3 | 6 |
| auth.md | 17 | 1 | — | 10 | 3 | 3 | — |
| cards.md | 20 | 1 | 2 | 2 | 8 | 7 | — |
| loans.md | 12 | — | — | 5 | 5 | 1 | 1 |
| spending-analysis.md | 12 | 2 | 2 | — | 4 | 4 | — |
| home.md | 2 | — | — | — | 2 | — | — |
| **합계** | **108** | **6** | **12** | **20** | **32** | **27** | **11** |

> '회의' 32줄은 여러 문서에 같은 주제가 반복된 것(후속 API 배분 4번, H-04 3번, 금융 공통 에러 3번 등)이라 **실제 안건은 6번의 9개**다.
> **10/24까지 할 것 = 이미 해결 정리 + PM 답(✅ 확정) 문서 반영 + 담당자끼리 + 회의.** '개발 중'과 '결정 · ML 후'는 이번 마감과 상관없다.

### 5-1. 이미 해결 — '확인 필요'에서 지우기 (6줄)

| 문서 · 줄 | 해결한 결정 | 담당 |
|---|---|---|
| finance-connect.md 동의 저장 "`purpose` 코드 이름과 종류" | L (회원가입 `terms` · `privacy`, 금융 `finance_inquiry`, 같은 모양) | 혜리 |
| finance-connect.md 계좌 선택 저장 "승인 전 예산 초안 만료 안 함" | D — 이미 답이 적혀 있으니 '확인 필요' 밖으로 옮기기 | 혜리 |
| auth.md "5. 내 계정 확인 필드 이름을 로그인 응답과 같게" | 문서에 이미 같은 3칸(`userId` · `email` · `nickname`) | 세진 |
| cards.md "7. 응답을 6번과 같은 `items` 구조로" | K + 본문에 이미 그렇게 적힘 | 세진 |
| spending-analysis.md "`previousTotal` ↔ `prevTotal` 명칭" | J — chat.md가 `previousTotal`로 바뀜 | 세진 |
| spending-analysis.md "`comparison` 사유 코드 · 우선순위" | J — 1차 `reason` = `not_requested` · `previous_incomplete` 두 개 | 세진 |

### 5-2. PM(나은)이 답하면 끝나는 것 (12줄 → 10개) — ✅ 10/22 확정 (결정기록 3-3 N)

> 아래 제안대로 확정했다. **각 명세의 해당 '확인 필요' 줄을 결정으로 옮기는 것**은 담당자가 한다(아래 표).

| 담당 | 옮길 줄 |
|---|---|
| 혜리 | chat.md 1~5번 (5줄) · finance-connect.md 6~8번 (3줄) |
| 세진 | spending-analysis.md 1 · 2번 (2줄) · cards.md 9 · 10번 (2줄) |

| # | 질문 | 문서 | 결정 (N) |
|---|---|---|---|
| 1 | 지난달에 같은 날짜가 없으면? (3/31에 물으면) | chat · spending-analysis | **지난달 말일까지 자름** — 3/1~31 vs 2/1~28 |
| 2 | '확인 필요' 금액이 제일 크면 `topSpending`에서 뺄지 | chat · spending-analysis | **뺌.** 문장에 "분류된 소비 기준" |
| 3 | `compare`에 `prev_week` · `none`을 둘지 | chat | **1차는 `prev_month`만** |
| 4 | '확인 필요' 거래를 카드에서 따로 볼지 | chat | **`unclassifiedAmount`로 충분**, 칸 추가 없음 |
| 5 | 챗봇 탭을 열 때 최근 대화 찾기 | chat | **1차는 화면이 마지막 `conversationId`를 기억.** 없거나 404면 새 대화. 대화 목록 API는 2차 |
| 6 | 같은 범위 동의가 이미 있을 때 | finance-connect | **같은 범위 · 같은 버전이 유효하면 새로 안 만들고 기존 기록을 돌려줌**(같은 요청 두 번 규칙) |
| 7 | 연결 상세 화면용 API | finance-connect | **1차 없음.** 연결 목록 + 계좌 후보로 그림 |
| 8 | 해제한 연결의 계좌 선택 · 과거 거래 | finance-connect | `isSelected`는 **그대로 둠**(다시 연결하면 복원). 잔액 · 월급 찾기 · 후보에서는 빠짐. **과거 거래는 소비 집계에 계속 포함**(실제로 쓴 돈) |
| 9 | 카드사 · 끝 4자리 자체가 없을 때 표시 | cards | 카드사 없으면 "카드", 번호 없으면 ` · ****` 부분 생략. 추측해서 채우지 않음 |
| 10 | 고정지출이 쓰는 카드를 선택 해제하면 | cards | **고정지출은 그대로.** 결제 수단도 유지(fixed.md "새로 고를 때만 검사"와 같음) |

### 5-3. 담당자끼리 정하면 되는 것 (20줄)

| 묶음 | 줄 수 | 무엇 | 누가 | 메모 |
|---|---|---|---|---|
| ✅ ~~대출 보완 세트~~ → 10/22 결정 P | 5 | 9번 PATCH 보내는 칸 · 범위 · 비울 수 있는 칸 · 수정 가능한 대출 상태 / 8번 `fieldStates` · `repaymentMethod` 제안 채택 / 금액 범위 · 금리 자릿수 / 3개월 평균 추정 | 세진 · 나은 | **34개 중 유일하게 개발 못 하는 API.** 1차 제안: 월 상환액(1원~1억, `null` 가능) · 납부일(1~31, `null` 가능) · 상환 방식(코드 4개, `null` 가능)만 받기. 잔액 · 금리 보완은 2차 |
| ✅ ~~회원가입 · 로그인 기본값~~ → 10/22 결정 X | 10 | 비밀번호 8~25자 · 영문 · 숫자 · 특수문자 중 2가지 이상, 자동 로그인 체크박스 없음 등 | 세진 | auth.md '확인 필요' 10줄을 결정으로 옮기기 |
| ✅ ~~카드 청구서 제안 채택~~ → 10/22 결정 Z | 2 | 예상/확정 · 범위 · 구매 내역 칸 모두 채택(없으면 `null`), 홈에 "예상" 표시(home.md 반영) | 세진 | cards.md '확인 필요' 정리 |
| ✅ ~~대출 기관도 연결 목록에~~ → 결정 Z ① | 1 | `businessType: loan` 보임 | 혜리 | finance-connect.md |
| ✅ ~~해제 확인 창 목록~~ → 결정 Z ③ | 1 | 연결 목록에 `accountIds` · `cardIds` | 혜리 | finance-connect.md |
| ✅ ~~거래 없는 기간의 챗봇 답~~ → 결정 Z ② | 1 | 0원 카드 + "이 기간엔 소비가 없어요" | 혜리 | chat.md |

### 5-4. 개발하면서 정해도 되는 것 (27줄) · 결정 · ML 후 (11줄)

- **개발 중**: CODEF 실제 제공 값(계좌 · 카드 · 청구서 · 대출), 은행명 변환표, `relogin` 기준 오류 코드, `connectedId` 처리, 조회 중 해제, 중지 · 해제 호출 순서, 페이지 나누기, 메시지 나눠 받기, 진행 확인 간격, 중복 ID · 최대 개수, 휴면 · 해지 카드, 취소 · 환불 귀속, 반올림, `updatedAt` 원천, 다품목 가맹점 코드(나은), 닉네임 DB 제약, 비로그인 API 예외 등
- **결정 · ML 후**: 예산 조정 작업 방식 · `error.code` · 만료 표시 · 작업 취소, `budget` 의도 1차 처리, 조정안 형식, 계좌 용도 받기, 선택 계좌 예산 기준, 분류 근거 표시, 분류 변경 뒤 재계산 시점, 대출 보완 저장과 예산 반영 분리

---

## 6. 회의 안건 (10/23)

> ⚠️ = 10/25 개발 전에 꼭

| # | 안건 | 누가 | 제안 / 메모 |
|---|---|---|---|
| ① ✅ | ~~대출 보완(15) 보내는 값~~ → **10/22 결정 P**: PATCH 3칸 · GET 칸 3개 추가 · **대출 직접 등록 POST 추가(35번째)** | 세진 · 나은 | 세진: loans.md 8 · 9번 반영 + 직접 등록 명세 새로 쓰기 |
| ② ✅ | ~~금융 공통 에러~~ → **10/22 결정 O**: GET은 저장된 것만 읽음 · 지금 쓰는 건 `consent_required`(403) 하나 · `relogin_required`(409) · `provider_unavailable`(502)는 연결 · 다시 불러오기 명세에서 | 혜리 · 나은 | 카드 · 대출 · 청구서 문서의 "외부 조회 실패 미정" 칸을 이걸로 채우기 (세진) · 연결 목록 · 계좌 후보도 확인 (혜리) |
| ③ ✅ | ~~H-04 · C-17~~ → **10/22 결정 Q**: 꼭 나갈 돈에 `card_bill` · `loan` 추가(home.md 반영 완료), 첫 청구서 미리 뗄 돈 = 총액 − 이번 주기 그 카드 사용액 | 세진 · 나은 | 세진: cards.md · loans.md의 C-17 · H-04 · 청구서 ID 경로 줄 정리 |
| ④ ✅ | ~~H-08~~ → **10/22 결정 R**: 로그인 응답 · `/me/`에 `onboardingCompleted`(첫 예산 승인 때 true) | 세진 · 나은 | 세진: auth.md 응답에 칸 추가 (home.md는 반영 완료) |
| ⑤ ✅ | ~~소비 분석에 고정지출 포함~~ → **10/22 결정 S**: 넣되 `fixedAmount`(그중 고정지출)로 따로 표시 | 팀 | 혜리: chat.md 카드에 칸 · 문장 추가 / 세진: spending-analysis.md 줄 정리 |
| ⑥ ✅ | ~~감지 · 매칭 담당~~ → **10/22 결정 T**: 명세 쓴 사람이 매칭도 만듦. 혜리 = 거래 수집 · 같은 구매 · 매칭 입구 / 나은 = 고정지출 · 저축 후보 · 납부 매칭 / 세진 = 카드값 · 대출 매칭 | 팀 | 혜리가 입구 함수 이름 · 순서를 먼저 정해 공유 |
| ⑦ ✅ | ~~후속 API 18개 배분~~ → **10/22 결정 U**: 나은 7(예산 엔진) · 세진 8(온보딩 입력 · 쿠션 · 비밀번호) · 혜리 3(연결 · 동기화 · 조정안 승인) | 팀 | 7번 표 참고 |
| ⑧ ✅ | ~~거래 기능 6개~~ → **10/22 결정 V**: **이체 · 월급 입금은 목록에 보임**(소비 합계엔 안 들어감), 나머지 5개 1차 안 함. 편의점 · 마트는 무조건 사용자에게 물어봄 | 팀 | 혜리: transactions.md 목록 범위 수정 (4-2 ⑤) |
| ⑨ ✅ | ~~연결 · 로그인 4개~~ → **10/22 결정 V**: 동의 = 은행 필수 · 카드 · 대출 선택 승인, 나머지 3개 1차 안 함 | 팀 | 혜리 · 세진: 해당 '확인 필요' 줄 정리 |

---

## 7. 남은 API (이번 취합 밖) — 10/22 결정 U로 배분

| 담당 | 개수 | API | 기다리는 것 |
|---|---|---|---|
| **나은** | 7 | 방향 PUT · 지킬 돈 GET · PUT · 추천 예산 POST · 카테고리 예산 GET · 금액 고정 PATCH · 예산 승인 POST | 예산 ML 결과 |
| **세진** | 8 | 내 정보 GET · PUT · 급여 GET · PUT · 쿠션 GET · PATCH · 비정기 준비금 GET (ML 결과 후, 금액은 나은의 예산 엔진 결과를 읽음) · 비밀번호 다시 설정 POST (본인 확인 방법 결정 후) | ML 결과 · 본인 확인 방법 |
| **혜리** | 3 | 금융기관 인증 · 새 연결 POST(**재연결 시 기존 계좌 · 카드 재사용**, 4-2 ②) · 다시 불러오기 POST(`relogin_required` · `provider_unavailable` · 기관별 결과, 결정 O) · 예산 조정안 승인 POST | 인증 방식 · 조정안 ↔ 예산 관계 |
| 2 · 3차 | 4 | 소비 분석 화면, 돌발 지출, 알림, 큰 지출 상담 | 범위 재검토 |

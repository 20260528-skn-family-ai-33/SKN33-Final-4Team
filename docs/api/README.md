# 머니쿠션 API 명세 — 1차 취합

> 취합 · 검수: 김나은 · 2026-10-22 · 기준: `main` `2fa4f48` (PR #26 ~ #30 병합 후)
> 명세 마감 **10/24(금)** · 개발 시작 **10/25(토)**
> 이 문서는 **목차 + 검수 결과 + 남은 일**이다. API 하나하나의 보내는 값 · 받는 값 · 실패는 아래 표의 원본 문서를 본다.
> 공통 규칙: [API_RULES.md](../API_RULES.md) · 결정: [결정기록](../결정기록_2026-10-21.md) · 전체 목록(ML 후 포함): [API 명세 목록](../머니쿠션_API_명세_목록_2026-10-21.md)

## 0. 요약

| 항목 | 결과 |
|---|---|
| 1차 '지금 · 기본만' API | **34개 모두 작성** (문서 10개) |
| 공통 규칙 지킴 | 로그인 안 됨 403 `not_authenticated` · `fields`는 칸 오류 때만 · 주소 ID 틀림 404 / 본문 ID 틀림 400 · 카테고리 대문자 코드 · 계좌 · 카드 `displayName` + `status` 3가지 → **API가 있는 9개 문서 모두 맞음** |
| 고쳐야 할 것 | **결정됐는데 아직 안 들어간 것 8개**(4-1) · **문서끼리 다른 것 5개**(4-2, ①~④는 10/22 결정 → 문서 반영만 남음) |
| 10/24까지 정할 것 | 9개 중 **3개 결정**(10/22 J · K · L), 남은 6개 (5번) — 개발을 막는 건 **대출 보완 요청 칸** |
| 남은 API | 1차 **18개**(ML 후 14 · 결정 후 4) + 2 · 3차 4개 (6번) |

---

## 1. 한눈에 보기 — 34개

| # | 묶음 | 기능 | 방식 · 주소 | 문서 | 담당 | 리뷰 |
|---|---|---|---|---|---|---|
| 1 | 공통 · 인증 | 저장 전 보안값 준비 | `GET /api/csrf/` | [auth.md](auth.md) | 세진 | 초안 (구현됨) |
| 2 | | 회원가입 | `POST /api/auth/signup/` | auth.md | 세진 | 초안 |
| 3 | | 로그인 | `POST /api/auth/login/` | auth.md | 세진 | 초안 |
| 4 | | 로그아웃 | `POST /api/auth/logout/` | auth.md | 세진 | 초안 |
| 5 | | 내 계정 확인 | `GET /api/auth/me/` | auth.md | 세진 | 초안 · 기본만 |
| 6 | 금융 동의 · 연결 | 금융 조회 동의 저장 | `POST /api/finance/consents/` | [finance-connect.md](finance-connect.md) | 혜리 | PR #28 리뷰 |
| 7 | | 계좌 후보 보기 | `GET /api/finance/connect/accounts/` | finance-connect.md | 혜리 | PR #28 리뷰 |
| 8 | | 계좌 선택 저장 | `PUT /api/finance/connect/accounts/` | finance-connect.md | 혜리 | PR #28 리뷰 |
| 9 | | 연결 목록 보기 | `GET /api/finance/connections/` | finance-connect.md | 혜리 | PR #28 리뷰 |
| 10 | | 연결 해제 | `DELETE /api/finance/connections/<id>/` | finance-connect.md | 혜리 | PR #28 리뷰 |
| 11 | 카드 · 대출 | 카드 후보 보기 | `GET /api/finance/connect/cards/` | [cards.md](cards.md) | 세진 | 초안 · 기본만 |
| 12 | | 카드 선택 저장 | `PUT /api/finance/connect/cards/` | cards.md | 세진 | 초안 · 기본만 |
| 13 | | 카드 청구서 보기 | `GET /api/finance/card-bills/<id>/` | cards.md | 세진 | 초안 · 기본만 |
| 14 | | 대출 정보 보기 | `GET /api/finance/loans/` | [loans.md](loans.md) | 세진 | 초안 · 기본만 |
| 15 | | 대출 정보 보완 | `PATCH /api/finance/loans/<id>/` | loans.md | 세진 | ⚠️ **보내는 값 미정** |
| 16 | 거래 | 거래 목록 보기 | `GET /api/finance/transactions/` | [transactions.md](transactions.md) | 혜리 | PR #28 리뷰 |
| 17 | | 거래 한 건 보기 | `GET /api/finance/transactions/<id>/` | transactions.md | 혜리 | PR #28 리뷰 |
| 18 | | 거래 분류 수정 | `PATCH /api/finance/transactions/<id>/` | transactions.md | 혜리 | PR #28 리뷰 |
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
| 32 | 통합 챗봇 | 이전 대화 보기 | `GET /api/chat/conversations/<id>/` | [chat.md](chat.md) | 혜리 | PR #28 리뷰 |
| 33 | | 챗봇에 메시지 보내기 | `POST /api/chat/messages/` | chat.md (+ 소비 분석 카드 칸 [spending-analysis.md](spending-analysis.md), 세진) | 혜리 · 세진 | ⚠️ **카드 칸 두 안** |
| 34 | | 오래 걸리는 작업 진행 확인 | `GET /api/tasks/<id>/` | chat.md | 혜리 | PR #28 리뷰 |

**요청 번호(`Idempotency-Key`)를 쓰는 API**: 6 동의 저장 · 20 고정지출 추가 · 23 · 24 고정지출 확정 · 무시 · 26 저축 추가 · 29 · 30 저축 확정 · 무시 · 33 메시지 보내기. 나머지(선택 저장 `PUT`, 수정 `PATCH`, 중지 · 해제 `DELETE`)는 두 번 보내도 결과가 같아서 안 쓴다.
**회원가입(2)**: 로그인 전이라 공통 요청 번호가 적용되지 않음 → 중복 가입 막는 방법은 5번 '정할 것'.

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
| 에러 `code` | `invalid` · `parse_error` · `not_authenticated` · `permission_denied` · `not_found` · `method_not_allowed` · `idempotency_conflict` · `server_error` | 외부 조회 실패용 코드는 아직 없음(5번 ⑤) | API_RULES |

---

## 3. 화면 → API

| 화면 | 쓰는 API (#) |
|---|---|
| 회원가입 · 로그인 · 마이(로그아웃) | 1 · 2 · 3 · 4 · 5 |
| 금융 연결(동의 → 계좌 → 카드) | 6 · 7 · 8 · 11 · 12 |
| 대출 확인 | 14 · 15 |
| 첫 설정: 고정지출 확인 → 저축 확인 | 19 ~ 24 → 25 ~ 30 |
| 홈 | 31 (카드 청구서 → 13은 H-04 정한 뒤) |
| 거래 | 16 · 17 · 18 |
| 마이 > 연결 관리 · 고정지출 · 정기 저축 | 9 · 10 / 19 ~ 22 / 25 ~ 28 |
| 챗봇 | 32 · 33 · 34 |

---

## 4. 검수 결과

### 4-1. 결정됐는데 명세에 아직 안 들어간 것 (결정기록 3-3, 10/22)

| # | 결정 | 고칠 곳 | 지금 적힌 것 | 이렇게 고침 | 담당 |
|---|---|---|---|---|---|
| ① | **A** 소비 분석 기본 기간 = **달력월** | chat.md `route.slots.period` · 확인 필요 2번째 줄 / spending-analysis.md A-1 | "없으면 이번 급여 주기" | "없으면 **이번 달 1일 ~ 오늘**". 확인 필요 줄 삭제 | 혜리 · 세진 |
| ② | **C** 달이 안 끝났으면 **같은 날짜까지** 비교 | chat.md 확인 필요 첫 줄 · 예시 JSON(`compare` 9/25~10/24) / spending-analysis.md A-2 `partial_period` | 확인 필요로 남음 | "10/1~21이면 9/1~21과 비교, 문장에 '9월 같은 기간보다'". 예시 기간도 달력월로 | 혜리 · 세진 |
| ③ | **E** 코드 확정 | chat.md 공통 값 표(`role` · `contentType`) · 작업 진행 확인(`status` · `taskType`) · 확인 필요 2줄 | "코드 확인 필요" | "확정(결정기록 3-3 E)"로 바꾸고 확인 필요 줄 삭제 | 혜리 |
| ④ | **D** 초안 만료 = 고정지출 **또는 저축 최소 금액**이 바뀔 때만 | chat.md 작업 진행 확인 '초안 만료(★10)' | "고정지출이 바뀌면" | "고정지출 · 정기 저축(최소 금액)이 바뀌면". 화면 문구도 "고정지출이나 저축이 바뀌어서…" | 혜리 |
| ⑤ | **D** 분류 변경 · 계좌 선택 변경으로는 만료 안 함 | transactions.md 분류 수정 확인 필요 마지막 줄 / finance-connect.md 계좌 선택 저장 확인 필요 마지막 줄 | "만료시킬지 → 김나은" | "만료 안 함(결정기록 3-3 D)". 줄 삭제 또는 결정으로 옮김 | 혜리 |
| ⑥ | **B** 같은 구매는 **카드 승인이 대표** | transactions.md '같은 구매가 두 번 들어올 때 (안)' · 확인 필요 첫 줄 | "(안)", "확정 → 김나은" | "(안)" 지우고 확정 표시, 확인 필요 줄 삭제 | 혜리 |
| ⑦ | **F** 연결 해제 때 고정지출 · 정기 저축을 **같이 그만할지 물어봄**(기본 체크 안 함) | finance-connect.md 연결 해제 본문 · 확인 필요 마지막 줄 | "고정지출은 유지" · "납부 예정 그대로 둘지 → 김나은" | 확인 창에서 체크한 것만 기존 중지 API(22 · 28)로 중지 → 이번 주기 미납 납부 예정도 취소. 나머지는 유지 + "연결 해제됨 · 다시 고르기". **새 API 없음** | 혜리 |
| ⑧ | **A** (목록 문서) | [API 명세 목록](../머니쿠션_API_명세_목록_2026-10-21.md) 7번 | "기간이 없으면 이번 급여주기" | 달력월 + 같은 날짜까지 비교로 고침 — **이 브랜치에서 고침** | 나은 ✅ |

### 4-2. 문서끼리 다른 것

| # | 무엇이 다른가 | 어디 | 고치는 방향 | 담당 |
|---|---|---|---|---|
| ① | **소비 분석 카드 칸이 두 가지** — chat.md는 `scope: "all" \| "category"`(문자) · `prevTotal`, spending-analysis.md는 `scope: { accounts, cards, category }`(객체) · `previousTotal` · `coverage` · `comparison` · `topSpending` · `categories[].previousAmount` | chat.md 소비 분석 카드 ↔ spending-analysis.md A-2 | ✅ **10/22 결정 (결정기록 J)** — **chat.md 하나로** 합침. 세진 안에서 `topSpending`(가장 많이 쓴 곳) · `comparison.available/reason`(비교 못 할 때, 1차 `reason` = `not_requested` · `previous_incomplete`)만 가져옴. 이름은 `previousTotal` · `previousAmount`처럼 줄이지 않음. `coverage`(자료 빠진 날짜) · `scope` 객체는 **1차 제외** | 혜리 · 세진 |
| ② | **선택 저장 규칙이 다름** — 계좌: 키 `selectedAccountIds`, 보낸 목록 = 전체 선택(빠진 건 해제), 빈 배열 400. 카드: 키 `cardIds`, 빠진 카드 자동 해제 **안 함**, 빈 배열 미정 | finance-connect.md 계좌 선택 저장 ↔ cards.md 7 | ✅ **10/22 결정 (결정기록 K)** — **같은 규칙**: 보낸 목록 = 전체 선택(빠진 건 해제), 키 `selectedCardIds`. 빈 배열만 다르게 — 계좌는 1개 이상, 카드는 0개 허용("카드는 나중에") | 혜리 · 세진 |
| ③ | **동의 저장 모양이 다름** — 회원가입 `consents[]`: `purpose`(`terms` · `privacy`) · `documentVersion` · **`agreed`**. 금융 동의 `consents[]`: `purpose`(`finance_inquiry`) · **`productScope`** · `documentVersion`, 동의한 것만 보냄(`agreed` 없음) | auth.md 2 ↔ finance-connect.md 동의 저장 | ✅ **10/22 결정 (결정기록 L)** — 한 모양으로: **동의한 것만 보내고 `agreed` 칸 없앰**, 금융 동의만 `productScope` 추가. 버전 표기는 **`v1`**(finance-connect.md의 `2026-10-v1` → `v1`) | 세진 · 혜리 |
| ④ | **해제된 것 보여 주기가 다름** — 계좌 후보 · 연결 목록은 `disconnected`를 **빼고** 줌, 카드 후보는 `status`에 `disconnected`가 **올 수 있음** | finance-connect.md ↔ cards.md 6 | ✅ **10/22 결정 (결정기록 M)** — 후보 목록은 둘 다 **해제된 연결 것은 뺌**(새로 고를 수 없으니까). `disconnected`는 고정지출 · 거래처럼 이미 연결된 것을 보여 줄 때만 | 세진 |
| ⑤ | 링크 · 문구 | finance-connect.md 머리말 `cards-loans.md` / 선택 불가 문구 "선택할 수 없는 계좌가 있어요." | `cards.md` · `loans.md`로 / API_RULES 문구 "선택할 수 없는 계좌예요."로 통일 | 혜리 |

### 4-3. 잘 맞는 것 (확인 완료)

- 로그인 안 됨 = **403 `not_authenticated`** — API가 있는 9개 문서 모두 (savings.md 실패 표에 코드를 적어 넣음, 이 브랜치)
- 에러 `fields`는 칸 오류 때만 · 표에 없는 칸 400 "이 칸은 보낼 수 없어요." · 주소 ID 404 / 본문 ID 400(같은 문구, 전체 미저장)
- 카테고리 대문자 코드 + `categoryName` (transactions · chat · fixed)
- 계좌 · 카드 객체 `{ accountId | cardId, displayName, status }` (transactions · cards · loans · fixed · home · savings)
- 급여 주기 = 설정한 월급날 기준(H-01) — cards · loans · spending-analysis · home · chat 모두 같음
- 같은 요청 두 번 = 성공(204 · 같은 응답) — 해제 · 중지 · 분류 수정 · 선택 저장
- 비공개 값(`connectedId` · 계좌번호 원문 · 비밀번호) 응답에 없음

---

## 5. 10/24까지 정할 것

> ⚠️ = 개발(10/25~)을 바로 막는 것

| # | 정할 것 | 왜 필요한가 | 누가 |
|---|---|---|---|
| ① ✅ | ~~소비 분석 카드 칸 하나로~~ (4-2 ①) → **10/22 결정 J**, 문서 반영만 | 챗봇 화면 · 집계 함수가 같은 칸을 써야 함 | 혜리 · 세진 |
| ② ⚠️ | **대출 보완(15) 보내는 값** — 월 상환액 · 납부일 · 상환 방식 중 무엇을, 어떤 범위로 | 지금은 요청 칸이 없어서 개발 불가 | 세진 · 나은 |
| ③ ✅ | ~~선택 저장 규칙 맞추기~~ (4-2 ②) → **10/22 결정 K**, 문서 반영만 | 계좌 · 카드 선택 화면이 같은 흐름 | 세진 |
| ④ | ~~동의 저장 모양 맞추기~~ (4-2 ③) → **10/22 결정 L** · **회원가입 중복 방지 방법**은 아직 | 동의 표 하나에 저장 | 세진 · 혜리 |
| ⑤ | **외부 조회 실패 에러** — CODEF 실패 · 동의 안 함 · 다시 연결 필요의 상태 코드 · `code` | 계좌 · 카드 · 대출 3개 문서가 모두 "미정"으로 둠 | 혜리 · 나은 |
| ⑥ | **H-08** 온보딩 진행 단계를 어느 API가 주나 (로그인 응답 vs `/me/`) | 로그인 뒤 홈 / 온보딩 이동 | 세진 · 나은 |
| ⑦ | **H-04 · C-17** 홈 '꼭 나갈 돈'에 카드 청구서 · 대출 넣는 규칙, 첫 청구서 선차감 | 홈 `mustPay`에 `card_bill` · `loan` 추가 | 세진 → 나은 |
| ⑧ | 소비 분석에 고정지출(월세 등) 넣을지 — 추천: 넣음(생활비 사용액에선 뺌) | 소비 분석 총액 | 팀 |
| ⑨ | 감지 로직 담당 (고정지출 · 저축 후보 · 대출 · 카드 매칭) | 혜리에게 몰림 | 팀 |

각 문서 끝의 '확인 필요'에 있는 나머지는 대부분 **구현하면서 정해도 되는 것**(문구 · 최대 개수 · 페이지 나누기 · CODEF 실제 값 확인)이다.

---

## 6. 남은 API (이번 취합 밖)

| 묶음 | 개수 | API | 기다리는 것 | 담당(제안) |
|---|---|---|---|---|
| 내 정보 · 온보딩 · 예산 | **14** | 내 정보 GET · PUT, 급여 GET · PUT, 방향 PUT, 지킬 돈 GET · PUT, 추천 예산 POST, 카테고리 예산 GET, 금액 고정 PATCH, 예산 승인 POST, 쿠션 GET · PATCH, 비정기 준비금 GET | 예산 ML 결과 | 나은 |
| 인증 | 1 | 비밀번호 다시 설정 | 본인 확인 방법 | 세진 |
| 금융 연결 | 2 | 금융기관 인증 · 새 연결, 다시 불러오기 | 인증 방식 · 실패 처리 | 혜리 |
| 챗봇 | 1 | 예산 조정안 승인 | 조정안 ↔ 예산 관계 | 혜리 |
| 2 · 3차 | 4 | 소비 분석 화면, 돌발 지출, 알림, 큰 지출 상담 | 범위 재검토 | — |

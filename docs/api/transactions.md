# 거래 API 명세

> 담당: 김혜리 · 명세 상태: 초안 · 개발 상태: 거래 목록 = 임시 데이터(`backend/finance/mock.py`), 나머지 = 미구현
> 공통 규칙은 `docs/API_RULES.md`, 결정 사항은 `docs/결정기록_2026-10-21.md`를 따른다 (camelCase · 원 단위 정수 · UUID · 세션 쿠키 + CSRF · 에러 모양)
> 카테고리: 2026-10-20 팀 분류 기준(10개 + 기타). 아직 판단하지 못한 거래는 `UNCLASSIFIED`이고 화면에는 '확인 필요'로 보인다. 거래 분류 ML이 나와도 아래 요청·응답 모양은 바뀌지 않는다.
> 10/22 반영: 결정기록 ★ 1·2·3·4·7·8·9·10·12·13·14 · 0번 체크리스트 · **H-03 확정**(확인 필요 거래 전체 개수, 최신순) · **H-07 확정**(결정기록 3-1 코드표)

**카테고리 코드 (결정기록 1번, DB `category.category_code`와 같음)**

| 코드 | 화면 이름 | 코드 | 화면 이름 |
|---|---|---|---|
| `FOOD` | 식비 | `BEAUTY` | 뷰티·미용 |
| `HOUSING_TELECOM` | 주거·통신 | `EDUCATION` | 교육 |
| `TRANSPORT_CAR` | 교통·차량 | `CULTURE_LEISURE` | 문화·여가 |
| `HOUSEHOLD` | 생활용품 | `TRAVEL_LODGING` | 여행·숙박 |
| `FASHION_SHOPPING` | 패션·쇼핑 | `ETC` | 기타 |
| `MEDICAL_HEALTH` | 의료·건강 | `UNCLASSIFIED` | 확인 필요 (고를 수 없음) |

저축 · 투자 · 대출 상환 · 카드값은 카테고리가 아니다.

**공통으로 쓰는 값**

| 값 | 모양 | 뜻 |
|---|---|---|
| 카테고리 | `"category": "FOOD"`, `"categoryName": "식비"` | 키는 `category`, 값은 위 코드. 응답에는 `categoryName`도 준다. 확인 필요 거래는 `"UNCLASSIFIED"` / `"확인 필요"` |
| 분류 상태 `classifyState` | `confirmed` · `suggested` · `needs_review` | 사용자가 확정 / 규칙·ML이 정함 / 확인 필요(미분류). DB `confirmation_state`와 같음(H-07 확정). `category = UNCLASSIFIED`이면 항상 `needs_review` |
| 결제 수단 `paymentAccount` · `paymentCard` | `{ "accountId", "displayName", "status" }` · `{ "cardId", "displayName", "status" }` 또는 `null` | `API_RULES.md` '계좌 · 카드 표시'와 같음. 카드 승인 거래는 `paymentCard`만, 계좌 거래는 `paymentAccount`만 채우고 다른 쪽은 `null`. `status`: `connected` · `relogin`(다시 연결 필요) · `disconnected` (★1). `displayName`은 서버가 만든다 |
| 취소 `cancel` | `{ "state": "partial", "amount": 12000 }` | `state`: `none` · `partial` · `full`, `amount`: 취소된 금액(없으면 0) |
| 돈의 출처 `funding` | `living` · `fixed` | 생활비에서 쓴 돈 / 고정비로 연결된 돈(★8, `spending_allocation.funding_bucket`). `fixed`면 생활비 카테고리 사용액에서 빠진다 |

---

## 거래 목록 보기

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 하단 탭 [거래]를 열 때, 카테고리 칩(전체·식비·…·확인 필요)을 누를 때 |
| 어디로 | `GET /api/finance/transactions/` |
| 로그인 필요 | O |
| 명세 상태 | 초안 |

**보내는 값 (주소 뒤 `?` 조건, 모두 선택)**

| 이름 | 타입 | 필수 | 범위·설명 | 예시 |
|---|---|---|---|---|
| `category` | 문자 | X | 카테고리 코드(`UNCLASSIFIED` = 확인 필요만). 없으면 전체 | `FOOD` |
| `from` | 날짜 | X | 시작일(포함). 없으면 **이번 달 1일**(달력월). 단 `category=UNCLASSIFIED`이면 기본값 없음(**전체 기간**) | `2026-10-01` |
| `to` | 날짜 | X | 종료일(포함). 없으면 **오늘** | `2026-10-31` |

```
GET /api/finance/transactions/?category=FOOD&from=2026-10-01&to=2026-10-31
```

> **기간은 달력월 기준이다 (급여 주기 아님).** 거래 화면 제목이 '10월'처럼 달을 보여 주기 때문이다. 홈의 예산·남은 생활비는 **급여 주기** 기준이다. 급여 주기는 **설정한 월급날에 바뀐다**(★14, home.md H-01). 그래서 같은 날 두 화면의 기간과 금액이 다를 수 있다. 홈처럼 이번 주기로 보고 싶으면 `from`·`to`에 주기 시작일·오늘을 보내고, 이때 `period.basis`는 `custom`이 된다.

**받는 값 (200)** — 최신 거래가 먼저(거래일 → 거래 시각 순, 시각이 없으면 같은 날의 맨 뒤). 날짜별 묶음과 날짜 합계는 화면에서 만든다.

| 이름 | 타입 | 뜻 |
|---|---|---|
| `period` | 객체 | 실제로 조회한 기간 `{ "from", "to", "basis" }` |
| `period.basis` | 문자 | `month`(달력월, 기본) · `custom`(`from`·`to`를 보낸 경우) · `all`(`category=UNCLASSIFIED`에 기간을 안 보낸 경우 — 전체 기간, 이때 `period.from`은 가장 오래된 확인 필요 거래일, 없으면 `null`) |
| `summary.spentTotal` | 정수 | **이번 응답 `items`의 소계**(원) = `netAmount` 합. `category`를 걸면 그 카테고리만의 합 |
| `summary.needsReviewCount` | 정수 | **이번 응답 `items` 중** '확인 필요' 거래 수 |
| `items` | 목록 | 거래 (평평한 목록, 날짜로 묶지 않음) |
| `items[].transactionId` | UUID | 거래 번호 |
| `items[].occurredOn` | 날짜 | 거래일 |
| `items[].occurredTime` | 시각 문자 / null | 거래 시각 `"14:32"` (기관이 주지 않으면 `null`) |
| `items[].merchantName` | 문자 | 표시 이름. 카드 = 가맹점명, 계좌 = 적요 |
| `items[].amount` | 정수 | 원거래 금액(원, 0 이상) |
| `items[].cancel` | 객체 | 취소 정보(위 공통 값) |
| `items[].netAmount` | 정수 | 실제 소비 금액 = `amount` − 취소액 (전체 취소면 0) |
| `items[].category` | 문자 | 카테고리 코드 |
| `items[].categoryName` | 문자 | 카테고리 화면 이름 |
| `items[].classifyState` | 문자 | 분류 상태 |
| `items[].paymentCard` | 객체 / null | 결제한 카드 `{ cardId, displayName, status }` |
| `items[].paymentAccount` | 객체 / null | 거래한 계좌 `{ accountId, displayName, status }` |
| `items[].funding` | 문자 | `living` · `fixed` (위 공통 값) |
| `items[].fixedExpenseId` | UUID / null | 이 거래가 연결된 고정지출(★8·13). 없으면 `null` |

> **`summary`는 필터가 걸린 목록의 소계일 뿐이다.** 홈의 '남은 생활비'·'이번 주기 사용액', 예산 화면의 카테고리 사용액과는 **다른 값**이다. 그 값들은 급여 주기 기준이고, 고정비(`funding: fixed`)를 빼는 등 예산 규칙이 들어간 계산이라 이 숫자를 가져다 쓰지 않는다. 이 소계에는 `funding: fixed` 거래도 들어 있다. '확인 필요' 칩과 홈 알림에 붙일 건수는 아래 H-03 기준(기간 없이 전체)이다.

```json
{
  "period": { "from": "2026-10-01", "to": "2026-10-11", "basis": "month" },
  "summary": { "spentTotal": 24800, "needsReviewCount": 1 },
  "items": [
    { "transactionId": "4c0f…", "occurredOn": "2026-10-11", "occurredTime": "18:40", "merchantName": "무신사 스토어", "amount": 89000,
      "cancel": { "state": "full", "amount": 89000 }, "netAmount": 0,
      "category": "FASHION_SHOPPING", "categoryName": "패션·쇼핑", "classifyState": "suggested",
      "paymentCard": { "cardId": "77ab…", "displayName": "신한 Mr.Life · ****5678", "status": "connected" }, "paymentAccount": null,
      "funding": "living", "fixedExpenseId": null },
    { "transactionId": "e2a1…", "occurredOn": "2026-10-11", "occurredTime": "12:10", "merchantName": "김밥천국 역삼점", "amount": 18000,
      "cancel": { "state": "none", "amount": 0 }, "netAmount": 18000,
      "category": "FOOD", "categoryName": "식비", "classifyState": "suggested",
      "paymentCard": { "cardId": "1c90…", "displayName": "신한 Deep Dream · ****1234", "status": "connected" }, "paymentAccount": null,
      "funding": "living", "fixedExpenseId": null },
    { "transactionId": "9d3b…", "occurredOn": "2026-10-09", "occurredTime": "08:02", "merchantName": "GS25 역삼점", "amount": 6800,
      "cancel": { "state": "none", "amount": 0 }, "netAmount": 6800,
      "category": "UNCLASSIFIED", "categoryName": "확인 필요", "classifyState": "needs_review",
      "paymentCard": { "cardId": "1c90…", "displayName": "신한 Deep Dream · ****1234", "status": "connected" }, "paymentAccount": null,
      "funding": "living", "fixedExpenseId": null }
  ]
}
```

- 기간 안에 거래가 없으면 `items: []`, `spentTotal: 0` (200).

**목록에 넣는 거래 (안)**

- **출금 중 소비 거래만** 넣는다. 저축 · 투자 · 대출 상환 · 카드값(카드대금 납부)은 카테고리가 아니라서 넣지 않는다.
- 고정지출로 연결된 거래(월세 출금, 넷플릭스 카드 결제 등)는 **목록에 넣고** `funding: fixed`로 표시한다(★8). 화면은 '고정' 배지 등으로 구분할 수 있다.

**같은 구매가 두 번 들어올 때 (중복 집계 방지, 안)**

체크카드 결제 1만 원은 **카드 승인**과 **계좌 출금**으로 두 번 들어올 수 있다. DB는 둘을 `transaction_link`(`relation_type = SAME_PURCHASE`)로 잇는다.

- **대표 소비 = 카드 승인 거래.** 가맹점명이 있어서 분류할 수 있기 때문이다.
- `SAME_PURCHASE`로 이어진 **계좌 출금 쪽은 목록에서 빼고**, `summary`와 챗봇 소비 집계에서도 뺀다. 같은 구매는 한 번만 센다.
- 아직 연결되지 않은 계좌 출금은 그대로 보이고 합계에 들어간다. 연결이 확인되면 사라진다.
- 신용카드 **대금 납부**(계좌 → 카드사)는 카드값이라 소비 목록·합계에 넣지 않는다(이미 카드 승인으로 셌음).

**정해진 것 (10/21 나은)**

- 목록 모양 `{ items, period, summary }`는 공통 규칙에 맞다(결정기록 0번: 옆에 `period`·`summary`·`next` 추가 OK).
- 임시 구현(`finance/api.py`의 `month`·`recent`·`days` 모양)은 이 명세 PR이 합쳐진 뒤 `TransactionsPage.jsx`와 **같은 PR에서** 함께 바꾼다(따로 바꾸면 그 사이 화면이 깨짐).

**실패하면**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| 모르는 카테고리 코드 | 400 | `invalid` | (개발 중 버그) `fields.category` |
| 날짜 형식이 틀리거나 `from`이 `to`보다 늦음 | 400 | `invalid` | "기간을 다시 골라 주세요." (`fields.from`) |
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 |

**H-03 확정 (10/22): 홈 '확인할 거래 수' = 확인 필요 거래 전체 개수, 최신순**

| 조건 | 내용 |
|---|---|
| 대상 거래 | 위 '목록에 넣는 거래'와 같음(출금 중 소비 거래 · 카드값 제외 · `SAME_PURCHASE` 계좌 출금 제외) |
| 분류 | `category = UNCLASSIFIED` (`classifyState: needs_review`) |
| 기간 | **제한 없음 (전체 기간).** 지난 주기 · 지난달에 처리 못 한 거래도 사라지지 않고 남는다 |
| 정렬 | **최신순** (거래일 → 거래 시각) |
| 같은 숫자를 목록에서 보려면 | `GET /api/finance/transactions/?category=UNCLASSIFIED` (기간 안 보냄)의 `summary.needsReviewCount` = 홈 숫자 |

- 홈의 [확인할 거래 N건 ›]과 거래 화면의 '확인 필요' 칩은 모두 위 요청을 쓴다. 그래야 숫자와 목록이 같다.
- `category=UNCLASSIFIED`에 기간을 안 보내면 전체 기간(`period.basis: all`), 기간을 보내면 그 기간만(`custom`). 다른 카테고리 · 전체 보기의 기본 기간은 그대로 달력월이다.

**확인 필요**

- **같은 구매 중복 기준**(위 안: 카드 승인이 대표, `SAME_PURCHASE` 계좌 출금은 목록·합계에서 제외)을 거래·챗봇·홈·예산 공통 집계 규칙으로 확정 → 김나은
- 목록에 넣을 거래 범위: 이체 · 입금(급여)을 볼 수 있는 화면이 필요한지, 거래 유형 필터가 필요한지 → 팀 회의
- 목록 나누기(페이지, `next`) 필요 여부: 3개월치 거래 수를 보고 결정 → 김혜리. 특히 `category=UNCLASSIFIED`는 전체 기간이라 오래 쓰면 길어질 수 있음

---

## 거래 한 건 보기

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 거래 목록에서 거래 하나를 눌러 상세 화면(또는 '확인 필요' 카드)을 열 때 |
| 어디로 | `GET /api/finance/transactions/<uuid:transaction_id>/` |
| 로그인 필요 | O |
| 명세 상태 | 초안 |

**보내는 값** — 없음 (주소의 ID = `transactionId`)

**받는 값 (200)** — 목록의 한 줄(위 `items[]`)에 아래 칸을 더한다

| 이름 | 타입 | 뜻 |
|---|---|---|
| `direction` | 문자 | `out`(출금) · `in`(입금). DB 코드와 같음(H-07 확정) |
| `transactionType` | 문자 | 거래 유형. DB 코드와 같음(H-07 확정) |
| `installmentMonths` | 정수 / null | 할부 개월(일시불 0, 계좌 거래 `null`) |
| `suggestion` | 객체 / null | 서비스가 제안한 분류. 사용자가 이미 확정했으면 `null` |
| `suggestion.candidates` | 목록 | 후보 카테고리, 확신도 높은 순 `[{ "category", "categoryName" }]` |
| `suggestion.reason` | 문자 / null | 확인 필요 이유(사용자에게 보여 줄 문장) |
| `classifiedBy` | 문자 / null | 분류 출처: `user` · `rule` · `ml` · `llm`. DB `classification_source`와 같음(H-07 확정) |
| `updatedAt` | 시각 | 마지막 수정 시각 |

```json
{
  "transactionId": "9d3b…", "occurredOn": "2026-10-09", "occurredTime": "08:02", "merchantName": "GS25 역삼점", "amount": 6800,
  "cancel": { "state": "none", "amount": 0 }, "netAmount": 6800,
  "category": "UNCLASSIFIED", "categoryName": "확인 필요", "classifyState": "needs_review",
  "paymentCard": { "cardId": "1c90…", "displayName": "신한 Deep Dream · ****1234", "status": "connected" }, "paymentAccount": null,
  "funding": "living", "fixedExpenseId": null,
  "direction": "out", "transactionType": "spending", "installmentMonths": 0,
  "suggestion": {
    "candidates": [ { "category": "FOOD", "categoryName": "식비" }, { "category": "HOUSEHOLD", "categoryName": "생활용품" } ],
    "reason": "편의점은 여러 물건을 팔아서 무엇을 샀는지 알 수 없어요."
  },
  "classifiedBy": null,
  "updatedAt": "2026-10-09T08:05:00+09:00"
}
```

**실패하면**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| 없는 거래 · 남의 거래 · **목록에 안 나오는 거래**(★3: `SAME_PURCHASE`로 빠진 계좌 출금, 카드값 등) | 404 | `not_found` | "거래를 찾을 수 없어요." |
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 |

**확인 필요**

- 후보의 확신도 숫자(`confidence`)를 화면에 보낼지. DB에는 있지만 사용자에게 숫자를 보여 줄지 정하지 않음 → 팀 회의
- 분류 근거(예: "상가정보 업종: 한식")를 보여 줄지 — 목록 3번 '분류 근거는 확정된 범위만' → 거래 분류 ML 확정 후 김혜리
- 카드 승인번호·거래 후 잔액 등 원천 상세를 보여 줄지 → 팀 회의
- 취소 거래와 원거래 연결(`transaction_link`)을 상세에 보여 줄지 → 김혜리

---

## 거래 분류 수정

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 거래 상세 또는 '확인 필요' 카드에서 카테고리를 고르고 [저장]할 때 |
| 어디로 | `PATCH /api/finance/transactions/<uuid:transaction_id>/` |
| 로그인 필요 | O |
| 명세 상태 | 초안 |

**보내는 값** — 보낼 수 있는 칸은 아래 하나뿐이다.

| 이름 | 타입 | 필수 | 범위·설명 | 예시 |
|---|---|---|---|---|
| `category` | 문자 | O | 10개 카테고리 + `ETC`(기타) 중 하나. `UNCLASSIFIED`·`null`은 보낼 수 없음 | `"FOOD"` |

```json
{ "category": "FOOD" }
```

- 금액·날짜·가맹점명·`classifyState`·`funding` 등 표에 없는 칸을 보내면 **400 "이 칸은 보낼 수 없어요."** (★2)
- 이미 같은 카테고리로 확정된 거래에 같은 값을 다시 보내면 **성공(200)** 으로 같은 결과를 돌려준다(결정기록 0번 '같은 요청 두 번').
- 저장하면 이 거래는 사용자 확정(`classifyState: confirmed`, `classifiedBy: user`)이 되고, **이후 같은 가맹점의 새 거래**는 이 카테고리를 먼저 쓴다(`finance/merchant_rules.py`의 `remembered_category()`).
- **단, 다품목 가맹점은 기억하지 않는다.** 편의점·슈퍼마켓·대형마트·종합 온라인몰처럼 결정표 D1·D16에서 `UNCLASSIFIED`(사용자 선택)로 정한 가맹점(다품목 브랜드 사전에 있는 가맹점)은, 사용자가 이번 거래를 식비로 골라도 **다음 거래는 다시 `UNCLASSIFIED`** 로 들어온다. GS25 도시락을 식비로 고른 기록이 다음 생필품 구매까지 식비로 만들지 않게 하기 위해서다.
- 고정지출로 연결된 거래(`funding: fixed`)도 카테고리는 바꿀 수 있다. 돈의 출처(고정비)는 그대로다.
- 이 요청은 같은 값을 두 번 보내도 결과가 같아서 요청 번호(`Idempotency-Key`)를 쓰지 않는다(★4: 새로 만들기 · 승인 요청만).

**받는 값 (200)** — 바뀐 거래 한 건(`GET /api/finance/transactions/<id>/`와 같은 모양)

```json
{
  "transactionId": "9d3b…", "merchantName": "GS25 역삼점",
  "category": "FOOD", "categoryName": "식비", "classifyState": "confirmed", "classifiedBy": "user",
  "suggestion": null, "updatedAt": "2026-10-22T09:12:00+09:00", "…": "…"
}
```

**실패하면**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| `category`가 없거나 `null`, 모르는 코드, `UNCLASSIFIED` | 400 | `invalid` | "카테고리를 다시 골라 주세요." (`fields.category`) |
| 표에 없는 칸을 보냄 (★2) | 400 | `invalid` | `fields.<그 칸>: ["이 칸은 보낼 수 없어요."]` |
| 없는 거래 · 남의 거래 · 목록에 안 나오는 거래 (★3) | 404 | `not_found` | "거래를 찾을 수 없어요." |
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 |
| 저장 보안값(CSRF) 없음 | 403 | `permission_denied` | "다시 시도해 주세요." |

**확인 필요**

- 같은 가맹점의 **이미 있는 '확인 필요' 거래**도 한꺼번에 바꿀지(지금 안: 새 거래에만 반영). 바꾼다면 응답에 바꾼 건수 `sameMerchantUpdatedCount` 추가 → 팀 회의
- 다품목 가맹점에서 이전 선택을 자동 적용은 하지 않되, 선택지 맨 위에 '추천'으로만 보여 줄지 → 팀 회의
- 현재 `remembered_category()` 코드는 다품목 가맹점을 거르지 않음. 다품목 브랜드 사전 확정 후 제외 조건 추가 → 김나은(코드) · 김혜리(사전)
- 확정한 분류를 다시 '확인 필요'로 되돌리는 기능이 필요한지 → 팀 회의
- 분류를 바꾸면 그 주기의 카테고리 사용액·예산 화면 숫자도 바뀜. 예산 쪽 다시 계산 시점 → 김나은 (★10: 고정지출 변경으로 초안이 만료되는 것과 같은 방식으로, 분류 변경도 승인 전 초안을 만료시킬지 포함)

---

## 거래 연결 · 감지 구현 메모 (거래 담당 김혜리, API 응답 모양은 바꾸지 않음)

고정지출 명세(`fixed.md`)로 정해진 규칙 중 **거래 쪽 코드**가 지켜야 할 것. 결정기록 ★ 표 번호.

| # | 규칙 | 거래 쪽 구현 |
|---|---|---|
| ★7 | '이 결제가 맞나요?'는 **카드만** (1차) | **계좌 출금은 납부 예정(`PaymentDue`)과 날짜 ±3일 · 금액으로 자동 연결**한다. 비슷한 출금이 여러 건이면 자동으로 고르지 않고 사용자가 고르게 남긴다 |
| ★8 | 연결된 카드 거래는 **고정비** | 사용자가 [맞아요]로 카드 거래를 고정지출에 연결하면 그 거래의 `spending_allocation.funding_bucket = fixed`. 목록에서는 `funding: fixed` |
| ★9 | 자동 발견 후보는 **근거 거래의 계좌 · 카드로 채움** | 감지 로직이 고정지출 후보를 만들 때 근거 거래의 결제 수단(계좌 ID 또는 카드 ID)도 같이 저장한다 |
| ★12 | 납부 예정은 **이번 주기 1건씩** | 납부 예정을 **만드는** 원칙은 이번 주기 1건씩이지만, **매칭 후보**는 주기로 자르지 않는다. 출금일 ±3일 안에 있는 **아직 안 낸 납부 예정**이면 지난 주기 것도 후보에 넣는다. 예) 급여일 25일, 10/24 예정 출금이 10/26에 나가면 지난 주기(9/25~10/24)의 미납 납부 예정과 짝짓는다. 이미 낸 회차는 후보가 아니다. 다음 주기 납부 예정이 아직 없으면(홈 · 예산을 처음 열 때 생김, 배치 없음) 그때 만든 뒤 다시 맞춘다 |
| ★13 | 결제 하나는 **고정지출 하나에만** | 이미 다른 고정지출에 연결된 카드 결제는 '맞나요?' 후보에서 뺀다. 보내면 400 |
| ★14 | '이번 주기' = 설정한 월급날 기준 | 매칭 · 집계에서 '이번 주기'는 모두 H-01 주기(설정한 월급날 ~ 다음 월급날 전날) |

- 감지 조건(결정기록 3번): 같은 받는 곳 · 한 달 1건 · 매달 같은 날 ±3일 · 같은 금액(변동 청구 ±10%) · 3달 연속. 등록된 대출과 맞는 출금은 대출 상환으로, 내 계좌끼리 정기 이체는 저축 후보로.

**H-07 DB 코드 (10/22 확정, 결정기록 3-1 코드표 · DB 반영은 김나은)**

API 값과 DB 값을 같게 둔다(소문자 영어).

| 칸 | 코드 | 상태 |
|---|---|---|
| `financial_transaction.direction` | `in`(입금) · `out`(출금) | ✅ 확정 (10/22) |
| `financial_transaction.transaction_type` | `spending`(소비) · `transfer`(이체) · `own_transfer`(내 계좌 간 이체) · `card_payment`(카드값) · `saving`(저축) · `loan_repayment`(대출 상환) · `income`(입금) | ✅ 확정 (10/22) |
| `financial_transaction.confirmation_state` | `needs_review` · `suggested` · `confirmed` (API `classifyState`와 같음) | ✅ 확정 (10/22) |
| `classification_source` | `user` · `rule` · `ml` · `llm` (API `classifiedBy`와 같음, `merchant_rules.USER_SOURCE = "user"` 유지) | ✅ 확정 (10/22) |

- 월급 찾기(H-01)는 `direction = in` + 관리 대상 입출금 계좌 + 내 계좌끼리 이체 제외.
- **내 계좌끼리 이체**: 적금 · 청약으로 보낸 이체는 저축 확인 화면에서 **저축으로 확인되면 `saving`**, 그 밖의 내 계좌끼리 이체는 **`own_transfer`** 다. (10/22)
- **카테고리가 `UNCLASSIFIED`이면 `confirmation_state = needs_review`** 다. 둘은 항상 같이 움직인다(카테고리가 정해지면 `suggested` 또는 `confirmed`). (10/22)

`connection.status`(`connected` · `relogin` · `disconnected`)와 `bank_account.account_type`(`demand` · `deposit` · `other`)은 `finance-connect.md`의 H-07을 본다.

# 거래 API 명세

> 담당: 김혜리 · 명세 상태: **1차 확정 (10/24, 결정 AA)** · 개발 상태: 거래 목록 · 한 건 보기 · 분류 수정 = DB 구현 (10/26, `backend/finance/api.py`, 테스트 `finance/tests.py`)
> 공통 규칙은 `docs/API_RULES.md`, 결정 사항은 `docs/결정기록_2026-10-21.md`를 따른다 (camelCase · 원 단위 정수 · UUID · 세션 쿠키 + CSRF · 에러 모양)
> 카테고리: 2026-10-20 팀 분류 기준(10개 + 기타). 아직 판단하지 못한 거래는 `UNCLASSIFIED`이고 화면에는 '확인 필요'로 보인다. 거래 분류 ML이 나와도 아래 요청·응답 모양은 바뀌지 않는다.
> 10/22 반영: 결정기록 ★ 1·2·3·4·7·8·9·10·12·13·14 · 0번 체크리스트 · **H-03 확정**(확인 필요 거래 전체 개수, 최신순) · **H-07 확정**(결정기록 3-1 코드표) · **결정기록 3-3 B·D 확정** · 10/22 결정 V① · W · V · **10/24 결정 AA⑤⑥⑦ (1차 확정)**

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
| `category` | 문자 | X | 카테고리 코드(`UNCLASSIFIED` = 확인 필요만). 없으면 전체. **쉼표로 여러 개**(11/3, 그중 하나면 나옴). 모르는 코드가 하나라도 있으면 400 | `FOOD,CULTURE_LEISURE` |
| `accountId` | UUID | X | 이 계좌의 거래만. 쉼표로 여러 개(11/3). `cardId`와 같이 보내면 **둘 중 하나** | `27d3…` |
| `cardId` | UUID | X | 이 카드의 거래만. 쉼표로 여러 개(11/3) | `38e7…` |
| `from` | 날짜 | X | 시작일(포함). 없으면 **이번 달 1일**(달력월). 단 `category=UNCLASSIFIED` **하나만** 보내면 기본값 없음(**전체 기간**). 다른 카테고리와 같이 보내면 이번 달 | `2026-10-01` |
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
| `summary.spentTotal` | 정수 | **이번 응답 `items` 중 소비 거래(`transactionType: spending`)의 소계**(원) = `netAmount` 합. 입금 · 이체는 넣지 않는다(V①). `category`를 걸면 그 카테고리만의 합 |
| `summary.needsReviewCount` | 정수 | **이번 응답 `items` 중** '확인 필요' 거래 수 |
| `items` | 목록 | 거래 (평평한 목록, 날짜로 묶지 않음) |
| `items[].transactionId` | UUID | 거래 번호 |
| `items[].occurredOn` | 날짜 | 거래일 |
| `items[].occurredTime` | 시각 문자 / null | 거래 시각 `"14:32"` (기관이 주지 않으면 `null`) |
| `items[].direction` | 문자 | `out`(출금) · `in`(입금). DB 코드와 같음(H-07, 10/22 V①로 목록에도 넣음) |
| `items[].transactionType` | 문자 | 목록에 나오는 값: `spending`(소비) · `income`(입금) · `transfer`(남에게 보낸 이체). DB 코드와 같음(H-07) |
| `items[].merchantName` | 문자 | 표시 이름. 카드 = 가맹점명, 계좌 = 적요 |
| `items[].amount` | 정수 | 원거래 금액(원, 0 이상) |
| `items[].cancel` | 객체 | 취소 정보(위 공통 값) |
| `items[].netAmount` | 정수 | 실제 소비 금액 = `amount` − 취소액 (전체 취소면 0) |
| `items[].category` | 문자 / null | 카테고리 코드. 입금 · 이체는 `null`(V①) |
| `items[].categoryName` | 문자 / null | 카테고리 화면 이름. 입금 · 이체는 `null` |
| `items[].classifyState` | 문자 / null | 분류 상태. 입금 · 이체는 `null` |
| `items[].paymentCard` | 객체 / null | 결제한 카드 `{ cardId, displayName, status }` |
| `items[].paymentAccount` | 객체 / null | 거래한 계좌 `{ accountId, displayName, status }` |
| `items[].funding` | 문자 / null | `living` · `fixed` (위 공통 값). 입금 · 이체는 `null` |
| `items[].fixedExpenseId` | UUID / null | 이 거래가 연결된 고정지출(★8·13). 없으면 `null` |
| `items[].isSalary` | 불린 | 인정된 월급 입금인지 (R12, 11/06). 사용자가 확인했거나 확인한 기준과 같은 패턴이라 자동 인정된 입금만 `true` — 홈과 같은 저장 근거(`salary_decision`). 판단 없는 후보 · 거절 · 취소 · 내 계좌 이체로 바뀐 거래는 `false`. R12 전 자동 추정은 저장된 판단이 아니라서 `false`로 보인다. 거래 한 건 보기에도 같은 값 |
| `items[].salaryReview` | 문자 또는 `null` | `isSalary`의 근거. `confirmed` 사용자 확인 · `auto` 자동 인정 · 아니면 `null`. 화면 "월급 · 자동 인식" |

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
      "funding": "living", "fixedExpenseId": null, "isSalary": false, "direction": "out", "transactionType": "spending" },
    { "transactionId": "e2a1…", "occurredOn": "2026-10-11", "occurredTime": "12:10", "merchantName": "김밥천국 역삼점", "amount": 18000,
      "cancel": { "state": "none", "amount": 0 }, "netAmount": 18000,
      "category": "FOOD", "categoryName": "식비", "classifyState": "suggested",
      "paymentCard": { "cardId": "1c90…", "displayName": "신한 Deep Dream · ****1234", "status": "connected" }, "paymentAccount": null,
      "funding": "living", "fixedExpenseId": null, "isSalary": false, "direction": "out", "transactionType": "spending" },
    { "transactionId": "9d3b…", "occurredOn": "2026-10-09", "occurredTime": "08:02", "merchantName": "GS25 역삼점", "amount": 6800,
      "cancel": { "state": "none", "amount": 0 }, "netAmount": 6800,
      "category": "UNCLASSIFIED", "categoryName": "확인 필요", "classifyState": "needs_review",
      "paymentCard": { "cardId": "1c90…", "displayName": "신한 Deep Dream · ****1234", "status": "connected" }, "paymentAccount": null,
      "funding": "living", "fixedExpenseId": null, "isSalary": false, "direction": "out", "transactionType": "spending" },
    { "transactionId": "5b7e…", "occurredOn": "2026-10-08", "occurredTime": "19:20", "merchantName": "이하늘", "amount": 30000,
      "cancel": { "state": "none", "amount": 0 }, "netAmount": 30000,
      "category": null, "categoryName": null, "classifyState": null,
      "paymentCard": null, "paymentAccount": { "accountId": "c91d…", "displayName": "국민 급여통장 · ****5678", "status": "connected" },
      "funding": null, "fixedExpenseId": null, "isSalary": false, "direction": "out", "transactionType": "transfer" },
    { "transactionId": "0a42…", "occurredOn": "2026-10-01", "occurredTime": null, "merchantName": "급여", "amount": 2600000,
      "cancel": { "state": "none", "amount": 0 }, "netAmount": 2600000,
      "category": null, "categoryName": null, "classifyState": null,
      "paymentCard": null, "paymentAccount": { "accountId": "c91d…", "displayName": "국민 급여통장 · ****5678", "status": "connected" },
      "funding": null, "fixedExpenseId": null, "isSalary": true, "direction": "in", "transactionType": "income" }
  ]
}
```

- 기간 안에 거래가 없으면 `items: []`, `spentTotal: 0` (200).

**목록에 넣는 거래 (10/22 V① 확정)**

- **소비 거래**(`spending`)와 함께 **입금**(`income`, 월급 등)과 **남에게 보낸 이체**(`transfer`)도 보여 준다.
- 입금 · 이체는 `category: null`이고 **소비 합계(`spentTotal`) · 카테고리 칩 · `needsReviewCount`에 들어가지 않는다.** 카테고리를 걸어 조회하면 나오지 않는다.
- 내 계좌끼리 이체(`own_transfer`) · 저축(`saving`) · 대출 상환(`loan_repayment`) · 카드값(`card_payment`)은 넣지 않는다.
  - **예외 (R11, 11/5)**: 이 기능이 자동 분류했거나(`ownTransferReview: auto`) 사용자가 확인한(`confirmed`) 내 계좌 이체는 **출금 쪽 한 줄만** 넣는다. 그 뒤 정기 저축으로 처리돼 `saving`이 된 출금도 넣는다. 되돌릴 수 있게 하려는 것이고 소비 합계에는 들어가지 않는다.
- 각 줄에 `ownTransferReview`(아래 '거래 한 건 보기' 표)를 같이 준다.
  - **예전 기록 (11/06)**: 11/5 전 규칙이 판단 표시 없이 `own_transfer`로 바꾸고 입금과 이은(`OWN_TRANSFER` 연결) 은행 출금은 DB를 고치지 않고 `ownTransferReview: auto`로 읽어 목록에 넣는다 → 사용자가 [맞아요] · [연결 해제]로 확인 · 정정한다(처음 고를 때 표시를 남김). 연결 없는 `own_transfer` · 표시 없는 `saving`은 넣지 않는다. 점검: `python manage.py own_transfer_legacy_check [--ids]` (읽기 전용).
- 적요에 카드사 이름만 있는 출금은 `card_payment`가 아니라 `transfer`(카드값 후보)로 목록에 남는다. 청구서에 연결될 때 카드값이 된다 (cards.md '11/06 추가').
- 고정지출로 연결된 거래(월세 출금, 넷플릭스 카드 결제 등)는 **목록에 넣고** `funding: fixed`로 표시한다(★8). 화면은 '고정' 배지 등으로 구분할 수 있다.

**같은 구매가 두 번 들어올 때 (중복 집계 방지, 10/22 B 확정)**

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
| `accountId` · `cardId`가 UUID 형식이 아니거나 · 없거나 · 남의 것 (11/3) | 400 | `invalid` | "선택할 수 없는 계좌 · 카드예요." (`fields.accountId` · `fields.cardId`) |
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 |

**H-03 확정 (10/22): 홈 '확인할 거래 수' = 확인 필요 거래 전체 개수, 최신순**

| 조건 | 내용 |
|---|---|
| 대상 거래 | 목록의 **소비 거래**(`transactionType: spending`)만. 입금 · 이체 · 카드값 · `SAME_PURCHASE` 계좌 출금 제외 |
| 분류 | `category = UNCLASSIFIED` (`classifyState: needs_review`) |
| 기간 | **제한 없음 (전체 기간).** 지난 주기 · 지난달에 처리 못 한 거래도 사라지지 않고 남는다 |
| 정렬 | **최신순** (거래일 → 거래 시각) |
| 같은 숫자를 목록에서 보려면 | `GET /api/finance/transactions/?category=UNCLASSIFIED` (기간 안 보냄)의 `summary.needsReviewCount` = 홈 숫자 |

- 홈의 [확인할 거래 N건 ›]과 거래 화면의 '확인 필요' 칩은 모두 위 요청을 쓴다. 그래야 숫자와 목록이 같다.
- `category=UNCLASSIFIED`에 기간을 안 보내면 전체 기간(`period.basis: all`), 기간을 보내면 그 기간만(`custom`). 다른 카테고리 · 전체 보기의 기본 기간은 그대로 달력월이다.

- **거래 유형 필터(소비만 · 입금만 등)는 1차에 없다** — 목록의 '월급' · '이체' 표시로 구분한다 (10/24 AA⑦)

**확인 필요**

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
| `transactionType` | 문자 | 거래 유형. DB 코드와 같음(H-07 확정). `transfer`(남에게 보낸 이체)면 화면에 **[소비로 분류하기]** 를 보여 준다(10/22 W) |
| `installmentMonths` | 정수 / null | 할부 개월(일시불 0, 계좌 거래 `null`) |
| `suggestion` | 객체 / null | 서비스가 제안한 분류. 사용자가 이미 확정했으면 `null` |
| `suggestion.candidates` | 목록 | 후보 카테고리, 확신도 높은 순 `[{ "category", "categoryName" }]` |
| `suggestion.reason` | 문자 / null | 확인 필요 이유(사용자에게 보여 줄 문장) |
| `classifiedBy` | 문자 / null | 분류 출처: `user` · `rule` · `ml` · `llm`. DB `classification_source`와 같음(H-07 확정) |
| `wasTransfer` | 불린 | 원래 이체였다가 소비로 바뀐 거래인지 (11/03). `true`면 화면이 [이체로 되돌리기](`category: null`)를 보여 준다. 고정지출에 연결된 거래는 `true`여도 되돌리기가 400(AA⑤) |
| `ownTransferReview` | 문자 / null | 내 계좌 이체 판단 (R11, 11/5). `transfer` · `own_transfer` · `saving` 출금만, 그 밖은 `null`. `suggested` = 출금 적요가 가입 이름(`nickname`)과 같아 **추정**(유형은 `transfer` 그대로, 화면 [맞아요]/[아니에요]) · `auto` = 규칙이 내 다른 계좌의 입금과 묶어 **자동 분류**(`own_transfer`, 그 뒤 저축 처리되면 `saving`. 확실한 식별이 아니라 규칙 추정 — [맞아요]/[연결 해제]. 기준은 아래 '내 계좌 이체 자동 분류') · `confirmed` = 사용자가 [맞아요](`own_transfer`, [확인 취소]) · `rejected` = 사용자가 [아니에요] · 확인 취소 · 연결 해제(`transfer`). `field_states` 원값은 내보내지 않는다 |
| `ownTransferRecheck` | 불린 | `auto`인데 나중에 들어온 거래로 상대 후보가 여럿이 됐지만, 저축 · 납부 · 고정지출 근거라 자동으로 풀지 않은 연결이면 `true`. 화면은 "다시 확인해 주세요"를 보여 주고 같은 상세에서 [맞아요]/[연결 해제]로 정정한다 (R11) |
| `canApplyToFuture` | 불린 | 화면에 "앞으로 이 가게 거래도 이 카테고리로" 체크박스를 보여 줄지 (10/30). 소비 거래이고 다품목 가맹점(이름 사전, 또는 이 거래나 같은 가맹점의 다른 거래에서 가게 검색 업종이 편의점·마트 등)·페이 충전이 아니면 `true`. 이체는 `false`(받는 사람은 기억하지 않음, W) |
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
  "canApplyToFuture": false,
  "wasTransfer": false,
  "isSalary": false,
  "updatedAt": "2026-10-09T08:05:00+09:00"
}
```

**실패하면**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| 없는 거래 · 남의 거래 · **목록에 안 나오는 거래**(★3: `SAME_PURCHASE`로 빠진 계좌 출금, 카드값 등) | 404 | `not_found` | "거래를 찾을 수 없어요." |
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 |

**정해진 것 (10/22 V)**

- 후보의 확신도 숫자(`confidence`)는 화면에 보내지 않는다.
- 카드 승인번호 · 거래 후 잔액 등 원천 상세는 보여 주지 않는다.

**확인 필요**

- 분류 근거(예: "상가정보 업종: 한식")를 보여 줄지 — 목록 3번 '분류 근거는 확정된 범위만' → 거래 분류 ML 확정 후 김혜리
- 취소 거래와 원거래 연결(`transaction_link`)을 상세에 보여 줄지 → 김혜리

---

## 거래 분류 수정

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 거래 상세 또는 '확인 필요' 카드에서 카테고리를 고르고 [저장]할 때, 이체 상세에서 [소비로 분류하기]를 할 때(10/22 W) |
| 어디로 | `PATCH /api/finance/transactions/<uuid:transaction_id>/` |
| 로그인 필요 | O |
| 명세 상태 | 초안 |

**보내는 값** — 보낼 수 있는 칸은 아래 세 개뿐이다. `category`(+ `applyToFuture`) 또는 `ownTransfer` 하나만 보낸다.

| 이름 | 타입 | 필수 | 범위·설명 | 예시 |
|---|---|---|---|---|
| `category` | 문자 / null | O | 10개 카테고리 + `ETC`(기타) 중 하나. `UNCLASSIFIED`는 보낼 수 없음. `null`은 **원래 이체였던 거래를 다시 이체로 돌릴 때만**(W) | `"FOOD"` |
| `ownTransfer` | 불린 | X | 내 계좌 이체 확인 (R11, 11/5). 다른 칸과 같이 보내면 400. 아래 '내 계좌 이체 확인' | `true` |
| `applyToFuture` | 불린 | X (기본 `false`) | `true`면 **같은 가맹점의 다음 새 거래**도 이 카테고리로 자동 분류 (10/30). `canApplyToFuture: false`인 거래에 `true`를 보내도 400을 내지 않고 재사용하지 않는다 | `true` |

```json
{ "category": "FOOD", "applyToFuture": true }
```

- 금액·날짜·가맹점명·`classifyState`·`funding` 등 표에 없는 칸을 보내면 **400 "이 칸은 보낼 수 없어요."** (★2)
- 이미 같은 카테고리로 확정된 거래에 같은 값을 다시 보내면 **성공(200)** 으로 같은 결과를 돌려준다(결정기록 0번 '같은 요청 두 번').
- 저장하면 이 거래는 사용자 확정(`classifyState: confirmed`, `classifiedBy: user`)이 된다.
- **같은 가맹점의 다음 거래에 쓰기 (10/30 변경)**: `applyToFuture: true`로 저장했을 때만, 이후 같은 가맹점의 새 거래가 이 카테고리를 먼저 쓴다(`finance/merchant_rules.py`의 `remembered_category()`, 출처 `rule`).
    - 기준은 같은 가맹점에서 사용자가 고른 거래 중 **가장 최근 거래**(거래일 순, 같은 날이면 마지막 수정 순)다. 그 거래가 `applyToFuture: true`일 때만 재사용하고, 아니면 재사용하지 않는다. 사용자가 선택을 바꾸면 마지막 선택을 따른다.
    - 새 칸 없이 그 거래의 `field_states.apply_to_future = true`로 남긴다. API 응답에는 내보내지 않는다.
    - 10/30 이전에 확정한 거래는 이 표시가 없어서 재사용되지 않는다.
- **단, 다품목 가맹점·페이 충전은 기억하지 않는다** (`applyToFuture`를 보내도 같음). 편의점·슈퍼마켓·대형마트·종합 온라인몰처럼 결정표 D1·D16에서 `UNCLASSIFIED`(사용자 선택)로 정한 가맹점(다품목 브랜드 사전에 있는 가맹점)은, 사용자가 이번 거래를 식비로 골라도 **다음 거래는 다시 `UNCLASSIFIED`** 로 들어온다. GS25 도시락을 식비로 고른 기록이 다음 생필품 구매까지 식비로 만들지 않게 하기 위해서다.
- 고정지출로 연결된 거래(`funding: fixed`)도 카테고리는 바꿀 수 있다. 돈의 출처(고정비)는 그대로다.
- **고정지출에 연결된 이체 (10/24 AA⑤)**: 월세를 집주인에게 계좌이체로 내는 경우처럼, 남에게 보낸 이체(`transfer`)가 확정된 고정지출의 납부 예정과 **금액 · 날짜(±3일)로 연결되면** 소비로 본다.
    - `transactionType` `transfer` → `spending`, 카테고리는 **그 고정지출의 카테고리**, `funding: fixed`('고정' 표시). 생활비 사용액엔 안 들어가고 소비 분석엔 `fixedAmount`로 들어간다(S).
    - 연결을 풀면 다시 `transfer`(카테고리 · `funding` 비움).
    - 연결은 매칭 입구 6번 `match_payment_dues`(나은)가 한다. 사용자가 PATCH로 카테고리를 바꿔도 연결은 그대로다.
    - 고정지출에 연결된 이체에 `category: null`을 보내면 400 — 고정지출 연결을 먼저 풀어야 이체로 돌아간다.
- **이체를 소비로 바꾸기 (10/22 W)**: 남에게 보낸 이체(`transfer`)에 카테고리를 보내면 `transactionType`이 `spending`이 되고 소비 합계 · 예산에 들어간다(`classifyState: confirmed`, `classifiedBy: user`).
    - `category: null`을 보내면 다시 `transfer`로 돌아간다. **원래 이체였던 거래만** 된다. 원래 소비였던 거래에 `null`을 보내면 400.
    - 이체의 **받는 사람은 기억하지 않는다.** 같은 사람에게 다음에 보낸 이체는 다시 `transfer`로 들어온다(`remembered_category()`를 쓰지 않음).
- 입금(`income`) 거래는 카테고리를 고를 수 없다 → 400 (10/24 AA⑦ 확정, 아래 실패 표).
- **'원래 이체였던 거래' 표시 (10/24 AA⑥)**: 새 칸 없이 거래의 `field_states.original_transaction_type = "transfer"`로 남긴다. W로 소비가 된 이체 · AA⑤로 소비가 된 이체 모두. `category: null`로 되돌릴 수 있는지, 연결을 풀 때 무엇으로 돌릴지 이 값으로 판단한다. 원값은 내보내지 않고, 거래 한 건 보기에 `wasTransfer`(불린)만 준다(11/03).
- **내 계좌 이체 확인 (R11, 11/5)** — `{"ownTransfer": true | false}`
    - `true` [맞아요]: `suggested` · `rejected`인 `transfer` → **이 출금만** `own_transfer`(`confirmed`). 같은 금액 입금 등 다른 거래로 넓히지 않는다. `auto`면 연결 · 유형(`own_transfer` · `saving`)은 그대로 두고 `confirmed`로만 바꾼다.
    - `false` [아니에요] · 확인 취소 · 연결 해제: → `transfer`(`rejected`). 규칙이 이은 입금이 있으면 연결(`OWN_TRANSFER`)을 지우고 입금을 `income`으로 되돌린 뒤 입금에도 `rejected`를 남겨 다시 잇지 않는다. 거절해도 **소비로 바꾸지 않는다** — 소비로 넣으려면 [소비로 분류하기].
    - **저축으로 처리된 출금(`saving`)** 에 `false`: 그 출금을 정기 저축의 근거 이체(`evidence`)에서 빼고 `transfer`로 되돌린다. 저축 자체(상태 · 금액 · 다른 근거)는 그대로이고, 저축 화면의 '근거 이체 수'가 하나 줄어든다. 이 출금은 이후 저축 자동 처리(`match_savings`)에서도 빠진다. 고정지출 근거이기도 하면 400 "고정지출 근거로 쓰인 거래예요. 고정지출 화면에서 먼저 정리해 주세요." 저축 화면에는 거래 한 건을 빼는 기능이 없어서 정정은 거래 상세에서만 한다.
    - 바꿀 수 있는 거래 (잠근 뒤 다시 읽은 **지금 상태** 기준): `suggested` · `rejected`인 `transfer`(사용자 확정 아님), `auto` · `confirmed`인 `own_transfer` · `saving` 은행 출금. 판단 표시가 남아 있어도 지금 유형이 소비 등이면(사용자가 [소비로 분류하기]를 함) `true` · `false` 모두 400 — 오래된 화면의 요청이나 동시 요청이 사용자 분류를 덮지 않는다. 취소 거래 · 납부(카드값 · 대출 · 고정지출 회차)에 붙은 출금 · 고정비로 충당된 출금도 400. 이미 원하는 상태면 200.
    - 사용자 판단은 재수집 · 저장 뒤 처리 · 이름 변경으로 덮어쓰지 않는다. 저장은 `field_states.own_transfer_review`.
    - `own_transfer` · `saving` 거래에 `category`를 보내면 400 — 확인을 먼저 취소한다.
    - 잠금: 회원 → 거래 (저장 뒤 처리와 같은 순서).
- **내 계좌 이체 자동 분류 (R11, 11/5 최종 기준, `finance/transfer_rules.py`)** — 거래 저장 뒤 1단계 `link_basics`
    - **양쪽 거래가 다 있으면 `auto`**: 같은 회원의 서로 다른 계좌 · 출금 ↔ 입금 · 같은 금액 · 시각 차이 **5분 이내**(5분 포함, 날짜와 시각을 합쳐 비교해 자정 넘김도 봄) · **양쪽 대표 적요가 모두 가입 이름**(공백 · 대소문자 정리 후 전체 일치) · 양쪽에서 맞는 상대가 **각각 하나뿐**(상대 거래의 유형과 상관없이 셈). 양쪽 거래와 연결(`OWN_TRANSFER`)을 한 트랜잭션에서 바꾼다.
    - 하지 않는 경우: 시각 없음 · 가입 이름이 비었음 · 적요끼리만 같음("이체" 등) · 부분 일치 · 후보가 여럿 · 취소 · 사용자 확정 · 납부 · 같은 구매 · 이미 이은 거래 · 사용자 판단(`confirmed` · `rejected`)이 있는 거래.
    - **상대 거래가 없거나 모호하면 `suggested`**: 출금 적요가 가입 이름이면 일반 이체(`transfer`) 그대로 추정만 남긴다. 상대 입금이 나중에 들어와 조건을 다 맞추면 `auto`로 올린다.
    - **늦게 온 거래로 다시 보기**: 새 거래가 기존 `auto` 연결의 후보가 되면 그 연결을 다시 본다. 여전히 양쪽 유일이면 둔다. 모호해졌으면 연결을 지우고 출금은 `transfer`(이름이 같으면 `suggested`), 입금은 `income`으로 되돌린다 — 다른 후보에 갈아 붙이지 않는다. 그래서 한꺼번에 받든 나눠 받든 결과가 같다. 사용자가 확인한 연결(`confirmed`)은 그대로. 저축으로 바뀌었거나 저축 · 고정지출 근거 · 납부에 붙은 연결은 풀지 않고 `ownTransferRecheck: true`로 표시한다(정정은 거래 상세).
    - CODEF 은행 거래에는 상대 계좌번호나 양쪽 송금을 잇는 식별값이 없어서(적요 1~4칸뿐) 이 규칙은 **추정**이다. `source_key`(R08)는 쓰지 않는다.
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
| `category`가 없음, 모르는 코드, `UNCLASSIFIED`, 원래 소비였던 거래에 `null` | 400 | `invalid` | "카테고리를 다시 골라 주세요." (`fields.category`) |
| 입금(`income`) 거래에 카테고리를 보냄 (AA⑦) | 400 | `invalid` | "이 거래는 카테고리를 고를 수 없어요." (`fields.category`) |
| 내 계좌 이체(`own_transfer`) · 저축(`saving`)에 카테고리를 보냄 (R11) | 400 | `invalid` | "내 계좌 이체 확인을 먼저 취소해 주세요." (`fields.category`) |
| `ownTransfer`를 바꿀 수 없는 거래 (판단 없음 · 지금 소비 등 다른 유형 · 입금 · 취소 · 납부 연결 · 고정비 충당) | 400 | `invalid` | "내 계좌 이체로 확인할 수 없는 거래예요." (`fields.ownTransfer`) |
| `ownTransfer`를 다른 칸과 같이 보냄 · 불린이 아님 | 400 | `invalid` | `fields.ownTransfer` |
| 표에 없는 칸을 보냄 (★2) | 400 | `invalid` | `fields.<그 칸>: ["이 칸은 보낼 수 없어요."]` |
| 없는 거래 · 남의 거래 · 목록에 안 나오는 거래 (★3) | 404 | `not_found` | "거래를 찾을 수 없어요." |
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 |
| 저장 보안값(CSRF) 없음 | 403 | `permission_denied` | "다시 시도해 주세요." |

**정해진 것 (10/22 V)**

- 같은 가맹점의 이미 있는 거래를 **한꺼번에 바꾸지 않는다** (새 거래에만 반영, `sameMerchantUpdatedCount` 없음).
- 확정한 분류를 '확인 필요'로 **되돌리는 기능은 없다.** (다른 카테고리로 다시 고르는 것은 됨)
- 편의점 · 마트 등 다품목 가맹점은 이전 선택과 상관없이 **항상 사용자에게 물어본다.**
- (10/30 팀 결정) 같은 가맹점 재사용은 사용자가 "앞으로도 적용"(`applyToFuture`)을 고른 경우에만.

**확인 필요**

- ~~현재 `remembered_category()` 코드는 다품목 가맹점을 거르지 않음~~ → 10/30 해결: 거래 분류(`finance/classifier.py`)가 사용자 기억보다 다품목·페이 충전을 먼저 걸러 과거 선택은 후보로만 보여 준다 (세진)
- 분류를 바꾸면 그 주기의 카테고리 사용액·예산 화면 숫자도 바뀜. 예산 쪽 다시 계산 시점 → 김나은. 승인 전 예산 초안은 분류 변경으로 **만료 안 함**(10/22 결정기록 D: 초안 만료는 고정지출 또는 저축 최소 금액이 바뀔 때만)

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
  - **11/4 구현** (`budgets/candidates.py`): 같은 결제 수단끼리 묶는다. 은행 거래에는 받는 계좌 번호가 없어서 연결 안 한 계좌로 보낸 이체는 적요 이름 + 금액 + 날짜로 묶는다. 남에게 보낸 이체도 적요에 회원 이름 · [임시] 저축 단어가 있으면 저축 후보(savings.md S-05). 단 사용자가 '내 계좌 이체가 아니에요'라고 한 이체는 회원 이름을 근거로 쓰지 않는다(R11, 저축 단어는 그대로). [임시] 최근 거래가 이번 달 · 지난달이어야 한다. 자세한 규칙은 fixed.md '후보 감지'.

**거래 저장 뒤 매칭 입구 (10/22 T, 구현: finance/pipeline.py (단계 함수는 각 담당자가 채움))**

거래를 저장한 뒤 **입구 함수 하나**가 아래 단계를 정해진 순서로 부른다. 담당자는 자기 단계 함수를 만들고 `finance/pipeline.py`의 목록에 **한 줄씩 등록**한다. 거래 저장 코드(동기화 · 시험 데이터)는 입구 함수만 부른다.

```python
# finance/pipeline.py
def process_new_transactions(user, transaction_ids):   # 입구: 거래 저장 직후 한 번 부름
    txs = FinancialTransaction.objects.filter(user=user, id__in=transaction_ids)
    for name, step in AFTER_SAVE_STEPS:
        step(user, txs)

AFTER_SAVE_STEPS = [
    ("link_basics",           link_basics),            # 1 세진: 취소 연결 · 내 계좌 이체 판단(R11: 양쪽 가입 이름 · 같은 금액 · 5분 · 양쪽 유일 → auto, 이름만 맞는 출금 → suggested, 날짜·금액만으로는 잇지 않음) · 카드값(card_payment) · 대출 상환 후보(loan_repayment) 판단 (SAME_PURCHASE 연결은 아직 없음)
    ("classify_spending",     classify_spending),      # 2 혜리: spending 카테고리 분류 (규칙 → 사전 → ML → LLM)
    ("match_card_payments",   match_card_payments),    # 3 세진: 카드값 출금 ↔ 카드 청구서
    ("match_loan_repayments", match_loan_repayments),  # 4 세진: 대출 상환 출금 ↔ 대출
    ("match_savings",         match_savings),          # 5 나은: 새 출금 ↔ 확정 정기 저축(보내는 계좌 · 금액 · 이체일 ±3일 · 받는 곳) → saving (11/4, savings.md S-13)
    ("match_payment_dues",    match_payment_dues),     # 6 나은: 계좌 출금 ↔ 고정지출 납부 예정(±3일), 카드 '맞나요?' 후보
    ("detect_candidates",     detect_candidates),      # 7 나은: 고정지출 · 저축 후보 찾기 (결정 T)
]
```

| 순서를 이렇게 둔 이유 | |
|---|---|
| 1 → 2 | 거래 유형이 정해져야 소비(`spending`)만 분류한다 (카드값 · 이체를 분류하지 않게) |
| 2 → 6 | 고정지출 매칭은 "거래 분류 직후"(fixed.md C-12·C-14) — 기억한 가맹점 · 카테고리를 쓴다 |
| 3 · 4 → 5 · 6 | 카드사 · 대출 기관으로 나간 출금을 먼저 가져가야, 같은 출금이 저축이나 고정지출로 **두 번 잡히지 않는다**(결정기록 '한 번만 센다') |
| 5 → 6 | 적금 이체를 저축으로 먼저 확정해야 고정지출 후보에서 빠진다 |
| 마지막 7 | 이미 연결된 거래는 후보 감지에서 뺀다 |

- **단계 함수 약속**: 모양은 `step(user, txs)`. 같은 거래로 두 번 불러도 결과가 같아야 한다(다시 돌려도 안전). 자기 표와 자기가 맡은 거래 칸만 바꾼다. 이미 앞 단계가 가져간 거래(예: `transaction_link`나 `payment_match`가 있는 거래)는 건너뛴다.
- 실패 처리: 단계별 `transaction.atomic()`으로 실패한 단계만 되돌리고 다음 단계는 계속한다. 로그에는 단계 이름과 `database_error` · `step_error`만 남긴다. 예외 원문과 traceback은 남기지 않는다.
- 재시도 기록: 거래별 `field_states.after_save_pending`에 `{단계 이름: 오류 코드}`를 저장한다. 실패한 단계 뒤의 단계는 `upstream_failed`로 남겨 앞 단계 복구 뒤 다시 확인한다. 처리 도중 아직 실행하지 않은 단계는 `pending`이다. 이 키는 파이프라인이 관리하며 다른 `field_states` 값은 보존한다.
- 다음 동기화는 같은 회원·은행 연결의 재시도 대상을 조회 기간과 무관하게 `retry_failed_transactions(user, ids)`로 처리한다. 실패 이전의 성공 단계는 반복하지 않고, 재시도 성공한 단계의 표시를 지운다. 새 거래는 기존처럼 `process_new_transactions(user, ids)`로 처리한다.
- **고정지출에 연결된 이체는 소비** (10/24 AA⑤): 6번 `match_payment_dues`가 이체(`transfer`)를 고정지출에 연결하면 `spending`으로 바꾸고 카테고리 · `funding: fixed`를 채운다. 원래 유형은 `field_states.original_transaction_type`에 남긴다(AA⑥).
- 재연결 시 같은 회원·은행·계좌 해시의 기존 계좌를 새 연결로 옮긴다. 계좌·거래 ID, 선택·용도, 고정지출·납부 연결은 유지하고 과거 `SyncRun.connection`은 바꾸지 않는다.

**H-07 DB 코드 (10/22 확정, 결정기록 3-1 코드표 · DB 반영은 김나은)**

API 값과 DB 값을 같게 둔다(소문자 영어).

| 칸 | 코드 | 상태 |
|---|---|---|
| `financial_transaction.direction` | `in`(입금) · `out`(출금) | ✅ 확정 (10/22) |
| `financial_transaction.transaction_type` | `spending`(소비) · `transfer`(이체) · `own_transfer`(내 계좌 간 이체) · `card_payment`(카드값) · `saving`(저축) · `loan_repayment`(대출 상환) · `income`(입금) | ✅ 확정 (10/22) |
| `financial_transaction.confirmation_state` | `needs_review` · `suggested` · `confirmed` (API `classifyState`와 같음) | ✅ 확정 (10/22) |
| `classification_source` | `user` · `rule` · `ml` · `llm` (API `classifiedBy`와 같음, `merchant_rules.USER_SOURCE = "user"` 유지) | ✅ 확정 (10/22) |

- 월급 찾기(H-01)는 `direction = in` + 관리 대상 입출금 계좌 + 내 계좌끼리 이체 제외.
- **내 계좌끼리 이체**: 적금 · 청약으로 보낸 이체는 저축 확인 화면에서 **저축으로 확인되면 `saving`**, 그 밖의 내 계좌끼리 이체는 **`own_transfer`** 다. (10/22) 확정한 뒤 다음 달부터 같은 저축 이체(연결 안 한 적금으로 보낸 같은 적요 `transfer` 포함)는 거래 저장 뒤 `match_savings`가 `saving`으로 바꿔 거래 목록에서 빠진다 (11/4, savings.md S-13).
- **카테고리가 `UNCLASSIFIED`이면 `confirmation_state = needs_review`** 다. 둘은 항상 같이 움직인다(카테고리가 정해지면 `suggested` 또는 `confirmed`). (10/22)

`connection.status`(`connected` · `relogin` · `disconnected`)와 `bank_account.account_type`(`demand` · `deposit` · `other`)은 `finance-connect.md`의 H-07을 본다.

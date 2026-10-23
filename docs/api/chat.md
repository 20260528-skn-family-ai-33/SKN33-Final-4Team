# 통합 챗봇 API 명세

> 담당: 김혜리(최종 편집) · 소비 분석 카드 항목: 권세진 · 명세 상태: **1차 확정 (10/24, 결정 AA)** · 개발 상태: 미구현
> 공통 규칙은 `docs/API_RULES.md`를 따른다 (camelCase · 원 단위 정수 · UUID · 세션 쿠키 + CSRF · 에러 모양)
> 반영한 결정 (10/21): 챗봇 소비 분석 = LLM 라우터가 문장 해석 + 서버 집계 + 템플릿 답변 (요구사항 CHT-006 · RPT-001 · RPT-004 · AGT-001). LLM은 문장 해석에만 쓰고, 카드의 숫자는 서버가 집계한다.
> 이번 범위: 이전 대화 보기 · 메시지 보내기(요청 모양 · 라우팅 결과 · 소비 분석 카드) · 오래 걸리는 작업 진행 확인. 예산 조정 카드·조정안 승인·되묻기 흐름은 '결정 후'.
> 공통 결정은 `docs/결정기록_2026-10-21.md`(4번 챗봇 소비 분석). 10/22 반영: ★ 2·3·4·8·10·14 · 0번 체크리스트(카테고리 키 `category` + `categoryName`) · **결정기록 3-3 A·C·D·E·J 확정** (README 4-1 · 4-2 ①) · 10/22 결정 N①~⑤ · S · Z②

**공통으로 쓰는 값**

| 값 | 모양 | 뜻 |
|---|---|---|
| 메시지 `message` | `{ "messageId", "role", "contentType", "content", "taskId", "createdAt" }` | 대화 한 줄 |
| `role` | `user` · `bot` | 보낸 쪽 (10/22 E 확정) |
| `contentType` | `text` · `card` | 글 / 결과 카드 (10/22 E 확정) |
| `content` (text) | `{ "text": "…", "choices": [ { "value", "label" } ] }` | `choices`는 누를 수 있는 선택지(없으면 `[]`) |
| `content` (card) | `{ "kind": "spending", … }` | 카드 종류별 모양. 이번에는 `spending`(소비 분석)만 |

---

## 이전 대화 보기

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 하단 탭 [챗봇]을 열어 이어서 대화할 때, 새로고침 뒤 대화를 다시 그릴 때 |
| 어디로 | `GET /api/chat/conversations/<uuid:conversation_id>/` |
| 로그인 필요 | O |
| 명세 상태 | 초안 (기본만) |

**보내는 값** — 없음 (주소의 `<id>` = `conversationId`)

**받는 값 (200)** — 메시지는 오래된 것부터

| 이름 | 타입 | 뜻 |
|---|---|---|
| `conversationId` | UUID | 대화 번호 |
| `status` | 문자 | 대화 상태 (`active` 등, DB choices 기준) |
| `createdAt` | 시각 | 대화 시작 시각 |
| `messages` | 목록 | 메시지(위 공통 값) |

```json
{
  "conversationId": "3f2c…",
  "status": "active",
  "createdAt": "2026-10-21T16:19:40+09:00",
  "messages": [
    { "messageId": "a1…", "role": "user", "contentType": "text", "content": { "text": "지난달보다 식비 얼마 썼어?", "choices": [] }, "taskId": null, "createdAt": "2026-10-21T16:19:58+09:00" },
    { "messageId": "a2…", "role": "bot", "contentType": "card", "content": { "kind": "spending", "…": "아래 소비 분석 카드" }, "taskId": null, "createdAt": "2026-10-21T16:20:00+09:00" }
  ]
}
```

- 서버가 기억하는 대화 상태(`active_agent`, `slots`)는 화면에 보내지 않는다. 다음 메시지를 처리할 때 서버 안에서만 쓴다.

**실패하면**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| 없는 대화·남의 대화 | 404 | `not_found` | 새 대화로 시작 |
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 |

**정해진 것 (10/22 N⑤)**

- 챗봇 탭을 열면 화면이 기억해 둔 마지막 `conversationId`로 이 API를 부른다. 기억한 값이 없거나 404면 `conversationId: null`로 새 대화를 시작한다.
- 대화 목록 API(`GET /api/chat/conversations/`)는 **2차**.

**확인 필요**

- 메시지가 많을 때 나눠 받기(최근 N개) 필요 여부 → 김혜리
- 예산 조정안·선택 버튼을 대화 안에 보여 주는 형식 → 결정 후(예산 조정 에이전트)

---

## 챗봇에 메시지 보내기

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 챗봇 입력창에 문장을 보내거나, 추천 선택지(예: [소비 분석], [예산 조정])를 누를 때 |
| 어디로 | `POST /api/chat/messages/` |
| 로그인 필요 | O |
| 요청 번호 | `Idempotency-Key` 헤더를 보낸다(서버 `@idempotent()`). 메시지 하나를 새로 만드는 요청이라 **[보내기] 또는 선택지를 누를 때** 만든다. 응답 전에 다시 보내면 같은 번호 → 처음 응답을 그대로 돌려주고 메시지를 두 번 만들지 않는다(★4) |
| 명세 상태 | 초안 (기본만 — 요청 모양 · 라우팅 결과 · 소비 분석 카드) |

**보내는 값** — `text`와 `choice` 중 **하나만** 보낸다. 표에 없는 칸을 보내면 400 "이 칸은 보낼 수 없어요."(★2)

| 이름 | 타입 | 필수 | 범위·설명 | 예시 |
|---|---|---|---|---|
| `conversationId` | UUID / null | O | 이어서 말할 대화. `null`이면 새 대화를 만든다 | `null` |
| `text` | 문자 | 둘 중 하나 | 사용자가 입력한 문장. 1~500자 | `"지난달보다 식비 얼마 썼어?"` |
| `choice` | 문자 | 둘 중 하나 | 누른 선택지의 `value` | `"spending"` |

```json
{ "conversationId": null, "text": "지난달보다 식비 얼마 썼어?" }
```
```json
{ "conversationId": "3f2c…", "choice": "spending" }
```

**받는 값 (200)**

| 이름 | 타입 | 뜻 |
|---|---|---|
| `conversationId` | UUID | 대화 번호 (새 대화면 새로 만든 번호) |
| `route.intent` | 문자 | 라우터가 판단한 의도 (아래 표) |
| `route.slots` | 객체 | 문장에서 뽑은 요청 정보 (아래 표). 없는 값은 빠짐 |
| `route.fallback` | 참/거짓 | LLM 라우터가 **실패**해서 `unknown`으로 대신 답했으면 `true`, 라우터가 정상 판단했으면 `false` |
| `messages` | 목록 | 이번 요청으로 **새로 생긴** 메시지(사용자 메시지 1개 + 봇 답변, AA⑧ 확정). 화면은 기존 대화 뒤에 그대로 붙인다 |
| `task` | 객체 / null | 오래 걸리는 작업을 시작했으면 `{ "taskId", "status" }`, 아니면 `null` |

**의도 `route.intent`** (AGT-001)

| 코드 | 뜻 | 1차 답변 |
|---|---|---|
| `spending` | 소비 분석 | 소비 분석 카드 (아래) |
| `budget` | 예산 조정 | 결정 후 — 1차 명세에서는 "곧 연결돼요" 안내 글 + `task: null` |
| `big` | 큰 지출 상담 | "준비 중이에요" 안내 글 (3차 기능) |
| `out` | 범위 밖 질문 | "머니쿠션은 소비 분석과 예산 조정을 도와드려요" 안내 + 선택지 |
| `unknown` | 알 수 없음 | `fallback: false`(라우터가 판단했지만 의도가 불명확) → "조금 더 자세히 적어 주세요." + 선택지 / `fallback: true`(라우터 실패) → "지금은 이해하지 못했어요, 아래에서 골라 주세요." + 선택지. 선택지는 둘 다 `[소비 분석]` `[예산 조정]` |

**라우터 실패 처리 (10/21 나은 확정, AGT-001)**

- LLM 라우터가 응답하지 않거나 형식이 깨지면 에러를 내지 않고 `intent: "unknown"`, `fallback: true`로 답한다.
- 진짜 `unknown`과 실패를 `fallback`으로 구분한다. 라우팅 근거로 기록하고, 나중에 분류 정확도를 잴 때 `fallback: true`는 뺀다.
- 실패했을 때는 **대화 중인 도우미(`active_agent`)와 모은 재료(`slots`)를 바꾸지 않는다.** 이어지던 대화가 있으면 다음 메시지에서 그대로 이어 간다.

**요청 정보 `route.slots`** (AGT-001)

| 이름 | 타입 | 뜻 | 예시 |
|---|---|---|---|
| `period` | 객체 | 분석 기간 `{ "from", "to" }`. 없으면 **이번 달 1일 ~ 오늘**(달력월, 10/22 A — 거래 목록과 같은 기준. 홈 · 예산은 급여 주기라서 다름, BUD-004) | `{ "from": "2026-10-01", "to": "2026-10-21" }` |
| `compare` | 문자 | 비교 기준. **1차는 `prev_month`(지난달)만** 받는다(10/22 N③). 이번 달이 안 끝났으면 지난달도 **같은 날짜까지만** 비교한다(10/1~21 vs 9/1~21, C). 지난달에 그 날짜가 없으면 지난달 말일까지(예: 3/31 → 2/1~2/28, N①) | `"prev_month"` |
| `category` | 문자 | 카테고리 코드 (결정기록 1번 코드표) | `"FOOD"` |
| `amount` | 정수 | 금액(원) — 예산 조정용 | `50000` |
| `direction` | 문자 | `up`(늘림) · `down`(줄임) — 예산 조정용 | `"up"` |

**소비 분석 카드 `content` (`kind: "spending"`)** — 숫자는 서버 집계(RPT-001), 문장은 템플릿(RPT-004)

| 이름 | 타입 | 뜻 |
|---|---|---|
| `kind` | 문자 | `"spending"` |
| `scope` | 문자 | 답변 종류(CHT-006): `all`(카테고리 없음 → 전체) · `category`(그 카테고리) |
| `period` | 객체 | 집계 기간 `{ "from", "to" }` |
| `compare` | 객체 / null | 비교 기간 `{ "from", "to" }`. 이번 달이 안 끝났으면 지난달 1일 ~ 같은 날짜(C), 그 날짜가 없으면 지난달 말일까지(N①). 비교하지 않으면 `null` |
| `comparison` | 객체 | 비교할 수 있는지 `{ "available", "reason" }` (10/22 J). 비교할 수 있으면 `available: true`, `reason: null` |
| `comparison.reason` | 문자 / null | 비교할 수 없는 이유: `not_requested`(비교를 묻지 않음) · `previous_incomplete`(비교 기간 자료가 다 모이지 않음). 비교할 수 없으면 `previousTotal` · `categories[].previousAmount` · `prevDiff` · `prevDiffRate`는 모두 `null` |
| `total` | 정수 | 기간 안 **전체** 소비 합계(원, 취소분 뺀 금액). `scope: category`여도 항상 전체 기준 |
| `fixedAmount` | 정수 | `total` 중 **고정지출**(`funding: fixed` — 월세 · 통신요금 · 구독 등) 합계(원, 10/22 S). 생활비로 쓴 돈 = `total − fixedAmount`. `scope`와 상관없이 전체 기준 |
| `previousTotal` | 정수 / null | 비교 기간 **전체** 소비 합계(10/22 J: `prevTotal`에서 이름 바꿈). 비교할 수 없으면 `null` |
| `categories` | 목록 | 카테고리별 합계(금액 큰 순). `scope: category`면 그 카테고리 1개 |
| `categories[].category` | 문자 | 카테고리 코드 |
| `categories[].categoryName` | 문자 | 카테고리 화면 이름 |
| `categories[].amount` | 정수 | 이번 기간 금액(원) |
| `categories[].previousAmount` | 정수 / null | 비교 기간 금액(원, 10/22 J). 비교할 수 없으면 `null` |
| `categories[].ratio` | 숫자 | 전체 소비(`total`) 중 이 카테고리 비율(0~100) |
| `categories[].prevDiff` | 정수 / null | 비교 기간 대비 금액 차이(원, 음수 = 줄어듦). 비교 안 하면 `null` |
| `categories[].prevDiffRate` | 숫자 / null | 증감률(%). **음수·100 초과 가능**, 비교 기간이 0원이면 `null` |
| `topSpending` | 객체 / null | 가장 많이 쓴 카테고리 `{ "basis": "category", "items": [ … ] }` (10/22 J). **동률이면 모두** `items`에 넣는다(카테고리 표 순서). `scope: category`이거나 소비가 0원이면 `null`. **'확인 필요'(`UNCLASSIFIED`) 금액은 순위에서 빼고**, 문장은 "분류된 소비 중 …"으로 쓴다(10/22 N②) |
| `topSpending.items[]` | 객체 | `category` · `categoryName` · `amount` · `ratio` (`categories[]`의 같은 항목과 같은 값) |
| `unclassifiedAmount` | 정수 | 기간 안 **전체** '확인 필요' 거래 합계(원). `total`에 포함. '확인 필요'는 이 한 줄로 보여 주고 칸을 더 두지 않는다(10/22 N④) |
| `lines` | 문자 목록 | 템플릿으로 만든 답변 문장. 같은 날짜까지 잘라 비교했으면 "9월 같은 기간보다"처럼 비교 기간을 밝힌다(C). 고정지출이 있으면 예: "이번 달 132만 원 썼어요. 월세 같은 고정지출 77만 원을 빼면 생활비는 55만 원이에요."(S) |
| `updatedAt` | 시각 | 집계 기준 시각 |

> **수집 상태(`coverage`)는 1차에서 넣지 않는다(10/22 J).**
> **합계 범위**: `total`·`fixedAmount`·`previousTotal`·`unclassifiedAmount`는 `scope`와 상관없이 **항상 전체 소비 기준**이다. 선택한 카테고리의 금액은 `categories[]`에만 있다. 예) 식비를 물으면 `categories`는 식비 1개(42만 원), `total`은 전체 132만 원, `ratio` 32 = 42만 ÷ 132만.
> 같은 구매가 카드 승인·계좌 출금으로 두 번 들어온 경우의 처리는 `transactions.md`의 '같은 구매가 두 번 들어올 때'와 같은 규칙을 쓴다(한 번만 셈).
> **고정지출 포함 (10/22 S 확정)**: 소비 분석에는 고정지출로 연결된 거래(`funding: fixed`, ★8 — 월세 출금, 넷플릭스 카드 결제 등)도 **포함**하고, 그중 고정지출 합계를 `fixedAmount`로 따로 준다(생활비 사용액에서는 제외 — 결정기록 7번). 카드값 납부 · 저축 · 대출 상환은 카테고리가 아니라서 넣지 않는다.

```json
{
  "conversationId": "3f2c…",
  "route": { "intent": "spending", "slots": { "category": "FOOD", "compare": "prev_month" }, "fallback": false },
  "messages": [
    { "messageId": "a1…", "role": "user", "contentType": "text", "content": { "text": "지난달보다 식비 얼마 썼어?", "choices": [] }, "taskId": null, "createdAt": "2026-10-21T16:19:58+09:00" },
    {
      "messageId": "a2…", "role": "bot", "contentType": "card", "taskId": null, "createdAt": "2026-10-21T16:20:00+09:00",
      "content": {
        "kind": "spending",
        "scope": "category",
        "period": { "from": "2026-10-01", "to": "2026-10-21" },
        "compare": { "from": "2026-09-01", "to": "2026-09-21" },
        "comparison": { "available": true, "reason": null },
        "total": 1320000,
        "fixedAmount": 770000,
        "previousTotal": 1250000,
        "categories": [ { "category": "FOOD", "categoryName": "식비", "amount": 420000, "ratio": 32, "previousAmount": 380000, "prevDiff": 40000, "prevDiffRate": 11 } ],
        "topSpending": null,
        "unclassifiedAmount": 35000,
        "lines": [ "9월 같은 기간보다 식비를 4만 원(11%) 더 썼어요." ],
        "updatedAt": "2026-10-21T16:20:00+09:00"
      }
    }
  ],
  "task": null
}
```

비교 기간이 0원일 때 (RPT-001):

```json
{ "category": "TRAVEL_LODGING", "categoryName": "여행·숙박", "amount": 150000, "ratio": 11, "previousAmount": 0, "prevDiff": 150000, "prevDiffRate": null }
```
→ 문장: "9월 같은 기간에는 여행·숙박 지출이 없었고, 이번 달에는 15만 원 썼어요."

소비가 없는 기간일 때 (10/22 Z②) — 에러나 안내 글이 아니라 **0원 카드**를 보낸다 (바뀌는 칸만):

```json
{ "total": 0, "fixedAmount": 0, "categories": [], "topSpending": null, "unclassifiedAmount": 0, "lines": [ "이 기간엔 소비가 없어요." ] }
```

`unknown`일 때 (라우터가 판단, `fallback: false`):

```json
{ "route": { "intent": "unknown", "slots": {}, "fallback": false },
  "messages": [ { "…": "사용자 메시지" },
    { "messageId": "b2…", "role": "bot", "contentType": "text", "taskId": null, "createdAt": "…",
      "content": { "text": "조금 더 자세히 적어 주세요.", "choices": [ { "value": "spending", "label": "소비 분석" }, { "value": "budget", "label": "예산 조정" } ] } } ],
  "task": null }
```

라우터 실패일 때 (`fallback: true`):

```json
{ "route": { "intent": "unknown", "slots": {}, "fallback": true },
  "messages": [ { "…": "사용자 메시지" },
    { "messageId": "b3…", "role": "bot", "contentType": "text", "taskId": null, "createdAt": "…",
      "content": { "text": "지금은 이해하지 못했어요, 아래에서 골라 주세요.", "choices": [ { "value": "spending", "label": "소비 분석" }, { "value": "budget", "label": "예산 조정" } ] } } ],
  "task": null }
```

- 선택지(`choice`)로 들어온 요청은 라우터를 거치지 않으므로 항상 `fallback: false`다.
- `conversationId`는 **본문**에 담긴 ID라 틀리면 404가 아니라 **400 `invalid`** 다(`API_RULES.md` '보안 · 소유권' 예외). 없는 것 · 남의 것 · 끝난 것 모두 같은 문구. 주소에 ID가 들어가는 '이전 대화 보기'는 404다.

**실패하면**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| `text`와 `choice`를 둘 다 보내거나 둘 다 안 보냄 | 400 | `invalid` | (개발 중 버그) |
| 표에 없는 칸을 보냄 (★2) | 400 | `invalid` | `fields.<그 칸>: ["이 칸은 보낼 수 없어요."]` |
| 같은 요청 번호에 다른 내용 | 409 | `idempotency_conflict` | (개발 중 버그) |
| 문장이 비었거나 500자 초과 | 400 | `invalid` | "500자 이내로 입력해 주세요." (`fields.text`) |
| 모르는 선택지 값 | 400 | `invalid` | (개발 중 버그) `fields.choice` |
| 본문의 `conversationId`가 없는 대화 · 남의 대화 · 끝난 대화 | 400 | `invalid` | `fields.conversationId: ["대화를 찾을 수 없어요."]` → 화면은 `conversationId: null`로 새 대화를 시작 |
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 |
| 저장 보안값(CSRF) 없음 | 403 | `permission_denied` | "다시 시도해 주세요." |
| LLM 라우터가 응답하지 않음·형식이 깨짐 | 200 | — | 에러가 아니라 `intent: unknown`, `fallback: true` + "지금은 이해하지 못했어요, 아래에서 골라 주세요." (위 '라우터 실패 처리') |
| 서버 버그 | 500 | `server_error` | "잠시 후 다시 시도해 주세요." |

**정해진 것 (10/22, 이전 '확인 필요'에서 옮김)**

- 지난달에 같은 날짜가 없으면 **지난달 말일까지** 비교한다 (예: 3/31에 물으면 2/1~2/28과 비교, N①)
- `topSpending`에서 '확인 필요' 금액은 **빼고**, 문장은 "분류된 소비 중 …" (N②)
- `compare`는 **1차 `prev_month`만** (`prev_week` · `none`은 2차, N③)
- '확인 필요'는 `unclassifiedAmount` **한 줄로 충분**, 칸을 더 두지 않음 (N④)
- 소비 분석에 **고정지출 포함**, 그중 합계를 `fixedAmount`로 따로 줌 (S)
- 소비가 없는 기간은 **0원 카드** + "이 기간엔 소비가 없어요." (Z②)
- 응답 `messages`에 **사용자 메시지도 넣는다** — 화면이 서버가 저장한 메시지(ID · 시각)로 바로 그림 (10/24 AA⑧)

**확인 필요**

- `budget` 의도의 1차 처리(안내 글만 vs 예산 조정 작업 시작 + `task`) → 결정 후

---

## 오래 걸리는 작업 진행 확인

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 예산 조정처럼 시간이 걸리는 작업을 시작한 뒤, 화면이 '만드는 중…'을 보여 주며 몇 초마다 상태를 물을 때 |
| 어디로 | `GET /api/tasks/<uuid:task_id>/` |
| 로그인 필요 | O |
| 명세 상태 | 초안 (기본만 — 작업 번호와 진행 상태) |

**보내는 값** — 없음 (주소의 `<taskId>` = 작업 시작 응답의 `task.taskId`)

**받는 값 (200)**

| 이름 | 타입 | 뜻 |
|---|---|---|
| `taskId` | UUID | 작업 번호 |
| `taskType` | 문자 | 작업 종류 (예: `budget_adjust`, 10/22 E 확정) |
| `status` | 문자 | `queued`(대기) · `running`(실행) · `succeeded`(완료) · `failed`(실패) · `canceled`(취소) (10/22 E 확정) |
| `conversationId` | UUID / null | 챗봇에서 시작한 작업이면 그 대화 |
| `createdAt` · `startedAt` · `completedAt` | 시각 / null | 만든 · 시작 · 끝난 시각 |
| `result` | 객체 / null | `succeeded`일 때 결과. **모양은 작업 종류별로 결정 후** |
| `error` | 객체 / null | `failed`일 때 `{ "code", "message" }` (사용자에게 보여 줄 문장) |

```json
{ "taskId": "d71c…", "taskType": "budget_adjust", "status": "running", "conversationId": "3f2c…",
  "createdAt": "2026-10-21T16:30:00+09:00", "startedAt": "2026-10-21T16:30:01+09:00", "completedAt": null,
  "result": null, "error": null }
```
```json
{ "taskId": "d71c…", "taskType": "budget_adjust", "status": "failed", "conversationId": "3f2c…",
  "createdAt": "…", "startedAt": "…", "completedAt": "2026-10-21T16:30:20+09:00",
  "result": null, "error": { "code": "regeneration_limit", "message": "조정안을 만들지 못했어요. 조건을 바꿔 다시 요청해 주세요." } }
```

**조회 규칙 (화면)**

- `queued`·`running`이면 2초 뒤 다시 묻는다.
- `succeeded`·`failed`·`canceled`가 오면 **조회를 멈춘다.**
- 완료되면 결과 메시지는 대화에도 저장되므로, 챗봇 화면은 `GET /api/chat/conversations/<id>/`로 다시 그려도 된다.
- **초안 만료(★10)**: 예산 조정안이 만들어진 뒤 고정지출이나 저축이 바뀌면(추가 · 금액 변경 · 중지 등, 10/22 결정기록 D) 그 조정안은 바뀌기 전 숫자로 만든 것이라 승인할 수 없다(COM-005 `EXPIRED`). 작업 자체는 `succeeded`로 남고, 승인 단계에서 만료를 알린다. 화면은 "고정지출이나 저축이 바뀌어서 다시 만들어야 해요"와 [다시 만들기]를 보여 준다.

**실패하면**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| 없는 작업·남의 작업 | 404 | `not_found` | 조회를 멈추고 "작업을 찾을 수 없어요." |
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 |

**확인 필요**

- 이 방식(작업 시작 → 진행 확인)을 실제로 쓸지, 1차 예산 조정은 바로 답하는 방식으로 할지 → 팀 회의 (예산 조정 에이전트 담당 권세진·김나은)
- 조회 간격(2초)과 최대 대기 시간(예: 60초 뒤 '시간 초과' 안내) → 팀 회의
- `error.code` 목록 (예: 재생성 3회 실패 `regeneration_limit`, 계산 엔진 검수 실패) → 결정 후
- 만료(★10)를 작업 결과(`result`)에도 표시할지, 조정안 승인 API에서만 알릴지 → 결정 후(조정안 승인과 함께)
- 작업 취소 API(`DELETE /api/tasks/<taskId>/`)가 필요한지 → 결정 후

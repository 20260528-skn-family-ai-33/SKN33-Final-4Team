# 통합 챗봇 API 명세

## 2026-11-10 추가: 초안 없는 분배 실패의 조정 시작

- router 엔진의 `POST /api/chat/messages/`에 `{conversationId:null, choice:"budget", budgetContext:{mode:"DESIGN_RECOVERY", sourceTaskId:"<추천 작업 UUID>"}}`를 보낸다. 기존 `baseBudgetId` 기반 INITIAL/NORMAL_ADJUSTMENT 계약은 유지한다. 두 ID를 함께 보내거나 최소금액·잠금·우선순위·금액을 클라이언트에서 주입하면 400이다.
- 서버가 본인 추천 작업의 비공개 전달 자료를 조회한다. 다른 회원·없는 작업·다른 종류의 작업은 404, 전달 자료가 없는 실패·미완료·성공 작업 및 만료된 자료는 409 `expired`이다. 현재 주기·정책·입력 해시·최소금액·잠금·우선순위·유지 목표를 확인하고, 새 예산이 생겼거나 조정 준비 중 조건이 바뀌어도 거부한다.
- 기준 Budget 없이 `agent="budget"`, `content.budgetAdjustment.phase="design_recovery"`, `canApprove=false`로 대화를 시작한다. 범위 또는 재원 선택 시 서버가 검수한 해결안의 `proposal`을 반환한다. 최초 승인 전까지 기존 예산과 설정을 유지한다. 재원이 부족하거나 최소금액과 잠금이 충돌하면 승인 가능한 안을 반환하지 않는다.
- 같은 실패 작업은 기존 `AsyncTask.conversation`으로 본인 대화에 한 번 연결한다. 반복 진입은 그 대화를 재사용하고 동일 요청 번호는 기존 응답을 돌려준다. 대화를 저장하기 직전에도 원본 설정을 재확인한다. 오래된 선택지와 다른 진행 중 조정에 대한 강제 덮어쓰기는 거부한다.
- 이 경로는 서버 계산으로 처리한다. 사용자가 승인하면 같은 대화에서 `proposal.state="saved"`로 조회할 수 있다.

> 담당: 김혜리(최종 편집) · 소비 분석 카드 항목: 권세진 · 명세 상태: **1차 확정 (10/24, 결정 AA)** · 개발 상태: 이전 대화 보기 = DB 구현 (10/26), 메시지 보내기 = 저장 · 선택지 처리 구현 (10/27, 소비 분석 집계 · LLM 라우터는 다음 단계), 작업 진행 확인 = 구현 (10/27, 공용 `backend/config/tasks.py`) — `backend/chat/api.py`
> **11/2 (나은, MEMO 25절)**: 새 챗봇 엔진(라우터 + 소비 분석 에이전트) 계약을 아래 '새 엔진(router) 계약'에 더했다. 서버 설정 `CHAT_ENGINE`이 `"router"`일 때만 쓰고, 기본(`"legacy"`)은 이 문서의 기존 표 그대로다. 두 엔진 모두 응답에 `engine` · `features`(기능 버튼 목록)를 준다.
> 공통 규칙은 `docs/API_RULES.md`를 따른다 (camelCase · 원 단위 정수 · UUID · 세션 쿠키 + CSRF · 에러 모양)
> 반영한 결정 (10/21): 챗봇 소비 분석 = LLM 라우터가 문장 해석 + 서버 집계 + 템플릿 답변 (요구사항 CHT-006 · RPT-001 · RPT-004 · AGT-001). LLM은 문장 해석에만 쓰고, 카드의 숫자는 서버가 집계한다.
> 11/05 연결: `CHAT_ENGINE=router`에서 예산 조정 대화·비교·변경안·승인 연계를 구현했다. 아래 기존 표는 legacy 계약이며, 현재 서비스 연결은 ‘새 엔진(router) 계약’과 ‘예산 조정 연결’을 따른다.
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

새 메시지는 대화 행 잠금 안에서 사용자 → 봇 순서로 저장한다. 새 메시지 시각이 직전 메시지보다 늦지 않으면 `createdAt`을 직전 메시지보다 1마이크로초 뒤로 보정해, 재조회해도 저장 순서가 유지되도록 한다. 기존 메시지의 시각·내용은 일괄 수정하지 않는다.

예산 조정은 자유 대화가 기본이다. 미정 금액·지출 사유·보호 조건을 이어받으며 자동으로 비교 카드를 펼치지 않는다. 답변의 보조 선택지 `recommend`(추천안 보기)는 검수된 추천 미리보기 한 개, `compare`(비교하기)는 대안 비교를 요청한다. 조건이 부족하면 필요한 질문만 하고, 미리보기·비교만으로 승인하지 않는다. 과거 답변의 보기 버튼도 현재 대화의 최신 선택지와 `turn`을 사용하며, 종료·저장 및 보호 확인을 건너뛰지 않는다.

승인 후 같은 대화에서 새 조정을 요청하면 완료된 작업 참조·이전 초안 진입 조건·호출 수를 이어 쓰지 않는다. 새 요청을 `start`로 전달하고 현재 승인 예산을 다시 읽는다. 완료 상태 재개(`resume`)와 취소(`cancel`)는 새 조정으로 바꾸지 않는다.

| 이름 | 타입 | 뜻 |
|---|---|---|
| `conversationId` | UUID | 대화 번호 |
| `status` | 문자 | 대화 상태 (`active` 등, DB choices 기준) |
| `createdAt` | 시각 | 대화 시작 시각 |
| `messages` | 목록 | 메시지(위 공통 값) |
| `engine` · `features` | 문자 · 목록 | 지금 엔진과 기능 버튼 목록 (11/2, 아래 '새 엔진(router) 계약') |

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
  → **11/2 변경(사용자 결정 10/30 · 11/2)**: 채팅 화면은 새로고침 · 나갔다 오기 모두 새 대화로 시작하고 이 API를 부르지 않는다(대화 번호는 화면 안에서만). API는 그대로 둔다.
  → **11/3 변경(N3)**: **새로고침하면 이 API로 같은 대화를 다시 그린다.** 화면은 대화 번호를 `sessionStorage`에 두고 채팅 화면을 떠나면 지운다 — 다른 화면에 갔다 오면 지금처럼 새 대화. 404(다른 계정 · 없는 대화)면 번호를 지우고 새 대화로 시작한다. 그 밖의 실패(네트워크 등)는 안내만 하고 대화를 비우지 않는다. 복원하는 동안은 보내기를 막는다. 조정 카드는 최신 카드 한 건만 재검수하고, 이전 카드는 저장한 내용으로 읽기 전용 표시한다.
- 대화 목록 API(`GET /api/chat/conversations/`)는 **2차**.

**확인 필요**

- 메시지가 많을 때 나눠 받기(최근 N개) 필요 여부 → 김혜리
- 예산 조정안·선택 버튼은 router 엔진의 아래 ‘예산 조정 연결’ 계약을 따른다. legacy에는 조정 카드가 없다.

---

## 챗봇에 메시지 보내기

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 챗봇 입력창에 문장을 보내거나, 추천 선택지(예: [소비 분석], [예산 조정])를 누를 때 |
| 어디로 | `POST /api/chat/messages/` |
| 로그인 필요 | O |
| 요청 번호 | `Idempotency-Key` 헤더를 보낸다(서버 `@idempotent()`). 메시지 하나를 새로 만드는 요청이라 **[보내기] 또는 선택지를 누를 때** 만든다. 응답 전에 다시 보내면 같은 번호 → 처음 응답을 그대로 돌려주고 메시지를 두 번 만들지 않는다(★4) |
| 명세 상태 | 초안 (기본만 — 요청 모양 · 라우팅 결과 · 소비 분석 카드) |

재전송 처리 (#66):

- 같은 키 · 같은 본문의 동시 요청은 먼저 저장된 성공 응답을 반환한다. 대화 버전 충돌이 나도 성공 기록이 있으면 이를 우선한다. 같은 키 · 다른 본문은 `409 idempotency_conflict`, 서로 다른 키의 router 대화 경합은 기존 `409 conversation_busy`를 유지한다.
- 화면은 성공 여부가 불명확한 메시지의 **본문 · 최초 conversationId(null 포함) · 요청 키**를 보관한다. 통신 실패 · 타임아웃 · 5xx · 정상 응답 본문 유실 시 **[다시 보내기]**로 같은 값을 보낸다. 복구 전에는 다른 메시지 · 선택지 전송을 막는다. 정상 응답을 받은 뒤 다음 메시지에 새 키를 발급한다(legacy · router 공통).
- 명시적인 4xx 거절(408 제외)은 입력 문장을 복원해 수정할 수 있게 한다. 보관 범위는 현재 화면이며, 화면 이탈 · 새로고침 후 복원 정책은 이번 수정에 포함하지 않는다.

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
| `budget` | 예산 조정 | legacy는 "곧 연결돼요" 안내 글 + `task: null`. router는 아래 예산 조정 연결 계약 사용 |
| `big` | 큰 지출 상담 | "준비 중이에요" 안내 글 (3차 기능) |
| `out` | 범위 밖 질문 | "머니쿠션은 소비 분석과 예산 조정을 도와드려요" 안내 + 선택지 |
| `unknown` | 알 수 없음 | `fallback: false`(라우터가 판단했지만 의도가 불명확) → "조금 더 자세히 적어 주세요." + 선택지 / `fallback: true`(라우터 실패) → "지금은 이해하지 못했어요, 아래에서 골라 주세요." + 선택지. 선택지는 둘 다 `[소비 분석]` `[예산 조정]` |

**라우터 실패 처리 (10/21 나은 확정, AGT-001)**

- LLM 라우터가 응답하지 않거나 형식이 깨지면 에러를 내지 않고 `intent: "unknown"`, `fallback: true`로 답한다.
- 설정한 AI 호출 상한에 도달했을 때도 `intent: "unknown"`, `fallback: true`를 유지하지만, 질문 이해 실패와 구별해 "설정된 AI 호출 상한에 도달해 질문을 처리하지 못했어요. 호출 한도를 확인한 뒤 다시 시도해 주세요."로 안내한다. 한도가 풀리기 전 같은 호출을 유도하는 선택지는 표시하지 않는다.
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

**정해진 것 (10/27 나은)**

- `legacy` 엔진의 `budget` 의도는 안내 글과 `task: null`을 반환한다. `router` 엔진은 11/05부터 아래 예산 조정 에이전트로 연결하며, legacy 응답을 실제 조정 결과로 취급하지 않는다.
- 오래 걸리는 작업 진행 확인(`GET /api/tasks/<id>/`)은 10/27 공용으로 구현했다(`config/tasks.py`). 처음 쓰는 작업은 금융 정보 다시 불러오기(`finance_sync`)이고, 예산 조정 · 추천 예산도 같은 도구를 쓴다.

**기존 엔진 구현 상태 (legacy, 10/27 기록)**

1. ✅ 대화 · 메시지 저장, 응답에 사용자 메시지 포함 (같은 대화에 동시에 온 메시지는 대화 행을 잠그고 차례로 저장)
2. ✅ 선택지(`choice`)는 라우터를 거치지 않고 처리 (`fallback: false`)
3. ⏳ 소비 분석 숫자 집계 → 카드 (권세진과 같이, 카드 칸은 `spending-analysis.md`). **그전까지 `spending`은 임시 안내 글**("소비 분석 카드는 곧 연결돼요. …")
4. ⏳ LLM 라우터 (`chat/router.py`의 `route_text`). **그전까지 문장(`text`)은 라우터 실패로 처리** → `intent: unknown`, `fallback: true` + 선택지

---

## 새 엔진(router) 계약 (11/2, MEMO 25절)

서버 설정 `CHAT_ENGINE = "router"`일 때의 차이만 적는다. 주소 · 로그인 · `Idempotency-Key` · 본문 칸 검사 · `conversationId` 오류(400)는 위 '챗봇에 메시지 보내기'와 같다.

**설정 (11/3)** — 일반 서버는 `.env`(`backend/config/settings.py`가 읽음)로 켠다. 잘못된 값이면 서버가 시작할 때 멈춘다.

| 환경 변수 | 값 | 뜻 |
|---|---|---|
| `CHAT_ENGINE` | `legacy`(기본) · `router` | 기존 선택지 챗봇 / 라우터 + 소비 분석 + 예산 조정. 청년 정책은 준비 중 |
| `CHAT_LLM` | `fake`(기본) · `live` | 가짜 해석(네트워크 없음) / 실제 LLM — `live`는 `OPENAI_API_KEY` · `LLM_MODEL`이 있어야 시작된다 |

- 일반 서버에는 기준 날짜(`CHAT_TODAY`)가 없다 → 오늘 날짜로 실제 거래를 읽고 `testMode: false`. 고정 기준일 · 분석 과정(`CHAT_DEV_TRACE`)은 더미 서비스 실행(`spending_fixture.service_settings`, 기준일 2026-10-29)에서만 켠다. 그 환경의 금융 연결 503은 일부러 막은 것이라 실제 연동 오류가 아니다.
- router의 예산 조정은 아래 대화·비교·승인 연결을 사용한다. 청년 정책은 아직 '준비 중' 안내만 한다. 기본 legacy에서는 예산 조정 안내만 반환하므로 실제 조정 테스트는 router를 켜야 한다.
- 남은 생활비 답은 예산 화면 · 홈과 같은 기준이다: 주거·통신 · 여행·숙박 지출은 빼고 "예산 밖 지출 N원은 남은 생활비에서 빼고 따로 보여드려요"로 안내한다(budgets.md R-13). 전체 소비 답에는 두 카테고리도 들어간다.

**기능 버튼 목록 — 두 엔진 공통**

- `GET /api/chat/messages/` (로그인 필요, 저장 없음) → `{ "engine", "features" }`. 대화를 시작하기 전 첫 화면이 버튼을 그릴 때 쓴다.
- 메시지 보내기 응답 · 이전 대화 보기 응답에도 같은 두 칸을 넣는다. 화면은 서버가 준 목록만 그린다.

| 이름 | 타입 | 뜻 |
|---|---|---|
| `engine` | 문자 | `legacy`(기존) · `router`(새 엔진) |
| `features` | 목록 | 기능 버튼 `[{ "value", "label" }]`. legacy = `spending` 소비 분석 · `budget` 예산 조정, router = 여기에 `policy` 청년 정책 |

```json
{ "engine": "router", "features": [ { "value": "spending", "label": "소비 분석" }, { "value": "budget", "label": "예산 조정" }, { "value": "policy", "label": "청년 정책" } ] }
```

**보내는 값 `choice`** — 둘 중 하나

| 모양 | 뜻 |
|---|---|
| 문자 `"spending"` · `"budget"` · `"policy"` | 기능 버튼 (기존과 같은 문자열. `policy`는 router 만 — legacy 에선 400 "알 수 없는 선택지예요.") |
| 객체 `{ "value", "turn" }` | 답변 선택지. `value` = 누른 `choices[].value`(1~100자), `turn` = 그 봇 메시지 `content.turn`(1 이상 정수). 이어서 말하는 대화에서만(`conversationId` 필수) |

**받는 값 (200)** — 기존 봉투 + 칸 몇 개

| 이름 | 타입 | 뜻 |
|---|---|---|
| `route.intent` | 문자 | 기존 코드 + `policy`(청년 정책). 실제로 답한 에이전트 · `out`(범위 밖) · `unknown` |
| `route.slots` | 객체 | router 에선 항상 `{}` (조건은 서버 대화 상태에만 둔다) |
| `route.fallback` | 참/거짓 | 라우터 해석 실패면 `true` (기존과 같은 뜻) |
| `agent` | 문자 / null | 지금 대화 중인 도우미 (`spending` · `budget` · `policy`) |
| `engine` · `features` | | 위 '기능 버튼 목록' |

**봇 메시지 `content` (`contentType: "text"`)** — 기존 `{ text, choices }`를 그대로 채우고 칸을 더한다. 확장 칸을 모르는 화면도 `text`만으로 읽을 수 있다.

| 이름 | 타입 | 뜻 |
|---|---|---|
| `text` | 문자 | `lead` + `lines`를 줄바꿈으로 이은 문장 (호환용) |
| `choices` | 목록 | `[{ "value", "label" }]` — 누르면 `{ value, turn }`으로 보낸다 |
| `lead` | 문자 목록 | 맨 위 핵심 안내 (예: 비교할 수 없는 이유) |
| `lines` | 문자 목록 | 본문 문장 (숫자는 모두 서버 계산값) |
| `sections` | 목록 | `[{ "title", "lines" }]` 묶음 문장 (예: 카테고리별 남은 예산) |
| `notices` | 문자 목록 | 작은 안내 (일부 내역 기준 · 확인 필요 · 고정지출 뺌 등) |
| `view` | 목록 / 없음 | 카드 블록 `head` · `total` · `rows` · `text` · `meter` (서버가 준 금액 · 비율 · 글자 그대로 그린다) |
| `chart` | 객체 / 없음 | 전반 분석의 카테고리 막대 `{ title, basis, limited, empty, rows: [{ code, name, amount, share, text }] }` |
| `alt` | 객체 / 없음 | 이번 달 조회(달력 한 달)를 **이번 예산 주기(시작일 ~ 기준일)** 로 다시 본 판 (11/11). `{ label: "예산 주기", baseLabel: "이번 달", lines, notices, view?, chart?, lead?, sections?, first? }` — 화면은 `[baseLabel \| label]` 전환 버튼으로 `view` · `chart` · `lead` · `notices` 를 이 판의 것으로 바꿔 그린다(금액은 다시 계산하지 않는다). 예산 주기가 없거나 달력 한 달과 같은 기간이면 없다. `first: true` = 넘겨 둔 예산 조정이 있는 대화라 예산 주기를 먼저 보인다 |
| `examples` | 목록 / 없음 | 예시 질문 |
| `turn` | 정수 | 이 봇 메시지 차례 번호 (답변 선택지 확인용) |
| `agent` | 문자 / null | 이 답을 한 도우미 |
| `inputs` | 문자 목록 | 현재 `[]`. 예산 조정은 구조화된 `budgetAdjustment` 카드와 자유 입력을 사용한다. |
| `result` | 참/거짓 | 분석 결과 카드면 `true`, 짧은 답 · 질문이면 `false` |
| `budgetAdjustment` | 객체 / 없음 | 예산 조정 단계·비교안·변경안. 아래 연결 계약을 따른다. |

**실패하면 (router 만, 둘 다 저장 없음)**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| 같은 대화의 다른 메시지가 먼저 저장됨 | 409 | `conversation_busy` | "앞 메시지를 처리하는 중이에요. 잠시 후 다시 보내 주세요." |
| 마지막 봇 메시지의 선택지가 아님(지난 질문의 선택지) | 409 | `stale_choice` | "이전 질문의 선택지예요. 가장 최근 질문에서 골라 주세요." |
| `choice` 객체 모양이 틀림 | 400 | `invalid` | `fields.choice: ["알 수 없는 선택지예요."]` |

---

## 예산 조정 연결 (router)

실제 자유 대화는 `CHAT_ENGINE=router`, `CHAT_LLM=live`와 서버의 LLM 키·모델 설정을 사용한다. `fake`는 명시한 테스트 문장용이며 일반 문장을 이해한다고 표시하지 않는다. LLM은 요청만 해석하고 금액 계산·검수·저장은 서버가 맡는다.

### 초과 카테고리 진입의 에이전트 내부 계약 (2026-11-11)

- 예산 조정 담당에게 넘기는 내부 `request.hints`는 `{"reason":"over","categories":["FOOD"],"over_amount":12000}`이다. 세 필드만 받으며, 기존 조회 복귀용 `{"from":"spending","category":"TRANSPORT_CAR"}`와 섞지 않는다. 현재 승인 예산에 있는 소비 카테고리 하나와 양수 정수 초과액을 받는다. `기타`·`미분류`, 여러 카테고리, 0원 예산, 불완전한 지출 자료는 이 경로에서 시작하지 않는다.
- 초과액은 현재 예산·지출로 다시 계산한다. 전달받은 `over_amount`를 희망 증액액으로 사용하지 않는다. 요청 문장 해석과 카테고리·방향 재질문 없이 기존 재원 선택(`phase="funding_scope"`)으로 연결하고, 재원을 고른 뒤 희망 금액을 확인하여 검수·승인 흐름을 따른다. 잠긴 항목은 기존 잠금 해제 확인을 거친다.
- 조정 중 다른 초과 진입이 오면 기존 요청·질문·미승인안을 보존하고 `phase="over_entry"`와 `over_keep`(기존 조정 이어가기) / `over_restart`(초과 항목으로 새 조정)를 반환한다. 기존 조정을 고르면 같은 단계로 돌아간다. 새 조정을 고르면 초과 여부를 다시 확인하고 기존 미승인안의 취소가 확인된 뒤 시작한다. 저장 중인 작업을 취소하거나 승인 예산을 변경하지 않는다.
- 이 계약은 예산 조정 안쪽 수신부다. 화면·소비 분석·라우터가 이 값을 만드는 바깥쪽 연결은 별도이며, 공개 메시지 API에 최상위 `hints`를 보내는 기능을 추가한 것은 아니다. 기존 공개 `budgetContext` 검증은 유지한다.
- 원인 확인 도구와 LLM의 원인 판단은 이번 기본 연결에 포함하지 않는다. 조회 도구와 기간별 집계 계약이 마련되면 연결하며, 원본 거래 ID·가맹점명은 LLM 입력에 넣지 않는다.

초안 수정 또는 예산 화면에서 시작할 때 기존 메시지 API에 다음을 보낸다. `budgetContext`는 기능 선택 `choice: "budget"`과 함께만 허용한다. 본인 기준 예산·현재 주기·모드는 서버가 다시 검사한다.

```json
{
  "conversationId": null,
  "choice": "budget",
  "budgetContext": {
    "baseBudgetId": "3f2c0000-0000-4000-8000-000000000001",
    "mode": "INITIAL_ADJUSTMENT"
  }
}
```

**초과한 카테고리에서 시작 (11/11)** — 예산 화면의 초과한 카테고리 줄 [예산 조정하기]는 `budgetContext`에 `hints`를 더해 보낸다. **승인된 예산의 `NORMAL_ADJUSTMENT`에서만** 쓸 수 있다(승인 전 초안 · 분배 실패 복구에는 `400`). 화면도 승인된 예산에서만 이 버튼을 보여 준다.

```json
{
  "conversationId": null,
  "choice": "budget",
  "budgetContext": {
    "baseBudgetId": "3f2c0000-0000-4000-8000-000000000001",
    "mode": "NORMAL_ADJUSTMENT",
    "hints": { "reason": "over", "categories": ["FOOD"], "overAmount": 12000 }
  }
}
```

| 칸 | 값 | 규칙 |
|---|---|---|
| `hints.reason` | `"over"` | 지금은 이 값만 |
| `hints.categories` | 카테고리 코드 배열 | **정확히 1개**, 생활비 배분 대상 카테고리(주거·통신 · 여행·숙박 · 기타 제외 — 예산 조정이 초과 항목 조정을 시작하는 범위와 같다) |
| `hints.overAmount` | 정수 | 1 이상. **참고값** — 증액 금액으로 확정하지 않고 예산 조정이 서버에서 최신 초과액을 다시 확인한다 |

- 세 칸이 모두 있어야 하고 다른 칸은 받지 않는다(금액 · 슬롯 직접 주입 금지). `hints: null`을 포함해 모양이 다르면 `400 invalid`(`fields.budgetContext`). 남의 기준 예산이면 `404`가 먼저다.
- 서버는 화면이 보낸 `hints`를 **저장하지 않고**, 예산 조정 에이전트 입력의 `hints`(`{"reason", "categories", "over_amount"}`, 위 '에이전트 내부 계약')로 그대로 넘긴다. 조정 작업이 없으면 새로 시작하고, 진행 중 · 보류한 조정이 있으면 라우터는 이어서/새로를 묻지 않고 그 작업에 넘긴다 — 기존 조정을 이어갈지 초과 항목으로 새로 할지는 예산 조정 에이전트가 묻는다(`phase="over_entry"`). 라우터 질문에 답하는 중이면 쓰지 않고 같은 질문을 다시 한다.
- 예산 화면의 버튼은 항상 새 대화(`conversationId: null`)로 시작한다. 같은 대화에서 `budgetContext`를 다시 보내는 경우의 기준 예산 검사(`409 not_ready`)는 `hints`와 무관하게 기존 규칙 그대로다.
- 이때 사용자 말풍선은 "예산 조정" 대신 "식비 예산 조정"처럼 카테고리 이름을 붙인다.

**초과한 카테고리 질문 (11/11)** — "초과한 카테고리 뭐야?"처럼 예산을 넘은 카테고리를 물으면 소비 분석이 카테고리별 남은 예산으로 답하고, 맨 위에 `예산을 넘은 카테고리: 식비 12,000원 초과, …`(많이 넘은 순서) 또는 `예산을 넘은 카테고리는 없어요.` 한 줄을 넣는다. 생활비 전체가 남아 있어도 넘은 카테고리가 있으면 아래 [예산 조정하기]가 붙는다.

**소비 분석의 초과 답변에서 시작 (11/11)** — 소비 분석이 카테고리 남은 예산 · 생활비 전체 예산을 답하며 초과를 알리면 답변에 `{"value": "budget", "label": "예산 조정하기"}` 선택지가 붙는다(기존). 이 선택지를 누르면(`choice: {value: "budget", turn}`) 서버가 **직전 답변의 초과 카테고리 하나**로 같은 `hints`를 만들어 예산 조정에 넘긴다. 화면은 `hints`를 보내지 않는다.

- 여러 카테고리가 넘었으면 가장 많이 넘긴 1개만 넘긴다. 예산 0원 칸 · 기타 · 확인 필요 · 예산 밖 카테고리는 고르지 않는다. 고를 것이 없으면 `hints` 없이 기존처럼 시작한다.
- 초과 기억은 **바로 다음 한 번**만 유효하다. 그 사이 다른 말을 했거나 위쪽 [예산 조정] 기능 버튼(`choice: "budget"`)을 누르면 쓰지 않는다.
- 조정 중 조회 질문으로 소비 분석에 다녀온 경우(넘겨 둔 조정)에도 같은 방식으로 넘기고, 이때 조회 복귀용 `{"from": "spending", "category"}`와 섞지 않는다. 기존 조정안은 보존되고 예산 조정이 "기존 조정 이어가기 / 초과 항목으로 새 조정"을 묻는다.
- 소비 분석 응답 `content`에는 초과 카테고리 칸을 따로 넣지 않는다(서버 안에서만 전달).

월중 조정은 `mode: "NORMAL_ADJUSTMENT"`와 승인 예산 ID를 사용한다. 기준을 생략한 일반 채팅 진입은 서버가 현재 예산을 고른다. 진행 중인 조정에서 다른 기준을 지정하면 `409 not_ready`이며, 먼저 취소한 뒤 새로 시작한다.

봇 `content.budgetAdjustment`의 공개 필드는 다음과 같다. 내부 대화 슬롯·개인별 조회 근거는 보내지 않는다.

| 필드 | 의미 |
|---|---|
| `status`, `phase`, `workRef` | 대화 결과, 현재 확인 단계, 서버 작업 ID. 대화 상태와 `AsyncTask.status` 실행 상태는 별개다. |
| `explanation` | `why`, `effect`, `next`, 선택적인 짧은 `brief`. 숫자는 검수 결과에서만 가져온다. |
| `comparison`, `alternatives` | 같은 요청액 또는 표시한 공통 기준액의 세 안. 반영액·부족액·전후 변화·추천 근거·자료 확인 필요 상태를 구분한다. |
| `proposal` | `proposalId`, `taskRef`, `mode`, `state`, `canApprove`, `changes`, `shortfalls`, `persistenceItems`, `targetItems`, `unlockCategories`, `keepForNextCycle` 등. 상세는 [budgets.md](budgets.md)의 조정안 계약을 따른다. |
| `canApprove` | 현재 대화에서 확정 단계로 이동 가능한지. 서버 승인 검사를 대체하지 않는다. 부분 반영 선택 전에는 `false`다. |
| `detail`, `alternativesNotice` | 상세 답변 여부와 비교할 수 없는 경우의 안내. |

첫 세 가지 범위 선택에 ‘비교하고 정하기’를 제공한다. 선택은 기존 `choice: {value, turn}`을 사용하고 자유 문장도 받는다. ‘예산 변화 보기’ 토글은 받은 데이터를 펼치는 UI 동작으로 API·LLM 호출이나 선택·승인을 하지 않는다. ‘이 안 선택’은 미리보기 선택이며, 실제 확정은 별도 승인 API를 호출한다. 단순 ‘네’로 여러 비교안 중 하나를 추측하지 않는다.

미승인안은 홈·일반 예산 조회에 적용되지 않는다. LLM 처리 뒤 대화 `rev`를 다시 검사하고, 제안·작업·메시지·슬롯을 같은 트랜잭션에서 저장한다. `conversation_busy`로 거절한 턴의 제안만 남는 부작용을 막는다. 새로고침의 대화 조회와 조정안 GET은 저장된 카드의 현재 승인·만료 상태를 서버 근거로 갱신한다.

지출 자료·ML 참조·재원 가용성·자동 감액 정책을 확인할 수 없으면 확인 필요 상태와 다음 행동을 안내한다. 임시 비율·합성 수치를 실제 ML 또는 가용액으로 표시하거나, 미확인을 0원으로 바꿔 성공안을 만들지 않는다.

---

## 오래 걸리는 작업 진행 확인

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 예산 조정처럼 시간이 걸리는 작업을 시작한 뒤, 화면이 '만드는 중…'을 보여 주며 몇 초마다 상태를 물을 때 |
| 어디로 | `GET /api/tasks/<uuid:task_id>/` |
| 로그인 필요 | O |
| 명세 상태 | 초안 (기본만 — 작업 번호와 진행 상태) |
| 개발 상태 | 구현 (10/27, 공용) — `backend/config/tasks.py`. 작업 하나 = `async_task` 한 줄 |

**보내는 값** — 없음 (주소의 `<taskId>` = 작업 시작 응답의 `task.taskId`)

**받는 값 (200)**

| 이름 | 타입 | 뜻 |
|---|---|---|
| `taskId` | UUID | 작업 번호 |
| `taskType` | 문자 | 작업 종류: `finance_sync`(금융 정보 다시 불러오기, finance-connect.md) · `card_first_load`(카드사 첫 연결 뒤 청구서 · 승인내역 불러오기, 11/09, 연결마다 하나) · `budget_adjust`(예산 조정, 10/22 E) 등 |
| `status` | 문자 | `queued`(대기) · `running`(실행) · `succeeded`(완료) · `failed`(실패) · `canceled`(취소) (10/22 E 확정) |
| `conversationId` | UUID / null | 챗봇에서 시작한 작업이면 그 대화 |
| `createdAt` · `startedAt` · `completedAt` | 시각 / null | 만든 · 시작 · 끝난 시각 |
| `result` | 객체 / null | `succeeded`일 때 결과. 모양은 작업 종류별 명세에 둔다(`finance_sync` → finance-connect.md '작업 결과') |
| `error` | 객체 / null | `failed`일 때 `{ "code", "message" }` (사용자에게 보여 줄 문장). 공용 코드: `server_error`(서버 오류, "잠시 후 다시 시도해 주세요.") · `stale`(10분 넘게 끝나지 않아 닫음, "작업이 중간에 멈췄어요. 다시 시도해 주세요.") |

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

**공용 작업 규칙 (10/27, `config/tasks.py`)**

- 작업을 시작하는 API는 `202 {taskId, status}`로 답하고, 화면은 이 API로 결과를 본다.
- **같은 회원 · 같은 종류의 작업이 진행 중이면 새로 만들지 않고 그 작업을 돌려준다**(`start_or_reuse`). 작업마다 "끝난 지 N분 안이면 마지막 결과" 규칙을 더 둘 수 있다(다시 불러오기는 1분).
- **1차는 작업을 요청 안에서 끝까지 처리**하고 응답만 작업 모양으로 준다(그래서 시작 응답이 바로 `succeeded`일 수 있다). 느려지면 서버 안에서만 뒤로 돌리고 API · 화면은 그대로 둔다(10/27 나은).
- **11/09: 금융 불러오기(`finance_sync` · `card_first_load`)는 뒤(웹 서버 스레드)에서 돈다** (`config/tasks.run_later`). 시작 응답은 `running`이고 화면이 진행을 본다. 별도 작업 서버가 없어서 서버가 재시작되면 그 작업은 기한(10분) 뒤 `failed(stale)`. 테스트는 `settings.TASKS_INLINE`으로 요청 안에서 돈다. 예산 작업은 그대로 요청 안에서 처리한다.
- 작업은 실행 권한 만료 시각(`async_task.lease_expires_at`)까지만 새 조회를 시작하고 결과를 저장 · 기록할 수 있다. 연장은 그 작업이 **자기** 기관 조회를 시작하거나 저장할 때, 그 순간 아직 유효할 때만 10분씩 한다(다른 작업 · 작업 없는 조회 · 다른 회원은 연장 못 함). 기간이 지난 작업은 상태가 아직 `running`이어도 새 조회 · 저장 · 성공 기록이 거절되고, 새 작업 시작 · 동의 철회 · 탈퇴 때 `failed / stale`로 닫힌다. 기관 조회 기록 하나는 시작 후 10분 안에만 저장할 수 있다. 이미 끝났거나 지워진 작업의 늦은 결과는 상태를 바꾸지 않는다(R20, 11/06).

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

- 현재 예산 조정 대화는 메시지 요청에서 바로 답하며 제안이 생기면 `budget_adjust` 작업을 함께 저장한다. 작업 실행 `succeeded`와 변경안 승인 대기는 별도 상태다.
- 조회 간격(2초)과 최대 대기 시간(예: 60초 뒤 '시간 초과' 안내) → 팀 회의
- `error.code` 목록 (예: 재생성 3회 실패 `regeneration_limit`, 계산 엔진 검수 실패) → 결정 후
- 조정안 승인이 조건 변경을 발견하면 `409 expired`와 함께 조정안·작업 `result.state`를 만료로 저장한다. 읽기 전용 카드 조회도 최신 조건을 검사해 만료를 표시한다.
- 작업 취소 API(`DELETE /api/tasks/<taskId>/`)가 필요한지 → 결정 후
- `queued` 상태는 1차(요청 안에서 처리)에서는 생기지 않는다. 뒤에서 돌리게 바꿀 때 쓴다

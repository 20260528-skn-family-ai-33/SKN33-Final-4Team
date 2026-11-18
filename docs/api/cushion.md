# 쿠션 API — 조회 · 설정 변경

> 담당: 권세진 · 작성일: 2026-10-28
> GET 구현 완료. PATCH는 후속 구현을 위한 명세이며 현재 호출하면 405다.
> 근거: [공통 규칙](../API_RULES.md), [지킬 돈 P-08·P-09](./budgets.md), CUS-001(목표 = 월급 × 3).

## GET /api/budgets/cushion/

로그인한 회원의 확보액·목표·월 적립금·재적립 예상 기간을 조회한다. 세션 쿠키가 필요하며 GET은 CSRF 토큰이 필요 없다. 본문·회원 ID 입력은 받지 않는다. 금융기관 호출이나 금융 조회 동의는 필요 없다.

### 데이터 원천

- 확보액: 본인의 `cushion_entry.amount` 전체 합계. 기록이 없으면 0원이다. 초기금·적립·사용·환급·정정의 부호를 그대로 합산하며, 지난 주기·교체된 예산의 기록도 포함한다. 마이너스 합계가 있더라도 숨기거나 0원으로 덮지 않는다.
- 월 적립금: `financial_profile.cushion_monthly`. 미설정은 null이며, 임의로 소득의 10%를 채우지 않는다. 0원 설정과 미설정은 구분한다.
- 목표: 사용자가 직접 정한 `financial_profile.cushion_goal_amount`(11/13)가 있으면 그 금액, 없으면 현재 `monthly_take_home_income × 3`. `cushion_target`은 지킬 돈 저장 당시 값이므로 급여 변경 후 오래된 값일 수 있다. GET은 현재 소득으로 계산하고 저장값을 고치지 않는다. 소득이 없으면 과거 목표를 대신 쓰지 않는다.
- 기존 소득이 0 이하이면 목표를 계산하지 않고 `missing`에 `takeHomeIncome`을 표시한다. 월 적립금 음수는 기존 DB 제약에서 금지한다.
- 쿠션 확보액은 앱의 추정액이다. 홈·예산·쿠션 조회와 예산을 사용하는 챗봇 요청에서 새 주기로 예산을 이어 쓰고, 설정한 **월급날 이틀 뒤**부터 적용 중인 쿠션 배정액을 반영한다.

### 보호 중인 쿠션 쌓기 (R27, 11/13)

`now`(확보액) = `cushion_entry` 합계. 아래 두 곳에서 종류 **`planned`(예산 보호)** 줄을 남긴다 (`budgets/cushion.py`). 실제 이체 완료를 뜻하지 않는 **예산에서 보호한 기록**이라, 실제로 옮긴 적립(`allocation`)과 종류를 나눈다 (검수 P2 — 예산 조정이 '이미 적립한 돈'으로 보고 줄이지 못하게 막는 계산은 `allocation`만 센다).

| 언제 | 남기는 금액 | 중복 방지 |
|---|---|---|
| 월급날 이틀 뒤부터 적용 중인 예산을 처음 확인하거나, 그 이후 예산이 승인될 때 | 그 예산의 쿠션 배정액(`budget_item` bucket=`cushion` 합계) − 그 주기에 이미 남긴 `planned` 합계 = **차액** | `request_key = budget-approve:<예산 ID>`로 중복 방지 |
| 지킬 돈 저장 (`PUT /api/budgets/protect/`)에서 이번 주기의 승인 쿠션 배정액이 바뀔 때 | 누적일 이후에는 새 월 쿠션 적립금 − 이미 남긴 금액 = **차액**. 누적일 전 변경은 그날 적용 중인 예산에 포함 | 값이 같으면 남기지 않음 |

- 예: 지난 주기까지 90만 원이고 이번 주기 배정액이 30만 원이면 월급날 이틀 뒤부터 120만 원이다. 이후 같은 주기에 20만 원으로 바꾸면 차액 10만 원을 빼 110만 원이다.
- 지난 주기의 승인 예산은 이후 조회에서 누적일이 지난 금액을 반영한다. 예산이 없었던 과거 주기는 소급해 만들지 않는다. 추천 · 초안은 누적 대상에서 제외한다.
- **지킬 돈 저장이 승인 예산을 바꾸는 규칙 (검수 P1)**: 생활비를 그대로 두고 저장하면 승인 예산의 저축 · 쿠션 줄만 새 값으로 바꾼다. 생활비 카테고리 배분은 그대로이고, 여윳돈은 `배분 대상 − 항목 합계`라 같이 맞춰진다(쿠션을 올린 만큼 여윳돈이 준다 — 보호한 돈을 다시 쓸 수 있는 돈으로 세지 않음). 다시 추천(LLM)은 하지 않는다. 바꾼 기록은 그 예산의 `validation_summary.protect_updates`에 남는다. 생활비를 바꿨거나, 승인 당시 고정지출이 더 커서 합계가 배분 대상을 넘으면 바꾸지 않고 응답 `approvedBudgetUpdated = false` → 화면이 다시 추천으로 보낸다. 만들어 둔 조정안 · 초안은 지킬 돈 저장이 이미 만료한다.
- **혜리 작업과 합칠 때 남은 것**: 예산 조정 · 분배 실패 복구의 쿠션 필수 판단(`cushion_required`)이 아직 월급 × 3이다 — 직접 정한 목표(`cushion_goal(profile, income)`)를 넘기도록 혜리 코드 호출부를 바꾼다(아래 PATCH 표).

### 응답 (200)

기존 `CushionPage.jsx`의 `cushion` 구조를 유지한다. 누락 설정 안내를 위한 `missing`과 숫자 예상 기간 `etaMonths`를 추가한다.

| 필드 | 타입 | 설명 |
|---|---|---|
| `missing` | 문자열 배열 | `takeHomeIncome`, `cushionMonthly` 중 미설정 항목. 정상 설정이면 `[]` |
| `cushion.now` | 정수 | 확보액, 원 |
| `cushion.goal` | 정수 / null | 목표액, 원. 직접 정한 금액 또는 월급 × 3. 둘 다 없으면 null |
| `cushion.goalLabel` | 문자열 | `월급의 3배` 또는 `직접 정한 금액` |
| `cushion.goalCustom` | 불리언 | 11/13. 목표를 직접 정했으면 true |
| `cushion.defaultGoal` | 정수 / null | 11/13. 기본 목표(월급 × 3). 목표 바꾸기 창의 안내용. 소득 미설정이면 null |
| `cushion.monthly` | 정수 / null | 월 적립금, 원 |
| `cushion.pct` | 정수 / null | 확보액 ÷ 목표 × 100을 반올림(0.5 올림). 100 초과도 그대로 표시 |
| `cushion.bar` | 숫자 / null | 같은 비율을 0~100으로 제한하고 소수 둘째 자리까지 반올림한 막대 길이 |
| `cushion.remain` | 정수 / null | `max(목표 − 확보액, 0)` |
| `cushion.etaMonths` | 정수 / null | 남은 금액 ÷ 월 적립금을 올림한 개월 수. 목표 달성은 0 |
| `cushion.eta` | 문자열 | 예상 기간 또는 미설정 안내 |
| `cushion.useRule` | null | 사용 정책 코드·한도가 미확정이므로 현재는 null. 허용으로 간주하지 않음 |
| `savings.items` | 배열 | 11/18. 저축 계좌(예적금) 목록. 각 줄 `accountId`, `displayName`(끝 4자리만), `status`, `organizationName`, `balance`(정수 / null = 잔액 모름), `fetchedAt` |
| `savings.total` | 정수 / null | 원화 잔액을 아는 저축 계좌 합계. 외화 계좌는 목록에 포함하고 원화 잔액은 null로 반환한다. 합산할 원화 잔액이 없으면 null |
| `savings.unknownCount` | 정수 | 잔액을 모르는 계좌 수 |
| `savings.asOf` | 날짜시각 / null | 가장 최근 조회 시각 |
| `notices.depositDue` | 불리언 | 11/18. 오늘이 쿠션 넣는 날(급여 주기 시작 다음 날)이면 true. 홈 `cushionNotices.depositDue`와 같은 기준. 오늘이 든 급여 주기가 없으면 false |
| `notices.balanceLow` | 불리언 | 11/18. 연결된 은행 계좌 잔액 합계가 쿠션 확보액보다 적으면 true(잔액을 모르는 계좌가 있으면 false). 홈 `cushionNotices.balanceLow`와 같은 기준 |

**저축 계좌 (11/18)**: 은행 연결 때 CODEF 보유계좌 응답에 함께 오는 예적금 계좌를 이미 저장하고 있어서(`finance/sync.py`) 그 저장값만 읽는다. CODEF를 새로 부르지 않는다(최신 잔액은 홈 [새로고침]으로 다시 불러온다). 고르는 기준: 본인 · 해제되지 않은 은행 연결 · `account_type = deposit`, 또는 `other` 중 CODEF 예금 구분 코드 `12`(예적금 — sync가 아직 11 외를 `other`로 저장해서). 입출금 선택(`is_selected`)과 상관없이 보여 준다. 쿠션 확보액(`cushion.now`)과는 따로 보여 주는 참고 정보이며 계산에 쓰지 않는다. PATCH 응답에도 같이 담긴다.

```json
{
  "missing": [],
  "cushion": {
    "now": 300000,
    "goal": 8400000,
    "goalLabel": "월급의 3배",
    "goalCustom": false,
    "defaultGoal": 8400000,
    "bar": 3.57,
    "pct": 4,
    "monthly": 210000,
    "useRule": null,
    "remain": 8100000,
    "eta": "39회 · 약 39개월",
    "etaMonths": 39
  },
  "savings": { "items": [], "total": null, "unknownCount": 0, "asOf": null },
  "notices": { "depositDue": false, "balanceLow": false }
}
```

### 누락·경계 처리

- 프로필이 없어도 200. 알려진 확보액은 반환하고 목표·월 적립금·비율·남은 금액·예상 개월은 null이다. 확보 기록도 없으면 확보액만 0이다.
- 목표가 없으면 `eta = "급여를 먼저 입력해 주세요."`.
- 목표에 도달하면 월 적립금이 없어도 `remain = 0`, `etaMonths = 0`, `eta = "목표 달성"`.
- 남은 금액이 있고 월 적립금이 없으면 `etaMonths = null`, `eta = "월 쿠션 적립금을 먼저 입력해 주세요."`.
- 월 적립금이 0이면 `etaMonths = null`, `eta = "월 적립금이 0원이라 계산할 수 없어요."`.
- 예상 기간은 매달 같은 금액을 적립하고 중간 사용·환급이 없다는 단순 계획이다. 실제 날짜·이자·자동 이체를 보장하지 않는다.
- 화면 후속 작업: null은 0원·0%로 표시하지 않고 미설정 안내로 보여준다. `useRule`은 사용 정책이 정해질 때까지 화면에 표시하지 않고(11/13: '정책 확인 필요' 줄 제거), PATCH 구현 전에는 설정 저장 성공을 표시하지 않는다. 2026-11-06(R26 임시 조치): 쿠션 팝업은 편집·저장 없이 '쿠션 기준 확인'(읽기 전용)으로 바꿨다. 월 적립금 저장 연결은 예산 설정 작업과 조율한 뒤 후속으로 한다.

## PATCH /api/budgets/cushion/ — 목표 금액 (11/13 구현)

머니쿠션 목표 금액을 직접 정한다. 화면: 머니쿠션 카드 [목표 바꾸기] → 가운데 창에서 금액 입력 (`frontend/src/popups/CushionPopup.jsx`).

```json
{ "goalAmount": 5000000 }
```

| 필드 | 타입 | 범위 |
|---|---|---|
| `goalAmount` | 정수 / null | 10,000 ~ 1,000,000,000원. `null` = 기본(월급의 3배)으로 되돌리기 |

- 칸이 없거나 다른 칸(`cushionMonthly` 포함)이 있거나 범위 밖 · 문자열 · 소수 · 불리언이면 400 `invalid`. 월 쿠션 적립금은 지킬 돈 설정(`PUT /api/budgets/protect/`)에서만 바꾼다.
- 저장: `financial_profile.cushion_goal_amount`. `cushion_target`(현재 목표)도 새 목표로 맞춘다. 같은 값이면 바꾸지 않고 200.
- 직접 정한 목표는 월급이 바뀌어도 그대로다. `null`로 되돌리면 다시 월급 × 3을 따라간다.
- 값이 바뀌면 만들어 둔 초안 · 검수된 예산(`draft` · `validated`)을 만료한다 — 쿠션 필수 판단 · 추천 쿠션이 달라질 수 있다. 승인 예산 · 쿠션 기록은 바꾸지 않는다.
- 프로필이 없으면 409 `not_ready`. 응답은 GET과 같은 전체 구조.

**목표를 같이 쓰는 곳 (모두 바뀜)** — 계산은 `budgets.protect.cushion_goal(profile, income)`

| 곳 | 내용 |
|---|---|
| 머니쿠션 GET | `goal`, `goalLabel`, `goalCustom`, 진행률 · 남은 금액 · 예상 기간 |
| 지킬 돈 GET · PUT | `cushion.target`(목표까지 N개월), **P-08 쿠션 필수**: 계좌 잔액 < 목표면 필수 |
| 예산 조정 · 분배 실패 복구 (혜리 코드) | **아직 월급 × 3 고정.** `budgets.protect.cushion_required(..., goal)`에 `cushion_goal(profile, income)`를 넘기도록 혜리 작업과 합칠 때 연결 |

## 오류

| 상황 | HTTP | code | fields |
|---|---|---|---|
| 비로그인·세션 만료 | 403 | `not_authenticated` | 없음 |
| PATCH CSRF 실패(구현 후) | 403 | `permission_denied` | 없음 |
| PATCH 형식·null·미허용 칸·금액 오류(구현 후) | 400 | `invalid` | 해당 칸 또는 `nonFieldErrors` |
| PATCH 선행 설정·배분 조건 불충족(구현 후) | 409 | `not_ready` | 없음 |
| 현재 PATCH·POST·PUT·DELETE | 405 | `method_not_allowed` | 없음 |

GET은 자료가 없다는 이유로 404·409를 반환하지 않는다. 로그인된 변경 요청은 DRF 인증 단계에서 CSRF를 먼저 확인할 수 있으므로, 현재 PATCH의 405 확인에는 유효한 CSRF 토큰을 사용한다.

## 검증

`python backend/manage.py test budgets.test_cushion budgets.test_protect budgets.test_salary`

별도 PostgreSQL 테스트 DB에서 합계·사용자 격리·인증·미설정·목표 갱신·반올림·목표 초과·0원 적립·읽기 전용·PATCH 미구현을 검사한다. Swagger 변경, 프런트 수정, 개발 DB 마이그레이션은 포함하지 않는다.

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
- 쿠션 확보액은 앱의 확정 증감 기록이며 은행 잔액과 다르다. GET 호출 · 시간 경과로는 더하지 않는다. **11/13부터 예산 승인 · 지킬 돈 저장 때 쿠션 배정액을 쌓는다** (아래 '보호 중인 쿠션 쌓기').
- 급여일·주기를 사용하지 않으므로 GET은 급여 예약 적용·주기 생성·예산 만료·프로필 생성 등 DB 변경을 하지 않는다.

### 보호 중인 쿠션 쌓기 (R27, 11/13)

`now`(확보액) = `cushion_entry` 합계. 아래 두 곳에서 종류 **`planned`(예산 보호)** 줄을 남긴다 (`budgets/cushion.py`). 실제 이체 완료를 뜻하지 않는 **예산에서 보호한 기록**이라, 실제로 옮긴 적립(`allocation`)과 종류를 나눈다 (검수 P2 — 예산 조정이 '이미 적립한 돈'으로 보고 줄이지 못하게 막는 계산은 `allocation`만 센다).

| 언제 | 남기는 금액 | 중복 방지 |
|---|---|---|
| 예산이 승인될 때 — 일반 승인 · 대화형 조정 · 분배 실패 복구 모두 (`budgets/signals.py`가 `Budget`이 `approved`로 저장될 때 부름, 검수 P1) | 그 예산의 쿠션 배정액(`budget_item` bucket=`cushion` 합계) − 그 주기에 이미 남긴 `planned` 합계 = **차액** | `request_key = budget-approve:<예산 ID>` — 재시도 · 중복 저장은 한 번만 |
| 지킬 돈 저장 (`PUT /api/budgets/protect/`)에서 생활비는 그대로 두고 저축 · 월 쿠션 적립금만 바꿨고 이번 주기에 승인한 예산이 있을 때 | 승인 예산의 저축 · 쿠션 줄을 새 값으로 바꾼 뒤(`update_approved_split`, 아래) 새 월 쿠션 적립금 − 이미 남긴 금액 = **차액** | 값이 같으면 남기지 않음 |

- 예: 지난 주기까지 90만 + 이번 주기 30만 승인 → 120만. 같은 주기에 20만으로 다시 승인하거나 지킬 돈에서 20만으로 바꾸면 −10만 → 110만.
- 추천 · 초안은 쌓지 않는다. 생활비 초과로 자동 차감하지 않는다(사용 승인은 아직 없음). 처음부터 `approved`로 만드는 예산(시드 · 테스트)은 항목이 없어 건너뛴다.
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
  }
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

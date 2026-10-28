# 쿠션 API — 조회 · 설정 변경

> 담당: 권세진 · 작성일: 2026-10-28
> GET 구현 완료. PATCH는 후속 구현을 위한 명세이며 현재 호출하면 405다.
> 근거: [공통 규칙](../API_RULES.md), [지킬 돈 P-08·P-09](./budgets.md), CUS-001(목표 = 월급 × 3).

## GET /api/budgets/cushion/

로그인한 회원의 확보액·목표·월 적립금·재적립 예상 기간을 조회한다. 세션 쿠키가 필요하며 GET은 CSRF 토큰이 필요 없다. 본문·회원 ID 입력은 받지 않는다. 금융기관 호출이나 금융 조회 동의는 필요 없다.

### 데이터 원천

- 확보액: 본인의 `cushion_entry.amount` 전체 합계. 기록이 없으면 0원이다. 초기금·적립·사용·환급·정정의 부호를 그대로 합산하며, 지난 주기·교체된 예산의 기록도 포함한다. 마이너스 합계가 있더라도 숨기거나 0원으로 덮지 않는다.
- 월 적립금: `financial_profile.cushion_monthly`. 미설정은 null이며, 임의로 소득의 10%를 채우지 않는다. 0원 설정과 미설정은 구분한다.
- 목표: 현재 `financial_profile.monthly_take_home_income × 3`. `cushion_target`은 지킬 돈 저장 당시 값이므로 급여 변경 후 오래된 값일 수 있다. GET은 현재 소득으로 계산하고 저장값을 고치지 않는다. 소득이 없으면 과거 목표를 대신 쓰지 않는다.
- 기존 소득이 0 이하이면 목표를 계산하지 않고 `missing`에 `takeHomeIncome`을 표시한다. 월 적립금 음수는 기존 DB 제약에서 금지한다.
- 쿠션 확보액은 앱의 확정 증감 기록이며 은행 잔액과 다르다. 예산 승인·GET 호출·시간 경과만으로 월 적립금을 확보액에 더하지 않는다. 증감 기록을 만드는 작업은 별도다.
- 급여일·주기를 사용하지 않으므로 GET은 급여 예약 적용·주기 생성·예산 만료·프로필 생성 등 DB 변경을 하지 않는다.

### 응답 (200)

기존 `CushionPage.jsx`의 `cushion` 구조를 유지한다. 누락 설정 안내를 위한 `missing`과 숫자 예상 기간 `etaMonths`를 추가한다.

| 필드 | 타입 | 설명 |
|---|---|---|
| `missing` | 문자열 배열 | `takeHomeIncome`, `cushionMonthly` 중 미설정 항목. 정상 설정이면 `[]` |
| `cushion.now` | 정수 | 확보액, 원 |
| `cushion.goal` | 정수 / null | 목표액, 원. 소득 미설정이면 null |
| `cushion.goalLabel` | 문자열 | `월급의 3배` |
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
- 화면 후속 작업: null은 0원·0%로 표시하지 않고 미설정 안내로 보여준다. `useRule`은 “정책 확인 필요”로 표시하고, PATCH 구현 전에는 설정 저장 성공을 표시하지 않는다. 현재 팝업의 목표 수정·사용 정책 선택은 아래 계약에 맞춰 수정해야 한다.

## PATCH /api/budgets/cushion/ — 명세만

월 쿠션 적립금만 변경한다. 로그인과 CSRF가 필요하다. 이 PR에서는 구현하지 않는다. 쿠션 사용 승인·한도·확보액 정정·입출금은 이 API의 기능이 아니다.

### 요청

| 필드 | 타입 | 생략 | null | 범위 |
|---|---|---|---|---|
| `cushionMonthly` | 정수 | 기존 값 유지 | 400 | 0 이상. 지킬 돈의 합계 제한과 P-08 적용 |

```json
{ "cushionMonthly": 200000 }
```

- 실수·숫자 문자열·불리언·배열·빈 문자열은 허용하지 않는다.
- 목표는 월급 × 3으로 고정되어 `cushionTarget`·`goal`은 보낼 수 없다. `now`, `livingBudget`, `savingFloor`, `useRule`, `cushionUsePolicy`, `cushionEnabled` 등 표에 없는 필드는 400이다.
- `{}`는 변경 없이 GET 형태의 200 응답. 같은 월 적립금 재요청도 중복 반영하지 않는다. 별도 `Idempotency-Key`는 필요 없다.

### 저장 전 검사와 처리

1. 회원 잠금·트랜잭션 안에서 처리하고, 빈 본문 객체·허용 필드·타입을 검사한다. 빈 객체는 그대로 반환한다.
2. `current_salary_profile(user, today)`로 예약 급여일을 반영한 뒤 지킬 돈과 동일한 `protect_json` 상태를 계산한다. 급여·급여일·자취 여부 또는 기존 생활비·저축·쿠션 설정이 없으면 409 `not_ready`. 최초 지킬 돈 설정은 PUT `/api/budgets/protect/`에서 한다.
3. 지킬 돈의 고정지출 초과·저축 초과·카드 첫 청구서 확인 보류도 같은 409 `not_ready`로 처리한다.
4. 기존 `living_budget`·`savings_floor`에 요청한 월 적립금을 합쳐 공통 지킬 돈 검증을 적용한다. 기존 저축이 최소 저축보다 작아진 경우도 저장하지 않는다. 생활비·저축을 자동으로 줄이지 않는다.
5. **P-08:** 입출금 계좌 잔액(홈·지킬 돈과 같은 기준)이 월 실수령액 × 3보다 작거나 모르면 쿠션 적립금 0원은 400. 이 판단에는 쿠션 확보액(`now`)을 쓰지 않는다.
6. `living_budget + savings_floor + 요청 cushion_monthly ≤ base.available`이어야 한다. 초과하면 400 `invalid`, `fields.nonFieldErrors`로 합계 오류를 반환한다. 남는 여윳돈은 차액으로 다시 계산한다.
7. 통과하면 지킬 돈과 같은 저장 처리를 재사용해 `cushion_monthly`와 현재 목표를 저장한다. 생활비·저축은 그대로 두며, 실제 변경 시에만 승인 전 예산(`draft`, `validated`)을 만료한다. 승인 예산·기존 쿠션 증감 기록은 수정하지 않는다(P-09). 실패하면 부분 저장하지 않는다.
8. 성공 응답은 GET과 같은 전체 구조다. 월 적립금 변경은 향후 계획 변경이며, 이번 달 확보액에 즉시 더하지 않는다.

공통 검증은 PUT용 요청을 내부 HTTP로 보내는 방식이 아니라 지킬 돈의 검사·저장 함수를 서비스 함수로 분리해 함께 사용한다. 실제 PATCH 구현 때 재사용 범위와 회귀 테스트를 확인한다.

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

# 급여 API

화면: `frontend/src/pages/SalaryPage.jsx`. 공통 규칙: `docs/API_RULES.md`.

## GET · PUT `/api/budgets/salary/`

로그인한 본인의 월 실수령액과 매월 급여일을 조회·저장한다. 금융 조회 동의는 필요 없다.
세션 인증을 사용하며 PUT에는 CSRF 토큰이 필요하다. 최초 저장과 수정 모두 200이다.

## 보내는 값

| 이름 | 타입 | 규칙 |
|---|---|---|
| `takeHomeIncome` | 정수 | 필수, 1~9,999,999,999,999원. MoneyInput의 13자리 상한과 동일 |
| `payday` | 정수 | 필수, 매월 1~31일 |
| `paydayApplyFrom` | 문자열 | 기존 급여일 변경 시 필수. `current_cycle` 이번 주기 / `next_cycle` 다음 주기 |

숫자 문자열·소수·boolean·null은 받지 않는다. 미정의 필드도 400이다.
주급·격주 지급, 휴일 보정, 세전 금액은 지원하지 않는다. 없는 날짜는 그 달 말일로 계산한다.
PUT은 두 필수 값을 모두 받는다. 같은 값으로 반복 요청해도 중복 행을 만들지 않는다.

최초 등록:

```json
{"takeHomeIncome": 2800000, "payday": 25}
```

급여일 변경:

```json
{"takeHomeIncome": 2800000, "payday": 10, "paydayApplyFrom": "next_cycle"}
```

## 적용 시점 (2026-10-28 결정)

- 최초 등록은 바로 적용한다. 최초 등록에 `next_cycle`을 보내면 400이다.
- 기존 급여일과 다른 날을 보내면 화면에서 **이번 주기부터 / 다음 주기부터**를 반드시 선택한다. 선택이 없으면 금액도 함께 저장하지 않고 400을 반환한다.
- `current_cycle`: 새 급여일 기준으로 오늘이 속한 주기를 즉시 다시 계산한다.
- `next_cycle`: **오늘보다 뒤에 오는 가장 가까운 새 급여일**에 전환한다. 현재 주기 시작일은 유지하고, 종료일을 전환일 전날로 바꾼다. 오늘이 새 급여일이면 다음 달 해당일에 전환한다.
- 예: 10/28에 현재 10/25~11/24 주기의 급여일을 10일로 수정할 때, 이번 주기 선택은 10/10~11/9, 다음 주기 선택은 10/25~11/9를 거쳐 11/10부터 새 주기가 된다.
- 월 실수령액은 저장 즉시 적용한다. 선택은 급여일에만 적용되며 미래 월급 금액 예약은 이번 범위에 없다.
- 예약 중 같은 새 급여일과 `next_cycle`을 재전송해도 전환일을 뒤로 미루지 않는다.
- 예약 중 현재 급여일을 보내고 선택을 생략하면 예약을 유지한다(금액만 수정 가능). 현재 급여일과 `current_cycle`을 보내면 예약을 취소한다. 다른 날을 선택하면 예약을 교체한다.
- 전환일 이후 급여 GET·PUT, 홈, 활성 주기를 읽는 고정지출·저축 API, 대출 납부 예정 저장에서 예약을 반영한다. 별도 스케줄러는 필요 없다. 첫 전환 주기의 이전 주기를 알 수 있도록 전환 정보는 DB에 보존한다.
- 지킬 돈·추천·카테고리 잠금·승인·예산 조회는 회원 잠금 뒤 `current_salary_profile(user, today)`로 예약을 반영한 프로필을 읽고, 주기 날짜는 `salary_cycle_dates(profile, today)`로 계산한다. 별도 호출부에서 예약 적용만 필요하면 `activate_salary_if_due(user, today)`를 사용한다. 일반 `cycle_dates`만 쓰면 전환 중인 주기의 경계를 놓칠 수 있다.

## 받는 값

GET과 PUT은 현재 적용 값과 변경 예약을 반환한다. PUT에 보낸 새 급여일이 예약되면 `payday`는 아직 기존 급여일이다.

```json
{
  "takeHomeIncome": 2800000,
  "payday": 25,
  "pendingPaydayChange": {"payday": 10, "effectiveOn": "2026-11-10"}
}
```

전환 이후에는 `payday: 10`, `pendingPaydayChange: null`이다.
미등록 GET은 `{"takeHomeIncome": null, "payday": null, "pendingPaydayChange": null}`이며 프로필을 만들지 않는다.

## 저장과 연동

- `financial_profile.monthly_take_home_income`에 월 실수령액을 그대로 저장한다. 예산 ML도 이 값을 소득 입력으로 읽는다. 기존 `monthly_disposable_income`에 복제하거나 고정비를 빼지 않는다.
- 급여일과 전환 정보 외에 생년월일·성별·자취 여부·자차·지킬 돈·쿠션 등 다른 프로필 값은 보존한다.
- 실제 값 변경과 예약 적용 시 본인의 `draft`·`validated` 예산만 `expired`로 바꾼다. 동일 값 재요청은 새로 만든 초안을 만료시키지 않는다. 승인 예산은 자동 재배분하지 않는다.
- 회원 → 프로필 순서로 잠그고 트랜잭션으로 저장한다. 홈도 회원 잠금 뒤에 급여 정보를 읽는다.
- 이미 활성 주기가 있으면 저장 시 변경된 주기 경계를 반영한다. 처음 등록한 회원의 주기 생성은 기존처럼 홈 조회가 담당한다.
- 급여일을 변경한 회원은 납부 예정을 **고정지출·대출별 납부 월**로 구분한다. 예전 주기 키로 만들어진 예정도 같은 납부 월이면 재사용하며 기존 ID·납부 연결을 보존한다. 새 예정의 키는 `recurring:<id>:month:YYYY-MM` 또는 `loan:<id>:month:YYYY-MM`이다.
- 주기가 길어져 월 납부일이 두 번 있으면 두 달의 예정이 각각 필요하다. 일부 납부·완납·납부 연결이 있는 대출 회차는 금액·날짜를 변경하지 않는다. 고정지출의 기존 활성 회차도 급여일 변경만으로 덮어쓰지 않는다.
- 주기 밖으로 밀린 예정은 실제 납부 의무이므로 급여일 변경만으로 취소하지 않는다. 이후 그 날짜를 포함하는 주기에서 같은 ID로 조회한다. 홈의 표시 범위·미납 상태·거래 매칭 규칙은 유지한다.
- 급여일을 변경하지 않은 회원의 기존 회차 키와 처리 방식은 유지한다. `payday_revision`으로 변경 이력을 구분한다.
- `users/0010_financialprofile_payday_change` 마이그레이션이 필요하다. 기존 회원은 변경 예약이 없는 상태로 유지된다.
- 프런트의 선택 확인창·실제 API 연결과 Swagger 갱신은 별도 작업이다.

## 실패

| 상황 | 상태 | code |
|---|---|---|
| 로그인 없음 | 403 | `not_authenticated` |
| CSRF 없음·불일치 | 403 | `permission_denied` |
| 필수 값·범위·타입·변경 시점 선택 오류 | 400 | `invalid` |
| 잘못된 JSON | 400 | `parse_error` |
| POST·PATCH·DELETE | 405 | `method_not_allowed` |

```json
{
  "error": {
    "code": "invalid",
    "message": "입력값을 확인해 주세요.",
    "fields": {"paydayApplyFrom": ["이번 주기부터인지 다음 주기부터인지 선택해 주세요."]}
  }
}
```

## 자동 검사

PostgreSQL 개발 환경에서 `python manage.py test budgets.test_salary budgets.test_home budgets.test_fixed finance.test_loans`를 실행한다.
Django가 별도 테스트 DB를 생성·삭제한다. 개발 DB의 급여 정보는 변경하지 않는다.

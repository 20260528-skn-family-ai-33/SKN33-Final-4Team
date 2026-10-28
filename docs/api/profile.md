# 내 정보 API

화면: `frontend/src/pages/ProfilePage.jsx`. 공통 규칙: `docs/API_RULES.md`.

## GET · PUT `/api/profile/`

로그인한 본인의 기본 정보·소비 우선순위·카테고리 최소 금액을 조회하고 저장한다.
다른 회원의 ID를 받지 않으며, 금융기관 조회 동의나 CODEF 호출은 필요 없다.

| 항목 | GET | PUT |
|---|---|---|
| 인증 | 세션 로그인 필수 | 세션 로그인·CSRF 필수 |
| 요청 | 본문 없음 | 아래 여섯 필드 전체 |
| 성공 | 200, 아래 응답 | 최초 저장·수정 모두 200, 저장한 응답 |
| 미등록 | 기본 값 null, 목록 [] | 처음 저장할 때 프로필 생성 |

## 보내는 값

PUT은 이 API가 관리하는 여섯 항목의 전체 교체다. 누락·null은 400이며 최소 금액을 설정하지 않으면 `categoryMinimums: []`를 보낸다.

| 필드 | 타입 | 규칙 |
|---|---|---|
| `birthDate` | 문자열 | `YYYY-MM-DD`, 실제 날짜, 오늘 이후 불가, 만 0~120세 |
| `gender` | 문자열 | `male` 남성 / `female` 여성 |
| `housingType` | 문자열 | `alone` 자취 / `family` 본가 거주 |
| `hasCar` | boolean | true 보유 / false 미보유. 0·1·문자열 불가 |
| `priorities` | 배열 | 3~10개, `category`와 정수 `rank` |
| `categoryMinimums` | 배열 | 0~11개, `category`와 정수 `minAmount` |

- 우선순위: 활성 소비 카테고리 10개 중 선택. `rank`는 1부터 항목 수까지 연속, 순위·카테고리 중복 불가. 요청 배열 순서는 자유이며 응답은 순위순이다. `ETC`·`UNCLASSIFIED`는 불가.
- 최소 금액: 활성 예산 카테고리 10개와 `ETC` 중 원하는 항목만 설정. 우선순위에 없는 카테고리도 가능. 항목당 1~9,999,999,999원(화면 입력 상한 10자리), 중복 불가. 0원으로 저장하지 않는다. 응답은 카테고리 표시 순서다.
- 최소 금액 합계와 생활비 비교는 예산 생성 단계에서 검사한다. 아직 급여·생활비가 없어도 내 정보를 저장할 수 있다.
- 알 수 없는 필드, 잘못된 자료형·카테고리, 소수·문자열 금액은 400이다. 배열 내부에서도 미정의 필드를 허용하지 않는다.
- 화면의 생년월일 6자리는 연도를 확정해 `YYYY-MM-DD`로, 성별 표시와 자차 선택은 위 코드·boolean으로 변환해서 보내야 한다. 프런트 API 연결은 별도다.

```json
{
  "birthDate": "1999-03-15",
  "gender": "female",
  "housingType": "alone",
  "hasCar": false,
  "priorities": [
    {"category": "FOOD", "rank": 1},
    {"category": "CULTURE_LEISURE", "rank": 2},
    {"category": "TRAVEL_LODGING", "rank": 3}
  ],
  "categoryMinimums": [
    {"category": "FOOD", "minAmount": 300000},
    {"category": "ETC", "minAmount": 10000}
  ]
}
```

## 받는 값

GET과 PUT 모두 위 예시와 같은 여섯 필드를 반환한다. 두 목록의 각 항목에는 `categoryName`도 함께 담는다. DB의 nullable 값은 그대로 null이다. GET은 행을 생성하지 않는다.

예: `priorities: [{"category": "FOOD", "categoryName": "식비", "rank": 1}, ...]`, `categoryMinimums: [{"category": "FOOD", "categoryName": "식비", "minAmount": 300000}]`.
`categoryName`은 응답 전용이며 PUT에는 보내지 않는다.

미등록 예시:

```json
{
  "birthDate": null,
  "gender": null,
  "housingType": null,
  "hasCar": null,
  "priorities": [],
  "categoryMinimums": []
}
```

## 저장 규칙

- `financial_profile`의 `birth_date`·`gender`·`housing_type`·`has_car`만 변경한다. 급여·급여일·쿠션·지킬 돈·대출 보유 응답은 보존한다.
- `category_priority`·`category_minimum`은 본인 목록만 전체 교체한다. 빠진 항목은 삭제하며 빈 최소 금액 배열은 전체 해제다.
- 회원 행을 먼저 잠그고 프로필·두 목록을 한 트랜잭션으로 저장한다. 일부 실패 시 모두 롤백하며 동시 저장의 목록이 섞이지 않는다.
- 동일한 PUT 반복은 같은 설정을 만든다. 별도의 `Idempotency-Key`는 요구하지 않는다.
- 자취 여부는 지킬 돈 비율의 입력이다(자취 55/30/5/10, 본가 45/40/5/10). 이 API는 비율 계산·예산 변경·온보딩 완료 처리를 하지 않는다. 기존 `budget_direction` 칼럼은 사용·변경하지 않는다.

## 실패

| 상황 | HTTP | code |
|---|---|---|
| 비로그인 | 403 | `not_authenticated` |
| CSRF 누락·불일치 | 403 | `permission_denied` |
| 누락·타입·값·목록 오류 | 400 | `invalid` |
| 잘못된 JSON | 400 | `parse_error` |
| POST·PATCH·DELETE | 405 | `method_not_allowed` |

입력 오류는 `error.fields`에 필드별 메시지를 담는다. 배열 항목 오류는 해당 필드의 배열 안에 항목별 오류를 담는다.

```json
{
  "error": {
    "code": "invalid",
    "message": "입력값을 확인해 주세요.",
    "fields": {"priorities": ["순위는 1부터 빠짐없이 지정해 주세요."]}
  }
}
```

## 의존성과 범위

- 최신 main에서 시작하며 PR #54의 `housing_type` 모델 정의와 `users/0009_financialprofile_housing_type`을 그대로 재사용한다. 다른 이름의 중복 마이그레이션은 만들지 않는다.
- #54의 예산 명세·자물쇠 모델 변경은 이 작업에 포함하지 않는다.
- Swagger 구현 상태·생성 파일과 프런트는 수정하지 않는다.

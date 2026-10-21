# API 규칙 (Django ↔ React)

React(`frontend/`)는 화면만, Django(`backend/`)는 데이터·계산·로그인을 맡는다. 둘은 `/api/...` 주소로 JSON을 주고받는다.
아래 규칙은 `backend/config/settings.py`의 `REST_FRAMEWORK`와 `backend/config/api.py`, `frontend/src/utils/api.js`에 이미 들어 있다. **API를 새로 만들 때 이 문서를 기준으로 한다.**

## 한눈에 보기

| 항목 | 규칙 | 예시 |
|---|---|---|
| 주소 | `/api/` + 화면 주소 모양, 끝에 `/` | `/api/finance/transactions/`, `/api/finance/transactions/<id>/` |
| 읽기 | `GET` | 화면 데이터, 목록, 하나 보기 |
| 만들기 | `POST` | 고정지출 추가, 예산 승인 |
| 고치기 | `PATCH` (바꾼 칸만 보냄) | 거래 카테고리 수정 |
| 지우기 | `DELETE` | 고정지출 삭제 |
| 키 이름 | React는 `camelCase`, 파이썬은 `snake_case` — **자동 변환** | `livingUsed` ↔ `living_used` |
| 돈 | **원 단위 정수** (소수·문자열·콤마 없음) | `420000` |
| 퍼센트 | 0~100 숫자. **증감률은 예외**: 음수 · 100 초과 가능, 비교 대상이 0원이면 `null` | `42`, `3.57`, `-12`, `null` |
| 날짜 | `YYYY-MM-DD` | `"2026-10-19"` |
| 매월 N일 | 그 달에 N일이 없으면 **그 달 말일** (2월 31일 → 2/28, 윤년 2/29). 급여일 · 고정지출 출금일 · 대출 납부일 공통. 주말·공휴일로 밀리는 건 계산하지 않는다 (실제 출금은 ±3일로 맞춤) | `paymentDay: 31` |
| 금액 범위 | API마다 명세에 적는다 (예: 고정지출 1,000원 ~ 1억 원, 카테고리 최소 금액 1원 이상). 범위 밖은 400 `invalid` | `fields.amount` |
| 시각 | ISO 8601, 한국 시간 | `"2026-10-19T15:30:00+09:00"` |
| ID | UUID 문자열 (ERD 기준) | `"3f2c…"` |
| 빈 값 | `null` (빈 문자열 `""`로 대신하지 않음) | `"link": null` |
| 계좌 · 카드 | 요청은 ID만, 응답은 `displayName` · `status`가 든 객체 ([계좌 · 카드 표시](#계좌--카드-표시)) | `"국민 급여통장 · ****5678"` |
| 카테고리 | 영문 대문자 코드 ([카테고리 코드](#카테고리-코드) 표). 요청은 코드만, 응답은 코드 + `categoryName` | `"HOUSING_TELECOM"` |
| 로그인 | Django **세션 쿠키** (토큰 없음) | 로그인 API가 쿠키를 심어 줌 |
| 저장 요청 보안 | **CSRF 토큰** — `apiSend`가 자동으로 붙임 | `X-CSRFToken` 헤더 |

## 성공 응답

- 감싸지 않고 데이터를 그대로 보낸다.
- **화면 API**(예: `/api/home/`)는 그 화면에 필요한 묶음을 한 번에 보낸다.
- **목록 API**는 `{"items": [...]}`로 보낸다. 목록과 함께 필요한 정보(조회 기간 `period`, 요약 `summary`, 다음 페이지 `next`)는 **같은 객체에 키를 추가**한다. 배열을 그대로 보내지 않는 이유가 이것이다.

```json
// GET /api/home/
{ "period": { "label": "10.01 — 10.31", "dday": 21 }, "home": { "livingUsed": 470000 }, "mustPay": [ ... ] }
```

## 에러 응답 — 항상 이 모양

```json
{ "error": { "code": "invalid", "message": "입력값을 확인해 주세요.", "fields": { "livingBudget": ["이 값이 0보다 크거나 같은지 확인하세요."] } } }
```

| 상황 | 상태 코드 | `code` | React에서 |
|---|---|---|---|
| 입력값 틀림 | 400 | `invalid` | `err.fields`로 칸 아래에 표시 |
| JSON 깨짐 | 400 | `parse_error` | 개발 중 버그 |
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 보내기 |
| 권한 없음 · CSRF 실패 | 403 | `permission_denied` | `err.message` 표시 |
| 없음 · 남의 데이터 | 404 | `not_found` | "찾을 수 없어요" |
| 안 되는 메서드 | 405 | `method_not_allowed` | 개발 중 버그 |
| 서버 버그 (배포) | 500 | `server_error` | "잠시 후 다시 시도해 주세요." |

> 세션 로그인은 로그인 안 했을 때 401이 아니라 **403 + `not_authenticated`**를 준다. 상태 코드가 아니라 `code`로 구분한다.

## 보안 · 소유권

- 모든 데이터는 **로그인한 사람 것만** 돌려준다. 조회할 때 항상 `user=request.user`로 거른다. (ERD: 단일 FK + 코드에서 소유 확인)
- 남의 데이터 ID로 요청하면 403이 아니라 **404**로 답한다. 그런 데이터가 있다는 것도 알려주지 않기 위해서다.
- 예외: **요청 본문에 ID를 담아 보내는 경우**(계좌 여러 개 선택 저장, 고정지출의 결제 계좌 지정 등)는 주소의 대상이 아니라 입력값이므로 **400 `invalid`**로 답한다. 이때도 존재 여부를 드러내지 않도록 없는 것 · 남의 것 · 고를 수 없는 것 모두 **같은 문구**를 `fields`에 넣는다 (예: `"선택할 수 없는 계좌예요."`). 여러 개 중 하나라도 틀리면 **전부 저장하지 않는다**.
- 은행 비밀번호, CODEF 키, `connectedId`, 계좌번호 원문은 **응답에 넣지 않는다**. 계좌번호는 `****1234`로 보낸다.
- 지금은 로그인 없이 다 열려 있다(`AllowAny`). 회원 모델(`users.User`)은 이미 있고, **로그인 API가 붙으면** 기본값을 `IsAuthenticated`로 바꾼다.

## 카테고리 코드

2026-10-21 확정. 목록은 10/20 회의(가게 업종 기준 10개 + 상태값 2개) 그대로, 코드 이름만 새로 정했다.
**서버는 코드로만 판단한다.** 화면 이름은 바뀔 수 있어도 코드는 바꾸지 않는다. 이름·순서는 `frontend/src/utils/categories.js`, DB 초기 데이터(`category` 표)와 맞춘다.

| 순서 | 코드 | 화면 이름 | 소비 우선순위 | 예산 칸 · 최소 금액 | 거래 · 고정지출에서 고르기 | ML 정답 |
|---|---|---|---|---|---|---|
| 1 | `FOOD` | 식비 | O | O | O | O |
| 2 | `HOUSING_TELECOM` | 주거·통신 | O | O | O | O |
| 3 | `TRANSPORT_CAR` | 교통·차량 | O | O | O | O |
| 4 | `HOUSEHOLD` | 생활용품 | O | O | O | O |
| 5 | `FASHION_SHOPPING` | 패션·쇼핑 | O | O | O | O |
| 6 | `MEDICAL_HEALTH` | 의료·건강 | O | O | O | O |
| 7 | `BEAUTY` | 뷰티·미용 | O | O | O | O |
| 8 | `EDUCATION` | 교육 | O | O | O | O |
| 9 | `CULTURE_LEISURE` | 문화·여가 | O | O | O | O |
| 10 | `TRAVEL_LODGING` | 여행·숙박 | O | O | O | O |
| 11 | `ETC` | 기타 | X | O | O | X |
| 12 | `UNCLASSIFIED` | 확인 필요 (미분류) | X | X | X (분류 전 상태값) | X |

- 요청: `{ "category": "HOUSING_TELECOM" }` · 응답: `{ "category": "HOUSING_TELECOM", "categoryName": "주거·통신" }`
- 저축 · 투자 · 대출 상환 · 카드값은 카테고리가 아니다. 저축은 저축 칸(`bucket=savings`), 대출 · 카드값은 각자의 납부 의무로 따로 다룬다.
- JSON 안에 넣는 코드(`conversation.slots`, `suggestion_candidates` 등)는 FK 검사가 안 되므로 저장 전에 이 표에 있는지 확인한다.
- 코드를 추가 · 변경하려면 이 표를 먼저 고치고(나은), 그다음 DB 초기 데이터와 `categories.js`를 맞춘다.

## 계좌 · 카드 표시

2026-10-21 확정. 계좌 · 카드가 나오는 **모든 API**(연결 관리 · 거래 · 고정지출 · 홈 등)가 같은 규칙을 쓴다. 화면에서 이름을 따로 조합하지 않는다.

**요청은 ID만, 응답은 객체로** (없으면 `null`)

```json
"paymentAccount": { "accountId": "3f2c…", "displayName": "국민 급여통장 · ****5678", "status": "connected" }
"paymentCard":    { "cardId": "9a1b…", "displayName": "신한 Deep Dream · ****4321", "status": "connected" }
```

**`displayName` — 서버가 만든다. 번호는 끝 4자리만**

| 대상 | 규칙 | 예시 |
|---|---|---|
| 계좌 | `{은행 짧은 이름} {별칭 → 없으면 계좌명 → 둘 다 없으면 '입출금'} · ****{끝 4자리}` | `국민 급여통장 · ****5678` · `국민 KB국민ONE통장 · ****5678` · `국민 입출금 · ****5678` |
| 카드 | `{카드명 → 없으면 '{카드사} 카드'} · ****{끝 4자리}` | `신한 Deep Dream · ****4321` · `신한 카드 · ****4321` |

**`status`**

| 값 | 뜻 | 화면 |
|---|---|---|
| `connected` | 정상 연결 | 그대로 표시 |
| `relogin` | 다시 연결 필요 (비밀번호 변경 등으로 조회만 멈춤) | "다시 연결 필요" 표시. 선택 · 기존 연결은 유지 |
| `disconnected` | 사용자가 연결 해제 | "연결 해제됨 · 다시 고르기". 새로 고를 수 없음 |

- 기록 자체가 없어져 칸이 비면(DB `SET_NULL`) 객체가 `null`이고, 화면은 "출금 계좌 확인 필요"처럼 안내한다. 이름 · 번호를 추측해 채우지 않는다.
- **결제 수단으로 고를 수 있는 것**: 계좌 = 내 계좌 · 관리 대상(`is_selected`) · 입출금 · `disconnected` 아님 · 저축 용도 아님 / 카드 = 내 카드 · 관리 대상 · `disconnected` 아님. 계좌와 카드를 동시에 지정하지 않는다.

## 코드로 보면

**Django (`앱/api.py`)** — 파이썬 이름 그대로 돌려주면 camelCase로 바뀌어 나간다.

```python
@api_view(["GET"])
def home(request):
    return Response({"living_used": 470000})   # → {"livingUsed": 470000}
```

**React** — 읽기는 `useApi`, 저장은 `apiSend`.

```jsx
const { data, error } = useApi('/api/home/')

try {
  await apiSend('/api/budgets/fixed/', 'POST', {
    name: '월세', amount: 700000, paymentDay: 25, category: 'HOUSING_TELECOM',
    paymentAccountId: '3f2c…', paymentCardId: null,   // 둘 다 null = 결제 수단 모름
  })
} catch (err) {
  // err.message · err.fields · err.code
}
```

## 새 API 만드는 순서

1. `backend/<앱>/api.py`에 함수를 만든다 (`@api_view`, 입력 검사는 `serializers`).
2. `backend/config/api_urls.py`에 주소를 한 줄 추가한다.
3. 브라우저로 `http://127.0.0.1:8000/api/...`를 열어 확인한다 (DEBUG면 보기 좋은 화면).
4. React 화면에서 `useApi` / `apiSend`로 부른다.

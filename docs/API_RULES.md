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
| 퍼센트 | 0~100 숫자 | `42`, `3.57` |
| 날짜 | `YYYY-MM-DD` | `"2026-10-19"` |
| 시각 | ISO 8601, 한국 시간 | `"2026-10-19T15:30:00+09:00"` |
| ID | UUID 문자열 (ERD 기준) | `"3f2c…"` |
| 빈 값 | `null` (빈 문자열 `""`로 대신하지 않음) | `"link": null` |
| 로그인 | Django **세션 쿠키** (토큰 없음) | 로그인 API가 쿠키를 심어 줌 |
| 저장 요청 보안 | **CSRF 토큰** — `apiSend`가 자동으로 붙임 | `X-CSRFToken` 헤더 |

## 성공 응답

- 감싸지 않고 데이터를 그대로 보낸다.
- **화면 API**(예: `/api/home/`)는 그 화면에 필요한 묶음을 한 번에 보낸다.
- **목록 API**는 `{"items": [...]}`로 보낸다. 나중에 페이지 나누기를 붙일 때 `next`만 추가하면 된다.

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
- 은행 비밀번호, CODEF 키, `connectedId`, 계좌번호 원문은 **응답에 넣지 않는다**. 계좌번호는 `****1234`로 보낸다.
- 지금은 로그인 없이 다 열려 있다(`AllowAny`). `users.User`를 만들면 기본값을 `IsAuthenticated`로 바꾼다.

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
  await apiSend('/api/budgets/fixed/', 'POST', { name: '월세', amount: 700000, paymentDay: 25 })
} catch (err) {
  // err.message · err.fields · err.code
}
```

## 새 API 만드는 순서

1. `backend/<앱>/api.py`에 함수를 만든다 (`@api_view`, 입력 검사는 `serializers`).
2. `backend/config/api_urls.py`에 주소를 한 줄 추가한다.
3. 브라우저로 `http://127.0.0.1:8000/api/...`를 열어 확인한다 (DEBUG면 보기 좋은 화면).
4. React 화면에서 `useApi` / `apiSend`로 부른다.

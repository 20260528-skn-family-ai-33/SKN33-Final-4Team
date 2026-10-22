# 금융 동의·계좌·연결 API 명세

> 담당: 김혜리 · 명세 상태: 초안 · 개발 상태: 미구현
> 공통 규칙은 `docs/API_RULES.md`, 결정 사항은 `docs/결정기록_2026-10-21.md`를 따른다 (camelCase · 원 단위 정수 · UUID · 세션 쿠키 + CSRF · 에러 모양)
> 범위: 목록 2번 중 동의·계좌·연결 5개. 카드 후보·선택, 대출, 카드 청구서는 권세진 담당(`cards.md` · `loans.md`). 금융기관 인증·새 연결(`POST /api/finance/connections/`)과 다시 불러오기(`POST /api/finance/sync/`)는 '결정 후'라 이번에 쓰지 않는다.
> 연결 순서(화면): 동의 저장 → (금융기관 인증·새 연결, 결정 후) → 계좌 후보 보기 → 계좌 선택 저장
> 10/22 반영: 결정기록 ★ 1·2·3·4 · 0번 체크리스트 · H-07 확정(결정기록 3-1 코드표) · **결정기록 3-3 D·F·K·L·M 확정** (README 4-1 · 4-2) · 10/22 결정 N⑥~⑧ · O · V · Z①③(결정기록 반영 전, 전달 메시지 기준)

**공통으로 쓰는 값**

| 값 | 모양 | 뜻 |
|---|---|---|
| 연결 상태 `status` (계좌 · 카드 · 연결) | `connected` · `relogin` · `disconnected` | 연결됨 / **다시 연결 필요**(인증 만료 등, 연결은 남아 있음) / 연결 해제됨. 세 가지뿐이다(★1) |
| 계좌 표시 이름 `displayName` | `"국민 급여통장 · ****5678"` | 서버가 만든다. `{은행 짧은 이름} {별칭 → 없으면 계좌명 → 둘 다 없으면 '입출금'} · ****{끝 4자리}` (`API_RULES.md` '계좌 · 카드 표시'). 다른 API의 `paymentAccount` 객체와 같은 문자열 |

---

## 금융 조회 동의 저장

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 계좌 연결 화면에서 금융 정보 조회 동의 항목을 체크하고 [동의하고 계속]을 누를 때 |
| 어디로 | `POST /api/finance/consents/` |
| 로그인 필요 | O |
| 요청 번호 | `Idempotency-Key` 헤더를 보낸다(서버 `@idempotent()`). 버튼 하나로 끝나는 동작이라 **[동의하고 계속]을 누를 때** 만든다. 응답 전에 다시 보내면 같은 번호 → 처음 응답을 그대로 돌려줌(★4) |
| 명세 상태 | 초안 (기본만) |

**보내는 값**

| 이름 | 타입 | 필수 | 범위·설명 | 예시 |
|---|---|---|---|---|
| `consents` | 목록 | O | 이번에 동의한 항목. 1개 이상 | |
| `consents[].purpose` | 문자 | O | 동의 목적 코드. 확인 필요(아래) | `"finance_inquiry"` |
| `consents[].productScope` | 문자 | O | 조회를 허용하는 범위: `bank` · `card` · `loan` | `"bank"` |
| `consents[].documentVersion` | 문자 | O | 화면에 보여 준 동의 문서 버전 | `"v1"` |

```json
{
  "consents": [
    { "purpose": "finance_inquiry", "productScope": "bank", "documentVersion": "v1" },
    { "purpose": "finance_inquiry", "productScope": "card", "documentVersion": "v1" }
  ]
}
```

- 동의 원문 해시(`document_hash`)와 동의 시각은 서버가 채운다. 화면이 보내면 400 "이 칸은 보낼 수 없어요."(★2)
- 동의하지 않은 항목은 보내지 않는다(거절 기록은 저장하지 않음). 회원가입 동의와 같은 모양이며 `agreed` 칸은 없다(10/22 결정기록 L).
- **같은 범위 · 같은 문서 버전의 유효한 동의가 이미 있으면 새로 만들지 않고 기존 기록을 돌려준다**(10/22 N⑥). 응답 모양은 같다.
- 동의 항목: **은행 필수, 카드 · 대출 선택**(10/22 V). 동의 철회 API는 1차에 없다(V).

**받는 값 (201)**

| 이름 | 타입 | 뜻 |
|---|---|---|
| `items` | 목록 | 저장된 동의 기록 |
| `items[].consentId` | UUID | 동의 기록 번호 |
| `items[].purpose` | 문자 | 동의 목적 코드 |
| `items[].productScope` | 문자 | `bank` · `card` · `loan` |
| `items[].documentVersion` | 문자 | 동의 문서 버전 |
| `items[].grantedAt` | 시각 | 동의 시각 |

```json
{
  "items": [
    { "consentId": "7b1e…", "purpose": "finance_inquiry", "productScope": "bank", "documentVersion": "v1", "grantedAt": "2026-10-22T10:05:00+09:00" },
    { "consentId": "a40c…", "purpose": "finance_inquiry", "productScope": "card", "documentVersion": "v1", "grantedAt": "2026-10-22T10:05:00+09:00" }
  ]
}
```

**실패하면**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| 필수 동의(은행)가 없음 | 400 | `invalid` | "계좌 조회 동의는 꼭 필요해요." (`fields.consents`) |
| 모르는 목적·범위 코드, 지원하지 않는 문서 버전 | 400 | `invalid` | `fields` 메시지 |
| 표에 없는 칸을 보냄 (★2) | 400 | `invalid` | `fields.<그 칸>: ["이 칸은 보낼 수 없어요."]` |
| 같은 요청 번호에 다른 내용 | 409 | `idempotency_conflict` | (개발 중 버그) |
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 |

**확인 필요**

- 동의 문구 최종본 → 팀 회의

---

## 계좌 후보 보기

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 금융기관 연결이 끝난 뒤 '예산에 쓸 계좌 고르기' 화면을 열 때. 연결 관리 화면에서 계좌 선택을 바꿀 때도 쓴다 |
| 어디로 | `GET /api/finance/connect/accounts/` |
| 로그인 필요 | O |
| 명세 상태 | 초안 (기본만) |

**보내는 값** — 없음

- **저장된 계좌만 읽는다.** 이 요청이 CODEF를 새로 부르지 않는다(10/22 O).
- 후보는 **입출금 계좌만**. 예적금 계좌는 후보에 넣지 않는다(10/22 V).

**받는 값 (200)**

| 이름 | 타입 | 뜻 |
|---|---|---|
| `items` | 목록 | 연결이 살아 있는 은행 기관에서 가져온 **입출금 계좌**. 해제한 연결(`disconnected`)의 계좌는 빠진다(10/22 결정기록 M) |
| `items[].accountId` | UUID | 계좌 구분 번호 (계좌번호 원문 아님) |
| `items[].connectionId` | UUID | 이 계좌를 가져온 연결 |
| `items[].displayName` | 문자 | 표시 이름 `"국민 급여통장 · ****5678"` |
| `items[].organizationName` | 문자 | 은행 이름 |
| `items[].maskedNumber` | 문자 | 가린 계좌번호 `****1234` |
| `items[].name` | 문자 / null | 계좌 상품명 |
| `items[].status` | 문자 | `connected` · `relogin` (★1). `relogin`이어도 고를 수 있고, 잔액은 마지막으로 받은 값 |
| `items[].balance` | 정수 / null | 잔액(원). 기관이 주지 않으면 `null` |
| `items[].isSelected` | 참/거짓 | 지금 관리 대상으로 선택돼 있는지 |
| `items[].fetchedAt` | 시각 / null | 마지막으로 정상 조회한 시각 |

```json
{
  "items": [
    { "accountId": "c91d…", "connectionId": "5e0a…", "displayName": "국민 KB마이핏통장 · ****5678", "organizationName": "국민은행", "maskedNumber": "****5678", "name": "KB마이핏통장", "status": "connected", "balance": 1240000, "isSelected": true, "fetchedAt": "2026-10-22T10:06:12+09:00" },
    { "accountId": "0b77…", "connectionId": "5e0a…", "displayName": "국민 입출금 · ****9012", "organizationName": "국민은행", "maskedNumber": "****9012", "name": null, "status": "connected", "balance": null, "isSelected": false, "fetchedAt": "2026-10-22T10:06:12+09:00" }
  ]
}
```

- 연결된 은행이 없거나 계좌가 하나도 없으면 `{"items": []}` (200). 화면은 "연결된 계좌가 없어요"와 [은행 연결하기]를 보여 준다.
- 계좌의 `status`는 그 계좌를 가져온 **연결의 상태를 따른다**(아래 H-07 확정). 그래서 같은 `connectionId`의 계좌는 상태가 같다. `relogin`이면 화면은 "다시 연결 필요" 표시를 붙인다(선택 · 기존 연결은 유지).

**실패하면**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| 유효한 은행 조회 동의가 없음 (10/22 O) | 403 | `consent_required` | 동의 화면으로 |
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 |

**확인 필요**

- `consent_required`는 `API_RULES.md` 에러 코드 표에 아직 없음 → 공통 표에 추가 요청 → 김나은
- CODEF 계좌 조회가 실제로 주는 항목(상품명·종류·잔액 제공 여부) → 실제 응답 확인 후 김혜리
- 은행 이름: `organization_code` → 은행명 변환표를 서버에 둘지 → 김혜리
- 계좌 용도(`budget_role`, 예: 급여 계좌)를 이 화면에서 받을지 → 예산 ML 후 김나은

---

## 계좌 선택 저장

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 계좌 고르기 화면에서 계좌를 체크하고 [저장]을 누를 때 |
| 어디로 | `PUT /api/finance/connect/accounts/` |
| 로그인 필요 | O |
| 요청 번호 | 쓰지 않음. 선택 목록 전체를 덮어쓰는 요청이라 두 번 보내도 결과가 같다(★4는 새로 만들기 · 승인 요청만) |
| 명세 상태 | 초안 (기본만) |

**보내는 값** — 선택한 계좌 **전체 목록**을 보낸다. 목록에 없는 계좌는 선택 해제된다(10/22 결정기록 K: 카드 선택 `selectedCardIds`와 같은 규칙, 단 계좌는 1개 이상). 보낼 수 있는 칸은 아래 하나뿐이다.

| 이름 | 타입 | 필수 | 범위·설명 | 예시 |
|---|---|---|---|---|
| `selectedAccountIds` | UUID 목록 | O | 1개 이상. 후보 목록의 계좌 번호만, 중복 없음 | `["c91d…"]` |

```json
{ "selectedAccountIds": ["c91d…"] }
```

**받는 값 (200)** — `GET /api/finance/connect/accounts/`와 같은 모양(바뀐 `isSelected` 반영)

```json
{ "items": [ { "accountId": "c91d…", "isSelected": true, "…": "…" }, { "accountId": "0b77…", "isSelected": false, "…": "…" } ] }
```

- 이미 같은 선택 상태로 다시 보내면 성공(200)으로 같은 결과를 돌려준다.

**실패하면**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| 목록이 비었음 | 400 | `invalid` | "계좌를 하나 이상 골라 주세요." (`fields.selectedAccountIds`) |
| 후보 목록에 없는 번호가 섞임 (없는 번호 · 남의 계좌 · 해제된 연결의 계좌 모두) | 400 | `invalid` | `fields.selectedAccountIds: ["선택할 수 없는 계좌예요."]` |
| 표에 없는 칸을 보냄 (★2, 예: `isSelected`) | 400 | `invalid` | `fields.<그 칸>: ["이 칸은 보낼 수 없어요."]` |
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 |

**검사 규칙 (10/21 나은 확정 · 결정기록 0번)**

- 여러 개를 본문으로 보내는 요청이라 남의 계좌가 섞여도 404가 아니라 **400 `invalid`** 로 답한다(주소가 틀리면 404, 내용이 틀리면 400).
- **없는 계좌든 남의 계좌든 고를 수 없는 계좌든 같은 문구**로 답한다. 남의 계좌라는 사실을 드러내지 않는다. 어느 번호가 틀렸는지도 알려 주지 않는다.
- '내 계좌'인지는 **이번 연결에서 받은 후보 목록**(`GET /api/finance/connect/accounts/`에 나오는 계좌 = 로그인한 회원의 해제되지 않은 은행 연결에서 가져온 입출금 계좌)에 있는지로 검사한다.
- **하나라도 틀리면 전체를 저장하지 않는다.** 일부만 저장하지 않으며, 기존 선택 상태가 그대로 남는다(트랜잭션 하나).
- 선택을 바꾸면 홈 잔액 · 월급 찾기(H-01)가 바로 바뀐다. 승인 전 예산 초안은 **만료 안 함**(결정기록 D).

**확인 필요**

- 선택 계좌가 예산 계산(잔액·소비 집계)에 들어가는 기준, 선택을 해제한 계좌의 과거 거래 처리 → 예산 ML 후 김나은

---

## 연결 목록 보기

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 마이 > 연결 관리 화면을 열 때 |
| 어디로 | `GET /api/finance/connections/` |
| 로그인 필요 | O |
| 명세 상태 | 초안 (기본만) |

**보내는 값** — 없음

**받는 값 (200)**

| 이름 | 타입 | 뜻 |
|---|---|---|
| `items` | 목록 | 해제하지 않은 기관 연결. `disconnected`는 목록에 나오지 않는다(★3) |
| `items[].connectionId` | UUID | 연결 번호 (해제할 때 사용) |
| `items[].businessType` | 문자 | 업권: `bank` · `card` · `loan` (10/22 Z①) |
| `items[].organizationName` | 문자 | 기관 이름 |
| `items[].alias` | 문자 / null | 사용자가 붙인 별칭 |
| `items[].status` | 문자 | `connected` · `relogin` (★1) |
| `items[].connectedAt` | 시각 | 연결한 시각 |
| `items[].lastAuthenticatedAt` | 시각 / null | 마지막 인증 시각 |
| `items[].accountCount` | 정수 | 이 연결에서 가져온 계좌 수 |
| `items[].cardCount` | 정수 | 이 연결에서 가져온 카드 수 |
| `items[].accountIds` | UUID 목록 | 이 연결에서 가져온 계좌 번호(10/22 Z③). 연결 해제 확인 창에서 이 계좌로 나가는 고정지출 · 저축을 고를 때 쓴다 |
| `items[].cardIds` | UUID 목록 | 이 연결에서 가져온 카드 번호(10/22 Z③) |

```json
{
  "items": [
    { "connectionId": "5e0a…", "businessType": "bank", "organizationName": "국민은행", "alias": null, "status": "connected", "connectedAt": "2026-10-22T10:05:30+09:00", "lastAuthenticatedAt": "2026-10-22T10:05:30+09:00", "accountCount": 2, "cardCount": 0, "accountIds": ["c91d…", "0b77…"], "cardIds": [] },
    { "connectionId": "91f2…", "businessType": "card", "organizationName": "신한카드", "alias": null, "status": "relogin", "connectedAt": "2026-10-22T10:07:02+09:00", "lastAuthenticatedAt": "2026-10-22T10:07:02+09:00", "accountCount": 0, "cardCount": 2, "accountIds": [], "cardIds": ["1c90…", "77ab…"] }
  ]
}
```

- 연결이 없으면 `{"items": []}` (200).
- `connectedId`·CODEF 키·인증 정보는 넣지 않는다.
- `relogin`이면 화면은 [다시 연결] 버튼을 보여 준다. 다시 연결 API는 '금융기관 인증·새 연결'과 함께 결정 후.
- 나중에 새 연결 API를 만들 때: **다시 연결하면 기존 계좌 · 카드를 다시 쓴다**(새 행을 만들지 않음, 10/22).
- 연결별 상세 API는 **1차에 없다**(10/22 N⑦). 상세 화면이 필요하면 이 목록과 계좌 후보 · 고정지출 목록으로 그린다.

**실패하면**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 |

**확인 필요**

- `relogin`이 되는 기준(CODEF 오류 코드 중 어느 것) → 김혜리(CODEF 동작 확인)

---

## 연결 해제

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 연결 관리 화면에서 기관 하나의 [연결 해제]를 누르고 확인할 때 |
| 어디로 | `DELETE /api/finance/connections/<uuid:connection_id>/` |
| 로그인 필요 | O |
| 요청 번호 | 쓰지 않음. 이미 해제된 연결을 다시 해제해도 성공이라 결과가 같다 |
| 명세 상태 | 초안 (기본만) |

**보내는 값** — 없음 (주소의 ID = `connectionId`)

**받는 값 (204)** — 본문 없음

- 행은 지우지 않고 해제 시각(`disconnected_at`)만 채우고 상태를 `disconnected`로 바꾼다(DB 설계: 해제해도 기록 유지). 이 연결에서 가져온 과거 거래는 남는다.
- 이 연결의 계좌 · 카드도 `status: disconnected`가 된다.
- **이미 해제한 연결을 다시 해제하면 204**(결정기록 0번: 이미 그 상태면 성공, 예: 이미 중지 → 204).
- **고정지출 · 정기 저축 처리 (10/22 결정기록 F)**: 화면은 연결 목록의 `accountIds` · `cardIds`(Z③)로 고정지출 · 저축 목록에서 이 연결로 나가는 것을 골라, 해제 확인 창에서 "이 연결의 계좌 · 카드로 나가는 고정지출 · 정기 저축도 같이 그만할까요?"를 물어본다. 기본은 **체크 안 함**.
    - 체크한 항목만 화면이 **기존 중지 API**로 중지한다: 고정지출 `DELETE /api/budgets/fixed/<id>/`(fixed.md `3. 고정지출 중지` — 이번 주기 미납 납부 예정도 취소), 정기 저축 `DELETE /api/budgets/savings/<id>/`(savings.md `4. 정기 저축 중지`).
    - 체크하지 않은 항목은 그대로 남고, 결제 수단(계좌 · 카드)이 `disconnected`로 보여 화면에 "연결 해제됨 · 다시 고르기"를 표시한다(결정기록 3번 C-02: 연결이 해제돼도 고정지출은 유지).
    - **새 API는 없다.** 이 해제 API는 고정지출 · 저축을 직접 바꾸지 않는다.
- 해제한 연결의 계좌는 **`isSelected`를 그대로 두고**, 해제 전 거래는 **소비 집계에 계속 넣는다**(10/22 N⑧).

**실패하면**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| 없는 연결 · 남의 연결 | 404 | `not_found` | "연결 정보를 찾을 수 없어요." |
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 |

**확인 필요**

- **기관 하나 해제 vs 공유 ID 삭제**: 현재 코드(`finance/codef.py`의 `add_bank`)는 여러 은행을 **같은 `connectedId`에 추가**한다. 그래서 이 API의 해제는 'CODEF 계정에서 그 기관만 빼기'이고, 마지막 기관을 해제할 때만 `connectedId` 자체를 지울지 정해야 함 → 김혜리(CODEF 동작 확인)
- 진행 중인 조회(`sync_run`)가 있을 때 해제를 막을지, 해제 뒤 도착한 조회 결과를 저장하지 않고 버릴지 → 팀 회의
- 중지 호출과 해제 호출의 순서, 중지 중 일부가 실패했을 때 해제를 계속할지 → 프런트 담당

---

## H-07 DB 코드 (10/22 확정, 결정기록 3-1 코드표 · DB 반영은 김나은)

API 값과 DB 값을 같게 둔다(소문자 영어).

| 칸 | 코드 | 상태 |
|---|---|---|
| `connection.status` | `connected` · `relogin` · `disconnected` | ✅ 확정 (10/22, API ★1과 같음). 해제하면 `disconnected` + `disconnected_at` |
| `bank_account.account_type` | `demand`(입출금) · `deposit`(예적금) · `other` | ✅ 확정 (10/22). 예적금은 `deposit` — 용도 칸 `budget_role = savings`와 헷갈리지 않게 `savings`를 쓰지 않음. 홈 잔액(H-02)과 월급 찾기(H-01)는 `demand`만. CODEF 응답의 계좌 구분 값을 이 셋으로 바꿔 저장 |
| 계좌 · 카드의 연결 상태 | 따로 저장하지 않고 **연결(`connection.status`)에서 가져와** 응답에 넣는 안 | ✅ 확정 (10/22). 같은 값을 두 곳에 두지 않는다 |

거래 `direction` · `transaction_type` · `confirmation_state` · `classification_source`는 `transactions.md`의 H-07을 본다.

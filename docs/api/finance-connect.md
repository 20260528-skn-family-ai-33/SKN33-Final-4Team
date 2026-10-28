# 금융 동의·계좌·연결 API 명세

> 담당: 김혜리 · 명세 상태: 초안 · 개발 상태: 동의 저장 · 계좌 후보 · 계좌 선택 · 연결 목록 = DB 구현 (10/26), 연결 해제 = DB 구현 (10/27, CODEF 기관 빼기는 미연결), 금융기관 인증 · 새 연결 = 구현 (10/27, CODEF 계정 등록 · 문자 인증), 다시 불러오기 = 구현 (10/27, 작업 방식 · 자동 불러오기) — `backend/finance/api_connect.py` · `api_new_connection.py` · `api_sync.py`
> 공통 규칙은 `docs/API_RULES.md`, 결정 사항은 `docs/결정기록_2026-10-21.md`를 따른다 (camelCase · 원 단위 정수 · UUID · 세션 쿠키 + CSRF · 에러 모양)
> 범위: 목록 2번 중 동의·계좌·연결 7개(10/27 금융기관 인증·새 연결, 다시 불러오기 추가). 카드 후보·선택, 대출, 카드 청구서는 권세진 담당(`cards.md` · `loans.md`). 다시 불러오기의 작업 결과 확인은 `chat.md` '오래 걸리는 작업 진행 확인'(공용).
> 연결 순서(화면): 동의 저장 → 금융기관 인증·새 연결 → 계좌 후보 보기 → 계좌 선택 저장
> 10/22 반영: 결정기록 ★ 1·2·3·4 · 0번 체크리스트 · H-07 확정(결정기록 3-1 코드표) · **결정기록 3-3 D·F·K·L·M 확정** (README 4-1 · 4-2) · 10/22 결정 N⑥~⑧ · O · V · Z①③

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

## 금융기관 인증 · 새 연결

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 계좌 연결 2단계(기관마다 로그인) 화면에서 기관 로그인 팝업의 [연결하기], 문자 인증 팝업의 [확인], 연결 관리의 [다시 연결]을 누를 때 |
| 어디로 | `POST /api/finance/connections/` |
| 로그인 필요 | O |
| 요청 번호 | 쓰지 않음. 같은 기관을 다시 보내면 서버가 '이미 연결된 기관 → 다시 로그인'으로 처리해서 연결이 두 개 생기지 않는다 |
| 명세 상태 | 초안 (10/27 나은 결정 반영) |
| 개발 상태 | 구현 (10/27) — `backend/finance/api_new_connection.py` · `codef_accounts.py` · `connected_id.py` · `institutions.py` |

**정해진 것 (10/27 나은)**

1. 로그인 방식은 **아이디 · 비밀번호만** 받는다. 공동인증서 · 간편인증은 받지 않는다. 공동인증서로만 연결되는 기관(산업 · 수협 · 신협 · 부산 · 광주 · 제주은행, 수협 · 광주 · 제주카드)은 400.
2. 추가 인증은 **문자(SMS) 본인확인만** 지원한다. CODEF가 `CF-03002`(추가 인증 필요)를 주면 응답의 그 기관이 `sms_required`가 되고, 화면은 문자 인증 팝업(`SmsAuthPopup`)을 연다. 받은 인증번호를 **같은 기관 정보와 함께 한 번 더 보낸다**(두 번 나눠 보내기, 2-way). 인증 시간이 지나면 [다시 받기] 없이 처음부터 다시 연결한다.
3. 카드사별 추가 값(현대카드 카드번호 · 카드 비밀번호)은 **저장하지 않고 매번 받는다**. CODEF에 보내고 바로 버린다(DB · 로그 저장 금지). 현대카드는 조회할 때도 매번 필요해서 다시 불러오기 API에서도 받는다.
4. 여러 기관을 한 번에 보내면 **성공한 것만 저장**한다. 하나라도 연결되거나 문자 인증 대기면 **200 + 기관별 결과**, 전부 실패하면 아래 '실패하면' 기준으로 에러.
5. 실패 안내는 아래 표. CODEF 원래 코드(`CF-xxxxx`)는 **로그에만** 남기고 응답에는 넣지 않는다.
6. `connectedId` 암호화 키는 **전용 키**를 쓴다: `.env`의 `CONNECTED_ID_ENCRYPTION_KEY`(32바이트 urlsafe base64, 필수 — 없거나 형식이 틀리면 서버가 켜지지 않음). `SECRET_KEY`에서 만들지 않는다(바꾸면 전원 재연결이 필요해서). 키는 단톡방에 올리지 않고 같은 DB를 쓰는 사람끼리만 따로 전달한다. 만드는 명령은 `.env.example`에 있다.
7. AES 암호화 라이브러리 `pycryptodome==4.1.0`을 `requirements.txt`에 명시한다(지금 `.venv` 버전, `easycodefpy`도 같은 라이브러리 사용).
8. **기관 점검 · 응답 없음은 502** `provider_unavailable` (10/28 나은 확인, `API_RULES.md` 10-22 O 기준. 추천안의 503은 잘못 적은 것)
9. **CODEF 실패 코드 (10/28 나은, CODEF 데모 현대카드로 확인)**: `CF-04000`(계정 등록 실패)은 겉 코드라 **응답 `data.errorList` 첫 줄의 코드**로 실패 안내를 고른다. `CF-12401`(로그인 값 누락)은 `extraMessage`에 빠진 칸 이름이 `{cardNo}` · `{cardPassword}`처럼 오므로 **그 칸만** `fields`에 표시한다. `CF-03002` = 문자 인증. `CF-13001`(조회일 · 기간 오류)은 우리 요청 실수(예: 청구서 `startDate`를 일 단위로 보냄)라 화면엔 502 안내, 로그로 확인. `CF-12100` = 아이디 · 비밀번호 오류(`codef.py` 주석 기준, 직접 재현은 아직)

**보내는 값**

| 이름 | 타입 | 필수 | 범위 · 설명 | 예시 |
|---|---|---|---|---|
| `institutions` | 목록 | O | 연결할 기관. 1~10곳. 같은 기관(업권 + 기관 코드)은 한 번만 | |
| `institutions[].businessType` | 문자 | O | `bank` · `card` (대출 기관 연결은 은행 연결로 함께 조회) | `"bank"` |
| `institutions[].organizationCode` | 문자 | O | CODEF 기관 코드. 서버 표(`finance/institutions.py`, 화면 `utils/institutions.js`와 같음)에 있는 아이디 로그인 기관만 | `"0088"` |
| `institutions[].loginId` | 문자 | O | 기관 홈페이지 아이디. 앞뒤 공백은 서버가 지움. 100자 이내 | `"hong123"` |
| `institutions[].password` | 문자 | O | 기관 홈페이지 비밀번호. 100자 이내. 서버가 RSA로 암호화해 CODEF에 한 번 보내고 버린다 | |
| `institutions[].cardNo` | 문자 | 카드사 일부 | 카드번호 숫자만 14~19자리. **현대카드(`0302`)는 필수**, 은행에 보내면 400. 저장 · 로그 안 함 | `"4111222233334444"` |
| `institutions[].cardPassword` | 문자 | 카드사 일부 | 카드 비밀번호 숫자 2자리 또는 4자리. **현대카드는 필수**. RSA로 암호화해 보내고 버림 | |
| `institutions[].smsAuth` | 객체 | 문자 인증 때만 | 첫 요청에서 `sms_required`를 받은 뒤 보낼 때만 넣는다. **이때는 그 기관 하나만** 보낸다 | |
| `institutions[].smsAuth.authToken` | 문자 | O | 첫 응답의 `smsAuth.authToken` 그대로 | |
| `institutions[].smsAuth.code` | 문자 | O | 문자로 받은 인증번호 6자리 | `"123456"` |

```json
// ① 처음 연결
{ "institutions": [
  { "businessType": "bank", "organizationCode": "0088", "loginId": "hong123", "password": "••••••" },
  { "businessType": "card", "organizationCode": "0302", "loginId": "hong123", "password": "••••••", "cardNo": "4111222233334444", "cardPassword": "••••" }
] }

// ② 문자 인증 (sms_required 받은 기관 하나만, 로그인 값도 다시 보낸다)
{ "institutions": [
  { "businessType": "card", "organizationCode": "0311", "loginId": "hong123", "password": "••••••",
    "smsAuth": { "authToken": "eyJ1Ijo…", "code": "123456" } }
] }
```

- 문자 인증 때 아이디 · 비밀번호(카드 값 포함)를 **다시 보내는 이유**: CODEF 2-way 요청은 처음 요청과 같은 값을 다시 받아야 하는데, 서버는 비밀번호를 어디에도 보관하지 않기 때문이다. 화면은 문자 인증 팝업이 닫힐 때까지 입력값을 메모리에만 들고 있다가 버린다.
- `authToken`에는 CODEF가 준 추가 인증 정보(작업 번호 · 시각)가 서버 서명과 함께 들어 있다. 비밀번호는 들어 있지 않다. **3분** 안에만 쓸 수 있고, 다른 회원 · 다른 기관 것은 쓸 수 없다.

**받는 값 (200)** — 기관별 결과

| 이름 | 타입 | 뜻 |
|---|---|---|
| `results` | 목록 | 보낸 순서대로 기관별 결과 |
| `results[].businessType` · `organizationCode` | 문자 | 보낸 값 그대로 |
| `results[].organizationName` | 문자 | 기관 이름 (예: `"신한은행"`, `"현대카드"`) |
| `results[].status` | 문자 | `connected` 연결됨 / `sms_required` 문자 인증 필요 / `failed` 실패 |
| `results[].connectionId` | UUID | (`connected`) 연결 번호. 연결 목록 · 해제와 같은 값 |
| `results[].reconnected` | 불리언 | (`connected`) 전에 연결한 적 있는 기관이면 true. 다시 연결이면 **기존 계좌 · 카드를 다시 쓴다** |
| `results[].syncStatus` | 문자 | (`connected`) 연결 직후 불러오기 결과: `done` 완료 / `failed` 연결은 됐지만 불러오기 실패(다시 불러오기로 재시도) / `not_supported` 카드사(카드 · 청구서 불러오기는 다시 불러오기 API에서) |
| `results[].accountCount` | 정수 / null | (`connected`, 은행) 이 연결에서 가져온 계좌 수. 카드사는 null |
| `results[].smsAuth.authToken` | 문자 | (`sms_required`) 문자 인증 때 그대로 보낼 값 |
| `results[].smsAuth.expiresAt` | 시각 | (`sms_required`) 이 시각까지 인증번호를 보내야 함 (3분) |
| `results[].smsAuth.phoneHint` | 문자 / null | (`sms_required`) 문자를 받은 번호 일부. **지금은 항상 null** (CODEF 응답에서 확인되면 채움, 아래 확인 필요) |
| `results[].error` | 객체 | (`failed`) `{code, message, fields?}` — 아래 실패 안내 표의 `code` · 문구. (`sms_required`) 인증번호가 틀려 다시 받는 경우에만 `{code: "invalid", message: "인증번호가 맞지 않아요."}` |

```json
{ "results": [
  { "businessType": "bank", "organizationCode": "0088", "organizationName": "신한은행", "status": "connected",
    "connectionId": "5e0a…", "reconnected": false, "syncStatus": "done", "accountCount": 2 },
  { "businessType": "card", "organizationCode": "0311", "organizationName": "롯데카드", "status": "sms_required",
    "smsAuth": { "authToken": "eyJ1Ijo…", "expiresAt": "2026-10-27T17:43:00+09:00", "phoneHint": null } },
  { "businessType": "card", "organizationCode": "0306", "organizationName": "신한카드", "status": "failed",
    "error": { "code": "invalid", "message": "아이디 또는 비밀번호를 확인해 주세요." } }
] }
```

- 연결되면 서버가 CODEF `connectedId`(회원의 기관 로그인 묶음)를 **암호화해서** 회원 정보에 저장하고, 기관 연결 행(`connection`, 상태 `connected`, 인증 방식 `id_password`)을 만든다. `connectedId` · 비밀번호 · 카드 값은 응답에 넣지 않는다.
- **다시 연결**: `relogin`인 기관을 보내면 같은 연결 행을 `connected`로 되돌린다. 해제한 기관을 다시 보내면 새 연결 행을 만들고(해제 기록은 남김), 계좌 · 거래는 거래 저장(`sync.py`)이 기존 것을 다시 쓴다(10/22).
- **은행은 연결 직후 계좌 · 거래를 불러온다**: 기간은 **4달 전 1일 ~ 오늘**(10/27). 저장은 나은의 거래 저장 흐름(`finance/sync.py` `sync_bank`, PR #44 · #45)을 그대로 쓴다. 불러오기가 실패해도 연결은 남기고 `syncStatus: failed`로 알린다.
- 기관은 보낸 순서대로 하나씩 처리한다. 같은 회원의 CODEF 계정 등록은 한 번에 하나씩만 하도록 서버가 잠근다(동시에 두 기관을 처음 연결해도 `connectedId`는 하나).

**문자 인증 흐름**

| 단계 | 화면 | 서버 |
|---|---|---|
| ① | 로그인 팝업 [연결하기] → 기관 정보 전송 | CODEF가 `CF-03002` → 그 기관 `sms_required` + `authToken`(3분) |
| ② | `SmsAuthPopup` 열기. 3분 안내 | — |
| ③ | 인증번호 6자리 [확인] → 같은 기관 정보 + `smsAuth` 전송 | CODEF에 2-way로 이어 보냄 → `connected` |
| ④-a | 인증번호가 틀림 | 다시 `sms_required` + 새 `authToken` + `error.message: "인증번호가 맞지 않아요."` → 팝업에 문구 표시 후 다시 입력 |
| ④-b | 3분이 지남 | 400 "인증 시간이 지났어요. 처음부터 다시 연결해 주세요." → 로그인 팝업부터 다시 |

- 문자 말고 다른 추가 인증(보안문자 · 간편인증 등)을 요구하면 400 "○○에서 요구한 추가 인증은 아직 지원하지 않아요."

**실패하면**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| 아이디 · 비밀번호 오류 | 400 | `invalid` | "아이디 또는 비밀번호를 확인해 주세요." |
| 계정 잠김 · 비밀번호 변경 필요 | 400 | `invalid` | "○○ 홈페이지에서 직접 로그인해 잠금을 풀어 주세요." |
| 카드번호 · 카드 비밀번호 빠짐 (현대카드는 서버가 먼저 검사) | 400 | `invalid` | `fields["institutions[0].cardNo"]` · `fields["institutions[0].cardPassword"]` |
| CODEF가 카드 값을 더 요구 (`CF-12401`, 겉 코드 `CF-04000` + `errorList`) | 400 | `invalid` | "카드번호와 카드 비밀번호를 확인해 주세요." — CODEF가 빠졌다고 한 칸만(`{cardNo}` → `cardNo`), 칸 이름이 없으면 두 칸 다 |
| SMS 인증번호 틀림 | 200 | — | 그 기관 `sms_required` + `error.message: "인증번호가 맞지 않아요."` (위 ④-a) |
| SMS 인증 시간 지남 · 다른 기관/회원의 토큰 | 400 | `invalid` | "인증 시간이 지났어요. 처음부터 다시 연결해 주세요." / "문자 인증 정보가 맞지 않아요. 처음부터 다시 연결해 주세요." |
| 문자 외 추가 인증 요구 | 400 | `invalid` | "○○에서 요구한 추가 인증은 아직 지원하지 않아요." |
| 동의 없음 (은행 → 은행 동의, 카드사 → 카드 동의) | 403 | `consent_required` | "계좌 조회 동의가 필요해요." / "카드 조회 동의가 필요해요." → 동의 화면으로. CODEF는 부르지 않음 |
| 기관 점검 · 응답 없음 · 알 수 없는 CODEF 오류 | **502** | `provider_unavailable` | "○○ 연결이 잠시 어려워요. 잠시 후 다시 시도해 주세요." |
| 입력값 틀림 (빈 아이디, 모르는 기관, 공동인증서 전용 기관, 은행에 카드 값, 같은 기관 두 번, 11곳 이상, 문자 인증을 여러 기관과 함께) | 400 | `invalid` | `fields["institutions[i].<칸>"]` · `fields.nonFieldErrors`. CODEF는 부르지 않음 |
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 |

- `○○`는 기관 이름(`organizationName`).
- **여러 기관이 전부 실패**하면: 하나라도 400 이유가 있으면 400 `invalid`, 아니면 502 `provider_unavailable`. `message`는 "연결하지 못했어요. 기관별 안내를 확인해 주세요."이고, 기관별 문구는 `fields["institutions[i]"]`(카드 값은 `fields["institutions[i].cardNo"]`처럼)에 넣는다. 기관 하나만 보냈으면 그 기관의 문구 · 상태를 그대로 쓴다.

**확인 필요**

- **아직 직접 확인하지 못한 CODEF 코드**: 아이디 · 비밀번호 오류(`CF-12100`, 주석 기준) · 잠김 · 비밀번호 변경 · 인증번호 틀림 · 인증 시간 지남 · 이미 등록된 기관. 데모 계정으로 받으면 `codef_accounts.py`의 `CODE_REASONS`에 옮긴다. 그전까지는 CODEF 안내 문장의 낱말(예: '비밀번호' + '일치')로 임시 판단하고, 판단이 안 되면 502 안내로 보낸다 → 김혜리(`codef_card_check`가 `errorList`까지 출력하도록 나은 PR에 올라옴)
- **문자 인증 2-way 세부**: 인증번호를 보내는 칸 이름(`smsAuthNo`)과 위치(요청 맨 바깥), CODEF 응답의 `method` 값(`smsAuthNo`), 인증 제한 시간, 문자 받은 번호(`phoneHint`)를 응답에서 얻을 수 있는지 → 실제 문자 인증 기관(롯데카드 · 씨티)으로 확인 필요 (김혜리)
- 새 환경변수 `CONNECTED_ID_ENCRYPTION_KEY`를 `docs/ENV_SETUP.md` · `LOCAL_SETUP.md` 3단계(.env 필수 칸) 표에도 적어 줄지 → 김나은(공통 문서)
- **연결 직후 불러오기를 요청 안에서 기다림**: 은행 여러 곳을 한 번에 연결하면 응답이 길어질 수 있다. 다시 불러오기처럼 작업(`taskId`) 방식으로 바꿀지는 실제 속도를 보고 정한다
- **CODEF에서 기관 빼기**가 아직 없어서(연결 해제 '정해진 것'), 한 번 연결한 기관은 해제했어도 다시 연결할 때 CODEF '계정 갱신'(`/v1/account/update`)으로 보낸다. 기관 빼기를 붙이면 '추가'(`/v1/account/add`)로 바뀐다. 추가가 '이미 등록됨'으로 실패하면 갱신으로 한 번 더 시도한다
- `connection.auth_method` 값 `id_password`는 H-07 코드표에 없다 → 코드표에 넣을지 김나은
- 화면 연동(김혜리): `ConnectAuthPage`의 `mockConnect` · `mockSms`를 이 API로 바꾸기, `SmsAuthPopup`의 `phoneHint`가 null일 때 문구, 로그인 팝업의 대구은행 출금 계좌 · 롯데카드 본인확인 칸은 이번 결정 범위 밖이라 서버가 받지 않음(보내면 400)

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
- `relogin`이면 화면은 [다시 연결] 버튼을 보여 준다. 다시 연결은 '금융기관 인증 · 새 연결' API에 그 기관을 보내면 된다(같은 연결 행을 `connected`로 되돌림).
- **다시 연결하면 기존 계좌 · 카드를 다시 쓴다**(새 행을 만들지 않음, 10/22).
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

**정해진 것 (10/27 나은)**

- **CODEF 쪽**: 해제할 때 그 은행만 `connectedId`에서 빼고, `connectedId` 자체는 남겨 둔다(회원 탈퇴 때만 삭제). CODEF 기관 빼기는 DB 처리 뒤 나중에 붙인다 → 김혜리
- **조회 중 해제**: 진행 중인 조회(`sync_run`)가 있어도 해제는 막지 않고, 해제 뒤 도착한 조회 결과는 저장하지 않고 버린다 → `codef_sync` 쪽 처리는 김나은
  - 순서 (10/27 확인): 해제 API와 `finance/sync.py`의 저장이 같은 연결 행을 잠근다. 해제가 먼저 끝나면 저장 직전 `disconnected_at` 확인에서 걸려 계좌·거래를 저장하지 않고, 저장이 먼저 잠그면 해제는 저장이 끝난 뒤 처리된다(해제 전에 도착한 데이터라 유지). 테스트: `finance/test_disconnect_sync.py`
- **동시 요청**: 연결 행을 잠그고(`select_for_update`) 처리한다. 동시에 여러 번 와도 모두 204, 해제 시각은 처음 값 유지

**확인 필요**

- 중지 호출과 해제 호출의 순서, 중지 중 일부가 실패했을 때 해제를 계속할지 → 프런트 담당
- 조회 중 해제로 저장을 버린 경우 지금은 `sync_run`이 `failed / sync_error`로 남고 `codef_sync` 명령도 "동기화에 실패했어요"로 나온다. 실제 오류와 구분할 코드(예: `error_code = disconnected`)를 둘지 → 김나은
- `sync_bank`는 `disconnected_at=None` 조건으로 연결을 `get_or_create`하므로, 해제된 은행에 동기화를 돌리면 새 연결이 생긴다(= 재연결). 수동 명령은 괜찮지만, 자동 동기화를 붙일 때는 연결 ID를 받아 해제된 연결은 건너뛸지 → 김나은

---

## 금융 정보 다시 불러오기

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 홈 · 연결 관리에서 [새로고침]을 누를 때(연결 전부), 연결 관리에서 기관 하나의 [다시 불러오기]를 누를 때(그 기관만) |
| 어디로 | `POST /api/finance/sync/` → 202 + 작업 번호, 결과는 `GET /api/tasks/<taskId>/`(`chat.md` '오래 걸리는 작업 진행 확인') |
| 로그인 필요 | O |
| 요청 번호 | 쓰지 않음. 대신 **"이 회원이 이미 불러오는 중인가"**로 막는다(아래 중복 요청) |
| 명세 상태 | 초안 (10/27 나은 결정 반영) |
| 개발 상태 | 구현 (10/27) — `backend/finance/api_sync.py` · `config/tasks.py` · 자동 불러오기 명령 `finance_auto_sync` |

**정해진 것 (10/27 나은)**

1. **대상**: 기본은 연결된 기관 전부 × 기관마다 **동의한 범위 전부**(은행: 계좌 · 거래 + 대출 / 카드: 카드 · 청구서). `connectionIds`로 특정 기관만 고를 수 있다(연결 관리 화면용). 동의하지 않은 범위는 건너뛴다(**실패로 세지 않음**).
2. **기간**: 거래 = 마지막으로 성공한 조회일 **7일 전 ~ 오늘**(겹친 거래는 `sync.py`가 중복 없이 저장) / 카드 청구서 = **지난달 + 이번 달**(CODEF billing-list는 월 단위 `YYYYMM`) / 계좌 잔액 · 카드 목록 · 대출 = 지금 상태를 통째로 다시 받기. 성공한 적이 없는 연결은 첫 연결과 같게 **4달 전 1일 ~ 오늘**.
3. **일부 실패**: 성공한 기관만 저장하고, 실패한 기관은 기관별 결과에 이유를 넣는다. 문구 · 코드는 '금융기관 인증 · 새 연결' 실패 안내 표와 같다. **로그인 오류 · 잠김이면 연결 상태를 `relogin`으로** 바꾼다.
4. **카드번호 · 카드 비밀번호가 필요한 곳(현대카드)**: 요청으로 받아서 CODEF에 보내고 바로 버린다(저장 · 로그 금지). 요청에 없으면 **그 기관만** "카드 비밀번호가 필요해요."로 실패, 나머지는 정상 저장.
5. **작업 방식**: 바로 답하지 않고 `202 {taskId, status}` → `GET /api/tasks/<taskId>/`로 상태와 기관별 결과를 본다. **1차는 요청 안에서 끝까지 처리하고 응답만 작업 모양**이라 보통 `status: succeeded`로 온다. 나중에 느려지면 서버 속만 뒤에서 돌리게 바꾸고 API · 화면은 그대로 둔다.
6. **중복 요청**: 그 회원이 이미 불러오는 중이면 새로 시작하지 않고 **진행 중인 `taskId`**를 그대로 돌려준다(에러 아님). 끝난 지 **1분 안**에 같은 대상으로 또 누르면 다시 부르지 않고 **마지막 결과**를 돌려준다.
7. **자동 불러오기**: 하루 3번(07 · 13 · 21시, 자정 전후 은행 점검 시간 피함). 연결이 살아 있는(`connected`) 회원만, 수동과 같은 범위 규칙. 그 회원이 불러오는 중이면 건너뛴다. 카드 값이 필요한 기관은 자동에서 뺀다. 1차는 cron으로 명령을 돌린다.
8. 연결 해제된 뒤 도착한 데이터는 저장하지 않고 버린다(연결 해제 '정해진 것', #51).
9. **진행 중 판단은 공용 작업 표(`async_task`)** 로 한다(10/28 나은). 자동 불러오기(`finance_auto_sync`)도 같은 표로 진행 중인지 확인해서 수동 · 자동이 겹치지 않는다. `sync_run`은 기관 조회 실행 기록용으로 그대로 둔다.
10. **자동 불러오기는 새 명령 `finance_auto_sync`** (10/28 나은). 실제 저장 함수는 개발용 `codef_sync`와 같은 `finance/sync.py` `sync_bank`(대출은 `sync_loans.sync_bank_loans`)를 쓴다 — 로직 두 벌 없음. 수동 API와 자동 명령도 같은 실행(`api_sync.sync_connections`)을 쓴다.
11. **카드 · 카드 승인 · 청구서 저장 함수는 권세진 담당**(대출 #50과 같은 방식, 10/28 나은). 그전까지 카드사는 "준비 중"(`cards: skipped / not_supported`)으로 건너뛴다. 만들 때 모양: `finance/sync_cards.py`의 `sync_card(user, connected_id, org, start, end, bill_months, card_no=None, card_password=None)` — 실패 시 `codef.CodefError`를 그대로 올리고 저장 직전 연결 해제를 확인. 청구서 `startDate`는 월 단위 `YYYYMM`(`CF-13001` 주의)

**보내는 값** — 전부 생략 가능 (`{}` 또는 본문 없음 = 연결 전부)

| 이름 | 타입 | 필수 | 범위 · 설명 | 예시 |
|---|---|---|---|---|
| `connectionIds` | UUID 목록 | X | 불러올 연결만 고를 때. 1~50개, 중복 없이. 내 연결 · 해제 안 한 것만(아니면 400 "선택할 수 없는 연결이에요.") | `["5e0a…"]` |
| `cardCredentials` | 목록 | X | 카드 값이 필요한 카드사 연결의 카드번호 · 카드 비밀번호. 이번 대상 안의 카드사 연결만 | |
| `cardCredentials[].connectionId` | UUID | O | 카드사 연결 번호 | |
| `cardCredentials[].cardNo` | 문자 | O | 숫자만 14~19자리 | `"4111222233334444"` |
| `cardCredentials[].cardPassword` | 문자 | O | 숫자 2자리 또는 4자리 | |

```json
// [새로고침] — 연결 전부
{}
// 연결 관리에서 현대카드만, 카드 값과 함께
{ "connectionIds": ["91f2…"], "cardCredentials": [{ "connectionId": "91f2…", "cardNo": "4111222233334444", "cardPassword": "••••" }] }
```

**받는 값 (202)**

| 이름 | 타입 | 뜻 |
|---|---|---|
| `taskId` | UUID | 작업 번호 → `GET /api/tasks/<taskId>/` |
| `status` | 문자 | `queued` · `running` · `succeeded` · `failed` (1차는 대부분 `succeeded`. 진행 중인 작업을 돌려받으면 `running`) |

```json
{ "taskId": "d71c…", "status": "succeeded" }
```

**작업 결과** — `GET /api/tasks/<taskId>/`의 `result` (`taskType: "finance_sync"`)

| 이름 | 타입 | 뜻 |
|---|---|---|
| `summary` | 객체 | `{total, succeeded, failed, skipped}` 기관 수 |
| `items` | 목록 | 기관별 결과 (대상 순서) |
| `items[].connectionId` · `businessType` · `organizationCode` · `organizationName` | | 연결 정보 |
| `items[].status` | 문자 | `succeeded` 하나 이상 저장 / `failed` 실패(이유는 `error`) / `skipped` 할 일 없음(동의 안 함 · 준비 중 · 조회 중 해제) |
| `items[].parts` | 목록 | 범위별 결과 `{kind, status, reason?}`. `kind`: `accounts`(계좌 · 거래) · `loans`(대출) · `cards`(카드 · 승인 · 청구서). `reason`(건너뛴 이유): `no_consent` 동의 안 함 · `not_supported` 아직 불러오기 기능 없음 · `disconnected` 조회 중 해제 · `not_run` 앞 범위가 실패해서 안 함 |
| `items[].newTransactionCount` | 정수 / null | 새로 저장한 은행 거래 수 |
| `items[].error` | 객체 / null | (`failed`) `{code, message, fields?}` |

```json
{ "taskId": "d71c…", "taskType": "finance_sync", "status": "succeeded", "conversationId": null,
  "createdAt": "2026-10-27T17:50:00+09:00", "startedAt": "2026-10-27T17:50:00+09:00", "completedAt": "2026-10-27T17:50:06+09:00",
  "result": {
    "summary": { "total": 3, "succeeded": 1, "failed": 2, "skipped": 0 },
    "items": [
      { "connectionId": "5e0a…", "businessType": "bank", "organizationCode": "0088", "organizationName": "신한은행", "status": "succeeded",
        "parts": [{ "kind": "accounts", "status": "succeeded" }, { "kind": "loans", "status": "skipped", "reason": "no_consent" }],
        "newTransactionCount": 5, "error": null },
      { "connectionId": "91f2…", "businessType": "card", "organizationCode": "0302", "organizationName": "현대카드", "status": "failed",
        "parts": [], "newTransactionCount": null,
        "error": { "code": "invalid", "message": "카드 비밀번호가 필요해요.", "fields": { "cardNo": ["카드 비밀번호가 필요해요."], "cardPassword": ["카드 비밀번호가 필요해요."] } } },
      { "connectionId": "77ab…", "businessType": "bank", "organizationCode": "0004", "organizationName": "국민은행", "status": "failed",
        "parts": [{ "kind": "accounts", "status": "failed" }, { "kind": "loans", "status": "skipped", "reason": "not_run" }], "newTransactionCount": null,
        "error": { "code": "relogin_required", "message": "아이디 또는 비밀번호를 확인해 주세요." } }
    ]
  },
  "error": null }
```

- **작업 상태와 기관 결과는 다르다**: 작업이 끝까지 돌았으면 기관이 전부 실패해도 작업은 `succeeded`이고, 실패는 `items[].error`로 본다. 작업 `failed`는 서버 오류로 중간에 멈춘 경우뿐이다(`error.code`: `server_error`, 10분 넘게 멈춘 작업은 `stale`).
- **기관별 실패 코드**: '금융기관 인증 · 새 연결' 실패 안내 표의 문구를 그대로 쓰되, 로그인 오류 · 잠김은 `code: relogin_required`로 보낸다(그 연결은 `relogin`이 되고 화면은 [다시 연결]). 이미 `relogin`인 연결이나 저장된 `connectedId`가 없는 회원은 CODEF를 부르지 않고 "○○ 다시 연결이 필요해요. 연결 관리에서 다시 연결해 주세요."(`relogin_required`). 기관 점검 · 응답 없음은 `provider_unavailable`.
- **카드 값이 필요해요**를 받으면 화면은 그 기관의 카드번호 · 카드 비밀번호를 받아 `connectionIds`(그 기관) + `cardCredentials`로 다시 보낸다. 카드 값을 보낸 요청은 1분 안이어도 새로 부른다.
- 은행 거래 저장은 나은의 `sync_bank`(PR #44 · #45), 대출은 권세진의 `sync_bank_loans`(PR #50)를 부른다. **카드 · 승인 · 청구서 저장 함수는 아직 없어서**(권세진 담당, 위 11번) 카드사는 지금 `cards: skipped / not_supported`로 온다.
- 진행 중 판단과 결과 보관은 공용 작업 표(`async_task`, `taskType = finance_sync`)로 한다. 기관 조회 한 번 한 번의 기록은 기존대로 `sync_run`에 남는다.

**중복 요청**

| 상황 | 답 |
|---|---|
| 그 회원의 불러오기 작업이 진행 중(수동 · 자동 모두) | 202 + **그 작업**의 `taskId` · `running`. 새로 부르지 않음 |
| 같은 대상으로 끝난 지 1분 안 | 202 + **마지막 작업**의 `taskId` · `succeeded`. 새로 부르지 않음 |
| 대상이 다르거나 카드 값을 보냄 · 1분 지남 | 새 작업 |
| 진행 중 작업이 10분 넘게 끝나지 않음 (서버가 중간에 멈춤) | 그 작업을 `failed / stale`로 닫고 새 작업 |

**자동 불러오기 (cron)**

```
# 매일 07:00 · 13:00 · 21:00 (한국 시간)
0 7,13,21 * * *  cd <앱 경로>/backend && python manage.py finance_auto_sync
```

- 대상: 활성 회원 중 `connected` 연결이 있는 회원. `relogin` 연결과 카드 값이 필요한 카드사(현대카드)는 뺀다.
- 그 회원이 불러오는 중이면 건너뛴다. 회원마다 작업 하나(`request_key`가 `finance_sync_auto:`로 시작)를 남기므로, 자동이 도는 중에 수동으로 누르면 그 작업 번호를 받는다.
- 출력은 숫자만: "자동 불러오기 N명 · 진행 중이라 건너뜀 N명 · 대상 기관 없음 N명".

**실패하면** (작업을 만들기 전)

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| 연결된 기관이 없음 | 400 | `invalid` | `fields.nonFieldErrors: ["연결된 기관이 없어요. 먼저 기관을 연결해 주세요."]` |
| 내 연결이 아님 · 해제된 연결 · UUID 아님 · 중복 · 빈 목록 | 400 | `invalid` | `fields.connectionIds: ["선택할 수 없는 연결이에요."]` |
| 카드 값 형식 · 대상 밖 연결 | 400 | `invalid` | `fields["cardCredentials[0].cardNo"]` 등 |
| 표에 없는 칸 | 400 | `invalid` | "이 칸은 보낼 수 없어요." |
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 |

**확인 필요**

- **대출**: PR #50이 main에 병합돼(10/28) `loans` 범위가 켜졌다(`sync_bank_loans` 호출). 대출 거래 기간도 은행 거래와 같은 기간(마지막 성공 7일 전 ~ 오늘)을 넘긴다 → 권세진 확인
- **cron 실행 위치 · 시간대**: 배포 서버 cron이 한국 시간인지(아니면 UTC 22 · 4 · 12시) → 배포 담당
- 작업 상태(`succeeded`)와 기관별 결과(`failed`)가 다를 때 화면 문구 → 프런트 담당. 예: 전부 실패면 "불러오지 못했어요", 일부면 "○곳은 불러오지 못했어요"

---

## H-07 DB 코드 (10/22 확정, 결정기록 3-1 코드표 · DB 반영은 김나은)

API 값과 DB 값을 같게 둔다(소문자 영어).

| 칸 | 코드 | 상태 |
|---|---|---|
| `connection.status` | `connected` · `relogin` · `disconnected` | ✅ 확정 (10/22, API ★1과 같음). 해제하면 `disconnected` + `disconnected_at` |
| `bank_account.account_type` | `demand`(입출금) · `deposit`(예적금) · `other` | ✅ 확정 (10/22). 예적금은 `deposit` — 용도 칸 `budget_role = savings`와 헷갈리지 않게 `savings`를 쓰지 않음. 홈 잔액(H-02)과 월급 찾기(H-01)는 `demand`만. CODEF 응답의 계좌 구분 값을 이 셋으로 바꿔 저장 |
| 계좌 · 카드의 연결 상태 | 따로 저장하지 않고 **연결(`connection.status`)에서 가져와** 응답에 넣는 안 | ✅ 확정 (10/22). 같은 값을 두 곳에 두지 않는다 |

거래 `direction` · `transaction_type` · `confirmation_state` · `classification_source`는 `transactions.md`의 H-07을 본다.

# 대출 API 명세 — 조회·보완·직접 등록

> 담당: 권세진 · 작성일: 2026-10-21 · 수정일: 2026-10-24 · 명세 상태: **1차 확정 (10/24, 결정 AA)**
> 개발 상태 (10/27): 저장 자료 기반 대출 API 3개 구현. CODEF 수집·실제 상환 매칭·월 상환 추정·화면 연결은 별도 작업
> 기준: [공통 규칙](../API_RULES.md), [결정 기록](../결정기록_2026-10-21.md), [API 목록](../머니쿠션_API_명세_목록_2026-10-21.md), [작성 틀](./_TEMPLATE.md)
> 관련 문서: [인증·회원](./auth.md) · [카드](./cards.md) · [대출](./loans.md) · [소비 분석](./spending-analysis.md)

대출 조회·정보 보완·직접 등록 3개 API와 CODEF 문서·샌드박스 확인 기록을 정리한다. 결정기록 3-3 O·P·Q·T·Y를 반영했으며, 실제 원천 데이터 검증·저장 세부 사항은 마지막 **확인 필요 사항 모음**에 남긴다.

기존 검토 참조를 위해 8·9번을 유지하고 직접 등록을 11번(팀 취합 목록 #35)으로 추가했다. 다른 기능의 번호는 위 관련 문서에서 확인한다.

문서 작성 완료와 팀 합의·구현 완료는 다르다. 본문의 **초안 제안·미정·확정 보류** 표시는 유지했다. 기존 모델에 칸이 있다는 사실만으로 CODEF 제공 여부나 사용자 수정 권한이 확정되는 것은 아니다. 예시의 개인정보·금액·UUID는 모두 가상 값이다.

| 번호 | 기능 | 방식·주소 | 목록의 작성 상태 |
|---|---|---|---|
| 8 | 대출 정보 보기 | `GET /api/finance/loans/` | 기본만 |
| 9 | 대출 정보 보완 | `PATCH /api/finance/loans/<id>/` | 기본만 |
| 11 (취합 목록 #35) | 대출 직접 등록 | `POST /api/finance/loans/` | 1차 추가, 결정 Y |

비밀번호 재설정·ML 입력 정보·추천 예산·쿠션 배분 규칙·후속 차수 기능은 이번에 정하지 않는다. 로그인·내 계정 응답의 `onboardingCompleted`는 첫 예산 승인 시 true이며, false면 저장된 값을 채운 온보딩 첫 단계로 이동한다(결정기록 3-3 R). 1차에는 단계 번호를 반환하지 않는다. 금융 동의·계좌·연결 해제·재조회·챗봇 전체 계약은 혜리 담당이며, `chat.md` 최종 편집도 혜리가 한다.

## 화면 연결 (2026-11-05)

- 연결 관리의 대출 항목 또는 홈 ‘꼭 나갈 돈 → 대출 상환 정보 확인’에서 대출 확인 화면을 연다. 선택한 대출을 먼저 표시한다.
- 온보딩은 연결 완료 → 월급 입력 → 고정지출 확인 → 저축 확인 → 대출 확인·직접 등록 → 지킬 돈 순서로 진행한다. 조회된 대출이 없어도 대출 확인 화면을 거치며, 대출이 없으면 등록 없이 다음으로 넘어갈 수 있다.
- 테스트 환경의 기존 연결 재사용 응답(`onboardingNextStep=salary`)은 프로필 저장 뒤 월급 입력으로 이동한다. 마이페이지에서 프로필만 수정할 때는 기존대로 마이페이지로 돌아간다.
- 목록은 GET 응답을 표시하고 각 대출의 상환 방식·월 상환 예정액·매월 납부일을 PATCH로 저장한다. 각 항목의 저장 성공을 따로 확인하므로 여러 대출 중 일부 저장 실패가 전체 성공으로 보이지 않는다.
- 직접 등록은 POST 성공 후 목록을 다시 읽는다. 같은 팝업에서 재시도할 때 요청 번호를 유지하고 저장 중 중복 제출을 막는다. 조회 동의가 없어도 직접 등록 대출의 등록·목록 조회·상환 정보 수정은 가능하다. `hasInquiryConsent=false`이면 기관 대출을 제외한 목록임을 안내하고 기관 조회 동의를 선택적으로 제공한다.
- 빈 입력은 null, ‘잘 모르겠어요’ 선택은 unknown이다. 기관 납부 날짜를 매월 납부일로 자동 변환하지 않고, 추정액도 사용자가 고르기 전에는 채우지 않는다.
- 로딩·빈 목록·동의 필요·오류를 구분한다. 저장 실패 시 입력을 유지하고 세션 만료 시 로그인으로 이동한다. 수정한 항목은 저장 후 하단 ‘다음/확인 마침’으로 이동한다.
- 화면 통합 회귀 테스트: `frontend/src/pages/FinanceDetails.test.jsx`.

## 결정기록에서 반영한 확정 기준

아래는 `결정기록_2026-10-21.md` 전체를 확인해 이 담당 범위에 적용한 팀 확정 사항이다. **팀 규칙이 확정됐다는 뜻이며, 아래 API의 초안 필드나 구현까지 완료됐다는 뜻은 아니다.**

| 근거 | 이 명세에 반영한 내용 |
|---|---|
| ★ 1·2·3 / 체크리스트 | 연결 상태 3가지, 허용하지 않은 요청 항목 400, 주소 ID 형식·소유권·동작 불가 상태 404, 본문 ID 오류 400·전체 미저장 |
| ★ 4 / C-09·C-10·C-11 / 공통 규칙 | 오류 `fields`의 조건부 포함, PATCH의 생략·null 처리, 중복 처리 방지와 요청 번호 생성 시점 |
| ★ 5·6·8 / C-08·C-12 / 돈이 나뉘는 순서 | 출금일이 있을 때만 납부 예정 생성, 카드 고정지출의 중복 납부 예정 금지, 연결된 카드 거래는 고정비로 분류, 첫 청구서만 선차감 |
| ★ 14 / H-01 | 실제 입금일이 아닌 설정한 월급날 기준으로 급여주기 계산 |
| H-07 / DB 코드표 | 연결 상태는 `connection.status`의 `connected`·`relogin`·`disconnected`를 그대로 사용. 계좌·카드에 별도 연결 상태를 저장하거나 API 전용 코드로 변환하지 않음. 모델 choices·CHECK 반영은 별도 구현 작업 |
| 3-3 O·P·Q·T·Y | 저장 자료 GET·동의 오류, 보완 세 칸·조회 추가 칸, 홈 연계, 매칭·추정 담당, 직접 등록 |
| 3-3 AA②③ (10/24) | `fieldStates`(값마다 `source`·`confirmationStatus`·`confirmedAt`) 채택, 대출 목록은 1차에 상환 중(`repaying`)만 |

### 요청·오류·재시도 공통 적용

- 보내는 값 표에 없는 필드와 서버가 정하는 필드(예: `status`)를 보내면 400 `invalid`, 해당 필드에 “이 칸은 보낼 수 없어요.”를 반환한다. 조용히 무시하지 않는다. 아직 확정 보류인 요청 필드는 허용 목록으로 취급하지 않는다.
- 오류에는 `code`·`message`가 항상 있다. `fields`는 입력 칸 오류가 있을 때만 넣고, 로그인 불일치·미인증·404처럼 칸과 무관한 오류에서는 키 자체를 생략한다. 빈 `{}`를 보내지 않는다. 여러 입력 칸의 조합 오류는 `fields.nonFieldErrors`를 사용한다.
- 주소 ID가 잘못된 UUID이거나, 없거나, 남의 항목이거나, 해당 동작을 할 수 없는 상태이면 JSON 404 `not_found`다. 본문 ID의 형식 오류·없는 항목·남의 항목·선택 불가 항목은 400이며, 여러 개 중 하나라도 잘못되면 전체 저장하지 않는다. 존재·소유·선택 가능 여부를 구분해 노출하지 않는다.
- 이미 요청한 상태라면 같은 작업을 다시 반영하지 않고 성공으로 응답한다. 다만 없는 ID·남의 데이터에는 적용하지 않는다. 대출 보완은 `repaying`만 가능하고 `repaid`는 404다(P). 카드의 원천 상태별 선택 가능 범위는 마지막 확인 목록에 남기며 고정지출 상태 코드를 그대로 가져오지 않는다.
- 새로 만들기·승인 요청에 쓰는 `Idempotency-Key`는 입력 폼·팝업을 열 때, 버튼 하나로 끝나는 동작은 누를 때 만든다. 처리 중 버튼을 잠그고 응답 전 재전송에는 같은 키를 쓰며, 완료 후 버린다.
- 요청 번호 적용 대상에서는 같은 회원·키·내용에 처음 응답을 재사용하고, 기록된 키로 다른 내용을 보내면 409 `idempotency_conflict`다. 실패한 4xx·5xx는 기록하지 않아 수정 후 같은 키로 재시도할 수 있다. 회원가입은 이메일 중복 검사·DB 유일성 제약으로 중복 가입을 막으며 별도 요청 번호를 쓰지 않는다(결정기록 3-3 X). 이 규칙만으로 모든 PUT·PATCH에 필수 헤더를 추가하지 않는다.

회원가입·로그인의 세션·CSRF 계약과 이메일 중복 방지는 [인증·회원 명세](./auth.md)를 참조한다.

아래 공통 규칙의 “6개 API”는 카드 3개와 대출 3개(직접 등록 포함)를 합한 범위다.

## 카드·대출 공통 규칙

- 6개 API 모두 로그인 필요. 조회는 세션 쿠키, 저장은 세션 쿠키와 `X-CSRFToken`을 사용한다.
- 금액은 원 단위 정수, 날짜는 `YYYY-MM-DD`, 시각은 한국 시간 ISO 8601, ID는 UUID 문자열이다.
- 목록은 `{ "items": [...] }`, 조회가 정상 완료되어 항목이 없으면 `{ "items": [] }`이다. 미조회·조회 실패를 보유 상품 없음으로 바꾸지 않는다.
- 모르는 값은 null, 확인된 0원은 0이다. 값이 없다고 계산해서 채우거나 다른 항목을 대신 쓰지 않는다.
- 계좌·카드 응답은 공통 규칙의 `displayName`·`status`를 포함한다. `connected`는 정상 연결, `relogin`은 다시 연결 필요(선택·기존 연결 유지), `disconnected`는 연결 해제(새로 선택 불가)다. **결정 기록 H-07에 따라 API 값은 DB 값과 같으며, 계좌·카드의 연결 상태는 연결된 `connection.status`에서 그대로 가져온다.** 별도 상태 저장·API용 매핑은 만들지 않는다. 기관이 제공하는 `cardStatus`와는 다른 값이다. 모델의 choices·CHECK 미반영은 구현 대기이며 정책 미정이 아니다. 연결 FK가 없는 카드의 응답·노출 방식은 별도 확인 대상으로 남긴다.
- 응답에는 비밀번호·CODEF 키·`connectedId`·원문 번호·기관 원천 식별값·암호화 원문을 넣지 않는다. 예시 데이터는 모두 가상이다.
- 본인 소유만 조회·수정한다. 관련 계좌·카드·연결의 소유권도 서버에서 검증한다.
- 고정비는 생활비를 나누기 전에 한 번만 뺀다. 확정 고정지출·대출 월 상환 예정액·첫 카드 청구서·이번 회차 할부금 사이에 같은 비용을 중복 차감하지 않는다. 승인된 예산은 사용자 승인 없이 변경하지 않는다.
- `PaymentDue`는 출금일이 있을 때만 만든다. 출금일을 모르면 날짜를 추측하지 않고 “출금일 확인 필요”로 안내한다. 카드로 내는 고정지출은 카드 청구서에 포함되므로 고정지출용 납부 예정을 따로 만들지 않는다.
- 고정지출과 연결된 카드 거래는 `spending_allocation.funding_bucket = fixed`로 분류하여 생활비 카테고리 사용액에서 제외한다. 연결은 이름만으로 판단하지 않고 실제 근거 거래를 사용한다. 소비 분석의 총액·카테고리 금액에는 포함하고, 그중 고정지출 합계를 `fixedAmount`로 따로 표시한다(결정기록 3-3 S).
- “이번 주기”는 설정한 월급날부터 다음 월급날 전날까지다(H-01). 실제 입금이 늦어도 주기는 바뀌고 그 사이 소비는 새 주기에 속한다. 다음 월급이 미리 들어와도 지난 주기 예산에 더하지 않는다. 매월 해당 날짜가 없는 달은 말일을 사용한다.

### 카드·대출 공통 실패 처리

각 API의 실패 표와 함께 적용한다.

| 상황 | HTTP 상태 | `error.code` | 화면 안내 |
|---|---|---|---|
| 비로그인·세션 만료 | 403 | `not_authenticated` | 로그인 화면으로 이동 |
| 저장 요청의 CSRF 실패 | 403 | `permission_denied` | 다시 시도 안내 |
| 입력 형식·값 오류 | 400 | `invalid` | 해당 `fields`를 입력 칸 아래 표시 |
| 주소의 ID 형식 오류·없는 항목·남의 항목·해당 동작 불가 상태 | 404 | `not_found` | 같은 JSON 오류로 찾을 수 없어요 |
| 요청 본문 ID가 없거나 다른 사람 소유·선택 불가 | 400 | `invalid` | 모두 같은 선택 불가 문구. 한 개라도 잘못되면 전체 저장 취소 |
| JSON 형식 오류 | 400 | `parse_error` | 개발 단계에서 요청 확인 |
| 지원하지 않는 방식 | 405 | `method_not_allowed` | 개발 단계에서 요청 방식 확인 |
| 금융 GET에 필요한 조회 동의 없음 | 403 | `consent_required` | 동의 안내, `fields` 생략 |
| 서버 내부 오류 | 500 | `server_error` | 잠시 후 다시 시도 안내 |

**결정기록 3-3 O 및 11/05 직접 등록 대출 예외:** 카드 후보·청구서·대출 GET은 저장된 자료만 읽고 CODEF를 호출하지 않는다. 카드 후보·청구서는 해당 금융 조회 동의가 없으면 403 `consent_required`이며 `fields`는 생략한다. **11/05 수정:** 대출 목록은 동의가 없어도 200으로 직접 등록(`origin=manual`) 대출만 반환한다. `hasInquiryConsent=false`로 기관 자료를 제외했음을 표시하며, 동의가 있으면 기관 대출도 포함하고 true를 반환한다. `provider_unavailable`(502)·`relogin_required`(409)는 추후 연결·재조회 API의 오류이며 이 GET의 외부 호출 오류로 넣지 않는다. `relogin` 상태에서도 저장된 마지막 자료를 조회한다. 조회 시각은 원래의 `fetchedAt`을 유지한다.

첫 청구서 계산 C-17(Q)과 카드 연동은 [카드 명세 10번](./cards.md#10-카드-청구서-보기)을 참조한다.

## 8. 대출 정보 보기

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 대출 확인 화면에서 본인의 조회·보완 정보를 확인할 때 |
| 어디로 | `GET /api/finance/loans/` |
| 로그인 필요 | O |
| 명세 상태 | 기본만: 모델 기반 응답 초안. CODEF 공식 문서·샌드박스 확인 완료, 실제 은행 검증은 미완료 |

**보내는 값**

본문 없음. **저장된 본인 대출만 읽으며 CODEF를 호출하지 않는다(O).** **목록에는 상환 중(`repaying`) 대출만 보인다(AA③).** 상환 완료(`repaid`) 대출은 1차에 보여 주지 않으며, 쿼리 필터도 1차에는 없다.

**받는 값 (200, 기본 응답 초안)**

| 이름 (`items[]` 안) | 타입 | 뜻·단위 |
|---|---|---|
| `loanId` | UUID 문자열 | 내부 대출 ID |
| `lenderName`, `productName` | 문자열 또는 null | 기관명·상품명 |
| `origin` | 문자열 | 기존 모델의 `provider`(기관 조회), `manual`(직접 등록) |
| `status` | 문자열 | 기존 모델의 `repaying`(상환 중), `repaid`(상환 완료). 1차 목록에는 `repaying`만 나온다(AA③). CODEF의 연체 여부 `resState`와 다른 뜻이며 직접 대응하지 않음 |
| `reportedPrincipal` | 정수 또는 null | 기관 조회 원금, 원 |
| `reportedBalance` | 정수 또는 null | 기관 조회 잔액, 원 |
| `reportedRate` | 숫자 또는 null | 서비스 응답 초안은 연 이율(%). CODEF `resRate`는 약정이율 문자열이며, 실제 은행의 단위·의미를 확인한 뒤 대응 |
| `manualBalance` | 정수 또는 null | 직접 등록한 잔액, 원. 등록 입력 범위 1~1,000,000,000(Y). 기관 원본과 구분하며 1차 PATCH로 수정 불가 |
| `manualRate` | 숫자 또는 null | 직접 등록한 연 이율, %. 등록 입력 범위 0~30(Y). 1차 PATCH로 수정 불가 |
| `maturityDate` | 날짜 문자열 또는 null | 만기일 |
| `reportedPaymentDate` | 날짜 문자열 또는 null | 기관이 제공한 납부일자. CODEF `resDatePayment`의 정확한 의미를 확인해야 하며 매월 납부일 `paymentDay`로 바로 바꾸지 않음 |
| `repaymentMethod` | 문자열 또는 null | P 확정: `equal_payment`·`equal_principal`·`bullet`·`unknown`. null은 모름·미입력. 코드의 뜻은 아래 표 참조 |
| `monthlyPayment` | 정수 또는 null | 저장된 월 상환 예정액, 원. 이번 CODEF 샌드박스 응답에는 직접 대응하는 필드가 없었음. 값의 출처·확인 상태는 아래 `fieldStates` 제안으로 구분 |
| `paymentDay` | 정수 또는 null | 매월 납부일 1~31. 없는 날짜는 말일, 주말·공휴일 보정 안 함(공통 규칙) |
| `paymentHistory` | 객체 배열 또는 null | P: 저장된 지난 납부 이력 중 최근 3건, 날짜 최신순. 없거나 확보하지 못했으면 null, 화면 숨김. 대출 실행 거래를 납부로 세지 않음 |
| `paymentHistory[].date` | 날짜 문자열 | 실제 납부 날짜, `YYYY-MM-DD`. 이력 내부 키는 이 명세의 전달 형식 |
| `paymentHistory[].principal` | 정수 또는 null | 해당 납부의 원금, 원. 대출 약정원금과 구분하며 미제공은 null |
| `paymentHistory[].interest` | 정수 또는 null | 해당 납부의 이자, 원. 미제공은 null, 확인된 0원만 0 |
| `estimatedMonthlyPayment` | 정수 또는 null | P: 확인된 지난 납부를 근거로 계산한 월 상환 추정액, 원. 자료가 없거나 산출할 수 없으면 null·화면 숨김. 사용자 저장값 `monthlyPayment`를 자동 덮어쓰지 않음 |
| `recentDues` | 객체 배열 | 11/06: 홈 ‘꼭 나갈 돈’과 같은 범위(지난 주기 시작 ~ 이번 주기 끝)의 유효한 납부 회차, 납부일 순. `dueId`·`dueDate`·`expectedAmount`(예정액)·`paidAmount`(연결한 출금 합계)·`paymentState`. 다 낸 회차도 [납부 거래 확인]으로 다시 열어 정정할 수 있게. 급여 주기가 없으면 `[]` |
| `overdue` | 불리언 또는 null | P: 연체 확인 true, 연체 없음 확인 false, 알 수 없으면 null·화면 숨김. `status: repaid`와 다른 뜻. DB `loan.overdue`(10/24 추가) |
| `fieldStates` | 객체 | **AA 확정:** `manualBalance`·`manualRate`·`repaymentMethod`·`monthlyPayment`·`paymentDay` 각 보완값의 출처·사용자 확인 상태. DB JSON 전체를 그대로 노출하지 않고 아래 허용 항목만 구성 |
| `fieldStates.<필드>.source` | 문자열 또는 null | `provider` 기관 조회 / `manual` 사용자 입력 / `estimated` 서버 추정. 값이 없거나 출처를 확인할 수 없으면 null |
| `fieldStates.<필드>.confirmationStatus` | 문자열 | `confirmed` 사용자 확인 완료 / `unconfirmed` 값은 있으나 사용자 미확인 / `unknown` 값 또는 확인 근거 없음. 대출 상환 상태 `status`와 구분 |
| `fieldStates.<필드>.confirmedAt` | 시각 문자열 또는 null | 해당 값을 사용자가 확인한 시각. `confirmed`일 때만 시각을 주며 기관 조회 시각으로 대체하지 않음 |
| `fetchedAt` | 시각 문자열 또는 null | 마지막 정상 조회 시각 |

```json
{
  "hasInquiryConsent": true,
  "items": [
    {
      "loanId": "25cd2314-1886-45dc-998e-650eb012a742",
      "lenderName": "예시은행",
      "productName": "예시 신용대출",
      "origin": "provider",
      "status": "repaying",
      "reportedPrincipal": 10000000,
      "reportedBalance": 6420000,
      "reportedRate": 5.8,
      "manualBalance": null,
      "manualRate": null,
      "maturityDate": "2028-03-25",
      "reportedPaymentDate": "2026-10-25",
      "repaymentMethod": null,
      "monthlyPayment": null,
      "paymentDay": null,
      "paymentHistory": null,
      "estimatedMonthlyPayment": null,
      "overdue": null,
      "fieldStates": {
        "manualBalance": { "source": null, "confirmationStatus": "unknown", "confirmedAt": null },
        "manualRate": { "source": null, "confirmationStatus": "unknown", "confirmedAt": null },
        "repaymentMethod": { "source": null, "confirmationStatus": "unknown", "confirmedAt": null },
        "monthlyPayment": { "source": null, "confirmationStatus": "unknown", "confirmedAt": null },
        "paymentDay": { "source": null, "confirmationStatus": "unknown", "confirmedAt": null }
      },
      "fetchedAt": "2026-10-21T09:30:00+09:00"
    }
  ]
}
```

정상 조회로 대출이 없다고 확인된 경우에는 `{ "items": [] }`이다. 기관 잔액과 사용자 보완 잔액을 어느 순서로 우선할지 임의 결정하지 않으며, `monthlyPayment: null`은 0원 상환을 뜻하지 않는다.

### 대출 보완값과 출처 — P · AA 확정

ACC-006의 값별 출처·확인 시각 요구와 `Loan.repayment_method`, `field_states`를 근거로 한 **메타데이터 제안**이다. 보완 세 필드·상환 방식 코드는 P로, `fieldStates`의 칸(`source`·`confirmationStatus`·`confirmedAt`)과 코드는 AA②로 확정됐다. DB에 어떻게 저장할지(`field_states` JSON 구조)만 개발 때 김나은·김혜리·권세진이 맞춘다. `origin`은 대출 자체의 등록 출처이므로 기관 대출에서 월 상환액만 직접 보완해도 `origin: provider`를 유지한다. 보완 필드의 `source`를 따로 표시한다. `reportedPrincipal`·`reportedBalance`·`reportedRate`는 기관 원본이며 보완값의 확인 상태로 덮어쓰지 않는다. `manualBalance`·`manualRate`에 값이 있으면 출처는 `manual`이고, 확인 근거가 없는 기존 값은 확인 상태만 `unknown`으로 표시한다.

| 상환 방식 코드 (결정기록 3-3 P) | 뜻 |
|---|---|
| `equal_payment` | 원리금균등 |
| `equal_principal` | 원금균등 |
| `bullet` | 만기일시 |
| `unknown` | 사용자가 “잘 모르겠어요”를 선택 |
| null | 아직 입력하거나 확인하지 않음 |

아래는 **9번 보완 저장 후 같은 대출을 GET으로 다시 읽을 때의 변경 필드 발췌**다. 현재 실행 결과가 아니다. 나머지 필드는 위 응답과 같다.

```json
{
  "loanId": "25cd2314-1886-45dc-998e-650eb012a742",
  "origin": "provider",
  "repaymentMethod": "equal_principal",
  "monthlyPayment": 310500,
  "paymentDay": 25,
  "fieldStates": {
    "manualBalance": { "source": null, "confirmationStatus": "unknown", "confirmedAt": null },
    "manualRate": { "source": null, "confirmationStatus": "unknown", "confirmedAt": null },
    "repaymentMethod": { "source": "manual", "confirmationStatus": "confirmed", "confirmedAt": "2026-10-22T11:00:00+09:00" },
    "monthlyPayment": { "source": "estimated", "confirmationStatus": "confirmed", "confirmedAt": "2026-10-22T11:00:00+09:00" },
    "paymentDay": { "source": "manual", "confirmationStatus": "confirmed", "confirmedAt": "2026-10-22T11:00:00+09:00" }
  },
  "fetchedAt": "2026-10-21T09:30:00+09:00"
}
```

- 이 예시는 사용자가 서버 추정액 310,500원을 그대로 확인한 상황이다. 추정 출처는 유지하고 확인 상태만 바꾼다는 제안이다. 사용자가 금액을 직접 고치면 `source: manual`로 구분한다. 확인 전 추정값은 `unconfirmed`·`confirmedAt: null`로 표시하고, 값이 없으면 위 첫 예시처럼 `unknown`으로 표시한다.
- 지난 납부 평균을 `estimatedMonthlyPayment`로 제공하는 것은 P로 확정됐고 계산·대출 상환 매칭은 세진 담당(T)이다. ACC-006의 최근 3개월 평균 방향과 P의 표시용 최근 3건을 구분한다. 평균에 사용할 상환 거래·월별 묶음·부분 수집·원 미만 처리와 실제 제공 여부는 구현 전 검증 항목이다. 위 숫자는 가상 값이며 샌드박스의 일반신규 거래에서 계산한 값이 아니다.
- 값이 바뀌면 이전 값의 확인 시각을 새 값에 재사용하지 않는다. 기관 재조회와 사용자 보완값이 충돌할 때의 우선순위는 별도 합의한다. 기존 기록의 출처가 불명확하면 `source: null`, 확인 근거가 없으면 `confirmationStatus: unknown`·`confirmedAt: null`로 반환하는 제안이다. 필드 자체로 수동 입력임을 아는 경우의 출처까지 지우지는 않는다.
- `repaymentMethod: unknown`을 사용자가 확인해도 상환 방식을 알아냈다는 뜻은 아니다. `confirmed`는 입력값 확인이며 예산 승인·납부 완료를 뜻하지 않는다.
- **9번 PATCH의 허용 필드는 P의 3개뿐이다.** `fieldStates`·확인 시각·기관 원본은 수정 요청으로 받지 않는다. 메타데이터의 세부 저장 방식은 나은과 협의하며 기존 `Loan.confirmed_at` 하나를 항목별 시각으로 복제하지 않는다.

**예산·납부 연동의 확정 원칙**

대출 월 상환 예정액은 생활비 배분 전에 확보할 돈에 포함한다. **저장한 월 상환액과 납부일이 모두 있을 때만 납부 예정을 만든다(P).** 해당 월에 납부일이 없으면 말일을 사용한다. 추정값만 있고 저장한 월 상환액이 없을 때는 추정값을 납부액으로 자동 채우지 않는다.

홈 연동은 [home.md](./home.md)에 Q로 반영됐다. `mustPay[].type = loan`, `sourceId = loanId`, 이름은 "{기관} {상품명}", 날짜는 매월 납부일, 금액은 **월 상환액 − 이미 낸 금액**이다. 누르면 대출 확인 화면으로 이동한다. 실제 출금은 대출의 해당 납부와 연결하며, 한 번 납부했다고 대출 전체를 `repaid`로 바꾸지 않는다. 별도 고정지출 후보로 중복 생성하지 않고 GET 자체가 납부 생성·예산 승인·출금 매칭을 실행하지 않는다(O).

대출 상환 출금 연결·월 상환 추정은 **권세진 담당(T)**이며 혜리의 거래 저장 후 공통 호출 입구에 등록한다.

### CODEF 문서·샌드박스 확인 기록 (2026-10-22)

**확인 수준을 구분한다.** 위의 서비스 응답 JSON은 가상 예시이며 실제 CODEF 응답이 아니다. 아래는 공식 문서 확인과 샌드박스 호출 결과다. 우리 서비스의 `GET /api/finance/loans/` 구현·테스트 완료나 실제 은행 조회 성공을 뜻하지 않는다.

- 문서 확인: [은행 개인 보유계좌](https://developer.codef.io/products/bank/common/p/account)의 `resLoan`에서 대출 계좌 목록을 확인하고, [은행 개인 대출 거래내역](https://developer.codef.io/products/bank/common/p/loan)에서 원금·잔액·약정이율·연체 여부·거래내역 필드를 확인했다. 보유계좌 API는 이번에 직접 호출하지 않았다.
- 호출 시각: **2026-10-22 10:42:53 KST**.
- 호출 대상: `POST https://sandbox.codef.io/v1/kr/bank/p/loan/transaction-list`.
- 입력: 공식 공개 예시의 연결 ID·가상 계좌, 기관코드 `0004`, `startDate=20190601`, `endDate=20190731`, `orderBy=0`, `inquiryType=1`. 공용 샌드박스 인증정보만 사용했으며 공유 `.env`·실제 계좌·은행 로그인 정보는 사용하지 않았다.
- 결과: **HTTP 200 / `result.code = CF-00000` / `result.message = 성공`**. 연결 ID·토큰·계좌번호 원문은 이 기록에 싣지 않는다.
- 샌드박스는 정해진 예시 응답을 반환한다. 날짜가 과거이거나 값이 채워져 있어도 현재 실제 계좌 상태·은행별 제공 여부를 검증한 것이 아니다. 근거: [공식 SDK 샌드박스 설명](https://github.com/codef-io/easycodefpy#quik-start).

**이번 샌드박스에서 실제 받은 값**

| CODEF 위치·필드 | 받은 값 | 뜻·서비스 적용 시 주의 |
|---|---|---|
| 계좌 상세 `resPrincipal` | `"120000000"` | 약정원금. 서비스의 `reportedPrincipal` 대응 후보 |
| 계좌 상세 `resLoanBalance` | `"120000000"` | 대출잔액. `reportedBalance` 대응 후보이며 월 상환액이 아님 |
| `resRate` | `"1.44"` | 약정이율 문자열. 은행별 단위·의미 확인 후 `reportedRate`로 변환 |
| `resLoanKind` | `"서울특별시신혼부부임차"` | 대출종류. 상환 방식 코드와 동일하게 취급하지 않음 |
| `resAccountStartDate` / `resAccountEndDate` | `"20190628"` / `"20210628"` | 신규일·만기일. 서비스 날짜 형식은 `YYYY-MM-DD` |
| `resDatePayment` | `"20190728"` | 웹 문서는 납부일자, 연결된 은행별 표는 다음이자납입일로 설명. 원금 포함 상환일·매월 납부일로 임의 해석하지 않음 |
| `resState` | `""` | 연체 여부 필드는 있으나 값이 비어 있었음. 연체 없음·상환 완료를 뜻하지 않음 |
| 최상위 `commStartDate` / `commEndDate` | `"20190601"` / `"20190708"` | 응답 조회 기간. 요청 종료일과 달랐으며 샌드박스의 고정 응답만으로 실제 은행의 조회 한도를 판단하지 않음 |
| `resTrHistoryList` | 1건 | 거래구분이 `일반신규`인 대출 실행 예시. 상환 이력으로 계산하지 않음 |

거래내역 1건의 발췌(응답에 있던 값 그대로, 개인정보 항목 제외):

```json
{
  "resTransTypeNm": "일반신규",
  "resAccountTrDate": "20190628",
  "resPrincipal": "120000000",
  "resInterest": "0",
  "resTranAmount": "",
  "resLoanBalance": "",
  "resInterestRate": ""
}
```

**이 결과를 반영할 때 지킬 구분**

- CODEF 금액·이율은 문자열로 왔다. 확인된 원화 금액은 우리 API의 원 단위 정수로 변환하되, 빈 문자열 `""`은 미제공으로 구분하여 nullable 응답에서는 null로 표현한다. `"0"`을 빈 값과 같게 처리하거나 빈 값을 0으로 채우지 않는다.
- 계좌 상세의 `resPrincipal`은 약정원금, 거래내역 안의 같은 이름은 해당 거래의 원금이다. 서로 덮어쓰거나 모든 거래 원금을 월 상환액으로 합산하지 않는다.
- 거래내역 필드의 존재는 확인했지만 이번 샘플은 상환 거래가 아니다. 실제 납부 이력의 제공 범위·거래구분 값과 추정에 필요한 기간은 추가 확인 대상이다.
- `monthlyPayment`에 직접 대응하는 필드는 이번 응답에 없었다. 금리·잔액만으로 월 상환액을 임의 계산하지 않는다. 사용자 보완 3칸과 지난 납부 기반 추정은 P로 확정됐지만 실제 상환 이력의 수집·평균 계산 검증은 별도다.
- `resState`는 연체 여부이며 서비스의 `repaying`·`repaid`와 구분한다. 연체의 실제 코드·표시 방식과 상환 완료 판정 기준을 새로 확정하지 않는다.
- 실제 은행 호출에서는 문서에 안내된 응답 조회 기간(`commStartDate`·`commEndDate`)을 확인한다. 은행에 따라 요청 기간이 제한될 수 있다.

은행별 제공 여부와 납부일 의미의 추가 확인 근거: [공식 문서에서 연결한 은행별 상세표](https://docs.google.com/spreadsheets/d/1urqr97NxnJJhTOpeOb5WzfnNQrI9jsAI/edit#gid=155531970). 실제 은행 검증은 아직 수행하지 않았다.

**실패하면**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| 비로그인 | 403 | `not_authenticated` | 로그인 필요 |
| 서버 오류 | 500 | `server_error` | 대출 정보 조회 실패·재시도 |
| 대출 조회 동의 없음 | 200 | — | 직접 등록 대출만 표시하고 기관 조회 동의를 선택적으로 안내 (`hasInquiryConsent=false`) |

## 9. 대출 정보 보완

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 대출 확인 화면에서 정보를 보완하고 [상환 예정액 저장]을 누를 때 |
| 어디로 | `PATCH /api/finance/loans/<id>/` |
| 로그인 필요 | O, 세션 쿠키 + CSRF |
| 요청 번호 | 쓰지 않음. 같은 값을 다시 저장해도 결과가 같음 |
| 명세 상태 | 결정기록 3-3 P 반영, 미구현 |

**보내는 값**

주소의 `id`는 본인의 상환 중(`repaying`) 대출 UUID다. 기관 조회·직접 등록 대출 모두 아래 세 칸만 수정한다. 직접 등록 대출에는 조회 동의가 필요 없고, 기관 조회 대출은 유효한 대출 조회 동의가 없으면 403 `consent_required`로 거절한다.

| 이름 | 타입 | 필수 | null | 범위·의미 |
|---|---|---|---|---|
| `monthlyPayment` | 정수 | X | 가능 | 월 상환액 1~100,000,000원. 0원·음수·소수는 400 |
| `paymentDay` | 정수 | X | 가능 | 매월 납부일 1~31. 해당 월에 없는 날은 말일 |
| `repaymentMethod` | 문자열 | X | 가능 | `equal_payment` 원리금균등 / `equal_principal` 원금균등 / `bullet` 만기일시 / `unknown` 잘 모르겠어요 |

```json
{
  "monthlyPayment": 310500,
  "paymentDay": 25,
  "repaymentMethod": "equal_principal"
}
```

- 안 보낸 칸은 유지하고, 세 칸 모두 `null`을 보내면 비워서 모름으로 저장한다. 빈 문자열은 400이다. 예를 들어 `{"paymentDay": null}`은 납부일만 비운다.
- 기관 원본·`status`·`origin`·소유자·연결 ID·`fieldStates`는 수정 대상이 아니다. 보내면 공통 규칙에 따라 400 `invalid`다. 직접 등록 대출의 기관명·상품명·잔액·금리·만기일 정정은 13번(R19). 기관 대출의 잔액·금리 보완은 2차다(P).
- **상환 완료(`repaid`) 대출은 404**다. 없는 대출·남의 대출과 같은 문구를 사용한다.
- 저장 결과 월 상환액·납부일이 모두 있으면 이번 주기 납부 예정을 만든다(P). 같은 PATCH를 반복해도 납부 예정을 중복 생성하지 않는다. 둘 중 하나가 없으면 새 납부 예정을 만들지 않는다. 이미 생성된 납부 예정의 값 변경·null 전환·부분 납부 처리는 마지막 구현 조율 항목에서 확인한다.
- 승인된 예산을 사용자 승인 없이 바꾸지 않는다. 보완 저장은 대출 전체 상환 완료 처리가 아니다.

**받는 값 (200 OK)**

8번 `items[]`의 **대출 한 건 객체**를 반환한다. `{ "items": [...] }`로 감싸지 않으며 변경된 세 필드와 나머지 저장값을 함께 반환한다. 예를 들어 위 요청 뒤에는 해당 객체의 `monthlyPayment = 310500`, `paymentDay = 25`, `repaymentMethod = equal_principal`이고 기관 조회 대출의 `origin = provider`는 유지된다.

**실패하면**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| 주소 UUID 형식 오류·없는 대출·남의 대출·상환 완료 대출 | 404 | `not_found` | "찾을 수 없어요.", `fields` 없음 |
| 타입·범위·상환 방식 코드 오류, 빈 문자열, 허용하지 않은 필드 | 400 | `invalid` | `fields.<필드>` 안내, 전체 미저장 |
| 비로그인 | 403 | `not_authenticated` | 로그인 화면으로 |
| CSRF 실패 | 403 | `permission_denied` | 다시 시도 |
| 서버 오류 | 500 | `server_error` | 저장 실패 안내 |

## 11. 대출 직접 등록 (취합 목록 #35)

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 대출 확인 화면에서 CODEF로 조회되지 않는 학자금대출 등을 직접 추가할 때 |
| 어디로 | `POST /api/finance/loans/` |
| 로그인 필요 | O, 세션 쿠키 + CSRF |
| 요청 번호 | `Idempotency-Key`. **등록 팝업을 열 때** 만들고 응답 전 재전송에 같은 키 사용(Y) |
| 명세 상태 | 결정기록 3-3 P·Y 반영, 미구현 |

**보내는 값**

| 이름 | 타입 | 필수 | 범위·설명 |
|---|---|---|---|
| `lenderName` | 문자열 | O | 기관 이름, 1~50자. 빈 문자열·null 불가 |
| `productName` | 문자열 | O | 대출 상품 이름, 1~50자. 빈 문자열·null 불가 |
| `manualBalance` | 정수 | O | 남은 금액 1~1,000,000,000원. 0원·null 불가 |
| `manualRate` | 숫자 또는 null | X | 연 이율 0~30(%). 0%는 유효한 값, 생략·null은 모름 |
| `maturityDate` | 날짜 문자열 또는 null | X | 만기일 `YYYY-MM-DD`. 생략·null은 모름 |
| `monthlyPayment` | 정수 또는 null | X | 1~100,000,000원. 생략·null은 모름(P) |
| `paymentDay` | 정수 또는 null | X | 1~31. 없는 날짜는 말일, 생략·null은 모름(P) |
| `repaymentMethod` | 문자열 또는 null | X | P의 `equal_payment`·`equal_principal`·`bullet`·`unknown`. 생략·null은 모름 |

```json
{
  "lenderName": "한국장학재단",
  "productName": "학자금대출",
  "manualBalance": 5000000,
  "manualRate": 1.7,
  "maturityDate": "2030-02-28",
  "monthlyPayment": 100000,
  "paymentDay": 25,
  "repaymentMethod": "equal_principal"
}
```

- 서버가 본인 회원·UUID·`origin = manual`·`status = repaying`을 채운다. 이를 클라이언트가 보내면 400이다. CODEF 호출이나 기관 연결 생성은 하지 않는다.
- 월 상환액·납부일이 모두 있으면 납부 예정을 만든다(P·Y). 직접 등록 뒤에도 1차 PATCH는 9번의 세 칸만 받으며 잔액·금리 수정은 없다.
- 같은 회원·요청 번호·내용의 재전송에는 처음 201 응답을 재사용하고 대출·납부 예정을 중복 생성하지 않는다. 같은 키로 다른 내용을 보내면 409 `idempotency_conflict`다.

**받는 값 (201 Created)**

8번 `items[]`의 **대출 한 건 객체**다. 아래는 가상 응답이며, `fieldStates`의 내부 형식은 8번의 메타데이터 제안이다. 기관 조회 원본·납부 이력·연체 정보가 없으므로 null이고 직접 입력한 값으로 채우지 않는다.

```json
{
  "loanId": "3404ede0-0fd6-4d08-b4c9-6626e58fc8a1",
  "lenderName": "한국장학재단",
  "productName": "학자금대출",
  "origin": "manual",
  "status": "repaying",
  "reportedPrincipal": null,
  "reportedBalance": null,
  "reportedRate": null,
  "manualBalance": 5000000,
  "manualRate": 1.7,
  "maturityDate": "2030-02-28",
  "reportedPaymentDate": null,
  "repaymentMethod": "equal_principal",
  "monthlyPayment": 100000,
  "paymentDay": 25,
  "paymentHistory": null,
  "estimatedMonthlyPayment": null,
  "overdue": null,
  "fieldStates": {
    "manualBalance": { "source": "manual", "confirmationStatus": "confirmed", "confirmedAt": "2026-10-22T14:00:00+09:00" },
    "manualRate": { "source": "manual", "confirmationStatus": "confirmed", "confirmedAt": "2026-10-22T14:00:00+09:00" },
    "repaymentMethod": { "source": "manual", "confirmationStatus": "confirmed", "confirmedAt": "2026-10-22T14:00:00+09:00" },
    "monthlyPayment": { "source": "manual", "confirmationStatus": "confirmed", "confirmedAt": "2026-10-22T14:00:00+09:00" },
    "paymentDay": { "source": "manual", "confirmationStatus": "confirmed", "confirmedAt": "2026-10-22T14:00:00+09:00" }
  },
  "fetchedAt": null
}
```

**실패하면**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| 필수 칸 누락·null·문자 길이·금액·금리·납부일·상환 코드·날짜 형식 오류 | 400 | `invalid` | `fields.<필드>`에 입력 조건 표시 |
| `origin`·`status` 등 허용하지 않은 필드 | 400 | `invalid` | "이 칸은 보낼 수 없어요." |
| 같은 요청 번호·다른 내용 | 409 | `idempotency_conflict` | 중복 요청 안내, `fields` 없음 |
| 비로그인 | 403 | `not_authenticated` | 로그인 화면으로 |
| CSRF 실패 | 403 | `permission_denied` | 다시 시도 |
| 서버 오류 | 500 | `server_error` | 등록 실패 안내 |

## 12. 대출 납부 거래 확인 (11/06)

홈 ‘꼭 나갈 돈’의 대출 항목 또는 대출 확인 화면의 회차에서 [납부 거래 확인]을 누르면, 저장된 내 계좌 출금 중 실제로 낸 거래를 골라 **이번 회차**를 확인한다. CODEF를 부르지 않고 저장된 거래만 본다.

| 메서드 | 주소 | 하는 일 |
|---|---|---|
| GET | `/api/finance/loan-dues/<dueId>/payments/?from=&to=` | 회차 상태 · 연결한 출금(`linked`) · 고를 수 있는 출금 후보(`items`, 최대 50건) |
| POST | 같은 주소 | 출금 연결 · 정정 `{ transactionId, settlement, confirmReclassify? }` (`Idempotency-Key` 선택) |
| DELETE | `/api/finance/loan-dues/<dueId>/payments/<transactionId>/` | 연결 취소. 원래 분류를 모르는 거래는 본문 `{ restoreType: transfer\|spending, restoreCategory? }` 필수 |

**판단 기준**

- 후보: 내 계좌의 출금 · 오늘까지 · 취소 아님 · 남은 금액 > 0. **금액이 다르거나 적요에 기관 이름이 없어도 빼지 않는다.** 기본 기간은 납부일 ±7일(오늘까지), `from`·`to`로 최대 92일까지 넓힌다. 연결 계좌가 아닌 다른 내 계좌 출금도 나온다.
- 후보에서 빼는 거래(고를 수도 없음, 400 `invalid`): 다른 회원 거래 · 취소 거래 · 내 계좌 이체(`own_transfer`, R11 확인 중 `own_transfer_review`·재확인 포함) · 저축 · 입금 · 고정지출/정기 저축의 근거 거래 · 내 계좌 이체 연결 거래 · 카드 승인과 같은 구매(`SAME_PURCHASE`)로 연결된 계좌 출금(11/06 보완. 그 연결·분류는 건드리지 않고, 풀어서 옮기지도 않음).
- 이미 **다른** 납부 회차에 연결된 거래: 후보에 없음, POST하면 409 `not_ready` (조용히 옮기지 않음 — 그 회차에서 먼저 연결을 푼다).
- 정렬(참고만): 연결 계좌 또는 전에 사용자가 이 대출로 확인한 계좌·적요 → 납부일 ±3일 → 날짜 차이 → 금액 차이. `hints`(`linkedAccount`·`nearDueDate`·`lenderInDescription`·`sameAsBefore`)와 `amountDifference`(실제 − 예정)는 화면 표시용이며 **자동 확정에 쓰지 않는다.**
- `settlement: full` = “이번 납부를 전부 냈어요” → 금액과 관계없이 이 회차 `paid`. `partial` = “일부만 냈어요” → 금액 비율과 관계없이 `partial`. 한 회차에 여러 출금을 연결할 수 있고(분할 납부), `full`은 회차에 하나만(다른 출금을 `full`로 고르면 앞의 것은 `partial`로).
- 대출의 `status`(repaid)·`monthlyPayment`·회차 예정액(`expectedAmount`)·승인 예산은 바꾸지 않는다.
- 분류 변경: 연결하면 거래 유형이 `loan_repayment`(사용자 확인)로 바뀌어 소비 합계에서 빠진다(대출은 월 상환액으로 따로 확보). 후보의 `reclassify`가 `needsConfirm: true`(소비·카드값 등 이체가 아닌 유형, 또는 사용자가 확정한 분류)면 `confirmReclassify: true` 없이 400 `invalid` (`fields.confirmReclassify`).
- 연결 취소: `linked[].cancelEffect.kind`로 미리 알려 준다.
  - `restore`: 이 연결이 바꾼 분류를 **그 뒤 바뀌지 않았을 때만** 원래 값(`toType`·`toCategory`)으로 되돌린다.
  - `keep`: 연결 뒤 사용자가 바꾼 분류라 지금 값 그대로 둔다(보낸 본문은 쓰지 않음).
  - `choose`: 원래 분류 기록이 없음(이번 변경 전 자동 연결, 또는 그런 거래를 사용자가 확인한 경우). 원래 분류를 **추측하지 않는다.** 본문 `restoreType`(`transfer` = 이체·카테고리 ‘확인 필요’ / `spending` = 소비, `restoreCategory` 필수·사용자 확정)이 없으면 400이고 연결·분류를 바꾸지 않는다 — 대출 상환 유형인 채로 연결만 풀면 거래 목록에서 사라지기 때문. 받으면 연결 해제와 분류 변경을 한 트랜잭션에서 처리한다.
  - 응답 `released.classification`(`restored`·`kept`·`chosen`)이 실제로 한 일이며, 화면 안내는 이 값대로(복원하지 않았으면 “되돌렸어요”라고 하지 않음). 그 회차에는 자동 연결이 다시 붙지 않게 표시한다(`skip_auto_match`). 이미 풀린 연결의 DELETE 재전송은 200 + `released: null`, 아무것도 바꾸지 않는다.
  - 같은 판단을 다시 POST하면 아무것도 쓰지 않는다(`updatedAt`도 그대로).
- 같은 요청 재전송은 연결이 하나만 남고, 같은 거래를 두 회차에 동시에 연결하면 한쪽만 성공(회원 잠금 → 대출 → 회차 → 거래 순).

**응답** (GET · POST · DELETE 공통, POST·DELETE에는 `items`·`searchFrom`·`searchTo` 없음, DELETE만 `released`. 확정 계약: `docs/openapi/parts/loans.json` `LoanDuePaymentState`)

```json
{
  "due": {"dueId": "…", "loanId": "…", "name": "합성은행 학자금", "dueDate": "2026-11-10",
          "expectedAmount": 300000, "paidAmount": 298000, "paymentState": "paid", "linkedAccount": {"accountId": "…", "displayName": "합성은행 월급통장 · ****1001", "status": "connected"}},
  "linked": [{"transactionId": "…", "date": "2026-11-10", "amount": 298000, "account": {…}, "description": "자동이체 출금",
              "amountDifference": -2000, "hints": {"linkedAccount": true, "nearDueDate": true, "lenderInDescription": false, "sameAsBefore": false},
              "reclassify": null, "settlement": "full", "source": "user"}],
  "searchFrom": "2026-11-03", "searchTo": "2026-11-12", "items": []
}
```

`settlement`: `full` · `partial` · null(자동 연결) / `source`: `user` · `rule`(자동 연결).

**자동 연결(기존 `finance/loan_payments.py`) 점검**: 조건(월 상환액과 같은 금액 · 적요의 기관 이름 · 납부일 ±3일 · 연결 계좌)은 그대로 두되, 한 출금에 맞는 대출이 여럿이거나 한 회차에 맞는 출금이 여럿이면 연결하지 않고 사용자 확인으로 남긴다. 사용자가 연결을 푼 회차에는 다시 붙이지 않는다.

**납부 상태 계산**: 대출 회차는 공통 90% 규칙을 쓰지 않는다. 사용자 확인 `full` → paid, `partial` → partial, 확인 없는 자동 연결은 예정액 이상일 때만 paid, 연결 없음은 출금일 전 unpaid · 지남 needs_review. 사용자 판단은 연결한 거래의 `field_states.loan_payment`에 저장돼 홈 재조회 · 재수집 · 상태 재계산 뒤에도 유지된다(재수집은 기존 거래를 덮어쓰지 않음).

| 상황 | 상태 | `code` |
|---|---|---|
| 남의 회차 · 없는 회차 · 취소된 회차 · 주소 ID 형식 오류 | 404 | `not_found` |
| 기관 조회 대출인데 대출 조회 동의 없음 | 403 | `consent_required` |
| `transactionId` 없음 · 남의 것 · 고를 수 없는 거래, `settlement` 오류, 표에 없는 칸, `from`·`to` 형식·기간 오류 | 400 | `invalid` |
| 분류 변경 확인 없음 | 400 | `invalid` (`fields.confirmReclassify`) |
| 연결 취소: 원래 분류를 모르는데 `restoreType` 없음 · `restoreCategory` 오류 · 표에 없는 칸 | 400 | `invalid` (`fields.restoreType` · `fields.restoreCategory`) |
| 다른 회차에 이미 연결된 거래 | 409 | `not_ready` |

## 13. 직접 등록 대출 정정 · 전체 완납 · 되돌리기 (R19, 11/06)

**정정** — `PATCH /api/finance/loans/<id>/` (9번)에 직접 등록(`origin=manual`) 대출만 아래 칸을 더 받는다. 새 입력 항목은 없고 등록 때 받은 칸을 고치는 것이다.

| 이름 | 타입 | null | 범위 |
|---|---|---|---|
| `lenderName` · `productName` | 문자열 | 불가 | 1~50자, 빈 문자열 400 |
| `manualBalance` | 정수 | 불가 | 0~1,000,000,000원. **0원은 정정값일 뿐 전체 완납이 아니다** |
| `manualRate` | 숫자 | 가능(비우기) | 0~30 |
| `maturityDate` | `YYYY-MM-DD` | 가능(비우기) | 없는 날짜 400 |

- 기관 조회 대출에 위 칸을 보내면 400 `invalid`(`fields.<칸>`: "기관에서 조회한 대출은 이 정보를 바꿀 수 없어요."). 기존 세 칸(월 상환액 · 납부일 · 상환 방식)은 그대로 받는다.
- 정정은 실제 상환이 아니다: 납부 거래 · 납부액을 만들지 않고, 지난 회차의 `paidAmount` · 연결 거래(`PaymentMatch`) · 사용자 납부 확인 · 회차 ID를 바꾸지 않는다. 월 상환액 · 납부일 변경은 기존 회차 규칙(9번)을 따른다 — 낸 · 일부 낸 회차는 그대로, 같은 의무의 회차를 새로 만들지 않는다.
- 상환 종료(`repaid`) 대출 PATCH는 지금처럼 404 — 오래된 화면의 수정이 완납 상태를 덮지 않는다.

**전체 완납** — `POST /api/finance/loans/<id>/payoff/` (본문 `{}`, 요청 번호 선택)

- ‘이 대출을 전부 갚았어요’. [납부 거래 확인]의 ‘이번 회차 다 냈어요’와 다른 동작이며, 회차 납부 확인은 대출을 완납으로 바꾸지 않는다.
- 사용자 신고로 `status=repaid`, `manualBalance=0`. 근거(사용자 신고 · 처리 시각)와 이전 상태 · 잔액을 `fieldStates`가 아닌 내부 기록(DB `field_states.payoff`)에 남기고 응답 `payoff`(`source: user_report` · `reportedAt` · `previousBalance`)로 보여 준다. 처리일은 오늘이며 날짜를 받지 않는다(본문에 칸이 있으면 400).
- 회차: **오늘보다 뒤** 회차 중 납부 · 연결 근거가 없는 것만 취소(`obligationState=canceled`). **오늘 · 지난** 회차(미납 · 확인 필요 · 일부 · 완료)와 미리 일부라도 낸 미래 회차는 그대로 두어 홈 ‘꼭 나갈 돈’과 [납부 거래 확인]에서 정리한다. 가짜 ‘납부 완료’로 바꾸거나 지우지 않는다.
- 이후 홈 조회 · 재매칭 · 다음 주기에도 새 회차를 만들지 않는다(회차 생성 전에 잠금 안에서 상태를 다시 본다). 지킬 돈의 대출 합계(`status=repaying`만)에서 빠진다.
- 예산: 승인 전 초안(`draft` · `validated`)만 만료. 승인된 예산 · 쿠션 · 생활비는 바꾸지 않는다.
- 이미 완납이면 처음 기록 그대로 200. 기관 조회 대출은 409 `not_ready`. 남의 · 없는 대출 404. 대출 행은 지우지 않는다.

**되돌리기** — `POST /api/finance/loans/<id>/payoff/cancel/` 본문 `{"reportedAt": "<payoff.reportedAt>"}`

- 화면에서 본 완납 처리와 지금 기록의 `reportedAt`이 다르면(그 뒤 다시 완납 처리됨) 409 `not_ready` — 최신 상태를 덮지 않는다. 이미 상환 중이면 그대로 200.
- `status=repaying`, 잔액이 완납 때 넣은 0원이면 이전 잔액으로 복구. 완납 근거는 지우지 않고 되돌린 시각을 덧붙인다.
- 남은 납부 기록 · 연결은 그대로. 이번 주기 회차만 기존 규칙으로 다시 준비한다(같은 회차를 다시 살리고, 지난 주기 회차는 만들지 않는다). 승인 전 초안만 만료.
- 되돌릴 대출은 `GET /api/finance/loans/?status=repaid`(직접 등록만)로 다시 찾는다. 다른 값은 400.

**화면** — 대출 확인 카드의 [대출 전체 완납 처리] → 확인 화면(남은 원금을 모두 갚아 상환을 종료하는 처리, 취소될 미래 회차 · 남는 오늘/지난 회차 안내와 [납부 거래 확인] 연결) → 처리. 처리 뒤와 ‘상환 종료한 직접 등록 대출’ 목록에서 [완납 취소].

## 구현 전 검토·검증 목록

향후 구현 시 검증할 항목이며 현재 테스트 통과 기록이 아니다.

- [ ] GET에서 CODEF를 호출하지 않는다. 동의 없음·철회 시 직접 등록 대출만 조회·수정할 수 있고 기관 대출은 노출·수정되지 않는다. `hasInquiryConsent`로 표시 범위를 안내한다.
- [ ] PATCH의 금액 1~100,000,000원·납부일 1~31·상환 방식 네 코드와 각 null을 검증한다. 생략은 유지, 범위 밖·빈 문자열·허용 외 필드는 400, 상환 완료는 404다(P).
- [ ] `paymentHistory` 최근 3건·`estimatedMonthlyPayment`·`overdue`를 원천과 구분하고, 미제공은 null로 반환한다(P).
- [ ] POST의 이름 1~50자·잔액 1~1,000,000,000원·금리 0~30%와 선택 칸을 검증하며 `origin: manual`·`status: repaying`을 서버에서 설정한다(Y).
- [ ] POST 201·PATCH 200은 목록이 아닌 대출 한 건이며, 같은 요청 번호의 재전송에서 대출·납부 예정이 중복 생성되지 않는다.
- [ ] 본인 상품만 조회되고 다른 사람의 주소 ID는 404가 된다.
- [ ] 빈 목록·null·0원·조회 실패를 구분한다.
- [ ] 계좌·카드의 표시 객체와 마스킹, 대문자 카테고리 코드를 지킨다.
- [ ] H-07에 따라 계좌·카드 `status`가 연결된 `connection.status`와 일치하고 별도 매핑·독립 상태 저장을 하지 않는다. 연결 FK 없음 처리는 합의 후 검증한다.
- [ ] 대출 응답 제안 채택 후 원본·수동 보완·추정값 수락·값 변경·미입력·모른다는 응답의 필드별 출처와 확인 시각이 재조회에서도 보존된다.
- [ ] 대출 기관 원본과 사용자 보완값을 구분하고 미확정 수정 필드는 허용하지 않는다.
- [ ] 카드 청구·대출 상환·고정지출·거래를 중복 차감하지 않는다.
- [ ] 주소 UUID 형식 오류·동작 불가 상태는 JSON 404, PATCH 생략·nullable null·필수 null·빈 문자열은 공통 규칙을 따른다. ID 경로에는 `<uuid:이름>`을 사용하고, 새 경로는 `api_urls.py`의 마지막 `not_found` 처리 위에 등록한다.
- [ ] 같은 요청은 중복 반영하지 않는다. 요청 번호 적용 대상은 같은 키 재시도·다른 내용 충돌·실패 후 재시도를 확인한다.
- [ ] 출금일 없는 납부 예정을 만들지 않고, 카드 고정지출용 납부 예정을 별도로 만들지 않는다.
- [ ] 고정지출과 연결된 카드 거래는 생활비 사용액에서 제외하고, 대출 상환 출금은 고정지출 후보로 중복 생성하지 않는다.
- [ ] 월급의 조기·지연 입금에도 설정한 월급날 기준 주기를 유지한다.

## 확인한 근거

- `docs/결정기록_2026-10-21.md` 전체(★ 표, 0~7절): 팀 확정 규칙과 세진 관련 미정 항목을 구분하여 반영. 기록에 적힌 다른 기능의 테스트 통과 결과를 이 API의 검증 결과로 가져오지 않음.

### 카드·대출·소비 분석

- `backend/finance/models.py`: Card, Loan, CardBill, CardBillItem 및 원본·보완 필드.
- `frontend/src/pages/ConnectCardsPage.jsx`, `LoanCheckPage.jsx`, `frontend/src/popups/CardBillPopup.jsx`: 필요한 화면 정보와 임시 예시.
- `docs/API_RULES.md`: 본문 ID 오류 400, 공통 계좌·카드 표시, 카테고리 코드, 날짜·금액 원칙.
- `docs/머니쿠션_API_명세_목록_2026-10-21.md`: 담당 범위·작성 상태·소비 분석 결정.
- `docs/머니쿠션_API_명세_작성_순서_2026-10-21.docx`: 카드 청구 항목 목록과 세진 담당 작성 범위.
- `산출물/01_기획/4팀_요구사항정의서.xlsx`: ACC-006(대출 값별 출처·확인), CHT-006·RPT-001(소비 분석·집계 근거·비교 제한), CARD-002(청구 예상/확정·기준 시각). 결정 기록과 충돌하는 요구는 결정 기록을 우선하며, 구체적인 신규 응답 필드는 본문 제안으로 구분.

## 확인 필요 사항 모음

P의 보완 세 칸·null·상환 완료 404·단건 응답, Q의 홈 표시, O의 GET 실패, Y의 직접 등록, T의 담당 배분, AA②③(`fieldStates` 채택·상환 중만 목록)은 본문에 반영했다. 아래는 실제 원천 검증과 구현 조율이며, 확정 정책을 다시 정하는 목록이 아니다.

| 기능 | 확인할 내용 | 확인할 사람 |
|---|---|---|
| 8. CODEF 원천 | 실제 은행·상품별 값 제공 여부, `resRate` 단위·의미, `resDatePayment`가 이자 납입일인지 원금 포함 상환일인지, `resState` 실제 값과 미제공 처리. 기존 샌드박스 거래는 일반신규 1건이라 실제 납부 이력을 검증하지 못함 | 권세진, 수집 연계는 김혜리 |
| 8. 월 상환 추정 | P의 지난 납부 평균에 사용할 실제 상환 거래·조회 기간·월별 묶음·부분 수집·원 미만 처리. 표시용 최근 3건과 평균 산정 범위를 구분하며 자료가 없으면 null | 권세진, 계산 세부 기준은 김나은과 조율 |
| 8. 메타데이터 | `fieldStates` 저장 원천(칸 모양은 AA② 확정). 추정값 수락/직접 입력 구분, 기관 재조회와 보완값 충돌 처리 | 김나은·김혜리·권세진 |
| 8·11. 숫자 변환 | 기관 원본 잔액·원금의 예외값, 금리 정밀도·소수 자릿수 저장. 직접 등록 금액·금리 범위(Y)와 보완 금액 범위(P)는 변경하지 않음 | 권세진, 김나은과 조율 |
| 9·11. 납부 예정·매칭 | 월 상환액·납부일 변경 및 null 전환 때 기존 납부 예정 처리, 실제 상환 출금 연결 키·부분 납부. 두 값이 있으면 납부 예정 생성·중복 차감 금지는 확정 | 권세진, 납부 예정 연계는 김나은, 수집 연계는 김혜리 |
| 9. 화면 저장 | 여러 대출을 순서대로 보완하다 일부 실패했을 때의 화면 안내·재시도. 각 PATCH는 단일 대출이고 승인 예산을 임의 변경하지 않음 | 권세진·김혜리 |

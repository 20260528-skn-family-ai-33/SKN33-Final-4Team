# 예산 API 명세 — 지킬 돈 · 추천 예산 · 카테고리 예산 · 금액 고정 · 승인

## 예산 AI 외부 전송 동의 — 2026-11-10 연결 (실제 서비스 외부 호출은 계속 OFF)

- `GET /api/budgets/ai-consent/`: 로그인한 회원 자신의 `available`, `status`, `purpose="budget_ai"`, `document`, `documentVersion`, `documentHash`, `grantedAt`, `revokedAt`를 반환한다. 상태는 `not_granted / granted / revoked / needs_renewal`이다. available은 예산 기능과 외부 AI 설정이 모두 true일 때만 true다. false이면 새 동의 UI를 숨기고 PUT은 409 budget_ai_unavailable로 막는다. 기존 활성 동의의 조회·철회는 계속 허용하며 비활성 화면에서는 철회 UI만 보인다. 현재 문서의 버전과 원문 해시가 일치하는 미철회 기록만 유효하다.
- `PUT /api/budgets/ai-consent/`: `{agreed: true, documentVersion: "v3", documentHash: "화면에서 받은 해시", healthAgreed: true, healthDocumentVersion: "v1", healthDocumentHash: "별도 건강 요약 문서 해시"}`로 직접 동의한 경우만 저장한다. 중복 요청은 새 기록을 늘리지 않는다. 예전 문서·해시 불일치는 `409 document_outdated`, 알 수 없는 버전·불린이 아닌 동의값·추가 입력은 400이다. 새 문서에 동의하면 종전 활성 기록을 철회 처리하고 이력을 보존한다.
- `DELETE /api/budgets/ai-consent/`: `{confirm: true}`로 budget_ai·budget_ai_health 두 목적의 활성 동의만 함께 철회한다. 금융 조회 동의·원거래·최소금액·잠금·승인 예산은 변경하거나 삭제하지 않는다. 다시 동의하면 새 동의 기록을 만든다. 성공 응답은 세 메서드 모두 200이며 PUT/DELETE는 세션과 CSRF가 필요하다. 회원 잠금을 사용해 추천 처리와 동의 변경 순서를 보존한다.
- 기존 `users.Consent`의 목적 문자열과 null 범위를 사용하므로 모델 변경·마이그레이션이 없다. 공용 문서 목록에 `budget_ai`·`budget_ai_health` 문서가 추가되지만 회원가입은 `terms/privacy`, 금융 연결은 `finance_inquiry`만 선택한다. 예산 AI는 선택 동의이며 기본 체크 해제다.
- 추천 화면과 마이의 동의·데이터 관리에서 동의·철회한다. 체크나 동의 저장만으로 추천·외부 전송을 자동 시작하지 않는다. 동의 조회 실패도 기본 예산 추천을 차단하지 않는다. 기존 금융 동의를 예산 AI 동의로 간주하지 않는다.
- 서버 `recommend(..., allow_external=True)`는 실제 회원의 현재 동의를 다시 조회해야 A/B 외부 LLM 경로에 진입한다. 플래그는 HTTP 입력이 아니며, **현재 실제 추천 API는 `BUDGET_DESIGN_EXTERNAL_ENABLED`(기본 false)를 전달한다. 이 플래그와 예산 기능 스위치가 모두 true일 때만 동의 게이트를 거쳐 외부 호출한다**. 합성 실험은 기존 명시적 synthetic 경로를 유지한다. B의 내부 `consent_verified` 인자는 서버만 전달한다. 실패 대체·최소액·잠금·한도·금액 단위 검수는 그대로이며 사용자 경로의 LangSmith는 끈다.
- 현행 동의 원본은 `backend/users/budget_ai_v3.json`·`backend/users/budget_ai_health_v1.json`이며 이전 v1·v2 원문은 보존한다. 두 목적의 최신 버전·해시와 미철회 기록이 모두 있어야 외부 호출을 허용한다. 예산 AI 요청본문은 미국 지역 `us.api.openai.com`으로만 보내고 Global 주소로 자동 대체하지 않는다. OpenAI 기본 보관 정책은 [공식 데이터 처리 문서](https://developers.openai.com/api/docs/guides/your-data)를 확인했으며 `store=false`를 무보관이라고 안내하지 않는다. OpenAI OpCo, LLC·미국·연락처·시점/방법·기본 최대 30일 로그 및 예외·철회/거부 영향을 명시했다. 운영자의 처리위탁 계약(건강 관련 요약 허용 여부 포함)·개인정보 처리방침과 실제 공유 설정은 출시 전 최종 확인 대상이다. 이 단계는 기능 연결·합성 검증이며 실제 사용자 외부 전송·서비스 활성화·배포 완료가 아니다.

## A/B 연결 준비 — 2026-11-05 (설정 확정 전 기본 비활성)

기존 URL·빈 POST 요청·작업 조회·사용자 승인 흐름을 재사용한다. `budgets.design_service`에 계산 연결을 분리했으며, PM 브랜치 `fix/manual-loan-consent-copy`가 수정하는 고정지출 목록 구간은 수정하지 않는다.

- `BUDGET_DESIGN_ENABLED`: Django 설정 bool 또는 같은 이름 환경변수 `true`로 새 연결을 켠다. **기본값은 false**이며 이때 기존 임시 엔진이 유지된다. 이번 작업에서 실행 환경을 켜거나 배포하지 않았다.
- `BUDGET_DESIGN_POLICY`: Django 설정 dict 또는 같은 이름 환경변수의 JSON으로 덮어쓸 수 있다. 미지정 시 승인된 `2026-11-10-forecast-band` 정책을 사용한다(서버가 A의 전체 생활비 20% 한도와 보호 조건을 계산). A 경로에는 `a` 객체가 필요하며 조정 목표율은 사용하지 않는다. B는 미분류 비중만으로 중단하지 않는다.
- 2026-11-10 사용자 승인: 서비스 초기 정책의 `b_forecast_band`는 `down_percent=20`, `up_percent=20`이다. B의 카테고리별 소비 기준에 대한 예측 허용 범위이며 A의 전체 이동 한도와 별개다. 사용자 최소금액·잠금·기존 하한이 우선하고 금액 단위 정산·범위 검수·규칙 B 대체 처리는 유지한다. 이 설정만으로 외부 호출이나 기능을 켜지 않는다. 별도 지정한 정책을 자동 병합/수정하지 않으며, 그 정책이 band를 생략하면 종전처럼 규칙 B와 `forecast_band_required`를 반환한다. 로컬 비교 창의 입력값·예측 함수 자체의 필수 인자 계약도 바꾸지 않는다.
- 최초 설계 A의 이동 한도는 기타 포함 생활비의 20%다. 항목별 증액 목표율은 사용하지 않는다. 실제 서비스에서는 LLM을 호출하지 않으므로 기본안을 반환한다. 합성 비교에서는 서버가 만든 작은·중간·큰 미리보기 중 하나를 LLM이 선택하거나 유지하며, 호출 실패 시 기본안으로 돌아간다. B는 개별 감액률을 제한하지 않으며 기존 사용자 최소액·잠금·하한을 지킨다. 이 한도는 예산 조정 에이전트에 적용하지 않는다.
- 명시한 설정이 유효하지 않거나 JSON `null`로 정책을 비활성화하면 작업은 `succeeded`이고 `result.outcome=needs_policy`, `budgetId=null`로 반환하며 예산을 저장·만료시키지 않는다. 설정이 잘못되었을 때 초기 정책으로 숨겨 대체하지 않는다. 기존 초안을 새 결과로 승인하면 안 된다. 정책 준비와 기능 활성화/이력 검증은 독립적이다.
- 그 밖의 미완료 상태는 `needs_data`(모델/입력·조회 검증 미준비), `needs_upper_allocation_review`(여윳돈으로도 부족)로 반환한다. `proposed`만 승인 가능한 새 초안이 생긴다. 기존 `infeasible` 충돌 검수는 유지한다. 작업 결과의 기존 다섯 필드 이름은 바꾸지 않는다.
- 최근 완료 3개월의 수집 범위는 선택된 연결의 성공한 SyncRun 기록으로 확인한다. 은행은 `bank_sync`, 카드는 `card_approval_list`이며 요청 기간만으로 조회 완료를 추정하지 않는다. 중복·취소·환불은 별도의 `BUDGET_HISTORY_VERIFIER(user, month_ids)` 서버 콜백이 월별 checks를 제공해야 한다. 미구현이면 각 check를 `unknown`으로 유지해 B를 선택하지 않는다. 프론트 요청으로 verified·모델·건수를 지정할 수 없다.
- 최근 3개월의 검증된 거래 건수 최댓값이 11건 이상이면 B, 부족하거나 10건 이하면 A다. 동의 게이트는 연결했지만 운영 처리위탁 조건 확인·활성화 전이므로 **현재 API 연결은 외부 LLM을 호출하지 않는 규칙 경로**다. 기존 합성 입력 비교 창의 LLM 예측 실험은 그대로 유지한다.
- 새 초안은 기존 `validation_summary`에 서버 메타데이터(선택 결과·정책/이력 fingerprint·여윳돈 이동액·화면용 설명)를 저장한다. 이 메타데이터에는 새 DB 칸을 쓰지 않는다. 별도 유지 목표 필드 budget_category_targets와 기존 migration은 docs/DB_MODELS.md의 DB 담당 검토 항목이다. `GET /api/budgets/`의 기존 `categories[].reason`에 서버 문장을 넣고, 새 연결 초안에는 선택적 `recommendation: {spareTransfer: 원 단위 정수}`를 반환한다. `split.living`/`split.spare`는 항목 합계로 계산한 서버 최종값이다.
- 여윳돈 충당 초안은 아직 프로필 생활비를 바꾸지 않는다. 승인 시 원래 설정·현재 정책·이력이 같음을 재검수한 후 이번 예산 버전의 생활비 항목에만 충당액을 반영하고 프로필 생활비는 바꾸지 않는다. 향후 생활비 설정 변경·부족 원인 판단은 예산 조정 에이전트의 별도 사용자 승인 범위다. 동일 승인 재요청은 이동을 반복하지 않는다. 설정/이력/분류 후보 변경이면 409로 초안을 만료시키고 최근 소비·분류 정보가 바뀌었음을 안내한다. 계산 엔진은 설정 원본과 DB를 직접 수정하지 않는다.
- 저장 합계 조건: `실수령액 − 전체 항목 합계 = 기존 여윳돈 − spareTransfer + unallocated`. 공용 `recommend.check_result`는 그대로 두고 별도 연결부에서 증가한 생활비 target과 이동 한도를 추가 검수한다.
- 새 연결의 최종 카테고리 금액(기타 포함)은 만 원을 우선하고 제약·합계상 필요하면 5천 원을 사용한다. **사용자 잠금액은 원액 유지**한다. 사용자 최소액/하한/한도는 지키며 화면에서 임의 반올림하지 않는다. ML·이력 참고값, 실제 소비, 고정지출·저축·쿠션과 여윳돈은 이 단위 규칙의 대상이 아니다. 과거 승인 예산도 다시 계산하지 않는다.
- 최종 기타 확보액은 사용자 최소 금액 이상으로 계산하고 서버 메타데이터에 보관한다. 검수 target은 `원래 target + spareTransfer + 원래 기타 − 최종 기타`다. 단위 정리 후 남는 돈은 A/B 모두 명시적 `unallocated`로 보존한다. 새 연결을 끄면 기존 엔진 계약은 그대로다.

아래 임시 엔진 명세는 새 연결을 비활성화했을 때 계속 적용된다. 서비스 정책과 이력 검증 콜백이 확정되기 전에는 새 연결이 실제 사용자 B 추천까지 완료된 것으로 간주하지 않는다.

> 담당: 김나은 · 명세 상태: 초안 · 개발 상태: 지킬 돈 · 임시 추천 · 카테고리 조회 · 자물쇠 · 승인 구현 (10/28)
> 작성일: 2026-10-27 · 10/27 온보딩 결정 반영 (자취 여부 비율 · 저축 먼저 떼기 · 비율 계산은 서버)
> 공통 규칙은 [API_RULES.md](../API_RULES.md)를 따른다 (camelCase · 원 단위 정수 · UUID · 세션 쿠키 + CSRF · 에러 모양 · PATCH · 요청 번호).

## 작성 범위와 근거

| 기능 | 방식 · 주소 | 이번 작성 범위 |
|---|---|---|
| 지킬 돈 보기 | `GET /api/budgets/protect/` | 비율 추천 · 저축 먼저 떼기 · 부족 판정 · 저장된 값 |
| 지킬 돈 저장 | `PUT /api/budgets/protect/` | 생활비 · 저축 · 월 쿠션 적립금 저장 |
| 추천 예산 만들기 | `POST /api/budgets/recommend/` | 작업 번호 방식 · 임시 함수 입출력 · 주기 중간 가입 |
| 카테고리 예산 보기 | `GET /api/budgets/` | 초안 · 승인 예산 · 쓴 돈 · 자물쇠 |
| 카테고리 금액 고정 · 해제 | `PATCH /api/budgets/<id>/categories/<code>/` | 자물쇠 · 잔소리 |
| 예산 승인 | `POST /api/budgets/<id>/approve/` | 버전 · 만료 · 중복 (조정안 승인과 공통) |

- 범위 · 상태: [API 명세 목록 5번](../머니쿠션_API_명세_목록_2026-10-21.md) (10/27 결정 블록).
- 화면: [ProtectPage.jsx](../../frontend/src/pages/ProtectPage.jsx) · [RecommendPage.jsx](../../frontend/src/pages/RecommendPage.jsx). 두 화면 모두 실제 API에 연결됨 (10/28 리뷰): 지킬 돈 화면은 `GET · PUT /api/budgets/protect/`의 추천 · 저장 값 · 부족 판정을 그대로 쓰고, [추천 예산 보기]는 저장 뒤 추천 예산 확인으로 넘어가 새 추천을 바로 만든다. 추천 예산 화면은 초안 조회(`GET /api/budgets/`) · 추천 만들기 · 금액 고정 · 승인 API를 쓴다. [utils/direction.js](../../frontend/src/utils/direction.js)의 비율 계산은 화면에서 더 이상 쓰지 않는다(내 정보의 자취 여부 이름 · 비율 표시만 사용).
- 데이터: [users/models.py](../../backend/users/models.py)의 `FinancialProfile`(`monthly_take_home_income` · `living_budget` · `savings_floor` · `cushion_monthly` · `cushion_target` · **자취 여부 칸 — 세진 내 정보 API에서 추가**), [budgets/models.py](../../backend/budgets/models.py)의 `BudgetCycle` · `Budget` · `BudgetItem` · `BudgetCategorySetting`, [finance/models.py](../../backend/finance/models.py)의 `RecurringSaving` · `BankAccount`.
- 근거 문서: [결정기록](../결정기록_2026-10-21.md) 2번(돈이 나뉘는 순서) · 예산 예측 최종 플로우(ML 담당, 10/27 수정본) 4.2 · 4.3.
- 요구사항: `BUD-001`(지킬 돈) · `BUD-003`(저축 최소 금액) · `BUD-005`(승인된 예산은 승인 없이 안 바꿈) · `CUS-001`(쿠션 목표 = 월급 × 3).
- 쿠션 조회 GET 구현·설정 PATCH 후속 명세는 [cushion.md](./cushion.md)에 정리한다. PATCH는 아직 구현하지 않았다.

### 10/27 결정 (온보딩)

| 번호 | 질문 | 결정 |
|---|---|---|
| P-01 | 생활비 · 저축 · 여윳돈 · 쿠션은 어떻게 나누나 | **자취 여부 비율.** 배분 기준 금액(아래 P-04)에 곱한다 |
| P-02 | 비율 | 자취 = 생활비 55% · 저축 30% · 여윳돈 5% · 쿠션 10% / 본가 거주 = 45% · 40% · 5% · 10% |
| P-03 | 비율 계산은 누가 | **서버.** 화면은 응답 값을 그대로 보여 준다 |
| P-05 | 정기 저축(저축이에요) 합계가 비율 저축보다 크면 | 저축 = 정기 저축 합계. 남은 금액(배분 기준 − 저축)을 **생활비 : 여윳돈 : 쿠션 원래 비율**로 다시 나눈다 (자취 55 : 5 : 10 · 본가 45 : 5 : 10) |
| P-06 | 남은 금액이 0 이하면 | 예산을 못 만든다. 부족액을 알리고 저장을 막는다. 저축을 몰래 줄이지 않는다 (플로우 문서 3장) |
| P-07 | 반올림 | 비율 그대로일 때: 생활비 · 저축은 1만원 단위 반올림, 쿠션은 1만원 단위 내림 / 다시 나눌 때: 생활비 · 쿠션 1만원 단위 내림. **자투리는 모두 여윳돈.** 반올림 때문에 합계가 배분 기준 금액을 넘으면 **넘친 만큼 생활비에서 뺀다** (생활비는 0원 아래로 안 내림) — 추천값은 항상 PUT 검사를 통과해야 한다 (리뷰 10/28) |

### 이번에 새로 정한 것 (10/27)

| 번호 | 질문 | 기본값 |
|---|---|---|
| P-04 | 배분 기준 금액의 "고정지출"은 무엇까지 | 이번 급여 주기의 ① **확정 고정지출 월 금액 합계**(`recurring_expense.status = confirmed`, 출금일을 몰라 납부 예정이 없는 것도 포함) + **상환 중 대출의 월 상환액**(`loan.status = repaying`의 `monthly_payment`, 비어 있으면 0) — 이미 낸 것도 포함 (가입 날짜에 따라 예산이 달라지지 않게) ② **카드 청구서는 첫 청구서만** `max(0, 첫 청구서 총액 − 이번 주기 시작 뒤 그 카드로 쓴 돈)` (결정기록 Q · cards.md C-17, 계산 함수는 세진). 두 번째 청구서부터는 넣지 않는다 — 카드로 쓴 돈은 이미 생활비로 세고 있어서 (예산에 아직 안 잡힌 할부의 이번 회차만 더한다, 11/04). **확정 고정지출은 결제 수단(계좌 · 카드)과 상관없이 전부 넣는다** — 카드 고정지출 거래는 생활비가 아니라 고정비로 분류되므로(cards.md), 여기서 빼면 어디에도 예산이 안 잡힌다. 대신 첫 청구서 선차감에서 카드 고정지출 금액을 빼 두 번 빠지지 않게 한다 (10/28 구현 검토로 바로잡음). **홈 `mustPay`의 카드 금액(남은 청구액)과는 다른 계산** |
| P-12 | 카드 첫 청구서 선차감 계산 · 자료를 아직 모르면 (10/28 리뷰 · 10/29 카드사 단위) | 관리 대상 카드(카드 동의 + 연결 중 + 관리 대상 선택)가 있는 **카드사마다** `finance.card_deduction.get_issuer_first_bill_deduction`으로 계산해 합친다 (CODEF 청구서가 카드사 통합이라, 10/29 나은 · cards.md '미리 뗄 돈'). 관리 대상이 아닌 같은 카드사 카드 몫은 첫 청구서에서 같이 뗀다. 카드 동의가 없거나 관리 대상 카드가 없으면 0원. **① 자료 확인**: 청구 이력(카드사 최초 연결일 = 그 카드사 카드 중 가장 먼저 연결된 날부터) · 카드 거래(주기 시작일부터)가 다 들어왔는지는 **카드 조회 기록**(`sync_run`, `product_code` = `card_bill_list` · `card_approval_list`, `status = success`, 그 카드사 카드들의 `covered_from ~ covered_to`를 이어 붙여 빈틈없이 이어진 날까지)으로만 판단한다. **기준일 = 마지막으로 다 불러온 날**(오늘 조회가 없어도 막지 않음. 그 뒤 쓴 돈은 아직 빼지 않으므로 선차감이 작아지지 않는다). 청구 이력이 주기 시작일까지 이어지지 않거나, 이번 주기에 연결한 카드의 거래를 연결일까지 못 불러왔으면 보류(기준일이 연결 전으로 가서 0원이 되는 것을 막음). **② 카드 고정지출**: 확정 고정지출은 P-04대로 고정비에 이미 있으므로 그 카드사 카드(관리 대상 아닌 카드 포함)로 내는 확정 고정지출의 월 금액을 선차감에서 뺀다. **고정지출 하나는 카드 전체에서 한 번만** 빼며, 어느 카드에서 뺄지는 ① 이번 주기에 실제로 결제돼 납부로 연결된 카드(먼저 결제된 것) ② 없으면 고정지출에 적어 둔 결제 카드 순서로 정한다(적어 둔 카드와 실제 결제 카드가 달라도 한 번, 리뷰 10/28). 이번 주기에 이미 결제돼 고정지출로 연결된 카드 거래는 '그 카드로 쓴 돈'에서 제외해 두 번 빼지 않는다. 이번 달에 새로 시작한 카드 구독처럼 첫 청구서에 아직 없는 금액도 빼므로 선차감이 그만큼 작을 수 있다(명세 P-04 규칙 그대로). 선차감 = `max(0, 카드사 첫 청구서 총액 − (그 카드사 관리 대상 카드로 쓴 돈 − 고정지출로 연결된 카드 거래) − 그 카드사 카드로 내는 확정 고정지출)`. 여기에 **이번 회차 할부금**(cards.md '이번 회차 할부금', 상세를 다 못 받았거나 회차 금액을 모르면 `installment_amount_unknown`으로 보류)을 더한다. **③ 보류**: 한 카드사라도 보류(이상한 자료는 `card_data_invalid`)면 **0원으로 치지 않고** 막는다: GET은 200 + `blocked = {reason: card_bill_pending, shortfall: null}` · `base` · `recommended` null · `cardPending = [{cardId, reason}]`(보류된 카드사의 관리 대상 카드마다 한 줄), 저장 · 추천은 409 `not_ready`. **④ 초안 고정**: 선차감은 카드로 쓸 때마다 바뀌므로 `input_hash`에 넣지 않는다. 추천 때 값을 `input_snapshot.fixed.card_reserved`로 저장하고 승인 때는 그 값으로 검사한다(대상 카드 목록 `card_ids`는 해시에 넣어 카드를 고르거나 빼면 만료). **카드 수집기는 위 `product_code`로 조회 기록을 남겨야 한다** (혜리 · 세진) |
| P-13 | 생활비가 바뀌어 자물쇠가 안 맞으면 (10/28) | 지킬 돈 저장(PUT)에서 **생활비가 바뀌었고**, 이번 주기 자물쇠 때문에 추천이 막히면(자물쇠를 빼고 계산하면 막히지 않을 때만 — 추천 엔진 `allocation_conflicts`와 같은 조건) **이번 주기 자물쇠를 모두 푼다.** 풀지 않으면 초안이 만료돼 자물쇠를 풀 곳이 없다(막다른 길). 사용자가 직접 바꾼 게 아니므로 바꾼 횟수(잔소리)는 늘리지 않는다. 응답 `locksReleased`에 푼 카테고리를 주고 화면은 "생활비가 바뀌어서 이번 달 자물쇠를 풀었어요"를 보여 준다. 아직 맞으면 그대로 둔다. **② 추천할 때 (리뷰 10/28)**: 이번 주기에 예산(`validated` · `approved`)이 하나도 없는데 자물쇠 때문에 추천이 막히면(같은 조건) 추천 전에 이번 주기 자물쇠를 풀고 작업 결과 `locksReleased`에 담는다 — 예: 지난달 자물쇠가 넘어온 뒤 내 정보에서 최소 금액을 올림. 예산이 있으면 그 화면에서 풀 수 있으므로 그대로 두고 `infeasible`로 안내한다. 최소 금액을 바꿀 때 바로 푸는 것은 내 정보 저장 API(세진)에 같은 규칙으로 요청 |
| P-11 | 저장을 막을 때 에러 | 409 **`not_ready`** (공통 에러 코드에 새로 추가). 입력 오류(400 `invalid`)와 구분 |
| P-08 | 쿠션 적립 필수 조건 | 입출금 계좌 잔액 합계(홈 `balance.total`과 같은 값) < 쿠션 목표(월급 × 3)면 **월 쿠션 적립금 0원 불가**. 잔액을 모르면 필수로 본다 (10/14 결정). 금액이 적어 추천 쿠션이 0원이 되면 **여윳돈 → 생활비 순서로 최대 1만원까지 가져온다. 그래도 쿠션이 0원일 때만 저축 중 최소 금액을 넘는 부분에서 최대 1만원을 가져온다. 목적은 쿠션 0원 금지이며, 저축은 되도록 줄이지 않는다** (리뷰 10/28). 예: 본가 · 배분 기준 13,000원 · 정기 저축 0원 → 생활비 10,000 · 저축 10,000 · 쿠션 0 = 20,000 → 넘친 7,000을 생활비에서 빼 3,000 → 쿠션 필수라 생활비 3,000을 쿠션으로 → 생활비 0 · 저축 10,000 · 쿠션 3,000 · 여윳돈 0 |
| P-09 | 지킬 돈을 바꾸면 예산은? | 저장된 값만 바뀐다. **승인 전 예산 초안은 만료**, **승인된 이번 주기 예산은 그대로**. 새 값은 다음에 추천 예산을 만들 때부터 쓴다 (BUD-005, 정기 저축 S-12와 같은 규칙) |
| P-10 | 먼저 있어야 하는 값이 없으면 | 에러가 아니라 200 + `missing` 목록. 화면이 빠진 화면으로 안내한다 (자취 여부 → 내 정보, 월 실수령액 · **급여일** → 급여). 급여일이 없으면 이번 주기를 정할 수 없어서 고정비도 계산하지 않는다 (홈 `period: null`과 같음) |

---

## 지킬 돈 보기

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 첫 설정의 [지킬 돈 설정] 화면, 마이 > 지킬 돈을 열 때 |
| 어디로 | `GET /api/budgets/protect/` |
| 로그인 필요 | O · 세션 쿠키 (읽기라 CSRF 없음) |
| 명세 상태 | 초안 |

**보내는 값**

없음. 계산 기준은 서버가 가진 값(자취 여부 · 월 실수령액 · 이번 주기 고정지출 · 확정 정기 저축 · 계좌 잔액)이다.

**받는 값 (200)**

| 이름 | 타입 | 뜻 |
|---|---|---|
| `missing` | 문자 배열 | 계산에 필요한데 없는 값. `housingType`(→ 내 정보) · `takeHomeIncome` · `payday`(→ 급여). 비어 있지 않으면 아래 `rates`(자취 여부가 있으면 채움) 말고 `base` · `recommended` · `blocked`는 `null`, `savingsMin`은 계산해서 줌 |
| `housingType` | 문자 / null | `alone`(자취) · `family`(본가 거주) |
| `rates` | 객체 / null | 쓰는 비율, 0~100 정수. `{ "living", "saving", "spare", "cushion" }` |
| `base.takeHomeIncome` | 정수 | 월 실수령액 |
| `base.fixedTotal` | 정수 | 이번 주기에 먼저 빼는 고정비 합계 (P-04 · 카드 선차감 P-12 포함). 홈 `mustPay` 합계와 다를 수 있다. 카드 선차감을 모르면 `base`가 `null` |
| `base.available` | 정수 | 배분 기준 금액 = `takeHomeIncome − fixedTotal`. 음수면 0이 아니라 **음수 그대로** (부족 판정용) |
| `savingsMin` | 정수 | 저축 최소 금액 = 확정 정기 저축 월 합계 (savings.md S-02, 저장하지 않고 그때 계산) |
| `recommended` | 객체 / null | 서버가 추천하는 금액 (아래) |
| `recommended.livingBudget` · `savingFloor` · `cushionMonthly` · `spare` | 정수 | 생활비 · 저축 · 월 쿠션 적립금 · 여윳돈. 넷의 합 = `base.available` |
| `recommended.rebalanced` | 참/거짓 | P-05로 **저축을 먼저 떼고 다시 나눴으면** `true` |
| `recommended.restAmount` | 정수 / null | 다시 나눴을 때 남은 금액(배분 기준 − 저축). 아니면 `null` |
| `recommended.ratioSavingFloor` | 정수 | 다시 나누기 전, 비율로만 계산한 저축 (안내 카드 문구 "추천 저축 ○원보다 커요"용) |
| `blocked` | 객체 / null | 예산을 못 만드는 경우만 (P-06 · P-12). `{ "reason", "shortfall" }` · `reason`: `fixed_over_income`(고정비만으로 월급 초과) · `savings_over_available`(정기 저축까지 빼면 남는 돈 없음) · `card_bill_pending`(카드 첫 청구서를 아직 확인 못 함) · `shortfall`: 모자란 금액, `card_bill_pending`이면 `null` |
| `cardPending` | 배열 | 카드 선차감을 계산하지 못한 카드 `[{ "cardId", "reason" }]` (P-12). 보통은 `[]` |
| `locksReleased` | 배열 | 이번 저장(PUT)으로 풀린 이번 주기 자물쇠 카테고리 코드 (P-13). GET은 항상 `[]` |
| `saved` | 객체 / null | 사용자가 저장한 값 `{ "livingBudget", "savingFloor", "cushionMonthly", "spare", "updatedAt" }`. 한 번도 저장 안 했으면 `null` → 화면은 `recommended`로 칸을 채운다 |
| `cushion.required` | 참/거짓 | P-08. `true`면 월 쿠션 적립금 0원 저장 불가 |
| `cushion.target` | 정수 / null | 쿠션 목표 = 월 실수령액 × 3. 월 실수령액이 없으면 `null` |
| `cushion.balanceTotal` | 정수 / null | 판단에 쓴 입출금 계좌 잔액 합계 |

```json
{
  "missing": [],
  "housingType": "family",
  "rates": { "living": 45, "saving": 40, "spare": 5, "cushion": 10 },
  "base": { "takeHomeIncome": 2800000, "fixedTotal": 770000, "available": 2030000 },
  "savingsMin": 1000000,
  "recommended": {
    "livingBudget": 770000, "savingFloor": 1000000, "cushionMonthly": 170000, "spare": 90000,
    "rebalanced": true, "restAmount": 1030000, "ratioSavingFloor": 810000
  },
  "blocked": null,
  "saved": null,
  "cushion": { "required": true, "target": 8400000, "balanceTotal": 2800000 }
}
```

계산 예 (본가 · 배분 기준 203만 · 정기 저축 100만): 비율 저축 812,000 → 81만 < 100만 → 저축 100만, 남은 103만을 45 : 5 : 10 → 생활비 floor(772,500) = 77만 · 쿠션 floor(171,667) = 17만 · 여윳돈 = 103만 − 77만 − 17만 = 9만.

**화면 메모**

- `recommended.rebalanced = true` → "저축을 먼저 뗐어요 · 다시 나눔" 카드 (`savingsMin` · `ratioSavingFloor` · `restAmount` 표시).
- `blocked`가 있으면 금액 칸을 숨기고 [정기 저축 고치기] · [고정지출 확인]만 보여 준다. [추천 예산 보기]는 막는다. `card_bill_pending`이면 "카드 청구서를 확인하고 있어요" + [카드 내역 다시 불러오기]를 보여 준다.
- 항목 옆 % = 금액 ÷ `base.available` (화면 계산).

**실패하면**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 |

`missing` · `blocked`는 실패가 아니라 200 안의 상태다.

---

## 지킬 돈 저장

| 칸 | 내용 |
|---|---|
| 어떤 기능 | [지킬 돈 설정]에서 [추천 예산 보기] / 마이 > 지킬 돈에서 [저장]을 누를 때 |
| 어디로 | `PUT /api/budgets/protect/` |
| 로그인 필요 | O · 세션 쿠키 + CSRF |
| 요청 번호 | 안 씀. 같은 값을 두 번 보내도 결과가 같다 (API_RULES) |
| 명세 상태 | 초안 |

**보내는 값** — 세 칸 모두 보낸다 (묶음 저장). 여윳돈은 보내지 않는다 (서버가 계산)

| 이름 | 타입 | 필수 | 범위 · 설명 | 예시 |
|---|---|---|---|---|
| `livingBudget` | 정수 | O | 0 이상, 원 | `770000` |
| `savingFloor` | 정수 | O | `savingsMin` 이상 | `1000000` |
| `cushionMonthly` | 정수 | O | 0 이상. `cushion.required`면 1 이상 | `170000` |

```json
{ "livingBudget": 770000, "savingFloor": 1000000, "cushionMonthly": 170000 }
```

**검사 순서** — 하나라도 걸리면 아무것도 저장하지 않는다

1. `missing`이 있으면 → 409 (먼저 내 정보 · 급여 · 급여일)
2. `blocked`면 → 409 (먼저 정기 저축 · 고정지출. `card_bill_pending`이면 "카드 청구서를 아직 확인하지 못했어요. 카드 내역을 다시 불러온 뒤 시도해 주세요.")
3. 칸 모양 (정수 · 0 이상 · 표에 없는 칸 금지) → 400
4. `savingFloor < savingsMin` → 400
5. `cushion.required`인데 `cushionMonthly = 0` → 400
6. 합계 > `base.available` → 400
7. 통과하면 저장. 여윳돈 = `available − 합계` (저장하지 않음)

**받는 값 (200)** — `GET`과 같은 모양 (`saved`가 방금 저장한 값)

**저장하면 같이 일어나는 일** (P-09)

- `financial_profile`의 `living_budget` · `savings_floor` · `cushion_monthly` · `cushion_target`(= 월 실수령액 × 3)을 바꾼다.
- 이 회원의 **승인 전 예산(`draft` · `validated`)은 `expired`**로 바꾼다. 승인된 예산(`approved`)은 그대로.
- 값이 이전과 같으면 아무것도 바꾸지 않는다 (초안도 만료시키지 않음).

**실패하면**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| 칸이 비었거나 형식이 틀림 · 표에 없는 칸 | 400 | `invalid` | `fields.<칸>` 메시지 |
| 저축 < 저축 최소 금액 | 400 | `invalid` | `fields.savingFloor`: "저축 확인에서 고른 ○원보다 적게 정할 수 없어요." |
| 쿠션 필수인데 0원 | 400 | `invalid` | `fields.cushionMonthly`: "잔액이 월급의 3배보다 적어서 쿠션을 꼭 적립해야 해요." |
| 합계 > 배분 기준 금액 | 400 | `invalid` | `fields.nonFieldErrors`: "배분 가능한 금액보다 ○원 많아요. 입력 금액을 줄여 주세요." |
| 자취 여부 · 월 실수령액이 없음 | 409 | `not_ready` | "내 정보 · 급여를 먼저 입력해 주세요." |
| 고정비 · 정기 저축만으로 모자람 | 409 | `not_ready` | "정기 저축이나 고정지출을 먼저 고쳐 주세요." |
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 |

---

## 추천 예산 · 카테고리 예산 공통 결정 (10/27)

| 번호 | 질문 | 결정 |
|---|---|---|
| R-01 | 추천 예산 만들기는 바로 답? 작업? | **작업 번호 방식.** 공용 `GET /api/tasks/<id>/` (`taskType: "budget_recommend"`). 1차는 요청 안에서 끝까지 계산하고 응답 모양만 작업 방식 → 에이전트(ML + 조정 LLM)를 붙여도 API · 화면은 안 고친다 |
| R-02 | 카테고리를 나누는 계산 | 지금은 서버 안 **임시 함수** `recommend_category_budgets(입력) → 출력`. 에이전트가 생기면 **이 함수 속만** 바꾼다. 입력 · 출력 모양은 아래 R-03 · R-04 |
| R-03 | 임시 함수 입력 | 나이 · 성별 · 자차 · 월 실수령액(ML 소득 칸에 그대로) · **주거 · 통신 확정 고정지출 합계**(ML 고정비 칸, 고정지출 전체 아님) · 나눌 생활비 `T` · 소비 우선순위 · 카테고리 최소 금액 · 자물쇠 건 카테고리와 금액 · 입력 버전 |
| R-04 | 임시 함수 출력 | 예산 예측 플로우 6.3 모양: `status`(`proposed` · `unchanged` · `infeasible`) · 배분 대상 카테고리 금액(R-13; 예산 밖 코드는 없거나 0원만 허용) · 카테고리별 이유(없으면 빈 배열) · 요약. **전후 차이 · 합계 검증은 서버**가 한다 |
| R-05 | 임시 함수 속 계산 | 평균 비율(`fallback_ratios`, ML 담당에게 받기)을 **배분 대상(R-13) 안에서 다시 나눠** 아래 순서. ① 자물쇠 건 카테고리 = 잠긴 금액 그대로 ② 남은 금액을 나머지 카테고리에 비율대로 나눔 ③ 비율 몫이 최소 금액보다 작은 카테고리는 **최소 금액으로 고정**하고, 남은 카테고리끼리 다시 비율대로 나눔 (모두 최소 이상이 될 때까지 반복) ④ **자물쇠 · 최소 금액으로 고정된 금액은 건드리지 않고**, 비율로 나눈 카테고리만 1만원 단위 내림 ⑤ 자투리는 비율로 나눈 카테고리 중 비율이 가장 큰 곳에. 자물쇠 + 최소 금액 합계 > `T`면 `infeasible` (리뷰 10/28: 내림이 최소 금액을 깨지 않게 순서 고정) |
| R-06 | 기타 카테고리 | ML은 기타를 나누지 않는다(플로우 4.6). **기타 = 기타를 잠갔으면 잠긴 금액, 아니면 사용자가 정한 기타 최소 금액, 없으면 0원.** 기타를 먼저 떼고 `T = 생활비 − 기타`를 배분 대상(R-13)에 나눈다. 기타로 쓴 돈이 넘치면 여윳돈에서 충당(플로우 4.7) |
| R-07 | 주기 중간에 가입하면 | **특별 규칙 없음 — 정상 한 달 예산을 이번 주기에 바로 적용**하고, 이번 주기 시작일부터 이미 쓴 거래를 바로 분류해 카테고리별 쓴 돈(`spent`)에 반영한다 (첫 연결 때 4달 전 1일부터 거래를 가져오므로 가능). 이미 낸 고정지출 · 저축도 거래로 잡혀 '냈음'으로 보인다. 이미 생활비를 넘게 썼으면 넘친 만큼 여윳돈에서 충당, 그래도 모자라면 부족액 표시 (플로우 4.7). 이번 주기 월급이 확인되지 않으면(홈 `salaryState`가 `received` 아님) 예산은 그대로 두고 안내만 (10/27 — 처음 정한 '남은 날만큼 나누기'를 같은 날 바꿈: 대부분 월급날이 아닐 때 가입하고, 이미 쓴 돈을 반영하는 게 더 정확해서) |
| R-08 | 예산 1개 = 어떤 줄들 | `budget_item`에 고정비(카테고리별 합계) · 저축 · 쿠션 적립 · 생활비(배분 대상 카테고리 + 기타, R-13). **여윳돈은 저장하지 않고** `funding_amount − 줄 합계` |
| R-09 | `funding_amount`(배분 대상 자금) | 월 실수령액 (주기 중간 가입도 같음) |
| R-10 | 초안이 여러 개 생기면 | 같은 주기에서 새 추천을 만들면 **그 주기의 이전 초안은 `expired`**. 승인된 예산은 그대로 |
| R-12 | 만료 에러 | 409 **`expired`** (공통 에러 코드에 새로 추가). 조정안 승인도 같이 쓴다 |
| R-11 | 초안 만료 조건 | 결정기록 D(고정지출 · 저축 최소 금액 변경) + **지킬 돈 저장(P-09)** + 주기가 넘어감. 만들 때의 입력을 `input_hash`로 저장하고, 승인 때 지금 입력과 다르면 만료. 카드 선차감 금액은 해시에서 빼고 대상 카드 목록만 넣는다 (P-12 ④). **자물쇠는 초안 위에서 하는 행동이므로 해시 · 만료 조건에서 제외**하며 `input_snapshot`에는 기록용으로 남긴다. **계산 규칙 버전(`policy_version`)이 지금 서버 `POLICY_VERSION`과 다른 초안도 승인 때 만료**(11/3: 옛 정책 초안 승인 막기). 과거 승인 예산은 바꾸지 않는다 |
| R-13 | 생활비 예산 범위 (11/3 확정) | **주거·통신(`HOUSING_TELECOM`) · 여행·숙박(`TRAVEL_LODGING`)은 생활비 예산 밖**(서버 목록 `budgets/scope.py` `OUT_OF_BUDGET`). 새 배분 · 최소 금액 · 자물쇠 · 예산 화면 카테고리에서 빼고, 생활비 사용액 · 남은 생활비 · 하루 금액에서도 그 지출을 뺀다(11/2 '포함' 결정을 바꿈). 카테고리 자체 · 거래 목록 · 전체 소비 합계 · 고정지출 카테고리에는 그대로 남는다. **소비 우선순위**는 화면 선택지(`PRIORITY_CATS`)와 추천 엔진 입력에서 두 카테고리를 빼고 순위를 1부터 다시 매긴다(11/3 후속, 예전에 저장한 순위는 DB에 그대로). 예전에 걸어 둔 두 카테고리의 최소 금액 · 자물쇠는 DB에 두되 추천 계산에서 뺀다(자물쇠 모두 풀기 P-13에서도 건드리지 않음). ML 입력 `housing_fixed`(주거·통신 확정 고정지출 합계)는 유지 |

---

## 추천 예산 만들기

**엔진 결과의 남는 여윳돈 계약 (2026-11-04 확정)**

- 플랜 B는 평소 소비 이상으로 생활비를 부풀리지 않고, 남는 금액을 엔진 결과 `out["unallocated"]`로 반환한다. 이 키는 현재 서버 검수가 허용하는 성공 결과(`proposed`)에만 허용하며, `bool`을 제외한 0 이상 정수여야 한다.
- `check_result`는 키가 있으면 **배분 대상 카테고리 합계 + unallocated = T**, 없으면 기존대로 **카테고리 합계 = T**를 검사한다. 카테고리 집합 · 예산 밖 0원 · 음수 금지 · 최소 금액 · 자물쇠 · 빈 충돌 목록 검사는 그대로다. `infeasible`에 이 키가 있으면 0원이라도 검수 실패다.
- R-05의 기존 임시 엔진과 플랜 A는 `unallocated`를 반환하지 않으므로 계산 · 검수 동작이 바뀌지 않는다.
- 저장 시에도 `unallocated`의 정수 · 0 이상 조건을 다시 확인하며, **월 실수령액 − 저장 항목 합계 = 지킬 돈 여윳돈 + unallocated**여야 한다. R-08 · R-09의 저장 항목과 `funding_amount`는 그대로여서 예산 API의 `split.spare`가 남은 금액만큼 늘어난다. 여윳돈 별도 항목이나 DB 필드는 만들지 않는다.
- `run_recommend`와 `task.result` 형식, 승인 흐름은 변경하지 않는다. `unallocated`는 엔진 검수 · 저장 계약이며 작업 응답에 새 필드를 추가하는 변경이 아니다.

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 첫 설정 [지킬 돈 설정] → [추천 예산 보기]를 누를 때 (지킬 돈 저장 직후), 예산 화면에서 [다시 추천받기]를 누를 때 |
| 어디로 | `POST /api/budgets/recommend/` |
| 로그인 필요 | O · 세션 쿠키 + CSRF |
| 요청 번호 | **씀** (`Idempotency-Key`). 같은 번호면 같은 작업을 돌려준다 |
| 명세 상태 | 초안 |

**보내는 값** — 없음 (빈 본문 `{}`). 서버가 저장된 지킬 돈 · 내 정보 · 고정지출 · 정기 저축 · 자물쇠를 읽는다.

**받는 값 (202)**

| 이름 | 타입 | 뜻 |
|---|---|---|
| `task.taskId` | UUID | 작업 번호 → `GET /api/tasks/<taskId>/`로 결과 확인 |
| `task.status` | 문자 | 1차는 바로 `succeeded`가 올 수 있다 |

```json
{ "task": { "taskId": "8b1e…", "status": "succeeded" } }
```

**작업 결과** (`GET /api/tasks/<taskId>/`의 `result`, `taskType: "budget_recommend"`)

| 이름 | 타입 | 뜻 |
|---|---|---|
| `outcome` | 문자 | `proposed`(초안 만듦) · `infeasible`(조건을 함께 못 지킴 — 초안 안 만듦) |
| `budgetId` | UUID / null | 만든 초안. `infeasible`이면 `null` → 화면은 `GET /api/budgets/`로 초안을 읽는다 |
| `summary` | 문자 | 짧은 안내 (예: "식비 · 교통비 최소 금액을 지키며 나눴어요.") |
| `conflicts` | 배열 | `infeasible`일 때 충돌 목록. 아래 세 종류를 사용한다 (10/28 구현 결정) |

- `minimums_over_living`: `{ "type", "shortfall" }`. 자물쇠 금액 + 잠기지 않은 카테고리 최소 금액이 배분 금액을 넘거나, 기타 금액이 생활비를 넘을 때의 부족액.
- `lock_below_minimum`: `{ "type", "category", "lockedAmount", "minAmount" }`. 잠긴 금액이 그 카테고리의 최소 금액보다 작을 때.
- `locks_mismatch_living`: `{ "type", "difference" }`. 배분 대상 카테고리(R-13)가 모두 잠겼는데 합계가 `T`와 다를 때. `difference = 잠긴 합계 − T` (음수도 가능).

이 충돌들은 작업 `succeeded` + `outcome: infeasible`로 안내한다. `failed` + `engine_check_failed`는 엔진 결과가 서버 검증을 통과하지 못했을 때만 사용한다.

**중복 · 진행 중**

- 같은 요청 번호 + 같은 본문 → 처음 작업을 그대로 (공통 규칙).
- 이 회원의 `budget_recommend` 작업이 `queued` · `running`이면 새로 만들지 않고 **그 작업 번호**를 돌려준다 (다시 불러오기와 같은 규칙).

**실패하면**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| 지킬 돈을 저장한 적 없음 · 내 정보 · 월 실수령액 · 급여일 없음 | 409 | `not_ready` | "지킬 돈을 먼저 정해 주세요." → 지킬 돈 화면 |
| 저장된 지킬 돈이 지금 조건과 안 맞음 (예: 그사이 정기 저축이 늘어 저축 < 저축 최소 금액, 합계 > 배분 기준) | 409 | `not_ready` | "지킬 돈을 다시 확인해 주세요." → 지킬 돈 화면 |
| 같은 요청 번호에 다른 내용 | 409 | `idempotency_conflict` | 개발 중 버그 |
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 |

작업 안에서 생긴 실패는 작업 `status: failed` + `error.code`로 알린다: `engine_check_failed`(서버 검증 실패 — 합계 · 음수 · 최소 금액). 에이전트를 붙이면 `regeneration_limit` 등이 추가된다.

---

## 카테고리 예산 보기

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 하단 탭 [예산] 화면, 첫 설정 [추천 예산 확인] 화면을 열 때 |
| 어디로 | `GET /api/budgets/` |
| 로그인 필요 | O · 세션 쿠키 (읽기라 CSRF 없음) |
| 명세 상태 | 초안 |

**보내는 값** — 없음. 이번 급여 주기 기준.

**어떤 예산을 보여 주나** — ① 이번 주기에 **승인 전 초안**(`validated`)이 있으면 그 초안(첫 설정 · 다시 추천 확인용) ② 없으면 **승인된 예산** ③ 둘 다 없으면 `budget: null`.

**예산이 없을 때** (첫 가입 · 급여일 없음) — 오류가 아니라 빈 값. `budget` · `split` · `living` · `overspend` · `nag`는 `null`, `categories`는 `[]`, `unclassifiedCount`는 그대로 센다 (거래는 예산과 상관없이 있을 수 있음). `cycle`은 급여일이 있으면 객체, 없으면 `null`. 급여일이 없어서 주기를 정할 수 없으면 `unclassifiedCount`는 홈과 같이 오늘까지 전체 기간의 확인 필요 소비 거래를 센다.

```json
{ "cycle": { "startsOn": "2026-10-25", "endsBefore": "2026-11-25" },
  "budget": null, "split": null, "living": null, "overspend": null,
  "categories": [], "unclassifiedCount": 3, "nag": null }
```

**받는 값 (200)**

| 이름 | 타입 | 뜻 |
|---|---|---|
| `cycle` | 객체 / null | `{ "startsOn", "endsBefore" }` 이번 급여 주기 (홈과 같음). **급여일이 없으면 `null`** (그때 `budget`도 `null`) |
| `budget` | 객체 / null | 없으면 `null` → 화면은 "추천 예산을 먼저 만들어 주세요" |
| `budget.budgetId` · `versionNo` | UUID · 정수 | 예산 번호 · 이번 주기 안 버전 |
| `budget.status` | 문자 | `validated`(승인 전 초안) · `approved`(지금 적용 중) |
| `budget.salaryConfirmed` | 참/거짓 | 이번 주기 월급이 들어왔는지 (홈 `salaryState = received`). `false`면 "이번 주기 월급이 확인되지 않았어요" 안내 (R-07) |
| `budget.outOfBudgetAllocated` | 정수 | **이전 기준 예산 표시** (N-F1). 이 예산의 생활비 줄 중 주거·통신 · 여행·숙박 배정 합계. 11/3 전에 승인한 예산만 0보다 크다. 생활비 총액(`living.budget` · `split.living`)은 승인 때 저장한 그대로 이 금액을 포함하고, `categories`에는 두 카테고리가 없어 카테고리 합계 = 생활비 − 이 금액. 0보다 크면 화면이 "이전 기준으로 승인한 예산이에요 … 새 기준으로 추천을 다시 받아 주세요"를 보여 준다. 조회만으로 DB 금액 · 상태를 바꾸지 않는다 |
| `budget.engineVersion` | 문자 / null | 이 예산을 만든 계산 엔진 버전. `temp-`로 시작하면 임시 비율 추천 → 추천 화면에 "테스트 추천" 표시 (11/3) |
| `split` | 객체 / null | 한 달 나눠보기: `{ "funding", "fixed", "saving", "cushion", "living", "spare" }` (원). `funding = fixed + saving + cushion + living + spare` |
| `living` | 객체 / null | `{ "budget", "spent", "remaining" }` 이번 주기 생활비 · 쓴 돈 · 남은 돈(0 아래 안 내림). 쓴 돈은 **주기 시작일 ~ 오늘**(홈 · 채팅과 같은 기간, 11/3) 거래이며 예산 밖 카테고리(R-13) 지출은 뺀다 |
| `overspend` | 객체 / null | 생활비를 넘게 썼을 때 여윳돈 충당 (플로우 4.7): `{ "over", "coveredBySpare", "spareRemaining", "shortfall" }` = 생활비 초과액 · 여윳돈으로 충당한 금액 · 남은 여윳돈 · 그래도 모자란 금액. 안 넘었으면 `over`·`coveredBySpare`·`shortfall` = 0, `spareRemaining` = 여윳돈 전체. 저축 · 쿠션에서는 가져오지 않는다 |
| `categories` | 배열 | 배분 대상 카테고리 + 기타(R-13, 주거·통신 · 여행·숙박 없음), 화면 순서(`display_order`). 예산이 없으면 `[]`. 옛 정책 승인 예산에 두 카테고리 줄이 있어도 목록에 넣지 않는다 |
| `categories[].category` · `categoryName` | 문자 | 카테고리 코드 · 이름 |
| `categories[].allocated` | 정수 | 배정 금액 |
| `categories[].spent` | 정수 | 이번 주기에 그 카테고리로 쓴 돈 (고정지출로 연결된 거래는 빼고, 거래 목록과 같은 기준) |
| `categories[].minAmount` | 정수 / null | 내 정보에서 정한 최소 금액 |
| `categories[].locked` · `lockedAmount` | 참/거짓 · 정수 / null | 자물쇠 · 잠긴 금액. 보고 있는 예산의 `allocated`와 다를 수 있다 (예: 초안에서 잠근 금액을 승인 예산 화면에서 볼 때) |
| `categories[].reason` | 문자 / null | 추천 이유 한 줄 (에이전트가 주면) |
| `unclassifiedCount` | 정수 | 이번 주기의 '확인 필요' 소비 거래 수 |
| `outOfBudget` | 객체 / null | **예산 밖 지출 안내** (11/3, R-13) `{ "total", "categories": [{ "category", "spent" }] }`. 생활비 쪽으로 잡힌 주거·통신 · 여행·숙박 지출(고정지출로 연결된 거래는 빼고). 생활비 사용액과 같은 기간. 계산(사용액 · 남은 돈 · 하루 금액)에는 넣지 않고 안내만. 쓴 돈이 없으면 `total` 0 · `categories` `[]`, 예산이 없으면 `null`. 홈 `budget.outOfBudget` · 채팅 `outside_spent`와 같은 금액 |
| `nag` | 문자 / null | 잔소리 문구 (아래 금액 고정 API 참고). 없으면 `null` |

```json
{
  "cycle": { "startsOn": "2026-10-25", "endsBefore": "2026-11-25" },
  "budget": { "budgetId": "5c2a…", "versionNo": 1, "status": "validated", "salaryConfirmed": true, "engineVersion": "temp-fallback-1" },
  "split": { "funding": 2800000, "fixed": 770000, "saving": 610000, "cushion": 200000, "living": 1120000, "spare": 100000 },
  "living": { "budget": 1120000, "spent": 0, "remaining": 1120000 },
  "overspend": { "over": 0, "coveredBySpare": 0, "spareRemaining": 100000, "shortfall": 0 },
  "categories": [
    { "category": "FOOD", "categoryName": "식비", "allocated": 330000, "spent": 0, "minAmount": 300000, "locked": false, "lockedAmount": null, "reason": null }
  ],
  "unclassifiedCount": 0,
  "outOfBudget": { "total": 0, "categories": [] },
  "nag": null
}
```

**실패하면**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 |

---

## 카테고리 금액 고정 · 해제

| 칸 | 내용 |
|---|---|
| 어떤 기능 | 예산 · 추천 예산 화면에서 카테고리 옆 자물쇠를 누를 때 |
| 어디로 | `PATCH /api/budgets/<uuid:budget_id>/categories/<code>/` |
| 로그인 필요 | O · 세션 쿠키 + CSRF |
| 요청 번호 | 안 씀 (같은 값을 두 번 보내도 결과가 같다) |
| 명세 상태 | 초안 |

**자물쇠의 뜻** — 다시 추천받을 때 **그 카테고리 금액을 그대로 두고** 나머지만 다시 나눈다. 금액 자체를 바꾸는 건 이 API가 아니라 챗봇 조정(10/15 결정 — 예산 초안 수정은 챗봇으로).

**보내는 값**

| 이름 | 타입 | 필수 | 범위 · 설명 | 예시 |
|---|---|---|---|---|
| `locked` | 참/거짓 | O | 걸기 `true` · 풀기 `false` | `true` |

**받는 값 (200)**

| 이름 | 타입 | 뜻 |
|---|---|---|
| `category` · `categoryName` | 문자 | 카테고리 |
| `locked` | 참/거짓 | 지금 상태 |
| `lockedAmount` | 정수 / null | 잠긴 금액 (다시 추천해도 유지될 금액). 풀려 있으면 `null` |
| `changeCount` | 정수 | 이번 주기에 이 카테고리 자물쇠를 바꾼 횟수 (값이 실제로 바뀔 때만 +1) |
| `nag` | 문자 / null | 잔소리 문구. 없으면 `null` |

**규칙**

- 자물쇠는 **급여 주기 단위**로 저장한다(`budget_category_setting`). 같은 주기의 초안 · 승인 예산이 같은 자물쇠를 본다. **새 주기가 처음 만들어질 때 바로 전 주기의 자물쇠(잠긴 카테고리 · 잠긴 금액)를 이어받는다** — 자물쇠는 다음 달에도 적용 (10/28 기획 확인). 바꾼 횟수는 0부터 다시 센다(잔소리는 이번 주기 기준). 생활비가 바뀌어 안 맞으면 풀린다(P-13).
- **걸 때 금액도 같이 저장한다** (리뷰 10/28): `budget_category_setting`에 **`locked_amount` 칸 추가**(DB 변경). 자물쇠를 거는 순간 **주소의 예산(`budget_id`)에서 그 카테고리 배정 금액**을 저장하고, 다시 추천받을 때 이 금액을 그대로 쓴다. 풀면 `null`. 이미 걸린 상태에서 다른 예산 화면에서 다시 `true`를 보내면 그 예산 금액으로 바꾼다 (`changeCount`는 안 늘림 — 잠금 여부는 그대로라서).
- 잔소리 규칙(10/15 결정): ① 주거 · 통신, 교통 · 차량 **말고** 다른 카테고리의 자물쇠를 이번 주기에 **3번 이상** 바꾸면 ② 이번 주기에 예산을 **3번 이상 승인**하면 → `nag`에 문구. 문구: "자주 바꾸고 있어요. 이번 달 계획을 한 번 정리해 볼까요?" (10/28 확정)
- 바꿀 수 있는 예산: 이번 주기의 `validated` · `approved`. 지난 주기 · `superseded` · `expired` 예산은 409.

**실패하면**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| 없는 예산 · 남의 예산 · 없는 카테고리 코드 · `UNCLASSIFIED` (주소가 틀림) | 404 | `not_found` | "찾을 수 없어요" |
| `locked` 없음 · 참/거짓 아님 | 400 | `invalid` | `fields.locked` |
| 지난 예산 · 이미 바뀐 예산 | 409 | `not_ready` | "지난 예산은 바꿀 수 없어요." |
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 |

---

## 예산 승인

| 칸 | 내용 |
|---|---|
| 어떤 기능 | [추천 예산 확인]에서 [이 예산으로 시작하기], 다시 추천받은 초안에서 [이 예산으로 바꾸기]를 누를 때 |
| 어디로 | `POST /api/budgets/<uuid:budget_id>/approve/` |
| 로그인 필요 | O · 세션 쿠키 + CSRF |
| 요청 번호 | **씀** (`Idempotency-Key`, API_RULES — 예산 승인) |
| 명세 상태 | 초안 |

**보내는 값** — 없음 (빈 본문 `{}`)

**승인하면** (한 트랜잭션)

1. 이 예산이 `validated`인지, 이번 주기인지, **지금의 지킬 돈이 그대로 쓸 수 있는지**(추천 만들기와 같은 검사 — 잔액이 줄어 쿠션이 필수가 됐는데 쿠션 0원 등, 리뷰 10/28. 카드 선차감은 초안에 저장한 값 사용, P-12 ④), 만들 때 입력(`input_hash`)이 지금 입력과 같은지 확인 (R-11)
2. 이번 주기에 이미 `approved`가 있으면 → `superseded`
3. 이 예산 → `approved`, `approved_at` · `approval_key` 저장
4. 첫 설정이면 온보딩 완료 (`onboardingCompleted = true`, auth.md ④). 별도 칸을 저장하지 않으며 auth는 `approved_at`이 있는 예산이 존재하는지 계산한다.

조정안 승인(혜리, `POST /api/budgets/proposals/<id>/approve/`)도 **같은 규칙(버전 · 만료 · 중복)**을 따른다.

**받는 값 (200)** — `GET /api/budgets/`와 같은 모양 (`budget.status = "approved"`)

**중복 승인**

- 같은 요청 번호 → 처음 응답 그대로.
- 다른 요청 번호인데 **이미 `approved`인 같은 예산** → **이번 주기 예산일 때만** 에러 없이 지금 상태를 돌려준다. 지난 주기에 승인한 예산이면 409 `not_ready` "지난 예산은 승인할 수 없어요." (리뷰 10/28)

**실패하면**

| 상황 | 상태 | `code` | 화면 안내 |
|---|---|---|---|
| 없는 예산 · 남의 예산 | 404 | `not_found` | "찾을 수 없어요" |
| 만료된 초안 (고정지출 · 저축 · 지킬 돈이 바뀜, 주기가 넘어감, 새 추천이 나옴) | 409 | `expired` | "조건이 바뀌어서 다시 추천받아야 해요." + [다시 추천받기] |
| 이미 대체된 예산(`superseded`) · 거절된 예산 · 지난 주기에 승인한 예산 | 409 | `not_ready` | "지난 예산은 승인할 수 없어요." |
| 같은 요청 번호에 다른 내용 | 409 | `idempotency_conflict` | 개발 중 버그 |
| 로그인 안 함 | 403 | `not_authenticated` | 로그인 화면으로 |

---

## 남은 확인

- 평균 비율 10개(R-05): ML 담당에게 `fallback_ratios` 숫자만 받기 (모델 파일은 저장소에 없음). 받기 전까지 임시 함수는 화면 시안 비율(`recommend.py` `FALLBACK_RATIOS`)을 쓴다.
- 잔소리 문구는 위 금액 고정 규칙의 문장으로 확정 (10/28).


### 2026-11-06 A/B → 조정 참조 및 다음 주기 목표

신규 A/B Budget.input_snapshot에는 원 ML 몫(활성 코드별 원 단위 올림 정수)과 ml_reference_meta(source/model_id/model_checksum/target/input_hash/raw_ratios/normalization)를 보존한다. target은 여윳돈 충당 전 최초 T₀다. transport_history_reference는 검증된 완료 3개월의 정확한 교통 기준액 분자/분모·월·지문·규칙 버전만 전달한다. 미검증은 unavailable이며 실제 거래/가맹점/개인 사유를 포함하지 않는다.

프로필 budget_category_targets는 category_targets 입력·해시에 포함한다. 최소액/잠금과 별도 출발값이며 ETC는 기타 예약액으로 분리한다. 불가 시 task outcome=target_conflict, conflicts에 requestedAmount/possibleAmount/diff/reason을 반환한다. 성공 후 목표와 최종액 차이는 GET 예산 recommendation.targetConflicts에 같은 구조로 반환하며 승인 전에 표시한다. 목표 자체는 변경하지 않는다. 목표 설정·변경·해제는 이전 초안을 만료시킨다. 필드 0011은 실제 DB 적용 전이다.

### 11/05 카드 확인 필요 안내

`cardPending[].reason=card_assignment_required`는 금액은 확인됐지만 결제 카드 확인이 필요한 승인 거래가 있음을 뜻한다. 지킬 돈 화면 안에서 결제 카드 확인 목록을 펼쳐 확인한다. 확인이 끝나면 지킬 돈 API를 다시 조회한다. 사용자 확인 후에도 금액·취소 오류 또는 조회 기간 공백이 남으면 계속 보류한다.

결제 카드 확인에는 카드번호·비밀번호 입력이나 CODEF 재조회가 필요 없다. 이 상태에서는 재조회 입력란을 숨기며, 사용자가 별도로 ‘카드 내역도 다시 불러오기’를 선택한 경우에만 표시한다.


### 11/05 지킬 돈에서 카드 내역 재조회

`cardPending[].cardId`와 연결 목록의 `cardIds`를 대조해 관련 카드사만 화면에 표시한다. 해당 기관의 다시 불러오기 버튼은 기존 `POST /api/finance/sync/`에 그 `connectionId`만 전달한다. 현대카드는 같은 화면에서 추가 인증 정보를 입력하고, 요청을 시작하면 입력칸을 비운다. 조회 결과에 따라 같은 화면에서 미매칭 거래 확인 또는 재로그인을 진행한다. `relogin`은 기관 로그인 팝업으로 다시 연결하며, 별도 문자 인증이 필요한 경우에만 금융 연결 화면으로 안내한다. 재조회·거래 확인 후에는 `GET /api/budgets/protect/`를 다시 호출한다.

카드 인증 정보의 영구 저장·브라우저 저장소 캐시는 추가하지 않았다. 기존 connectedId 보관과 카드사별 추가 인증 요구는 그대로 유지한다.

### 11/05 동의 철회 후 예산 처리

금융 조회 동의를 철회하고 데이터를 삭제하면 기존 예산·쿠션·분석 결과와 지킬 돈 저장값도 초기화된다. 계좌 조회 동의를 철회한 채 다시 동의하지 않은 회원의 지킬 돈 PUT, 추천 POST, 카테고리 금액 고정 PATCH, 승인 POST는 `403 consent_required`다. 카드·대출 선택 동의만 철회했다면 남은 데이터로 예산을 다시 설정할 수 있다.

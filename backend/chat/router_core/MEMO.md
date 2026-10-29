# 라우터 메모 (router_core) — 내부 초안, 팀 공유 전

> 상태 (2026-10-29, 28차 = 역할별 실제 LLM 평가 실행 (15번, 138호출). 27차 = 역할별 실제 LLM 평가 준비 (14번, 실제 호출 없음). 26차 = 단계 B 역할별 입력 · 프롬프트 분리 + 오프라인 검증 (13번, 실제 LLM 미호출). 25차 = 단계 A 구현: 기존 프롬프트 그대로 코드 책임 분리 (12번). 24차 = 11번 설계 정정: 담당 에이전트 우선 · not_mine 재분류 (설계만). 23차 = 메인 라우터 + 세 에이전트 구조 설계안. 22차 = 답변 템플릿 · 대화 맥락 독립 연결 (10번). 21차 = 소비 분석 1~9번 내부 결과 (9번). 20차 = 수집 완전성 판정 (8번). 19차 = 승인 예산 읽기 · 분석 기간 계산 (7번). 18차 = 소비 분석 검증용 독립 DB fixture · 기존 집계 함수 검증 (6번). 17차 = 지시문 고정(6a3bb2a3) · 신규 평가 완료. 실제 서비스 연결 없음)
> [확정] = 사용자 규칙, [임시] = 구현자가 고른 것, [미정] = 결정 필요(질문), [폐기] = 현행 규칙 아님(기록용). 판단은 항상 [확정] · [임시] 칸 기준.

## 1. 쓰는 API · 함수

**실제로 쓰는 것**
- `budgets/migrations/0003_seed_categories.py` `CATEGORIES` — 카테고리 코드 · 화면 이름 원본 (DB 초기 데이터, 읽기만). 코드 검사 · 되묻기 선택지 · 직접 입력한 이름 찾기에 씀. `API_RULES.md` 표 · `frontend/src/utils/categories.js` 와 코드 · 이름이 같음을 확인. (2차까지 쓰던 `config/openapi.py` `CATEGORIES` 는 이름이 없어서 더 이상 쓰지 않음)
- 표준 라이브러리만 사용 (`dataclasses`, `json`). Pydantic 은 `.venv` 에 있지만 `requirements.txt` 에 없어서 쓰지 않음.

**통합 때 쓸 후보**
- `chat/router.py` `route_text(text, conversation)` — 여기서 `router_core.route()` 호출 + Decision → `(intent, slots)` 변환.
- `chat/replies.py` `reply()` — Decision `status` · `reason` 별 답변 문장 · 안내(months_capped, last_month_only, category_compare, budget_current 질문 문구).
- `chat.Conversation.active_agent` / `slots` — `MemoryStateStore` 를 대신할 영속 저장소 후보.
- 소비 집계 함수(세진, 3단계) — `run_items(items, fetch)` 의 `fetch` 자리.
- 예산 조정 에이전트(나은 · 세진) — `Decision.handoff` = `{task: "budget_adjust", text: 원문, context: {conversation_id, selected_from, last_analysis}, hints: {categories?, direction?, amount?, amount_mode?}}` 를 받는 쪽. 세부 해석 · 되묻기는 에이전트 책임.
- `budgets.Category` (`is_active`, `display_order`, `display_name`) — 통합 때 카테고리 선택지 · 이름의 실제 원본.
- 소비 분석 계층 → `record_analysis(store, conversation_id, decision, results)` — 조회 성공 결과를 직전 분석으로 기록하는 내부 인터페이스(1단계). `results` 는 `run_items` 결과, 성공 항목 `result` 에 `period`(실제 조회 기간) · `as_of`(조회 시점) 필수, `compare_period` · `facts` 선택. 지금은 테스트 대역만 호출.
- LLM 호출 — `complete(system, user) -> str` 자리. SDK · 모델 미정 (`ENV_SETUP.md` 의 `OPENAI_API_KEY` 는 "제공자 확정 후").
- 예산 조정 에이전트 결과 → `record_budget_task(store, conversation_id, agent_result, server)` — 통합 계층이 에이전트 답 뒤에 부르는 내부 인터페이스(2단계). 성공 · 실패 · 취소는 `server` 어댑터 확인값으로만 정함. 지금은 테스트 대역만 호출.
- 취소 확인 뒤 새 작업 시작 → `pop_queued_start(store, conversation_id) → Decision | None` — 통합 계층이 `record_budget_task` 바로 다음에 부른다. 시작 준비된 새 요청이 있으면 `mode=start` 를 **한 번만** 돌려준다.
- 읽기 전용 서버 어댑터 `server.task_status(task_ref) → {"task_ref", "state", "saved_ref"?, "checked_at"}` — **실제 구현 없음, 테스트 대역(FakeServer)뿐.** 통합 때 `chat.AsyncTask` · `budgets.Budget` 을 읽는 서버 코드로 공급(아래 필요한 것).

## 2. 필요한 것

- 지난달 단독 조회 ("지난달 얼마 썼어?") — 후속 기능 후보. 지금은 `unsupported/last_month_only`.
- 소비 집계 함수 계약: 유형(1~9) · 카테고리 · `months_applied` 를 받아 날짜 범위(달력월 / 월급날 기준 예산 기간 / 지난달 같은 날짜 / 완료된 달)를 서버가 계산하는 입력 모양.
- 예산 조정 에이전트 계약: 위 `handoff` 모양은 라우터 쪽 초안(맥락은 `context.last_analysis` 까지 구현). 에이전트의 진행 상태 · 완료(서버 저장 결과) 신호 · 진행 중 작업 보관 주체 · 대기 상태 소유권은 미정(2단계). 기존 chat.md `slots` 의 `amount` · `direction` 과 `hints` 의 관계도 통합 때 정해야 함.
- 예산 잔액의 비교 · 기간 조회(지난달 대비 · 최근 N개월 · 지난달 단독 · 카테고리끼리) — 지원 범위 밖 (현재 예산 기간으로 볼지 되물음). 후속 기능 후보.
- **카테고리 공급 방식 교체 (실제 연결 전 필수).** 지금 `schema.CATEGORY_NAMES` 는 마이그레이션 초기 데이터 상수를 읽는 **테스트 단계 임시 방안**이다. 초기 데이터는 이후 추가 · 이름 변경 · `is_active=False` 를 반영하지 못하므로, 연결 전에 현재 서비스의 카테고리 · 활성 여부 · 화면 순서를 확인하는 공급 방식(`budgets.Category` 조회 등)으로 바꿔야 한다.
- 카테고리 선택지 UI · 기존 chat API 의 선택 입력 연결 — 통합 검수 항목. 지금 API 의 `choice` 는 `spending`/`budget` 만 받으므로(`router.CHOICE_LABELS`) 카테고리 코드를 버튼으로 보내려면 API 계약 변경이 필요. 이번엔 문장(text)으로 코드나 화면 이름을 보내는 것만 처리. 기존 API 형식은 바꾸지 않음.
- 카테고리끼리 직접 비교 — 1차 미지원. 후속 기능 후보.
- `Decision` → 기존 `(intent, slots)` 대응표 (아래 미정 참고). 기존 `slots` 의 `period {from,to}` 는 라우터가 날짜를 계산해야 채울 수 있음 → 라우터는 날짜 계산을 안 하므로 누가 채울지 정해야 함.
- LLM SDK 의존성 · 설정(키 이름, 모델 이름, 타임아웃) — 추가 전 보고 필요.
- 대화 상태 저장 위치 · 만료 — **통합 전 결정 항목.** `pending` 과 `last_analysis` 둘 다 지금은 테스트용 메모리 저장소(만료 없음). `Conversation.slots` 등 DB 칸이 있다는 것만으로 계약이 확정된 게 아님(기존 API 공개 칸 `route.slots` 와도 분리 필요). 운영 만료값은 정하지 않음. 모델 변경이 필요하면 DB 담당에게.
- 소비 분석 계층이 `record_analysis` 에 넘길 결과 계약(기간 · 조회 시점 · 비교 기간 · facts 모양) 확정, 실제 호출 위치.
- 설명 요청(`Decision.explain`) 처리: 같은 조건 재조회 vs 기록된 결과 설명 구분, 설명 생성 · 검증 — 3b 단계.
- 직전 분석은 1개만 기억. 더 이전 분석 참조("아까 그 전에 본 거")는 미지원.
- **(2단계) 서버 어댑터 실제 계약 — 존재하지 않음, 통합 때 필요:** ① `task_ref` 가 무엇인지(`AsyncTask.task_id` 후보, 예산 조정 작업 종류는 아직 없음 — 지금 작업 종류는 `budget_recommend` · 동기화뿐) ② 작업 상태 → `proposing` · `awaiting_approval` · `saving` · `saved` · `failed` · `canceled` · `expired` · `rejected` 대응 규칙(`AsyncTask.status` + `Budget.status` 조합) ③ **이 작업의 변경이 저장됐다는 근거 `saved_ref`** — 작업과 저장된 예산 버전을 잇는 식별자(예: `Budget.task` FK + `version_no` 후보, 계약 미정). 현재 승인 예산이 있다는 사실(A)만으로 이번 작업 저장(B)을 판단하지 않는다 ④ 읽기 전용 보장(승인 · 저장 API 호출 금지).
- (11차) **취소 대신 다른 결과로 끝났을 때 보관한 새 요청을 어떻게 이어갈지 — 정책 필요.** 지금은 시작하지 않고 `budget_result.unstarted_request` 에만 보관(안내 코드 · 재시작 질문 없음). 예: 저장 성공 후 "새 요청도 진행할까요?" 를 물을지, 버릴지.
- (11차) 에이전트에게 보내는 취소 요청(`mode=cancel`)과 사용자의 "취소" 원문 전달(`mode=continue, text="취소"`)을 에이전트가 어떻게 처리할지 확정. 지금 사용자의 "취소" 는 원문 그대로 continue.
- **(2단계) 현재 적용 중인 승인 예산 읽기 계약** — 읽기 전용, 현재 주기 + `approved`. 지난 주기 예산도 `approved` 로 남으므로(`budget_approve` 주석) 주기 조건 필요. 없음 · 조회 불가를 구분해 돌려줘야 함. `budget_data()` 는 초안 우선 · 주기 생성 부작용이라 그대로 쓰지 않음. 소비 분석 계층이 `Decision.budget_basis.refetch_current_budget` 를 보고 부른다.
- (2단계) 예산 조정 에이전트 결과 모양(`task_ref` · `ended` · `content`)과 전달 정보 `handoff.mode`(start · continue · resume · cancel) · `context.active_task` · `task_state` · `cancel_for` 를 에이전트 쪽과 확정.
- (2단계) 서버 중복 작업 정책 — 대화를 넘어선 같은 회원의 동시 조정 작업(`start_or_reuse` 는 같은 종류 진행 중 작업을 재사용). 지금은 **대화당 하나**만 라우터가 제한.
- (2단계) 안내 코드 문구: `budget_task_suspended`(보류됨 · 재개 시 서버 확인 필요 — "제안이 그대로 있다"는 보장 표현 금지), `budget_task_saving`(저장 진행 중 · 결과는 확인 후), `budget_change_not_saved`, `no_budget_change_record`. 질문 코드: `cancel_target`, `budget_task_choice`, `scope/analysis_unclear`. 실패 코드: `task_check_failed`.

## 3. 임시 결정

**[확정] 사용자 규칙 (구현됨)**
- 최근 N개월만 기본 3 · 최대 3. 원래 말한 개월 수는 `interpretation.months` · `items[].months_requested` 로 보존, 적용값은 `months_applied`, 넘으면 `notices=("months_capped",)`.
- 0개월 이하 → 개월 수 되묻기, 나머지 조건 보존.
- 1~7번 항목에는 개월 칸 자체를 넣지 않는다.
- 여러 카테고리는 말한 순서대로 각각 항목, 공통 조건 복사. 하나를 고르게 묻지 않음.
- 소비 + 예산 조정이 섞이면 무엇부터 할지 질문, 고르지 않은 쪽은 실행 안 함.
- 지난달 단독 조회는 미지원 안내, 다른 유형으로 바꾸지 않음.
- 실패 시 대기 상태 유지, 새 요청은 해석 성공 뒤에 이전 상태를 바꿈, 완료 시 대기 상태 해제, 대화별 분리. 완료 후에도 직전 성공 분석은 이어받음(7차, 아래).
- 알아보지 못한 카테고리는 ETC/UNCLASSIFIED 에 넣지 않음.
- (10/29 3차) 분석 대상(`target`)을 먼저 유지하고, 비교 방식(`current`·`prev_month`·`recent`·`last_month`·`category_compare`)이 그 대상에서 지원되는지로 판단 (아래 표). 특정 문장 예외 없음.
- (10/29 3차) 예산 잔액은 `current` 만 지원. 나머지 비교 방식은 `asking=budget_current` + `reason=<비교 방식>`(미지원 안내용). 동의 → 비교 · 기간 조건을 지우고 3번(카테고리 있음) · 4번(없음).
- (10/29 3차) 모르는 카테고리는 원래 자리에 `{"unknown": "원문"}` 으로 남기고, 답으로 받은 코드가 그 자리를 채움 ("데이트비랑 식비" → 문화여가 → CULTURE_LEISURE, FOOD).
- (10/29 5차 → 역할 정정으로 일부 변경) ETC 는 남은 예산 조회(3번) 경로로 그대로. UNCLASSIFIED 는 소비 조회만 지원, **남은 예산 조회**에서는 미지원: `skipped=[{category, reason: "no_budget_category"}]`, `items` 에 넣지 않음. 다른 코드로 바꾸거나 0 원으로 만들지 않음. **예산 조정에서는 라우터가 ETC · UNCLASSIFIED 를 판단하지 않고 hints 에 그대로 둔다.**
- (10/29 5차) 복수 카테고리 **남은 예산 조회**는 지원되는 항목을 그대로 처리(`status=ready`)하고 UNCLASSIFIED 만 `skipped` 로 구분. UNCLASSIFIED 만 있으면 `status=unsupported, reason=no_budget_category` (현재 기간 질문도 하지 않음).
- (10/29 10차, 2단계 확정) 예산 조정 전달 `handoff.mode` = start(새 작업) · continue(진행 중 작업에 대한 말) · resume(보류 작업 재개). 원문 · 대화 ID · 직전 분석 · 작업 참조(`context.active_task`)를 넘기고, 세부 해석 · 질문 · 승인은 에이전트 책임.
- (10차) 저장 성공 · 실패 · 취소 · 만료는 읽기 전용 서버 어댑터 확인값으로만 정한다. 에이전트 `content` · LLM 출력 · 결과 안의 `server` 같은 칸은 믿지 않는다. `saved` 는 이 작업과 저장 결과를 잇는 `saved_ref` 가 있어야만 인정. 확인 실패면 대화 상태를 바꾸지 않고 성공 · 취소 · 만료를 추정하지 않는다.
- (10차) 진행 중 조정이 있을 때 "응" · "그렇게 해줘" 등 새 요청이 아닌 말은 원문 그대로 에이전트(continue). 라우터는 승인 여부를 정하지 않는다.
- (10차) 제안 · 승인 대기 중 소비 분석 요청 → 작업을 보류 표시하고 소비 분석으로 전환. 자동 승인 · 저장 · 삭제 없음. `saving` 중이면 보류 표시만 하고 저장 진행 사실 유지(`budget_task_saving`), 결과는 별도 확인. 보류 안내는 전환하는 답변에 한 번만.
- (10차) 재개: 읽기 전용 어댑터로 상태 확인(승인 API 호출 안 함). 진행 중이면 보류 해제 + resume, 끝났으면 결과를 기록하고 resume 으로 넘겨 에이전트가 안내. 확인 실패 → `failed/task_check_failed`, 상태 유지.
- (10차 → 11차 수정) 재개 요청(`resume=true`)과 새 조정 요청을 구분. 기존 작업이 있는데 새 조정 요청이면 자동 resume · 덮어쓰기 · 새 작업 생성 없이 `asking=budget_task_choice`(이어서 / 취소하고 새로).
- (11차 확정) "취소하고 새로" → **기존 작업 취소 요청만** 에이전트에 전달(`mode=cancel, context.cancel_for="restart"`). 새 요청 원문 · 보조값은 `budget_task.next_start` 에 보관. 서버 어댑터가 기존 작업의 `canceled` 를 확인한 뒤에만 `pop_queued_start` 가 `mode=start` 를 한 번 돌려준다. 취소 진행 중 · 확인 실패면 기존 작업과 새 요청 모두 유지.
- (11차) `saving` 중에는 취소 요청만으로 저장이 멈췄다고 보지 않는다. 취소 대신 `saved` 등 다른 결과로 끝나면 그 결과 그대로 기록(취소로 바꾸지 않음)하고, 새 요청은 시작하지 않고 `budget_result.unstarted_request` 에 보관.
- (11차) 서버 작업 참조가 없는 조정 대화(에이전트가 묻는 중)도 같은 순서: 취소 전달 → 에이전트가 `ended` 를 알리면 시작 준비. 이건 서버 작업 취소가 아니라 대화 종료라서 `budget_result` 를 남기지 않는다(`ended_without_task`).
- (11차) 이미 끝났다고 기록한 작업의 결과가 다시 와도(`record_budget_task` 에 같은 옛 `task_ref`) 지금 작업 · 시작 준비를 건드리지 않고 기록된 결과만 돌려준다 → start 중복 방지.
- (11차) 서버 확인이 실패하면 어떤 상태도 바꾸지 않는다: `budget_task_choice` 질문에서 "이어서" → 확인 실패여도 질문 · 작업 · 결과 · 직전 분석 그대로. 대기 질문이 있는 중 재개 요청이 확인 실패해도 대기 질문 유지. 확인 성공 뒤에만 질문을 해제한다.
- (10차) 대화당 진행 중 · 보류 조정 작업은 하나 (현재 단계 정책). 대화를 넘는 서버 중복 정책은 통합 항목.
- (10차) 조정 작업 취소는 에이전트에 전달(continue)하고, 라우터가 취소됐다고 먼저 기록하지 않는다. 라우터 질문과 조정 작업이 함께 있어 "취소" 대상이 불명확하면 `asking=cancel_target`(질문 / 예산 조정)으로 묻는다.
- (10차) 끝난 작업은 해제하되 결과 참조(`budget_result`: outcome · task_ref · saved_ref · checked_at)는 남긴다. "바뀐 예산으로 …" 는 `budget_basis={refetch_current_budget: True, last_change}` 로 재조회 필요만 전달 — 과거 결과의 참조를 현재 적용 예산으로 고정하지 않음. 분석 종류 · 카테고리는 명시 · 맥락으로 정하고, 범위가 없으면 `scope/analysis_unclear` 로 묻는다(3 · 4번에 고정 안 함). 저장 안 됨 → `budget_change_not_saved`, 기록 없음 → `no_budget_change_record`. 기존 승인 예산이 쓸 수 있는지는 라우터가 가정하지 않고 소비 분석 쪽 재조회 결과(없음 · 조회 불가 구분)에 맡김.
- (10/29 8차 확정) "전체는?" · "전체 소비는?" · "전체 예산은?" → 이전 카테고리를 해제하고 유지 가능한 기간 · 비교로 유형 재결정 (식비 1·2번 → 1번, 7번 → 6번, 9번 → 8번 · 개월 유지, 3번 → 4번).
- (10/29 8차 확정) 최근 추이에서 개월 수만 바꾸면 최근 추이 · 카테고리 유지, 원래 말한 개월 보존 · 3개월 제한 안내. 기간 방식을 바꾸면 이전 최근 추이 · 개월 해제, 최근 N개월로 바뀌면 지난달 비교 해제.
- (10/29 8차 확정) 명시한 조건끼리 모순이면 추측하지 않고 `asking=scope, reason=conflicting_conditions` 로 되묻는다. 답하면 이어받은 다른 조건(카테고리 등)은 유지.
- (10/29 7차, 1단계 확정) 소비 분석 완료 후에도 직전 **성공** 분석 맥락을 유지하고 후속 질문에 이어받는다. 대기 상태(`pending`)와 직전 분석(`last_analysis`)은 따로 저장 · 해제.
- (10/29 7차) 직전 분석은 소비 분석 계층이 성공 결과를 넘길 때(`record_analysis`)만 바뀐다. 라우터 `ready` 만으로는 기록하지 않음. 전부 실패면 직전 성공 유지, 일부 성공이면 항목마다 `ok` 로 구분(실패 항목은 결과 없음, 설명 참조 대상 아님).
- (10/29 7차) LLM 은 명시한 칸과 `refers_to`(previous · none · unclear)만 준다. 이어받기 · 기본값은 코드: 명시 칸 우선 → 빈 칸만 직전 조건에서(호환되는 것만) → 그 뒤에 기본값(3개월 등). 독립 요청(`none`)에는 이어받지 않음.
- (10/29 7차) "최근 N개월 분석" 뒤 "예산은?" → 소비 기간을 예산 기간으로 조용히 바꾸지 않고 현재 예산 기간 확인 질문. 동의 → 3번(카테고리) · 4번(전체), 거절 · 불분명은 기존 `budget_current` 규칙. 예산 **조정** 요청은 이 질문 없이 에이전트로 전달(`context.last_analysis` 동봉).
- (10/29 7차) 이어받을 분석이 없거나 참조가 불명확하면(`refers_to=unclear`) 기본값으로 채우지 않고 `asking=scope` 로 조건을 묻는다. 설명 요청 대상이 여러 개면 `asking=explain_target` 로 묻는다.
- (10/29 7차) "왜 늘었어?"(`explain`)는 정형 조회로 바꾸지 않는다. `Decision.explain = {question, items}` 로 참조할 직전 분석 항목(실제 조회 기간 · 조회 시점 포함)만 넘긴다. 직전 분석 조건 · 결과는 내부 대화 상태에만 두고 카드 명세 · API 응답은 바꾸지 않음.
- (10/29 6차, 역할 정정 확정) 메인 라우터는 예산 조정 의도만 판별하고 **원문 + 맥락 + 선택적 hints** 를 넘긴다. 세부 정보 부족 · 모르는 카테고리 · UNCLASSIFIED · by/to 불명확으로 되묻거나 막지 않고, 카테고리를 빼거나 요청 · 금액을 나누지 않는다. 카테고리 · 금액 · 방향 · by/to · 적용 범위 확인과 되묻기는 예산 조정 에이전트 몫.
- (10/29 6차) 예산 조정 **만** 요청했고 의도가 유효한데 보조값(categories · direction · amount · amount_mode)만 형식이 틀리면 hints 를 통째로 비우고 원문 전달. JSON 해석 불가 · 의도 형식 오류 · LLM 호출 실패는 기존대로 실패 + 대기 상태 유지. 소비 분석(복합 의도 포함)의 형식 검사는 완화하지 않음.
- (10/29 6차) 소비 + 예산 조정 복합 의도 선택 질문은 라우터에 유지. 예산 조정을 고르면 `context.selected_from=["spending","budget_adjust"]` 로 원문에 다른 작업이 섞였음을 알린다.
- (10/29 4차) 소비 조회의 카테고리 선택지 = ETC · UNCLASSIFIED 포함 12개, 기존 화면 이름 그대로.
- (10/29 4차) 남은 예산 + 카테고리끼리 비교 → 현재 기간 질문이 아니라 `unsupported/category_compare`. 단순 복수 카테고리 잔액 조회는 카테고리마다 3번.
- (10/29 4차) 남은 예산 + 지난달 단독 → 미지원 사유(`reason=last_month`) + 현재 예산 기간 확인 질문 유지.
- (10/29 4차) 지원 여부 · 예산 데이터 유무를 이유로 금액을 0 으로 만들지 않는다. 라우터 결과(`items`)에는 금액 칸이 없고, `hints.amount` 는 사용자가 말한 값일 때만 있다.
- (10/29 3차) 카테고리 되묻기 결과: `ask_about`=모르는 말, `options`=[{code, name}] 서비스 카테고리 목록 (이름은 초기 데이터 그대로). 답은 코드 · 화면 이름 · 자연어 모두 받음. 조회 조건만 정하고 거래 카테고리 · DB 는 바꾸지 않음.
- (10/29) 예산 잔액 + 개월 수/최근 → 8·9번으로 바꾸지 않고 "현재 예산 기간의 ○○ 잔액을 확인할까요?" 되묻기(`asking=budget_current`, `ask_about`=카테고리). 동의 → 현재 예산 기간 3번(카테고리 없으면 4번), 거절 → `cancelled/declined` 로 대기 종료, 불분명 → 같은 질문 · 상태 유지.
- (10/29) 카테고리끼리 직접 비교 → `unsupported/category_compare`. compare 없는 복수 카테고리 조회와 구분.
- (10/29) 일부 카테고리만 불명확 → 확인된 카테고리 · 기간은 대기 상태에 보존, 모르는 말(`ask_about`)만 질문.
- (10/29) "식비 알려줘" → 2번.
- (10/29 → 역할 정정) 금액 · 방향 · by/to · 카테고리는 예산 조정에서 **보조값(hints)** 일 뿐 최종 해석이 아니다. 라우터는 계산 · 나누기 · 제거하지 않는다.
- (10/29) 기존 API 의도 값 변환은 보류.
- (10/29) 결과는 `status`(ready 처리 가능 · ask 되묻기 · unsupported 미지원 · failed 실패 · cancelled 취소/거절)와 `intent`(spending · budget_adjust · unsupported · unclear · 미정이면 None)를 따로 가진다.

**[임시] 구현자 선택**
- 내부 의도: `spending`(조회·분석, 남은 예산 포함) · `budget_adjust` · `unsupported` · `unclear`. 기존 `INTENTS` 와 다른 이름으로 둬서 섞이지 않게 함.
- LLM 칸: `target`(spend/budget_left/overview) + `period`(this_month/last_month/recent) + `compare`(prev_month/category) → 코드가 비교 방식 하나로 정리 (`rules._mode`). 모순 조합은 `_conflict` 가 먼저 되묻고(8차), 나머지 조합에만 우선순위 적용: category_compare > prev_month > recent(개월 수만 말해도) > last_month > current.
- 분석 대상 × 비교 방식 표:

  | 대상 \ 방식 | current | prev_month | recent | last_month | category_compare |
  |---|---|---|---|---|---|
  | 쓴 돈 (spend) | 1 · 2 | 6 · 7 | 8 · 9 | 미지원 last_month_only | 미지원 category_compare |
  | 전반 분석 (overview) | 5 · 2 | 6 · 7 | 8 · 9 | 미지원 last_month_only | 미지원 category_compare |
  | 남은 예산 (budget_left) | 4 · 3 | 현재 기간 확인 질문 | 현재 기간 확인 질문 | 현재 기간 확인 질문 | 미지원 category_compare |

- 모르는 말 자리는 답한 코드로 앞에서부터 채움. 덜 답하면 남은 자리를 다시 묻고, 더 많이 답하거나 모르는 말로 답하면 같은 질문. 채운 뒤 같은 코드가 겹치면 처음 자리만 남김.
- 목록에서 고른 답(코드, 또는 가운뎃점 · 띄어쓰기를 무시한 화면 이름이 정확히 같을 때)은 LLM 없이 처리하고, 한 번에 한 자리만 채움. 그 밖의 답("문화여가야")은 LLM 해석.
- **남은 예산 조회**의 카테고리 되묻기 선택지는 UNCLASSIFIED 를 뺀 11개 — 예산 미지원 확정에 맞춰 구현자가 정함. 화면 이름 · 코드를 직접 입력해 UNCLASSIFIED 를 고르면 위 미지원 규칙으로 처리.
- UNCLASSIFIED 판단은 카테고리 되묻기가 끝난 뒤(모르는 말이 다 채워진 뒤), 현재 기간 질문보다 먼저.
- 현재 예산 기간 동의 시 개월 수 · 기간 · 비교 칸을 지운 요청으로 다시 규칙을 적용. 동의/거절은 LLM `confirm`(true/false) 칸, 그 밖은 불분명.
- 불명확 의도(`intent=unclear`)는 `status=ask, asking=detail` 이지만 대기 상태를 만들지 않음.
- `hints` 는 값이 있는 칸만 넣는다(없으면 칸 생략, 전부 없으면 `{}`). 모르는 카테고리는 `{"unknown": "원문"}` 그대로. `context` = `conversation_id` · `selected_from` · `last_analysis`(직전 성공 분석, 없으면 None).
- 이어받기 호환 규칙(`rules.overlay`): 기간 · 비교 · 개월은 한 묶음 — 하나라도 명시하면 직전 묶음 전체를 버림. 개월 수만 말하면 `_mode` 가 최근 N개월로 보므로 최근 추이 · 카테고리는 유지되고("3개월은?" → 9번 · 3개월), 지난달 비교 등 다른 방식이었으면 최근 N개월로 바뀜("최근 2개월로 봐줘" → 지난달 비교 해제). "이번 달은?" → 최근 추이 · 개월 제거. 기존 규칙이 이미 이렇게 동작해 코드는 바꾸지 않고 테스트로 고정(8차). 대상과 카테고리는 각각 빈 칸이면 이어받음. 남은 예산과 맞지 않는 기간 묶음은 그대로 넘겨 기존 조합 표가 확인 질문을 하게 둠.
- 이어받는 카테고리는 직전 요청 조건 그대로(조회 실패 항목 포함 — 사용자가 물은 조건이라서). 결과 참조(설명)는 성공 항목만. 확인 필요.
- 이어받는 개월 수는 사용자가 원래 말한 값(`months_requested`, 예: 6) → 3개월 제한 안내가 다시 붙음.
- (8차) 카테고리 전체를 명시하는 추출 칸 `all_categories`(true/null) 추가. 카테고리를 말하지 않음(null → 이어받음)과 전체를 명시함(true → 직전 카테고리 해제, 기간 · 비교 묶음은 위 규칙대로 유지)을 구분. "전체 기간"처럼 카테고리가 아닌 범위는 LLM 이 이 칸을 넣지 않도록 지시문에 명시 — 실제 구분 정확도는 LLM 평가 필요.
- (10차) 진행 중(보류 아님) 작업이 있을 때 LLM 이 `followup` 을 비우면 새 요청이 아닌 것으로 보고 에이전트로 보낸다(진행 중 대화를 끊지 않으려고). 다른 작업으로 전환하려면 `followup=new` 가 필요 — 실제 LLM 정확도 확인 필요.
- (10차) 라우터 서버 작업이 아직 없는 상태(`task_ref=None`, 에이전트가 묻는 중)도 진행 중 작업으로 본다. 에이전트가 `ended` 를 주면 변경 없이 해제(`ended_without_task`), 결과 기록 없음. 이 경우 재개 · 새로 시작에 서버 확인이 없다.
- (10차) `cancel_target` 질문 중 다시 "취소" → 같은 질문 반복. 질문 취소를 고르면 라우터 질문만 해제, 예산 조정을 고르면 라우터 질문은 되살리고 "취소" 원문을 에이전트로.
- (10차) LLM 이 감지한 취소(`followup=cancel`)는 대기 질문이 있으면 그 질문을 취소한다(가장 최근 질문). 코드 취소 단어만 대상이 둘일 때 묻는다.
- (10차) 소비 분석이 아닌 요청(범위 밖 등)으로 바뀔 때는 조정 작업을 보류하지 않는다(소비 분석 전환만 보류).
- (11차) 에이전트가 프로토콜과 달리 기존 작업 취소 확인 전에 새 작업(`task_ref` 가 다름)을 만들면: 기존 작업이 진행 중이면 `previous_task_active`(교체 안 함). 기존 작업이 끝났다고 확인되면 그 결과를 기록하고 새 작업을 받아들이되, 보관한 새 요청으로 다시 start 하지 않는다(중복 방지 — 보관 요청은 버려짐, 확인 필요).
- (11차) `record_budget_task` 에서 서버가 확인한 기존 작업 종료는 기록하고, 그 뒤 새 작업 확인이 실패하면 새 작업은 받아들이지 않는다(서버가 확인한 사실만 반영).
- 이어받을 분석이 없을 때 설명 요청("왜 늘었어?")은 대기 상태 없이 `asking=detail` 로만 묻는다(조회 조건 질문 `scope` 와 달리 이어서 완성할 요청이 없어서).
- 설명 요청이 실패 항목을 가리키면 `unsupported/reference_not_available`.
- (8차) 모순 기준(`rules._conflict`): 지난달 비교 + 최근 N개월(또는 개월 수) · 지난달 단독, 이번 달 · 지난달 + 개월 수, 전체 + 특정 카테고리. 새 요청에도 같은 규칙 적용(이전에는 지난달 비교가 조용히 우선했음). 그 밖의 조합은 기존 우선순위 그대로.
- (8차) 모순 검사는 직전 분석을 이어받은 **뒤** 한다 → 대기 상태에 이어받은 카테고리 등이 남아 답을 받은 뒤에도 유지된다.
- (8차) "그 전에 본 거"처럼 직전보다 이전 분석을 가리키면 LLM 이 `refers_to=unclear` 로 답하게 지시 → 직전 분석에 연결하지 않고 조건을 되묻는다(`reason=reference_unclear`). 이전 분석은 저장하지 않으므로 "직전 것만 기억해요" 같은 전용 안내는 없음.
- LLM 에는 직전 분석 **조건만**(`previous`) 보내고 결과 · 금액 · facts 는 보내지 않음.
- 예산 조정 전달 `context.last_analysis` 에는 직전 분석 기록 전체(서버 facts 포함)를 그대로 넣음 — 서버 안 전달이라서. 에이전트가 쓸 모양은 통합 때 정함.
- 취소: "취소" · "됐어" · "그만" 은 대기 중일 때 LLM 없이 코드가 처리. 그 밖의 취소 표현은 LLM `followup: cancel`.
- 대기 중 LLM 이 `followup` 을 비워 보내면 불분명으로 보고 같은 질문을 다시 함.
- 대기 중 LLM 에는 묻는 항목 · 선택지만 보내고, 저장한 원문 · 이전 조건은 보내지 않음.
- 형식 검사 → 해석 실패(`failed`) + 대기 상태 · 직전 분석 유지: JSON 아님 · 모르는 칸 · 의도 누락/허용 밖 값 · 정수 아닌 개월 수 · `confirm` · `explain` · `all_categories` 가 참/거짓 아님 · `refers_to` 허용 밖. 보조값 칸(categories · direction · amount · amount_mode — 코드표 밖 카테고리 코드, `{"unknown"}` 모양 오류, 정수 아닌 금액, **금액 없이 by/to** 포함)이 틀리면: 의도가 예산 조정 **하나뿐**이면 hints 를 통째로 비우고 원문 전달, 그 밖(소비 분석 · 복합 의도 · 의도 없는 후속 답)은 실패. 후속 답에 대한 예외는 없음. LLM 호출 오류 종류는 SDK 미정이라 모두 실패로 묶음.
- `run_items` 의 실패 표시는 예외 이름만 (메시지에 개인 정보가 섞일 수 있어서).
- `Decision.interpretation` 은 이번 메시지의 LLM 해석. 후속 답변이면 합친 요청이 아니라 답변 해석.
- 실패 이유 코드(`reason`)는 문제 칸 이름 그대로 (`months`, `call` …) — 문구는 정하지 않음.

**[폐기] 현행 규칙 아님 — 기록용**
- 완료 후 조건 상속 없음 → 10/29 1단계(7차)에서 폐기. 현행: 직전 성공 분석을 호환되는 조건만 이어받음. 테스트 `test_no_inheritance_after_done` → `test_follow_up_without_previous_analysis_asks_scope` 로 교체(직전 분석이 없으면 기본값으로 채우지 않고 묻는 쪽으로 기대 결과 변경).
- 라우터의 예산 조정 by/to 되묻기(`asking=amount_mode`, 3차) → 10/29 역할 정정(6차)으로 폐기. 현행: by/to 는 hints 보조값, 확인은 예산 조정 에이전트.
- 금액 없이 by/to 만 있는 후속 답 예외(4차) → 역할 정정으로 폐기. 현행: 위 형식 검사 규칙(보조값 오류).
- 예산 조정 요청의 카테고리 되묻기 · UNCLASSIFIED 제외(`skipped`) · 11개 선택지(3~5차) → 역할 정정으로 폐기. 현행: 남은 예산 조회에만 적용, 예산 조정은 hints 그대로 전달.

**[미정] 질문 필요**
- 내부 의도 ↔ 기존 `budget`/`big`/`out`/`unknown` 대응 (보류): 문서(chat.md)상 `budget`=예산 조정, `out`=범위 밖, `unknown`=불명확은 확인됨. 소비 분석 범위의 미지원(지난달 단독 · 카테고리 비교) · 되묻기(`ask`) · 거절(`cancelled`) 을 어느 기존 의도로 보낼지 미확인. `big`(큰 지출 상담)은 내부 의도에 없음.
- 대화 상태(대기 · 직전 분석) 저장 위치 · 만료 정책 (통합 전 결정, 위 2번).

## 4. 답변 문구 원칙 (10/29 재사용 구조 검토 후 정리, 코드 변경 없음)

**원칙**
- 질문 · 사유 · 공통 안내는 재사용 가능한 문구로 관리한다 (`[사유 문구] + [질문 줄기] + [선택지]` 조합).
- 카테고리 · 숫자 · 예문마다 별도 템플릿을 만들지 않는다. 카테고리 이름 · 서버 수치 · 기간은 자리에 넣는다.
- 여러 항목 결과를 한 답변으로 묶을 때 공통 안내(기간 제한 · 확인 필요 제외 · 일부 실패 · 자료 부족)는 한 번만 출력한다.
- 재사용 과정에서도 분석 유형별 필수 정보(기간 · 대상 · 서버 수치 · 비교 기간 · 남은 예산의 예산 기간 등)는 빠뜨리지 않는다. 소비 기간(달력월)과 예산 기간(월급 주기) 명칭은 구분한다.
- 조건 확인 질문(`asking=scope`)은 실제로 부족하거나 충돌한 항목을 묻는다. 모든 scope 질문을 기간 질문 하나로 고정하지 않는다.
  - 현재 구현: scope 질문은 `reason`(no_previous_analysis · reference_unclear · conflicting_conditions)과 `ask_about`(카테고리)만 담고, **충돌한 칸이 무엇인지는 담지 않는다** → 통합 전 검토.
- 현재 구현된 문장 수와 앞으로 필요한 문구 수를 구분한다. 제안 개수는 확정값이 아니다.
  - 현재 구현: 옛 `chat/replies.py` 6문장(+ 선택지 라벨 2개). router_core 의 사용자용 문장 0개.
  - 제안(미확정): 질문 줄기 약 6 · 사유 문구 약 11 · 공통 안내 약 4 · 미지원 줄기 1 · 취소 1 · 분석 본문 약 9 · 설명 실패 대체 약 4 (옛 문장 2개 재사용).

**통합 전 검토**
- 사유 코드 정리: 예) 소비 미지원 `last_month_only` 와 예산 질문 사유 `last_month` 처럼 같은 개념에 이름이 둘. `reason` 이 상태(unsupported · ask · cancelled · failed)마다 뜻이 다름.
- `Decision` → 답변 문장 연결 위치(`chat/replies.py`, 공통 파일이라 승인 필요). 지금 `replies.reply()` 는 옛 `(intent, slots)` 만 받는다.

**지원 요청 + 범위 밖 요청이 섞였을 때 — 현재 동작 기록 (확정 정책 아님)**
- 예산 조정 + 범위 밖 → 예산 조정만 처리 (`rules.decide` 의도 우선순위).
- 소비 분석 + 범위 밖 → 소비 분석만 처리, 범위 밖 부분은 안내 없이 무시 (`rules.decide` 의도 우선순위. 지금까지 기록 없던 동작).
- 둘 다 범위 밖 부분을 알리지 않는다. 안내를 붙일지 · 되물을지는 정책 결정 필요.


## 5. 실제 LLM 해석 평가 (12차, 도구만 준비 — 실제 호출 · 성능 측정 아직 없음)

**도구** (`scripts/router_eval/`, router_core · 공통 파일 · 의존성 변경 없음)
- `openai_rest.py` — 표준 라이브러리 REST 어댑터(`ResponsesClient.complete`). `POST /v1/responses`, `store: false`, 출력 토큰 상한 · 시간 제한, 재시도 0, 다른 모델 전환 없음. 생성 요청은 보내기 전에 세고 실패도 상한에 포함. 온도 · 추론 강도는 지원 여부를 확인 못 해 보내지 않음.
- `run.py` — `route()` 에 complete 로만 주입(코드가 처리하는 입력은 호출 0회, 추출 · 결정 채점에 한 번의 응답만 씀). 기본 dry-run(네트워크 없음, 사례의 정답 추출값으로 사례 ↔ 코드 정합성 점검). live 는 `--live` + `--max-calls` + `ROUTER_EVAL_LIVE=1` 모두 필요. 결과는 `--output-dir`(저장소 밖만)에 meta.json · results.jsonl. 키 · 헤더 기록 안 함. `--structured` 로 Structured Outputs(json_schema strict) 선택 가능 — 받은 뒤 기존 `parse` 형식 · 의미 검사는 그대로.
- `cases.json` (버전 2026-10-29.1) — 단일 입력 40개(dev 28 · holdout 12, 정책 미정 1개는 분모 제외) + 연속 대화 3개(앞 턴의 실제 처리 결과로 상태를 이어감, 소비 조회 · 에이전트 · 서버 결과는 가짜).
- `test_router_eval.py` — 오프라인 검증 10개(dry-run 무호출, 정답 응답, 틀린 추출값, 형식 오류, HTTP 실패, 호출 상한, 원인 후보 기록, 스키마).

**채점 규칙**
- 추출값과 최종 결정 · 상태를 따로 채점. 사례마다 `must`(명시 칸) · `allow`(의미상 같은 허용값) · `observe`(예산 조정 보조값 — 기록만) 를 정하고, **나열하지 않은 칸은 비어 있어야 한다**(말하지 않은 조건 채우기 · 이전 조건 복사 검출). `unclear` 는 채점 안 함.
- 결과 종류: ok · llm(추출 틀림) · format · route(추출 맞는데 결정 · 상태 틀림) · unexpected_calls · call_failed · cap_skipped.
- route 실패는 같은 이전 상태에서 정답 추출값으로 다시 돌려 `gold_replay_ok` 와 원인 후보(코드 규칙 · 기대값 · 상태 구성 · 평가 도구)만 기록 — 코드 오류로 확정하지 않음.
- 사용량을 모르는 실패 시도는 `unknown_usage_attempts` 로 세고 비용을 완전한 값으로 표시하지 않음(`cost_complete: false`).
- dev 로만 지시문을 고치고, holdout 은 `--split holdout` 을 명시할 때만 실행. 결과에 지시문 해시 · 사례 해시 기록.

**모델 근거 (공식 문서 검색 결과, 2026-10-29)** — developers.openai.com 모델 페이지 · 가격표: `gpt-6-luna`, `v1/responses`, Structured Outputs 지원, 입력 $0.10 · 출력 $0.50 / 100만 토큰. 페이지 본문 직접 열람은 403 이라 온도 · 추론 강도 · 최대 출력은 미확인. **이 키로 실제 사용 가능한지(모델 조회 API)는 아직 확인 안 함.**

**확인 · 결정 필요**
- ~~첫 실제 연결 점검 제안(아직 실행 안 함)~~ → 13차에 실행(아래). dev 전체는 28회, holdout 12회, 연속 대화 9회(코드 처리 턴 제외).
- (13차 정리) 단위 테스트 가짜 응답 "일단 이번 달 소비부터 보여줘" 를 정책대로 `target=overview` → 5번으로 맞춤(보류 · 전환 검증은 그대로). router_core 규칙 · 프롬프트는 변경 없음.
- 정책 미정 사례(d28: 소비 분석 + 범위 밖 혼합)는 현재 동작만 기록.
- 형식 오류는 실제 모델에 일부러 낼 수 없어 단위 테스트(가짜 LLM)가 담당. 실제 평가는 자연히 생긴 형식 실패율만 센다.

**2단계 남은 항목 (통합 전 확인, 연결 안 함)**
- 취소 대신 저장 완료 등으로 끝났을 때 보관한 새 요청(`unstarted_request`) 처리 정책.
- `mode=cancel` · 사용자 "취소" 원문(`continue`) 에 대한 에이전트 계약.
- 통합 계층 호출 순서 `record_budget_task` → `pop_queued_start`.
- 실제 영속 저장에서 같은 대화 동시 요청 잠금 · 중복 start 방지.

**13차 실제 연결 점검 (2026-10-29, 생성 요청 5회 — 장부 limit 5 / used 5)**
- 설정(고정, 자동 변경 없음): `gpt-6-luna` · `POST /v1/responses` · `reasoning.effort=low` · `max_output_tokens=2000`(추론 토큰 포함) · Structured Outputs(json_schema strict) · `store=false` · 시간 제한 30초 · 재시도 0.
  - low 를 고른 이유: 라우터는 짧은 분류 · 추출이라 높은 추론이 필요 없고 응답 시간 · 비용을 줄이려고. none 은 맥락 판단(refers_to 등)에 불리할 수 있어 제외. 출력 2000 은 추론 + JSON(약 100~200) 여유분.
  - 예상 최대 출력 비용: 요청당 2000 × $0.50/100만 = $0.001, 5회 최대 $0.005 (+ 입력 약 1.5천 토큰 × 5).
- 사례: d01 · d08 · d11 · d13 · d16 (기본 조회 · 6개월 원본 · 복수 + 모르는 카테고리 · 예산 조정 전달 · 맥락 후속). holdout 실행 안 함.
- 결과: 인증 · 요청 형식 · Structured Outputs · 응답 파싱 · 사용량 기록 모두 정상(incomplete · refusal · 출력 없음 · 형식 오류 0). 추출 4/5, 최종 결정 5/5.
  - d16 "그럼 교통비는?": `target=overview` 를 채움(사용자가 말하지 않은 조건 — 직전 분석의 대상과 같은 값을 복사). 결정은 이어받기 결과가 같아 우연히 맞음. **추출 오류로 기록, 튜닝은 아직 안 함.**
- 사용량: 입력 7,505 · 출력 602 토큰(추론 토큰 포함), 추정 $0.001052(공식 가격표 기준 계산, 청구서 대조 안 함). 응답 시간 중앙값 3.4초 · 최대 3.7초.
- 결과 파일: 저장소 밖 scratchpad(`router-eval-live/`). 5건으로 정확도 · 서비스 품질을 검증한 것이 아님.

**14차 개발용 28개 기준 측정 (2026-10-29)**
- 설정: 13차와 동일(gpt-6-luna · effort low · 출력 2000 · Structured Outputs strict · 재시도 0). 지시문 해시 `6a09685c…`, 사례 버전 2026-10-29.1.
- 장부(새로 만듦): `<scratchpad>/router-eval-dev28/ledger.json` — limit 28 / used 28. 13차 장부(`router-eval-live/ledger.json`, 5/5)는 그대로.
- 결과 파일: `<scratchpad>/router-eval-dev28/*-meta.json` · `*-results.jsonl` (저장소 밖). `<scratchpad>` = 이 세션의 Claude scratchpad 디렉터리.
- 채점 27개(정책 미정 d28 제외), 미실행 0, 호출 실패 0. 형식 100%(28/28) · **추출 19/27(70.4%)** · **최종 결정 24/27(88.9%)** · 상태 27/27(100%).
- 사용량: 입력 42,069 · 출력 3,898 토큰(추론 0~145/요청), 추정 $0.006156(공식 단가 계산), 응답 시간 중앙값 2.6초 · 최대 5.0초.
- 실패 8건과 원인 후보(확정 아님):
  - 결정까지 틀림 3건 — d04 "생활비 예산" → `{"unknown":"생활비"}` 로 카테고리 되묻기(프롬프트: '생활비 · 전체 예산 = 카테고리 없음' 설명 없음) · d25 "소비부터 보여줘" → target=spend → 1번(프롬프트: '보여줘' 가 overview 설명에 없음, 정책 1 · 5 선택 자체도 재확인 후보) · d22 직전 분석 없는 "그럼 교통비는?" → refers_to=unclear(되묻기는 같고 사유 코드만 다름. 프롬프트 정의상 previous 가 없으면 previous 를 고를 근거가 없어 **기대값 · 정책 후보**).
  - 추출만 틀림 5건(결정은 맞음) — d16 · d18 후속 질문에 말하지 않은 target=overview 채움(**반복: 13차 d16 포함 3회, 맥락 후속 4건 중 2건**, 직전 대상과 같은 값이라 결정이 우연히 맞음 — 직전 대상이 spend 였다면 1 · 5번이 바뀔 위험) · d24 "응" 에 confirm=true(묻지 않은 확인값, 지금 코드는 budget_current 질문에서만 confirm 을 쓰므로 무시됨) · d09 "알려줘" 에 target=spend(기대 허용값은 overview · null, 최근 추이라 결정 무관 — 프롬프트 준수 · 허용값 재검토 후보) · d26 보류 작업 재개에 followup=answer(허용값 null · new — 보류 작업에 대한 말이라 answer 도 해석 가능, **기대값 후보**).
  - 코드 규칙 오류로 볼 근거는 없음: 결정 실패 3건 모두 추출값이 기대와 달랐고, 정답 추출값으로는 dry-run 에서 모두 통과.
- 정책 미정 d28: intents=[spending, unsupported] 로 둘 다 추출, 현재 코드는 소비 분석만 처리(범위 밖 부분 안내 없음). 기록만.
- 개선 제안(미적용): 프롬프트에 ① 후속 질문이라도 문장에 없는 칸은 null(서버가 이어받음) 예시 ② '생활비 · 전체 예산' = 카테고리 없음 ③ '보여줘' 와 target 대응 ④ confirm 은 budget_current 질문에만 ⑤ previous 가 없을 때 refers_to 기준. 평가 쪽 결정 필요: d22 · d26 · d09 기대값. 가설 확인 후보: Structured Outputs strict(모든 칸 필수)가 칸 채우기를 늘리는지 — 일반 출력과 소수 사례 비교.

**15차 평가 기준 정리 · 지시문 개선 · 개발용 재평가 (2026-10-29)**
- 평가 기준(사례 버전 2026-10-29.2, `cases.json` 의 `changes` 에 이유 기록): d22 직전 분석 없는 "그럼 교통비는?" → `refers_to=unclear` · 사유 `reference_unclear` / d26 보류 작업 명시 재개 → `followup=answer` 도 허용(핵심은 resume=true + resume 전달). 유지: d04 전체 생활비 4번 · d09 '알려줘'=overview(spend 불허) · d25 '소비 보여줘'=5번. d28 정책 미정 계속 분모 제외.
- 지시문 원칙(`llm.py`, 해시 `77f7fc85…`): ① 이번 문장에서 말한 조건만, 말하지 않은 칸은 null ② previous 는 refers_to 판단에만, 조건 복사 금지 ③ 후속 질문은 바꾼 것만(카테고리 · 전체 · 기간) ④ '생활비' · '전체' 같은 범위 말은 카테고리 아님, 그 안의 특정 카테고리는 구분 ⑤ 금액 하나를 물으면 spend, 보여 달라 · 알려 달라면 overview ⑥ previous 가 없는데 이어받아야 뜻이 통하면 unclear ⑦ confirm 은 budget_current 질문에서만, 진행 중 조정의 "응"은 followup=answer 만.
  - 과적합 방지: 예시는 평가 사례(개발 · 확인 · 연속 대화)와 같은 문장을 쓰지 않음 — 원래 지시문에 있던 평가 문장 예시 3개("그럼 교통비는?" · "이번 달은?" · "왜 늘었어?")도 다른 표현으로 교체(지시문 속 따옴표 문구와 사례 문장 겹침 0 확인). 확인용 사례는 수정 근거로 쓰지 않음. 라우터 규칙 변경 없음.
- 도구: `run.py --replay <results.jsonl> [--cases-file <사례 파일>]` — 저장 응답으로 API 없이 재채점. 옛 사례 파일 보관: `<scratchpad>/cases-2026-10-29.1.json`(해시 1a83503a…).
- 점수(채점 27개, 미실행 0, 호출 실패 0):
  | 구분 | 추출 | 최종 결정 | 상태 |
  |---|---|---|---|
  | A. 기준 응답 · 옛 기준 (14차 재현, replay) | 19/27 (70.4%) | 24/27 (88.9%) | 27/27 |
  | B. 기준 응답 · 새 기준 (replay, 기준 변경 효과만) | 21/27 (77.8%) | 25/27 (92.6%) | 27/27 |
  | C. 개선 지시문 · 새 기준 (live 28회) | 26/27 (96.3%) | 26/27 (96.3%) | 27/27 |
  - A→B (기준 변경): d22 · d26 — 모델 응답은 그대로.
  - B→C (모델 응답 변화): 고쳐짐 d04 · d09 · d16 · d18 · d24 · d25 / **새로 틀림 d20** "이번 달 총소비 알려줘" → target=overview → 5번(기대 1번). 원인 후보: 새 원칙 ⑤의 '알려 달라 → overview' 가 '총소비'(총액)보다 우선됨 — 프롬프트 규칙 충돌.
  - 여전히 틀린 사례 없음(B 에서 남은 실패는 모두 C 에서 고쳐짐).
  - 개발용 사례에서 기존 오류가 고쳐진 것이지 새 질문에 대한 성능 향상으로 볼 수 없음. 각 1회 실행이라 실행 간 변동은 측정 안 함.
- 사용량(C): 입력 53,465(지시문이 길어져 요청당 약 +400) · 출력 4,177 토큰, 추정 $0.007435, 응답 시간 중앙값 2.4초 · 최대 3.5초. 장부 `<scratchpad>/router-eval-dev28-v2/ledger.json` 28/28. 이전 장부 2개(5/5, 28/28) 보존. 결과 `<scratchpad>/router-eval-dev28-v2/`.
- d28(정책 미정): 이번에도 intents=[spending, unsupported] 추출, 현재 동작은 소비 분석만.
- 다음(제안): d20 원칙 충돌(총액 표현 vs '알려줘')을 일반 원칙으로 한 번 정리 → 개발용 1회 재평가 → **지시문 고정(해시 기록)** → 확인용 12개 1회 + 연속 대화(기존 3개 + 새로 쓴 시나리오) 별도 평가. 고정 뒤 확인용 결과로는 튜닝하지 않고, 튜닝이 필요하면 쓴 사례는 개발용으로 옮기고 확인용을 새로 쓴다.

**16차 target 해석 우선순위 수정 · 개발용 재평가 (2026-10-29)**
- 지시문 원칙 변경(해시 `6a3bb2a3…`): target 은 요청 말투("알려줘" · "보여줘")가 아니라 분석 대상으로 정한다 — 총액 · 합계 · 얼마 썼는지 = spend, 남은 예산 = budget_left, 전반 분석 · 평가 = overview. 대상을 따로 말하지 않은 독립 소비 조회는 overview(기존 정책). 후속 질문에서 대상을 말하지 않으면 null(overview 로 채우지 않음). 예시는 평가에 없는 '병원비' 표현만 사용. 사례 ID · 문장 분기 없음, 기대값 · 허용값 변경 없음(사례 v2 그대로).
- 테스트: chat 83 (대비 회귀 2개 추가 — 같은 '알려줘 · 보여줘'라도 총액 1 · 2번 / 잔액 3 · 4번 / 전반 2 · 5번, 후속 질문 target 비면 직전 잔액 대상 이어받기) · 평가 도구 17 (지시문 유출 검사 추가).
- 개발용 재평가(live 28회, 장부 `<scratchpad>/router-eval-dev28-v3/ledger.json` 28/28, 이전 장부 3개 보존): 채점 27 · 미실행 0 · 호출 실패 0. **추출 26/27(96.3%) · 최종 결정 27/27(100%) · 상태 27/27.** 입력 56,125 · 출력 4,269 토큰, 추정 $0.007747, 응답 시간 중앙값 2.4초 · 최대 4.5초.
  - v2 대비: d20 "이번 달 총소비 알려줘" 고쳐짐(spend → 1번). **새로 틀림 d04** "생활비 예산 얼마 남았어?" — `all_categories=true` 를 추가로 채움(기대: 비어 있음). 결정은 4번으로 같음. 원인 후보: 기대값 · 정책(생활비 전체 = 카테고리 전체라는 해석도 성립) 또는 지시문(all_categories 설명의 "전체 예산은?" 예시가 독립 요청에도 적용됨). **기대값은 바꾸지 않고 결정 필요로 남김.**
  - 후속 질문 target: d16 · d17 · d18 · d19 모두 null(말하지 않은 target 채우기 사라짐). d21 "예산은?" 은 사용자가 말한 대상이라 budget_left(정상).
  - d28(정책 미정): intents=[spending, unsupported] 그대로.
  - 각 1회 실행. 개발용 사례의 기존 오류가 줄었다는 뜻이며 새 질문 성능으로 보지 않음.
- **지시문 · 평가 문장 겹침 이력 (확인용을 새 사례로 취급하지 않는다)**
  - 현재 지시문 본문에 그대로 들어 있는 평가 문장(모델 호출 턴): "전체는?"(d18 · s1-3, "그럼 전체는?" 안 — 8차부터), "예산은?"(d21, "전체 예산은?" 안 — 8차부터), "응"(d24, followup 예시 — 10차부터). 유출 검사 테스트에 알려진 겹침으로 등록, 새 겹침만 막음.
  - 현재 지시문과 부분 겹침: h10 "그 전에 본 거 다시 보여줘" ↔ "그 전에 본 거"(8차부터) · d26 · s3-4 "아까 예산 조정 이어서 하자" ↔ "아까 예산 조정 계속하자"(10차부터) · h11 "그렇게 진행해줘" ↔ "그렇게 해줘"(10차부터).
  - 과거 지시문(1~14차)에 그대로 있었던 평가 문장: "그럼 교통비는?"(d16 · d22 · s1-2) · "이번 달은?"(d17) · **"왜 늘었어?"(d23 · 확인용 h09)** · "데이트비랑 식비"(d11 · s2-1 과 유사) — 15차에 교체. 13 · 14차 측정은 이 지시문으로 했다.
  - 과정상 참고: 확인용 사례는 지시문 작성자와 같은 작성자가 같은 시점에 썼고, 15차 겹침 점검 때 확인용 문장도 대조해 "왜 늘었어?" 예시를 교체했다(확인용이 지시문 수정에 영향). 확인용은 아직 한 번도 실행하지 않았지만 **h07 · h08 · h09 · h10 · h11 은 표현이 지시문 예시와 겹쳤던 이력이 있어 독립 평가로 보기 어렵다.**
- 다음 연속 대화 제안(아직 작성 · 실행 안 함, 지시문을 보지 않은 사람이 쓰는 것을 권장): ① 비교 → 카테고리 되묻기 → 기간 변경 → 잔액 전환 확인 질문 ② 분석 → 예산 조정 → 진행 중 다른 카테고리 총액 질문(보류) → 재개 시 저장 중 상태 ③ 보류 작업 + 개월 수 질문 대기 중 "취소" → 취소 대상 질문 → 질문만 취소 → 재개 ④ 직전 분석 없음 + 모호한 지시어 → 조건 되묻기 → 답 → 설명 요청 ⑤ 복합 의도 → 소비 선택 → 예산 조정 → 에이전트 취소 확인 → "바뀐 예산으로" 분석(저장 안 됨 안내 + 범위 되묻기).
- 고정 가능 여부: 최종 결정 27/27 · 남은 추출 오류 1건(결정 영향 없음). **고정 전 결정 필요**: ① d04 기대값(all_categories=true 허용 여부) ② 지시문의 평가 문장 겹침(위 목록)을 평가와 무관한 표현으로 바꾼 뒤 개발용 1회 재측정하고 고정할지, 지금 고정하고 겹친 사례를 별도 표시할지.

**17차 지시문 고정 · 신규 검증 (2026-10-29)**
- 지시문 고정: 해시 `6a3bb2a3397193c0…`. live 실행은 `--prompt-sha 6a3bb2a3` 로 고정값과 다르면 실행하지 않는다. 일반 표현("응" · "전체는?")의 겹침만 없애려는 수정은 하지 않음 — 겹침 검사는 참고 자료(합격 기준 아님).
- 기준 정리(사례 v2026-10-29.3): d04 만 all_categories null · true 허용(다른 생활비 표현 · 후속 질문으로 확대 안 함). v3 저장 응답 재채점(API 없음): 추출 26/27 → **27/27 — 채점 기준 변경 효과**(모델 응답 동일). 결정 · 상태는 27/27 그대로.
- 사례 구분(`cases.json` 의 `splits`): dev 28(개선에 사용) · holdout 12(**참고 · 회귀용**, 오염 이력으로 독립 평가 아님, 이번 미실행) · new 12 + 연속 대화 s4~s8(지시문 고정 뒤 작성, 실행 전 기대값 고정 — 사례 파일 해시 `cffc9d75…`, 사본 `<scratchpad>/cases-2026-10-29.3-frozen.json`). **신규 사례도 지시문 작성자가 썼다는 한계가 있다.**
- 도구 보완: `--split new`, 연속 대화도 split 별 실행, 턴 직전 가짜 서버 상태 변경(`before`), 첫 오류 이후 실패 표시(`after_first_error` — 앞 오류가 만든 상태 때문인지는 확정 못 함), `--prompt-sha`. 테스트: 평가 도구 19 · chat 83 통과. dry-run(정답 추출값): 신규 단일 12/12 · 연속 대화 5/5(22턴) 정합.
- 실행: 장부 `<scratchpad>/router-eval-new42/ledger.json` limit 42 / used 32(단일 11 + 연속 21, 코드 처리 턴 2개는 호출 0), 호출 실패 0. 결과 같은 폴더.
- 단일 질문(채점 11 = 12 − 정책 미정 n06, 그중 모델 호출 10): 형식 10/10 · **추출 9/10** · **최종 결정 10/11** · 상태 11/11.
  - n10 "남은 예산은 얼마야?"(직전: 최근 2개월 식비) → refers_to=none(독립 요청으로 봄) → 4번(전체 잔액)으로 바로 답함. 기대: previous → 식비 잔액 현재 예산 기간 확인 질문. **범위 변경 판단 오류.** 원인 후보: LLM 해석(맥락 후속 판단) 또는 기대값 · 정책 모호(문장만으로는 전체 잔액 질문으로도 완결) — 독립 · 후속 경계 정책 결정 필요.
  - n06(정책 미정, "식비 빼고 나머지") → categories=[FOOD] + all_categories=true + unclear 설명 → 현재 코드는 모순으로 보고 조건 되묻기. 기록만.
- 연속 대화(22턴): 턴 결정 22/22 · 상태 22/22 · 추출 포함 엄격 기준 20/22, 시나리오 4/5(s8 만 추출 기준 실패, 결정 · 상태 기준으로는 5/5).
  - s8 2턴 "소비 먼저"(의도 선택 답) · 4턴 "아니 그냥 그만둘래"(진행 중 조정 취소) — 후속 답에 intents 를 채움(4턴은 refers_to=none 도). 결정은 둘 다 맞음(선택 반영 · 에이전트로 continue). 4턴은 도구가 after_first_error 로 표시했지만 2턴이 만든 상태 때문이 아니라 **같은 유형의 독립 오류**. 원인 후보: 지시문이 후속 답의 intents 를 비우라고 정하지 않음 + 기대값 엄격도(정책 확인 필요).
  - 강조 항목: 범위 변경 — n10 오류, s4(모르는 카테고리 → 선택 → 기간 변경 → 잔액 확인 → 동의) 성공 / 취소 — s6(취소 대상 질문 → 질문만 취소, 작업 유지) · s8(에이전트 전달 → 서버 canceled 확인 뒤 해제) 성공 / 재개 — s5(서버 saving 중 재개, 상태 saving 유지) · s6 성공 / 저장 성공 판단 — s8 5턴 "저장 안 됨" 안내 · 최신 예산 재조회 표시, 성공 오판 없음.
- 사용량: 32회, 입력 64,661 · 출력 5,247 토큰, 추정 $0.00909(공식 단가 계산), 응답 시간 중앙값 2.7초 · 최대 5.8초.
- 남은 결정: ① 직전 분석 뒤 완결된 문장("남은 예산은 얼마야?")의 독립 · 후속 경계 ② 후속 답(의도 선택 · 진행 중 조정 답)에서 intents 를 채우는 것을 허용할지(라우터 결정에는 영향 없음).

## 6. 소비 분석 fixture 검증 (18차, 2026-10-29)

가상 사회초년생 3명(A 윤서 · B 민재 · C 지우), 거래 설계 414행. 사용법은 `backend/spending_fixture/README.md`.
입력 자료: 사용자와 정리한 설계 명세 04 · 05 사본(`spending_fixture/data/`, 해시 고정). 06 목록의 코드 21개는 현재 작업 트리와 해시가 같았다(코드 변경 영향 없음).
**fixture 관계 검증과 소비 분석 계층 검증은 다르다.** 이번에 확인한 것은 기존 집계 함수까지이고, 1~9번 분석 계층은 없다.

**쓰는 API · 함수 (실제 DB 테스트로 확인)**
- `finance.api.listed_transactions` — 사용자 필터 · 같은 구매(SAME_PURCHASE) 계좌 행 제외. 저축 · 내 계좌 이체 · 카드값은 목록 자체에 없음(LIST_TYPES).
- `finance.api.transaction_item` — 취소 차감(부분 120,000 · 전액 0), 비소비는 category · funding None, 고정비 = PaymentMatch → PaymentDue(recurring).
- `finance.spending.spending_summary` — 전체 / `living_only=True`(고정비 제외). 월별(7 · 8 · 9 · 10/1~29) · 9/1~29 · 현재 주기 생활비가 05 정답과 전부 일치(A · B · C).
- `budgets.home.cycle_dates` — 순수 날짜 함수. A 10/25~11/25 · B 10/10~11/10 · C 10/20~11/20, 시작 전날 · 시작일 경계 확인.
- `budgets.overview.budget_data` — 생활비 잔액 · 초과 계산식 확인용으로만(테스트 DB, 롤백). B: remaining 0 · over 62,000 · covered 62,000 · spare_remaining 138,000 · 식비 배정 400,000/사용 532,000 일치. C: 초안만 있어 budget None(초안을 예산으로 답하지 않음).
  **그대로 쓸 수 없음**: validated 를 approved 보다 먼저 고른다(A → v2 식비 450,000). 주기 준비(ensure_cycle) · select_for_update 쓰기 경로가 있다. → 테스트 `test_a_category_budget_uses_approved_v1` 를 expectedFailure(미구현 표시)로 둠.

**필요한 것 (부족 목록 — 함수 · 입력 · 출력 초안, 구현 안 함)**
- (19차 구현됨 → 7번) 승인 예산 읽기 전용 조회: `approved_budget(user, on_date) → {"state": "approved" | "none" | "error", "budget_id", "version_no", "cycle": {starts_on, ends_before}, "living": {코드: 배정액}, "living_total", "spare"}`. status=approved 만, 주기 생성 · 잠금 · 쓰기 없음, "없음"과 "조회 실패" 구분. validated · draft 는 절대 고르지 않음.
- (19차 구현됨 → 7번, 이름 · 모양 바뀜) 분석 기간 계산: `analysis_period(type, today, payday, months_applied) → {"current": (시작, 끝 미만), "compare": (…) | None, "months": [(…)…]}` — 달력월(10/1~29), 지난달 같은 날짜(9/1~29), 완료된 N개월(7 · 8 · 9월, 진행 중 10월 제외), 예산 주기(급여일 이상 ~ 다음 급여일 미만).
- (20차 구현됨 → 8번, 입력 Period 로 바뀜) 수집 완전성 판정: `coverage(user, start, end) → {"state": "complete" | "partial" | "none", "missing": [{"asset", "from", "to"}], "checked_at"}` — 선택된 계좌(bank_sync, 연결 단위) · 카드(card_approval_list, 카드 FK)별 성공 covered 범위로 판정. 합계 0원은 수집 성공 증명이 아님. 카드 청구서 조회 성공을 승인내역 성공으로 대신 쓰지 않음.
- 1~9유형 결과 구성(총액 · 카테고리 · 잔액 · 전반 · 지난달 대비 · 3개월 추이) + `record_analysis` 로 넘길 `period` · `as_of` 어댑터. 순위 분모는 전체 소비, UNCLASSIFIED 순위 제외(설계 02).
- 미정 계약(임의 확정 안 함): 카테고리 음수 잔액 표시(B 식비 −132,000), 비율 · 증감률 반올림, C 부분 수집 안내 문구 · 응답 칸. (이전 기간 0원일 때 증감률은 미정 아님 — `docs/api/chat.md` `prevDiffRate`: 비교 기간이 0원이면 `null`, 19차 정정)
- 변형 fixture(설계 02 5번: B 승인 교체, C 승인만 추가 · 카드 성공 조회 추가, 기간 경계, 0원 비교, 동률 1위) — 이번에 만들지 않음.
- ML 입력 매핑(처분가능소득 · 모델 정의 고정비 · 기타 제외 생활비) — 후속. DB 의 disposable 칸은 null.

**임시 결정 (seed 도구)**
- [임시] 별도 DB `money_cushion_router_fixture`(자동 테스트 DB `test_money_cushion_router_fixture`)는 `spending_fixture/settings.py` 로만 지정. 공통 설정을 상속하고 DB 이름만 바꿈. localhost 가 아니면 설정 단계에서, fixture DB 이름이 아니면 쓰기 단계에서 멈춤.
- [임시] 모든 PK 는 UUID5(고정 이름공간 + plan_id 등), 시각은 날짜의 09:00 / 23:00 KST 고정. auto_now(_add) 칸은 저장 뒤 고정 시각으로 다시 씀. `--apply` = 이 fixture 사용자만 지우고 다시 넣기(한 트랜잭션) → 두 번 실행 결과 동일 확인.
- [임시] 비밀번호는 사용할 수 없는 고정값(`!` 시작), 이메일 `fixture-a/b/c@example.test`, 마스킹 번호 `FIXTURE-A-****`. connectedId · 암호문 · source_detail · balance · balance_after 는 null.
- [임시] 취소 없음은 canceled_amount null, 전액 취소는 is_canceled=True. 비소비 행 적요는 `가상급여입금` 같은 가상 문구.
- [임시] approved 예산은 `engine_version="fixture-snapshot-v1"` · reason "테스트 스냅샷 (실제 승인 · 엔진 검수 아님)", approved_at = 주기 시작일 09:00. validated_at 은 A v2(10/28) 에만. input_hash · snapshot · validation_summary · task 는 null. **seed 가 만든 approved 는 실제 승인 · 엔진 검수 성공이 아니다.**
- [임시] expected_payday = ends_before, approved_carryover 0, confirmed_income 은 주기 안에 급여 입금 행이 있을 때만(A · B 6월 시작 주기는 null).
- [임시] PaymentDue.occurrence_key 는 서비스 형식 `recurring:{고정지출 ID}:{주기 시작일}`. 은행 SyncRun 은 account=null(연결 단위), 실패 error_code="sync_error". 계좌 · 카드 fetched_at = 마지막 성공 covered_to 23:00(C 카드 10/24).
- [임시] 작은 손계산 fixture 는 테스트 안에서만 만든다(개발용 fixture DB 에 넣지 않음). 식비 예산 차이 11,000 은 서비스 함수가 없어 확인하지 않음.
- [임시] `python manage.py test`(공통 설정)에서는 이 테스트 25개가 skip 된다. fixture 설정으로만 실행.

## 7. 소비 분석 기반: 승인 예산 읽기 · 분석 기간 (19차, 2026-10-29)

새 내부 모듈 `backend/spending_analysis/` (Django 앱 아님, 모델 · 마이그레이션 없음). 채팅 서비스 · router_core 와 아직 연결하지 않음.
테스트는 `spending_fixture/test_analysis.py` (fixture 전용 설정에서만, 공통 설정에서는 skip).

**쓰는 함수 (입력 · 출력 계약)**
- `periods.py` — 순수 함수, Django · 현재 날짜를 읽지 않음. 모든 기간 `Period(start, end_before)` = 시작 이상 ~ 끝 미만(DB `ends_before` 와 같은 경계), API `{from, to}` 는 `(start, last_day)`.
  - `this_month(today)` → 이번 달 1일 ~ today
  - `previous_month_same_days(today)` → 지난달 1일 ~ 같은 일자, 없으면 말일 (10/29 → 9/1~29, 3/31 → 2/28 · 윤년 2/29)
  - `recent_months(today, months)` → 완료된 달 목록(오래된 달부터, 진행 중인 달 제외). months 는 라우터 적용값 1~3 정수만, 아니면 `ValueError`(보정하지 않음)
  - `budget_period(cycle_start, cycle_end_before, today)` → `{"cycle": 주기 전체, "spent": 주기 시작 ~ today}`. today 가 주기 밖이면 `ValueError`
- `budget.py` — `approved_budget(user, on_date)` 읽기 전용(SELECT 만, 잠금 · 주기 생성 · 상태 변경 없음):
  - `{"state": "approved", "cycle": {cycle_id, starts_on, ends_before}, "budget": {budget_id, version_no, approved_at}, "living": {코드: 원}, "living_total", "buckets": {bucket: 합계}, "funding", "spare"}`
  - `{"state": "no_cycle"}` · `{"state": "no_approved", "cycle": …}` · `{"state": "error", "reason": "db_error" | "overlapping_cycles"}`
  - 주기 = on_date 가 들어 있는 DB 주기(starts_on ≤ on_date < ends_before). approved 만, 금액은 BudgetItem(같은 사용자 행만). validated · draft · 프로필 living_budget 으로 대신하지 않음.
- 다음 1~9번 구현에서 쓰는 방법:
  - 1 · 2 · 5번: `this_month(today)` 범위로 `listed_transactions` → `transaction_item` → `spending_summary`
  - 3 · 4번: `approved_budget(user, today)` → state 가 approved 일 때만 `budget_period(...)["spent"]` 범위의 `living_only=True` 집계와 비교. no_cycle · no_approved · error 는 각각 다른 안내
  - 6 · 7번: `this_month` + `previous_month_same_days` (이전 0원이면 증감률 null — chat.md)
  - 8 · 9번: `recent_months(today, months_applied)` (라우터가 정한 적용값 그대로)
  - `record_analysis` 의 `period` = 실제 조회 Period, `as_of` = 조회 시점 — 연결은 다음 작업

**검증 (테스트 DB test_money_cushion_router_fixture)**
- A: 검수된 v2 가 있어도 승인 v1 식비 350,000 · 생활비 900,000 · 여윳돈 200,000. 주기 10/25~11/25 미만, 사용액 범위 10/25~29. 급여일 전날(10/24) → 9/25 주기, 당일 → 10/25 주기, 11/25 → no_cycle.
- C: draft 만 → no_approved (프로필 목표 800,000 사용 안 함). B 변형(v1 superseded · v2 approved) → v2 식비 500,000 · 쇼핑 100,000.
- 다른 사용자 BudgetItem 이 같은 예산을 가리켜도 안 섞임. 조회 전후 예산 · 주기 · fixture 건수 동일, 실행 SQL 전부 SELECT(FOR UPDATE 없음). DB 오류 · 겹친 주기는 error.
- 날짜 경계 시나리오(별도 사용자, 날짜별 2의 거듭제곱 금액): 이번 달 60,000 · 9/1~29 1,000 · 7 · 8 · 9월 256,000 · 128,000 · 3,000 · 사용액 범위 48,000 ↔ 주기 전체 112,000.

**필요한 것 (다음 작업)**
- (20차 구현됨 → 8번) 수집 완전성 판정. 남은 것: 1~9번 응답 구성, 답변 문구, `record_analysis` 연결.
- 급여일 변경 예약(payday_changes_on) 중 주기는 DB 주기를 그대로 읽는다 — 주기 준비는 기존 서비스(ensure_cycle) 책임이고 여기서 만들지 않는다. DB 에 주기가 아직 없으면 no_cycle.

**임시 결정**
- [임시] 주기는 급여일로 계산하지 않고 DB 주기에서 찾는다(승인 예산이 붙은 실제 주기와 어긋나지 않게).
- [임시] 같은 날짜를 포함하는 주기가 2개면 고르지 않고 `error/overlapping_cycles`.
- [임시] `periods.py` 는 `budgets.home.add_month` 와 같은 계산을 따로 둔다(Django import 없이 순수 함수로 쓰려고). 말일 처리는 `calendar.monthrange`.
- [임시] months 검증 범위 1~3 은 라우터 `MONTHS_MAX` 와 같은 값을 따로 둔 것 — 라우터 값이 바뀌면 같이 바꿔야 한다.
- [임시] B 승인 교체 변형은 `seed.variant_b_approved_v2` — 테스트 안에서만 호출(롤백), 기본 적재 · 개발용 fixture DB 에는 넣지 않음.
- `budget_data` 의 validated 우선 동작은 그대로 두고, 기존 expectedFailure 테스트는 그 동작 기록으로 유지.

## 8. 소비 분석 기반: 수집 완전성 판정 (20차, 2026-10-29)

`backend/spending_analysis/coverage.py` — 읽기 전용(SELECT 만, 쓰기 · 잠금 · 주기 생성 · 동기화 · CODEF 없음). 테스트 `spending_fixture/test_coverage.py`.
기존 API 응답에 coverage 칸을 추가하지 않았다. 채팅 서비스 · router_core 와 아직 연결하지 않음.

**확인한 기존 코드 (판정 근거)**
- `finance.api.listed_transactions`: 사용자 · 유형(spending · income · transfer)만 거른다. is_selected · 연결 해제 · 원천 종류(bank · card · manual · loan)로 거르지 않음 → 집계 대상 = 사용자의 모든 계좌 · 카드 거래(+ 직접 입력 등).
- `finance.sync.sync_bank`: `bank_sync` 를 **연결 단위**(account FK 없음)로 남기고 성공 때 covered = 요청 범위. 거래는 입출금(kind 11 → `demand`) 계좌만 받는다. 실패는 covered 없이 status=failed.
- `finance.sync_cards`: `card_approval_list` 를 **카드마다**, 승인내역을 빠짐없이 카드에 붙였을 때만 남긴다. covered = 카드사가 실제로 조회해 준 기간(commStart/EndDate 로 좁혀짐). `card_bill_list`(청구서) · `card_sync`(연결 단위) · `card_list` 는 승인내역 근거가 아님.
- covered_to 는 **포함**(CODEF endDate, `budgets.protect.covered_until` 도 run_to+1일로 이어 붙임) → 내부 `Period(covered_from, covered_to + 1일)`.

**쓰는 함수 (입력 · 출력)**
- `coverage(user, period)` → `{"state", "period", "targets", "undetermined"}` (`error` 일 때는 `{"state": "error", "reason": "db_error", "period"}`)
  - targets: 계좌 · 카드마다 `{"kind": "account" | "card", "id", "covered": [Period], "missing": [Period], "evidence": [{"sync_id", "covered", "completed_at"}], "latest_success_at"}`
  - undetermined: 범위 규칙이 없는 원천 `{"kind", "id", "reason"}` — `not_demand_account` · `manual_card` · `<source_kind>_transactions/no_sync_record`(기간 안의 직접 입력 · 대출 원천 목록 거래)
  - state: `complete` = 대상 1개 이상 + 모든 대상 빈 날 없음 + undetermined 없음 / `none` = 확보된 날이 하나도 없음(대상 없음 포함) / `partial` = 그 밖(undetermined 가 있으면 다 덮여도 partial) / `error` = 이 함수의 DB 조회 실패. 기관 조회 실패 기록은 '성공 없음'일 뿐 error 가 아니다.
  - **원천 범위 자체를 확정할 수 없는 경우는 complete 가 될 수 없다** — 호출 쪽은 state 만 보지 말고 undetermined 로 이유를 구분한다.
  - complete 는 범위가 다 있다는 뜻이지 최신이라는 뜻이 아니다. 최신성은 `latest_success_at` · evidence 의 `completed_at` 으로 따로 본다.
- `merge(periods)` · `gaps(period, covered)` — 순수 함수. 겹침 · 바로 이어짐(앞 end_before == 뒤 start, 즉 covered_to 다음 날) 병합, 빈 구간 목록.
- 대상 매칭: 계좌 = `bank_sync` 성공 중 (account FK == 그 계좌) 또는 (account FK 없음 + 그 계좌의 **현재** connection). 카드 = `card_approval_list` 성공 중 card FK == 그 카드. 모두 user 로 한 번 더 거름. 실패 기록은 읽지 않아서 나중 실패가 확보 범위를 지우지 않음.

**판정 결과 (fixture, 기준 10/29)**
- A · B: 7 · 8 · 9월, 10/1~29, 현재 주기 사용액 범위(A 10/25~29, B 10/10~29) 모두 complete. 근거 = 대상마다 성공 조회 1건(계좌 · 카드), 최근 성공 각 기간 말일 23:00.
- C: 7 · 8 · 9월 none(계좌 · 카드 성공 0건 — 0원 아님). 10/1~29 partial(계좌 10/1~19 · 카드 10/1~19 + 10/25~29 부족). 현재 주기 10/20~29 partial(계좌 완전, 카드 10/25~29 부족, 카드 최근 성공 10/24 23:00 — 10/25~29 실패 기록이 덮지 않음).

**다음 1~9번에서 쓰는 방법**
- 각 유형이 실제로 조회하는 Period(이번 달 · 지난달 같은 날짜 · 완료된 달 · 주기 사용액 범위)마다 `coverage` 를 부른다.
- 6 · 7번 비교: 이전 기간이 complete 가 아니면 `comparison.available=false, reason="previous_incomplete"`(chat.md 기존 계약), 이전 금액 · 증감은 null. 이전이 complete 이고 0원이면 증감률 null(기존 명세).
- 8 · 9번 추이: complete 가 아닌 달을 0원으로 그리지 않는다(표시 방법은 응답 구성 때 결정).
- 1 · 2 · 3 · 4 · 5번 현재 기간: partial 이면 '확인된 거래 기준' 제한이 필요 — 문구 · 응답 칸은 미정(아래).
- `record_analysis` 결과에 판정 state · 최근 성공 시각을 같이 남길지는 연결 작업 때 결정.

**필요한 것 (원천 · 수집 범위 정책 미정)**
- 재연결 계좌: sync_bank 는 계좌를 새 연결로 옮기므로 옛 연결의 bank_sync 성공은 지금 판정에서 빠진다(보수적으로 덜 덮음). 계좌별 이력 기준을 쓸지 결정 필요.
- 입출금 외 계좌(예적금 · 기타 · 종류 미상): 거래를 받지 않는다. 지금은 undetermined 로 complete 를 막는다 — 실제 사용자 대부분에게 예적금이 있으면 complete 가 거의 안 나오므로 '집계 대상 아님'으로 뺄지 결정 필요.
- 직접 등록 카드 · 직접 입력 거래 · 대출 원천 거래: 수집 범위 개념이 없다. 지금은 undetermined. 직접 입력은 '추가 자료'로 보고 막지 않을지 결정 필요.
- 해제된 연결 · 관리 대상 아님(is_selected=false) 카드 · 계좌: 거래가 집계에 들어가므로 대상에 포함(해제 이후 기간은 missing). 해제 시점 이후를 대상에서 뺄지는 결정 필요.
- 카드사 단위 조회: `budgets.protect.history_until` 은 같은 카드사 카드들의 기록을 이어 붙인다. 여기서는 카드별 기록만 쓴다(sync_cards 가 카드마다 남기므로 현재 데이터에선 같음).
- 계좌 · 카드가 생기기 전 기간(개설 전): 근거가 없어 missing. 개설일 정보가 생기면 다시 검토.
- 부분 수집 안내 문구 · 응답 칸(C), 최신성 기준(몇 시간 지나면 오래된 자료인지)은 미정.

**임시 결정**
- [임시] state 는 4가지만 두고, 원천 범위를 확정할 수 없는 경우는 별도 state 대신 `undetermined` 목록 + complete 금지로 표현.
- [임시] `merge` · `gaps` 는 `budgets.protect.covered_until`(시작일부터 이어진 마지막 날만 돌려줌)을 쓰지 않고 따로 둔다 — 빈 구간 목록이 필요해서. 이어 붙이는 규칙(다음 날이면 이어짐)은 같다.
- [임시] 판정 범위 밖 성공 기록은 처음부터 읽지 않는다(covered 가 period 와 겹치는 것만). 기준 시각 인자는 필요 없어서 두지 않음(판정은 기록만 본다).
- [임시] `periods.MAX_MONTHS == router_core.rules.MONTHS_MAX` 회귀 테스트 추가.

## 9. 소비 분석 1~9번 내부 결과 (21차, 2026-10-29)

`spending_analysis/analyze.py`(실행 · 입력 검증) + `result.py`(결과 형식 · 순수 계산). 읽기 전용, 테스트 `spending_fixture/test_analyze.py`.
채팅 API · slots · 답변 문구 · LLM · record_analysis 와 연결하지 않았다. 수집 판정의 미정 원천 정책(8번)은 바꾸지 않았다.

**쓰는 함수 (입력 · 출력)**
- `analyze(user, item, *, today, as_of)` — item = 라우터 `Decision.items` 한 항목 `{type, category, months_requested?, months_applied?}`, today = 기준 날짜(date), as_of = 조회 시각(시간대 있는 datetime, KST 날짜가 today 이상).
  잘못된 입력(유형 · 카테고리 짝 · 3번 UNCLASSIFIED · 개월 수 · 모르는 칸 · 날짜 형식)은 `ValueError`, 보정하지 않음.
- 공통: `{"item"(원본 그대로, months_requested 포함), "type", "category", "today", "as_of", "state": "ok" | "error"}`. error 는 `reason="db_error"` 만 있고 금액 칸이 없다.
- 기간 블록 `{"period": Period, "coverage": coverage() 결과 전체, "observed": 합계, "confirmed": complete 일 때만 같은 합계, 아니면 None}`.
  합계 칸 = 05 와 같은 이름(total · fixed_amount · living_amount · by_category · unclassified_amount · needs_review_count) + 유형별 `category`{code, amount, share} · `top_spending` · `shares`.
- 유형별:
  - 1 · 2 · 5 → `current`(이번 달 블록). 2번도 total · fixed_amount 는 전체 기준, 선택 카테고리는 `category`. 5번 `top_spending`(UNCLASSIFIED 제외, 동률 모두 카테고리 순서, 분류 소비 없으면 None) · `shares`(분모 = 미분류 포함 전체, 전체 0원이면 {}).
  - 3 · 4 → `budget`{state: approved | no_approved | no_cycle, approved 면 budget_id · version_no · approved_at · allocated}, `cycle_period`, `spent_period`(주기 시작 ~ 기준일), `coverage`, `observed`{spent: 생활비(고정비 제외)}, `confirmed`{allocated, spent, difference(부호), remaining=max(차이,0), over=max(−차이,0), 4번만 covered_by_spare · spare_remaining · shortfall}. confirmed 는 approved + 사용액 범위 complete 일 때만. no_approved 면 초안 · 프로필 목표 금액을 담지 않는다. no_cycle 이면 기간 · 사용액도 없음.
  - 6 · 7 → `current` · `previous`(지난달 같은 날짜 블록), `comparison`{"total", 7번은 "category"도} 각각 {available, previous_incomplete, current_incomplete, delta, rate}. 두 기간 모두 complete 일 때만 delta · rate. 이전 0원이면 delta 는 내고 rate=None.
  - 8 · 9 → `months`(완료된 달 블록 목록) + `series`[{period, state, observed, confirmed}] — 불완전한 달은 confirmed=None, 목록에서 빼지 않음.
- 비율 · 증감률: `percent(part, whole)` → {"value": Decimal 소수 둘째 자리 HALF_UP, "exact": {numerator: part×100, denominator: whole}}, whole=0 이면 None. 정수 산술로 한 번만 반올림.

**어디에 담기는지**
- 분석 기간 = 블록 `period`(3 · 4번은 `spent_period`, 주기 전체는 `cycle_period`) / 비교 기간 = `previous.period` / 조회 시각 = `as_of` / 예산 버전 = `budget.version_no` · `budget_id` · `approved_at` / 수집 상태 = 블록 `coverage.state` · `missing` · `undetermined` · `evidence` · `latest_success_at`.

**결과 예시 (fixture, today 10/29, as_of 10/29 23:59:59 KST)**
- A `{type: 3, category: FOOD}` → 승인 v1, spent_period 10/25~29, complete, confirmed {allocated 350,000, spent 60,000, difference 290,000, remaining 290,000, over 0}.
- B `{type: 7, category: FOOD}` → 이번 달 식비 630,000 vs 9/1~29, available, delta 224,000, rate 55.17. `{type: 4}` → confirmed {1,300,000, 1,362,000, −62,000, remaining 0, over 62,000, covered_by_spare 62,000, spare_remaining 138,000, shortfall 0}.
- C `{type: 1}` → partial, observed 390,000, confirmed None. `{type: 4}` → no_approved, observed spent 290,000, confirmed None. `{type: 6}` → available false, previous_incomplete · current_incomplete 둘 다 true. `{type: 8, months_applied: 3}` → 7 · 8 · 9월 모두 state none · observed 0 · confirmed None.

**record_analysis 로 넘길 수 있는 것 / 연결 때 정할 것**
- 넘길 수 있는 것: `period`(블록 period 또는 spent_period), `as_of`, `compare_period`(previous.period), `facts` 후보(confirmed 수치 · budget.version_no · coverage.state).
- **정하지 않음**: partial · none · no_approved 결과를 '직전 분석'으로 기록할지(지금 record_analysis 는 ok 만 보고 기록 — 그대로 연결하면 불완전 결과도 직전 성공 분석이 된다). Period · Decimal · datetime 을 대화 상태에 저장할 JSON 모양. facts 에 observed 를 넣을지.
- 그 밖: run_items 의 fetch 끼우기(위 lambda), 답변 템플릿, 현재 기간 불완전 / 승인 예산 없음 / 주기 없음 / db_error 안내, chat.md 카드 칸으로 옮기기(current_incomplete 는 API reason 코드 없음 — 임의로 만들지 않음).

**임시 결정**
- [임시] 블록의 confirmed 는 observed 와 같은 dict(복사 아님)를 가리킨다 — 호출 쪽에서 고치지 말 것.
- [임시] 6번은 total 비교만, 7번은 total + 선택 카테고리 비교. 카테고리별 전체 비교표(chat.md categories[].prevDiff)는 응답 구성 때 추가.
- [임시] 3번 카테고리에 승인 예산 줄이 없으면 allocated=None · confirmed=None.
- [임시] 수집 판정과 거래 조회는 같은 DB 스냅샷이 아니다(읽기 전용 트랜잭션 없음). 판정 직후 동기화가 끝나면 어긋날 수 있음 — 연결 때 검토.
- [임시] 현재 수집 판정 한계(8번 필요한 것)가 그대로 적용된다: 입출금 외 계좌 · 직접 등록 카드 · 직접 입력 거래가 있으면 complete 가 안 나와 confirmed=None.

## 10. 답변 템플릿 · 대화 맥락 독립 연결 (22차, 2026-10-29)

독립 실행 · 격리 테스트만. chat API · `chat/replies.py` · `chat/router.py` · 실제 대화 상태 DB(Conversation)는 연결하지 않았다. LLM 설명 생성 없음.

**쓰는 함수**
- `spending_analysis/adapter.py`
  - `run(user, decision, *, today, as_of)` → `run_items` 결과(항목 순서 유지, 한 항목 실패가 다른 결과를 안 지움). analyze 의 `state=error` 는 예외(`AnalysisFailed`)로 바꿔 ok=False — 성공 분석으로 다루지 않는다.
  - `record(store, conversation_id, decision, results)` → 결과를 JSON 저장형 요약으로 바꿔 `record_analysis` 호출.
  - `summarize(result)` → `{evidence: "confirmed" | "limited", limits: [...], period, compare_period?, as_of, facts?}`. facts 는 확정 수치만(limited 면 없음).
    limits 코드: `current:partial|none`, `previous:partial|none`, `month:YYYY-MM:partial|none`, `budget:no_approved|no_cycle`, `comparison_unavailable`.
  - `jsonable(v)` · `period_from_json(d)` — 저장 형식: Period → `{"from","to"}`(둘 다 포함, chat.md 와 같음), date → `YYYY-MM-DD`, datetime → ISO 8601(시간대 포함), Decimal → 문자열(`"25.83"`, float 금지), UUID → 문자열, 금액 원 정수.
- `spending_analysis/answer.py` — `compose(results, notices=())` → `{"lines": 본문, "notices": 공통 안내}`.
  유형 · 상황별 본문 템플릿(TEXT) + 공통 안내(NOTICE, 같은 문장은 한 번만). 카테고리 · 금액별 템플릿 없음. 숫자는 서버 결과를 쉼표 · `원` · `%` 형식만 바꿔 넣는다.
  기간 표시: 달력월 `이번 달(10월 1일~29일)` / 비교 `지난달 같은 기간(9월 1일~29일)` / 예산 `이번 예산 기간(10월 25일~29일)` / 추이 `7월`.
  구분: 부분 자료 → "확인된 거래 기준" + 공통 안내 / 수집 기록 없음 → "확인된 자료가 없어요"(0원 아님) / 확정 0원 → "0원이에요" / 승인 예산 없음 → "아직 승인된 예산이 없어서…" / 조회 실패 → "…결과를 불러오지 못했어요" + 재시도 안내 / 초과 → 서버 over 로 "…초과했어요"(+ 여윳돈 충당 · 부족) / 비교 불가 → 증감 없이 이유 안내 / 이전 0원 → 증감률 대신 서버 delta / 불완전한 달 → "확인된 자료 없음"·"확인된 거래 기준"(생략 · 0원 표시 없음). '확인 필요' 거래 안내는 전체 범위 답변에만.
- `spending_analysis/analyze.py` 6번에 `comparison["categories"]` 추가(카테고리별 {available, previous_incomplete, current_incomplete, delta, rate}).

**router_core 변경 (내부 기록 계약)**
- `record_analysis`: 항목에 `evidence`(result 에 없으면 `"unknown"` — 근거 아님) · `limits` 저장. 전부 실패면 기존 기록 보존(그대로).
- `rules._explain`: 설명 근거는 `ok` 가 아니라 `evidence == "confirmed"` 항목만. 직전 조회가 부분 자료 · 비교 불가면 `unsupported/evidence_incomplete` — 더 이전 분석으로 바꾸지 않음. 참조 항목이 아예 없으면 기존처럼 `reference_not_available`.
- `MemoryStateStore` · Conversation 모델은 안 바꿈.

**대화 맥락의 두 역할**
- 최근 조회 조건(후속 질문용) = `last_analysis.request`. 확정 · 부분 자료 · 자료 없음 · 승인 예산 없음 모두 남긴다(항목 하나라도 조회가 끝나면). 예: C "이번 달 얼마 썼어?"(partial) → "그럼 식비는?" → 이번 달 식비(2번) 재조회.
- 사실 설명 근거 = `last_analysis.items[]` 중 `evidence == "confirmed"` 의 `facts` · `period` · `as_of`. 예: A 지난달 대비 식비(확정) → "왜 늘었어?" → 그 항목 facts(delta 42,000, rate "12.07"). 비교 불가로 다시 물은 뒤 "왜 늘었어?" → evidence_incomplete.
- 보존: LLM 실패(route failed) · 모든 항목 DB 실패 → 기존 기록 그대로. 섞이면 항목마다 ok · evidence · limits.

**6번 카테고리별 비교표**
- 필수 확인: chat.md 소비 카드 `categories[]` 는 목록(null 아님)이고 `previousAmount` · `prevDiff` · `prevDiffRate` 를 가진다 → 6번에도 필요.
- 완료: 소비 분석 계층(analyze)에서 계산. 답변 생성기는 계산하지 않음(지금 답변 문장에는 전체 증감만 쓴다).
- 내부 초안(확정 아님): 대상 카테고리 = 이번 · 지난 기간 중 한쪽에라도 있는 카테고리(카테고리 표 순서, '확인 필요' 제외 — unclassifiedAmount 한 줄). '금액 큰 순' 정렬 · ratio 는 카드 구성 때.

**필요한 것 (로컬 채팅 화면 연결 전)**
1. `chat/router.py` route_text ↔ router_core.route 통합, 기존 `(intent, slots)` 계약 유지 방법.
2. chat.md 소비 카드 매핑: `period/compare/comparison/total/fixedAmount/previousTotal/categories[]/topSpending/unclassifiedAmount/lines/updatedAt` ← analyze 결과. `current_incomplete` 는 API reason 코드가 없음 → 결정 필요. coverage 는 1차 응답에 넣지 않음(10/22 J) → 부분 자료 안내는 lines 로만.
3. 금액 표기 정책: chat.md 예시는 "132만 원"(반올림) · 비율 정수("11%"). 지금 답변은 원 단위 그대로 · 소수 둘째 자리 — 표기 규칙 결정 필요.
4. 대화 상태 영속 저장(Conversation.slots 등) · 만료, 설명 요청(explain) 답변 생성(LLM 또는 템플릿) — 설명은 evidence=confirmed facts 만 쓰기.
5. 예산 조정 handoff 의 `context.last_analysis` 에 limited 항목도 들어간다 — 에이전트가 evidence 를 보도록 계약에 적기.
6. 조사(을/를 · 이/가) 자동 처리 없음 — 카테고리 이름 뒤에 조사가 오지 않는 문장으로만 썼다.
7. 수집 판정 미정 정책(8번)은 그대로 — 실제 사용자는 예적금 · 직접 입력 때문에 complete 가 덜 나올 수 있다.

**임시 결정**
- [임시] evidence=confirmed 는 항목의 모든 기간이 complete 이고 (3 · 4번은 승인 예산까지) 확정 수치가 있을 때만.
- [임시] months_capped 안내는 답변 생성기가 Decision.notices 에서 받아 적용값으로 표시(다른 라우터 안내는 기존 replies 몫).
- [임시] 조회 실패 항목 문장은 DB 실패 · 잘못된 입력을 구분하지 않는다(둘 다 "불러오지 못했어요").

## 11. 메인 라우터 + 에이전트 분리 설계안 (23차, 2026-10-29 — 설계만, 코드 · 프롬프트 변경 없음)

> **팀 합의 전 초안.** 이번 개발 대상은 메인 라우터와 소비 분석 에이전트 분리다.
> 예산 조정 · 청년 정책 추천 에이전트는 독립된 외부 모듈로 보고, **연결 계약만** 제안한다(내부 흐름 · 질문 · 프롬프트 · 도구 · 알고리즘 · 내부 상태는 담당자가 정함).
> 표시: [확정] 기존 확정 정책 / [제안] 이번 추천안 / [미정] 결정 필요.

### 11-1. 저장소에서 확인한 현재 상태
- `chat/router.py` 공개 계약: `INTENTS = (spending, budget, big, out, unknown)`, `CHOICES = (spending "소비 분석", budget "예산 조정")`, `route_choice(choice) → (choice, {})`(LLM 안 거침), `route_text(text, conversation)` → 지금은 항상 `RouterUnavailable`.
- `chat/api.py`: `choice` 는 `CHOICE_LABELS` 에 있는 값만 받는다(아니면 400). 선택지는 unknown · out 답변에만 붙는다. 첫 채팅용 시작 버튼 · 첫 인사 메시지는 API 계약에 없다(대화는 첫 메시지 때 만들어짐).
- `chat.Conversation.active_agent`(코드 미정) · `slots`(JSON), `AsyncTask.conversation` FK 가 이미 있다. 라우터 실패 때 둘을 바꾸지 않는다는 규칙이 chat.md 에 있다.
- 청년 정책 추천: README '후순위' 한 줄뿐. 코드 · API 명세 · 데이터 · 검색 방식 없음 → 계약과 테스트 대역만.
- frontend `AssistantPage.jsx` 는 `mockRouter.js` 로 흐름만 보여 준다(이번에 손대지 않음).
- `router_core` 는 지금 한 번의 LLM 호출(llm.PROMPT, 해시 6a3bb2a3 로 평가 완료)로 의도 + 소비 세부 조건 + 예산 보조값 + 후속 답 판단을 함께 뽑는다.

### 11-2. 기능 이동표 (현재 파일 · 함수 기준)

| 구분 | 현재 위치 | 내용 | 이유 |
|---|---|---|---|
| 메인 라우터 유지 | `router_core/__init__.py` `route` | 대화 상태 읽기 → 해석 → 담당 에이전트로 보내기, 실패 시 상태 보존 | 전달 · 전환 조정은 라우터 책임 |
| 〃 | `_cancel_word` · `CANCEL_WORDS` · `_reask` | "취소"를 LLM 없이 처리, 취소 대상(질문/작업) 묻기 | 여러 에이전트에 걸치는 판단 |
| 〃 | `rules.decide` 첫 부분(복합 의도 → `asking=intent`) | 무엇부터 할지 확인 | 의도 간 조정 |
| 〃 | `_handoff` · `_forward` · `_resume` · `_task_answer` · `_check` · `_finish` · `record_budget_task` · `pop_queued_start` | 예산 작업 시작 · 이어 받기 · 재개 · 취소 먼저 · 새 작업 한 번만 시작, 서버 확인 | [확정] 규칙 유지. 예산 에이전트 내부가 아니라 라우터가 지키는 연결 규칙 |
| 〃 | `route` 의 소비 전환 시 작업 보류 · 안내(`budget_task_suspended/saving`) | 전환 · 보류 | 에이전트 간 조정 |
| 소비 분석 에이전트로 이동 | `rules._mode · _spend · _budget_left · _ready · MONTHS_*` | 대상 · 기간 · 비교 방식 해석, 1~9 유형 결정, 개월 수 제한 | 소비 분석 세부 조건 |
| 〃 | `rules.overlay · _conflict · conditions · unknown_words · category_options · NO_BUDGET` | 직전 조건 이어받기 · 전체 범위 전환 · 모르는 카테고리 · 잔액 조회 제외 카테고리 | 〃 |
| 〃 | `rules.merge` 중 months · category · scope · explain_target · budget_current | 소비 조건 되묻기 답 합치기 | 〃 (intent 선택 답 합치기만 라우터에 남김) |
| 〃 | `rules._explain` | 설명 대상 · 확정 근거(evidence=confirmed) 확인 | 〃 |
| 〃 | `__init__.record_analysis · run_items`, `route` 의 `after_budget_change` 처리 | 분석 맥락 기록, "바뀐 예산으로" 안내 | 소비 결과 기록은 소비 에이전트만 쓴다. 예산 변경 결과는 라우터가 넘긴 참조로 읽기만 |
| 〃 | `schema.py` 소비 칸(target · period · compare · months · categories · all_categories · refers_to · explain · after_budget_change), `find_category` | 소비 해석 형식 검사 | 〃 |
| 〃 | `spending_analysis/adapter.py · answer.py` | 분석 호출 · 답변 · 기록 요약 | 이미 분리돼 있음 → 소비 에이전트의 대화 계층이 사용 |
| 공통 재사용(그대로) | `spending_analysis/periods · budget · coverage · analyze · result` | 계산 · 승인 예산 · 수집 완전성 | 계산 계층. 대화를 모름 |
| 〃 | `MemoryStateStore`(테스트용 저장소), `spending_fixture` DB · 05 기대값 · 테스트, `scripts/router_eval` | 상태 저장 · 검증 | 그대로 쓴다 |
| 나눠야 함 | `schema.py` · `llm.PROMPT` | 라우터 칸(intents · followup · choice · confirm · resume · unclear)과 소비 칸이 한 형식 · 한 프롬프트 | 1단계는 그대로 두고 결과만 나눠 넘김(11-5), 2단계에서 나눔 |
| 〃 | `schema` 의 예산 보조값(direction · amount · amount_mode · hints) | 라우터가 뽑아 예산 쪽에 넘김 | [미정] 예산 담당자가 쓸지 결정 — 쓰지 않으면 원문만 넘김 |
| 새로 필요 | — | 에이전트 식별자 표, 버튼 진입, 공통 입력 · 결과 계약, 일반 '보류 작업' 기록, 청년 정책 대역, 소비 에이전트 진입 함수 | 11-3 ~ 11-4 |

### 11-3. 최소 폴더 · 모듈 구성안 [제안]
```
backend/chat/router.py              공개 계약 그대로 (INTENTS · CHOICES · route_choice · route_text). 통합 때 안에서 router_core 를 부른다
backend/chat/router_core/           메인 라우터 (이름 유지 — chat/router/ 를 만들지 않는다)
    __init__.py   route(...) — 텍스트 · 버튼 진입, 전환 · 보류 · 재개 · 취소, 예산 작업 서버 확인 (기존 함수 유지)
    agents.py     에이전트 식별자 → 호출 함수 dict 하나 (프레임워크 · 기반 클래스 없음)
    schema.py · llm.py   라우터 해석 (2단계에서 소비 칸을 뺀다)
backend/spending_analysis/          소비 분석 에이전트 = 대화 계층 + 계산 계층
    agent.py      handle(request, store, complete) → 결과 (해석 · 유형 결정 · 되묻기 · 이어받기 · 설명 · 분석 · 답변 · 기록)
    rules.py      router_core/rules.py 의 소비 부분 이동
    adapter.py · answer.py            대화 계층이 쓰는 기존 모듈
    periods · budget · coverage · analyze · result   계산 계층 (변경 없음)
예산 조정 · 청년 정책 추천: 담당자 모듈. 라우터는 agents.py 의 호출 함수로만 부른다. 연결 전엔 테스트 안의 대역.
```
- 의존 방향: router_core → (agents.py 표) → 각 에이전트. 에이전트는 router_core 를 import 하지 않는다(지금 `adapter.py` 가 쓰는 `record_analysis · run_items` 는 소비 쪽으로 옮겨 역방향 의존을 없앤다). 에이전트끼리 서로 import 하지 않는다.
- 계약 값은 dict(JSON 저장 가능)로 둔다. 새 클래스 · 레지스트리 프레임워크 · 메시지 버스 없음.

### 11-4. 상태 소유권과 연결 계약 초안 [제안 — 담당자 협의 전]

**상태 소유권** (공유 저장소 하나, 대화 ID 별 칸. 칸마다 쓰는 쪽은 하나)

| 상태 | 쓰는 쪽 | 읽는 쪽 | 비고 |
|---|---|---|---|
| 현재 담당 에이전트 `active_agent` | 라우터 | 라우터 | 영속 후보 `Conversation.active_agent`(코드: spending · budget · policy 제안) |
| 라우터 질문(의도 선택 · 취소 대상 · 작업 재개/새로 · 전환 확인) | 라우터 | 라우터 | 지금 `pending` 중 asking ∈ {intent, cancel_target, budget_task_choice} |
| 에이전트 되묻기 상태 | 각 에이전트 | 그 에이전트 | 라우터는 결과의 `status=awaiting_input` 만 안다(내용 모름). 소비: months · category · scope · explain_target · budget_current · detail |
| 소비 직전 조건 · 결과 · evidence · limits | 소비 에이전트 | 소비 에이전트 | 다른 에이전트가 필요하면 라우터가 소비 에이전트의 요약 함수(확정 근거만)로 받아 그 요청에만 넣는다. 복사본을 따로 두지 않는다 |
| 진행 · 보류 작업 기록 `works[agent] = {work_ref, suspended, phase?}` | 라우터 | 라우터 | 지금 `budget_task` 를 에이전트 공통 모양으로. 예산은 phase 를 서버 확인값으로만 갱신 |
| 끝난 작업 결과 `results[agent]` | 라우터 | 라우터 → 필요한 에이전트에 참조로 | 예산 = 지금 `budget_result`(outcome · saved_ref · checked_at, 서버 확인만). 정책 = 에이전트가 알린 종료 사유만(저장 개념 없음) |
| 재개 참조 `work_ref` | 에이전트가 만들어 결과로 돌려줌 → 라우터가 보관 | 라우터가 resume 때 그대로 돌려줌 | 라우터는 내용을 해석하지 않는다(예산은 서버 task_ref) |

**입력 (라우터 → 에이전트)** — 24차 정정본, 11-10 참고
```
{
  "agent": "spending" | "budget" | "policy",
  "mode": "start" | "continue" | "resume" | "cancel",
  "entry": "text" | "button",
  "text": 사용자 원문 | null,                       # 버튼이면 null
  "input_id": "...",                                # 이번 사용자 입력 하나의 번호. 재전달 제한 · 중복 처리 방지
  "conversation_id": "...",
  "today": "YYYY-MM-DD",
  "as_of": "ISO 8601 시각(시간대 포함)",
  "work_ref": 이 에이전트가 앞서 돌려준 참조 | null,
  "context": {
    "switched_from": 직전 담당 에이전트 | null,
    "declined_by": 이번 입력을 not_mine 으로 돌려준 에이전트 | null,
    "budget_change": {"outcome": ..., "checked_at": ...} | null,     # 소비 '바뀐 예산으로' 요청 때만
    "shared": {...} | null,                                          # 다른 에이전트의 확정 요약, 그 요청에 필요할 때만
    "interpretation": 라우터 통합 추출 중 이 에이전트 몫 | null        # [임시] 단계 A 에서만. 단계 B 에서 없앤다
  }
}
```
- 사용자 식별은 서버 쪽 인자(회원 객체)로 넘기고 LLM 입력에는 넣지 않는다. 거래 원문 · 전체 대화 기록은 넘기지 않는다.

**결과 (에이전트 → 라우터)** — 24차 정정본, 11-10 참고
```
{
  "status": "replied" | "awaiting_input" | "unsupported" | "ended" | "failed" | "not_mine",
  "reply": {"lines": [...], "notices": [...], "card": ... | null} | null,
  "work_ref": 재개 참조 | null,
  "ended_reason": "done" | "canceled" | null,
  "error": 오류 코드 | null,
  "reason": 미지원 · not_mine 이유 코드 | null
}

status 의 뜻
  replied         자기 분야 요청에 답함
  awaiting_input  자기 분야 요청인데 정보가 부족해 되물음 (되묻기 상태는 에이전트가 보관)
  unsupported     자기 분야지만 지원하지 않는 요청 — 에이전트가 안내 문장을 reply 에 담는다
  ended           에이전트 작업이 끝남 (예산은 라우터가 서버 확인으로 다시 판단)
  failed          에이전트 오류 — not_mine 으로 바꾸지 않는다. 라우터는 기존 상태를 그대로 둔다
  not_mine        다른 분야 요청 — reply 없음, 목적지를 적지 않는다, 자기 상태를 바꾸지 않는다
```
- 예산 조정: 에이전트가 "저장했다"고 해도 라우터는 믿지 않는다. [확정] 저장 성공 = 서버 `task_status` 의 `saved` + `saved_ref` 만. 끝남 · 실패 · 취소도 서버 확인으로만 정하고, 확인 실패면 아무것도 바꾸지 않는다. 기존 승인 예산이 있다는 사실(`approved_budget`)과 이번 작업 저장 성공은 다른 값이다.
- 청년 정책 추천: 저장 개념을 가정하지 않는다. `ended/canceled` 는 에이전트 결과 그대로. 자격 충족 · 수혜 확정은 라우터가 판단하지 않는다.
- 소비 분석: `ended` 를 쓰지 않는다(매 답변 `replied`, 되묻기면 `awaiting_input`). 보류할 장기 작업이 없다.

**기존 start · continue · resume · cancel 과의 관계**
- 같은 네 모드를 모든 에이전트에 쓴다. 지금 예산 handoff `{task, mode, text, hints, context{conversation_id, selected_from, last_analysis, active_task, task_state?, cancel_for?}}` 는 위 입력의 `agent="budget"` 형태로 옮긴다: selected_from → `switched_from`, last_analysis → `shared.spending`(확정 근거 요약만, limited 는 조건만), active_task · task_state → `work_ref` + 서버 확인 상태, cancel_for → `context.cancel_for` 유지.
- 취소 먼저 새로 시작(`next_start` → 서버 canceled 확인 → `pop_queued_start` 한 번) [확정] 은 라우터에 그대로 둔다.

**버튼 · 자연어 진입 차이**
- 버튼: 목적지가 정해져 있으므로 라우터 분류 LLM 없이 전환 규칙을 적용하고 `entry=button, text=null` 로 연결한다(그 에이전트에 보류 작업이 있으면 재개/새로 질문 — 자연어와 같은 규칙). 선택만으로 분석 조건 확정 · 예산 승인 · 변경을 하지 않는다. 에이전트 내부 호출 수는 에이전트 몫이다.
- 소비 분석 버튼 → 소비 에이전트가 `awaiting_input` + "어떤 소비가 궁금하세요?" + 예시(이번 달 얼마 썼어? · 지난달보다 식비 더 썼어? · 식비 예산 얼마 남았어?). 다른 버튼의 시작 안내는 각 에이전트 몫.
- 대화 도중 버튼: 자연어 전환과 같은 규칙(보류 · 재개 질문 · 저장 중 안내). 덮어쓰기 · 삭제 없음.
- 지금 API 로 표현 가능: `choice: "spending" | "budget"`(기존 값). 변경 필요(이번에 추가 안 함): 청년 정책 `choice` 값과 `route.intent` 코드(chat.md 의도 표 · `CHOICES` · `CHOICE_LABELS` 검사), 첫 채팅 시작 버튼을 내려주는 방법.

### 11-5. LLM 호출 구조 [제안 — 24차 정정본]

호출 수는 '라우터 분류 호출'과 '에이전트 호출'을 따로 센다. 에이전트 호출은 소비 분석은 이 설계 기준 예상값, 예산 · 정책은 담당자 몫(표에 '담당자').

최종 구조 (단계 B: 역할별 프롬프트 분리 뒤)

| 상황 | 라우터 분류 | 담당 에이전트 | 그 뒤 |
|---|---|---|---|
| 기능 버튼 | 0 | 소비: 시작 안내 템플릿이면 0 예상 / 예산 · 정책: 담당자 | — |
| 담당 없음 + 새 자연어 | 1 | 목적지 에이전트 1 (자기 프롬프트로 세부 해석) | — |
| 담당 있음 + 일반 후속 · 되묻기 답 | 0 | 1 (소비: 목록 선택 · 코드 매칭이면 0) | — |
| 담당 있음 + 다른 분야 자연어 | 0 | 1 (자기 분야인지 판단 → not_mine) | 라우터 재분류 1 + 새 담당 start 1 → **전환 때 추가 호출 최대 2** |
| 라우터가 직접 한 질문의 답 | 선택지 · 코드 매칭 0, 아니면 1 | 0 | 답에 따라 연결 |
| '취소' 단어 | 0 (기존 코드 규칙) | cancel 전달 시 담당자 | — |

이전 과정 (단계 A: 기존 통합 프롬프트 유지, 코드 책임만 분리) — [임시]

| 상황 | 호출 | 설명 |
|---|---|---|
| 담당 없음 + 새 자연어 | 통합 1 | 기존 프롬프트(해시 6a3bb2a3)가 라우터 분류와 소비 세부 추출을 함께 한다. 소비 몫은 `context.interpretation` 으로 넘겨 소비 에이전트가 다시 호출하지 않는다 |
| 소비 담당 + 후속 | 통합 1 | 소비 에이전트가 같은 통합 프롬프트(되묻기 · 직전 조건 포함)를 부른다. 결과의 intents 가 소비가 아니면 not_mine |
| 소비 담당 + 다른 분야 → not_mine | 추가 0 | 라우터 재분류에 방금 받은 통합 결과(라우터 형식의 의도 · 후속 칸)를 그대로 쓴다. 같은 프롬프트를 같은 입력으로 다시 부르지 않기 위한 임시 처리 — not_mine 자체로 목적지를 정하는 것이 아니다 |
| 예산 담당(지금은 대역) + 문장 | 통합 1 | 기존 동작 유지(라우터가 budget_task 맥락으로 답/새 요청을 가름). 예산 담당자가 계약을 구현하면 최종 구조로 바꾼다 |

- 새 요청에서 통합 추출을 재사용하는 것은 단계 A 의 임시 구조다. 최종 구조는 '라우터 = 분야 분류', '에이전트 = 자기 분야 판단 + 세부 해석'.
- 오분류 방지: 담당 에이전트는 자기 분야가 아니면 답으로 처리하지 않고 not_mine. 라우터는 재분류 결과가 not_mine 을 돌려준 에이전트를 다시 가리키면 재전달하지 않고 확인 질문(11-10).

### 11-6. 단계별 이전 · 검증 계획 [제안]
0. 기준 고정: 지금 테스트(router 75 · spending_fixture 95 · router_eval 19)와 평가 결과(dev v3 · new42 장부 · 응답)를 보존. `test_conversation.py` 를 분리 전 기준 흐름(라우터 → 소비 분석 → 답변 → 기록)으로 삼는다.
단계 A — 기존 통합 프롬프트 그대로, 코드 책임만 분리
1. 소비 에이전트 진입만 추가: `spending_analysis/agent.py` 가 지금 `route` 의 소비 결정을 받아 adapter.run → compose → record 를 한다(로직 이동 없음). `test_conversation.Chat` 을 이 진입으로 바꿔도 결과가 같아야 한다.
2. 소비 규칙 이동: rules · merge · explain · record_analysis · run_items 를 소비 쪽으로 옮긴다. 이동한 테스트가 그대로 통과해야 한다.
3. 담당 우선 흐름: 담당이 있으면 입력을 먼저 담당에게, not_mine → 라우터 재분류(단계 A 는 통합 결과 재사용), 재전달 제한 · 확인 질문, 라우터 질문 우선 처리. `agents.py` 표, `works` · `results` 공통 모양(예산 규칙 그대로), 버튼 진입. 예산 · 정책은 대역.
4. 저장 응답 replay(router_eval, API 호출 0)로 결정 · 상태가 분리 전과 같은지 확인.

단계 B — 역할별 프롬프트 분리 + 재평가
5. 라우터 프롬프트(분야 분류 · 라우터 질문 답) / 소비 프롬프트(자기 분야 판단 · 세부 해석 · 되묻기 답)로 나눈다. 새 사례 · 새 장부로 실제 LLM 재평가. 단계 A 의 `context.interpretation` 과 통합 결과 재사용을 없앤다.
6. `chat/router.py` · `chat/api.py` 통합(공개 계약 유지, 청년 정책 값 추가 여부는 API 합의 후).

테스트 이동
- `chat/test_router_core.py`: RouterCoreTest 의 유형 1~9 · 되묻기 · 개월 수 · 카테고리 → 소비 에이전트 테스트로. 복합 의도 · 예산 handoff · 취소 단어 · BudgetTaskTest 전부 → 라우터에 남김. ConversationContextTest 의 이어받기 · 설명 → 소비, 전환 · 보류 · 재개 → 라우터.
- `spending_fixture/test_*`: 계산 계층 그대로. `test_conversation.py` → 소비 에이전트 종단 테스트.
- 추가: 에이전트 계약 테스트(입력 · 결과 모양, failed 시 상태 보존), 버튼 진입, 대역 기반 전환 시나리오.

대표 대화 시나리오(추가할 테스트)
- S1 [소비 분석] 버튼 → 시작 안내(LLM 0) → "식비 얼마 썼어?" → 2번 답변.
- S2 소비 개월 수 되묻기 중 → "예산 줄여줘" → 소비 에이전트 not_mine(자기 상태 그대로) → 라우터 재분류 → 예산 start → 전환이 정해진 뒤 소비 되묻기 정리, 직전 분석은 유지.
- S2-1 소비 담당 중 "지난달 식비 예산 얼마 남았어?"(자기 분야 미지원 조합) → not_mine 이 아니라 소비 에이전트의 unsupported/되묻기.
- S2-2 not_mine 뒤 라우터 재분류가 다시 소비를 가리킴 → 재전달 없이 확인 질문. 라우터 분류 실패 → 소비 되묻기 · 작업 그대로.
- S2-3 소비 에이전트 오류 → failed(not_mine 아님), 상태 보존.
- S2-4 라우터가 "무엇부터 할까요?" 물은 중 답 → 담당 에이전트가 아니라 라우터가 먼저 처리.
- S3 예산 승인 대기 중 [소비 분석] 버튼 → 예산 보류 안내 → 분석 → "아까 조정 계속하자" → 서버 확인 뒤 resume(확인 실패면 상태 그대로).
- S4 예산 진행 중 "새로 할래" → cancel 먼저 → 서버 canceled 확인 → 새 start 한 번만.
- S5 [청년 정책] 대역: start → awaiting_input → "그만" → cancel → ended. 대역 failed → 기존 상태 보존.
- S6 예산 저장 중(saving) 정책 버튼 → saving 상태를 바꾸지 않고 안내, 저장 성공은 서버 확인 때만.
- S7 소비 DB 오류 · LLM 실패 → 상태 보존. S8 두 대화 분리.
- S9 C(부분 자료) → "그럼 식비는?" 이어받기, "왜 늘었어?" evidence_incomplete (22차 동작 유지).

분리 후 실제 LLM 재평가 항목
- 단계 A(프롬프트 그대로): 저장 응답 replay 로 결정 · 상태 동일성(호출 0). 지금 평가 결과(dev v3 · new42)는 보존.
- 단계 B(프롬프트 분리): ① 라우터 분야 분류(청년 정책 포함 새 사례) ② 소비 에이전트의 자기 분야 판단(not_mine 과 미지원 · 되묻기 구분) ③ 되묻기 중 답 / 새 요청 경계(n10 · s8 유형) ④ 버튼 뒤 자연어 ⑤ 전환 문장의 재분류 ⑥ 정책 · 예산 진행 중 취소. 새 사례는 프롬프트를 쓰지 않은 사람이 작성, 기대값 실행 전 고정, 새 장부 · 상한.

### 11-7. 병행 개발 범위 (공통 계약 + 소비 분리 이후) [제안]
- 라우터: 의도 · 전환 · 보류 · 재개 · 취소 · 버튼 진입 · 라우터 프롬프트. 예산 · 정책은 대역으로 전부 검증 가능.
- 소비 분석: 유형 · 답변 · 카드 매핑 · 설명 · 소비 프롬프트. 라우터 없이 `agent.handle` 단독 테스트(fixture DB).
- 예산 조정: 담당 모듈이 계약의 handle 과 서버 `task_status` 를 구현. 라우터 쪽 서버 확인 규칙은 FakeServer 로 이미 검증 중.
- 청년 정책 추천: 담당 모듈이 계약 구현 + 데이터 출처 결정. 라우터 연결은 대역으로 먼저 검증.
- 공통 수정 파일(한 사람이 소유, 계약 변경은 MEMO/명세 먼저): 계약 초안(이 MEMO → 이후 docs/api), `router_core/agents.py`(에이전트당 한 줄), `chat/router.py` · `chat/api.py` · `docs/api/chat.md`(통합 담당), `chat/models.py`(DB 담당만 · makemigrations 는 김나은만).
- 충돌 방지: 에이전트는 자기 패키지 안에서만 작업, 서로 import 금지, 라우터 소유 상태에 쓰지 않음, 계약 칸 추가는 선택 칸으로만.
- 대역(연결 계약 검증용 최소 동작만, 24차 정정): FakeBudgetAgent · FakePolicyAgent — 테스트가 정한 문장에 정해진 status 를 돌려준다(start → awaiting_input + work_ref, continue → replied · ended · not_mine · unsupported 중 지정값, cancel → ended, 고장 모드 failed). 자기 분야 판별 · 추천 · 조정 내용은 흉내 내지 않는다. FakeServer(기존)는 예산 서버 확인용 그대로.

### 11-8. 예산 주기 · 조회 범위 표시 개선안 [제안]
- 지금: "이번 예산 기간(10월 25일~29일) 식비 예산 350,000원 중 …" → 조회 범위를 예산 기간 전체처럼 보이게 한다.
- 제안: "이번 예산 기간(10월 25일~11월 24일) 중 10월 29일까지 식비 예산 350,000원 중 60,000원을 썼고 290,000원 남았어요."
  `cycle_period`(끝 미포함 → 표시는 last_day) 와 `spent_period.last_day` 를 함께 쓴다. answer.py 템플릿만 바꾸면 되고 계산 변경 없음. 승인 예산 없음 문장도 같은 방식.

### 11-9. 결정 기록

**쓰는 API · 함수 (분리 뒤에도 그대로)**
- 계산: `spending_analysis.periods · budget.approved_budget · coverage.coverage · analyze.analyze · result`, 답변 `answer.compose`, 기록 요약 `adapter.summarize · jsonable`.
- 라우터: `route` · `record_budget_task` · `pop_queued_start` 의 예산 규칙, 서버 `task_status` 어댑터 계약.
- 공개 계약: `chat.router.route_text · route_choice · INTENTS · CHOICES`.

**필요한 것**
- [미정] 청년 정책 의도 코드 · `choice` 값 · chat.md 의도 표 변경(API 합의). 추천: `intent: "policy"`, `choice: "policy"` — 기존 값은 그대로.
- [미정] 첫 채팅 시작 버튼 전달 방법. 추천: 화면 고정 버튼이 기존 `choice` 를 보내게 하고(API 변경 최소), 정책 버튼은 위 합의 후.
- [미정] 동시에 보류할 작업 수. 추천: 에이전트당 1개(예산은 [확정] 대화당 1개 그대로), 소비 분석은 보류 작업 없음.
- [미정] 끝난(archived) 대화의 작업 재개. 추천: 재개하지 않고 새 대화에서 서버 상태로 안내(예산 저장 결과는 서버에 있음).
- [미정] 청년 정책 진행 중 '취소'의 뜻(질문만 / 추천 대화 종료) — 담당자와. 라우터는 취소 대상 질문 규칙을 같이 쓴다.
- [미정] 라우터가 예산 보조값(direction · amount · hints)을 계속 뽑을지 — 예산 담당자 결정. 쓰지 않으면 원문만.
- [미정] 대화 상태 영속(`Conversation.slots` JSON 모양 · 크기 · 만료), `active_agent` 코드값.
- [미정] `big`(큰 지출 상담, 3차)과 청년 정책 추천의 관계(README 상 '동의 기반 복지 검색'과 겹칠 수 있음).
- 외부 에이전트에 넘기는 소비 맥락은 확정 근거 요약만(22차 evidence 규칙) — 예산 담당자 확인 필요.

**임시 결정 (이번 설계에서 고른 것, 구현 전)**
- [제안] 이름 · 위치: 메인 라우터는 `chat/router_core` 유지, 소비 에이전트는 `spending_analysis` 안(`agent.py` · `rules.py` 추가). 새 패키지 · 프레임워크 없음.
- [제안 · 24차 정정] 단계 A 는 통합 프롬프트 그대로(통합 결과를 소비 에이전트 · 라우터 재분류에 재사용 — 임시). 단계 B 에서 역할별 프롬프트 분리 + 재평가.
- [제안 · 24차 정정] 전환이 결정된 뒤에만 소비 되묻기 상태를 정리하고, 직전 분석(조건 · 근거)은 유지. not_mine 을 돌려줄 때 에이전트는 자기 상태를 바꾸지 않는다.
- [확정 유지] 실패 시 상태 보존, 서버 확인만으로 저장 성공 판단, 취소 먼저 새 작업 한 번, 대화별 상태 분리, 원래 추출값과 규칙 적용값 분리(months_requested · months_applied), 부분 자료 · 자료 없음 · 0원 구분, 승인 예산 없음 · 조회 실패 구분, 후속 조건과 확정 근거 구분.

### 11-10. 24차 정정: 담당 에이전트 우선 입력 흐름 [제안 — 담당자 협의 전]

바뀐 점: 23차의 "라우터가 매번 먼저 해석" · `handback.interpretation`(not_mine 이 목적지 해석을 들고 옴) · "전환 추가 호출 0" 을 폐기한다.
not_mine 은 '내 분야가 아님'만 알린다. 목적지는 라우터가 원문과 최소 맥락으로 다시 판단한다.

**입력 흐름**
```
사용자 입력 1건 (input_id 하나)
│
├─ 기능 버튼
│    라우터가 목적지 확정 (라우터 분류 LLM 없음)
│    ├─ 목적지 = 현재 담당      → 그 에이전트에 entry=button 전달
│    └─ 목적지 ≠ 현재 담당      → 전환 규칙(현재 작업 보류 · 저장 중 안내, 삭제 · 완료 처리 없음)
│                                  → 목적지에 보류 작업 있음: 재개/새로 질문(라우터 질문)
│                                  → 없음: entry=button, mode=start
│
├─ 문장 + 라우터 질문 대기 중 (의도 선택 · 취소 대상 · 재개/새로 · 목적지 확인)
│    라우터가 먼저 처리: 선택지 · 코드 매칭 → 아니면 라우터 LLM
│    ├─ 질문의 답        → 그 답대로 연결
│    ├─ 새 요청          → 아래 '담당 없음'과 같은 분류 (대기 질문은 새 요청이 확정된 뒤 정리)
│    └─ 판단 실패        → 질문 · 작업 그대로, 같은 질문 다시
│
├─ 문장 + '취소' 단어
│    기존 라우터 코드 규칙 (질문 취소 / 작업 취소 / 둘 다 있으면 대상 질문)
│
├─ 문장 + 담당 없음
│    라우터 분류
│    ├─ 목적지 하나      → mode=start
│    ├─ 여러 분야        → 무엇부터 할지 라우터 질문
│    ├─ 불분명 · 범위 밖 → 라우터 안내 · 선택지
│    └─ 분류 실패        → 상태 보존, 실패 안내
│
└─ 문장 + 담당 있음
     담당 에이전트에 먼저 전달 (mode=continue)
     ├─ replied · awaiting_input · unsupported · ended → 결과 반영
     ├─ failed      → 상태 보존, 실패 안내 (not_mine 으로 바꾸지 않음)
     └─ not_mine    → 라우터 재분류 (원문 + 최소 맥락 + declined_by)
          ├─ 다른 에이전트로 정해짐  → 전환 규칙 적용 → 새 담당 start (같은 input_id)
          │     └─ 새 담당도 not_mine → 더 보내지 않고 목적지 확인 질문
          ├─ declined_by 를 다시 가리킴 → 재전달하지 않고 목적지 확인 질문
          ├─ 불분명                → 목적지 확인 질문
          └─ 분류 실패              → 기존 작업 · 담당의 되묻기 상태 그대로, 실패 안내
```

**재전달 제한** [제안]
- 같은 `input_id` 는 에이전트에 최대 2번(현재 담당 1 + 재분류로 정해진 새 담당 1)만 전달한다.
- not_mine 을 돌려준 에이전트(`declined_by`)에는 같은 입력을 다시 보내지 않는다.
- 제한에 걸리면 라우터 확인 질문으로 바꾼다: "어떤 걸 도와드릴까요?" + 지금 작업 계속 · 기능별 선택지. 그 답은 라우터 질문의 답으로 처리.
- 확인 질문이 정해지기 전에는 기존 작업 · 담당 · 되묻기 상태를 바꾸지 않는다.

**not_mine · 미지원 · 되묻기 · 오류 구분**
| 경우 | 누가 | 결과 |
|---|---|---|
| 다른 분야 요청 | 담당 에이전트 | not_mine (목적지 없음, 자기 상태 그대로) → 라우터 재분류 |
| 자기 분야 · 지원 안 함 (예: 소비 '지난달 단독', 카테고리끼리 비교) | 담당 에이전트 | unsupported + 안내 |
| 자기 분야 · 정보 부족 (예: 개월 수 0, 모르는 카테고리) | 담당 에이전트 | awaiting_input (되묻기) |
| 에이전트 내부 오류 · 조회 실패 | 담당 에이전트 | failed (상태 보존) |
| 라우터가 직접 한 질문의 답 | 라우터 | 라우터가 먼저 처리 |

- 예산 · 청년 정책도 같은 결과 계약을 쓴다. 자기 분야 판별 방법 · 프롬프트 · 되묻기는 각 담당자 설계 범위.
- 소비 분석에서 지금 router_core 의 `unsupported`(last_month_only · category_compare) · `ask`(months · category · scope · budget_current · detail) 는 그대로 소비 에이전트의 unsupported · awaiting_input 이 된다.

**이번에 확정하지 않은 것 (기존 [미정] 그대로)**
- 청년 정책 의도 · choice 값, 동시에 보류할 작업 수, 끝난 대화 재개, 정책 진행 중 취소의 뜻, 예산 보조값 유지, 상태 영속 형식 — 11-9 참고.
- 재전달 최대 2회 · 목적지 확인 질문 문구 · `input_id` 생성 위치(API 의 Idempotency-Key 재사용 여부)도 [제안] 단계.

## 12. 단계 A 구현: 메인 라우터 / 소비 분석 에이전트 분리 (25차, 2026-10-29)

기존 통합 프롬프트(해시 6a3bb2a3)와 소비 계산 · 답변 · fixture · 기대값을 그대로 두고 코드 책임만 나눴다.
chat/router.py · api.py · replies.py · config/ · 모델 · 마이그레이션 · 의존성은 바꾸지 않았다. 실제 LLM · 에이전트 호출 없음.

**쓰는 API · 함수 (바뀐 위치)**

| 책임 | 위치 |
|---|---|
| 공용 해석 형식 · 통합 프롬프트 · 한 턴의 결정 형식 [임시 · 단계 A] | `chat/interpretation/` — `schema.py` · `llm.py`(router_core 에서 내용 그대로 이동, 해시 동일) · `decision.py`(Decision + agent · declined · interpret 칸) |
| 메인 라우터 | `chat/router_core/__init__.py` — route · route_button · reroute · run_turn · current_question · record_budget_task · pop_queued_start · MemoryStateStore |
| 라우터 규칙 | `chat/router_core/rules.py` — destination · budget_request · merge_intent · ROUTER_QUESTIONS |
| 소비 대화 계층 | `spending_analysis/dialog.py` — step · start_button · cancel_question · decide · merge · overlay · _explain · record_analysis · run_items (router_core/rules.py · __init__.py 에서 규칙 그대로 이동) |
| 소비 에이전트 진입 | `spending_analysis/agent.py` — handle(decision, user, store, conversation_id, today, as_of) → 공통 결과 |
| 소비 계산 · 답변 · 기록 요약 | `spending_analysis/periods · budget · coverage · analyze · result · answer · adapter` (adapter 는 import 만 dialog 로) |

- 의존 방향: router_core → chat.interpretation, spending_analysis.dialog / spending_analysis → chat.interpretation. 에이전트는 router_core 를 import 하지 않는다(순환 없음). 외부 에이전트는 run_turn 의 agents 인자로만 연결.

**기능 이동표 (실제 이동)**
- 라우터에 남김: 라우터 질문(의도 선택 · 목적지 확인 · 취소 대상 · 예산 작업 이어서/새로) 먼저 처리, 취소 단어, 목적지 분류, 전환(보류) · 담당 관리, 예산 작업 시작 · 이어 받기 · 재개 · 취소 먼저 · 서버 확인 · 새 작업 한 번 시작, 정책 작업 기록.
- 소비 에이전트로 이동: 유형 1~9 · 개월 수 제한 · 되묻기와 답 합치기(months · category · scope · explain_target · budget_current) · 목록 선택 코드 처리 · 직전 조건 이어받기 · 전체 범위 전환 · 설명 근거 확인 · '바뀐 예산으로' 안내 · 분석 기록 · 자기 분야 판단(not_mine).
- 새로 생김: 담당 우선 전달, not_mine 재분류, 같은 입력 전달 2회 제한 · 목적지 확인 질문, 버튼 진입, run_turn(독립 실행 · input_id · 실패 복구), 정책 연결(대역).

**공통 입력 · 결과 계약 (구현본, 담당자 협의 전 초안)**
```
입력 (라우터 → 외부 에이전트, run_turn 이 input_id · today · as_of 를 붙인다)
{
  "agent": "budget" | "policy",
  "mode": "start" | "continue" | "resume" | "cancel",
  "entry": "text" | "button",
  "text": 원문 | null,
  "input_id": "...",
  "conversation_id": "...",
  "today": "YYYY-MM-DD" | null,
  "as_of": "ISO 8601" | null,
  "work_ref": 앞서 받은 재개 참조 | null,
  "context": {"conversation_id", "switched_from": null, "declined_by": 거절한 에이전트 | null, ...},
  # 예산만 [임시 호환]: "task", "hints", context.selected_from · last_analysis · active_task · task_state? · cancel_for?
}
+ 같은 프로세스 인자 interpret (임시 · 단계 A: 통합 해석 함수, 한 번만 호출)

소비 분석 에이전트 [임시 · 단계 A]: 입력 dict 대신 Decision 을 받는다 (handle(decision, user=, store=, conversation_id=, today=, as_of=))

결과 (에이전트 → 라우터)
{
  "status": "replied" | "awaiting_input" | "unsupported" | "ended" | "failed" | "not_mine",
  "reply": {...} | null,
  "work_ref": 재개 참조 | null,
  "ended_reason": "done" | "canceled" | null,
  "error": 코드 | null,
  "reason": 미지원 · not_mine 이유 | null
  # 소비만 더: "question" {asking, ask_about, options, reason} · "explain" (참조 근거)
}
```

**확정 사항 (이번 구현에서 지킨 규칙)**
- 라우터 질문이 있으면 라우터가 먼저 처리. 담당이 있으면 담당이 먼저 받고, 없으면 라우터가 분류. 버튼은 분류 LLM 없음.
- not_mine 은 목적지가 아니다: 라우터가 원문 + 최소 맥락으로 재분류. 에이전트 오류 · 계약 밖 결과는 failed(not_mine 아님).
- 같은 입력은 최대 2번(현재 담당 + 새 담당). 거절한 에이전트 재지정 · 두 번째 not_mine → 목적지 확인 질문(asking=intent, reason=destination_unclear), 상태는 이번 입력 전으로.
  이 제한은 한 입력 안의 재전달만 막는다(서버 저장 · 외부 작업 중복 방지가 아님).
- 전환 시 다른 에이전트의 미완료 작업 · 소비 되묻기는 보류만(삭제 · 완료 처리 없음). 예산은 suspended, 정책은 work.suspended, 소비 되묻기는 소비 칸에 남는다.
- 소비 분석 한 번 끝남 ≠ 대화 맥락 끝남: 담당 · 직전 분석이 남아 "그럼 교통비는?" 이 이어받는다.
- 새 담당 호출 실패(failed) · 소비 전 항목 조회 실패 → run_turn 이 이번 입력 전 상태로 되돌린다(보류도 원래대로, 새 작업 기록 없음). 해석 실패 · 서버 확인 실패는 기존처럼 상태 그대로.
- 예산: 저장 성공 = 서버 saved + saved_ref, 취소 먼저 → 서버 canceled 확인 → 보관 요청 start 한 번 (run_turn 이 pop_queued_start 를 부른다).
- 청년 정책은 현재 프롬프트에 의도가 없어 문장으로는 분류하지 않는다(범위 밖으로 나옴). 버튼 + 대역으로만 연결을 검증했다 — 정책 분류가 검증된 것이 아니다.

**임시 호환 처리 (단계 B 또는 담당자 합의 때 없앤다)**
- 통합 해석 재사용: `_Interpret` 가 한 입력에 LLM 을 한 번만 부르고 결과를 담당 에이전트의 자기 분야 판단 · 라우터 재분류에 같이 쓴다. 그래서 호출 수 · LLM 입력이 분리 전과 같다.
- 라우터가 통합 프롬프트 입력(previous)과 예산 전달 맥락(last_analysis)을 만들려고 소비 직전 조회를 읽는다(쓰지 않음).
- 예산 전달 dict 에 기존 칸(task · hints · selected_from · last_analysis · active_task · task_state · cancel_for)을 같이 둔다.
- 예산 에이전트 대역(test_router_core · router_eval 의 budget_double_declines): 진행 중 작업에 온 문장을 통합 해석으로 보고 "followup=new 이고 예산 조정 아님"이면 not_mine — 분리 전 라우터 판단을 흉내 냄. 실제 예산 에이전트의 판별은 담당자 몫.
- 소비 에이전트는 공통 입력 dict 가 아니라 Decision 을 같은 프로세스에서 받는다.
- 평가 도구 fixture 의 22차 이전 last_analysis(evidence 없음)를 confirmed 로 읽는다, 가짜 분석 결과에 evidence=confirmed (아래 회귀 참고).

**검증 (격리 설정 spending_fixture.settings, 테스트 DB test_money_cushion_router_fixture)**
- chat.test_router_core 75 통과 — 기대값 그대로. 읽는 방법만 변경: 대기 질문 current_question(), 예산 전달은 기존 칸만 비교(legacy) + 새 계약 칸 따로, 예산 진행 중 입력은 대역 판단 뒤 reroute.
- chat.test_router_agents 20 통과(새): 완료 후 후속 질문, 되묻기 중 전환 · 보류 · 버튼 복귀 후 답, 자기 분야 미지원 비전달, 예산 우선 → not_mine → 재분류(LLM 1회), 두 번째 not_mine · 거절 에이전트 재지정 → 목적지 확인 질문 + 상태 복구, 라우터 질문 우선, 분류 실패 · 에이전트 실패(failed · 예외 · 계약 밖) 복구, 소비 실패 시 예산 보류 복구, 예산 saved_ref · 취소 먼저 · start 한 번, 버튼 LLM 0 · 시작 안내, 보류 작업 + 버튼 → 기존 질문, 라우터 질문 중 버튼, 정책 대역 계약 · 취소 전달 · 보류 · 미정 재시작, 정책 문장 비분류, input_id, 대화 분리.
- chat.tests 9 · spending_fixture 95(예상된 실패 1 = budget_data 기존 기록) 통과. test_conversation 은 run_turn + 소비 agent.handle 경로로 바꿔도 기대 문장 그대로.
- router_eval 테스트 19 통과. 저장 응답 replay(API 호출 0)와 정답 추출 dry-run 을 분리 전후로 비교:

| 묶음 | 비교 단위 | 분리 전 | 분리 후 | 사례 · 턴별 차이 (calls · kind · route · state · llm) |
|---|---|---|---|---|
| dev v3 저장 응답 | 28 | ok 28 | ok 28 | 0 |
| new42 저장 응답 (단일 12 + 연속 22턴) | 34 | 단일 11/12 · 연속 4/5 (n10 · s8 2턴 · 4턴 추출) | 같음 | 0 |
| 정답 추출 dry-run (전 split + 연속 8) | 84 | 52/52 · 32/32 | 같음 | 0 |

- 기존 평가와 비교 가능한 항목: 위 84 단위 전부(같은 저장 응답 · 같은 기대값). 연속 대화 replay 는 이번에 도구에 추가(시나리오#턴 키, 저장 응답 없는 턴에서 호출이 생기면 call_failed 로 드러남).
- 구조 변경으로 달라진 항목(평가 사례에는 아직 없음): ① route() 단독 결과 — 예산 진행 중 입력은 이제 예산 전달로 끝나고 재분류는 run_turn · 에이전트 결과에 달림(위 비교는 대역이 분리 전 판단을 흉내 내서 같음) ② 전환 시 소비 되묻기 보존(분리 전에는 지워짐) ③ 예산 진행 중 새 예산 요청은 라우터의 '이어서/새로' 질문이 아니라 예산 에이전트가 먼저 받음(보류 작업일 때만 라우터 질문) ④ 버튼 · 정책 · 목적지 확인 질문 · 실패 복구는 새 동작.
- 22차 회귀 발견 · 수정: 설명 근거를 evidence=confirmed 로 바꾼 뒤 평가 도구의 가짜 분석 기록 · 사례 fixture 에 evidence 가 없어 "왜 늘었어?" 3건(d23 · h09 · s7 3턴)이 route 실패였다(22차에 평가 도구 테스트를 안 돌려 놓침). cases.json 은 그대로 두고 하네스만 고침 → 17차 결과와 같아짐.

**필요한 것 · 남은 작업**
- [미정 — 질문] 소비로 돌아와 새 소비 요청을 하면 보류된 소비 되묻기를 어떻게 할지. 지금(임시): 문장으로 돌아오면 새 요청이 질문을 대신함(기존 '새 요청이면 질문을 바꾼다' 규칙), 버튼으로 돌아오면 질문을 남기고 다음 문장을 그 질문 맥락으로 해석.
- [미정 — 질문] 보류된 정책 작업이 있을 때 정책 버튼(이어서 / 새로). 지금: 상태를 바꾸지 않고 reason=policy_resume_undecided.
- [미정 — 질문] 동시에 보류할 작업 수. 지금: 구조상 에이전트당 1개(예산 = 확정 규칙, 정책 1), 전체 상한 없음.
- [확인 필요] 예산 진행 중 새 예산 요청을 예산 에이전트가 먼저 받는 것(구조 변경 ③) — 예산 담당자와.
- 소비 되묻기 · 미지원 · 설명 답변 문장 템플릿(지금은 질문 칸 · 이유 코드만, 시작 안내만 문장), 라우터 안내 코드(budget_task_suspended · policy_work_suspended · destination_unclear) 문장.
- 테스트 파일 이동(소비 규칙 테스트를 spending_analysis 쪽으로)은 아직 — 지금은 chat/test_router_core.py 에 그대로.
- 단계 B: 라우터 프롬프트(분야 분류 · 정책 의도 · 라우터 질문 답) / 소비 프롬프트(자기 분야 판단 · 세부 해석 · 되묻기 답) 분리, `_Interpret` · context.interpretation · 예산 대역 흉내 · last_analysis 직접 전달 제거, 새 사례로 실제 LLM 재평가(11-6).
- 통합: chat/router.py route_text · chat/api.py 연결, 상태 영속 저장소(Conversation.active_agent · slots), input_id 를 Idempotency-Key 와 연결할지.

## 13. 단계 B: 역할별 입력 · 프롬프트 분리 + 오프라인 검증 (26차, 2026-10-29)

실제 LLM · 에이전트 · 외부 API 호출 없음. chat/router.py · api.py · replies.py · config/ · 모델 · 마이그레이션 · 의존성 변경 없음.

**쓰는 API · 함수 (바뀐 것)**

| 책임 | 위치 |
|---|---|
| 라우터 해석 (분야 분류 · 라우터 질문 답 · 재개, [미정 유지] 예산 보조값) | `chat/router_core/reading.py` — RouterReading · parse · PROMPT(해시 19c9f8c9) · read(text, pending, complete, context) |
| 라우터 결정 형식 · 규칙 | `chat/router_core/rules.py` — Decision(라우터) · destinations · budget_request · merge_intent |
| 라우터 흐름 | `chat/router_core/__init__.py` — route · reroute · route_button · run_turn · record_budget_task · pop_queued_start |
| 소비 해석 (mine = 자기 분야 여부 + 소비 세부 조건) | `spending_analysis/reading.py` — SpendingReading · parse · PROMPT(해시 a7547d3d) · read(text, pending, complete, previous, chosen) |
| 소비 대화 계층 | `spending_analysis/dialog.py` — Decision(소비) · step(원문 → 자체 해석) · summary · publish · start_button · cancel_question · record_analysis |
| 소비 에이전트 진입 | `spending_analysis/agent.py` — handle(공통 입력, store, complete, user, execute) |
| 분리 전 형식 보존 · 변환 (테스트 · replay 전용) | `chat/interpretation/` — schema.py · llm.py(통합 지시문 6a3bb2a3 그대로) · legacy.py(to_router · to_spending · RoleFakeLLM · view · SAID) |

- 단계 A 임시 결합 정리: 통합 해석 함수(_Interpret) 전달 · 공유 추출 재사용 · 공용 Decision(chat/interpretation/decision.py) · 예산 대역의 라우터 판단 흉내 · 소비 에이전트의 '미리 만든 Decision 받기' 를 없앴다.
- 의존: router_core → (spending 없음, 에이전트는 run_turn 의 agents 인자로만) / spending_analysis → chat 없음(서비스 코드). 소비 맥락은 소비가 store.save_shared(cid, "spending", 요약) 로 공개하고 라우터 · 다른 에이전트는 그것만 읽는다.

**공통 입력 · 결과 계약 (구현본, 담당자 협의 전 초안)**
```
입력 (라우터 → 에이전트, run_turn 이 input_id · conversation_id · today · as_of 를 붙인다)
{
  "agent": "spending" | "budget" | "policy",
  "mode": "start" | "continue" | "resume" | "cancel",
  "entry": "text" | "button",
  "text": 원문 | null,
  "input_id": "...",
  "conversation_id": "...",
  "today": "YYYY-MM-DD" | null,
  "as_of": "ISO 8601" | null,
  "work_ref": 앞서 받은 재개 참조 | null,
  "context": {
    "conversation_id", "switched_from": null, "declined_by": 거절한 에이전트 | null,
    소비: "selected_from": 라우터 질문 선택지 | null, "budget_change": {"outcome", "checked_at"} | null,
    예산: "selected_from", "shared": {"spending": 소비 요약 | null}, "active_task", "task_state"?, "cancel_for"?
  },
  예산만 [임시 호환]: "task": "budget_adjust", "hints": {[미정 유지] 보조값}
}

결과 (에이전트 → 라우터)
{
  "status": "replied" | "awaiting_input" | "unsupported" | "ended" | "failed" | "not_mine",
  "reply": {...} | null,
  "work_ref": 재개 참조 | null,
  "ended_reason": "done" | "canceled" | null,
  "error": 코드 | null,
  "reason": 미지원 · not_mine 이유 | null
  소비만 더: "detail" {items, asking, ask_about, options, reason, notices, skipped, explain} (JSON),
             "decision" (같은 프로세스 진단 · 테스트용 원본 — 계약 칸 아님)
}

소비 요약 (store.get_shared(cid, "spending"))
{"has_question": bool, "previous": 직전 조회 조건 | null,
 "items": [{type, category, ok, evidence, limits, period, compare_period?, as_of, facts(evidence=confirmed 일 때만)}]}

LLM 입력
라우터: {"text", "pending": 라우터 질문 {asking, options} | null,
         "context": {"active", "budget_task": {phase, suspended} | null, "previous_spending": 소비 요약의 previous | null, "declined_by"}}
소비:   {"text", "pending": 소비 되묻기 {asking, options} | null, "previous": 직전 조건 | null, "chosen": bool}
```

**확정 사항 (이번 구현에서 지킨 규칙)**
- 담당 우선: 담당이 있으면 원문을 먼저 담당에게(라우터 LLM 없음). 예산 진행 중 입력은 예산 에이전트에 원문 그대로 — 같은 분야 판단 · 조건 변경 · 새 작업 여부 · 되묻기는 예산 담당자 몫.
- not_mine → 라우터가 원문 + 최소 맥락(+ declined_by)으로 재분류(라우터 LLM 1회). 라우터가 직접 분류해 보낸 에이전트가 거절하면 다시 분류하지 않고 목적지 확인 질문. 같은 입력 전달 최대 2회.
- 자기 분야 미지원(unsupported) · 정보 부족(awaiting_input) · 해석 오류(failed)는 not_mine 과 구분 — 소비는 mine=true 인데 지원하지 않거나 정보가 부족하면 직접 안내 · 되묻기.
- 라우터 질문(의도 선택 · 목적지 확인 · 취소 대상 · 이어서/새로)은 라우터가 먼저. 사용자가 라우터 질문에서 소비를 고르면 소비는 chosen=true 로 원래 문장의 소비 부분만 해석.
- 버튼: 라우터 분류 LLM 0회(에이전트 내부 호출은 에이전트 몫). 전환 · 보류 규칙은 자연어와 같다.
- 실패 시 상태 보존: 라우터 해석 실패 · 서버 확인 실패는 상태 그대로, 에이전트 failed 는 run_turn 이 이번 입력 전으로 되돌린다. not_mine 을 돌려준 에이전트는 자기 상태를 바꾸지 않는다.
- 예산 서버 저장 확인 · 취소 확인 · 보류 · 재개 · 취소 먼저 새 작업 한 번 시작 규칙 그대로.
- 청년 정책은 라우터 프롬프트에 의도를 넣었지만 실제 LLM 평가 전이다. 연결은 가짜 분류 결과(intents=["policy"]) + 대역으로만 확인했다.

**임시 호환 처리 · 남은 임시 결정**
- [미정 유지] 라우터가 예산 보조값(categories · direction · amount · amount_mode)을 뽑아 hints 로 넘긴다 — 예산 담당자 결정 전까지 기존 그대로.
- [임시 · 미정] 소비로 돌아와 새 소비 요청(문장)을 하면 보류된 소비 되묻기를 그 요청이 대신한다. 버튼으로 돌아오면 질문을 남기고 다음 문장을 그 질문의 맥락으로 해석.
- [미정] 보류된 정책 작업이 있을 때 정책 버튼 · 정책 분류 → 상태 그대로, reason=policy_resume_undecided. 동시에 보류할 작업 수는 구조상 에이전트당 1개, 전체 상한 미정.
- [임시] 예산 전달 dict 의 task · hints · active_task · task_state · cancel_for 기존 칸 유지.
- [진단 · 테스트용] 소비 결과의 "decision", current_question()(소비 내부 칸을 읽음), run_turn agents 값 None = 연결 없는 대역(전달까지만), 소비 handle 의 execute 가 None 을 돌려주면 실행 생략.
- [테스트 · replay 전용] chat/interpretation/legacy.py 의 통합 → 역할별 변환: '역할별 모델이 같은 뜻으로 답했다'는 가정. mine = chosen · 소비 되묻기 답/취소/불분명 · (소비 있고 예산 조정 없음).

**검증 (격리 설정 spending_fixture.settings, 테스트 DB test_money_cushion_router_fixture)**
- 전체 207 = 통과 206 + 예상된 실패 1(budget_data 기존 기록), 실패 0. router_eval 테스트 20 통과.
  - spending_analysis.test_dialog 43 (옮긴 소비 규칙 23 + 맥락 17 + 새 3: not_mine 상태 불변 · 미지원/정보 부족/오류 구분 · 요약의 확정 근거만)
  - chat.test_router_core 36 (옮긴 라우터 11 + 맥락 1 + 예산 작업 23 + 라우터 형식 오류 1) — run_turn + legacy.view, 예산 대역은 정해 둔 문장만 not_mine · 그 밖에는 전달까지만
  - chat.test_router_agents 24 (담당 우선 · 재분류 호출 수 · 왕복 제한 · 실패 복구 · 버튼 · 정책 가짜 분류 · input_id · 대화 분리 · 소비 요약 맥락)
  - chat.tests 9 · spending_fixture 95(대화 연결 14 포함, 기대 문장 그대로)
- 기존 기대값 변경 (이유 있는 것만, 나머지는 그대로):
  ① 예산 전달의 소비 맥락 last_analysis → shared.spending (계약 변경, 4곳) ② 소비 되묻기 중 "5만 원만큼": failed → 같은 질문 다시(ask) — 소비 해석에는 금액 칸이 없음. 대기 상태 보존 확인은 그대로, 라우터 쪽 형식 오류(failed)는 별도 테스트로 유지 ③ 소비 결정에 handoff 칸이 없어 "handoff=None" 확인 한 줄 제거 ④ LLM 입력 확인은 라우터 호출만 골라서(값 그대로).
- 저장 응답 replay(API 0) · dry-run — 통합 원문을 역할별로 변환해 새 구조에 넣음:

| 묶음 | 단위 | 비교 가능 | 비교 가능 단위의 결과 차이 (kind · 결정 · 상태 · 추출) | 호출 수(비교 가능) A → B | 구조 변경 (턴 시작 때 예산 진행 중) |
|---|---|---|---|---|---|
| dev v3 저장 응답 | 28 | 26 | 0 | 26 → 48 | d24 · d25 |
| new42 저장 응답 | 34 | 26 | 0 (n10 · s8 2턴 추출 오류는 그대로) | 25 → 42 | n11 · s5 3·4턴 · s6 2~5턴 · s8 4턴 |
| 정답 dry-run | 84 | 70 | 0 | 68 → 118 | 위 + h02 · h11 · s3 3·4턴 |

  - 구조 변경 항목: 원문이 예산 에이전트로 그대로 가서 결과가 예산 에이전트 판단에 달린다(평가 도구에서는 예산 연결 없음 → 전달까지만). 뒤 턴은 그 결과에 따라 이어진다.
  - 추출 채점은 통합 원문 그대로라 기존 점수와 같다. 역할별 프롬프트의 실제 추출 성능은 아직 모른다.
- 평가 도구: run.py 는 dry-run · replay(변환)만, live 는 13번 계획 확정 전까지 막음. 호출 어댑터 · 장부는 직접 테스트로 유지. 사례 fixture 의 보관 요청(통합 형식)도 질문한 쪽 형식으로 변환. 역할별 지시문에도 평가 문장 겹침 검사(알려진 3개만 겹침).

**실제 LLM 평가 계획 (단계 B, 아직 실행 안 함)**
- 준비: 역할별 기대값 형식(expect_router · expect_spending 의 must/allow), 역할별 구조화 출력 스키마(지시문별로 고르기), run.py live 경로(역할별 시도 기록 · 장부는 호출마다), 새 장부.
- 질문 범위
  A. 기존 사례 재채점(비교용): dev 28 + 연속 대화 s1~s3, new 12 + s4~s8 — 기존 통합 기대값을 역할별로 옮겨 적고 사람이 확인. holdout 12 는 참고 · 회귀용(실행 안 함, 오염 이력).
  B. 단계 B 새 사례(지시문을 쓰지 않은 사람이 작성, 실행 전 기대값 고정): 라우터 분야 분류(청년 정책 포함, 범위 밖 · 불분명, 복합) 약 12, 소비 mine 판단(다른 분야 · 복합 · 미지원 · 정보 부족 · 되묻기 답 · 취소) 약 12, 전환 연속 대화(되묻기 중 전환 · 예산 진행 중 원문 전달 → 대역 not_mine → 재분류 · 목적지 확인 질문 · 버튼 뒤 문장) 4개 × 4턴 안팎.
- 예상 호출 수(변환 dry-run 기준, 예산 대역 없음): A = dev+s1~s3 59 + new+s4~s8 42 = 101. B ≈ 라우터 단일 12 + 소비 단일 12~18(not_mine 이면 재분류 1 추가) + 연속 대화 16턴 × 약 1.6 ≈ 26 → 약 55. 합계 약 156.
- 호출 상한: 새 장부 limit 180 (실패도 상한에 포함, 재시도 · 모델 전환 없음). A 와 B 를 장부 하나로 나눠 실행하거나 A 101 · B 70 장부 둘.
- 비용 감(확정 아님): 17차 32호출 ≈ 입력 64.7k · 출력 5.2k 토큰 · $0.009. 역할별 지시문이 라우터 약 1.9k자 · 소비 약 3.4k자라 호출당 입력은 비슷하거나 작다 → 약 156호출이면 대략 $0.04~0.06 (실행 전 공식 단가로 다시 계산).
- 보고: 역할별 형식 · 추출 점수, 결정 · 상태(비교 가능 / 구조 변경 구분), not_mine · 미지원 · 되묻기 구분 정확도, 라우터 재분류 호출 수, 청년 정책 분류(새 사례 기준), 호출 · 지연 · 사용량 · 비용.

**남은 작업**
- 평가: 위 계획 확정 → 역할별 기대값 · 스키마 · live 경로 구현 → 실제 평가.
- 소비 되묻기 · 미지원 · 설명 · 라우터 안내 코드의 답변 문장, 설명 생성(근거는 evidence=confirmed 만).
- 통합: chat/router.py route_text · chat/api.py 연결, 대화 상태 영속(Conversation.active_agent · slots), input_id 와 Idempotency-Key 관계, 청년 정책 choice · intent 값(API 합의).
- 미정 정책 결정: 보류된 소비 되묻기 · 정책 작업 재진입, 보류 작업 수, 예산 보조값 유지 여부.

## 14. 역할별 실제 LLM 평가 준비 (27차, 2026-10-29 — 실제 LLM · 외부 API 호출 없음)

**쓰는 것**
- `scripts/router_eval/stage_b.py` — 역할별 평가 실행(기본 dry-run). 채점을 셋으로 나눈다: 역할별 해석(라우터 · 소비 각각 형식 · 추출) / 최종 결정(expect_route, legacy.view) / 상태(expect_state + 보류된 소비 되묻기 · 정책 작업).
  연속 대화는 이전 턴의 실제 결과 상태를 그대로 넘기고, 턴 사이에는 사례의 after(분석 실행 · 예산 결과 대역)만 이어 붙인다.
- `scripts/router_eval/cases_b.json` (B-2026-10-29.1, sha fb159e29) — 역할별 기대값(expect_router · expect_spending, 없으면 그 역할은 불리면 안 됨), doubles(예산 · 정책 대역 결과), policy_pending.
  기존 cases.json(cffc9d75) · 저장 결과는 그대로. `make_cases_b.py` 로 다시 만들면 같은 파일이 나온다(테스트로 확인).
- 역할별 Structured Outputs 스키마(stage_b.role_schema — 라우터 · 소비, strict), `openai_rest.ResponsesClient` 의 schema 에 '지시문 → (이름, 스키마)' 함수 허용.
- live: 기본 꺼짐. --live + --max-calls + --ledger + ROUTER_EVAL_LIVE=1 + 키 · 모델이 모두 있어야 한다. 시도마다 장부를 먼저 늘리고(실패 포함), 재시도 · 모델 전환 없음, 인증 · 권한 · 모델 · 요청 · 한도 오류면 남은 호출 중단(aborted). 지시문 고정은 --router-sha · --spending-sha.
- `run.py` 는 분리 전 사례의 변환 replay(26차)로 그대로 둔다.

**사례 구분**
- regression_dev(dev 28 + s1~s3) · regression_new(17차 new 12 + s4~s8): 회귀용. reference(holdout 12): 참고용, 기본 실행 안 함.
- new_b(단계 B 새 단일 8 + 연속 대화 2): 프롬프트 작성자와 같은 작성자 → 독립 평가 아님(independent=false). 실행 전 기대값 고정.
- 정책 미정 단위는 분모에서 빼고 따로 보고: d28 · n06 · bs1 3턴(보류된 소비 되묻기로 돌아와 새 요청) · bs2 3턴(보류된 정책 재진입).
- 대역 의존 단위(double_dependent): d25 · n11 · s3 3턴 · s5 3턴 · s6 2턴(예산 대역 not_mine), b01 · bs1 3턴 · bs2 1~2턴(예산 · 정책 대역).
- 변환 규칙: 회귀 단위는 새 구조로 한 번 돌려 불리는 역할과 그때의 입력(되묻기 · chosen)으로 역할별 기대값을 만든다. 라우터 기대값의 예산 보조값은 예산 조정 요청일 때만(그 밖에는 비어 있어야 함). 턴 시작 때 예산 진행 중이고 분리 전 기대 결정이 소비 쪽이면 예산 대역 not_mine. 분리 전 결정 · 상태와 다르면 review — 이번 변환은 review 0.

**소비 되묻기 중 입력 (테스트로 확인)**
- "5만 원만큼"(불분명) → 소비 해석 mine=true · followup=unclear → 같은 질문, 상태 그대로.
- "예산을 5만 원 줄여줘"(명확한 다른 분야) → mine=false → not_mine → 라우터 재분류 → 예산 시작(보조값 그대로), 소비 질문은 보류만.
- "지난달보다 5만 원 더 썼어?"(금액이 든 소비 요청) → 새 소비 요청(6번), 질문 반복 아님.
- 소비 형식에 없는 칸(amount 등) · 형식 오류 → failed, 상태 그대로.
- 판단은 소비 해석(LLM) 결과에 달렸다 — 프롬프트는 바꾸지 않았다(이미 '새 요청이 소비가 아니면 mine=false', '답인지 모르면 unclear').

**예상 호출 수 (dry-run = 사례 파일 기대 역할 수, 일치 확인)**
| 묶음 | 라우터 | 소비 | 합계 |
|---|---|---|---|
| regression_dev (단일 28 + s1~s3) | 32 | 33 | 65 |
| regression_new (단일 12 + s4~s8) | 25 | 28 | 53 |
| new_b (단일 8 + bs1 · bs2) | 12 | 9 | 21 |
| 실행 대상 합계 | 69 | 70 | **139** |
| reference (참고, 기본 실행 안 함) | 10 | 7 | 17 |
- 정책 미정 단위도 호출은 나간다(채점만 제외). 호출 상한 제안: 새 장부 limit 160 (139 + 실패 여유 약 15%).

**검증**: Django 212 = 통과 211 + 예상된 실패 1(budget_data 기존 기록) / 평가 도구 30 통과 (기존 20 + stage_b 10).

**남은 결정**
- 실제 실행 승인: 묶음 · 상한(제안 160) · 모델 · 추론 강도 · Structured Outputs 사용 여부.
- 독립 평가가 필요하면 프롬프트를 쓰지 않은 사람이 새 사례를 작성.
- 기존 미정 정책(보류된 소비 되묻기 · 정책 재진입, 보류 작업 수, 예산 보조값 유지) — 정해지면 policy_pending 단위를 채점에 넣는다.

## 15. 역할별 실제 LLM 평가 실행 (28차, 2026-10-29)

**실행 조건 (실행 전 기록 — `<scratchpad>/router-eval-b160/pre-run.json`)**
- 모델 gpt-6-luna(이전 실제 평가 5 · 28×3 · 32호출에서 쓴 같은 ID, LLM_MODEL 로 확인), reasoning=low, 역할별 Structured Outputs, 재시도 0.
- 지시문: 라우터 19c9f8c9 · 소비 a7547d3d (--router-sha · --spending-sha 로 고정 확인). 사례 cases_b.json B-2026-10-29.1 (fb159e29), 원본 cases.json 2026-10-29.3 (cffc9d75).
- 새 장부 limit 160. 점검 b05 1단위(라우터 1 + 소비 1) → 형식 정상 확인 → 나머지 57단위(b05 제외, holdout 제외). 같은 장부, 중복 채점 없음.
- 결과: `<scratchpad>/router-eval-b160/` (check · rest 결과, 턴별 시간 · 호출 기록은 저장소 밖 측정 래퍼 measure_b.py 로 따로 남김 — 저장소 코드 변경 없음).

**결과 (분모: 정책 미정 d28 · n06 · bs1#3 · bs2#3 제외)**
| | 라우터 해석 | 소비 해석 | 형식 | 최종 결정 | 상태 | 연속 대화(엄격) | 연속 대화(결정 · 상태) |
|---|---|---|---|---|---|---|---|
| 회귀 dev (28 + s1~s3) | 28/31 | 28/32 | 35/35 | 36/37 | 36/37 | 2/3 | 3/3 |
| 회귀 new (12 + s4~s8) | 18/26 | 17/27 | 30/30 | 27/33 | 31/33 | 0/5 | 3/5 |
| 새 사례 (8 + bs1 · bs2, 같은 작성자) | 10/10 | 6/7 | 12/12 | 12/12 | 12/12 | 2/2 | 2/2 |
| 합계 | 56/67 (83.6%) | 51/66 (77.3%) | 77/77 | 75/82 (91.5%) | 79/82 (96.3%) | 4/10 | 8/10 |

**실패 구분 (프롬프트 · 기대값은 고치지 않음, 원인은 후보)**
- 결정까지 틀린 최초 오류 4: d22 "그럼 교통비는?"(직전 분석 없음) 라우터 unclear → 범위 질문 대신 detail / n10 "남은 예산은 얼마야?" 소비 refers_to=none → 4번 바로 응답(17차와 같음, 독립 · 후속 경계 미정) / s6#4 "방금 질문만 취소해줘" 라우터 followup=cancel(기대 answer + choice=question) → 취소 대상 질문이 남음 / s7#1 "거기서 제일 많이 나간 건 뭐야?"(직전 없음) 소비 mine=false → 목적지 확인 질문(기대: 소비가 범위 질문).
- 이어진 실패 3: s6#5(s6#4 가 남긴 질문에 답으로 처리 → 재개 대신 취소), s7#2 · s7#3(s7#1 의 목적지 질문 상태 위에서). 도구는 s6#4 도 '최초 오류 이후'로 표시하지만 s6#2 는 추출만 틀리고 결정 · 상태가 맞아서 s6#4 는 독립 오류.
- 결정은 맞고 추출만 틀림:
  - 기대값 변환 문제 후보 6단위: 예산 진행 중 재분류 턴(d25 · n11 · s3#3 · s5#3 · s6#2)에서 두 역할 모두 followup=null, 기대값은 "new" — 분리 전 통합 기대값(예산 작업 맥락이 있으면 followup 을 채움)을 옮긴 것. 새 지시문은 질문 대기일 때만 followup 을 채운다. s8#2 소비(chosen) 기대값도 답 턴의 통합 기대값을 옮겨 원래 문장의 소비 조건(target · period)을 비워 둠.
  - 모델 해석 후보: 되묻기 답에 refers_to="unclear"(d27 · b06 · s4#2), 라우터 질문 답에 intents 채움(s8#2, 17차와 같은 유형).
- 대역 의존 결과(결정 · 상태 맞음): 예산 대역 not_mine — d25 · n11 · s3#3 · s5#3 · s6#2, 정책 대역 — b01 · bs2#1 · bs2#2, 예산 대역 + 정책 미정 — bs1#3.
- 정책 미정 제외: d28(소비 mine=false — 기대 true), n06(FOOD + all_categories), bs1#3 · bs2#3(결정 · 상태는 현재 임시 동작대로).
- 새 사례 b06 "5만 원만큼"(되묻기 중): 결정 · 상태 맞음(같은 질문 다시), 소비 refers_to 만 어긋남. b07 · b08 · bs1#2(다른 분야 not_mine · 금액이 든 소비 요청) 모두 맞음.

**호출 · 시간 · 비용**
- 호출 138 / 상한 160 (점검 2 + 나머지 136), 실패 0 · fatal 없음 · 재시도 0. 계획 139 와 차이: 라우터 70(계획 68) · 소비 66(계획 69) — 모델 결과에 따라 분기가 달라짐(d22 · s6#5 · s7#2 소비 미호출, s7#2 · s7#3 라우터 추가).
- 개별 호출 응답 시간: 중앙값 1.75초 · p90 2.5초 · 최대 8.8초 (최소 1.0초).
- 사용자 한 턴 전체(라우터 → 담당 → 결과): 호출 있는 81턴 중앙값 3.3초 · p90 4.7초 · 최대 10.9초. 호출 1회 턴 중앙값 1.7초, 2회 턴 중앙값 3.7초. 코드 처리 턴(호출 0) 5개는 0.01초 이하.
- 토큰: 입력 187,385(그중 캐시 111,340) · 출력 14,059(추론 5,363). 비용 약 $0.0258 (17차와 같은 단가 $0.10 / $0.50 per 1M, 캐시 할인 미반영 · 이번에 공식 가격 재확인 안 함).

**다음 결정 (이번에 고치지 않음)**
- 기대값: 예산 진행 중 재분류 턴의 followup, chosen 소비 기대값을 새 지시문 기준으로 다시 적을지 (변환 규칙 수정 → 재채점은 API 없이 가능).
- 프롬프트 후보: 되묻기 답의 refers_to, 라우터 질문 답의 intents, "방금 질문만 취소해줘"의 followup(answer vs cancel), 직전 분석 없는 지시어 문장의 처리(라우터 unclear / 소비 mine=false).
- 미정 정책: n10 독립 · 후속 경계, 보류 질문 · 작업 재진입, 예산 보조값.

## 16. 28차 결과의 기준 점검 · 일반 규칙 수정 (29차, 2026-10-29 — 실제 LLM 호출 없음)

**보존** — `scripts/router_eval/frozen/`: 28차에 쓴 cases_b B-2026-10-29.1 (fb159e29) · 라우터 지시문 19c9f8c9 · 소비 지시문 a7547d3d 원문. 28차 실행 결과(`<scratchpad>/router-eval-b160/`)는 그대로, 재채점은 저장 응답 재생으로 replay-v2 · replay-v2-code 에 따로.

**집계 범위**: 138 = 실제 요청 전부(점검 2 + 나머지 136, 정책 미정 단위 호출 포함, 라우터 70 · 소비 66, 모두 형식 정상). '형식 77/77' 은 호출 수가 아니라 단위 수 — 채점 단위(정책 미정 · 호출 실패 제외) 중 LLM 을 한 번이라도 부른 단위. 82 와의 차이 5 는 코드로 처리해 호출이 없는 단위.

**기준 변경 (B-2026-10-29.2, 근거는 역할별 계약 — 모델 응답에 맞추지 않음)**
- followup 은 그 역할이 질문 중일 때만 채점: 라우터 followup 은 라우터 질문 중, 소비 followup 은 소비 되묻기 중에만 코드가 읽는다. 분리 전 기대에 followup 이 있었는데 그 역할에 질문이 없으면 관찰만 — d25 · n11 · s3#3 · s5#3 · s6#2(must "new" 제거), d26 · h12 · s3#4 · s5#4 · s6#5(allow 제거).
- chosen 소비 기대값은 원래 문장의 기대값: 라우터 질문에서 소비를 고르면 소비 에이전트는 원래 문장을 해석한다 — s8#2.
- 정책 검토 표시(점수에는 넣고 따로 목록): n10 독립 · 후속 경계, s8#2 라우터 질문 답의 intents (둘 다 17차부터 미정). 기대값은 바꾸지 않음.
- 바꾸지 않은 것: 되묻기 답의 refers_to="unclear"(d27 · b06 · s4#2) — 소비 merge 가 답에서 물은 칸만 읽어 결정에 영향 없음 → 계약 위반 아님, 코드 · 프롬프트 수정 불필요. 해석 점수에는 실패로 그대로 남긴다.

**점수 (분모는 15번과 같음)**
| | 라우터 | 소비 | 형식 | 결정 | 상태 | 연속(엄격) | 연속(결정 · 상태) |
|---|---|---|---|---|---|---|---|
| 원래 v1 (28차) | 56/67 | 51/66 | 77/77 | 75/82 | 79/82 | 4/10 | 8/10 |
| 기준 변경 v2 | 61/67 | 57/66 | 77/77 | 75/82 | 79/82 | 6/10 | 8/10 |
| v2 − n10 | 60/66 | 57/65 | 76/76 | 75/81 | 78/81 | 6/10 | 8/10 |
| v2 + 코드 규칙 (재생) | 61/66 | 57/65 | 76/76 | 76/81 | 80/81 | 6/10 | 8/10 |
- n10 영향: n10 은 라우터 · 상태 맞음, 소비 해석 · 결정 틀림 → 빼면 라우터 60/66 · 소비 57/65 · 결정 75/81 · 상태 78/81 (결정 91.5% → 92.6%).
- 코드 규칙 재생: s6#4 결정 · 상태 정답(해석 점수는 followup=cancel 이라 그대로 실패). s6#5 는 바뀐 상태에서 소비가 먼저 받는데 그 응답이 저장돼 있지 않아 재생 불가(분모에서 빠짐) → 실제 재평가 필요.

**코드 · 프롬프트 변경 (문장 · 사례 ID 분기 없음)**
- `_task_answer`: 라우터 작업 질문(cancel_target · budget_task_choice)에서 choice 가 그 질문의 선택지이고 followup 이 answer 또는 cancel 이면 답. 선택지 밖 · unclear 는 그대로 다시 묻는다.
- 라우터 지시문 → f894b169: 내 소비를 묻는 문장은 가리키는 대상이 불분명해도(직전 맥락 없어도) spending, unclear 는 기능조차 모를 때만 / 취소 대상을 고른 말은 취소 말투여도 followup="answer".
- 소비 지시문 → 302b9cd8: 내 소비를 묻는데 가리키는 대상만 불분명하면 '뜻을 알 수 없는 말'이 아님 → mine=true · refers_to="unclear".
- 원문은 frozen/ 에 새 해시로도 보관. 평가 문장 유출 검사 통과.

**다음 실제 재평가 계획 (승인 후)**: 새 장부 · 상한 160, 같은 모델 · reasoning=low · Structured Outputs · 재시도 0, 사례 B-2026-10-29.2, 지시문 f894b169 · 302b9cd8 해시 고정, 같은 58단위(약 139호출) — 점검 1단위 후 나머지. 볼 것: d22 · s7#1~3(지시어 문장), s6#4 · s6#5(취소 대상 답), 회귀(나머지 단위 하락 여부). 결과는 v1 · v2 점수와 나란히, 같은 작성자 사례라 독립 평가 아님.

## 17. 역할별 실제 LLM 재평가 (30차, 2026-10-29)

**집계 바로잡기 (15번)**: 28차 역할별 호출은 라우터 71 · 소비 67 = 138 (점검 b05 2회 포함). 15번의 '라우터 70 · 소비 66'은 나머지 실행 136회만 센 값. 계획 139 = 라우터 69 · 소비 70 (점검 포함, v1 · v2 사례 같음).
**단위 수**: 실행 단위 58 = 단일 질문 48(dev 28 · new 12 · 새 8, holdout h* 12 제외) + 연속 대화 10(38턴). 채점 단위 86 = 48 + 38, 정책 미정 4(d28 · n06 · bs1#3 · bs2#3) 빼면 82.
**Django 840** = 이 작업 범위(chat · spending_analysis · spending_fixture) 214(이전 212 + 이번 추가 2) + 기존 팀 앱(users · finance · budgets · config) 626. 이전 212 는 범위만 돌린 수.
**16번 재생 81**: s6#5 — 코드 변경으로 소비가 먼저 받는데 그 응답이 저장돼 있지 않아 채점 불가(call_failed).

**실행 조건 (`<scratchpad>/router-eval-b160-v2/pre-run.json`)**: gpt-6-luna · reasoning=low · 역할별 Structured Outputs · 재시도 0, 새 장부 limit 160, 지시문 라우터 f894b169 · 소비 302b9cd8, 사례 B-2026-10-29.2 (730ef08b), 28차와 같은 58단위, 점검 b05 → 나머지.
- 실행 실수: 나머지 실행의 ID 목록에서 b05 를 빼지 못해 b05 를 한 번 더 호출(+2). 장부 141/160, 채점은 점검 실행의 b05 만(두 응답 원문 동일). 채점 대상 호출 139 = 라우터 69 · 소비 70 (계획과 같음), 실패 0.

**결과 (같은 기준 v2 · 같은 58단위, 28차는 저장 응답 재채점)**
| | 라우터 | 소비 | 형식 | 결정 | 상태 | 연속(엄격) | 연속(결정 · 상태) |
|---|---|---|---|---|---|---|---|
| 28차 · 정책 미정 제외 | 61/67 | 57/66 | 77/77 | 75/82 | 79/82 | 6/10 | 8/10 |
| 30차 · 정책 미정 제외 | 64/65 | 58/66 | 77/77 | 80/82 | 82/82 | 6/10 | 10/10 |
| 28차 · 정책 미정 포함 | 65/71 | 59/70 | — | 79/86 | 83/86 | — | — |
| 30차 · 정책 미정 포함 | 68/69 | 61/70 | — | 84/86 | 86/86 | — | — |
| 30차 · v1 기준(참고) | 59/65 | 53/66 | 77/77 | 80/82 | 82/82 | 4/10 | 10/10 |
- 라우터 분모 차이는 호출 경로 차이(라우터를 안 거친 턴). 결정 · 상태 분모는 같다. n10(정책 검토) 빼면 30차 결정 80/81 · 상태 81/81.
- 개선(결정 · 상태): d22 · s6#4 · s6#5 · s7#1 · s7#2 · s7#3. n06(정책 미정) 해석도 맞음.
- 새로 틀림: n05 "지난달보다 여행비 더 나갔어?" 소비가 여행비를 unknown 으로 → 카테고리 질문(결정 틀림). 해석만: s4#5 · s5#3 refers_to="previous"(결정 · 상태 맞음, s5 엄격 실패).
- 그대로: n10(결정), s8#2(라우터 답에 intents), 되묻기 답 refers_to(d27 · b06 · s4#2 · s7#2).
- 한 번 실행이라 n05 가 프롬프트 변화 때문인지 실행 간 흔들림인지 구분 못 함.

**시간 · 비용**: 개별 호출 중앙 1.72초 · p90 2.33 · 최대 4.99. 사용자 한 턴(호출 있는 81턴) 중앙 3.2초 · p90 4.1 · 최대 5.9, 1회 턴 중앙 1.8초 · 2회 턴 중앙 3.5초. 토큰 입력 204,058(캐시 193,668) · 출력 12,950, 약 $0.027 (중복 b05 포함, 단가 재확인 안 함).

## 18. 평가 도구 보완 · 대화 안내 문구 (31차, 2026-10-29 — 실제 LLM 호출 없음)

**b05 중복 실행 원인 · 수정**: 30차 나머지 실행의 ID 목록을 쉼표 한 줄 파일에서 줄 단위 grep 으로 빼려다 b05 가 빠지지 않았다. 도구에는 같은 장부로 이미 보낸 단위를 막는 장치가 없었다.
- `run.Ledger.claim(ids)`: live 실행 전에 보낼 단위 ID 를 장부 `units` 에 기록, 이미 있는 단위가 섞이면 아무것도 보내지 않고 멈춘다(이 칸이 없는 옛 장부는 빈 목록으로 봄 — 기존 장부 파일은 고치지 않음).
- `stage_b --cases`: 고른 묶음에 없는 ID(띄어쓰기로 붙은 목록 · 오타 · --scenarios 없이 고른 흐름)는 조용히 빠지지 않고 멈춘다.
- 테스트: 점검 b05 뒤 b04,b05,b06 → 호출 0 으로 멈춤, b04,b06 → 실행 · 장부 units 확인 / 잘못된 ID 목록 3가지.

**실패 사례 기록 — n05 "지난달보다 여행비 더 나갔어?"** (30차): 소비가 `{"unknown": "여행비"}` → 카테고리 되묻기(결정 틀림). 28차 같은 문장은 TRAVEL_LODGING 으로 맞음 → 2회 중 1회 실패.
- 지금 소비 지시문에 가는 카테고리 정보: `코드=화면 이름` 12개(시드 그대로, 예: TRAVEL_LODGING=여행·숙박) + "위 코드에 확실히 맞지 않는 말이면 unknown" 규칙 + 예시 "데이트비랑 식비". 동의어 · '○○비' 같은 일상 표현 정보는 없다. 다른 원천도 없음(`budgets.Category` 는 화면 이름만, 거래 분류기 키워드는 가맹점 이름용).
- 판단: 결과가 틀린 숫자가 아니라 되묻기(안전한 실패)라 급하지 않다. 보완한다면 문장 예외가 아니라 일반 규칙(화면 이름의 한 단어 + 비 · 값 → 그 카테고리, 예: 교통·차량 ↔ 교통비) 또는 카테고리별 동의어 목록(공급 원천 · 담당 결정 필요). 반복 실행으로 흔들림인지 먼저 확인 권장. 이번엔 고치지 않음.

**대화 안내 문구 — `chat/router_core/messages.py`** (`render(run_turn 결과)` · `opening()`, chat/replies.py 등 공통 파일 변경 없음)
- 본문: 소비 분석 결과는 소비 에이전트가 `answer.compose` 로 만든 reply 그대로(금액 계산 없음). 예산 · 정책 에이전트 reply 도 그대로 — 세부 대화는 만들지 않음.
- 조합: [사유] + [질문 줄기] + [선택지], 공통 안내는 한 번만. 카테고리 이름은 자리에 넣음.
- [확정 출처] 범위 밖 · 불명확 · 라우터 실패 = chat.md, 현재 예산 기간 질문 = 3번, 시작 인사 = AssistantPage, 기능 선택지 = chat/router.py CHOICES.
- [임시] 나머지 질문 줄기 · 사유 · 미지원 · 취소 · 실패 · 전환 안내 문장. 보류 안내는 "제안이 그대로 있다" 같은 보장 표현 없이 재개 때 확인한다고만.
- 문구 없는 코드는 테스트가 잡는다(코드 검색으로 모은 질문 · 사유 · 안내 코드 목록).

**미정 정책 — 문구 · 동작을 정하지 않은 것**
- `policy_resume_undecided`(보류된 정책 작업 + 정책 버튼): 문구 없이 `undecided` 로만 돌려줌.
- n10 독립 · 후속 경계: 문구가 아니라 해석 정책. 지금 동작 그대로.
- 보류된 소비 되묻기로 돌아와 새 요청([임시] 새 요청이 질문을 대신함), 정책 작업 재진입 방식, 동시에 보류할 작업 수 — 복귀 안내 문구 없음.
- 정책 보류 안내는 멈췄다는 사실만(복귀 방법은 말하지 않음).
- 범위 밖 안내에 청년 정책을 넣을지: chat.md 확정 문구("소비 분석과 예산 조정")를 그대로 씀. 정책 선택지의 API 값 · 이름("청년 정책")은 [임시].
- 소비 + 범위 밖 혼합(d28)에서 범위 밖 부분 안내 여부. 취소 대신 다른 결과로 끝난 뒤 보관한 새 요청(`unstarted_request`) 안내.
- 에이전트 실패 문구(chat.md 에는 라우터 실패만 있음), 조건 충돌 질문이 어떤 칸이 충돌했는지 말할지(결정에 칸 정보 없음), 금액 '만 원' 줄임 표기.

**기록 보존**: `reports/router_eval/` — 회차별 요약 JSON(설정 · 해시 · 점수 · 실패 단위 · 시간 · 토큰, 로컬 경로 제외) + 응답 원문 jsonl. ML 집계 기록(`reports/ml_models/`)과 같은 원칙. 보관 응답만으로 30차 점수 재현 확인. 장부 · 턴별 원본 기록은 저장소 밖에 그대로.

## 19. 개발용 로컬 채팅 화면 (32차, 2026-10-29 — 실제 LLM 호출 없음)

- `spending_fixture/local_chat.py` + `local_chat.html` (stdlib http.server, 127.0.0.1:8765). 사용법은 `spending_fixture/README.md`.
- 재사용: run_turn · 소비 분석 에이전트(fixture DB 실제 분석) · messages.render · 평가 도구 ResponsesClient · Ledger. 페르소나별 MemoryStateStore.
- 예산 · 정책 대역 규칙(화면에 표시): 예산 — 시작 = 승인 대기 작업, '그렇게 해줘' = 서버 상태 저장 중(끝내지 않음), 취소 = 서버 상태 취소됨, 그 밖 문장 not_mine. 정책 — 시작 = 작업, 취소는 받기만(뜻 미정), 그 밖 not_mine. 대역 규칙은 확인용이지 에이전트 계약이 아니다.
- messages 변경: 취소 세 가지 구분 — 질문 취소 "방금 질문은 취소했어요." / 에이전트에 전달 "예산 조정 쪽에 취소 요청을 전달했어요. 실제로 취소됐는지는 확인한 뒤…" (+ 취소하고 새로: 기존 취소부터 요청) / 서버 확인 `OUTCOME`(render 의 budget_outcome = 이번 입력에서 새로 기록된 서버 확인 결과). 미정 경로는 `review` 코드 목록(정책 재진입 · 정책 '취소'의 뜻) → 로컬 화면은 "[검토 중] …" 설명으로만 표시.
- 가짜 해석에서 선택지를 누르면 그 질문의 답으로 읽은 결과를 그 턴에만 넣는다(실제 LLM 모드에서는 선택지 이름 문장을 그대로 해석).

## 20. 로컬 채팅에서 확인한 개선 1~4 (33차, 2026-10-29 — 실제 LLM · ML · CODEF 호출 없음)

공통: chat/router.py · api.py · replies.py · config · 모델 · 마이그레이션 · 서비스 API · 의존성 변경 없음. 기본 414건 · 05 독립 기대값 그대로, 변형은 테스트 안 별도 사용자만.
표시용 새 칸(sections · chart · suggest · explain)은 소비 에이전트 reply → messages.render → 로컬 채팅 내부 응답에만 있다.

### 20-1. 기간 충돌 · 대화 내 별칭
- 원인 구분: "이번 달 3개월 식비"는 코드가 덮어쓰지 않는다 — period=this_month + months=3 이 오면 원래도 되물었다(대역 테스트). 실제 화면에서 바로
  최근 3개월로 간 것은 소비 해석 LLM 이 '이번 달'을 빼고 추출했기 때문으로 본다(period 칸이 하나라 두 기간을 한 칸에 담을 수 없음).
  로컬 채팅은 응답 원문을 남기지 않아 실제 출력은 확인하지 못했다 → 실제 LLM 확인 필요.
- 지시문 원칙 5 추가: 서로 맞지 않는 조건도 고르거나 고치지 말고 각 칸에 모두 채운다(예시는 다른 문장 "지난달 2개월 교통비"). → 소비 프롬프트 해시가 바뀜, 재평가 필요.
- 일반 규칙: `dialog._conflict` 가 명시 조건의 보기 방식(이번 달 · 지난달 · 지난달 비교 · 최근 N개월)이 둘 이상이면 period_choice, 전체 + 특정 카테고리면
  category_choice 로 되묻는다. 선택지 = 사용자가 말한 쪽들. 이번 달 + 지난달 비교는 한 보기(충돌 아님). 고르면 그 묶음 칸만 바꾸고 카테고리 · 대상은 유지.
  선택지 이름 그대로면 LLM 없이 답으로 본다(카테고리 선택과 같은 방식). 기존 scope 질문 → "어느 기간의 식비를 볼까요?" [이번 달] [최근 3개월].
- 별칭: 카테고리 질문에 사용자가 목록 이름 · 코드를 그대로 고른 대응만 소비 칸 `aliases`{원문 말: 코드}에 저장. 같은 대화의 소비 조회 · 예산 잔액 조회에서
  모르는 말을 그 코드로 바꾼다. 해석한 답("여가 쪽이야")은 이번 조회에만 쓰고 기억하지 않는다 [임시].
  처음: "이번 대화에서는 '데이트비'를 문화·여가로 조회할게요." / 다시 쓸 때: "…정한 대로 문화·여가로 조회했어요." + ['데이트비' 카테고리 다시 고르기]
  (누르면 LLM 없이 그 말만 다시 묻고, 고른 값으로 바뀐다). 대화 · 회원별 저장소라 새 대화에서 초기화. 다른 분야 판단(not_mine) 뒤에만 적용 → 예산 조정 요청을 끌어오지 않는다.
  확인 필요(UNCLASSIFIED)로 고른 별칭은 소비 조회는 되고 남은 예산은 기존 제외 정책대로 미지원.
- 고친 버그: `find_category` 가 이름의 띄어쓰기를 지우지 않아 '확인 필요'를 이름으로 고르지 못했다.

### 20-2. 답변 배치 · 문구
- 지난달 비교(6 · 7번): 머리말 "이번 달(10월 1일~29일) 소비" / "지난달 같은 기간(9월 1일~29일)과 비교했어요." 한 번, 항목마다 묶음 "식비 · 390,000원" +
  "지난달보다 42,000원 증가했어요. ↑ 12.07%" · "지난달과 같아요." — 증감은 문장으로, 화살표는 덧붙임. 비교 못 하면 '비교했어요' 줄을 쓰지 않는다.
- 예산 잔액: "이번 예산 기간은 10월 25일~11월 24일이고, 10월 29일까지 쓴 금액으로 계산했어요." (주기 전체와 사용액 기간 구분, 여러 항목에 한 번)
- 안내 문구 교체: 부분 수집 · 확인 필요 거래 · 지난달 비교 불가 · 카테고리 질문 (사용자 제시 방향). 조사(을/를 · 은/는 · 으로/로)는 받침으로 고른다.
- 최근 N개월: 금액이 있는 달(확정 · 일부 내역 기준 · 수집 기록 없이 기록된 거래)은 달마다, 내역 없는 달은 "7·8월은 확인할 수 있는 내역이 없어요." 한 줄,
  전부 없으면 한 문장. 수집 완료 0원은 "9월 0원"으로 남긴다. 내부 series · 수집 상태는 그대로.

### 20-3. 비중 · 예산 조정 제안 · 그래프
- 5번: "…가장 많이 쓴 곳은 주거·통신, 700,000원이에요. 전체 소비 1,510,000원의 46.36%예요." 비중은 서버 shares(분모 = 고정지출 · 확인 필요 포함 전체 소비).
  1위 규칙 · 동률(모두, 카테고리 순서) 그대로, 동률이면 "각각".
- 예산 초과(확정 over > 0)면 reply.suggest=["budget_adjust"] → messages 가 "필요하면 예산 조정을 요청할 수 있어요." + [예산 조정하기](value budget = 기능 버튼).
  누르면 route_button("budget") 그대로 — 승인 · 저장 없음, 소비 공유 요약은 기존 handoff.context.shared 로. 80% 알림 등 새 정책 없음.
- 5번 reply.chart: 카테고리 · 금액 · 비율(서버 값) 금액 내림차순 · 동률 카테고리 순서, 확인 필요 포함, 부분 수집이면 limited 문구, 0원 · 내역 없음 문구.
  로컬 화면은 접이식 가로 막대 + 같은 글자. 예산 초과 색 없음(월과 예산 주기 기간이 다름).

### 20-4. 월별 분석 · 근거 기반 설명
- `result.trend_stats`: 완전 수집 달만으로 월평균(2개월 이상일 때만, 원 단위 HALF_UP — percent 와 같은 정수 산술) · 이웃한 두 달 증감(compare 재사용, 이전 0원이면
  증감률 없음). 빠진 달을 건너뛴 비교 없음, 제외한 달은 excluded 로 남기고 문장으로 알린다. 확정 2개월 미만이면 "평균과 증감은 계산하지 않았어요."
  진행 중인 이번 달은 series 에 없어 섞이지 않는다. 평가 표현 없음. 기록 facts 에 stats 추가(전부 확정일 때만).
- `spending_analysis/explain.py`: 직전 확정 항목의 두 기간 · 카테고리로만 다시 조회 → 금액 · 증감 · 건수 · 건당 평균 결제액 · 가맹점별(전체면 카테고리별) 증감.
  6 · 7 = 이번 달 vs 지난달 같은 기간, 8 · 9 = 마지막 이웃한 두 달, 1~5 = 비교 기준 없음 안내. 다시 조회한 기간이 완전 수집이 아니거나 합계가 기록과 다르면 쓰지 않는다.
  답변 = 서버 증감 문장 + "거래 내역에서 확인한 변화예요. 이것만으로 이유를 단정할 수는 없어요." + 근거 문장.
- 설명 LLM(선택): 근거 이름 · 키만 넘기고({F1} 자리표시), 숫자 · % · 모르는 키 · 금지 표현(습관 · 과소비 · 아마 …) · 자리표시 없는 문장이면 버리고 템플릿.
  (34차) 로컬 채팅 실제 LLM 모드에 연결: 평가 도구 `stage_b.schema_for` 에 설명 스키마(spending_explanation, {sentences: [문자열]}) 추가 —
  채점 역할(ROLES · ROLE_OF)에는 넣지 않았다. 같은 ResponsesClient · 상한 · 장부를 쓰고 "왜 늘었어?" 한 번에 1회 더. 가짜 해석 모드는 템플릿만.
  개발 표시: LLM 문장 사용 / 검사 탈락 / 호출 실패 · 상한(failed) 을 나눠 보여준다. 자리표시 바로 뒤 조사(을/를 · 은/는 · 이/가 · 과/와 · 으로/로)는
  끼운 값의 받침으로 서버가 고친다(josa 는 answer.py 로 옮겨 messages 와 같이 씀). 실제 모델 응답 품질은 아직 확인 전.

### 20-5. 미정 · 확인 필요 (추천안)
- 가맹점 식별: 지금은 `normalize_merchant`(공백만 정리) — 지점명이 다르면 다른 가맹점. 추천: 1차는 그대로, 가맹점 묶음 규칙은 거래 분류 담당과 정한다.
- 건수 기준: 순금액 > 0 인 소비 거래(전액 취소 제외, 부분 취소는 1건). 추천: 이대로.
- 별칭 저장 범위: 목록에서 그대로 고른 답만. 해석한 답("여가 쪽이야")도 저장할지. 추천: 지금처럼 고른 답만.
- 설명 LLM: 로컬 채팅에만 연결(34차). 서비스 연결 여부 · 평가 사례 · 금지 표현 목록 범위는 실제 응답을 본 뒤 정한다.
- 소비 해석 프롬프트 원칙 5의 실제 효과(이번 달 3개월 · 지난달 2개월 · 전체랑 식비)와 period_choice · category_choice 답 해석은 실제 LLM 확인 전.
- 테스트: 전체(fixture 설정) 886개 중 실패 1 = budgets 동시성 테스트의 응답 순서(단독 3회 통과, 이번 변경과 무관한 순서 의존).
- 34차 테스트: 전체(fixture 설정) 888개 통과(기대 실패 1 · skip 1), router_eval 통과. 실제 LLM 호출 없음(가짜 API 응답으로 실제 호출 경로 검증).


## 21. 사용자 테스트 보완 1~6 (35차, 2026-10-29 — 실제 LLM · 외부 API 호출 없음)

원인 구분 (실제 LLM 원문은 로컬 채팅이 남기지 않아 코드 경로로 추정 — 실제 확인 필요):
1 대기 상태 병합: 기간 질문 중 다시 충돌하는 답("이번 달 2개월")을 선택지 하나로 못 맞추면 옛 질문을 반복 / 화면에 지난 선택지 검사 없음.
2 LLM 해석: 33차 원칙 5 의 "지난달 → period" 가 "지난달보다"를 period=last_month + compare 로 함께 채우게 했을 가능성. 3 기능 없음.
4 · 5 LLM 해석 + 형식 부족: 세부 지출 · 조회 기준을 정하는 말을 담을 칸이 없음(커피 → FOOD, "데이트비는 식비로 볼게" → mine=false → 범위 밖). 6 화면 구조: 제한 안내가 notices(아래)에만.

변경 정책:
- 1. 질문 갱신: period_choice 중 고쳐 말한 기간 묶음으로 요청을 바꿔 다시 decide — 해소되면 바로 처리, 아직 충돌이면 새 조건으로 질문 · 선택지를 다시 만든다.
  바꾸지 않은 조건(카테고리 · 대상) 유지. 지난 선택지: 로컬 채팅이 선택지에 답변 번호(turn)를 붙여 최신이 아니면 상태 변화 · 호출 없이 거절, 화면도 마지막 답변 선택지만 활성.
- 2. 소비 프롬프트 원칙 6: "지난달보다 · 대비 · 이랑 비교"의 지난달은 compare 만(period 비움). 코드 규칙은 그대로 — 해석이 last_month + prev_month 를
  함께 내면 덮지 않고 [지난달] [지난달과 비교] 로 되묻는다. 원칙 5 의 예시 문구도 "보려는 기간 이번 달 · 지난달 → period" 로 좁혔다.
- 3. include_current(새 해석 칸): "이번 달 포함 최근 N개월" = 이번 달 + 직전 N−1개월, 달마다 1일 ~ 기준일과 같은 일자(없으면 말일, `periods.recent_months_to_date`).
  기존 최근 N개월(완료된 달)은 그대로. 지난달 대비 + 최근 N개월(포함 여부 미언급)은 include_choice [완료된 N개월] [이번 달 포함 N개월] — 기간 충돌이 아니라
  포함 여부만 묻는다. 포함 여부 없이 개월만 답하면 다시 묻는다. 최대 3개월 · 원래 개월 수 보존 그대로. 항목 칸 include_current=true(8 · 9번만),
  답변은 달마다 실제 날짜 범위 · "9월 같은 기간보다", 평균 · 증감 · 수집 완전성 · 설명 근거 모두 같은 날짜 범위 블록으로 계산. 서비스 API 변경 없음.
- 4. 세부 지출: 해석 categories 에 {detail, category | null}. 조회 전에 fallback_scope "커피에 쓴 돈만 따로 … 대신 식비 전체를 확인할까요?" [식비 전체 보기] [취소],
  속한 곳이 불분명하면 fallback_category(카테고리 목록). 동의하면 {…, accepted: true}(서버만 붙임, 스키마에 없음) → 항목 instead_of="커피".
  별칭 저장 없음. 답변 lead "커피에 쓴 돈만 따로 구분할 수 없어 식비 전체로 조회했어요." · 금액 줄 "식비 전체", 공유 요약 · 기록에 instead_of,
  설명은 payload {scope: "식비 전체", requested: "커피"} + "식비 전체 기준이라 커피만의 변화는 알 수 없어요." 가맹점 · 품목별 조회는 하지 않는다.
- 5. alias(새 해석 칸): "데이트비는 식비로 볼게" = 이 대화의 조회 기준 → 바로 저장(덮어씀), 조회가 없으면 noted 상태로 "이번 대화에서는 '데이트비'를 식비 전체로
  조회할게요." 만 답하고 보류된 질문은 그대로. 라우터 지시문에 "조회 기준을 정하는 말 = spending" 한 줄. 별칭 안내 문구는 "○○ 전체로" 로 바꿈.
- 6. reply.lead(핵심 안내: 개월 상한 · 비교 불가 · 대체 범위)를 결과 위에. 비교 불가면 "참고로 이번 달(…) 금액이에요." + 금액 묶음(일부 내역 기준 표시)을 보조로.
  화면은 lead 칸을 그대로 맨 위에 굵게 + "안내:" 글자(문자열 검색 · 재정렬 없음).
- 평가 도구 스키마: 소비 해석에 include_current · alias · 세부 지출 항목 추가(소비 프롬프트 해시 변경 → 재평가 필요). legacy 대역 SPENDING_ONLY 에도 두 칸.

남은 제한 · 미정:
- [정책 해석] '단순 카테고리 선택은 이번 조회의 범위 선택'의 범위: 대체 범위 동의(식비 전체 보기)는 기억하지 않는다. 모르는 말의 카테고리 질문 답은 33차 결정대로
  기억한다(사용자 명시 결정). 카테고리 질문 답도 이번 조회만으로 바꿀지 확인 필요.
- 이번 달 포함 평균은 '같은 날짜 범위의 평균'이지 월평균이 아니다 — 문구에 "같은 기간"을 붙였다. 서비스 화면 표기 미정.
- 그래프는 전반 분석(카테고리 없음)에만 있어 세부 지출 대체 범위와 겹치지 않는다.
- 실제 LLM 확인 전: 원칙 6 · 7, include_current · alias 추출, 기간 질문 중 고쳐 말하기를 followup=answer 로 내는지.
- 테스트: 전체(fixture 설정) 904개 통과(기대 실패 1 · skip 1), router_eval 35개 통과.

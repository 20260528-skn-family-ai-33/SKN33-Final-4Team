# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

머니쿠션(Money Cushion): 사회초년생용 AI 금융 코치. React 모바일 웹(`frontend/`) + Django REST API(`backend/`) + PostgreSQL. 문서·주석·커밋은 한국어로 쓴다.

## 명령어

```bash
# 백엔드 (Python 3.12, 루트의 .venv; .env는 루트에 — docs/ENV_SETUP.md)
cd backend
python manage.py runserver            # http://127.0.0.1:8000, Swagger: /api/docs/
python manage.py migrate
python manage.py test                 # 전체 (PostgreSQL 테스트 DB 생성 → DB 계정에 CREATEDB 필요)
python manage.py test finance.test_cards                  # 모듈 하나
python manage.py test finance.test_cards.SomeTests.test_x # 테스트 하나
python manage.py codef_check          # CODEF 연결 확인 (finance/management/commands/ 에 codef_* 명령 더 있음)

# 프론트엔드 (Vite가 /api 를 127.0.0.1:8000 으로 프록시)
cd frontend
npm run dev                           # http://localhost:5173
npm run lint                          # oxlint
npm run build
node --test src/utils/direction.test.js   # node:test 기반 단위 테스트 (npm 스크립트 없음)

# OpenAPI 명세 (docs/openapi/parts/*.json → openapi.json/.yaml)
python -m pip install -r requirements-docs.txt
python scripts/build_openapi.py --validate          # 다시 생성
python scripts/build_openapi.py --check --validate  # 생성물이 최신인지 확인

# ML (루트에서, 모델별 별도 가상환경 — docs/ML_MODELS.md)
python -m unittest discover -s tests/ml_models -p 'test_spending*.py' -v     # 거래 분류 (.venv-ml)
python -m unittest discover -s tests/ml_models -p 'test_allocation*.py' -v   # 예산 배분 (.venv-budget)
```

로컬 DB는 Docker 컨테이너 `money-cushion-db` (`pgvector/pgvector:pg17`). 세팅 전체는 `docs/LOCAL_SETUP.md`.
`tests/codef/`는 Django 테스트가 아니라 CODEF 데모 API를 직접 부르는 수동 스크립트다.

## 구조

- **Django 앱 4개**: `users`(회원·재무 프로필·동의·요청 번호) · `finance`(CODEF 연결·계좌·카드·대출·거래·고정지출/저축·카드 청구서) · `budgets`(카테고리·예산 주기·예산·쿠션·홈 화면) · `chat`(대화·메시지·비동기 작업). 테이블 배치와 설계 이유는 `docs/DB_MODELS.md`.
- **API 진입점**: 모든 `/api/` 주소는 `backend/config/api_urls.py` 한 곳에 모인다. 새 주소는 **맨 끝 `not_found` 줄 위에** 추가하고 ID 자리는 `<uuid:이름>`. 뷰는 각 앱의 `api.py`(finance는 `api_cards.py`·`api_loans.py`·`api_sync.py` 등으로 나뉨) — 함수형 `@api_view`.
- **아직 목업인 API가 있다**: 예산·쿠션 등 일부는 각 앱의 `mock.py` 가짜 데이터를 그대로 돌려준다. Swagger(`/api/docs/`)가 확정 계약(`docs/openapi/`)과 현재 서버 상태를 구분해 보여준다.
- **camelCase ↔ snake_case 자동 변환**: `config/api.py`의 `CamelJSONRenderer`/`SnakeJSONParser`가 DRF 기본값이라, 파이썬 코드는 snake_case로만 쓴다.
- **CODEF 흐름**: `finance/codef*.py`가 외부 호출, `sync.py`·`sync_loans.py`·`sync_ranges.py`가 DB 반영, `pipeline.py`가 동기화 후 처리. 회원별 `connectedId`는 `connected_id.py`에서 AES-GCM 암호화. 목록·조회 GET은 저장된 데이터만 읽고 CODEF를 부르지 않는다 — CODEF는 연결·다시 불러오기 API에서만.
- **오래 걸리는 작업**: `config/tasks.py` + `chat.AsyncTask`. 1차는 요청 안에서 끝까지 처리하고 응답만 `taskId`·`status` 모양으로 준다(나중에 백그라운드로 바꿔도 API는 그대로). 같은 회원·같은 종류 작업이 진행 중이면 재사용(`start_or_reuse`).
- **프론트**: `src/pages/`(화면) · `src/popups/`(팝업, `PopupProvider`) · `src/utils/api.js`(읽기 `useApi`, 쓰기 `apiSend` — CSRF·`Idempotency-Key` 자동). 라우트는 `src/routes.js`. 화면 추가 방법은 `docs/FRONTEND_GUIDE.md`.
- **ML**: `scripts/spending_classifier/`(거래 분류 SVM), `scripts/budget_allocation/`(생활비 배분 CatBoost). 아직 웹 API에 연결되지 않았고, 가중치(`.joblib`)는 Git에 없다 — 담당자에게 받아 `reports/ml_models/*.json`의 SHA256과 대조.

## API 규칙 (상세: `docs/API_RULES.md`, API별 명세: `docs/api/*.md`)

API를 만들거나 고칠 때는 해당 `docs/api/<묶음>.md` 명세를 먼저 읽고 맞춘다. 핵심만:

- 돈은 **원 단위 정수**, 날짜 `YYYY-MM-DD`, 빈 값은 `""`가 아니라 `null`. 매월 N일이 없는 달은 말일.
- 성공은 감싸지 않고 그대로, 목록은 `{"items": [...]}`. 에러는 항상 `{"error": {"code", "message", "fields"?}}` — `fields`는 칸 오류일 때만 넣는다(빈 `{}`도 금지).
- 남의 데이터·없는 ID·주소 속 ID 형식 오류는 **404**. 본문에 담긴 ID가 틀리면 **400 `invalid`**(없음·남의 것 모두 같은 문구). 항상 `user=request.user`로 거른다.
- PATCH: 안 보낸 칸 = 그대로, `null` = 비우기. 명세 표에 없는 칸은 serializer `validate()`에서 400으로 막는다(DRF는 조용히 무시하므로).
- 새로 만들기·승인 요청은 `config/idempotency.py`의 `@idempotent()`(승인류는 `required=True`). 이미 원하는 상태인 요청은 에러 없이 성공으로 답한다.
- 카테고리는 영문 대문자 코드(`FOOD`, `HOUSING_TELECOM` …). 코드 목록은 `API_RULES.md` 표 · DB 시드(`budgets/migrations/0003_seed_categories.py`) · `frontend/src/utils/categories.js`가 같아야 한다.
- 계좌·카드 응답은 서버가 만든 `displayName`(끝 4자리만) + `status`(`connected`/`relogin`/`disconnected`) 객체. 계좌번호 원문·`connectedId`·CODEF 키는 응답에 넣지 않는다.
- 금액 계산은 계산 엔진(서버 코드)만 한다. LLM/에이전트는 제안만 하고 DB를 직접 바꾸지 않으며, 예산 변경은 엔진 검수 + 사용자 승인 후 저장.

## 팀 규칙

- **`makemigrations`는 DB 담당(김나은)만** 만든다. 모델을 바꾸면 `docs/DB_MODELS.md`에 변경 이유를 같이 적는다.
- 커밋 메시지 형식: `YYYY.MM.DD/이름/type: 설명` (예: `2026.10.28/김나은/feat: ...`).
- 실제 개인정보·원본 거래 데이터(`data/raw/`, `transactions_raw.json`)와 `.env`는 커밋하지 않는다. `living_expense_playground/`는 로컬 실험용(gitignore).

## 테스트 실행 규칙
- 작업 중에는 바꾼 파일과 관련된 테스트 모듈만 돌린다. 빠르게 하려면 --keepdb를 쓴다.
- --keepdb 실행에서 FK 오류나 Category.DoesNotExist가 나면, test_fixed 등의 TransactionTestCase가 시드를 지운 것이다. 그때는 --keepdb 없이 한 번 돌린다.
- 전체 테스트(python manage.py test)는 내가 요청할 때만 돌린다.
- budgets.test_design_candidates의 예전 실패 11개는 #89에서 해결됐다(2026-11-10 main 확인). 이제 실패가 나면 새로 생긴 실패로 보고한다.
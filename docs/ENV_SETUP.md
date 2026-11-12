# 개발 환경 · 환경변수 설정 가이드

머니쿠션 로컬 개발 환경을 준비하고 `.env`를 설정하는 방법을 정리한다.

> 처음 세팅한다면 OS별 단계 가이드 [`LOCAL_SETUP.md`](LOCAL_SETUP.md)부터 따라 한다.

| 항목 | 기준 |
|---|---|
| Python | **3.12** |
| 웹 프레임워크 | Django 5.2 LTS + DRF (API) · 화면은 React (`frontend/`, 환경변수 없음) |
| DB | PostgreSQL |
| 의존성 | 프로젝트 루트 `requirements.txt` |
| 환경변수 | 프로젝트 루트 `.env` (템플릿: `.env.example`) |

---

## 1. 빠른 시작

프로젝트 루트에서 실행한다.

```bash
# 1) 가상환경 생성 (Python 3.12)
python3.12 -m venv .venv          # Windows: py -3.12 -m venv .venv
source .venv/bin/activate         # Windows PowerShell: .venv\Scripts\Activate.ps1

# 2) 의존성 설치
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# 3) .env 만들기
cp .env.example .env              # Windows: copy .env.example .env

# 4) Django 시크릿 키 생성 → 출력값을 .env의 DJANGO_SECRET_KEY에 붙여넣기
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

> Python 3.12가 없으면 macOS는 `brew install python@3.12`, Windows는 python.org 설치 파일을 사용한다.

---

## 2. 환경변수 목록

### 지금 필요한 값

| 변수 | 필수 | 로컬 예시 | 설명 |
|---|---|---|---|
| `DJANGO_SECRET_KEY` | ✅ | (1-4에서 생성한 값) | Django 서명·세션 암호화 키. 팀원마다 각자 생성해도 됨 |
| `DJANGO_DEBUG` | ✅ | `True` | 로컬 `True`, 배포 `False` |
| `DJANGO_ALLOWED_HOSTS` | ✅ | `localhost,127.0.0.1` | 접속 허용 호스트. 쉼표로 구분 |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | 배포 시 | `https://도메인` | HTTPS 배포 주소. 로컬은 비워둠 (DEBUG면 React 개발 서버 `localhost:5173`은 자동 허용) |
| `DJANGO_ADMIN_URL` | 배포 시 | (비워둠 → `admin/`) | 관리자 화면 주소. 배포 때는 추측하기 어려운 값(예: `python -c "import secrets; print(secrets.token_urlsafe(12))"` 출력)으로 바꾸고 Git·채팅에 올리지 않음. `api`로 시작하면 서버가 안 켜짐 |
| `POSTGRES_DB` | ✅ | `money_cushion` | DB 이름 |
| `POSTGRES_USER` | ✅ | `money_cushion` | DB 사용자 |
| `POSTGRES_PASSWORD` | ✅ | (각자 설정) | DB 비밀번호 |
| `POSTGRES_HOST` | ✅ | `localhost` | 배포 시 RDS 엔드포인트 |
| `POSTGRES_PORT` | ✅ | `5432` | DB 포트 |
| `CONNECTED_ID_ENCRYPTION_KEY` | ✅ | (`.env.example`의 명령으로 생성) | 회원별 CODEF 연결 ID 암호화 키 (32바이트). 바꾸면 저장된 연결을 못 읽음 |
| `IDENTIFIER_HMAC_KEY` | ✅ | (같은 명령으로 한 번 더 생성) | 계좌 · 대출 · 카드 번호 중복 확인 키용 HMAC 키 (32바이트). 위 키와 다른 값. 바꾸면 금융 데이터를 지우고 다시 연결해야 함 |

### 외부 API (발급 후 입력)

| 변수 | 발급처 | 설명 |
|---|---|---|
| `CODEF_ENV` | — | `sandbox` · `demo` · `product` 중 하나. 비우면 `demo` |
| `CODEF_DEMO_ID` | codef.io > MY > 키관리 | 데모 버전 client_id |
| `CODEF_DEMO_SECRET` | codef.io > MY > 키관리 | 데모 버전 client_secret |
| `CODEF_CONNECTED_ID` | `python manage.py codef_connect` 결과 | 테스트용 은행 계정 연결 ID (개인별) |
| `CODEF_PUBLIC_KEY` | CODEF 개발자센터 | 계정 비밀번호 RSA 암호화용 공개키 |
| `DATA_GO_KR_SERVICE_KEY` | 공공데이터포털 | 상가(상권)정보 · 복지서비스 API 공통 인증키 (활용 신청은 API별로 따로) |

### 제공자 확정 후 추가

| 변수 | 설명 |
|---|---|
| `KAKAO_REST_API_KEY` | 검색/지도 API (카카오 기준. 네이버로 정하면 `NAVER_CLIENT_ID` · `NAVER_CLIENT_SECRET`) |
| AWS 관련 (S3 · SQS 등) | 배포 구조 확정 후 추가 |

> 새 환경변수를 추가하면 **`.env.example`과 이 문서를 함께 갱신**한다. 값은 비워서 올린다.

### 예산 조정 챗봇 연결

청년 정책 추천도 `CHAT_ENGINE=router`, `CHAT_LLM=live`에서 사용한다. `OPENAI_API_KEY`, `LLM_MODEL`에 더해
`EMBEDDING_MODEL`이 필요하며, 공유 정책 덤프의 임베딩 모델과 같은 값(`text-embedding-3-small`)을 설정한다.
정책 데이터는 `docs/agents/청년정책.md`의 복원 절차에 따라 DB에 한 번 적재한다. 모델 설정이 없거나 비어 있으면
정책 검색은 기존 실패 안내로 응답한다. 서버가 켜지는 것만으로 정책 검색 설정까지 확인되는 것은 아니다.

| 변수 | 값·역할 |
|---|---|
| `CHAT_ENGINE` | `router`에서 실제 조정 대화·카드를 연결한다. `legacy`는 이전 안내 흐름이다. |
| `CHAT_LLM` | 일반 자유 입력은 `live`. `fake`는 명시한 합성 테스트 문장용이다. |
| `OPENAI_API_KEY`, `LLM_MODEL` | 서버의 실제 해석기 설정. 키·원본 프롬프트는 로그와 프론트에 보내지 않는다. |
| `BUDGET_ADJUST_ALLOCATOR` | 선택 사항. 같은 그룹의 감액 함수(callable 또는 dotted path). 기본은 `budgets.adjustment_core.calculation.proportional_group_reduction`이며, 감액 가능액에 비례한 몫을 1만 원 우선·5천 원 보완으로 나눈다. `(group, required, capacities)`를 받아 `{reductions, remaining}`을 반환한다. 그룹 순서·순위 자격·보호 하한은 별도로 검수하며 최초 설계 A/B를 재선택하지 않는다. |
| `BUDGET_ADJUST_REFERENCE_PROVIDER` | 선택 사항. 최초 예산에 ML 참조가 없을 때 1회 보완하는 함수(callable 또는 dotted path). 미설정이면 통합된 `design_service.reference_from_initial_input`이 있을 때 사용한다. 최초 저장 `engine_input`으로 실제 ML 금액/비율과 모델 ID·체크섬을 반환한다. 성공·실패 시도는 최초 스냅샷 메타에 기록하며, 원 입력·예산액·프로필은 바꾸지 않는다. 합성·임시 비율은 ML 참조로 인정하지 않는다. |

실제 ML 참조가 필요한 자동 감액은 참조를 확인할 수 없으면 확인 필요로 안내한다. 확인된 재원과 사용자가 항목·금액까지 지정한 조정은 보호 조건을 검사하는 기존 경로를 사용한다. 지출 수집이 불완전하거나 저축 실행·쿠션 적립 상태를 확인할 수 없으면 가용액을 임의로 0원이나 미실행으로 판단하지 않는다.

교통 참고 하한은 자차 없음 6만 원·있음 10만 원이며 미응답이면 적용하지 않는다. 잠금 원액이 시스템 하한보다 우선하고, 잠금액이 사용자 최소액보다 낮으면 충돌이다. 설계 스냅샷의 `transport_history_reference`에서 검증된 완료 3개월·기준액 분자/분모·이력 지문·버전을 읽고 낮은 소비 기준액을 적용한다. 누락·잘못된 자료는 `transport_history_status="unavailable"`이며 현재 지출로 추정하지 않는다. 실제 금융정보의 외부 LLM 전송 동의는 별도 확인 전이며, 외부 호출 시험은 합성 데이터로 제한한다.

라우터 채팅의 입력 검사·조회·LLM 준비는 멱등 응답 저장 트랜잭션 밖에서 수행한다. 최초 ML 참조 보완은 짧은 트랜잭션으로 1회 시도를 확보하고, 모델 추론 중에는 예산 행을 잠그지 않는다. 저장은 회원 → 대화·예산 순서로 잠그며, 승인 직전 재검사에서는 새 참조 추론이나 보완 기록을 시작하지 않는다. 같은 성공 요청 키의 재전송은 준비·LLM을 다시 실행하지 않는다.

로컬 LLM 호출 장부는 Windows 파일 교체의 일시적인 접근·공유 거부만 잠금 안에서 최대 6회(총 대기 310ms) 재시도한다. 같은 기록의 파일 교체만 반복하며 호출 수를 다시 더하거나 LLM 요청을 재전송하지 않는다. 계속 실패하거나 디스크 오류가 나면 외부 호출을 보내지 않고 오류를 반환한다.

실제 서비스 DB에는 DB 담당 절차로 `users`의 유지 목표 필드 마이그레이션을 적용해야 한다. 코드·파일 준비와 DB 적용은 별개이며 이 작업에서 운영 DB에 자동 적용하지 않는다. 기존 `.env` 비밀값이나 배포 설정은 문서 갱신만으로 변경되지 않는다.

---

## 3. 로컬 PostgreSQL 준비

둘 중 하나를 선택한다.

**A. Docker (권장)** — 설치가 간단하고 팀원 간 버전이 같아진다.

```bash
docker run --name money-cushion-db \
  -e POSTGRES_DB=money_cushion \
  -e POSTGRES_USER=money_cushion \
  -e POSTGRES_PASSWORD=<.env와 같은 비밀번호> \
  -p 5432:5432 -d pgvector/pgvector:pg17
```

Windows PowerShell은 한 줄로 실행한다.

```powershell
docker run --name money-cushion-db -e POSTGRES_DB=money_cushion -e POSTGRES_USER=money_cushion -e POSTGRES_PASSWORD=<.env와 같은 비밀번호> -p 5432:5432 -d pgvector/pgvector:pg17
```

> `pgvector/pgvector:pg17`은 PostgreSQL 17에 벡터 검색 확장(pgvector)이 들어 있는 이미지다. 벡터 검색(정책 RAG 예정)에 쓴다. `finance` 0009 마이그레이션도 이 확장이 있어야 돈다.

**B. 직접 설치 (macOS)**

```bash
brew install postgresql@17
brew services start postgresql@17
psql postgres -c "CREATE USER money_cushion WITH PASSWORD '<비밀번호>' CREATEDB;"
psql postgres -c "CREATE DATABASE money_cushion OWNER money_cushion;"
brew install pgvector
psql template1 -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql money_cushion -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

> `template1`에 확장을 켜 두면 Django 테스트가 새로 만드는 테스트 DB에도 pgvector가 켜진다.

> `CREATEDB` 권한은 Django 테스트(`manage.py test`)가 테스트 DB를 만들 때 필요하다.

**이미 DB를 띄워 둔 경우 (pgvector 추가)** — `finance` 0009 마이그레이션부터 pgvector가 필요하다. `migrate` 전에 한 번만 실행한다.

```bash
# A. Docker (기존 데이터 유지)
docker exec -it money-cushion-db bash -c "apt-get update && apt-get install -y postgresql-17-pgvector"

# B. 직접 설치 (macOS)
brew install pgvector
psql template1 -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql money_cushion -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

설치 확인: `installed_version`은 `migrate` 전에는 비어 있고, **`migrate` 후에 버전이 나오면** DB에 켜진 것이다.

```bash
# A. Docker (PC에 psql이 없어도 된다)
docker exec money-cushion-db psql -U money_cushion -d money_cushion -c "SELECT name, default_version, installed_version FROM pg_available_extensions WHERE name='vector';"

# B. 직접 설치 (macOS)
psql -h localhost -U money_cushion -d money_cushion -c "SELECT name, default_version, installed_version FROM pg_available_extensions WHERE name='vector';"
```

> 결과가 0줄이면 pgvector가 설치되지 않은 것이다.

> `could not open extension control file` → pgvector가 설치되지 않은 것이다. 위 명령을 먼저 실행한다.
> `permission denied to create extension "vector"` → 관리자 계정으로 `CREATE EXTENSION vector;`를 한 번 실행한 뒤 다시 `migrate`한다.

---

## 4. Django 설정에서 읽기

실제 설정은 `backend/config/settings.py`. Django는 `backend/` 아래에 있고 `.env`는 프로젝트 루트에 있으므로 `BASE_DIR.parent`에서 읽는다.

```python
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR.parent / ".env")  # BASE_DIR = backend/


def required(name):
    """꼭 있어야 하는 값. 칸이 없거나 비어 있으면 서버를 켤 때 바로 알려준다"""
    value = os.getenv(name, "").strip()
    if not value:
        raise ImproperlyConfigured(f".env에 {name} 값이 비어 있어요. docs/LOCAL_SETUP.md 3단계를 확인하세요.")
    return value


def env_list(name, default=""):
    return [v.strip() for v in os.getenv(name, default).split(",") if v.strip()]


SECRET_KEY = required("DJANGO_SECRET_KEY")
DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() == "true"
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": required("POSTGRES_DB"),
        "USER": required("POSTGRES_USER"),
        "PASSWORD": required("POSTGRES_PASSWORD"),
        "HOST": os.getenv("POSTGRES_HOST", "localhost"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
    }
}
```

- 필수 값(시크릿 키 · DB 이름 · 사용자 · 비밀번호)은 `required()`로 읽는다. 칸이 없거나 **비어 있으면** 서버를 켤 때 `ImproperlyConfigured: .env에 ○○ 값이 비어 있어요`로 바로 알려준다. (`os.environ["..."]`만 쓰면 `.env.example`을 복사만 해서 빈 칸인 경우를 못 잡는다)
- 필수가 아닌 값(호스트 · 포트 · 허용 호스트)은 `os.getenv`로 읽고 기본값을 둔다.
- `DEBUG` 기본값은 `False`로 둔다. `.env`가 없는 서버에서 실수로 디버그 모드가 켜지는 것을 막는다. `True` · `true` 모두 켜짐으로 읽는다.
- 새 필수 값을 추가하면 `required("이름")`으로 읽고, `.env.example`과 이 문서에도 같이 적는다.

---

## 5. 보안 규칙

- `.env`는 절대 커밋하지 않는다. (`.gitignore`에 등록됨, `.env.example`만 커밋)
- 실제 키를 README · 이슈 · PR · 단톡방에 그대로 붙여넣지 않는다. 팀원에게는 개인 메시지로만 전달한다.
- 키가 커밋·노출되면 파일만 지우지 말고 **발급처에서 키를 즉시 재발급**한다.
- 개발·시연에는 합성·모의 데이터와 CODEF **데모 버전**(`CODEF_ENV=demo`)을 사용한다. 데모 키는 팀이 일일 호출 한도를 같이 쓰므로, 응답은 JSON으로 저장해두고 저장본으로 개발한다. (`sandbox`는 라이브러리 공용 키로 도는 연습용, `product`는 실서비스용)

예산 A/B 연결은 `BUDGET_DESIGN_ENABLED=true`로 설정한다. 외부 추천을 허용할 때는 `BUDGET_DESIGN_EXTERNAL_ENABLED=true`를 추가한다. `CHAT_ENGINE=router`, `CHAT_LLM=live`에서 일반 라우터는 목적지를 분류하며, 예산 조정 에이전트로 전달하기 전에 조정 전용 동의 두 개를 확인한다. 분배용 동의는 재사용하지 않는다. 외부 기능 OFF에서는 기존 동의 철회만 허용한다. ML 실행 파일과 모델 위치는 `BUDGET_INFERENCE_PYTHON`, `BUDGET_MODEL_PATH`로 설정할 수 있다.

# 개발 환경 · 환경변수 설정 가이드

머니쿠션 로컬 개발 환경을 준비하고 `.env`를 설정하는 방법을 정리한다.

| 항목 | 기준 |
|---|---|
| Python | **3.12** |
| 웹 프레임워크 | Django 5.2 LTS (백엔드 · 화면 모두 Django 템플릿 + HTMX) |
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
| `DJANGO_CSRF_TRUSTED_ORIGINS` | 배포 시 | `https://도메인` | HTTPS 배포 주소. 로컬은 비워둠 |
| `POSTGRES_DB` | ✅ | `money_cushion` | DB 이름 |
| `POSTGRES_USER` | ✅ | `money_cushion` | DB 사용자 |
| `POSTGRES_PASSWORD` | ✅ | (각자 설정) | DB 비밀번호 |
| `POSTGRES_HOST` | ✅ | `localhost` | 배포 시 RDS 엔드포인트 |
| `POSTGRES_PORT` | ✅ | `5432` | DB 포트 |

### 외부 API (발급 후 입력)

| 변수 | 발급처 | 설명 |
|---|---|---|
| `CODEF_CLIENT_ID` | CODEF 개발자센터 | 금융 거래 API 클라이언트 ID |
| `CODEF_CLIENT_SECRET` | CODEF 개발자센터 | 클라이언트 시크릿 |
| `CODEF_PUBLIC_KEY` | CODEF 개발자센터 | 계정 비밀번호 RSA 암호화용 공개키 |
| `CODEF_ENV` | — | `sandbox`(샌드박스) · `demo`(데모) · `product`(정식) 중 사용 환경 |
| `DATA_GO_KR_SERVICE_KEY` | 공공데이터포털 | 상가(상권)정보 · 복지서비스 API 공통 인증키 (활용 신청은 API별로 따로) |

### 제공자 확정 후 추가

| 변수 | 설명 |
|---|---|
| `OPENAI_API_KEY` | LLM 에이전트 호출용 (LLM 제공자 확정 후) |
| `KAKAO_REST_API_KEY` | 검색/지도 API (카카오 기준. 네이버로 정하면 `NAVER_CLIENT_ID` · `NAVER_CLIENT_SECRET`) |
| AWS 관련 (S3 · SQS 등) | 배포 구조 확정 후 추가 |

> 새 환경변수를 추가하면 **`.env.example`과 이 문서를 함께 갱신**한다. 값은 비워서 올린다.

---

## 3. 로컬 PostgreSQL 준비

둘 중 하나를 선택한다.

**A. Docker (권장)** — 설치가 간단하고 팀원 간 버전이 같아진다.

```bash
docker run --name money-cushion-db \
  -e POSTGRES_DB=money_cushion \
  -e POSTGRES_USER=money_cushion \
  -e POSTGRES_PASSWORD=<.env와 같은 비밀번호> \
  -p 5432:5432 -d postgres:17
```

**B. 직접 설치 (macOS)**

```bash
brew install postgresql@17
brew services start postgresql@17
psql postgres -c "CREATE USER money_cushion WITH PASSWORD '<비밀번호>' CREATEDB;"
psql postgres -c "CREATE DATABASE money_cushion OWNER money_cushion;"
```

> `CREATEDB` 권한은 Django 테스트(`manage.py test`)가 테스트 DB를 만들 때 필요하다.

---

## 4. Django 설정에서 읽기

`settings.py` 예시. `manage.py`가 프로젝트 루트에 있고 `settings.py`가 그 한 단계 아래 폴더에 있는 구조를 기준으로 한다.

```python
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_list(name: str) -> list[str]:
    return [v.strip() for v in os.getenv(name, "").split(",") if v.strip()]


SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = os.getenv("DJANGO_DEBUG", "False") == "True"
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["POSTGRES_DB"],
        "USER": os.environ["POSTGRES_USER"],
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.getenv("POSTGRES_HOST", "localhost"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
    }
}
```

- 필수 값은 `os.environ["..."]`로 읽어 **빠지면 바로 에러**가 나게 한다.
- `DEBUG` 기본값은 `False`로 둔다. `.env`가 없는 서버에서 실수로 디버그 모드가 켜지는 것을 막는다.

---

## 5. 보안 규칙

- `.env`는 절대 커밋하지 않는다. (`.gitignore`에 등록됨, `.env.example`만 커밋)
- 실제 키를 README · 이슈 · PR · 단톡방에 그대로 붙여넣지 않는다. 팀원에게는 개인 메시지로만 전달한다.
- 키가 커밋·노출되면 파일만 지우지 말고 **발급처에서 키를 즉시 재발급**한다.
- 개발·시연에는 합성·모의 데이터와 CODEF 샌드박스를 사용한다.

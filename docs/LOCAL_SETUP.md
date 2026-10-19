# 로컬 개발 환경 세팅 가이드 (Django + React + PostgreSQL)

처음 받은 저장소에서 **React 화면이 Django API를 거쳐 Docker PostgreSQL까지 연결되는 것**까지 따라 하는 가이드다.
환경변수 설명은 [`ENV_SETUP.md`](ENV_SETUP.md) 참고.

| 항목 | 기준 |
|---|---|
| Python | 3.12 |
| Django | 5.2 LTS + Django REST framework (`requirements.txt`) |
| Node.js | 22 이상 (LTS) · React 화면용 (`frontend/package.json`) |
| DB | PostgreSQL 17 (Docker 컨테이너 `money-cushion-db`) |
| Django 위치 | `backend/` (`backend/manage.py`, 설정 `backend/config/settings.py`) |
| React 위치 | `frontend/` (화면 코드 `frontend/src/`) |
| `.env` 위치 | 프로젝트 루트 (settings.py가 `backend`의 한 칸 위에서 읽음) |

## 전체 흐름

| 단계 | 할 일 | 확인 |
|---|---|---|
| 0 | 사전 설치 (Python 3.12 · Node.js · Git · Docker Desktop) | `docker --version` · `node -v` |
| 1 | 저장소 받기 | `backend/manage.py` 존재 |
| 2 | 가상환경 + 패키지 설치 | `python -m django --version` → `5.2.x` |
| 3 | `.env` 만들기 | 필수 칸 채움 |
| 4 | PostgreSQL 컨테이너 실행 | `docker ps`에 `money-cushion-db` |
| 5 | `migrate` | `Applying ... OK` |
| 6 | 관리자 계정 + 서버 실행 | `/admin` 로그인 |
| 7 | React 화면 실행 (터미널 하나 더) | `http://localhost:5173`에 홈 화면 |

---

## 🪟 Windows (PowerShell)

### 0. 사전 설치

- **Python 3.12**: python.org 설치 파일. 설치 첫 화면에서 **Add python.exe to PATH 체크**
- **Node.js**: nodejs.org에서 **LTS** 설치 파일 (npm도 같이 설치됨)
- **Git**: git-scm.com
- **Docker Desktop**: docker.com. 설치 중 **WSL 2 사용** 선택 → 설치 후 재부팅 → Docker Desktop 실행 (작업표시줄에 고래 아이콘)

```powershell
py -3.12 --version
node -v      # v22 이상
npm -v
docker --version
```

### 1. 저장소 받기

```powershell
git clone https://github.com/20260528-skn-family-ai-33/SKN33-Final-4Team.git
cd SKN33-Final-4Team
```

이미 받아뒀다면 `git pull`. `backend\manage.py`가 있어야 한다.

### 2. 가상환경 + 패키지 설치

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m django --version   # 5.2.x 확인
```

> **`Activate.ps1`을 실행할 수 없다는 빨간 에러**가 나면 한 번만 실행 후 다시 시도:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

프롬프트 앞에 `(.venv)`가 보이면 가상환경이 켜진 것. **이후 모든 명령은 `(.venv)`가 보이는 상태에서** 실행한다.

### 3. `.env` 만들기

```powershell
copy .env.example .env
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

출력된 값을 `.env`의 `DJANGO_SECRET_KEY=` 뒤에 붙여넣고, `POSTGRES_PASSWORD`에 **내가 정한 로컬 DB 비밀번호**를 적는다. 나머지는 예시값 그대로 둔다.

### 4. PostgreSQL 컨테이너 실행

`비밀번호` 부분을 3단계의 `POSTGRES_PASSWORD`와 **똑같이** 바꿔서 한 줄로 실행:

```powershell
docker run --name money-cushion-db -e POSTGRES_DB=money_cushion -e POSTGRES_USER=money_cushion -e POSTGRES_PASSWORD=비밀번호 -p 5432:5432 -d postgres:17
docker ps
```

### 5. migrate

```powershell
cd backend
python manage.py migrate
```

### 6. 관리자 계정 + 서버 실행

```powershell
python manage.py createsuperuser
python manage.py runserver
```

브라우저에서 `http://127.0.0.1:8000/admin` 접속 → 로그인되면 Django 쪽은 완료. **이 터미널은 켜둔 채로** 7단계로.

### 7. React 화면 실행

PowerShell 창을 **하나 더** 열고 (가상환경은 필요 없음):

```powershell
cd SKN33-Final-4Team\frontend
npm install        # 처음 한 번 + package.json이 바뀌었을 때
npm run dev
```

브라우저에서 `http://localhost:5173` → 홈 화면이 숫자와 함께 보이면 완료.

---

## 🍎 macOS (터미널)

### 0. 사전 설치

```bash
brew install python@3.12 node
```

Docker Desktop을 docker.com에서 설치 후 실행 (메뉴바에 고래 아이콘).

```bash
python3.12 --version
node -v      # v22 이상
npm -v
docker --version
```

### 1. 저장소 받기

```bash
git clone https://github.com/20260528-skn-family-ai-33/SKN33-Final-4Team.git
cd SKN33-Final-4Team
```

### 2. 가상환경 + 패키지 설치

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m django --version   # 5.2.x 확인
```

> conda를 쓰면 프롬프트에 `(base)`도 같이 보인다. **`(.venv)`가 켜져 있는지** 꼭 확인. 안 켜진 상태로 `django-admin` 등을 실행하면 conda 쪽 다른 버전의 Django가 실행된다.

### 3. `.env` 만들기

```bash
cp .env.example .env
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

출력값을 `DJANGO_SECRET_KEY=` 뒤에, 로컬 DB 비밀번호를 `POSTGRES_PASSWORD=` 뒤에 적는다.

### 4. PostgreSQL 컨테이너 실행

```bash
docker run --name money-cushion-db \
  -e POSTGRES_DB=money_cushion \
  -e POSTGRES_USER=money_cushion \
  -e POSTGRES_PASSWORD=비밀번호 \
  -p 5432:5432 -d postgres:17
docker ps
```

### 5. migrate

```bash
cd backend
python manage.py migrate
```

### 6. 관리자 계정 + 서버 실행

```bash
python manage.py createsuperuser
python manage.py runserver
```

`http://127.0.0.1:8000/admin` 로그인되면 Django 쪽은 완료. **이 터미널은 켜둔 채로** 7단계로.

### 7. React 화면 실행

터미널 탭을 **하나 더** 열고 (가상환경은 필요 없음):

```bash
cd SKN33-Final-4Team/frontend
npm install        # 처음 한 번 + package.json이 바뀌었을 때
npm run dev
```

브라우저에서 `http://localhost:5173` → 홈 화면이 숫자와 함께 보이면 완료.

---

## 매일 작업 시작할 때 (공통)

| 순서 | Windows | macOS |
|---|---|---|
| Docker Desktop 실행 | 앱 실행 | 앱 실행 |
| DB 컨테이너 켜기 | `docker start money-cushion-db` | `docker start money-cushion-db` |
| 가상환경 켜기 | `.venv\Scripts\Activate.ps1` | `source .venv/bin/activate` |
| Django 서버 (터미널 1) | `cd backend` → `python manage.py runserver` | 같음 |
| React 화면 (터미널 2) | `cd frontend` → `npm run dev` | 같음 |
| 보기 | `http://localhost:5173` | 같음 |

> `docker run`은 **처음 한 번만**. 이후엔 `docker start`.
> `git pull` 후 `requirements.txt`가 바뀌었으면 `pip install -r requirements.txt`, `frontend/package.json`이 바뀌었으면 `npm install`을 다시 한다.
> `docker rm money-cushion-db`로 컨테이너를 지우면 **DB 데이터도 사라진다.**

## 자주 나는 에러

| 에러 | 원인 | 해결 |
|---|---|---|
| `KeyError: 'DJANGO_SECRET_KEY'` | `.env`에 시크릿 키 없음 / `.env`가 루트에 없음 | 3단계 다시. `.env`는 `backend` 안이 아니라 **프로젝트 루트**에 |
| `password authentication failed` | `.env` 비밀번호 ≠ 컨테이너 비밀번호 | 둘을 맞추거나 컨테이너 재생성: `docker rm -f money-cushion-db` 후 4단계 |
| `connection refused` (5432) | DB 컨테이너가 꺼져 있음 | Docker Desktop 실행 → `docker start money-cushion-db` |
| `port is already allocated` (5432) | PC에 PostgreSQL이 따로 설치돼 실행 중 | 기존 PostgreSQL 서비스 중지. 또는 `-p 5433:5432`로 띄우고 `.env`의 `POSTGRES_PORT=5433` |
| `Conflict. The container name ... is already in use` | 이미 만든 컨테이너 | `docker start money-cushion-db` |
| `No module named 'django'` / `'rest_framework'` | 가상환경 꺼짐 / 패키지 안 깔림 | 가상환경 켜고 `pip install -r requirements.txt` |
| 화면에 **"데이터를 못 불러왔어요"** | Django 서버가 꺼져 있음 | 터미널 1에서 `python manage.py runserver` |
| `npm: command not found` / `'npm'은(는) ... 아닙니다` | Node.js 미설치 | 0단계의 Node.js 설치 후 터미널 다시 열기 |
| `Cannot find module` / `Failed to resolve import` | `npm install` 안 함 | `frontend`에서 `npm install` |
| `Port 5173 is in use` | React 서버가 이미 켜져 있음 | 켜진 창을 쓰거나 `Ctrl+C`로 끄고 다시 |

## CODEF (선택)

- 키는 `.env`의 `CODEF_DEMO_ID` · `CODEF_DEMO_SECRET` · `CODEF_PUBLIC_KEY` · `CODEF_CONNECTED_ID`에 넣는다.
- 키는 단톡방이 아니라 **개인 메시지**로 전달받는다.
- 데모 키 하나를 팀이 같이 쓰면 **일일 호출 한도도 같이 쓴다.** 응답은 JSON으로 저장해두고 개발할 때는 저장본을 쓴다.
- 실제 거래내역이 담긴 응답 파일은 **커밋하지 않는다.**

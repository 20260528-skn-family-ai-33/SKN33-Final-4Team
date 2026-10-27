# Swagger API 문서 사용법

기존 `docs/api/*.md`의 현재 명세를 OpenAPI 3.0.3으로 정리했다. Swagger UI에서 기능별 요청·응답·오류 예시를 확인할 수 있다. 문서 작성과 서버 구현은 별도 상태다.

## 실행하고 보기

프로젝트 루트에서 가상환경을 켠 뒤 의존성을 설치한다. 최초 개발 환경 설정은 [로컬 설정 가이드](LOCAL_SETUP.md)를 따른다.

```bash
python -m pip install -r requirements.txt
python backend/manage.py runserver
```

| 주소 | 용도 |
|---|---|
| http://127.0.0.1:8000/api/docs/ | **전체 명세 35개**. 기능별 계약을 읽는 화면이며 실행 버튼은 표시하지 않는다. |
| http://127.0.0.1:8000/api/docs/runtime/ | **현재 서버 확인**. `Try it out → Execute`로 실제 요청을 보낸다. 구현된 26개를 저장·수정·삭제까지 실행한다. |
| http://127.0.0.1:8000/api/schema/ | 전체 계약의 OpenAPI JSON |
| http://127.0.0.1:8000/api/schema/runtime/ | 현재 서버 확인용 OpenAPI JSON |

Swagger UI 자산은 `drf-spectacular-sidecar` 패키지에서 제공한다. 실행 중 외부 CDN에 접근하지 않는다. 개발 서버는 `DJANGO_DEBUG=True`일 때 정적 파일을 제공하며, 배포에서는 일반 Django 정적 파일 배포 설정을 적용한다.

## 현재 범위

전체 명세에는 인증·금융 동의 및 연결·카드·대출·거래·고정지출·정기 저축·홈·챗봇 API 35개가 있다. 각 API 제목과 설명에서 구현 상태를 확인한다.

| 상태 | 개수 | 의미 |
|---|---:|---|
| 구현 | 26 | CSRF 1 · 인증 4(가입·로그인·로그아웃·내 계정) · 금융 동의 및 연결 4(동의·계좌 후보·계좌 선택·연결 목록) · 거래 3(목록·한 건·분류 수정) · 고정지출 6 · 정기 저축 6 · 홈 1 · 이전 대화 1 |
| 목업 | 3 | 연결 해제 · 챗봇 메시지 · 작업 확인. 예시 응답만 돌려주는 껍데기이며 입력 검증·DB 저장까지 완료된 상태가 아니다. |
| 미구현 | 6 | 카드 3(후보·선택·청구서) · 대출 3(목록·보완·직접 등록). 계약만 있고 서버 주소가 없다. |

‘전체 명세’에는 확정 계약의 형식을 표시하고, ‘현재 서버 확인’에는 구현된 26개를 표시한다. 껍데기 3개와 미구현 6개는 실제 동작처럼 보이지 않도록 실행 화면에서 뺐다. 목업의 실제 응답은 확정 계약과 다를 수 있다. 서버에 있는 예산·쿠션 목업 등 이번 확정 명세 범위 밖의 API는 이 35개에 포함하지 않았다. 금융기관 신규 인증·새 연결·재조회처럼 결정 후 작성하는 API도 추가하지 않았다.

현재 서버 확인 화면에서는 `implemented`로 표시한 API를 저장·수정·삭제(PUT 포함)까지 호출한다. 실제 DB가 바뀌므로 연습용 계정으로 확인한다. 구현되지 않은 기능이 동작하는 것처럼 보이지 않도록 전체 계약 화면의 실행 기능은 꺼 두었다. 문서 자체를 조회하는 코드는 DB 쿼리를 실행하지 않는다. 기본 Django 실행 환경은 프로젝트 설정을 사용한다.

## 현재 서버 확인에서 테스트하는 순서

1. `GET /api/csrf/` 실행 → `csrftoken` 쿠키가 생긴다.
2. `POST /api/auth/signup/`(새 연습 계정) 또는 `POST /api/auth/login/` 실행 → 세션 쿠키가 생긴다. 이후 요청에 자동으로 붙는다.
3. `GET /api/auth/me/`로 로그인됐는지 확인한다.
4. 나머지 API를 실행한다. 주소에 `{id}`가 있는 API는 목록 API 응답에서 ID를 복사해 넣는다.
5. 끝나면 `POST /api/auth/logout/`.

- 계좌 후보·연결 목록·거래·이전 대화는 **DB에 저장된 데이터만 읽는다**(CODEF를 새로 부르지 않음). 데이터가 없으면 빈 목록이나 404가 정상이다.
- 계좌 후보·계좌 선택은 금융 조회 동의(`POST /api/finance/consents/`)를 먼저 해야 한다. 없으면 403 `consent_required`.

## 쿠키·CSRF와 요청 규칙

- 인증 계약은 Django 세션 쿠키 방식이다. JWT나 access token을 입력하지 않는다. 브라우저가 같은 출처의 세션 쿠키를 전송한다.
- 회원가입·로그인·로그아웃을 포함한 저장 요청은 `csrftoken` 쿠키와 `X-CSRFToken` 헤더가 필요하다. 현재 서버 확인 화면은 `csrftoken` 쿠키 값을 `X-CSRFToken` 헤더에 자동으로 넣는다. 로그인한 주소와 같은 주소(localhost 또는 127.0.0.1)로 열어야 쿠키가 전달된다.
- Swagger 요청은 현재 화면과 같은 출처에만 허용한다. 인증 정보를 브라우저 저장소에 유지하지 않으며 외부 Swagger 검증 서버에 명세를 보내지 않는다.
- API JSON 필드는 camelCase, 돈은 원 단위 정수, ID는 UUID다. 입력 항목 제한·PATCH의 생략과 null·멱등 요청 번호·오류 응답은 [공통 규칙](API_RULES.md)을 따른다.
- 문서의 예시는 가상 값이다. 실제 개인정보·금융 조회 원문·쿠키·비밀번호를 명세 예시에 넣지 않는다.

## 파일과 갱신 순서

| 파일 | 역할 |
|---|---|
| `docs/api/*.md`, `docs/API_RULES.md` | 팀이 관리하는 기능별 명세와 공통 규칙 |
| `docs/openapi/parts/*.json` | 각 기능의 OpenAPI 원본. 요청·응답 스키마, 예시, 설명을 관리한다. |
| `backend/config/openapi.py` | 기능별 원본을 합치고 공통 정의·구현 상태·현재 서버 응답을 구성한다. |
| `docs/openapi/openapi.json` | 공유·검토용으로 생성한 전체 계약 JSON |
| `docs/openapi/openapi.yaml` | Swagger Editor 등 OpenAPI 도구에 가져갈 수 있는 전체 계약 YAML |
| `backend/config/api_docs.py` | JSON 문서와 Swagger UI 제공 |

현재 문서는 모델에서 자동 추론한 완성본이 아니라 기존 합의 명세를 옮긴 **수동 관리 계약**이다. 새 API를 개발하거나 계약을 바꿀 때 다음을 함께 갱신한다.

1. 기능별 Markdown 명세와 공통 규칙의 변경점을 확인한다.
2. 해당 `docs/openapi/parts/*.json`의 필드·필수 여부·범위·오류·예시를 수정한다. 원문에서 미정인 내용은 임의로 확정하지 않는다.
3. `backend/config/openapi.py`의 구현 상태 목록을 수정한다. 실제 API를 검증한 뒤 `planned` → `implemented`로 바꾸며, 목업은 `mock`으로 유지한다. 현재 서버 확인 대상과 응답 스키마도 실제 코드에 맞춘다.
4. 프로젝트 루트에서 문서를 다시 생성하고 검증한다.

```bash
python -m pip install -r requirements-docs.txt
python scripts/build_openapi.py --validate
python scripts/build_openapi.py --check --validate
```

첫 명령은 검증 도구를 설치한다. 두 번째 명령은 JSON·YAML을 다시 생성하며, 세 번째는 생성 결과가 저장된 파일과 같은지 확인하고 명세를 검증한다. 이 문서 생성·검증 작업에는 데이터베이스가 필요하지 않다. 생성 파일을 직접 고치면 다음 생성 시 덮어써지므로 `parts` 원본을 수정한다.

새 API의 요청·응답을 Serializer로 정의해 자동 생성으로 전환할 때도 기존 계약과 실제 응답의 차이를 먼저 확인한다. `manage.py spectacular`이 현재 수동 계약 35개를 그대로 재생성하는 명령은 아니다.

## 참고

- [drf-spectacular 공식 문서](https://drf-spectacular.readthedocs.io/en/stable/readme.html)
- [Swagger UI 설정](https://swagger.io/docs/open-source-tools/swagger-ui/usage/configuration/)
- [기능별 API 명세 목록](api/README.md)

"""확정 명세와 현재 목업을 구분한 OpenAPI 문서. DB나 Django 설정 없이 생성 가능."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PARTS = ROOT / "docs" / "openapi" / "parts"
DOMAINS = ("auth", "finance-connect", "cards", "loans", "transactions", "fixed", "savings", "home", "chat")
METHODS = {"get", "post", "put", "patch", "delete"}
CATEGORIES = ["FOOD", "HOUSING_TELECOM", "TRANSPORT_CAR", "HOUSEHOLD", "FASHION_SHOPPING", "MEDICAL_HEALTH", "BEAUTY", "EDUCATION", "CULTURE_LEISURE", "TRAVEL_LODGING", "ETC", "UNCLASSIFIED"]
TAGS = ["공통 · 인증", "금융 동의 · 연결", "카드", "대출", "거래", "고정지출", "정기 저축", "홈", "통합 챗봇"]

# 실제 API 구현 및 계약 검증이 끝났을 때 변경. URL 존재만으로 구현 완료가 되지 않는다.
IMPLEMENTATION = {
    ("get", "/api/csrf/"): "implemented",
    ("get", "/api/home/"): "mock",
    ("get", "/api/finance/transactions/"): "mock",
}
for _domain in ("fixed", "savings"):
    for _method, _suffix in (("get", ""), ("post", ""), ("patch", "{id}/"), ("delete", "{id}/"), ("post", "confirm/"), ("post", "ignore/")):
        IMPLEMENTATION[(_method, f"/api/budgets/{_domain}/{_suffix}")] = "mock"
IDEMPOTENT_PATHS = {
    "/api/finance/consents/", "/api/finance/loans/", "/api/chat/messages/",
    "/api/budgets/fixed/", "/api/budgets/fixed/confirm/", "/api/budgets/fixed/ignore/",
    "/api/budgets/savings/", "/api/budgets/savings/confirm/", "/api/budgets/savings/ignore/",
}


def _display(kind):
    return {
        "type": "object", "required": [kind + "Id", "displayName", "status"],
        "properties": {
            kind + "Id": {"type": "string", "format": "uuid"},
            "displayName": {"type": "string", "description": "서버가 만든 표시명. 번호는 끝 네 자리만 표시."},
            "status": {"type": "string", "enum": ["connected", "relogin", "disconnected"]},
        },
    }


def _base():
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "머니쿠션 API 명세서", "version": "2026-10-24",
            "description": (
                "확정 명세 35개 · 9개 기능 묶음. **명세 작성 완료와 API 구현 완료는 다릅니다.**\n\n"
                "현재 구현 1개(CSRF), 목업 14개(홈·거래 목록·고정지출·정기 저축), 미구현 20개입니다. "
                "전체 명세는 읽기 전용이며, 실제 응답 확인은 [현재 서버 확인](/api/docs/runtime/)에서 합니다. "
                "목업의 입력 검증·인증·DB 연동은 완료되지 않았고, 홈·거래 목록의 응답 형태도 확정 계약과 다릅니다.\n\n"
                "기준: docs/api/*.md, docs/API_RULES.md, 결정 AA·AB. "
                "요청·응답은 camelCase, 금액은 원 단위 정수, ID는 UUID, 날짜는 YYYY-MM-DD, 시각은 ISO 8601. "
                "빈 값은 null이며 PATCH의 생략은 유지, null은 허용된 칸만 비웁니다. 알 수 없는 요청 필드는 400.\n\n"
                "인증은 Django 세션 쿠키입니다(JWT 없음). 변경 요청에는 X-CSRFToken, "
                "일부 생성·확정 요청에는 Idempotency-Key를 보냅니다. 세션 없음은 403 not_authenticated, "
                "주소의 없는·남의·잘못된 ID는 404, 본문의 부적합한 ID는 400입니다. "
                "오류의 fields는 입력 항목 오류일 때만 존재합니다.\n\n"
                "미정 항목은 설명에 남겼습니다. ML 후 예산·쿠션, 비정기 준비금(2차), "
                "금융기관 새 연결·재조회 등 아직 작성하지 않은 명세는 포함하지 않습니다."
            ),
        },
        "servers": [{"url": "/", "description": "문서를 제공하는 동일 서버"}],
        "tags": [{"name": name} for name in TAGS],
        "security": [{"sessionAuth": []}],
        "paths": {},
        "components": {
            "securitySchemes": {"sessionAuth": {
                "type": "apiKey", "in": "cookie", "name": "sessionid",
                "description": "Django 로그인으로 발급되는 세션 쿠키. 브라우저가 동일 출처 요청에 자동 전송. Authorize에 JWT를 넣는 방식이 아닙니다.",
            }},
            "parameters": {
                "CsrfToken": {
                    "name": "X-CSRFToken", "in": "header", "required": True,
                    "description": "GET /api/csrf/로 받은 csrftoken 쿠키 값. 로그인 후 토큰이 바뀌면 최신 값을 사용합니다.",
                    "schema": {"type": "string"},
                },
                "IdempotencyKey": {
                    "name": "Idempotency-Key", "in": "header", "required": False,
                    "description": "화면에서 보내는 요청 번호(선택 헤더, 생략하면 중복 방지 보장 없음). 8~64자 영문·숫자·하이픈·밑줄, UUID도 가능. 같은 회원·번호·내용 재전송은 처음 응답, 내용이 다르면 409. 실패는 기록하지 않음. 폼을 다시 열거나 새 동작을 시작할 때 새 번호를 만듭니다.",
                    "schema": {"type": "string", "minLength": 8, "maxLength": 64, "pattern": "^[A-Za-z0-9_-]{8,64}$"},
                },
            },
            "schemas": {
                "Error": {
                    "type": "object", "required": ["error"],
                    "properties": {"error": {
                        "type": "object", "required": ["code", "message"],
                        "properties": {
                            "code": {"type": "string", "description": "invalid, parse_error, not_authenticated, permission_denied, not_found, method_not_allowed, idempotency_conflict, consent_required, relogin_required, provider_unavailable, server_error 등. 사용 가능한 코드는 각 API 참조."},
                            "message": {"type": "string"},
                            "fields": {"type": "object", "description": "입력 항목 오류일 때만 포함. 여러 칸의 조합 오류는 nonFieldErrors.", "additionalProperties": {"type": "array", "items": {"type": "string"}}},
                        },
                    }},
                },
                "AccountDisplay": _display("account"), "CardDisplay": _display("card"),
                "CategoryCode": {"type": "string", "enum": CATEGORIES},
                "SelectableCategoryCode": {"type": "string", "enum": CATEGORIES[:-1], "description": "사용자 선택에는 UNCLASSIFIED 제외. ETC는 선택 가능."},
            },
        },
    }


def operations(schema):
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            if method in METHODS:
                yield path, method, operation


def build_contract():
    schema = _base()
    for domain in DOMAINS:
        part = json.loads((PARTS / f"{domain}.json").read_text())
        for path, item in part["paths"].items():
            target = schema["paths"].setdefault(path, {})
            if target.keys() & item.keys():
                raise ValueError(f"중복 API: {path}")
            target.update(item)
        for name, component in part.get("components", {}).get("schemas", {}).items():
            if name in schema["components"]["schemas"]:
                raise ValueError(f"중복 schema: {name}")
            schema["components"]["schemas"][name] = component
    for path, method, operation in operations(schema):
        state = IMPLEMENTATION.get((method, path), "planned")
        operation["x-implementation-status"] = state
        label = {"implemented": "구현", "mock": "목업", "planned": "미구현"}[state]
        operation["summary"] = f"[{label}] {operation['summary']}"
        source = operation["x-source"]
        note = {
            "implemented": "현재 서버에서 구현된 API입니다.",
            "mock": "현재 서버는 예시 응답을 반환합니다. 입력 검증·인증·DB 저장 기능은 아직 구현되지 않았습니다. 홈·거래 목록은 응답 구조·필터도 아래 확정 계약과 다릅니다. GET의 실제 응답은 현재 서버 확인 화면을 이용하세요.",
            "planned": "명세는 작성되었으며 서버 API는 아직 구현되지 않았습니다.",
        }[state]
        operation["description"] = f"**개발 상태: {label}** — {note}\n\n{operation.get('description', '')}\n\n원본: `{source}`"
        if method in {"post", "put", "patch", "delete"}:
            params = operation.setdefault("parameters", [])
            if not any(p.get("name", "").lower() == "x-csrftoken" or p.get("$ref", "").endswith("/CsrfToken") for p in params):
                params.append({"$ref": "#/components/parameters/CsrfToken"})
            if "403" in operation["responses"] and "permission_denied" not in operation["responses"]["403"]["description"]:
                operation["responses"]["403"]["description"] += " CSRF 실패 시 permission_denied."
            if method == "post" and path in IDEMPOTENT_PATHS:
                if not any(p.get("name", "").lower() == "idempotency-key" or p.get("$ref", "").endswith("/IdempotencyKey") for p in params):
                    params.append({"$ref": "#/components/parameters/IdempotencyKey"})
    source_files = ["docs/API_RULES.md", "docs/api/README.md", "docs/api/spending-analysis.md", "docs/결정기록_2026-10-21.md"] + [f"docs/api/{d}.md" for d in DOMAINS]
    schema["x-source-sha256"] = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in source_files}
    return schema


def _sample_schema(value):
    """현 서버 목업 전용: 샘플의 모양을 설명하며 확정 계약으로 사용하지 않는다."""
    if value is None:
        return {"nullable": True, "description": "현재 샘플은 null. 정식 타입은 확정 명세 참조."}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        variants = []
        for item in value:
            item_schema = _sample_schema(item)
            if item_schema not in variants:
                variants.append(item_schema)
        return {"type": "array", "items": {} if not variants else variants[0] if len(variants) == 1 else {"anyOf": variants}}
    return {"type": "object", "properties": {k: _sample_schema(v) for k, v in value.items()}, "required": list(value)}


def build_runtime_schema():
    # 현재 응답용 목업과 집계 함수를 재사용한다. DB나 CODEF를 호출하지 않는다.
    from budgets import mock as budget_mock
    from budgets import examples as budget_examples
    from budgets.api import _must_pay
    from config.api import camel
    from finance import mock as finance_mock
    from finance.api import filter_days, PENDING

    contract = build_contract()
    schema = deepcopy(contract)
    schema["paths"] = {}
    schema["security"] = []
    schema["info"]["title"] = "머니쿠션 — 현재 서버 확인"
    schema["info"]["description"] = "현재 명세 범위에서 GET으로 호출 가능한 5개를 보여 줍니다. CSRF는 구현, 홈·거래 목록·고정지출 목록·정기 저축 목록은 예시 데이터입니다. 이 화면의 스키마는 현재 샘플의 모양만 설명하며 입력 검증·인증·DB 저장 구현 완료를 뜻하지 않습니다. 현재 기본 권한은 AllowAny입니다. 예산·쿠션 시안은 이번 35개 확정 명세 범위 밖이라 제외했습니다."
    samples = {
        "/api/csrf/": {"ok": True},
        "/api/home/": camel({"period": budget_mock.PERIOD, "home": budget_mock.HOME, "must_pay": _must_pay(), "budget": None if budget_mock.BUDGET is None else {"sample": True}}),
        "/api/finance/transactions/": camel({"month": finance_mock.MONTH, "recent": finance_mock.RECENT, "pending_count": finance_mock.PENDING_COUNT, "pending_label": PENDING, "categories": finance_mock.CATEGORIES, "selected": "전체", "days": filter_days("전체")}),
        "/api/budgets/fixed/": camel(budget_examples.FIXED_LIST_EXAMPLE),
        "/api/budgets/savings/": camel(budget_examples.SAVINGS_LIST_EXAMPLE),
    }
    for path, sample in samples.items():
        operation = deepcopy(contract["paths"][path]["get"])
        operation["operationId"] = "runtime_" + operation["operationId"]
        operation["security"] = []
        operation["parameters"] = []
        operation["description"] = "현재 구현의 실제 응답 구조입니다. " + ("CSRF 쿠키를 발급합니다." if path == "/api/csrf/" else "목업 데이터이며 확정 계약과 다릅니다. 인증·저장·금융 연동 구현 완료를 뜻하지 않습니다.")
        operation["responses"] = {"200": {"description": "현재 서버 응답", "content": {"application/json": {"schema": _sample_schema(sample), "example": sample}}}}
        if path == "/api/finance/transactions/":
            operation["parameters"] = [{"name": "category", "in": "query", "required": False, "description": "목업은 한글 이름을 사용. 정식 명세는 영문 코드.", "schema": {"type": "string", "default": "전체", "example": "식비"}}]
        schema["paths"][path] = {"get": operation}
    schema["tags"] = [t for t in schema["tags"] if t["name"] in {"공통 · 인증", "홈", "거래", "고정지출", "정기 저축"}]
    # 실행 화면은 별도 문서: 사용하지 않는 확정 계약 모델을 함께 보여 주지 않는다.
    schema["components"] = {}
    return schema

"""동의 문서 원문 보관소 — 화면 표시(GET /api/consent-documents/)와 동의 기록 해시가 모두 여기서 나온다

- 문서는 (목적, 범위, 버전)으로 구분한다. 회원가입 약관은 범위가 None, 금융 조회는 bank · card · loan.
- 원문 = JSON 파일에 담긴 문서 객체(label · title · required · version · date · sections) 그대로.
  해시 = 그 객체를 키 정렬 · 공백 없는 JSON(UTF-8, 한글 그대로)으로 만든 SHA-256. 화면은 이 객체를 받아 그대로 그린다.
- 내용을 고칠 때는 파일을 덮어쓰지 말고 새 버전 파일을 추가한다. 과거 동의 기록의 해시가 그 파일 원문을 가리킨다.
"""
import hashlib
import json
from pathlib import Path

from rest_framework.exceptions import APIException, ValidationError

_HERE = Path(__file__).parent


def canonical_hash(document):
    text = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load(name):
    return json.loads((_HERE / name).read_text(encoding="utf-8"))


_SIGNUP_V1 = _load("signup_terms_v1.json")
_FINANCE_V2 = _load("finance_inquiry_v2.json")
_BUDGET_AI_V1 = _load("budget_ai_v1.json")
_BUDGET_AI_V2 = _load("budget_ai_v2.json")
_BUDGET_AI_V3 = _load("budget_ai_v3.json")
_BUDGET_AI_HEALTH_V1 = _load("budget_ai_health_v1.json")
_BUDGET_ADJUST_AI_V1 = _load("budget_adjust_ai_v1.json")
_BUDGET_ADJUST_AI_HEALTH_V1 = _load("budget_adjust_ai_health_v1.json")

# 금융 조회 v1: 화면 원문이 아니라 임시 문장 한 줄을 그대로 UTF-8 SHA-256 한 문서 (11/06 전 기록).
# 그 기록의 해시를 다시 확인할 수 있게 문장과 방식을 그대로 남긴다. 새 동의는 받지 않는다
FINANCE_V1_PLACEHOLDER = "머니쿠션 금융 정보 조회 동의 v1 (최종 문구 확정 전 임시 원문)"
FINANCE_V1_HASH = hashlib.sha256(FINANCE_V1_PLACEHOLDER.encode("utf-8")).hexdigest()

# 지금까지 내보낸 모든 문서 (목적, 범위, 버전) → 원문 해시. 새 버전을 만들어도 옛 줄은 지우지 않는다
ARCHIVE = {
    ("budget_ai", None, "v1"): canonical_hash(_BUDGET_AI_V1),
    ("budget_ai", None, "v2"): canonical_hash(_BUDGET_AI_V2),
    ("budget_ai", None, "v3"): canonical_hash(_BUDGET_AI_V3),
    ("budget_ai_health", None, "v1"): canonical_hash(_BUDGET_AI_HEALTH_V1),
    ("budget_adjust_ai", None, "v1"): canonical_hash(_BUDGET_ADJUST_AI_V1),
    ("budget_adjust_ai_health", None, "v1"): canonical_hash(_BUDGET_ADJUST_AI_HEALTH_V1),
    ("terms", None, "v1"): canonical_hash(_SIGNUP_V1["terms"]),
    ("privacy", None, "v1"): canonical_hash(_SIGNUP_V1["privacy"]),
    **{("finance_inquiry", scope, "v1"): FINANCE_V1_HASH for scope in ("bank", "card", "loan")},
    **{("finance_inquiry", scope, "v2"): canonical_hash(_FINANCE_V2[scope]) for scope in ("bank", "card", "loan")},
}

# 지금 동의받는 문서. 화면은 이것만 받는다 (ARCHIVE 에 없는 버전이면 서버가 뜨지 않게 아래에서 확인)
CURRENT = {
    ("terms", None): _SIGNUP_V1["terms"],
    ("privacy", None): _SIGNUP_V1["privacy"],
    ("finance_inquiry", "bank"): _FINANCE_V2["bank"],
    ("finance_inquiry", "card"): _FINANCE_V2["card"],
    ("finance_inquiry", "loan"): _FINANCE_V2["loan"],
    ("budget_ai", None): _BUDGET_AI_V3,
    ("budget_ai_health", None): _BUDGET_AI_HEALTH_V1,
    ("budget_adjust_ai", None): _BUDGET_ADJUST_AI_V1,
    ("budget_adjust_ai_health", None): _BUDGET_ADJUST_AI_HEALTH_V1,
}
for (_purpose, _scope), _doc in CURRENT.items():
    assert ARCHIVE[(_purpose, _scope, _doc["version"])] == canonical_hash(_doc), "현재 문서는 ARCHIVE 원문과 같아야 해요"


class DocumentOutdated(APIException):
    """화면에서 본 문서가 더는 동의받지 않는 버전 → 409. 화면은 문서를 다시 받아 새로 확인받는다"""
    status_code = 409
    default_code = "document_outdated"
    default_detail = "약관이 새로 바뀌었어요. 바뀐 내용을 확인하고 다시 동의해 주세요."


def current_items():
    return [
        {"purpose": purpose, "product_scope": scope, "version": doc["version"],
         "document_hash": canonical_hash(doc), "document": doc}
        for (purpose, scope), doc in CURRENT.items()
    ]


def consent_hash(purpose, scope, version):
    """사용자가 본 버전의 원문 해시. 최신으로 바꿔 기록하지 않는다 — 지난 버전이면 409, 모르는 버전이면 400"""
    doc = CURRENT.get((purpose, scope))
    if doc is not None and doc["version"] == version:
        return ARCHIVE[(purpose, scope, version)]
    if (purpose, scope, version) in ARCHIVE:
        raise DocumentOutdated()
    raise ValidationError({"document_version": ["지원하지 않는 동의 문서 버전이에요."]})

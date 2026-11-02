"""역할별 해석 · 설명 문장의 Structured Outputs 스키마 (strict: 모든 칸 필수 · null 허용). 받은 뒤 역할별 parse · explain.checked 검사는 그대로.
평가 도구(stage_b)에서 옮김 (MEMO 25-12) — 실제 해석기(live) · 평가 도구가 같은 스키마를 쓴다
"""
from chat.interpretation.schema import CATEGORY_NAMES
from chat.router_core import reading as router_reading
from spending_analysis import claims as spending_claims
from spending_analysis import explain as spending_explain
from spending_analysis import reading as spending_reading

ROLES = {"router": (router_reading.PROMPT, router_reading.RouterReading, router_reading.parse),
         "spending": (spending_reading.PROMPT, spending_reading.SpendingReading, spending_reading.parse)}
ROLE_OF = {prompt: role for role, (prompt, _, _) in ROLES.items()}


def role_schema(role):
    """Structured Outputs 스키마 (strict: 모든 칸 필수 · null 허용). 받은 뒤 역할별 parse 검사는 그대로 한다"""
    nullable = lambda t: {"type": [t, "null"]}  # noqa: E731
    enum = lambda values: {"type": ["string", "null"], "enum": [*values, None]}  # noqa: E731
    strings = {"type": "array", "items": {"type": "string"}}
    categories = {"type": "array", "items": {"anyOf": [
        {"type": "string", "enum": list(CATEGORY_NAMES)},
        {"type": "object", "properties": {"unknown": {"type": "string"}}, "required": ["unknown"],
         "additionalProperties": False}]}}
    # 소비 해석만: 세부 지출 {detail, category | null, names: 가게 이름의 다른 표기} (서버가 붙이는 accepted 는 넣지 않는다)
    spending_categories = {"type": "array", "items": {"anyOf": [*categories["items"]["anyOf"], {
        "type": "object", "properties": {"detail": {"type": "string"}, "category": enum(CATEGORY_NAMES), "names": strings},
        "required": ["detail", "category", "names"], "additionalProperties": False}]}}
    alias = {"anyOf": [{"type": "object", "properties": {"word": {"type": "string"},
                                                          "category": {"type": "string", "enum": list(CATEGORY_NAMES)}},
                        "required": ["word", "category"], "additionalProperties": False}, {"type": "null"}]}
    if role == "router":
        r = router_reading
        props = {"intents": {"type": "array", "items": {"type": "string", "enum": list(r.INTENTS)}},
                 "followup": enum(r.FOLLOWUPS), "choice": enum(r.CHOICES), "resume": nullable("boolean"),
                 "categories": categories, "direction": enum(r.DIRECTIONS), "amount": nullable("integer"),
                 "amount_mode": enum(r.AMOUNT_MODES), "unclear": strings}
    else:
        r = spending_reading
        props = {"mine": {"type": "boolean"}, "target": enum(r.TARGETS), "period": enum(r.PERIODS),
                 "compare": enum(r.COMPARES), "months": nullable("integer"), "categories": spending_categories,
                 "followup": enum(r.FOLLOWUPS), "confirm": nullable("boolean"), "unclear": strings,
                 "refers_to": enum(r.REFERS), "explain": nullable("boolean"),
                 "after_budget_change": nullable("boolean"), "all_categories": nullable("boolean"),
                 "include_current": nullable("boolean"), "alias": alias, "payment": enum(r.PAYMENTS),
                 "explain_kind": enum(r.EXPLAIN_KINDS),
                 "weekday": {"type": "array", "items": {"type": "string", "enum": list(r.WEEKDAYS)}},
                 "time_slot": {"type": "array", "items": {"type": "string", "enum": list(r.TIME_SLOTS)}},
                 "amount_min": nullable("integer"), "amount_max": nullable("integer"), "measure": enum(r.MEASURES),
                 "group_by": enum(r.GROUPS), "top_n": nullable("integer"), "month": nullable("integer"),
                 "date_from": nullable("string"), "date_to": nullable("string"), "period_said": nullable("string"),
                 "include_fixed": nullable("boolean"), "other_person": nullable("boolean")}
    assert set(props) == set(ROLES[role][1].__dataclass_fields__), f"{role} 스키마와 해석 칸이 다름"
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


# 소비 설명 문장 (채점 역할 아님 — 로컬 채팅의 실제 호출용). 문장 수 1~3 · 숫자 금지 등은 받은 뒤 explain.checked 가 검사
EXPLAIN_SCHEMA = {"type": "object", "properties": {"sentences": {"type": "array", "items": {"type": "string"}}},
                  "required": ["sentences"], "additionalProperties": False}


# B 계획 · 답 (MEMO 26) — 받은 뒤 서버가 claims._plan · claims.check 로 다시 검사한다
PLAN_SCHEMA = {"type": "object", "properties": {
    "queries": {"type": "array", "items": {"type": "object", "properties": {
        "by": {"type": "string", "enum": list(spending_claims.AXES)}, "focus": {"type": ["string", "null"]}},
        "required": ["by", "focus"], "additionalProperties": False}},
    "reason": {"type": "string"},
    "hypothesis": {"anyOf": [{"type": "object", "properties": {
        "axis": {"type": "string", "enum": list(spending_claims.HYPOTHESIS_AXES)}, "subject": {"type": ["string", "null"]}},
        "required": ["axis", "subject"], "additionalProperties": False}, {"type": "null"}]}},
    "required": ["queries", "reason", "hypothesis"], "additionalProperties": False}
ANSWER_SCHEMA = {"type": "object", "properties": {"sentences": {"type": "array", "items": {"type": "object", "properties": {
    "text": {"type": "string"}, "claims": {"type": "array", "items": {"type": "string"}}},
    "required": ["text", "claims"], "additionalProperties": False}}}, "required": ["sentences"], "additionalProperties": False}


def schema_for(system):
    if system == spending_explain.PROMPT:
        return "spending_explanation", EXPLAIN_SCHEMA
    if system == spending_claims.PLAN_PROMPT:
        return "explain_plan", PLAN_SCHEMA
    if system == spending_claims.ANSWER_PROMPT:
        return "explain_answer", ANSWER_SCHEMA
    role = ROLE_OF[system]
    return f"{role}_reading", role_schema(role)

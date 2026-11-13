"""역할별 해석 · 설명 문장의 Structured Outputs 스키마 (strict: 모든 칸 필수 · null 허용). 받은 뒤 역할별 parse · 서버 검사는 그대로.
평가 도구(stage_b)에서 옮김 (MEMO 25-12) — 실제 해석기(live) · 평가 도구가 같은 스키마를 쓴다
"""
from chat.interpretation.schema import CATEGORY_NAMES
from chat.router_core import reading as router_reading
from spending_analysis import agent_loop as spending_agent_loop
from spending_analysis import agent_tools as spending_agent_tools
from spending_analysis import claims as spending_claims
from spending_analysis import investigation
from spending_analysis import supplement as spending_supplement
from spending_analysis import reading as spending_reading
from spending_analysis import insight as spending_insight
from spending_analysis import narrative as spending_narrative

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
                 "explain_kind": enum(r.EXPLAIN_KINDS), "hypothesis": investigation.SCHEMA,
                 "weekday": {"type": "array", "items": {"type": "string", "enum": list(r.WEEKDAYS)}},
                 "time_slot": {"type": "array", "items": {"type": "string", "enum": list(r.TIME_SLOTS)}},
                 "amount_min": nullable("integer"), "amount_max": nullable("integer"), "measure": enum(r.MEASURES),
                 "group_by": enum(r.GROUPS), "top_n": nullable("integer"), "month": nullable("integer"),
                 "date_from": nullable("string"), "date_to": nullable("string"), "period_said": nullable("string"),
                 "include_fixed": nullable("boolean"), "other_person": nullable("boolean"),
                 "compare_month": nullable("integer"), "multi_step": nullable("boolean"),
                 "analysis_scope": enum(("living", "total")),
                 "analysis_goal": enum(("reason", "habit", "overview", "forecast")),
                 "analysis_activity": enum(("dining", "delivery", "groceries", "cafe", "cinema", "performance", "exercise", "unknown")),
                 "analysis_planned": nullable("boolean")}
    assert set(props) == set(ROLES[role][1].__dataclass_fields__), f"{role} 스키마와 해석 칸이 다름"
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


# B 계획 · 답 (MEMO 26) — 받은 뒤 서버가 claims._plan · claims.check 로 다시 검사한다
PLAN_SCHEMA = {"type": "object", "properties": {
    "queries": {"type": "array", "items": {"type": "object", "properties": {
        "by": {"type": "string", "enum": list(spending_claims.AXES)}, "focus": {"type": ["string", "null"]}},
        "required": ["by", "focus"], "additionalProperties": False}},
    "reason": {"type": "string"},
    "hypothesis": investigation.SCHEMA},
    "required": ["queries", "reason", "hypothesis"], "additionalProperties": False}
# B 답의 보충(11/10): 숫자 없는 문장 1~2개 | null + 이유. 받은 뒤 supplement.check 가 다시 검사한다
EXTRA = {"extra": {"type": ["string", "null"]},
         "extra_reason": {"type": ["string", "null"], "enum": [*spending_supplement.REASONS, None]}}
ANSWER_SCHEMA = {"type": "object", "properties": {"sentences": {"type": "array", "items": {"type": "object", "properties": {
    "text": {"type": "string"}, "claims": {"type": "array", "items": {"type": "string"}}},
    "required": ["text", "claims"], "additionalProperties": False}}, **EXTRA},
    "required": ["sentences", *EXTRA], "additionalProperties": False}


def agent_judge_schema():
    """추가 판단 1회 (MEMO 31) — 도구는 서버가 허용한 것 중 하나, 인자는 나누기 기준(by)뿐. 받은 뒤 agent_loop._choice 가 다시 검사"""
    obj = lambda props: {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}  # noqa: E731
    return obj({"action": {"type": "string", "enum": ["tool", "answer"]},
                "tool": {"type": ["string", "null"], "enum": [*spending_agent_tools.TOOLS, None]},
                "args": {"anyOf": [obj({"by": {"type": "string", "enum": list(spending_claims.AXES)}}), {"type": "null"}]},
                "reason": {"type": "string"}})


def schema_for(system):
    if system == spending_narrative.CHECK_PROMPT:
        return "spending_insight_check", spending_narrative.CHECK_SCHEMA
    if system == spending_insight.PROMPT:
        return "spending_insight", spending_insight.SCHEMA
    if system == spending_agent_loop.PROMPT:
        return "spending_agent_judge", agent_judge_schema()
    from budgets.adjustment_core import financial_conversation as conversation, financial_planner as planner
    extra = {planner.PROMPT: ("budget_adjust_plan", planner.SCHEMA),
             conversation.DIALOGUE_PROMPT: ("budget_adjust_dialogue", conversation.DIALOGUE_SCHEMA),
             conversation.FEEDBACK_PROMPT: ("budget_adjust_feedback", conversation.FEEDBACK_SCHEMA),
             conversation.ANSWER_PROMPT: ("budget_adjust_answer", conversation.ANSWER_SCHEMA),
             conversation.ONE_OFF_PROMPT: ("budget_adjust_one_off", conversation.ONE_OFF_SCHEMA)}
    if system in extra:
        return extra[system]
    from budgets.adjustment_core import interpretation as budget_reading
    if system == budget_reading.PROMPT:
        return "budget_adjustment_reading", budget_reading.SCHEMA
    if system == spending_claims.PLAN_PROMPT:
        return "explain_plan", PLAN_SCHEMA
    if system == spending_claims.ANSWER_PROMPT:
        return "explain_answer", ANSWER_SCHEMA
    role = ROLE_OF[system]
    return f"{role}_reading", role_schema(role)

"""소비 분석 추가 판단 (MEMO 31 — MEMO 29 · 30 의 여러 단계 루프 · 요약 LLM 을 판단 1회로 줄였다)

첫 조회는 기존 경로(라우터 → 소비 해석 · 맥락 → dialog 검증 → adapter.run)가 이미 끝냈다. 여기서는
판단 LLM 1회 → answer(종료) 또는 추가 도구 1회(where_changed · budget_lookup) → 서버 문장 · 카드로 답한다.
- 첫 조회를 다시 고르거나 다시 실행하지 않는다. 추가 실행 뒤 판단 · 요약 LLM · B · narrate 를 부르지 않는다
- 고를 수 있는 도구(allowed)와 남은 요구(needs)는 서버가 정한다. LLM 은 그 안에서 고르고, 도구 조건은 서버가 첫 결과에서 상속
- 호출 실패 · 시간 초과 · 형식 오류 · 허용 밖 선택 · 도구 실패 → 재시도 없이 첫 결과 그대로 + 못 확인한 요구 안내
- LLM 이 answer 를 골라도 남은 요구(needs)는 서버가 확인한다 — 못 채웠으면 안내 한 줄 (끝낸 것처럼 답하지 않는다)
- 대화 저장소에 기록하지 않는다 (첫 조회 기록은 기존 경로가 이미 했다 — 추가 예산 조회가 비교 맥락을 덮지 않게)
"""
import json
import time

from . import agent_tools
from .agent_tools import TOOLS

TIMEOUT = 15  # 초 — 판단 LLM 호출 하나의 대기 한도
PROMPT = """너는 가계부 챗봇 '소비 분석'의 확인 담당이다. 서버가 첫 조회를 이미 했다. 그 결과로 답이 되면 answer, 모자라면 allowed 의 추가 확인 하나만 고른다.
입력: question, follow_up(앞 대화를 이어 묻는지), conditions(서버가 확정한 조회 조건), results(첫 조회 결과, 금액·날짜·가게 이름은 □),
needs(서버가 본 아직 답하지 못한 요구), allowed(고를 수 있는 도구).
- where_changed: 첫 비교를 by(category·merchant·time_slot·weekday)로 나눠 어디서 바뀌었는지 본다. args={"by":...}
- budget_lookup: 지금 예산 주기의 남은 예산. args=null
규칙: allowed 밖은 고르지 않는다. needs 가 있으면 그것을 채운다(by 는 needs 그대로). 기간·카테고리·필터는 서버가 첫 결과 그대로 쓴다.
금액을 계산하거나 원인을 단정하지 않는다. reason 은 숫자 없는 한 문장.
JSON: {"action":"tool"|"answer","tool":"where_changed"|"budget_lookup"|null,"args":{"by":"time_slot"}|null,"reason":"..."}"""

AXIS_NAMES = {"category": "카테고리", "merchant": "가게", "time_slot": "시간대", "weekday": "요일"}
UNMET = {"where_changed": "{name}별로 나눠 보는 건 이번에 확인하지 못했어요.",
         "budget_lookup": "남은 예산은 이번에 확인하지 못했어요. '예산 얼마 남았어?'로 따로 물어봐 주세요.",
         None: "질문의 나머지 부분은 이번에 확인하지 못했어요."}  # 서버가 요구를 특정하지 못한 채 판단이 실패한 경우
CONDITIONS = ("target", "period", "compare", "categories", "payment", "weekday", "time_slot", "include_fixed")  # 숫자(달 · 금액 · 날짜) 칸은 보내지 않는다


def unmet_line(need):
    tool = need and need["tool"]
    return UNMET[tool].format(name=AXIS_NAMES.get(need.get("by"), "기준")) if tool == "where_changed" else UNMET[tool]


def _conditions(request):
    out = {k: getattr(request, k) for k in CONDITIONS if getattr(request, k) not in (None, (), False)}
    if "categories" in out:
        out["categories"] = [c for c in out["categories"] if isinstance(c, str)]  # 코드만 (가게 이름 · 모르는 말은 빼고)
    return {k: list(v) if isinstance(v, tuple) else v for k, v in out.items() if v not in ((), [])}


def _choice(raw, allowed):
    """판단 LLM 응답 → (tool | None, args, reason) 또는 fallback 이유"""
    try:
        data = json.loads(raw)
        action, tool, args = data["action"], data.get("tool"), data.get("args")
    except Exception:
        return "invalid_json"
    reason = data.get("reason") if isinstance(data.get("reason"), str) else None
    if action == "answer":
        return None, None, reason
    if action != "tool":
        return "invalid_json"
    if tool not in allowed:
        return "not_allowed"
    if tool == "budget_lookup":
        return tool, {}, reason
    if not isinstance(args, dict):
        return "invalid_json"
    return tool, {k: v for k, v in args.items() if v is not None}, reason


def _reply(first, extra, unmet):
    """첫 결과 카드 · 문장 + (성공한) 추가 결과 카드 · 문장 + 못 확인한 요구 안내 — 모두 서버 문장"""
    reply = dict(first["server"]["reply"])
    if extra is not None:
        part = extra["server"]["reply"]
        reply["view"] = [*(reply.get("view") or []), *(part.get("view") or [])]
        for key in ("lines", "notices", "lead", "suggest"):
            mine = reply.get(key) or []
            reply[key] = [*mine, *[t for t in part.get(key) or [] if t not in mine]]
        reply = {k: v for k, v in reply.items() if v or k in ("lines", "notices")}
    if unmet:
        reply["notices"] = [*reply.get("notices", []), *[unmet_line(n) for n in unmet]]
    return reply


def run(question, first, *, request, needs, allowed, user, today, as_of, complete, follow_up=False, clock=None):
    """첫 관찰 결과 → {"status": answered | tool | fallback, "reply", "trace"}
    needs = 서버가 본 남은 요구 [{"tool", "by"?}], allowed = 이번에 고를 수 있는 도구 (비어 있으면 LLM 을 부르지 않는다)"""
    trace = {"allowed": list(allowed), "needs": needs, "choice": None, "tool_status": None, "fallback": None}

    def end(status, extra=None):
        done = [n for n in needs if extra is not None and n["tool"] == extra["tool"]]
        wanted = needs or ([None] if status == "fallback" else [])
        unmet = [n for n in wanted if n not in done]
        return {"status": status, "reply": _reply(first, extra, unmet), "trace": {**trace, "status": status, "unmet": unmet}}

    def fail(why):
        trace["fallback"] = why
        return end("fallback")

    if not allowed:
        return end("answered")
    payload = {"question": question, "follow_up": follow_up, "conditions": _conditions(request),
               "results": [agent_tools.for_llm(first)], "needs": needs, "allowed": list(allowed)}
    clock = clock or time.monotonic
    started = clock()
    try:
        raw = complete(PROMPT, json.dumps(payload, ensure_ascii=False), timeout=TIMEOUT)
    except Exception:  # 호출 오류 · 대기 한도 — 종류를 가리지 않고 재시도 없이
        return fail("call_failed")
    if clock() - started > TIMEOUT:
        return fail("timeout")
    choice = _choice(raw, allowed)
    if isinstance(choice, str):
        return fail(choice)
    tool, args, reason = choice
    trace["choice"] = {"tool": tool, "args": args, "reason": reason}
    if tool is None:
        return end("answered")
    need = next((n for n in needs if n["tool"] == tool), None)
    if need and need.get("by") not in (None, args.get("by")):  # 서버가 본 요구와 다른 기준 — 실행하지 않는다
        return fail("wrong_by")
    ctx = {"user": user, "today": today, "as_of": as_of, "question": question, "first": first, "request": request,
           "measure": (need or {}).get("measure")}  # 요일 합계 · 하루 평균 — 서버가 본 요구 그대로 (LLM 은 by 만)
    obs = TOOLS[tool](args, ctx)
    trace.update(tool_status=obs["status"], tool_reason=obs.get("reason"))
    if obs["status"] != "ok":
        if not needs:  # LLM 이 고른 확인이 실패 — 그 확인을 못 했다고 밝힌다
            needs = [{"tool": tool, **({"by": args["by"]} if args.get("by") else {})}]
            trace["needs"] = needs
        return fail("tool_" + obs["status"])
    return end("tool", obs)

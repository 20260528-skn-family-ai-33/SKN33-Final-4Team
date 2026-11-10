"""소비 분석 에이전트 루프 (MEMO 29, 11/10 1일차 — 아직 agent.py · chat/engine.py 에 연결하지 않았다)

LLM 이 도구 하나를 고른다 → 서버가 실행(agent_tools) → 관찰 결과(금액 · 날짜 · 가게 이름 없음)를 돌려준다 → 다음 도구 또는 끝.
- 안전장치: 도구 MAX_STEPS 번까지 · 같은 도구 + 같은 인자는 다시 실행하지 않음(duplicate) · 전체 TIMEOUT 초 ·
  LLM 오류 · 형식 오류 · 모르는 도구는 재시도 없이 fallback · 도구가 되물으면(ask) 바로 멈춤 · 성공 결과 없이 answer 면 fallback
- 답(reply)은 오늘은 서버가 성공한 결과의 카드 · 문장을 이어 붙인다 (_stack — 내일 LLM 조합으로 바꿀 자리)
- 대화 저장소(store)에 기록하지 않는다 (연결 때 정함). 기록용 항목은 results[*]["server"]["records"] 에 있다
"""
import json
import time

from . import agent_tools
from .agent_tools import TOOLS

MAX_STEPS = 3
TIMEOUT = 15  # 초 — 루프 전체 (LLM 호출 · 도구 실행 포함)
PROMPT = """너는 가계부 챗봇 '소비 분석'의 계획 담당이다. 질문에 답하려면 다음에 무엇을 할지 하나만 JSON 으로 고른다.
입력: question(사용자 질문), results(지금까지 실행한 도구의 관찰 결과). 금액·날짜·가게 이름은 서버만 알고 □로 가려져 있다.
도구:
- spend_lookup: 쓴 돈의 합계·지난달 비교·달끼리 비교·최근 몇 달 추이·건수·평균·묶어 보기. args = 소비 해석 칸(target, period, compare, categories 등)
- budget_lookup: 남은 예산. args = {categories, breakdown(카테고리별이면 true)}
- explain_change: 앞에서 본 비교 결과가 늘거나 준 이유를 물을 때. args = {ref(앞 결과 id), hypothesis(사용자가 짐작한 원인 후보, 없으면 null)}
- where_changed: 앞에서 본 비교를 시간대·요일·가게·카테고리로 나눠 어디서 바뀌었는지 볼 때. args = {ref, by}
규칙:
- 질문에 필요한 도구만 고른다. 이미 답할 수 있으면 answer 를 고른다. 같은 도구를 같은 인자로 다시 부르지 않는다.
- 금액을 계산하지 않고 원인을 단정하지 않는다. 숫자는 서버만 다룬다.
- explain_change·where_changed 의 ref 는 status=ok 인 비교 결과의 id 만 쓴다.
- status 가 invalid 면 인자를 고쳐 다시 고르거나, 답할 수 있으면 answer 를 고른다.
- reason 은 왜 이 행동을 골랐는지 한 문장 (숫자 없이).
JSON: {"action":"tool","tool":"spend_lookup","args":{...},"reason":"..."} 또는 {"action":"answer","tool":null,"args":null,"reason":"..."}"""


def _left(started, clock):
    return TIMEOUT - (clock() - started)


def _clean(args):
    """Structured Outputs 는 모든 칸을 채운다(null) — 말하지 않은 칸은 빼고 검사 · 중복 비교한다"""
    return {k: v for k, v in args.items() if v is not None}


def _choice(raw):
    """계획 LLM 응답 → (action, tool, args) 또는 fallback 이유"""
    try:
        data = json.loads(raw)
        action, tool, args = data["action"], data.get("tool"), data.get("args")
    except Exception:
        return "invalid_json"
    if action == "answer":
        return action, None, None
    if action != "tool":
        return "invalid_json"
    if tool not in TOOLS:
        return "unknown_tool"
    if not isinstance(args, dict):
        return "invalid_json"
    return action, tool, _clean(args)


def _stack(results):
    """성공한 결과의 카드 · 문장 · 안내를 순서대로 잇는다 (같은 문장은 한 번만). 내일 LLM 답 조합으로 바꿀 자리"""
    reply = {"lines": [], "notices": [], "lead": [], "view": []}
    for obs in results:
        if obs["status"] != "ok":
            continue
        part = obs["server"]["reply"]
        reply["view"] += part.get("view") or []
        for key in ("lines", "notices", "lead"):
            reply[key] += [t for t in part.get(key) or [] if t not in reply[key]]
    return {k: v for k, v in reply.items() if v or k in ("lines", "notices")}


def run(question, *, user, today, as_of, complete, clock=time.monotonic):
    """질문 하나 → {"status": answered | ask | fallback, "steps", "results", "reply", "decision", "fallback"}
    complete(system, user, timeout=초) — 계획 LLM (같은 함수를 explain_change 의 B 에도 넘긴다)"""
    started, steps, results, seen = clock(), [], [], set()
    ctx = {"user": user, "today": today, "as_of": as_of, "complete": complete, "question": question, "results": results}

    def end(status, reply=None, decision=None, fallback=None):
        return {"status": status, "steps": steps, "results": results, "reply": reply, "decision": decision, "fallback": fallback}

    while len(steps) < MAX_STEPS:  # 상한이면 LLM 을 더 부르지 않고 지금까지로 답한다
        left = _left(started, clock)
        if left <= 0:
            return end("fallback", fallback="timeout")
        payload = {"question": question, "results": [agent_tools.for_llm(o) for o in results]}
        try:
            raw = complete(PROMPT, json.dumps(payload, ensure_ascii=False), timeout=left)
        except Exception:  # 호출 오류 · 대기 한도 — 종류를 가리지 않고 재시도 없이
            return end("fallback", fallback="call_failed")
        if _left(started, clock) <= 0:
            return end("fallback", fallback="timeout")
        choice = _choice(raw)
        if isinstance(choice, str):
            return end("fallback", fallback=choice)
        action, tool, args = choice
        if action == "answer":
            break
        key = (tool, json.dumps(args, sort_keys=True, ensure_ascii=False))
        if key in seen:
            obs = {"tool": tool, "status": "duplicate"}
        else:
            seen.add(key)
            obs = TOOLS[tool](args, ctx)
        rid = f"R{len(results) + 1}"
        if "facts" in obs:  # 사실 id = 결과 id + 순번 (B 주장의 C1 … 도 이 모양으로)
            obs["facts"] = [{"id": f"{rid}.F{n}", **{k: v for k, v in f.items() if k != "id"}} for n, f in enumerate(obs["facts"], 1)]
        obs = {"id": rid, **obs}
        results.append(obs)
        steps.append({"tool": tool, "args": args, "status": obs["status"], **({"reason": obs["reason"]} if "reason" in obs else {})})
        if obs["status"] == "ask":
            return end("ask", decision=obs["server"]["decision"])
    if _left(started, clock) <= 0:
        return end("fallback", fallback="timeout")
    if not any(o["status"] == "ok" for o in results):
        return end("fallback", fallback="no_result")
    return end("answered", reply=_stack(results))

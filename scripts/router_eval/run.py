"""라우터 LLM 해석 평가 — 독립 실행 (chat API · DB · 실제 에이전트 연결 없음)

    cd <저장소>                          # 기본 = dry-run: 네트워크 호출 없음
    .venv/bin/python scripts/router_eval/run.py --split dev --scenarios
    .venv/bin/python scripts/router_eval/run.py --split new --scenarios --replay <이전 results.jsonl>

단계 B (역할별 해석 분리) 뒤:
- 사례의 기대 추출값 · 저장 응답은 분리 전 통합 형식이다. dry-run · replay 는 그 원문을 역할별(라우터 · 소비) 형식으로
  바꿔 새 구조(담당 우선 · not_mine 재분류)에 넣는다 — '역할별 모델이 같은 뜻으로 답했다'는 가정의 변환이다
- 추출 채점은 통합 형식 원문 그대로(기존 평가와 비교 가능), 결정 · 상태 채점은 새 구조. 턴 시작 때 예산 작업이 진행 중이면
  원문이 예산 에이전트로 가서 결과가 그 판단에 달린다 → comparable=False 로 따로 센다
- live(실제 호출)는 역할별 기대값 · 스키마 · 장부 계획(MEMO 13번) 확정 전이라 막아 둔다. 호출 어댑터(openai_rest) · 장부는 그대로 둔다

- 사례: cases.json (가상 문장 · 상태). 단일 입력(split dev · holdout)과 연속 대화(scenarios) 점수는 따로 낸다
- 라우터 · 에이전트가 주입받는 complete 로만 모델을 부른다 → 코드가 처리하는 입력은 호출 0회, 역할마다 한 번
- dry-run: 모델 대신 사례의 정답 추출값(gold)을 돌려준다 → 사례 · 정책 · 코드가 서로 맞는지 오프라인 점검
- 결과: --output-dir (저장소 밖 경로만). 키 · 헤더 · 실제 대화는 기록하지 않는다
- replay: --replay <이전 results.jsonl> — 저장된 모델 응답을 그대로 다시 넣어 API 호출 없이 (바뀐 기준으로) 다시 채점.
  --cases-file 로 다른 버전의 사례 파일을 쓸 수 있다 (옛 기준 점수 재현용)
"""
import argparse
import copy
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()

from functools import partial  # noqa: E402

from chat.interpretation import llm  # noqa: E402  (분리 전 통합 지시문 — 저장 응답 · 고정 해시 비교용)
from chat.interpretation.legacy import to_router, to_spending, view  # noqa: E402
from chat.interpretation.schema import CATEGORY_NAMES, Interpretation  # noqa: E402
from chat.router_core import (MemoryStateStore, current_question, pop_queued_start,  # noqa: E402
                              record_budget_task)
from chat.router_core import reading as router_reading  # noqa: E402
from chat.router_core import run_turn as router_run_turn  # noqa: E402
from chat.router_core.rules import ROUTER_QUESTIONS  # noqa: E402
from spending_analysis import agent as spending_agent  # noqa: E402
from spending_analysis import dialog  # noqa: E402
from spending_analysis import reading as spending_reading  # noqa: E402
from spending_analysis.dialog import record_analysis, run_items  # noqa: E402
from openai_rest import CapReached, ResponseError, ResponsesClient  # noqa: E402

CASES = Path(__file__).resolve().parent / "cases.json"
# 추출 채점 대상 칸. unclear(자유 설명)는 채점하지 않는다
FIELDS = [f for f in Interpretation.__dataclass_fields__ if f != "unclear"]
CHECK_AT = "2026-10-29T12:00:00+09:00"
ROLE_OF = {router_reading.PROMPT: "router", spending_reading.PROMPT: "spending"}
LIVE_PENDING = ("단계 B: 역할별 프롬프트의 실제 LLM 평가는 아직 실행하지 않는다 — 역할별 기대값 형식 · 구조화 출력 스키마 · "
                "장부 상한은 backend/chat/router_core/MEMO.md 13번 계획을 확정한 뒤 연결")


# ---------- 사례 · 상태 ----------

def load(path=CASES):
    raw = path.read_bytes()
    data = json.loads(raw)
    fixtures = data["fixtures"]

    def resolve(value):
        if isinstance(value, str) and value.startswith("@"):
            return copy.deepcopy(fixtures[value[1:]])
        if isinstance(value, dict):
            return {k: resolve(v) for k, v in value.items()}
        return value

    data["cases"] = [resolve(c) for c in data["cases"]]
    data["scenarios"] = [resolve(s) for s in data["scenarios"]]
    data["sha256"] = hashlib.sha256(raw).hexdigest()
    return data


class FakeServer:
    """읽기 전용 서버 어댑터 대역 (가짜 작업 상태)"""

    def __init__(self, states=None):
        self.states = dict(states or {})

    def task_status(self, task_ref):
        return {"task_ref": task_ref, "checked_at": CHECK_AT, **self.states[task_ref]}


def build_store(state, conversation):
    store = MemoryStateStore()
    if state.get("pending"):
        # 단계 A: 라우터 질문과 소비 되묻기는 저장 칸이 다르다 (사례 fixture 는 분리 전 한 칸 형식)
        # 보관한 요청도 분리 전 통합 형식 → 질문한 쪽의 형식으로 바꾼다 (응답 변환과 같은 규칙)
        pending = dict(state["pending"])
        if pending["asking"] in ROUTER_QUESTIONS:
            pending["request"] = pending["request"] and to_router(pending["request"])
            store.set(conversation, pending)
        else:
            pending["request"] = to_spending(pending["request"], {"chosen": True})
            store.save_agent_state(conversation, "spending", {"pending": pending})
            store.set_active(conversation, "spending")
    if state.get("last_analysis"):
        # 사례 fixture 는 22차(evidence 도입) 전 형식: 그때 ok=True 는 '확정 분석'이었다 → evidence 없으면 confirmed 로 읽는다
        analysis = copy.deepcopy(state["last_analysis"])
        for item in analysis["items"]:
            if item.get("ok"):
                item.setdefault("evidence", "confirmed")
        store.save_analysis(conversation, analysis)
    if state.get("budget_task"):
        store.save_task(conversation, state["budget_task"])
    if state.get("budget_result"):
        store.save_budget_result(conversation, state["budget_result"])
    dialog.publish(store, conversation)  # 소비 에이전트가 공개하는 요약 (라우터는 이것만 읽는다)
    return store


def snapshot(store, conversation):
    # pending = 사용자가 지금 답할 질문 (라우터 질문 또는 담당 소비 되묻기) — 분리 전 한 칸과 같은 뜻으로 비교
    return {"pending": current_question(store, conversation), "last_analysis": store.get_analysis(conversation),
            "budget_task": store.get_task(conversation), "budget_result": store.get_budget_result(conversation)}


def gold(expect_llm):
    """사례의 정답 추출값 → 모델이 냈어야 할 JSON (명시 칸 + 허용값의 첫 값)"""
    data = dict(expect_llm.get("must", {}))
    for key, values in expect_llm.get("allow", {}).items():
        data.setdefault(key, values[0])
    data.setdefault("intents", [])
    return json.dumps(data, ensure_ascii=False)


# ---------- 채점 ----------

def plain(value):
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    return value


def empty(value):
    return value is None or value is False or value == [] or value == {}


def same_categories(got, want):
    """모르는 말은 표기 차이를 허용 (공백 무시, 한쪽이 다른 쪽을 포함)"""
    if not isinstance(got, list) or len(got) != len(want):
        return False
    for g, w in zip(got, want):
        if isinstance(w, dict):
            a, b = (str(g.get("unknown", "")) if isinstance(g, dict) else "").replace(" ", ""), w["unknown"].replace(" ", "")
            if not a or not (a in b or b in a):
                return False
        elif g != w:
            return False
    return True


def same(field, got, want):
    if field == "intents":
        return isinstance(got, list) and sorted(got) == sorted(want)
    if field == "categories":
        return same_categories(got if got is not None else [], want)
    return plain(got) == plain(want)


def score_llm(raw, expect):
    """→ {"format_ok", "ok", "fields": {칸: {...}}, "observed": {...}}. 명시 · 허용 외의 칸은 비어 있어야 한다"""
    try:
        data = json.loads(raw)
        llm.parse(data)
    except Exception:
        return {"format_ok": False, "ok": False, "fields": {}, "observed": {}}
    must, allow, observe = expect.get("must", {}), expect.get("allow", {}), set(expect.get("observe", []))
    fields, observed = {}, {}
    for field in FIELDS:
        got = data.get(field)
        if field in observe:
            observed[field] = got  # 예산 조정 보조값: 관찰만
            continue
        if field in must:
            ok, want = same(field, got, must[field]), must[field]
        elif field in allow:
            ok, want = any(same(field, got, v) or (v is None and empty(got)) for v in allow[field]), {"one_of": allow[field]}
        else:
            ok, want = empty(got), "empty"  # 말하지 않은 조건을 채우거나 이전 조건을 복사하면 여기서 걸린다
        fields[field] = {"ok": ok, "got": plain(got), "want": want}
    return {"format_ok": True, "ok": all(f["ok"] for f in fields.values()), "fields": fields, "observed": observed}


def get(obj, path):
    """점 경로 조회. 목록이면 다음 칸을 원소마다 꺼낸다 (explain.items.category)"""
    for key in path.split("."):
        if isinstance(obj, (list, tuple)):
            obj = [get(o, key) for o in obj]
        elif isinstance(obj, dict):
            obj = obj.get(key)
        else:
            obj = getattr(obj, key, None)
    return plain(obj)


def score_paths(obj, expect):
    checks = {path: {"ok": get(obj, path) == plain(want), "got": get(obj, path), "want": want}
              for path, want in expect.items()}
    return all(c["ok"] for c in checks.values()), checks


# ---------- 실행 ----------

class Recorder:
    """라우터 · 소비 에이전트에 주입하는 complete. 부른 역할(지시문으로 구분)과 응답 원문을 남긴다"""

    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def __call__(self, system, user):
        role = ROLE_OF.get(system, "other")
        try:
            text = self.inner(system, user)
        except CapReached:
            self.calls.append({"cap": True, "role": role})
            raise
        except ResponseError as error:
            self.calls.append({"error": error.kind, "role": role})  # 인증 · incomplete · refusal 등 (의미 해석 오류와 구분)
            raise
        except Exception as error:
            self.calls.append({"error": type(error).__name__, "role": role})
            raise
        self.calls.append({"text": text, "role": role})
        return text


def role_complete(source):
    """[단계 B 변환 replay] 이 턴의 통합 형식 원문 하나 → 부른 역할(라우터 · 소비) 형식으로 바꿔 답하는 complete.
    '역할별 모델이 같은 뜻으로 답했다'고 가정한 변환이다 (역할별 프롬프트의 실제 성능이 아니다)"""
    def complete(system, user):
        if source is None:
            raise LookupError("이 턴의 저장 응답 없음")  # 분리 전에는 호출하지 않던 입력에 새 구조가 호출했다
        try:
            data = json.loads(source)
        except ValueError:
            return source  # JSON 이 아닌 원문은 그대로 (형식 오류로 채점)
        out = to_router(data) if system == router_reading.PROMPT else to_spending(data, json.loads(user))
        return json.dumps(out, ensure_ascii=False)
    return complete


def budget_active(store, conversation):
    task = store.get_task(conversation)
    return bool(task and not task["suspended"])


def turn_once(text, store, conversation, server, complete):
    """새 구조 한 턴: 라우터 → 담당(소비는 실제 소비 에이전트, 분석 실행만 생략 / 예산은 연결 없음 — 전달까지만)"""
    agents = {"spending": partial(spending_agent.handle, store=store, complete=complete, execute=lambda decision: None)}
    return view(router_run_turn(conversation, store, complete, text=text, agents=agents, server=server))


def run_turn(turn, store, conversation, server, source_for):
    """한 턴 실행 → (결과 dict, 결정). source_for(turn) = 이 턴의 통합 형식 원문 (replay 저장 응답 · dry-run 정답)
    - 추출 채점은 그 원문(분리 전 형식)으로 한다 → 기존 평가와 비교 가능
    - 결정 · 상태 채점은 새 구조로 한다. 턴 시작 때 예산 작업이 진행 중이면 원문이 예산 에이전트로 그대로 가므로
      결과가 예산 에이전트 판단에 달린다 → comparable=False (구조 변경으로 달라진 항목)"""
    before = copy.deepcopy(store)
    comparable = not budget_active(store, conversation)
    expect_llm, source = turn.get("expect_llm"), source_for(turn)
    recorder = Recorder(role_complete(source))
    decision = turn_once(turn["text"], store, conversation, server, recorder)
    result = {"text": turn["text"], "calls": len([c for c in recorder.calls if "cap" not in c]),
              "roles": [c["role"] for c in recorder.calls], "raw": [source] if expect_llm and source is not None else [],
              "comparable": comparable}
    if any("cap" in c for c in recorder.calls):
        result["kind"] = "cap_skipped"
        return result, decision
    if any("error" in c for c in recorder.calls):
        result["kind"] = "call_failed"
        result["call_error"] = next(c["error"] for c in recorder.calls if "error" in c)
        return result, decision
    if (expect_llm is None) != (result["calls"] == 0):
        result["kind"] = "unexpected_calls"  # 코드로 바로 처리해야 할 입력에 호출했거나 그 반대
    if expect_llm is not None and source is not None:
        result["llm"] = score_llm(source, expect_llm)
    result["route_ok"], result["route"] = score_paths(decision, turn.get("expect_route", {}))
    result["state_ok"], result["state"] = score_paths(snapshot(store, conversation), turn.get("expect_state", {}))
    llm_ok = expect_llm is None or result.get("llm", {}).get("ok", False)
    if "kind" not in result:
        if expect_llm is not None and not result["llm"]["format_ok"]:
            result["kind"] = "format"
        elif not llm_ok:
            result["kind"] = "llm"
        elif not (result["route_ok"] and result["state_ok"]):
            result["kind"] = "route"
            result["diagnosis"] = diagnose(turn, before, conversation, server)
        else:
            result["kind"] = "ok"
    return result, decision


def diagnose(turn, before, conversation, server):
    """추출은 맞았는데 결정이 틀림 → 같은 이전 상태에서 정답 추출값으로 다시 돌려 본다. 원인은 확정하지 않는다"""
    source = gold(turn["expect_llm"]) if turn.get("expect_llm") else None
    decision = turn_once(turn["text"], before, conversation, server, role_complete(source))
    route_ok, _ = score_paths(decision, turn.get("expect_route", {}))
    state_ok, _ = score_paths(snapshot(before, conversation), turn.get("expect_state", {}))
    return {"gold_replay_ok": route_ok and state_ok,
            "candidates": (["채점하지 않는 칸(unclear 등)의 영향", "평가 도구"] if route_ok and state_ok
                           else ["코드 규칙", "기대값", "상태 구성", "평가 도구", "구조 변경(예산 담당 우선 · 역할 분리)"])}


def after_turn(turn, decision, store, conversation, server):
    """연속 대화: 실제 처리 결정에 가짜 소비 조회 · 에이전트 · 서버 결과를 이어 붙인다 (정답 상태로 초기화하지 않음)"""
    after = turn.get("after") or {}
    if after.get("analysis"):
        record_analysis(store, conversation, decision, run_items(
            decision.items, lambda item: {"evidence": "confirmed", "period": {"from": "2026-10-01", "to": "2026-10-29"},
                                          "as_of": CHECK_AT}))  # 가짜 조회 = 확정 분석 성공
        dialog.publish(store, conversation)  # 소비 에이전트가 실행 뒤 하는 요약 공개
    if "agent" in after:
        server.states.update(after.get("server", {}))
        record_budget_task(store, conversation, after["agent"], server)
        pop_queued_start(store, conversation)


def run_case(case, source_for):
    conversation = "eval"
    store = build_store(case.get("state", {}), conversation)
    return {"id": case["id"], "split": case["split"], "policy_pending": case.get("policy_pending", False),
            **run_turn(case, store, conversation, FakeServer(case.get("server")), source_for)[0]}


def run_scenario(scenario, source_for):
    conversation, store, server, turns = "eval", MemoryStateStore(), FakeServer(scenario.get("server")), []
    first_error = None
    for index, turn in enumerate(scenario["turns"]):
        server.states.update((turn.get("before") or {}).get("server", {}))  # 이 턴 전에 바뀐 가짜 서버 상태
        result, decision = run_turn(turn, store, conversation, server, source_for)
        if result["kind"] != "ok":
            # 첫 오류 뒤의 실패는 앞 오류가 만든 상태 때문일 수 있다 → 따로 표시 (원인 확정 아님)
            result["after_first_error"] = first_error is not None
            first_error = first_error or index + 1
        turns.append({"turn": index + 1, **result})
        if result.get("kind") in ("cap_skipped", "call_failed", "aborted"):
            break  # 호출이 안 됐으면 뒤 상태가 의미 없다
        after_turn(turn, decision, store, conversation, server)
    return {"id": scenario["id"], "turns": turns, "first_error_turn": first_error,
            "ok": len(turns) == len(scenario["turns"]) and all(t["kind"] == "ok" for t in turns)}


def summarize_scenarios(results):
    turns = [t for s in results for t in s["turns"]]
    planned = sum(1 for _ in turns)
    changed = [f"{s['id']}#{t['turn']}" for s in results for t in s["turns"] if not t.get("comparable", True)]
    return {"count": len(results), "ok": sum(s["ok"] for s in results),
            "structure_changed_turns": changed,  # 예산 작업 진행 중 턴: 결과가 예산 에이전트 판단에 달림
            "comparable_turns_ok": sum(t["kind"] == "ok" for t in turns if t.get("comparable", True)),
            "comparable_turns": sum(1 for t in turns if t.get("comparable", True)),
            "turns_run": planned, "turns_ok": sum(t["kind"] == "ok" for t in turns),
            "first_errors": sum(1 for t in turns if t["kind"] != "ok" and not t.get("after_first_error")),
            "after_first_error": sum(1 for t in turns if t["kind"] != "ok" and t.get("after_first_error")),
            "kinds": {k: sum(t["kind"] == k for t in turns) for k in sorted({t["kind"] for t in turns})}}


# ---------- 요약 · 저장 ----------

def summarize(results):
    # 정확도 분모: 정책 미정 · 보내지 않음 · 호출 실패(연결 문제, 의미 해석 아님)는 뺀다 — 개수는 따로 센다
    scored = [r for r in results if not r["policy_pending"] and r["kind"] not in ("cap_skipped", "aborted", "call_failed")]
    with_llm = [r for r in scored if "llm" in r]
    rate = lambda n, d: None if d == 0 else round(n / d, 3)  # noqa: E731
    return {
        "cases": len(results), "policy_pending_excluded": sum(r["policy_pending"] for r in results),
        "cap_skipped": sum(r["kind"] == "cap_skipped" for r in results),
        "aborted": sum(r["kind"] == "aborted" for r in results),
        "call_failed": sum(r["kind"] == "call_failed" for r in results),
        "format_ok_rate": rate(sum(r["llm"]["format_ok"] for r in with_llm), len(with_llm)),
        "extraction_ok_rate": rate(sum(r["llm"]["ok"] for r in with_llm), len(with_llm)),
        "route_ok_rate": rate(sum(r.get("route_ok", False) for r in scored), len(scored)),
        "state_ok_rate": rate(sum(r.get("state_ok", False) for r in scored), len(scored)),
        "kinds": {k: sum(r["kind"] == k for r in results) for k in sorted({r["kind"] for r in results})},
        "structure_changed": [r["id"] for r in results if not r.get("comparable", True)],
        "call_errors": {k: sum(r.get("call_error") == k for r in results)
                        for k in sorted({r["call_error"] for r in results if r.get("call_error")})},
    }


def usage_totals(attempts, price_in, price_out):
    known = [a["usage"] for a in attempts if a.get("usage")]
    tokens_in = sum(u.get("input_tokens", 0) for u in known)
    tokens_out = sum(u.get("output_tokens", 0) for u in known)
    unknown = len(attempts) - len(known)
    cost = None
    if price_in is not None and price_out is not None:
        cost = round(tokens_in / 1e6 * price_in + tokens_out / 1e6 * price_out, 6)
    latencies = sorted(a["latency_ms"] for a in attempts if a.get("latency_ms") is not None)
    return {"attempts": len(attempts), "input_tokens": tokens_in, "output_tokens": tokens_out,
            "unknown_usage_attempts": unknown,  # 사용량을 모르는 실패는 0 원으로 보지 않는다
            "cost_usd_known_part": cost, "cost_complete": unknown == 0 and cost is not None,
            "latency_ms_median": latencies[len(latencies) // 2] if latencies else None,
            "latency_ms_max": latencies[-1] if latencies else None}


class Ledger:
    """저장소 밖 JSON {"limit", "used"}. 처음 만들 때의 상한을 고정, 시도마다 바로 늘려 쓴다 (재실행해도 초기화 안 됨)"""

    def __init__(self, path, limit):
        self.path = Path(path).expanduser().resolve()
        if self.path == ROOT or ROOT in self.path.parents:
            raise SystemExit("--ledger 는 저장소 밖 경로여야 해요")
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.data = {"limit": limit, "used": 0}
            self.save()

    @property
    def remaining(self):
        return max(self.data["limit"] - self.data["used"], 0)

    def claim(self, ids):
        """이 장부로 보낼 단위를 실행 전에 기록. 이미 보낸 단위가 섞이면 아무것도 보내지 않고 멈춘다 (점검 단위 재실행 방지)"""
        done = set(self.data.get("units", []))  # 이 칸이 생기기 전 장부는 빈 목록으로 본다
        again = [i for i in ids if i in done]
        if again:
            raise SystemExit(f"이 장부로 이미 실행한 단위예요: {', '.join(again)}")
        self.data["units"] = [*self.data.get("units", []), *ids]
        self.save()

    def spend(self):
        self.data["used"] += 1
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.data), encoding="utf-8")


def output_dir(value):
    path = Path(value).expanduser().resolve()
    if path == ROOT or ROOT in path.parents:
        raise SystemExit("--output-dir 는 저장소 밖 경로여야 해요")
    path.mkdir(parents=True, exist_ok=True)
    return path


def schema():
    """Structured Outputs 용 JSON Schema (strict: 모든 칸 필수 · null 허용). 받은 뒤 기존 parse 검사는 그대로 한다"""
    nullable = lambda t: {"type": [t, "null"]}  # noqa: E731
    enum = lambda values: {"type": ["string", "null"], "enum": [*values, None]}  # noqa: E731
    from chat.interpretation import schema as s
    props = {
        "intents": {"type": "array", "items": {"type": "string", "enum": list(s.INTENTS)}},
        "target": enum(s.TARGETS), "period": enum(s.PERIODS), "compare": enum(s.COMPARES),
        "months": nullable("integer"),
        "categories": {"type": "array", "items": {"anyOf": [
            {"type": "string", "enum": list(CATEGORY_NAMES)},
            {"type": "object", "properties": {"unknown": {"type": "string"}}, "required": ["unknown"],
             "additionalProperties": False}]}},
        "direction": enum(s.DIRECTIONS), "amount": nullable("integer"), "amount_mode": enum(s.AMOUNT_MODES),
        "followup": enum(s.FOLLOWUPS), "choice": enum(s.CHOICES), "confirm": nullable("boolean"),
        "unclear": {"type": "array", "items": {"type": "string"}},
        "refers_to": enum(s.REFERS), "explain": nullable("boolean"), "resume": nullable("boolean"),
        "after_budget_change": nullable("boolean"), "all_categories": nullable("boolean"),
    }
    assert set(props) == set(Interpretation.__dataclass_fields__), "schema 와 Interpretation 칸이 다름"
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description="라우터 LLM 해석 평가 (기본 dry-run)")
    parser.add_argument("--split", choices=["dev", "holdout", "new", "all"], default="dev",
                        help="holdout(참고 · 회귀용) · new(신규 검증)는 명시해서 실행. --scenarios 도 같은 split 만")
    parser.add_argument("--prompt-sha", help="고정한 지시문 해시. 다르면 실행하지 않는다")
    parser.add_argument("--scenarios", action="store_true", help="연속 대화 시나리오도 실행")
    parser.add_argument("--live", action="store_true", help="실제 생성 요청 (ROUTER_EVAL_LIVE=1 필요)")
    parser.add_argument("--max-calls", type=int, help="live 생성 요청 시도 상한 (실패 포함). 장부가 이미 있으면 장부 상한이 우선")
    parser.add_argument("--ledger", help="live 호출 장부 JSON (저장소 밖). 재실행해도 한도가 초기화되지 않는다")
    parser.add_argument("--cases", help="실행할 사례 ID 쉼표 목록 (예: d01,d08)")
    parser.add_argument("--reasoning-effort", default="low", choices=["none", "low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--model", default=None, help="기본: 환경변수 LLM_MODEL")
    parser.add_argument("--structured", action="store_true", help="Structured Outputs(json_schema) 사용")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--max-output-tokens", type=int, default=2000, help="추론 토큰 포함")
    parser.add_argument("--price-in", type=float, help="입력 100만 토큰당 USD (공식 가격표 값을 직접 입력)")
    parser.add_argument("--price-out", type=float, help="출력 100만 토큰당 USD")
    parser.add_argument("--output-dir", help="결과 저장 (저장소 밖 경로)")
    parser.add_argument("--replay", help="이전 results.jsonl 의 모델 응답으로 다시 채점 (API 호출 없음)")
    parser.add_argument("--cases-file", help="사례 파일 경로 (기본: scripts/router_eval/cases.json)")
    args = parser.parse_args(argv)

    data = load(Path(args.cases_file) if args.cases_file else CASES)
    cases = [c for c in data["cases"] if args.split == "all" or c["split"] == args.split]
    if args.cases:
        wanted = args.cases.split(",")
        cases = [c for c in cases if c["id"] in wanted]
        if len(cases) != len(wanted):
            raise SystemExit("--cases 중 이 split 에 없는 ID 가 있어요 (holdout 은 --split holdout 을 명시)")
    scenarios = [s for s in data["scenarios"] if args.split == "all" or s["split"] == args.split] if args.scenarios else []
    prompt_sha = hashlib.sha256(llm.PROMPT.encode("utf-8")).hexdigest()
    if args.prompt_sha and not prompt_sha.startswith(args.prompt_sha):
        raise SystemExit(f"지시문 해시가 고정값과 달라요: {prompt_sha[:8]} ≠ {args.prompt_sha}")
    planned = sum(c.get("expect_llm") is not None for c in cases) + \
        sum(t.get("expect_llm") is not None for s in scenarios for t in s["turns"])

    if args.live:
        raise SystemExit(LIVE_PENDING)
    if args.replay:
        saved = {}
        for line in Path(args.replay).read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if "id" in row and row.get("raw"):
                saved[row["id"]] = row["raw"][0]
            for t in (row.get("scenario") or {}).get("turns", []):  # 연속 대화: "시나리오#턴" 으로 찾는다
                if t.get("raw"):
                    saved[f"{row['scenario']['id']}#{t['turn']}"] = t["raw"][0]
        for s in scenarios:
            s["turns"] = [{**t, "replay_key": f"{s['id']}#{i}"} for i, t in enumerate(s["turns"], 1)]
        missing = [c["id"] for c in cases if c.get("expect_llm") and c["id"] not in saved] + \
            [t["replay_key"] for s in scenarios for t in s["turns"] if t.get("expect_llm") and t["replay_key"] not in saved]
        if missing:
            raise SystemExit(f"replay 할 저장 응답이 없는 사례: {missing}")
        # 저장 응답이 없는 턴: 새 구조가 그 턴에 호출하면 call_failed(LookupError)로 드러난다
        source_for = lambda turn: saved.get(turn.get("replay_key", turn.get("id")))  # noqa: E731
    else:
        # dry-run: 네트워크 없이 정답 추출값(통합 형식)을 역할별로 바꿔 돌려준다 (사례 · 코드 정합성 점검)
        source_for = lambda turn: gold(turn["expect_llm"]) if turn.get("expect_llm") else None  # noqa: E731

    case_results = [run_case(c, source_for) for c in cases]
    scenario_results = [run_scenario(s, source_for) for s in scenarios]
    roles = [r for x in case_results + [t for s in scenario_results for t in s["turns"]] for r in x.get("roles", [])]
    meta = {
        "mode": "replay" if args.replay else "dry-run",
        "replay_of": Path(args.replay).name if args.replay else None, "time": datetime.now(timezone.utc).isoformat(),
        "translated": "통합 형식 원문 → 역할별 형식 변환 (실제 역할별 프롬프트 응답 아님)",
        "prompt_sha256": prompt_sha,  # 분리 전 통합 지시문 (저장 응답의 지시문)
        "role_prompt_sha256": {"router": hashlib.sha256(router_reading.PROMPT.encode("utf-8")).hexdigest(),
                               "spending": hashlib.sha256(spending_reading.PROMPT.encode("utf-8")).hexdigest()},
        "role_calls": {r: roles.count(r) for r in sorted(set(roles))},
        "cases_version": data["version"], "cases_sha256": data["sha256"], "split": args.split,
        "planned_model_calls": planned,  # 분리 전 기준(입력당 1회) 모델 턴 수
        "rough_input_chars_per_call": {"router": len(router_reading.PROMPT) + 400, "spending": len(spending_reading.PROMPT) + 400},
        "single": summarize(case_results),
        "scenarios": summarize_scenarios(scenario_results),
    }
    if args.output_dir:
        out = output_dir(args.output_dir)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (out / f"{stamp}-meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        with open(out / f"{stamp}-results.jsonl", "w", encoding="utf-8") as f:
            for r in case_results + [{"scenario": s} for s in scenario_results]:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    for r in case_results:
        if r["kind"] != "ok":
            print(f"[{r['kind']}] {r['id']} {r['text']}")
    for s in scenario_results:
        for t in s["turns"]:
            if t["kind"] != "ok":
                print(f"[{t['kind']}] {s['id']} 턴{t['turn']} {t['text']}")
    return meta, case_results, scenario_results


if __name__ == "__main__":
    main()

"""단계 B 역할별 평가 — 라우터 · 소비 해석을 역할별 기대값으로 따로 채점 (기본 dry-run, 네트워크 없음)

    cd <저장소>
    .venv/bin/python scripts/router_eval/stage_b.py --split all --scenarios                 # dry-run: 예상 호출 수 확인
    .venv/bin/python scripts/router_eval/stage_b.py --split all --scenarios --replay <results.jsonl>
    ROUTER_EVAL_LIVE=1 .venv/bin/python scripts/router_eval/stage_b.py --live --structured \\
        --max-calls 180 --ledger <저장소 밖>/ledger.json --output-dir <저장소 밖> ...      # 실제 호출 (기본 꺼짐)

- 사례: cases_b.json (format=role). expect_router · expect_spending = 그 역할이 이 입력에서 불려야 할 때의 기대 추출값.
  없으면 그 역할은 불리지 않아야 한다. 기존 cases.json · 저장 결과는 그대로 두고 회귀용으로 변환했다 (make_cases_b.py)
- 채점은 셋을 따로: 역할별 해석(라우터 · 소비 각각 형식 · 추출) / 최종 결정(분리 전 Decision 칸, legacy.view) / 상태 보존
- 연속 대화는 이전 턴의 실제 결과 상태를 그대로 다음 턴에 넘긴다 (정답 상태로 바꾸지 않음). 턴 사이의 분석 실행 ·
  예산 에이전트 결과는 사례의 after(대역)로 이어 붙인다
- 예산 · 정책 에이전트는 사례의 doubles 에 적힌 결과만 돌려주는 대역 (없으면 전달까지만). 대역 결과가 결정에 쓰인 단위는
  double_dependent 로 따로 센다
- live: --live + --max-calls + --ledger + ROUTER_EVAL_LIVE=1 이 모두 있어야 한다. 시도마다 장부를 먼저 늘리고(실패 포함),
  재시도 · 다른 모델 · 설정 전환 없음. 인증 · 권한 · 모델 · 요청 · 한도 오류면 남은 호출을 보내지 않는다
- 정책 미정(policy_pending) 단위는 분모에서 빼고 따로 보고한다. 같은 작성자가 쓴 사례는 독립 평가가 아니다
"""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

import run  # django.setup · 채점 도구 · 장부 · 서버 대역
from chat.interpretation.legacy import view
from chat.router_core import reading as router_reading
from chat.router_core import run_turn as router_run_turn
from openai_rest import ResponsesClient
from spending_analysis import agent as spending_agent
from spending_analysis import explain as spending_explain
from spending_analysis import reading as spending_reading

CASES_B = Path(__file__).resolve().parent / "cases_b.json"
ROLES = {"router": (router_reading.PROMPT, router_reading.RouterReading, router_reading.parse),
         "spending": (spending_reading.PROMPT, spending_reading.SpendingReading, spending_reading.parse)}
EXPECT = {"router": "expect_router", "spending": "expect_spending"}
ROLE_OF = {prompt: role for role, (prompt, _, _) in ROLES.items()}


# ---------- 사례 ----------

def load(path=CASES_B):
    data = run.load(path)
    if data.get("format") != "role":
        raise SystemExit("역할별 사례 파일(format=role)이 아니에요 — 분리 전 사례는 run.py 로")
    return data


def units(data, split, scenarios):
    cases = [c for c in data["cases"] if split == "all" or c["split"] == split]
    flows = [s for s in data["scenarios"] if split == "all" or s["split"] == split] if scenarios else []
    return cases, flows


def planned_calls(cases, flows):
    """사례 파일의 기대 역할 수 (= dry-run · live 에서 나가야 할 호출 수)"""
    turns = cases + [t for s in flows for t in s["turns"]]
    return {role: sum(1 for t in turns if t.get(key)) for role, key in EXPECT.items()}


# ---------- 역할별 스키마 · 정답 ----------

def role_schema(role):
    """Structured Outputs 스키마 (strict: 모든 칸 필수 · null 허용). 받은 뒤 역할별 parse 검사는 그대로 한다"""
    nullable = lambda t: {"type": [t, "null"]}  # noqa: E731
    enum = lambda values: {"type": ["string", "null"], "enum": [*values, None]}  # noqa: E731
    strings = {"type": "array", "items": {"type": "string"}}
    categories = {"type": "array", "items": {"anyOf": [
        {"type": "string", "enum": list(run.CATEGORY_NAMES)},
        {"type": "object", "properties": {"unknown": {"type": "string"}}, "required": ["unknown"],
         "additionalProperties": False}]}}
    # 소비 해석만: 세부 지출 {detail, category | null} (서버가 붙이는 accepted 는 넣지 않는다)
    spending_categories = {"type": "array", "items": {"anyOf": [*categories["items"]["anyOf"], {
        "type": "object", "properties": {"detail": {"type": "string"}, "category": enum(run.CATEGORY_NAMES)},
        "required": ["detail", "category"], "additionalProperties": False}]}}
    alias = {"anyOf": [{"type": "object", "properties": {"word": {"type": "string"},
                                                          "category": {"type": "string", "enum": list(run.CATEGORY_NAMES)}},
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
                 "include_current": nullable("boolean"), "alias": alias}
    assert set(props) == set(ROLES[role][1].__dataclass_fields__), f"{role} 스키마와 해석 칸이 다름"
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


# 소비 설명 문장 (채점 역할 아님 — 로컬 채팅의 실제 호출용). 문장 수 1~3 · 숫자 금지 등은 받은 뒤 explain.checked 가 검사
EXPLAIN_SCHEMA = {"type": "object", "properties": {"sentences": {"type": "array", "items": {"type": "string"}}},
                  "required": ["sentences"], "additionalProperties": False}


def schema_for(system):
    if system == spending_explain.PROMPT:
        return "spending_explanation", EXPLAIN_SCHEMA
    role = ROLE_OF[system]
    return f"{role}_reading", role_schema(role)


def gold(expect):
    """기대값 → 모델이 냈어야 할 JSON (명시 칸 + 허용값의 첫 값)"""
    data = dict(expect.get("must", {}))
    for key, values in expect.get("allow", {}).items():
        data.setdefault(key, values[0])
    return json.dumps(data, ensure_ascii=False)


def gold_complete(turn):
    """dry-run: 부른 역할의 기대값을 돌려준다. 기대값이 없는 역할을 부르면 LookupError (사례 · 구조 불일치)"""
    def complete(system, user):
        expect = turn.get(EXPECT[ROLE_OF[system]])
        if expect is None:
            raise LookupError(f"예상하지 않은 역할 호출: {ROLE_OF[system]}")
        return gold(expect)
    return complete


# ---------- 채점 ----------

def score_role(raw, expect, role):
    """한 역할의 응답 원문 → {"format_ok", "ok", "fields"}. 명시 · 허용 외의 칸은 비어 있어야 한다"""
    _, reading_type, parse = ROLES[role]
    try:
        data = json.loads(raw)
        parse(data)
    except Exception:
        return {"format_ok": False, "ok": False, "fields": {}}
    must, allow, observe = expect.get("must", {}), expect.get("allow", {}), set(expect.get("observe", []))
    fields = {}
    for field in reading_type.__dataclass_fields__:
        if field == "unclear" or field in observe:
            continue  # 자유 설명 · 관찰만 하는 예산 보조값은 채점하지 않는다
        got = data.get(field)
        if field in must:
            ok, want = run.same(field, got, must[field]), must[field]
        elif field in allow:
            ok, want = any(run.same(field, got, v) or (v is None and run.empty(got)) for v in allow[field]), \
                {"one_of": allow[field]}
        else:
            ok, want = run.empty(got), "empty"
        fields[field] = {"ok": ok, "got": run.plain(got), "want": want}
    return {"format_ok": True, "ok": all(f["ok"] for f in fields.values()), "fields": fields}


def snapshot(store, conversation):
    """상태 채점용: 분리 전 칸 + 보류된 소비 되묻기 · 정책 작업 (전환 때 지우지 않았는지)"""
    out = run.snapshot(store, conversation)
    out["spending_pending"] = (store.get_agent_state(conversation, "spending") or {}).get("pending")
    out["policy_work"] = store.get_work(conversation, "policy")
    return out


def double(spec):
    """예산 · 정책 대역: 사례에 적힌 결과만. 없으면 None(전달까지만), "not_mine" 은 다른 분야라는 답"""
    if spec is None:
        return None
    result = {"status": "not_mine"} if spec == "not_mine" else spec
    return lambda request: dict(result)


def run_turn(turn, store, conversation, server, complete_for):
    """한 단위 실행 → (결과, 결정). 이전 상태는 store 그대로 (연속 대화면 앞 턴의 실제 결과)"""
    doubles = turn.get("doubles", {})
    recorder = run.Recorder(complete_for(turn))
    agents = {"spending": partial(spending_agent.handle, store=store, complete=recorder, execute=lambda decision: None),
              "budget": double(doubles.get("budget")), "policy": double(doubles.get("policy"))}
    turned = router_run_turn(conversation, store, recorder, text=turn["text"], agents=agents, server=server)
    decision = view(turned)
    calls = [c for c in recorder.calls if "cap" not in c]
    expected = [role for role, key in EXPECT.items() if turn.get(key)]
    result = {"text": turn["text"], "roles": [c["role"] for c in calls], "expected_roles": expected,
              "raw": {c["role"]: c["text"] for c in calls if "text" in c},
              "double_used": [a for a in turned["delivered"] if a in ("budget", "policy") and doubles.get(a) is not None],
              "policy_pending": turn.get("policy_pending", False), "policy_review": turn.get("policy_review")}
    if any("cap" in c for c in recorder.calls):
        return {**result, "kind": "cap_skipped"}, decision
    if any("error" in c for c in recorder.calls):
        return {**result, "kind": "call_failed", "call_error": next(c["error"] for c in recorder.calls if "error" in c)}, \
            decision
    for role in sorted(set(result["roles"]) | set(expected)):
        if role not in expected:
            result[role] = {"format_ok": True, "ok": False, "unexpected_call": True}
        elif role not in result["raw"]:
            result[role] = {"format_ok": True, "ok": False, "missing_call": True}
        else:
            result[role] = score_role(result["raw"][role], turn[EXPECT[role]], role)
    result["decision_ok"], result["decision"] = run.score_paths(decision, turn.get("expect_route", {}))
    result["state_ok"], result["state"] = run.score_paths(snapshot(store, conversation), turn.get("expect_state", {}))
    roles = [result[r] for r in ("router", "spending") if r in result]
    result["kind"] = ("format" if not all(r["format_ok"] for r in roles) else
                      "extraction" if not all(r["ok"] for r in roles) else
                      "decision" if not result["decision_ok"] else
                      "state" if not result["state_ok"] else "ok")
    return result, decision


def run_case(case, complete_for):
    store = run.build_store(case.get("state", {}), "eval")
    result, _ = run_turn(case, store, "eval", run.FakeServer(case.get("server")), complete_for)
    return {"id": case["id"], "split": case["split"], **result}


def run_scenario(scenario, complete_for):
    store, server, turns, first_error = run.MemoryStateStore(), run.FakeServer(scenario.get("server")), [], None
    for index, turn in enumerate(scenario["turns"]):
        server.states.update((turn.get("before") or {}).get("server", {}))
        result, decision = run_turn(turn, store, "eval", server, complete_for)
        if result["kind"] != "ok" and not result["policy_pending"]:
            result["after_first_error"] = first_error is not None  # 앞 오류가 만든 상태 때문일 수 있다 (원인 확정 아님)
            first_error = first_error or index + 1
        turns.append({"id": f"{scenario['id']}#{index + 1}", "turn": index + 1, **result})
        if result["kind"] in ("cap_skipped", "call_failed", "aborted"):
            break
        run.after_turn(turn, decision, store, "eval", server)  # 실제 결과 상태 위에 대역 결과만 이어 붙인다
    scored = [t for t in turns if not t["policy_pending"]]
    return {"id": scenario["id"], "split": scenario["split"], "turns": turns, "first_error_turn": first_error,
            "ok": len(turns) == len(scenario["turns"]) and all(t["kind"] == "ok" for t in scored)}


def summarize(rows):
    """역할별 해석 · 최종 결정 · 상태를 따로. 분모 = 정책 미정 · 보내지 않음 · 호출 실패를 뺀 단위 (개수는 따로)"""
    scored = [r for r in rows if not r["policy_pending"] and r["kind"] not in ("cap_skipped", "aborted", "call_failed")]
    count = lambda pick: {"ok": sum(1 for r in scored if pick(r) is True),  # noqa: E731
                          "of": sum(1 for r in scored if pick(r) is not None)}
    role = lambda name: lambda r: r[name]["ok"] if name in r else None  # noqa: E731
    return {
        "units": len(rows),
        "policy_pending_excluded": [r["id"] for r in rows if r["policy_pending"]],
        "not_sent": {k: [r["id"] for r in rows if r["kind"] == k] for k in ("cap_skipped", "aborted", "call_failed")
                     if any(r["kind"] == k for r in rows)},
        "router_reading": count(role("router")), "spending_reading": count(role("spending")),
        "format": count(lambda r: all(r[x]["format_ok"] for x in ("router", "spending") if x in r)
                        if any(x in r for x in ("router", "spending")) else None),
        "decision": count(lambda r: r["decision_ok"]), "state": count(lambda r: r["state_ok"]),
        "double_dependent": [r["id"] for r in rows if r["double_used"]],
        "policy_review": [r["id"] for r in rows if r.get("policy_review")],  # 점수에는 넣고 따로 표시
        "kinds": {k: sum(r["kind"] == k for r in rows) for k in sorted({r["kind"] for r in rows})},
    }


# ---------- 실행 ----------

def main(argv=None, opener=None):
    parser = argparse.ArgumentParser(description="단계 B 역할별 평가 (기본 dry-run)")
    parser.add_argument("--split", default="all", help="regression_dev · regression_new · reference · new_b · all")
    parser.add_argument("--scenarios", action="store_true")
    parser.add_argument("--cases", help="실행할 단위 ID 쉼표 목록")
    parser.add_argument("--cases-file", help="역할별 사례 파일 (기본 cases_b.json)")
    parser.add_argument("--router-sha", help="고정한 라우터 지시문 해시 앞자리. 다르면 실행하지 않는다")
    parser.add_argument("--spending-sha", help="고정한 소비 지시문 해시 앞자리")
    parser.add_argument("--replay", help="이전 stage_b results.jsonl 의 역할별 응답으로 다시 채점 (API 호출 없음)")
    parser.add_argument("--live", action="store_true", help="실제 생성 요청 (ROUTER_EVAL_LIVE=1 · --max-calls · --ledger 필요)")
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--ledger")
    parser.add_argument("--structured", action="store_true", help="역할별 Structured Outputs(json_schema) 사용")
    parser.add_argument("--model", default=None, help="기본: 환경변수 LLM_MODEL")
    parser.add_argument("--reasoning-effort", default="low", choices=["none", "low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--max-output-tokens", type=int, default=2000)
    parser.add_argument("--price-in", type=float)
    parser.add_argument("--price-out", type=float)
    parser.add_argument("--output-dir")
    args = parser.parse_args(argv)

    data = load(Path(args.cases_file) if args.cases_file else CASES_B)
    cases, flows = units(data, args.split, args.scenarios)
    if args.cases:
        wanted = [i.strip() for i in args.cases.split(",") if i.strip()]
        unknown = set(wanted) - {c["id"] for c in cases} - {s["id"] for s in flows}
        if unknown:
            raise SystemExit(f"고른 묶음에 없는 단위 ID: {', '.join(sorted(unknown))}")
        cases = [c for c in cases if c["id"] in wanted]
        flows = [s for s in flows if s["id"] in wanted]
    shas = {role: hashlib.sha256(prompt.encode("utf-8")).hexdigest() for role, (prompt, _, _) in ROLES.items()}
    for role, pin in (("router", args.router_sha), ("spending", args.spending_sha)):
        if pin and not shas[role].startswith(pin):
            raise SystemExit(f"{role} 지시문 해시가 고정값과 달라요: {shas[role][:8]} ≠ {pin}")

    client = ledger = None
    if args.live and args.replay:
        raise SystemExit("--live 와 --replay 는 같이 쓸 수 없어요")
    if args.live:
        if not args.max_calls or args.max_calls < 1 or not args.ledger:
            raise SystemExit("--live 는 --max-calls(1 이상)와 --ledger 가 필요해요")
        if os.environ.get("ROUTER_EVAL_LIVE") != "1":
            raise SystemExit("--live 는 환경변수 ROUTER_EVAL_LIVE=1 이 함께 있어야 해요")
        key, model = os.environ.get("OPENAI_API_KEY"), args.model or os.environ.get("LLM_MODEL")
        if not key or not model:
            raise SystemExit("OPENAI_API_KEY · 모델(--model 또는 LLM_MODEL)이 필요해요")
        ledger = run.Ledger(args.ledger, args.max_calls)
        ledger.claim([c["id"] for c in cases] + [f["id"] for f in flows])
        client = ResponsesClient(model, key, min(args.max_calls, ledger.remaining), args.timeout, args.max_output_tokens,
                                 args.reasoning_effort, schema_for if args.structured else None,
                                 on_attempt=ledger.spend, **({"opener": opener} if opener else {}))
        complete_for = lambda turn: client.complete  # noqa: E731
    elif args.replay:
        saved = {}
        for line in Path(args.replay).read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            for unit in row.get("turns", [row]):
                for role, text in (unit.get("raw") or {}).items():
                    saved[(unit["id"], role)] = text

        def complete_for(turn):
            def complete(system, user):
                key = (turn["_id"], ROLE_OF[system])
                if key not in saved:
                    raise LookupError(f"저장 응답 없음: {key}")
                return saved[key]
            return complete
    else:
        complete_for = gold_complete

    for c in cases:
        c["_id"] = c["id"]
    for s in flows:
        for i, t in enumerate(s["turns"], 1):
            t["_id"] = f"{s['id']}#{i}"
    case_results = [run_case(c, complete_for) for c in cases]
    flow_results = [run_scenario(s, complete_for) for s in flows]
    if client and client.fatal:
        for r in case_results + [t for s in flow_results for t in s["turns"]]:
            if r["kind"] == "cap_skipped":
                r["kind"] = "aborted"  # 앞선 fatal 오류로 보내지 않음
    turns = [t for s in flow_results for t in s["turns"]]
    roles = [r for row in case_results + turns for r in row["roles"]]
    meta = {
        "mode": "live" if args.live else "replay" if args.replay else "dry-run",
        "time": datetime.now(timezone.utc).isoformat(), "split": args.split,
        "cases_version": data["version"], "cases_sha256": data["sha256"], "independent": data.get("independent", {}),
        "role_prompt_sha256": shas, "structured_outputs": args.structured, "retries": 0,
        "model": client.model if client else None, "reasoning_effort": args.reasoning_effort if client else None,
        "max_calls": args.max_calls, "ledger": ledger.data if ledger else None,
        "fatal_error": client.fatal if client else None, "blocked_by_cap": client.blocked if client else 0,
        "planned_calls": planned_calls(cases, flows),
        "made_calls": {r: roles.count(r) for r in sorted(set(roles))},
        "single": {split: summarize([r for r in case_results if r["split"] == split])
                   for split in sorted({r["split"] for r in case_results})},
        "scenarios": {split: {**summarize([t for s in flow_results if s["split"] == split for t in s["turns"]]),
                              "flows": sum(1 for s in flow_results if s["split"] == split),
                              "flows_ok": sum(s["ok"] for s in flow_results if s["split"] == split)}
                      for split in sorted({s["split"] for s in flow_results})},
        "usage": run.usage_totals(client.attempts, args.price_in, args.price_out) if client else None,
    }
    if args.output_dir:
        out = run.output_dir(args.output_dir)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (out / f"{stamp}-b-meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        with open(out / f"{stamp}-b-results.jsonl", "w", encoding="utf-8") as f:
            for row in case_results + flow_results:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    for row in case_results + turns:
        if row["kind"] != "ok":
            print(f"[{row['kind']}] {row['id']} {row['text']}")
    return meta, case_results, flow_results


if __name__ == "__main__":
    main()

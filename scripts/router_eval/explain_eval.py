"""B 1차 증감 설명 평가 (MEMO 26-15) — 사례(cases_explain.json)마다 서버 멈춤 · 계획 · 답 · 검사를 따로 채점한다.

    .venv/bin/python scripts/router_eval/explain_eval.py                                       # dry-run (가짜 계획 · 답, 호출 없음)
    .venv/bin/python scripts/router_eval/explain_eval.py --replay <이전 results.jsonl>          # 저장된 응답으로 다시 채점 (호출 없음)
    ROUTER_EVAL_LIVE=1 .venv/bin/python scripts/router_eval/explain_eval.py --live --max-calls 40 \\
        --ledger <저장소 밖>/ledger.json --output-dir <저장소 밖> --prompt-sha <앞 8자리> --cases-sha <앞 8자리>

- 임시 테스트 DB(spending_fixture.settings)에 design.json 을 적재하고, 사례의 extra 거래만 그 사례 동안 넣었다 되돌린다(서비스 · 로컬 fixture DB 안 건드림)
- 실제 경로 그대로: explain.evidence → explain._planned(claims.run — 계획 · 묶기 · 답 · 검사 · 시간 상한)
- 실행 전 지시문(계획 + 답) · 사례 파일 해시를 고정(--prompt-sha · --cases-sha). 다르면 실행하지 않는다. 결과를 보고 기대값을 바꾸지 않는다
- 장부 · 상한 · 재시도 없음은 chat.llm 규칙 그대로. 결과 파일엔 계획 · 답 LLM 원문(자리표시뿐 — 금액 · 가게 이름 없음)과 채점만
"""
import argparse
import hashlib
import json
import os
import statistics
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["DJANGO_SETTINGS_MODULE"] = "spending_fixture.settings"  # 로컬 fixture 설정만 (서비스 DB 면 seed 가 멈춘다)
import django  # noqa: E402

django.setup()

from django.db import connection, transaction  # noqa: E402

from chat.llm.fake import FakeReading  # noqa: E402
from chat.llm.ledger import Ledger  # noqa: E402,F401  (저장소 밖 경로 검사를 같이 쓴다)
from spending_analysis import claims, explain  # noqa: E402

CASES = Path(__file__).resolve().parent / "cases_explain.json"


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prompt_sha():
    return sha(claims.PLAN_PROMPT + "\n---\n" + claims.ANSWER_PROMPT)


class Recorder:
    """complete 를 감싸 역할별 원문 · 시간 · 실패를 남긴다 (키 · 요청 원문은 남기지 않음)"""

    def __init__(self, complete):
        self.complete, self.calls = complete, []

    def __call__(self, system, user, timeout=None):
        role = "plan" if system == claims.PLAN_PROMPT else "answer"
        started, row = time.monotonic(), {"role": role, "raw": None, "error": None}
        self.calls.append(row)
        try:
            row["raw"] = self.complete(system, user, timeout=timeout)
            return row["raw"]
        except Exception as error:
            row["error"] = type(error).__name__
            raise
        finally:
            row["ms"] = round((time.monotonic() - started) * 1000)


class Replay:
    """이전 결과의 계획 · 답 원문을 사례 · 역할 순서대로 다시 넣는다 (바뀐 검사 · 채점 기준으로 다시 채점, API 호출 없음)"""

    def __init__(self, path):
        self.saved = {r["id"]: list(r["calls"]) for r in map(json.loads, Path(path).read_text(encoding="utf-8").splitlines())}
        self.queue = []

    def start(self, case_id):
        self.queue = list(self.saved.get(case_id, []))

    def __call__(self, system, user, timeout=None):
        row = self.queue.pop(0) if self.queue else {"raw": None, "error": "missing"}
        if row["raw"] is None:
            raise RuntimeError(f"저장된 응답 없음: {row['error']}")
        return row["raw"]


def _extra(case, persona_user):
    from finance.models import Card, FinancialTransaction
    card = Card.objects.filter(user=persona_user).first()
    for i, tx in enumerate(case.get("extra", [])):
        hh, mm = map(int, tx["time"].split(":"))
        FinancialTransaction.objects.create(
            user=persona_user, source_kind="card", card=card, source_key=f"eval:{case['id']}:{i}",
            occurred_on=date.fromisoformat(tx["day"]), occurred_time=f"{hh:02d}:{mm:02d}", amount=tx.get("amount", 20_000),
            direction="out", transaction_type="spending", merchant_name=tx.get("merchant", "늦은밤분식"),
            confirmed_category_id=tx.get("category", case["category"]), confirmation_state="confirmed")


def score(case, trace, first):
    """→ {"stop", "plan", "answer"}: True · False · None(해당 없음) + 이유"""
    out, why = {"stop": trace.get("stop") == case["stop"]}, []
    if not out["stop"]:
        why.append(f"stop={trace.get('stop')}")
    if case["stop"] is not None:
        return {**out, "plan": None, "answer": None, "why": why}
    plan = trace.get("plan") or {"queries": []}
    got = [(q["by"], bool(q["focus"])) for q in plan["queries"]]
    ok = True
    for m in case["plan"]["must"]:
        if not any(by == m["by"] and (not m.get("focus") or focus) for by, focus in got):
            ok, _ = False, why.append(f"plan_missing={m['by']}")
    if any(by in case["plan"]["must_not"] for by, _ in got):
        ok, _ = False, why.append("plan_forbidden")
    zero = case["plan"]["zero"]
    if (zero == "required" and got) or (zero == "forbidden" and not got):
        ok, _ = False, why.append(f"plan_zero={zero}")
    out["plan"] = ok
    cited = {c["axis"] for c in trace.get("cited", [])} | {c["kind"] for c in trace.get("cited", [])}
    ans = trace.get("fallback") is None and not trace.get("check")
    if trace.get("fallback"):
        why.append(f"fallback={trace['fallback']}" + (f"({','.join(trace['check'])})" if trace.get("check") else ""))
    missing = [m for m in case["answer"]["must"] if m not in cited]
    if ans and missing:
        ans, _ = False, why.append(f"answer_missing={','.join(missing)}")
    if ans and case["answer"]["any"] and not cited & set(case["answer"]["any"]):
        ans, _ = False, why.append("answer_any")
    out["answer"] = ans
    # 가설 참고값(hypothesis 단어 목록)과 계획 LLM 판단 비교 — 단어 목록이 '첫 결과로 답함'이라 볼지 vs 계획 0개
    guess = claims.hypothesis(case["question"])
    ref = bool(guess and any(c["axis"] == guess["axis"] and (guess["name"] is None or c["subject"] == guess["name"]) for c in first))
    out["hypothesis"] = {"ref": guess, "ref_answered": ref, "plan_zero": not got, "differs": ref != (not got)}
    return {**out, "why": why}


def run_case(case, complete, today):
    from spending_fixture.test_fixture import user
    item = {"type": 7 if case["category"] else 6, "category": case["category"], "fresh": True}
    persona = user(case["persona"])
    with transaction.atomic():
        _extra(case, persona)
        ev = explain.evidence(persona, item, today)
        first = claims.first_claims(ev, item) if ev["state"] == "ok" else []
        started = time.monotonic()
        planned, trace = explain._planned(ev, item, case["question"], complete)
        elapsed = round((time.monotonic() - started) * 1000)
        transaction.set_rollback(True)
    return {"id": case["id"], "question": case["question"], "lines": planned and planned["lines"], "trace": trace,
            "ms": elapsed, "human": case["human"], **score(case, trace or {}, first)}


def summarize(rows, calls):
    def rate(key):
        scored = [r[key] for r in rows if r[key] is not None]
        return {"ok": sum(scored), "of": len(scored)}
    llm = [r for r in rows if r["stop"] and r["plan"] is not None]
    fallbacks = {}
    for r in llm:
        reason = (r["trace"] or {}).get("fallback")
        if reason:
            fallbacks[reason] = fallbacks.get(reason, 0) + 1
    ms = sorted(r["ms"] for r in llm)
    return {"stop": rate("stop"), "plan": rate("plan"), "answer": rate("answer"),
            "fallback": {"count": sum(fallbacks.values()), "of": len(llm), "reasons": fallbacks},
            "latency_ms": {"median": statistics.median(ms) if ms else None, "max": ms[-1] if ms else None,
                           "p90": ms[min(len(ms) - 1, int(len(ms) * 0.9))] if ms else None},
            "hypothesis_differs": [r["id"] for r in rows if r.get("hypothesis", {}).get("differs")],
            "calls": len(calls), "call_errors": sum(1 for c in calls if c["error"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description="B 1차 증감 설명 평가 (기본 dry-run)")
    parser.add_argument("--live", action="store_true", help="실제 계획 · 답 LLM (ROUTER_EVAL_LIVE=1 필요)")
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--ledger")
    parser.add_argument("--output-dir")
    parser.add_argument("--cases", help="실행할 사례 ID 쉼표 목록")
    parser.add_argument("--replay", help="이전 results.jsonl 의 응답으로 다시 채점 (호출 없음)")
    parser.add_argument("--prompt-sha")
    parser.add_argument("--cases-sha")
    args = parser.parse_args(argv)
    raw_cases = CASES.read_text(encoding="utf-8")
    shas = {"prompt": prompt_sha(), "cases": sha(raw_cases)}
    print(json.dumps({"prompt_sha": shas["prompt"][:8], "cases_sha": shas["cases"][:8]}))
    for name, want in (("prompt", args.prompt_sha), ("cases", args.cases_sha)):
        if want and not shas[name].startswith(want):
            raise SystemExit(f"{name} 해시가 고정값과 달라요 ({shas[name][:8]} != {want})")
    data = json.loads(raw_cases)
    cases = [c for c in data["cases"] if not args.cases or c["id"] in args.cases.split(",")]
    if args.live:
        if os.environ.get("ROUTER_EVAL_LIVE") != "1" or not args.max_calls or not args.ledger or not args.output_dir:
            raise SystemExit("실제 호출은 ROUTER_EVAL_LIVE=1 + --max-calls + --ledger + --output-dir 를 모두 줘야 해요")
        if not (args.prompt_sha and args.cases_sha):
            raise SystemExit("실제 호출 전에 --prompt-sha · --cases-sha 로 해시를 고정해 주세요")
        from chat.llm.live import LiveReading
        complete = Recorder(LiveReading(args.max_calls, args.ledger))
    elif args.replay:
        complete = Recorder(Replay(args.replay))
    else:
        complete = Recorder(FakeReading())
    old = connection.creation.create_test_db(verbosity=0, autoclobber=True, keepdb=False)
    try:
        from spending_fixture import seed
        seed.apply(seed.load("design.json"))
        today = seed.AS_OF.date()
        rows = []
        for case in cases:
            before = len(complete.calls)
            if args.replay:
                complete.complete.start(case["id"])
            row = run_case(case, complete, today)
            row["calls"] = complete.calls[before:]
            rows.append(row)
            print(json.dumps({k: row[k] for k in ("id", "stop", "plan", "answer", "why", "ms")}, ensure_ascii=False))
    finally:
        connection.creation.destroy_test_db(old, verbosity=0)
    summary = summarize(rows, complete.calls)
    summary.update(mode="live" if args.live else "replay" if args.replay else "dry", cases_version=data["version"], **{f"{k}_sha": v[:8] for k, v in shas.items()},
                   time=datetime.now(timezone.utc).isoformat())
    if args.live:
        summary["usage"] = complete.complete.usage()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.output_dir:
        out = Path(args.output_dir).expanduser().resolve()
        if out == ROOT or ROOT in out.parents:
            raise SystemExit("--output-dir 는 저장소 밖 경로여야 해요")
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (out / f"{stamp}-explain-results.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                                                             encoding="utf-8")
        (out / f"{stamp}-explain-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    main()

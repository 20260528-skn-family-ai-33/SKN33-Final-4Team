"""소비 분석 에이전트 루프의 도구 4개 (MEMO 29, 11/10 1일차 — 아직 아무 데서도 부르지 않는다)

도구 = (인자 dict, 문맥) → 관찰 결과 dict. 문맥 = {user, today, as_of, complete(B 계획 · 답 LLM), question, results(이번 루프의 관찰 결과)}
- 소비 · 예산 조회는 일반 질문과 같은 경로: reading.parse → dialog.decide → adapter.run → answer.compose (tools.py 를 LLM 인자로 직접 부르지 않는다)
- 관찰 결과 = {"id", "tool", "status", "reason"?, "facts"?, "server"?}. server 는 서버만 본다(문장 · 카드 · 기록 · Decision).
  LLM 에는 for_llm() 사본만 — 금액 · 날짜 · 가게 이름 없이 방향 · 순위 · 상태 · 숫자를 □로 가린 문장
- status: ok · invalid(인자 틀림) · ask(되물어야 함 — 루프가 멈춘다) · unsupported · failed(조회 실패)
- explain_change · where_changed 는 앞 결과(ref)가 확정된 6 · 7 · 8 · 9번 비교일 때만
"""
import re

from django.db import DatabaseError

from . import adapter, claims, explain, investigation, narrate
from .agent import _merchant
from .answer import compose
from .dialog import analysis_row, decide
from .reading import ReadFailed, parse

SPEND_ARGS = ("target", "period", "compare", "compare_month", "month", "months", "include_current", "categories", "payment",
              "weekday", "time_slot", "amount_min", "amount_max", "measure", "group_by", "top_n", "date_from", "date_to",
              "include_fixed")
SPEND_TARGETS = ("spend", "overview")
BUDGET_ARGS = ("categories", "breakdown")
EXPLAIN_ARGS = ("ref", "hypothesis")
WHERE_ARGS = ("ref", "by")
COMPARE_TYPES = (6, 7, 8, 9)
TOP = 3  # where_changed 카드에 보일 같은 방향 변화 줄 수 (LLM 에는 전부)


def _obs(tool, status, reason=None, **extra):
    return {"tool": tool, "status": status, **({"reason": reason} if reason else {}), **extra}


def for_llm(obs):
    """LLM 에 보낼 사본 — 서버 칸(server)을 뺀다"""
    return {k: v for k, v in obs.items() if k != "server"}


def _mask(text, shops=()):
    for n, shop in enumerate(shops, 1):
        text = text.replace(shop, f"가게 {n}")
    return re.sub(r"[0-9][0-9,.]*", "□", text)


def _shops(result):
    """문장에 들어가는 가게 이름 (범용 조회의 가게별 묶음 · 가장 큰 1건) — LLM 에는 순번으로"""
    fig = result.get("confirmed") or result.get("observed") or {}
    if result["type"] != 10 or not isinstance(fig, dict):
        return []
    names = [g["name"] for g in fig.get("groups") or [] if result["item"]["query"].get("group_by") == "merchant"]
    names += [b["merchant_name"] for b in (fig.get("max"), fig.get("max_living")) if b and b["merchant_name"]]
    return sorted(set(names), key=len, reverse=True)  # 긴 이름부터 (짧은 이름이 긴 이름 일부를 먼저 바꾸지 않게)


def _state(r, summary):
    if r["result"]["type"] in (6, 7) and not narrate._comparison(r["result"])["available"]:
        return "not_comparable"
    return "confirmed" if summary["evidence"] == "confirmed" else "limited"


def _facts(results, summaries):
    """조회 결과 → LLM 용 사실. 6 · 7번(비교 가능)은 답변 조합 문장(방향 있음), 그 밖은 답 문장 한 줄씩"""
    out = []
    for r, s in zip(results, summaries):
        if not r["ok"]:
            continue
        state = _state(r, s)
        if narrate.applies([r]):
            rows = [{"kind": f["kind"], "subject": f["subject"], "direction": f["direction"], "preview": p["preview"]}
                    for f, p in zip(narrate.facts([r]), narrate.for_llm(narrate.facts([r])))]
        else:
            rows = [{"kind": "line", "subject": None, "direction": None, "preview": _mask(line, _shops(r["result"]))}
                    for line in compose([r])["lines"]]
        out += [{**row, "rank": None, "state": state} for row in rows]
    return out


def _lookup(tool, data, ctx):
    """해석 칸 → 일반 질문과 같은 결정 · 실행 · 답 (안전 검사를 건너뛰지 않는다)"""
    try:
        interp = parse({"mine": True, **data})
    except ReadFailed as error:
        return _obs(tool, "invalid", f"bad_{error}")
    today = ctx["today"]
    decision = decide(interp, ctx["question"], merchant=ctx["user"] and _merchant(ctx["user"], {"today": today and today.isoformat()}),
                      today=today)
    if decision.status == "ask":
        return _obs(tool, "ask", decision.reason, server={"decision": decision})
    if decision.status != "ready" or not decision.items:
        return _obs(tool, "unsupported", decision.reason or decision.status)
    results = adapter.run(ctx["user"], decision, today=today, as_of=ctx["as_of"])
    if not any(r["ok"] for r in results):
        return _obs(tool, "failed", "analysis_failed")
    summaries = [adapter.summarize(r["result"]) if r["ok"] else None for r in results]
    rows = [analysis_row({**r, "result": s} if r["ok"] else r) for r, s in zip(results, summaries)]
    return _obs(tool, "ok", facts=_facts(results, summaries),
                server={"reply": compose(results, decision.notices), "results": results, "records": rows, "decision": decision})


def spend_lookup(args, ctx):
    """소비 합계 · 비교 · 추이 · 건수 · 평균"""
    if set(args) - set(SPEND_ARGS):
        return _obs("spend_lookup", "invalid", "unknown_args")
    if args.get("target") not in (None, *SPEND_TARGETS):
        return _obs("spend_lookup", "invalid", "bad_target")
    return _lookup("spend_lookup", args, ctx)


def budget_lookup(args, ctx):
    """예산 잔액 (breakdown = 카테고리별 — 해석의 '카테고리별(compare=category)'과 같은 뜻)"""
    if set(args) - set(BUDGET_ARGS) or args.get("breakdown") not in (None, True, False):
        return _obs("budget_lookup", "invalid", "unknown_args")
    data = {"target": "budget_left", "categories": args.get("categories"), **({"compare": "category"} if args.get("breakdown") else {})}
    return _lookup("budget_lookup", data, ctx)


def _compared(tool, ref, ctx):
    """ref → 확정된 비교 기록 항목 목록, 아니면 관찰 결과(unsupported · invalid)"""
    if not isinstance(ref, str):
        return _obs(tool, "invalid", "bad_ref")
    found = next((o for o in ctx["results"] if o.get("id") == ref), None)
    if found is None:
        return _obs(tool, "invalid", "unknown_ref")
    rows = [row for row in (found.get("server") or {}).get("records", ())
            if row["ok"] and row["type"] in COMPARE_TYPES and row.get("evidence") == "confirmed"]
    return rows or _obs(tool, "unsupported", "no_comparison")


def _claim_facts(found):
    return [{**c, "state": "confirmed"} for c in claims.for_llm(found)]


def explain_change(args, ctx):
    """앞에서 본 비교가 왜 늘었는지 · 줄었는지 (B — 실패하면 지금 템플릿 설명)"""
    if set(args) - set(EXPLAIN_ARGS):
        return _obs("explain_change", "invalid", "unknown_args")
    try:
        hypothesis = investigation.parse(args.get("hypothesis"))
    except (ValueError, TypeError):
        return _obs("explain_change", "invalid", "bad_hypothesis")
    rows = _compared("explain_change", args.get("ref"), ctx)
    if isinstance(rows, dict):
        return rows
    user, today = ctx["user"], ctx["today"]
    try:
        reply = explain.answer(user, rows, today=today, plan=ctx["complete"], question=ctx["question"], hypothesis=hypothesis)
        # LLM 에는 주장(가게는 순번 · 금액 없음)만. 답 문장에는 가게 이름이 있어 보내지 않는다
        # shortcut: explain.answer 와 여기서 근거를 두 번 읽는다, 루프를 서비스에 연결할 때 근거를 한 번만 읽게 바꾼다
        found = []
        for row in rows:
            ev = explain.evidence(user, row, today)
            if ev["state"] == "ok":
                found += claims.first_claims(ev, row)
    except (DatabaseError, adapter.AnalysisFailed):
        return _obs("explain_change", "failed", "analysis_failed")
    return _obs("explain_change", "ok", facts=_claim_facts(found), server={"reply": reply})


def where_changed(args, ctx):
    """앞에서 본 비교를 기준별로 나눠 증감 순위 (B 의 비교하며 묶기)"""
    if set(args) - set(WHERE_ARGS):
        return _obs("where_changed", "invalid", "unknown_args")
    if args.get("by") not in claims.AXES:
        return _obs("where_changed", "invalid", "bad_by")
    rows = _compared("where_changed", args.get("ref"), ctx)
    if isinstance(rows, dict):
        return rows
    if len(rows) > 1:  # 어느 비교를 나눌지 모른다 — 고르지 않는다
        return _obs("where_changed", "unsupported", "many_comparisons")
    row = rows[0]
    try:
        ev = explain.evidence(ctx["user"], row, ctx["today"])
    except DatabaseError:
        return _obs("where_changed", "failed", "analysis_failed")
    if ev["state"] != "ok":
        return _obs("where_changed", "unsupported", ev["state"])
    if ev["delta"] == 0:
        return _obs("where_changed", "unsupported", "no_change")
    try:
        found = claims.compare_group(ev, row, args["by"])
    except ValueError:  # 카테고리로 좁힌 비교를 카테고리로 묶기 등
        return _obs("where_changed", "unsupported", "bad_axis")
    texts = [c["text"] for c in found if c["rank"] is not None][:TOP]
    texts = texts[:1] + [t.removeprefix(claims.FIXED) for t in texts[1:]]  # '고정지출을 빼고 보면'은 한 번만
    return _obs("where_changed", "ok", facts=_claim_facts(found),
                server={"reply": {"lines": texts, "notices": [], "view": [{"kind": "text", "lines": texts}]}})


TOOLS = {"spend_lookup": spend_lookup, "budget_lookup": budget_lookup, "explain_change": explain_change,
         "where_changed": where_changed}

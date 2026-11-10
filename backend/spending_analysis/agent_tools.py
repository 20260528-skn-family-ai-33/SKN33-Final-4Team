"""소비 분석 추가 판단(MEMO 31)의 관찰 결과와 추가 도구 2개

- observe: 첫 조회(기존 경로가 이미 실행한 결과) → 관찰 결과. 첫 조회를 다시 실행하지 않는다
- 추가 도구 = (인자 dict, 문맥) → 관찰 결과. 문맥 = {user, today, as_of, question, first(첫 관찰 결과), request(검증된 첫 조회 요청)}
  · where_changed: 첫 비교 결과를 by 로만 나눈다. 기간 · 비교 기간 · 카테고리 · 필터는 첫 결과 기록에서 서버가 상속 (LLM 은 by 만)
  · budget_lookup: 남은 예산. 인자 없음 — 카테고리는 첫 요청 그대로, 기간은 기존 예산 주기 규칙 (dialog.decide)
- 관찰 결과 = {"id", "tool", "status", "reason"?, "facts"?, "server"?}. server 는 서버만 본다(문장 · 카드 · 기록 · Decision).
  LLM 에는 for_llm() 사본만 — 금액 · 날짜 · 가게 이름 없이 방향 · 순위 · 상태 · 숫자를 □로 가린 문장
- status: ok · invalid(인자 틀림) · unsupported · failed(조회 실패)
"""
import re
from dataclasses import replace

from django.db import DatabaseError

from . import adapter, claims, explain, narrate
from .answer import compose, josa, won
from .dialog import analysis_row, decide
from .reading import parse

WHERE_ARGS = ("by",)
COMPARE_TYPES = (6, 7, 8, 9)
TOP = 3  # where_changed 카드에 보일 같은 방향 변화 줄 수 (LLM 에는 전부)


def _obs(tool, status, reason=None, **extra):
    return {"tool": tool, "status": status, **({"reason": reason} if reason else {}), **extra}


def for_llm(obs):
    """LLM 에 보낼 사본 — 서버 칸(server)과 사실마다 서버 문장(text)을 뺀다"""
    out = {k: v for k, v in obs.items() if k != "server"}
    if "facts" in out:
        out["facts"] = [{k: v for k, v in f.items() if k != "text"} for f in out["facts"]]
    return out


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
            table = narrate.facts([r])
            rows = [{"kind": f["kind"], "subject": f["subject"], "direction": f["direction"], "preview": p["preview"],
                     "text": f["text"]} for f, p in zip(table, narrate.for_llm(table))]
        else:
            rows = [{"kind": "line", "subject": None, "direction": None, "preview": _mask(line, _shops(r["result"])),
                     "text": line} for line in compose([r])["lines"]]
        out += [{**row, "rank": None, "state": state} for row in rows]
    return out


def observe(tool, decision, results):
    """이미 실행한 조회 결과 → 관찰 결과 (facts 는 LLM 용, server 는 카드 · 기록 · Decision)"""
    if not any(r["ok"] for r in results):
        return _obs(tool, "failed", "analysis_failed")
    summaries = [adapter.summarize(r["result"]) if r["ok"] else None for r in results]
    rows = [analysis_row({**r, "result": s} if r["ok"] else r) for r, s in zip(results, summaries)]
    return _obs(tool, "ok", facts=_facts(results, summaries),
                server={"reply": compose(results, decision.notices), "results": results, "records": rows, "decision": decision})


def comparisons(obs):
    """관찰 결과 중 후속 분석에 쓸 수 있는 비교 (확정된 6 · 7 · 8 · 9번 기록)"""
    return [row for row in (obs.get("server") or {}).get("records", ())
            if row["ok"] and row["type"] in COMPARE_TYPES and row.get("evidence") == "confirmed"]


def budget_lookup(args, ctx):
    """남은 예산 — 첫 요청의 카테고리(코드만) 그대로. 일반 질문과 같은 결정 · 실행 (안전 검사를 건너뛰지 않는다)"""
    if args:
        return _obs("budget_lookup", "invalid", "unknown_args")
    codes = [c for c in ctx["request"].categories if isinstance(c, str)]
    interp = parse({"mine": True, "target": "budget_left", **({"categories": codes} if codes else {})})
    today = ctx["today"]
    decision = decide(interp, ctx["question"], today=today)
    if decision.status != "ready" or not decision.items:  # 되묻기 · 미지원은 여기서 묻지 않는다 — 못 확인했다고 안내
        return _obs("budget_lookup", "unsupported", decision.reason or decision.status)
    try:
        results = adapter.run(ctx["user"], replace(decision, request=decision.request or interp), today=today, as_of=ctx["as_of"])
    except DatabaseError:
        return _obs("budget_lookup", "failed", "analysis_failed")
    return observe("budget_lookup", decision, results)


def where_changed(args, ctx):
    """첫 비교를 기준별로 나눠 증감 순위 (B 의 비교하며 묶기 — 첫 결과가 읽은 두 기간 집계를 다시 묶는다)"""
    if set(args) - set(WHERE_ARGS):
        return _obs("where_changed", "invalid", "unknown_args")
    if args.get("by") not in claims.AXES:
        return _obs("where_changed", "invalid", "bad_by")
    rows = comparisons(ctx["first"])
    if not rows:
        return _obs("where_changed", "unsupported", "no_comparison")
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
        found = claims.compare_group(ev, row, args["by"], per_day=ctx.get("measure") != "sum")  # 요일 합계를 물었으면 합계
    except ValueError:  # 카테고리로 좁힌 비교를 카테고리로 묶기 등
        return _obs("where_changed", "unsupported", "bad_axis")
    ranked = [c for c in found if c["rank"] is not None][:TOP]
    if not ranked:
        return _obs("where_changed", "unsupported", "no_group")
    texts = [c["text"] for c in ranked]
    texts = texts[:1] + [t.removeprefix(claims.FIXED) for t in texts[1:]]  # 글자만 보는 화면용 문장 — '고정지출을 빼고 보면'은 한 번만
    return _obs("where_changed", "ok", server={"reply": {"lines": texts, "notices": [], "view": _where_view(args["by"], ranked)}})


GROUP_TITLE = {"time_slot": "시간대별 변화", "weekday": "요일별 하루 평균 변화", "merchant": "가게별 변화", "category": "카테고리별 변화"}


def _where_view(by, ranked):
    """나눠 본 결과 카드: 순위 줄(전 → 후 · 증감, 색 + 글자) + 한 줄 요약. 시간대 · 요일은 고정지출을 뺀 기준이라 제목에 적는다"""
    up = ranked[0]["direction"] == "up"
    name = (lambda c: f"{c['subject']} 시간대") if by == "time_slot" else (lambda c: c["subject"] or "이름 없는 곳")
    rows = [{"name": name(c), "value": f"{won(c['before'])} → {won(c['after'])}",
             "note": ("+" if c["after"] >= c["before"] else "−") + won(abs(c["after"] - c["before"])), "direction": c["direction"]}
            for c in ranked]
    top = [name(c) for c in ranked if c["rank"] == 1]
    said = f"{' · '.join(top)}{josa(top[-1], ('이', '가'))} 가장 많이 {'늘었어요' if up else '줄었어요'}."
    said += claims._share_tail(ranked[0]["share"], ranked[0]["direction"]) if len(top) == 1 else ""
    title = ("요일별 변화" if by == "weekday" and ranked[0]["metric"] != "day_average" else GROUP_TITLE[by]) \
        + (" (고정지출 제외)" if by in ("time_slot", "weekday") else "")
    return [{"kind": "rows", "title": title, "rows": rows}, {"kind": "text", "lines": [said]}]


TOOLS = {"where_changed": where_changed, "budget_lookup": budget_lookup}

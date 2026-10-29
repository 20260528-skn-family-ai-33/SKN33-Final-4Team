"""라우터 Decision.items → 소비 분석 analyze 연결 어댑터 (독립 실행용 — chat API · replies.py · 대화 상태 DB 미연결)

- run(user, decision, *, today, as_of) → dialog.run_items 결과 [{item, ok, result | error}], 항목 순서 그대로.
  한 항목이 실패해도 다른 결과는 남는다. analyze 가 예외 없이 state=error 를 돌려줘도 그 항목은 실패(ok=False)다.
- record(store, conversation_id, decision, results) → record_analysis 에 JSON 저장형 요약을 넘긴다.
  요청 조건(후속 질문용)은 부분 자료 · 자료 없음 · 승인 예산 없음이어도 남고,
  evidence="confirmed" 는 모든 기간이 complete 이고 확정 수치가 있을 때만 (설명 근거). 전부 실패면 기존 기록 보존.
- 저장 형식: Period → {"from", "to"}(둘 다 포함, chat.md 와 같음) · date → "YYYY-MM-DD" · datetime → ISO 8601(시간대 포함)
  · Decimal → 문자열 "25.83"(float 로 바꾸지 않는다) · UUID → 문자열 · 금액은 원 정수 그대로
"""
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal


from .analyze import analyze
from .dialog import record_analysis, run_items
from .periods import Period


class AnalysisFailed(Exception):
    """analyze 가 state=error 로 끝난 항목 (판정 · 조회의 DB 실패)"""


def run(user, decision, *, today, as_of):
    def fetch(item):
        got = analyze(user, item, today=today, as_of=as_of)
        if got["state"] == "error":
            raise AnalysisFailed(got["reason"])
        return got
    return run_items(decision.items, fetch)


def jsonable(value):
    if isinstance(value, Period):  # NamedTuple 이라 tuple 보다 먼저
        return {"from": value.start.isoformat(), "to": value.last_day.isoformat()}
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, uuid.UUID)):
        return str(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def period_from_json(value):
    return Period(date.fromisoformat(value["from"]), date.fromisoformat(value["to"]) + timedelta(days=1))


def _limits(role, block):
    state = block["coverage"]["state"]
    return [] if state == "complete" else [f"{role}:{state}"]


def summarize(result):
    """analyze 결과 → 기록 요약 {evidence, limits, period, compare_period?, as_of, facts?}. facts 는 확정 수치만"""
    kind, limits, compare_period, facts = result["type"], [], None, None
    if kind in (1, 2, 5):
        block = result["current"]
        period, limits = block["period"], _limits("current", block)
        facts = block["confirmed"]
    elif kind in (3, 4):
        period = result.get("spent_period")
        if result["budget"]["state"] != "approved":
            limits.append(f"budget:{result['budget']['state']}")
        if "coverage" in result and result["coverage"]["state"] != "complete":
            limits.append(f"current:{result['coverage']['state']}")
        if result.get("confirmed"):
            facts = {**result["confirmed"], "budget_id": result["budget"]["budget_id"],
                     "version_no": result["budget"]["version_no"], "cycle_period": result["cycle_period"]}
    elif kind in (6, 7):
        period, compare_period = result["current"]["period"], result["previous"]["period"]
        limits = _limits("current", result["current"]) + _limits("previous", result["previous"])
        if not result["comparison"]["total"]["available"]:
            limits.append("comparison_unavailable")
        else:
            facts = {"current": result["current"]["confirmed"], "previous": result["previous"]["confirmed"],
                     "comparison": result["comparison"]}
    else:
        months = result["months"]
        period = Period(months[0]["period"].start, months[-1]["period"].end_before)
        for m in months:
            limits += _limits(f"month:{m['period'].start:%Y-%m}", m)
        if not limits:
            facts = {"series": result["series"], "stats": result["stats"]}
    out = {"evidence": "confirmed" if facts is not None and not limits else "limited", "limits": limits,
           "period": period, "as_of": result["as_of"]}
    if compare_period:
        out["compare_period"] = compare_period
    if out["evidence"] == "confirmed":
        out["facts"] = facts
    return jsonable(out)


def record(store, conversation_id, decision, results):
    record_analysis(store, conversation_id, decision,
                    [{**r, "result": summarize(r["result"])} if r["ok"] else r for r in results])

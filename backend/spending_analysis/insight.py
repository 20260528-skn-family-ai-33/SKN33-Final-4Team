"""DB 집계와 설명 후보 검증 → 질문 중심 서술 → 근거 대조. 원거래는 전송하지 않는다."""
import calendar
import re
from datetime import date, timedelta
from statistics import median

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import DatabaseError

from budgets.scope import OUT_OF_BUDGET
from . import benchmark, budget_habits, followups, lifestyle, narrative, personality, reasoning, result as R, scope, snapshot, story, tools, trend
from .answer import NAMES, period_comparison_bars, span, won
from .coverage import coverage
from .periods import Period, previous_month_same_days, this_month

STAGES = ("diagnosis", "evidence", "outlook")
TITLES = ("핵심 진단", "분석 근거", "앞으로의 의미")
PROMPT, SCHEMA = narrative.PROMPT, narrative.SCHEMA


def eligible(decision):
    request = decision.request or decision.interpretation
    items = decision.items or (decision.explain or {}).get("items", ())
    return bool(request and decision.status == "ready" and items and request.hypothesis is None
                and request.target != "budget_left" and (request.target == "overview" or request.explain or request.analysis_goal)
                and request.period in (None, "this_month") and request.compare in (None, "prev_month", "category")
                and request.measure is None and request.group_by is None
                and all(i["type"] in (1, 2, 5, 6, 7) and not i.get("merchant") and not i.get("instead_of")
                        and not i.get("compare_month") for i in items))


def _metrics(rows):
    paid = [r for r in rows if r["net_amount"] > 0]
    total = sum(r["net_amount"] for r in rows)
    largest = max((r["net_amount"] for r in paid), default=0)
    avg = R.average(sum(r["net_amount"] for r in paid), len(paid))
    return {"amount": total, "count": len(paid), "days": len({r["occurred_on"] for r in paid}), "average": avg and avg["value"],
            "largest_payment": largest, "without_largest": total - largest}


def _timing(rows, period):
    days = [period.start + timedelta(days=n) for n in range((period.end_before - period.start).days)]
    grouped = {}
    for key, weekdays in scope.DAY_GROUPS.items():
        count = sum(d.weekday() in weekdays for d in days)
        amount = sum(r["net_amount"] for r in rows if r["occurred_on"].weekday() in weekdays)
        grouped[key] = R.average(amount, count)["value"] if count else None
    slots = {k: sum(r["net_amount"] for r in rows if scope.slot_of(r["occurred_time"]) == k) for k in scope.SLOTS}
    return {"daily_average": grouped, "time_slots": slots,
            "time_slot_days": {k: len({r["occurred_on"] for r in rows if r["net_amount"] > 0
                                      and scope.slot_of(r["occurred_time"]) == k}) for k in scope.SLOTS},
            "missing_time_count": sum(r["occurred_time"] is None for r in rows)}


def covered_through(user, today):
    """이번 달 1일부터 빈틈없이 수집된 마지막 날. 최근 며칠만 아직 안 불러온 경우 그날까지를 '완전한 이번 달'로 본다.
    모든 대상(계좌 · 카드)이 1일부터 이어서 수집됐을 때만 — 중간에 빈 날 · 판정할 수 없는 원천이 있으면 None"""
    period = this_month(today)
    found = coverage(user, period)
    if found["state"] != "partial" or found.get("undetermined") or not found.get("targets"):
        return None
    ends = []
    for target in found["targets"]:
        first = target["covered"][0] if target["covered"] else None
        if first is None or first.start != period.start:
            return None
        ends.append(first.end_before)
    last = min(ends) - timedelta(days=1)
    return last if period.start + timedelta(days=2) <= last < today else None


def _coverage_note(found, period):
    label = span(period)
    if found["state"] == "error":
        return f"{label} 거래의 조회 완료 여부를 확인하지 못했어요."
    missing = sorted({p for t in found.get("targets", []) for p in t.get("missing", [])})
    if missing:
        dates = ", ".join(span(p) for p in missing[:2])
        return f"일부 계좌·카드의 {dates}{' 등' if len(missing) > 2 else ''} 거래를 빠짐없이 불러왔는지 확인되지 않았어요."
    if found.get("undetermined"):
        return f"직접 등록한 카드·거래 등이 있어 {label}의 전체 소비가 빠짐없이 반영됐는지 확인할 수 없어요."
    return f"{label}의 전체 계좌·카드 내역이 빠짐없이 반영됐는지 확인할 수 없어요."


def summarize(user, decision, today):
    request = decision.request or decision.interpretation
    items = decision.items or decision.explain["items"]
    where = items[0]
    asked_today, clipped = today, None
    try:
        clipped = covered_through(user, today)
    except DatabaseError:
        clipped = None
    if clipped:  # 최근 며칠만 아직 안 불러왔다 → 그 전날까지로 분석 기간을 맞춘다 (비교 · 전망이 '일부 내역'으로 막히지 않게)
        today = clipped
    periods = (this_month(today), previous_month_same_days(today))
    collected = [coverage(user, p) for p in periods]
    states = [c["state"] for c in collected]
    comparison_available = states == ["complete", "complete"]
    incomplete = 0 if states[0] != "complete" else 1
    comparison_reason = None if comparison_available else (
        _coverage_note(collected[incomplete], periods[incomplete]) + " 지난달과의 총액 비교는 제외했어요.")
    living = request.analysis_scope == "living"
    source = [[r for r in tools.rows(user, p, {**where, "include_fixed": True}, include_activity=True)
               if r["transaction_type"] == "spending"] for p in periods]
    scoped = [[r for r in side if r["funding"] != "fixed"
               and (not living or r["category"] not in OUT_OF_BUDGET)] for side in source]
    # '확인 필요'(분류 전) 결제는 무엇에 쓴 돈인지 몰라 분석 대상에서 뺀다 (11/13 사용자 결정). 금액은 하단에만 알린다
    unclassified_amount = [sum(r["net_amount"] for r in side if r["category"] == "UNCLASSIFIED") for side in scoped]
    rows = [[r for r in side if r["category"] != "UNCLASSIFIED"] for side in scoped]
    requested = [i.get("category") for i in items if i.get("category")]
    if requested:
        rows = [[r for r in side if r["category"] in requested] for side in rows]
    current, previous = (_metrics(side) for side in rows)
    codes = set(r["category"] for side in rows for r in side if r["category"] in NAMES and r["category"] != "UNCLASSIFIED")
    category_rows = []
    for code in codes:
        sides = [[r for r in side if r["category"] == code] for side in rows]
        now, before = (_metrics(side) for side in sides)
        (large, regular), (prior_large, prior_regular) = trend.split_large(sides[0], sides[1] if comparison_available else [])
        category_rows.append({"code": code, "name": NAMES[code], "current": now, "previous": before,
                              "delta": now["amount"] - before["amount"],
                              "large": [_metrics(large), _metrics(prior_large)],
                              "regular": [_metrics(regular), _metrics(prior_regular)],
                              "timing": [_timing(side, p) for side, p in zip((regular, prior_regular), periods)],
                              "activities": lifestyle.summarize((regular, prior_regular), _metrics)})
    selected = category_rows
    label = "·".join(NAMES[c] for c in requested) if requested else "생활비" if living else "변동 소비"
    if requested and living:
        label = "생활비 중 " + label
    if where.get("payment"):
        label += " 카드 결제" if where["payment"] == "card" else " 계좌 결제"
    if scope.filter_label(where):
        label += " · " + scope.filter_label(where)
    summary = {"basis": {"scope": label, "categories": requested, "fixed_excluded": True,
                          "excluded_categories": list(OUT_OF_BUDGET) if living else [],
                          "filters": {k: where[k] for k in scope.FILTERS if k in where and k not in ("merchant", "merchant_names")},
                          "current": {"from": periods[0].start.isoformat(), "to": periods[0].last_day.isoformat()},
                          "previous": {"from": periods[1].start.isoformat(), "to": periods[1].last_day.isoformat()},
                          "coverage": states[0], "comparison_available": comparison_available,
                          "as_of": today.isoformat(), "asked_today": asked_today.isoformat(), "clipped": bool(clipped),
                          "observed_through": max((r['occurred_on'].isoformat() for r in source[0]), default=None),
                          "comparison_reason": comparison_reason,
                          "count_basis": "취소 차감 후 순금액이 양수인 소비 결제"},
               "current": current, "previous": previous, "categories": selected,
               "observed_count": _metrics(rows[0] if requested else scoped[0])["count"],
               "user_context": {"activity": request.analysis_activity, "planned": request.analysis_planned},
               "unclassified_amount": unclassified_amount}
    months = trend.history_months(today)
    history, complete_months, complete, source_history = [], [], False, []
    try:
        complete_months = [p for p in months if coverage(user, p)["state"] == "complete"]
        observed = Period(today - timedelta(days=27), today + timedelta(days=1))
        observed_coverage = coverage(user, observed)
        complete = observed_coverage["state"] == "complete" and states[0] == "complete"
        windows = {28: complete}
        window_notes = {28: _coverage_note(observed_coverage, observed) if not complete else ""}

        def checked(projection, current_rows, past_rows):
            if projection["state"] != "ready":
                return projection
            days = projection["validation"].get("selected", {}).get("lookback_days", 28)
            if days not in windows:
                window = Period(today - timedelta(days=days - 1), today + timedelta(days=1))
                found = coverage(user, window)
                windows[days] = found["state"] == "complete"
                window_notes[days] = _coverage_note(found, window) if not windows[days] else ""
            if not windows[days]:
                return {**{k: v for k, v in projection.items() if k not in ("month_end", "remaining", "observed", "model")},
                        "state": "unavailable", "reason": window_notes[days] + f" 예측에 필요한 최근 {days}일의 소비 합계를 확인할 수 없어 월말 예상액은 보류했어요."}
            continuity = reasoning.persistence(current_rows, past_rows, today, complete_months, complete)
            if continuity["state"] == "mixed" and continuity["weeks"][-1] > continuity["baseline_range"][1]:
                return {**{k: v for k, v in projection.items() if k not in ("month_end", "remaining", "observed", "model")},
                        "state": "unavailable", "reason": "최근 한 주의 증가가 이어지는지 확인되지 않아 그 수준을 월말까지 연장한 예상액은 보류했어요."}
            if continuity["state"] == "reverted":
                (_, regular), (_, past_regular) = trend.split_large(current_rows, past_rows)
                fitted = trend._weekly([*past_regular, *regular], today, days)
                future_week = trend._remaining(fitted, projection["validation"]["selected"], today, today + timedelta(days=7))
                if future_week > continuity["baseline_range"][1]:
                    return {**{k: v for k, v in projection.items() if k not in ("month_end", "remaining", "observed", "model")},
                            "state": "unavailable", "reason": "급증 후 최근 소비가 이전 범위로 돌아왔어요. 선택한 방식이 급증의 영향을 미래에도 반영해 월말 예상액은 보류했어요."}
            return projection

        source_history = [r for r in tools.rows(user, Period(months[0].start, periods[0].start),
                          {**where, "include_fixed": True}, include_activity=True) if r["transaction_type"] == "spending"]
        history = [r for r in source_history if r["funding"] != "fixed" and r["category"] != "UNCLASSIFIED"
                   and (not living or r["category"] not in OUT_OF_BUDGET) and (not requested or r["category"] in requested)]
        for cat in selected if comparison_available else []:
            current_rows = [r for r in rows[0] if r["category"] == cat["code"]]
            past_rows = [r for r in history if r["category"] == cat["code"]]
            (large, regular), (past_large, past_regular) = trend.split_large(current_rows, past_rows)
            prior_large = [r for r in past_large if periods[1].start <= r["occurred_on"] < periods[1].end_before]
            prior_regular = [r for r in past_regular if periods[1].start <= r["occurred_on"] < periods[1].end_before]
            cat["large"], cat["regular"] = [_metrics(large), _metrics(prior_large)], [_metrics(regular), _metrics(prior_regular)]
            cat["timing"] = [_timing(side, p) for side, p in zip((regular, prior_regular), periods)]
            cat["activities"] = lifestyle.summarize((regular, prior_regular), _metrics)
        summary["trends"] = {code: checked(value, [r for r in rows[0] if r["category"] == code], [r for r in history if r["category"] == code]) for code, value in trend.summarize(rows[0], history, today, complete_months, complete=complete).items()}
        summary["overall_trend"] = checked(trend.forecast(rows[0], history, today, complete=complete, months=complete_months), rows[0], history)
        if states[0] != "complete":
            summary["forecast_reason"] = _coverage_note(collected[0], periods[0]) + " 이 내역을 한 달 전체로 늘려 계산하면 실제보다 적게 예상할 수 있어 월말 예상액은 보류했어요."
        elif observed_coverage["state"] != "complete":
            summary["forecast_reason"] = window_notes[28] + " 예측에 필요한 최근 4주의 소비 합계를 확인할 수 없어 월말 예상액은 보류했어요."
        summary["monthly_totals"] = [{"month": p.start.isoformat()[:7], "amount": sum(r["net_amount"] for r in history if p.start <= r["occurred_on"] < p.end_before)}
                                     for p in complete_months[-3:]]
        if complete_months:
            reference = complete_months[-1]
            reference_rows = [r for r in source_history if reference.start <= r["occurred_on"] < reference.end_before
                              and not (r["funding"] == "fixed" and r["category"] in ("HOUSING_TELECOM", "ETC"))]
            summary["reference_month"] = {"label": f"{reference.start.month}월 전체",
                "unclassified": sum(r["net_amount"] for r in reference_rows if r["category"] == "UNCLASSIFIED"), "categories": {
                code: sum(r["net_amount"] for r in reference_rows if r["category"] == code)
                for code in NAMES if code != "UNCLASSIFIED"}}
        if not requested and not any(where.get(k) for k in scope.FILTERS):
            all_current = [r for r in source[0] if r["funding"] != "fixed" and r["category"] != "UNCLASSIFIED"]
            all_history = [r for r in source_history if r["funding"] != "fixed" and r["category"] != "UNCLASSIFIED"]
            projection = (summary["overall_trend"] if all_current == rows[0] and all_history == history else
                          checked(trend.forecast(all_current, all_history, today, complete=complete, months=complete_months), all_current, all_history))
            total = {"state": projection["state"], "reason": projection.get("reason")}
            if projection["state"] == "ready":
                try:
                    fixed = tools.fixed_forecast(user, periods[0], source[0])
                except (DatabaseError, tools.DbError):
                    total = {"state": "unavailable", "reason": "등록 고정지출의 남은 금액을 불러오지 못해 총지출 대신 변동 소비 전망을 보여드려요."}
                else:
                    actual = sum(r["net_amount"] for r in source[0])
                    total.update(actual=actual, remaining_variable=projection["remaining"], fixed_paid=fixed["paid"],
                                 fixed_remaining=fixed["remaining"],
                                 month_end=round((actual + projection["remaining"] + fixed["remaining"]) / 1000) * 1000,
                                 variable_validation=projection["validation"],
                                 monthly_totals=[{"month": p.start.isoformat()[:7], "amount": sum(r["net_amount"] for r in source_history if p.start <= r["occurred_on"] < p.end_before)}
                                                 for p in complete_months[-3:]])
            summary["total_forecast"] = total
    except (DatabaseError, tools.DbError):
        summary["trends"] = {}
        summary["forecast_reason"] = "과거 소비 추이 자료를 불러오지 못해 월말 예상액은 계산하지 않았어요."
    summary.update(reasoning.analyze(rows[0], rows[1], history, periods, complete_months, comparable=comparison_available, window_complete=complete))
    # 이번 달 내역만으로 하는 분석 — 과거 자료 · 지난달 비교가 없어도 나온다
    budget = None
    if not any(where.get(k) for k in scope.FILTERS):
        try:
            budget = tools.budget_left(user, None, today, breakdown=True)
        except (DatabaseError, tools.DbError, ValueError, ValidationError):  # 예산 없이도 나머지 분석은 그대로
            budget = None
    month_end_before = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
    whole = not requested and not any(where.get(k) for k in scope.FILTERS)
    summary["snapshot"] = [
        *snapshot.budget_pace(budget, today, requested or None, rows[0]),
        *(snapshot.change_total(rows[0], rows[1], [r for r in history if (r["occurred_on"].year, r["occurred_on"].month) == (periods[1].start.year, periods[1].start.month)],
                                summary["total_material"]) if comparison_available else []),
        # 분류 전 결제도 이번 달에 쓴 돈이다 — 큰 결제의 몫을 말할 때는 함께 본다(항목별 분석에서는 뺀다)
        *snapshot.large_share(rows[0] if requested else scoped[0], history),
        *snapshot.frequent(rows[0], today, periods[0].start, month_end_before),
        *(snapshot.weekend(rows[0], periods[0].start, today) if whole else []),
        *(snapshot.composition(rows[0]) if whole else []),
    ]
    summary["observation"] = snapshot.observation(rows[0] if requested else scoped[0])
    # 소비 추이 → 습관 → 앞으로 (11/13 확정 구성). 카테고리 · 결제수단 등으로 좁힌 질문은 기존 분석만
    summary["story"] = None
    if not any(where.get(k) for k in scope.FILTERS):
        try:
            summary["story"] = story.build([r for r in source[0] if r["funding"] != "fixed"], [r for r in source_history if r["funding"] != "fixed"],
                                           complete_months, today, complete=complete, budget=budget, categories=requested or None)
        except (DatabaseError, tools.DbError):
            summary["story"] = None
    return summary, None


def chart(summary, today):
    changed = [code for code, t in (summary.get("trends") or {}).items()
               if code in NAMES and t.get("weekly_direction") in ("increase", "decrease")]
    codes = (changed or list(dict.fromkeys(code for f in summary.get("findings", []) for code in
                 ([f["category"]] if f.get("category") else f.get("categories", [])))))[:2]
    choices = [(code, NAMES[code], summary.get("trends", {}).get(code, {})) for code in codes]
    if not choices:
        choices = [("overall", summary.get("basis", {}).get("scope", "생활비"), summary.get("overall_trend", {}))]
    series = []
    for code, name, projection in choices:
        weeks = projection.get("weeks", [])
        if len(weeks) != 4 or not any(w["amount"] for w in weeks):
            continue
        points = []
        for i, week in enumerate(weeks):
            first = today - timedelta(days=27 - i * 7)
            last = first + timedelta(days=6)
            points.append({"label": f"{first.month}/{first.day}~{last.month}/{last.day}", "amount": week["amount"]})
        series.append({"code": code, "name": name, "points": points})
    if not series:
        return None
    return {"kind": "trend", "title": "최근 4주 소비 추이", "series": series,
            "basis": "관측된 결제 금액 · 큰 결제·고정지출·환불 입금 제외"}


def _outlook(summary):
    """→ (월말 전망 문장 | None, 하단 안내). 예측이 검증되지 않으면 본문 문장은 만들지 않고 이유만 하단에 한 줄"""
    parts, notes = [], []
    overall = summary.get("overall_trend", {})
    if overall.get("state") != "ready":
        reason = summary.get("forecast_reason") or overall.get("reason") or "월말 예상액을 검증할 과거 자료가 부족해 이번에는 빼고 분석했어요."
        if "오차" in reason:  # 검증 결과(오차)는 하단에서 확인할 수 있게 남긴다 (요구사항 7)
            return None, ["월말 전망: 과거 소비로 시험해 보니 예측이 충분히 맞지 않아 월말 예상액은 이번에는 빼고 분석했어요.",
                          f"예측 검증 결과: {reason}"]
        return None, [f"월말 전망: {reason}"]
    report = overall["validation"]
    selected = report["selected"]
    parts.append(f"지금 흐름이면 월말 {summary['basis']['scope']}는 약 {won(overall['month_end'])}이 될 거예요.")
    amounts = [r['amount'] for r in summary.get('monthly_totals', [])]
    if len(amounts) >= 3:
        usual = round(median(amounts))
        difference = overall['month_end'] - usual
        if reasoning.material(difference, amounts, usual)['meaningful']:
            parts.append(f"최근 {len(amounts)}개월 보통 수준({snapshot.short(usual)})보다 약 {snapshot.short(abs(difference))} {'많아요' if difference > 0 else '적어요'}.")
        else:
            parts.append(f"최근 {len(amounts)}개월과 비슷한 수준이에요.")
    total = summary.get("total_forecast", {})
    if total.get("state") == "ready":
        parts.append(f"고정지출까지 더하면 약 {won(total['month_end'])}이에요.")
        notes.append(f"총지출 = 지금까지 쓴 돈 {won(total['actual'])}(낸 고정지출 {won(total['fixed_paid'])} 포함) + 남은 변동 소비 예상 {won(round(total['remaining_variable'], -3))} + 아직 안 낸 고정지출 {won(total['fixed_remaining'])}")
    notes.append(f"월말 전망 방식: 과거 소비로 여러 방식을 시험해 {trend.MODEL_NAMES[selected['model']]}(최근 {selected['lookback_days']}일 반영)을 골랐어요. "
                 f"따로 떼어 둔 {len(report['test_months'])}개월에서 실제 월말과 평균 {won(round(report['test_mae'], -3))} 차이가 났어요. 앞으로 생길 큰 결제는 포함하지 않아요.")
    return " ".join(parts), notes


# 발견 종류별 기본 순서 (작은 수가 먼저). 질문 의도마다 무엇을 먼저 보여 줄지 다르다
ORDER = {
    "overview": ("budget_fast", "change_total", "change", "large_share", "frequent", "pattern", "weekend", "composition", "budget_ok"),
    "reason": ("total", "change_total", "large_share", "change", "budget_fast", "frequent", "pattern", "weekend", "composition", "budget_ok"),
    "habit": ("change", "frequent", "pattern", "weekend", "large_share", "budget_fast", "composition", "budget_ok"),
    "forecast": ("budget_fast", "budget_ok", "change_total", "change", "large_share", "frequent", "pattern", "weekend", "composition"),
}
SHOW = 3


def _group(f):
    if f.get("snapshot"):
        return f["kind"]
    if f["kind"] == "total":
        return "total"
    if f["kind"] == "pattern":
        return "pattern"
    return "change"


def _pattern_findings(summary):
    """이번 달 활동 · 시간대 쏠림 (reasoning.observed_pattern). 자주 하는 소비(snapshot.frequent)와 같은 활동이면 뺀다"""
    taken = {f.get("activity") for f in summary.get("snapshot", [])}
    out = []
    for c in summary.get("candidates", []):
        pattern = c.get("pattern")
        if not pattern or pattern["activity"] in taken or reasoning.HEDGE.search(pattern["conclusion"]):
            continue
        out.append(dict(id=f"P{c['id']}", kind="pattern", section="생활 패턴", category=c.get("category"), subject=c.get("subject"),
                        impact=pattern["amount"], comparable=False, conclusion=pattern["conclusion"], evidence=[pattern["evidence"]],
                        outlook="", limits=[], pattern=pattern))
    return out


def select(summary, goal):
    changes = [f for f in summary.get("findings", []) if f.get("comparable") and not f.get("quiet")]
    if goal != "habit" and any(f["kind"] == "change_total" for f in summary.get("snapshot", [])):
        # 지난달 대비 전체 요약이 이미 카테고리별 차이를 말한다 — 같은 말을 되풀이하지 않게, 흐름이 바뀐 신호가 있는 것만 남긴다
        changes = [f for f in changes if f.get("persistence", {}).get("state") == "sustained" or f["kind"] in ("type_mix", "type_offset", "offset")]
    pool = [*summary.get("snapshot", []), *changes, *_pattern_findings(summary)]
    if goal == "reason" and summary["basis"]["comparison_available"]:
        delta = summary["current"]["amount"] - summary["previous"]["amount"]
        if delta <= 0 or not summary["total_material"]["meaningful"]:
            scope_name = summary["basis"]["scope"]
            conclusion = (f"{scope_name}는 지난달 같은 기간보다 오히려 {won(abs(delta))} 적어요." if delta < 0 and summary["total_material"]["meaningful"] else
                          f"{scope_name}는 지난달 같은 기간({won(summary['previous']['amount'])})과 비슷한 수준이에요.")
            pool.append(dict(id="TOTAL", category=None, kind="total", section="지난달과 비교", impact=abs(delta), comparable=True,
                             conclusion=conclusion, evidence=[f"이번 달 {won(summary['current']['amount'])}, 지난달 같은 기간 {won(summary['previous']['amount'])}이에요."],
                             outlook="", limits=[]))
    order = ORDER.get(goal, ORDER["overview"])
    ranked = sorted(pool, key=lambda f: (order.index(_group(f)) if _group(f) in order else len(order), -f.get("impact", 0), f["id"]))
    limit = 2 if goal == "forecast" else SHOW  # 전망 질문은 전망 + 그 흐름을 만든 발견 하나
    chosen, seen = [], set()
    for f in ranked:
        key = (f.get("category"), _group(f))
        if key in seen and f.get("category"):
            continue
        if f["kind"] == "budget_ok" and len(chosen) >= 2:  # 예산 속도가 평범하면 다른 발견이 모자랄 때만
            continue
        seen.add(key)
        chosen.append(f)
        if len(chosen) == limit:
            break
    return chosen or summary.get("observation", [])


def _sections(findings, statements, forecast_text):
    """(제목, 줄들) 목록. statements = [(문장, finding id 목록)] — 같은 소제목은 합친다"""
    by_id = {f["id"]: f for f in findings}
    sections = {}
    for text, ids in statements:
        title = "월말 전망" if ids and ids[0] == "FORECAST" else by_id.get(ids[0], {}).get("section", "분석 근거") if ids else "분석 근거"
        sections.setdefault(title, [])
        if text not in sections[title]:
            sections[title].append(text)
    if forecast_text and "월말 전망" not in sections:
        sections["월말 전망"] = [forecast_text]
    return sections


# ---------- 분석 메뉴 · 계산 방법 (11/13) ----------
# '소비 분석해줘'처럼 무엇을 볼지 정하지 않은 말 · '또 뭐 분석해 줄 수 있어?' → 볼 수 있는 분석과 설명 (같은 분석을 다시 내보내지 않는다)
# '왜 이렇게 계산됐어?' → 방금 보인 숫자의 계산식 (다른 분석으로 바꿔 답하지 않는다)
MENU = (
    ("전체 분석", "최근 몇 달간의 생활비 흐름과 매달 반복되는 소비를 살펴보고, 지금처럼 소비할 때 이번 달 말과 다음 달의 예상 지출까지 한눈에 확인해요.",
     "전체적으로 분석해줘"),
    ("소비 습관", "외식·카페·배달처럼 매달 반복되는 소비가 생활비에서 얼마나 되는지, 줄이면 한 달에 얼마가 남는지 짚어요.",
     "내 소비 습관 어때?"),
    ("이번 달 말·다음 달 예상", "지금 쓰는 속도가 이어지면 이번 달 말과 다음 달에 얼마가 되는지, 예산을 넘을 항목이 있는지 봐요.",
     "이대로 쓰면 이번 달 얼마나 쓸까?"),
    ("지난달과 비교", "지난달 같은 기간보다 더 쓰거나 덜 쓴 이유를 항목별로 찾아요.", "지난달과 달라진 소비는 뭐야?"),
)
_MENU = re.compile(r"(뭐|뭘|무엇|무슨|어떤)\s*(걸|것을?|을|를)?\s*(더\s*|또\s*)?(분석|볼\s*수|알\s*수)"
                   r"|분석\s*(으로|에서|은|는)?\s*(뭐|뭘|무엇|어떤\s*(걸|것))"
                   r"|분석\s*(종류|옵션|메뉴|목록|기능)"
                   r"|(할|해\s*줄|볼)\s*수\s*있는\s*(분석|거|것)")
_BARE = re.compile(r"^(내\s*)?(소비|지출|소비\s*패턴|돈)?\s*(을|를)?\s*분석\s*(좀\s*)?(해\s*줘|해\s*주세요|해\s*줄래|해\s*봐|부탁해?|부탁드려요|하기|시작)?\s*[.!?~ㅎㅋ]*$")
_CALC = re.compile(r"계산(된|됐|되|한|했|하신)|계산\s*(방식|방법|과정|근거|식)|어떻게\s*계산|산출|추정"
                   r"|어떻게\s*(나온|나왔|나와|구한|구했|예측|예상|정한|잡은|알아)"
                   r"|(무슨|어떤)\s*기준|기준이\s*(뭐|뭔)|근거가\s*(뭐|뭔)|근거\s*알려"
                   r"|\d[\d.,]*\s*만?\s*원?\s*(은|는|이|가)?\s*(왜|어떻게)")


def wants_menu(question):
    text = (question or "").strip()
    return bool(text) and bool(_BARE.match(text) or _MENU.search(text))


def wants_calculation(question):
    return bool(_CALC.search(question or ""))


def menu_reply(lead="어떤 분석을 볼까요? 누르거나 편하게 물어보세요."):
    items = [{"title": t, "desc": d, "ask": a} for t, d, a in MENU]
    tip = "항목이나 기간을 콕 집어 물어도 돼요. 예: '이번 달 식비 자세히 보여줘', '지난 3개월 카페 얼마 썼어?'"
    return {"lines": ["어떤 분석을 볼까요?", *(f"{t} — {d}" for t, d, _ in MENU), tip], "lead": [], "notices": [],
            "view": [{"kind": "head", "title": "소비 분석 · 볼 수 있는 분석"},
                     {"kind": "text", "lines": [lead]},
                     {"kind": "options", "items": items},
                     {"kind": "text", "lines": [tip]}],
            "examples": []}


def _calculation_reply(summary, today, suffix):
    detail = story.calculation(summary["story"], today)
    if not detail:
        return None
    blocks = [{"kind": "head", "title": f"{span(this_month(today))} · 계산 방법{suffix}"},
              {"kind": "text", "title": "계산 요약", "lines": ["계산 요약", *detail["lead"]], "tone": "key"}]
    if detail["lines"]:
        blocks.append({"kind": "text", "lines": detail["lines"]})
    if detail["rows"]:
        blocks.append({"kind": "rows", "title": "항목별 이번 달 말 예상", "rows": detail["rows"]})
    basis = summary["basis"]
    notes = list(detail["notes"])
    if basis.get("comparison_available"):  # 지난달 비교 숫자를 봤을 수 있어 기간 기준도 하단에
        cur, prev = basis["current"], basis["previous"]
        end = date.fromisoformat(basis["as_of"]).day
        notes.append(f"지난달 비교는 같은 날짜까지만 봐요 ({_md(cur['from'])}~{end}일 vs "
                     f"{_md(prev['from'])}~{date.fromisoformat(_same_day(prev['from'], basis['as_of'])).day}일).")
    if _unclassified_note(summary):
        notes.append(_unclassified_note(summary))
    lines = [*detail["lead"], *detail["lines"], *(f"{r['name']} {r['value']}" + (f" ({r['note']})" if r["note"] else "") for r in detail["rows"])]
    return {"lines": lines, "lead": [], "notices": notes, "view": blocks,
            "examples": ["이대로 쓰면 이번 달 얼마나 쓸까?", "소비 분석으로 뭘 볼 수 있어?"]}


def _md(iso):
    d = date.fromisoformat(iso)
    return f"{d.month}월 {d.day}일"


def _same_day(start_iso, as_of_iso):
    start, as_of = date.fromisoformat(start_iso), date.fromisoformat(as_of_iso)
    last = calendar.monthrange(start.year, start.month)[1]
    return start.replace(day=min(as_of.day, last)).isoformat()


def answered_topic(decision, question=None):
    """이 답이 다룬 분석 종류 (추천 버튼 중복 방지용). 전체 분석 · 모르는 것은 None"""
    if wants_calculation(question):
        return "calculation"
    request = decision.request or decision.interpretation
    goal = getattr(request, "analysis_goal", None) or ("reason" if getattr(request, "explain", None) else "overview")
    return goal if goal in ("habit", "reason", "forecast") else None


def answer(user, decision, today, complete, question=None, covered=()):
    asked = question
    if wants_menu(asked):  # 무엇을 볼지 고르기 전 — DB 를 읽지 않고 메뉴만
        return menu_reply(), None
    try:
        summary, reason = summarize(user, decision, today)
    except (DatabaseError, tools.DbError):
        return None, "소비 패턴을 확인할 자료를 불러오지 못했어요. 잠시 후 다시 분석해 주세요."
    if summary is None:
        return None, reason
    basis = summary["basis"]
    today = date.fromisoformat(basis["as_of"]) if basis.get("as_of") else today
    suffix = " · 일부 내역 기준" if basis["coverage"] != "complete" else ""
    observed_day = date.fromisoformat(basis['observed_through']) if basis.get('observed_through') else None
    observed_note = (f"{observed_day.month}월 {observed_day.day}일까지 저장된 거래로 분석했어요. 이후 거래나 누락 내역이 추가되면 달라질 수 있어요."
                     if observed_day and basis['coverage'] != 'complete' else None)
    request = decision.request or decision.interpretation
    question = question or (decision.explain or {}).get("question") or "소비 분석해줘"
    goal = getattr(request, "analysis_goal", None) or ("reason" if request.explain else "overview")
    comparison = []
    if goal == "overview" and len(basis.get("categories", ())) == 1 and basis["comparison_available"]:
        comparison.append(period_comparison_bars(
            summary["current"]["amount"], summary["previous"]["amount"], this_month(today), previous_month_same_days(today),
            title=f"{basis['scope']} · 같은 기간 비교"))
    if wants_calculation(asked) and summary.get("story"):
        reply = _calculation_reply(summary, today, suffix)
        if reply is not None:
            if observed_note:
                reply['notices'] = [observed_note, *reply.get('notices', [])]
            return followups.apply(reply, question, complete, **_followup_context(summary, "calculation", reply, today), covered=covered), None
    if summary.get("story") and (summary["story"]["findings"] or summary["story"].get("slot") or summary["story"].get("weekend")):
        reply = _story_answer(user, summary, goal, today, suffix)
        reply["view"][2:2] = comparison
        if observed_note:
            reply['notices'] = [observed_note, *reply.get('notices', [])]
        return followups.apply(reply, question, complete, **_followup_context(summary, goal, reply, today), covered=covered), None
    findings = select(summary, goal)
    forecast, notes = _outlook(summary)
    if forecast and goal in ("reason", "habit"):  # 이유 · 습관 질문에는 전망을 붙이지 않는다 (방식 안내만 하단에서 뺀다)
        forecast, notes = None, [n for n in notes if not n.startswith(("월말 전망 방식", "총지출 ="))]
    elif forecast and goal == "overview":  # 전반 분석에는 전망 한 문장만
        forecast = re.split(r"(?<=[.!?])\s+", forecast)[0]
    if not findings and not forecast:
        current = summary["current"]
        line = (f"저장된 내역에서 확인한 소비는 {won(current['amount'])}, {current['count']}건이에요."
                if current["count"] else "이번 달에 분석할 소비 내역이 아직 없어요.")
        if not current["count"] and summary["unclassified_amount"][0]:
            line = "결제 내역은 있지만, 사용처가 정해진 내역이 없어 소비 성향은 아직 구분할 수 없어요."
        lines = [line]
        categories = [row for row in summary['categories'] if row['current']['amount'] > 0]
        if categories:
            top = max(categories, key=lambda row: row['current']['amount'])
            lines.append(f"이 내역에서는 {top['name']}에 {won(top['current']['amount'])}을 써 가장 큰 비중을 차지해요.")
        elif not current['count'] and summary['previous']['count']:
            lines.append(f"지난달 같은 기간에 저장된 소비는 {won(summary['previous']['amount'])}, {summary['previous']['count']}건이에요.")
        notes = [n for n in (observed_note, *notes, basis.get("comparison_reason"), _unclassified_note(summary), _small_sample_note(summary),
                 f"분석 범위: {basis['scope']} · {span(this_month(today))} · 고정지출 제외") if n]
        return {"lines": lines, "lead": [], "notices": notes, "examples": [],
                "view": [{"kind": "head", "title": f"{span(this_month(today))} · 소비 분석{suffix}"},
                         {"kind": "text", "lines": lines}, *comparison]}, None
    data = narrative.payload(question, findings, forecast, goal) if findings else None
    composed = narrative.compose(data, complete) if data else None
    if composed is not None:
        lead, evidence, outlook = composed
        if goal == "reason" and not basis["comparison_available"]:
            lead = [_no_comparison_line(summary), *lead]
        statements = composed_statements(data, evidence, outlook)
        cited = {i for _, ids in statements for i in ids} | set((data.get("_statement_ids") or {}).get("diagnosis", []))
        sections = _sections(findings, statements, None if "FORECAST" in cited else forecast)
    else:
        # 서버 문장: 질문에 맞는 가장 큰 발견의 결론을 먼저, 나머지는 소제목별로 결론 + 근거 한 줄
        statements = []
        sentences = re.split(r"(?<=[.!?])\s+", forecast) if forecast else []
        if goal == "forecast" and sentences:
            lead = sentences[:1]
            statements.extend((text, ["FORECAST"]) for text in sentences[1:])
            for f in findings[:2]:
                statements.extend((text, [f["id"]]) for text in [f["conclusion"], *f["evidence"][:1]])
        else:
            # 습관 질문은 '달라진 점'과 '반복 방식'을 함께 말한다 (요구사항: 확인된 반복 방식 + 최근 달라진 점 연결)
            joined = 2 if goal == "habit" and len(findings) > 1 and findings[0]["section"] != findings[1]["section"] else 1
            lead = [f["conclusion"] for f in findings[:joined]] if findings else sentences[:1]
            if goal == "reason" and not basis["comparison_available"]:
                lead = [_no_comparison_line(summary), *lead]
            for i, f in enumerate(findings):
                lines = ([] if i < joined else [f["conclusion"]]) + f["evidence"][:1 if i else 2]
                if f.get("outlook"):
                    lines.append(f["outlook"])
                statements.extend((text, [f["id"]]) for text in lines)
            if forecast and findings:
                statements.append((forecast, ["FORECAST"]))
        sections = _sections(findings, statements, None)
    # 하단 안내: 짧게, 겹치지 않게 (요구사항 10 · 13)
    if basis.get("clipped"):
        after = today + timedelta(days=1)
        notes.insert(0, f"{after.month}월 {after.day}일 이후 내역은 아직 불러오지 않아 {today.month}월 {today.day}일까지 기준으로 분석했어요.")
    if basis["comparison_reason"]:
        notes.append(basis["comparison_reason"])
    if observed_note:
        notes.insert(0, observed_note)
    notes.extend(dict.fromkeys(t for f in findings for t in f.get("limits", [])))
    if _unclassified_note(summary):
        notes.append(_unclassified_note(summary))
    if _small_sample_note(summary):
        notes.append(_small_sample_note(summary))
    notes.append(f"분석 범위: {basis['scope']} · {span(this_month(today))} · 고정지출 제외")
    reference_lines, reference_notes = benchmark.for_user(user, summary, today) if goal != "reason" else ([], [])
    notes.extend(n for n in reference_notes if n.startswith(("ML 기준은", "남는 돈은")))
    actions = list(dict.fromkeys([*(a for f in findings for a in f.get("actions", [])),
                                  *(lifestyle.suggestions(findings) or summary.get('reference_actions', []))]))[:3]
    blocks = [{"kind": "head", "title": f"{span(this_month(today))} · 소비 분석{suffix}"}]
    blocks.append({"kind": "text", "title": "핵심 진단", "lines": ["핵심 진단", *lead], "tone": "key"})
    blocks.extend(comparison)
    graph = chart(summary, today) if _changing_trend(summary) else None
    visuals = {f["section"]: f["visual"] for f in findings if f.get("visual")}
    for title, lines in sections.items():
        lines = [l for l in dict.fromkeys(lines) if l not in lead]
        if lines:
            blocks.append({"kind": "text", "title": title, "lines": [title, *lines]})
        if title in visuals:
            blocks.append(visuals.pop(title))
        if title in ("생활 패턴", "월말 전망") and graph:
            blocks.append(graph)
            graph = None
    blocks.extend(visuals.values())
    if graph:
        blocks.append(graph)
    for title, lines in (("또래 기준·저축 여력", reference_lines), ("해볼 행동", actions)):
        if lines:
            blocks.append({"kind": "text", "title": title, "lines": [title, *lines]})
    body = [line for b in blocks if b["kind"] == "text" for line in b["lines"][1:]]
    reply = {"lines": list(dict.fromkeys(body)), "lead": [], "notices": list(dict.fromkeys(notes)), "view": blocks, "examples": []}
    if goal == "habit":
        reply = _habit_card(user, summary, reply, today)
    return followups.apply(reply, question, complete, **_followup_context(summary, goal, reply, today), covered=covered), None


def _small_sample_note(summary):
    count = summary.get("observed_count", summary.get("current", {}).get("count", 0))
    return (f"이번 분석은 확인된 결제 {count}건 기준이에요. 지금까지의 특징이며 평소 습관으로 확정한 결과는 아니에요."
            if 0 < count < 5 else None)


def _changing_trend(summary):
    return any(t.get("weekly_direction") in ("increase", "decrease") for t in
               [summary.get("overall_trend") or {}, *(summary.get("trends") or {}).values()])


def _followup_context(summary, goal, reply, today):
    """이미 보여 준 분석 근거와 새로 설명할 후보를 함께 판단한다."""
    st = summary.get("story") or {}
    by_id = {f["id"]: f for f in st.get("findings", [])}
    seen = set(reply.get("lines", []))
    graphs = []

    def visit(blocks):
        for block in blocks:
            if block["kind"] in ("months", "trend", "pace") or (block["kind"] == "rows" and any("ratio" in row for row in block.get("rows", []))):
                graphs.append(block)
            seen.update(block.get("lines", []))
            seen.update(block.get("reasons", []))
            visit(block.get("blocks", []))

    visit(reply.get("view", []))
    out = []
    habits = [by_id["HABIT"]["conclusion"]] if "HABIT" in by_id else []
    habits += [s for s in (st.get("slot"), st.get("weekend")) if s]
    habits += [f["conclusion"] for f in select(summary, "habit") if not f.get("comparable")]
    weekly = chart(summary, today) if _changing_trend(summary) else None
    habit_visuals = [f["visual"] for f in _habit_findings(summary) if f.get("visual")]
    if (goal != "habit" and any(line not in seen for line in habits) and (not weekly or weekly not in graphs) and
            not any(visual in graphs for visual in habit_visuals)):
        out.append({"id": "habit", "question": "내 소비 습관 어때?"})
    changes = [f for f in select(summary, "reason") if f.get("comparable") and f["kind"] != "total"]
    material = summary.get("total_material", {}).get("meaningful")
    if (goal != "reason" and summary["basis"].get("comparison_available") and
            (material or changes) and any(f["conclusion"] not in seen for f in changes) and
            (not weekly or weekly not in graphs)):
        out.append({"id": "reason", "question": "지난달과 달라진 소비는 뭐야?"})
    forecast = by_id.get("FORECAST")
    answered = []
    if habits and all(line in seen for line in habits):
        answered.append("habit")
    if summary["basis"].get("comparison_available") and changes and all(f["conclusion"] in seen for f in changes):
        answered.append("reason")
    if forecast and forecast["conclusion"] in seen:
        answered.append("forecast")
    if goal == "calculation":
        answered.append("calculation")
    if (goal not in ("forecast", "calculation") and forecast and forecast["conclusion"] not in seen and
            (not st.get("chart") or st["chart"] not in graphs) and
            (not _rising_weeks(summary) or not weekly or weekly not in graphs)):
        out.append({"id": "forecast", "question": "이대로 쓰면 이번 달 얼마나 쓸까?"})
    if goal != "calculation" and forecast and forecast["conclusion"] in seen and st.get("projection"):
        out.append({"id": "calculation", "question": "월말 예상은 어떻게 계산했어?"})
    return {"candidates": out[:3], "answered": answered}


def _unclassified_note(summary):
    amount = summary["unclassified_amount"][0]
    return f"분류 전 결제 {won(amount)}은 어디에 쓴 돈인지 아직 몰라 항목별 분석에서는 뺐어요. 소비 내역에서 분류를 정하면 다음 분석부터 들어가요." if amount else None


def composed_statements(data, evidence, outlook):
    """narrative.compose 결과(채운 문장)를 finding id 와 다시 짝짓는다 — 소제목을 정하려고"""
    ids = data.get("_statement_ids") or {}
    out = [(text, ids.get("evidence", [])[i] if i < len(ids.get("evidence", [])) else []) for i, text in enumerate(evidence)]
    out.extend((text, ids.get("outlook") or []) for text in outlook)
    return out


# ---------- 추이 → 습관 → 앞으로 (11/13 사용자 확정 구성) ----------

# 메뉴마다 답의 구성이 다르다 (11/14): 같은 세 섹션을 순서만 바꿔 보이지 않게
# - overview: 추이 · 습관 · 앞으로를 한 줄씩 + 월별 막대 → 더 볼 분석으로 잇는다
# - habit: 무엇을 · 언제 · 얼마나 반복해서 쓰는지 + 또래 기준 + 해볼 행동 (월별 막대 · 예상 없음)
# - forecast: 이번 달 말 · 다음 달 + 월별 막대 + 항목별 예상(예산 또는 평소 대비) (습관 · 추이 문장 없음)
# - reason: 지난달 같은 기간 대비 항목별 변화와 이유 (막대 · 습관 · 예상 없음)
ASK = {"overview": "전체적으로 분석해줘", "habit": "내 소비 습관 어때?", "forecast": "이대로 쓰면 이번 달 얼마나 쓸까?",
       "reason": "이번 달 생활비 왜 이렇게 많이 나왔지?"}


def _key(lead):
    return {"kind": "text", "title": "핵심 진단", "lines": ["핵심 진단", *dict.fromkeys(lead)], "tone": "key"}


def _section(title, lines):
    lines = [l for l in dict.fromkeys(lines) if l]
    return [{"kind": "text", "title": title, "lines": [title, *lines]}] if lines else []


def _change_rows(summary):
    changed = {f.get("category") for f in summary.get("candidates", [])
               if f.get("comparable") and not f.get("quiet") and f.get("material", {}).get("meaningful")}
    rows = sorted((c for c in summary["categories"] if c["code"] in changed), key=lambda c: -abs(c["delta"]))[:5]
    return [{"name": c["name"], "value": story._m(c["current"]["amount"]), "note": f"지난달 {story._m(c['previous']['amount'])}",
             "badge": {"direction": "up" if c["delta"] > 0 else "down", "text": f"{'+' if c['delta'] > 0 else '-'}{story._m(abs(c['delta']))}"}}
            for c in rows]


def _why_lines(finding):
    """변화 하나를 결론 + 원인 한 줄로 (금액 표와 겹치는 'X원 (지난달 Y원)' 줄은 뺀다)."""
    lines = [finding["conclusion"]]
    evidence = [l for l in finding.get("evidence", []) if "(지난달 같은 기간" not in l]
    best = next((l for l in evidence if "관련 지출" in l), None) or next((l for l in evidence if "영향이" in l or "큰 결제" in l), None)
    if best and best not in finding["conclusion"]:
        lines.append(best)
    return lines


def _rising_weeks(summary):
    """최근 4주 주마다 늘고 있는 항목 → 예상보다 더 쓸 수 있다는 주의 한 줄 (예상액을 바꾸지는 않는다)."""
    rising = [NAMES[code] for code, t in (summary.get("trends") or {}).items()
              if t.get("weekly_direction") == "increase" and code in NAMES]
    if not rising:
        return None
    listed = "·".join(rising[:2])
    return f"{snapshot.josa(listed, '이가')} 최근 4주 동안 주마다 늘고 있어요. 이 흐름이 이어지면 예상보다 더 쓸 수 있어요."


def _no_comparison_line(summary):
    return "지난달보다 더 썼는지는 비교할 수 없어, 지금 확인된 소비의 특징을 살펴봤어요."


def _total_line(summary):
    """고정지출까지 더한 월말 총지출. 생활비 예상은 위에 보여 준 이야기(story) 예상을 그대로 써서 두 숫자가 어긋나지 않게 한다"""
    total, projection = summary.get("total_forecast") or {}, (summary.get("story") or {}).get("projection")
    if total.get("state") != "ready" or not projection:
        return None
    remaining = max(projection["month_end"] - projection["observed"], 0)
    amount = round((total["actual"] + remaining + total["fixed_remaining"]) / 1000) * 1000
    return f"고정지출까지 더하면 월말 총지출 예상은 약 {story.man(amount)}이에요."


def _now_points(summary):
    """이번 달 분석 결과 — 첫 화면 핵심 진단의 재료. 의미 있는 변화 → 이번 달 특징(쏠림 · 큰 결제 · 반복 · 예산 속도) → 적은 내역의 관찰.
    예산 속도가 '맞게'인 것은 강조할 특징이 아니라 뺀다 (유형 카드 · 상세에서 쓴다)"""
    changes = [f for f in summary.get("findings", []) if f.get("comparable") and not f.get("quiet")]
    current = [f for f in summary.get("snapshot", []) if f["kind"] != "budget_ok"
               and not (changes and f["kind"] == "change_total")]  # 항목별 변화가 있으면 총액 비교 문장은 같은 말의 반복
    current += [f for f in _pattern_findings(summary) if f not in current]
    # 예산을 넘었거나 빨리 쓰는 항목은 바로 행동할 수 있는 정보라 변화보다 앞에 둔다
    pace = [f for f in current if f["kind"] == "budget_fast"]
    points = [*pace, *changes, *(f for f in current if f not in pace)]
    return points or list(summary.get("observation", []))


HABIT_KINDS = ("frequent", "pattern", "weekend", "composition", "large_share", "observation")


def _habit_points(summary):
    """습관 답의 첫 화면 재료 — 어떻게 쓰는지(반복 · 시간대 · 쏠림 · 큰 결제 비중)가 먼저, 지난달 대비 변화는 그다음.
    전체 분석과 같은 변화 문장으로 시작해 내용이 반복되지 않게 한다"""
    points = _now_points(summary)
    return [*(f for f in points if f["kind"] in HABIT_KINDS), *(f for f in points if f["kind"] not in HABIT_KINDS)]


def _share_rows(summary):
    """카테고리별 이번 달 비중 (가로막대). 분류된 생활비가 두 항목 이상일 때만. 미분류는 비중에서 빼고 하단 안내로"""
    rows = [(c["code"], c["name"], c["current"]["amount"]) for c in summary.get("categories", [])
            if c["code"] in NAMES and c["code"] != "UNCLASSIFIED" and c["current"]["amount"] > 0]
    total = sum(amount for *_, amount in rows)
    if len(rows) < 2 or total <= 0:
        return None
    rows.sort(key=lambda r: -r[2])
    return {"kind": "rows", "title": "이번 달 어디에 썼나요", "rows": [
        {"code": code, "name": name, "amount": amount, "ratio": round(amount * 100 / total, 1), "note": f"{round(amount * 100 / total):g}%"}
        for code, name, amount in rows]}


def _story_blocks(user, summary, goal, today, by_id, st):
    """→ (blocks, examples, reference_notes). 메뉴에 맞는 재료가 없으면 overview 구성으로"""
    first = lambda i: (by_id[i]["evidence"] or [None])[0] if i in by_id else None
    others = [ASK[g] for g in ("habit", "forecast", "reason", "overview") if g != goal]
    if goal == "forecast" and "FORECAST" not in by_id:
        # 전망이 없는데 과거 월별 그래프를 전망의 답처럼 다시 보여 주지 않는다.
        current = select(summary, "habit")[:1]
        blocks = [_key(["월말 예상액은 아직 계산하지 않았어요.", *(f["conclusion"] for f in current)])]
        blocks += _section("확인된 소비 특징", [line for f in current for line in f.get("evidence", [])[:1]])
        return blocks, [], []
    if goal == "habit":
        # 첫 화면: 매달 반복되는 습관 → 이번 달 구체적인 특징 · 변화 → 언제 쓰는지 (최대 3줄, 총론만 두지 않는다)
        now = _habit_points(summary)
        habitual = [f["conclusion"] for f in now if f["kind"] in HABIT_KINDS]
        others = [f["conclusion"] for f in now if f["kind"] not in HABIT_KINDS]
        timing = [x for x in (st.get("weekend"), st.get("slot")) if x][:1]
        lead = [*([by_id["HABIT"]["conclusion"]] if "HABIT" in by_id else []), *habitual, *timing, *others][:3]
        lead = lead or [first("TREND") or "현재 확인된 내역에서는 두드러지는 소비 습관은 보이지 않아요."]
        change = next((f for f in summary.get("findings", []) if f.get("comparable") and not f.get("quiet")
                       and f["kind"] not in ("large_total", "large_payment", "unclassified_change")), None)
        if change:  # 반복되는 방식 + 최근 달라진 점은 핵심에 같이 (근거는 아래 섹션)
            lead.append(change["conclusion"])
        blocks = [_key(lead)]
        if st.get("habit_rows"):
            blocks.append({"kind": "rows", "title": "매달 반복되는 소비", "rows": st["habit_rows"]})
        blocks += _section("언제 쓰나요", [x for x in (st.get("weekend"), st.get("slot")) if x not in lead])
        if change:
            blocks += _section("지난달과 달라진 점", [l for l in change["evidence"][:2] if l not in lead])
        ref_lines, ref_notes = benchmark.for_user(user, summary, today)
        blocks += _section("또래 기준·저축 여력", ref_lines)
        blocks += _section("해볼 행동", st["actions"])
        habits = st.get("habits") or {}
        look = ("외식이랑 카페에 얼마 썼어?" if "dining" in habits and "cafe" in habits else "외식에 얼마 썼어?" if "dining" in habits
                else "카페에 얼마 썼어?" if "cafe" in habits else None)
        return blocks, [x for x in (look, ASK["forecast"], ASK["reason"]) if x][:3], ref_notes
    if goal == "forecast" and "FORECAST" in by_id:
        f = by_id["FORECAST"]
        nxt = next((l for l in f["evidence"] if l.startswith("다음 달")), None)
        lead = [f["conclusion"], *([nxt] if nxt else [])]
        blocks = [_key(lead)]
        if st["chart"]:
            blocks.append(st["chart"])
        if st.get("forecast_rows"):
            blocks.append({"kind": "rows", "title": "항목별 이번 달 말 예상", "rows": st["forecast_rows"]})
        ahead = [l for l in f["evidence"] if l not in lead and not l.startswith("이번 달 말")]  # 핵심 진단의 월말 금액을 되풀이하지 않는다
        ahead.append(_total_line(summary))
        ahead.append(_rising_weeks(summary))
        blocks += _section("앞으로", ahead)
        weekly = chart(summary, today) if _rising_weeks(summary) else None
        if weekly:
            blocks.append(weekly)
        if st.get("over"):
            blocks += _section("해볼 행동", st["actions"])
        return blocks, ["왜 이렇게 계산된거야?", ASK["habit"], ASK["reason"]], []
    if goal == "reason":
        cause = next((x for x in select(summary, "reason") if x["kind"] in ("change_total", "large_share", "total")), None)
        if cause and summary["basis"].get("comparison_available"):
            offset = next((x for x in summary.get("findings", []) if x["kind"] == "offset"), None)
            lead = [offset["conclusion"] if offset else cause["conclusion"]]  # 상쇄면 총액 비교 대신 어떤 항목이 늘고 줄었는지를 결론으로
            blocks = [_key(lead)]
            rows = _change_rows(summary)
            if rows:
                blocks.append({"kind": "rows", "title": "항목별 변화 (지난달 같은 기간 대비)", "rows": rows})
            why = sorted((l for l in cause["evidence"] if l not in lead), key=lambda l: not l.startswith("이번 달 "))  # 총액 비교를 먼저
            changes = [x for x in summary.get("findings", []) if x.get("comparable") and not x.get("quiet")
                       and x["kind"] not in ("total", "offset", "usual") and x["conclusion"] not in lead]
            changes.sort(key=lambda x: -x.get("impact", 0))
            for x in changes[:2]:  # 지난달 대비 달라진 점만 — 결론과 원인 한 줄씩 (평소 모습 설명은 습관 답에서)
                why += _why_lines(x)
            st["limits"] = list(dict.fromkeys(t for x in changes[:2] for t in x.get("limits", [])))
            frequent_dining = next((x for x in summary.get("snapshot", []) if x["kind"] == "frequent"), None)
            if frequent_dining and any(x.get("category") == frequent_dining.get("category") for x in changes[:2]):
                why.append(frequent_dining["conclusion"])
            blocks += _section("왜 달라졌나요", why)
            weekly = chart(summary, today) if _changing_trend(summary) else None
            if weekly:
                blocks.append(weekly)
            return blocks, [ASK["forecast"], ASK["habit"]], []
        if not summary["basis"].get("comparison_available"):
            current = select(summary, "habit")[:2]
            lead = [f["conclusion"] for f in current] or [st.get("slot") or st.get("weekend") or _no_comparison_line(summary)]
            blocks = [_key(lead), *_section("확인된 소비 특징", [line for f in current for line in f.get("evidence", [])[:1]])]
            return blocks, [], []
    # overview: 이번 달 분석이 먼저(무엇이 달라졌고 어디에 몰렸나), 지난 달들 추이 · 월말 전망은 한 줄씩.
    # 그래프는 내용에 맞는 것만 — 어디에 썼나 = 카테고리 비중, 예산 속도 = 예산 막대, 추이가 실제로 변할 때만 월별 막대
    now = _now_points(summary)
    trend_f, forecast_f = by_id.get("TREND"), by_id.get("FORECAST")
    moving = bool(trend_f and trend_f["kind"] != "trend_flat")
    lead = [f["conclusion"] for f in now[:2]]
    if trend_f and (moving or not lead):
        lead.append(trend_f["conclusion"])
    if forecast_f:
        lead.append(forecast_f["conclusion"])
    if not lead:
        lead = [st.get("slot") or st.get("weekend") or "확인된 내역에서는 특정 항목이나 시간대에 소비가 몰리는 특징은 보이지 않아요."]
    if st.get("no_comparison"):
        lead.insert(0, _no_comparison_line(summary))
    blocks = [_key(lead)]
    share = _share_rows(summary)
    if share:
        blocks.append(share)
    shown = {line for line in lead}
    for f in now[:3]:
        lines = [f["conclusion"], *([] if f["kind"] == "offset" else (f.get("evidence") or [])[:2])]
        blocks += _section(f.get("section") or "이번 달 특징", [l for l in lines if l not in shown])
        shown.update(lines)
        if f.get("visual") and f["visual"] not in blocks and not (share and f["kind"] == "composition"):
            blocks.append(f["visual"])
    if "HABIT" in by_id:  # 매달 반복되는 소비 습관 (지난 달들 기준) — 한 줄 + 근거
        habit_f = by_id["HABIT"]
        blocks += _section("소비 습관", [l for l in [habit_f["conclusion"], *habit_f["evidence"][:1]] if l not in shown])
    if trend_f:
        blocks += _section("소비 추이", [l for l in [trend_f["conclusion"], *trend_f["evidence"][:1]] if l not in shown])
        if moving and st.get("chart"):
            blocks.append(st["chart"])
    if forecast_f:
        ahead = [l for l in forecast_f["evidence"] if l not in shown and not l.startswith("이번 달 말")]
        ahead.append(_total_line(summary))
        blocks += _section("앞으로", ahead)
    weekly = chart(summary, today) if _changing_trend(summary) else None
    if weekly and weekly not in blocks:
        blocks.append(weekly)
    return blocks, others[:3], []


def _habit_findings(summary):
    findings = [*summary.get("snapshot", []), *_pattern_findings(summary),
                *(f for f in summary.get("findings", []) if f.get("comparable") and not f.get("quiet"))]
    return findings or summary.get("observation", [])


def _habit_details(summary, body, today, by_id):
    """요약 뒤에서 현재 계산된 근거와 그래프를 보존한다. 조용한 변화는 설명에 추가하지 않는다."""
    body = list(body)
    seen = {line for b in body if b["kind"] == "text" for line in b["lines"]}

    def add(title, lines):
        fresh = [line for line in dict.fromkeys(lines) if line and line not in seen]
        body.extend(_section(title, fresh))
        seen.update(fresh)

    if "TREND" in by_id:
        f = by_id["TREND"]
        add("소비 추이", [f["conclusion"], *f["evidence"]])
    st = summary["story"]
    for f in _habit_findings(summary):
        add(f["section"], [f["conclusion"], *f.get("evidence", []), f.get("outlook")])
        if f.get("visual") and f["visual"] not in body:
            body.append(f["visual"])
    graph = chart(summary, today) if _changing_trend(summary) else None
    if graph and graph not in body:
        body.append(graph)
    share = _share_rows(summary)
    if share and not any(b["kind"] == "rows" and b.get("title") == "분류된 소비의 비중" for b in body):
        body.append(share)
    if "FORECAST" in by_id:
        f = by_id["FORECAST"]
        add("월말 전망", [f["conclusion"], *f["evidence"], _total_line(summary)])
        if st.get("forecast_rows"):
            body.append({"kind": "rows", "title": "항목별 이번 달 말 예상", "rows": st["forecast_rows"]})
    return body


def _persona_context(user, summary, today):
    """소비 유형 관리 축 · 보조 근거 재료 (읽기만 한다). 없는 값은 None — 그 특성만 판정하지 않는다"""
    context = {}
    if not getattr(user, "pk", None):
        return context
    try:
        from budgets.models import Budget
        from budgets.protect import fixed_total
        from finance.models import FinancialTransaction
        profile = user.financial_profile
        birth, housing, floor = profile.birth_date, profile.housing_type, profile.savings_floor
        context.update(housing=housing if isinstance(housing, str) else None,
                       savings_floor=floor if type(floor) is int else None,
                       age=today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day)) if isinstance(birth, date) else None)
        # 배분 기준 금액 = 이번 급여 주기에 승인한 예산의 배분 대상 자금 (월 실수령액 − 확정 고정지출)
        budget = (Budget.objects.filter(user=user, status="approved", cycle__status="active", cycle__starts_on__lte=today,
                                        cycle__ends_before__gt=today).order_by("-approved_at", "-pk")
                  .select_related("cycle").first())
        if budget and type(budget.funding_amount) is int:
            fixed = fixed_total(user, budget.cycle)
            if fixed is not None:
                context["base"] = budget.funding_amount - fixed
            context["saving_seen"] = FinancialTransaction.objects.filter(
                user=user, transaction_type="saving", is_canceled=False,
                occurred_on__gte=budget.cycle.starts_on, occurred_on__lte=today).exists()
    except (AttributeError, ObjectDoesNotExist, DatabaseError, TypeError, ValueError):
        return context
    # 모델 C는 대상(20~34세 1인 가구)일 때만 부른다 — 대상 밖이면 하위 프로세스를 띄우지 않는다
    if context.get("housing") == "alone" and context.get("age") is not None and 20 <= context["age"] <= 34:
        context["model"] = benchmark.reference(user, summary, today)
    else:
        context["model"] = {"reason": "out_of_range"}
    return context


def _habit_card(user, summary, reply, today, context=None):
    """소비 습관 답: 맨 위 = 소비 유형 카드(마스코트 · 이름 · 한 줄 소개 · 태그 · 근거 · 해볼 한 가지), 아래 '자세히 보기' = 기존 분석 전부"""
    basis = summary.get("basis", {})
    scoped = bool(basis.get("categories") or any(basis.get("filters", {}).values()))
    history = ({"state": "not_applicable"} if scoped else
               budget_habits.for_user(user, today) if getattr(user, "pk", None) else {"state": "unavailable"})
    if history["state"] == "unavailable":
        reply = {**reply, "notices": [*reply["notices"], "승인된 예산 변경 이력을 확인하지 못해 소비 내역으로 성향을 살펴봤어요."]}
    if context is None:
        context = {} if scoped else _persona_context(user, summary, today)
    result = personality.assess(summary, history, {**context, "scoped": scoped})
    profile = result["profile"]
    if not profile and result["reason"] == "too_unclassified":
        reply = {**reply, "notices": [*reply["notices"], "분류되지 않은 결제가 많아 소비 유형은 아직 정하지 않았어요."]}
    details = [*reply["view"][1:], *_section("예산을 관리하는 습관", personality.habit_lines(history, profile))]
    # 핵심 진단(구체적인 소비 특징 · 변화)은 접지 않고 첫 화면에 둔다 — 유형 카드 뒤에 묻히거나 상세를 열어야만 이해되지 않게
    key = details[0] if details and details[0].get("tone") == "key" else None
    details = details[1:] if key else details
    if profile:
        persona = {"kind": "persona", "mascot": profile["mascot"], "name": profile["title"], "desc": profile["desc"],
                   "reasons": profile["lines"], "action": profile.get("action"), "tags": profile.get("tags", []),
                   # 특성 기록은 검증 · 로그용 (화면에 쓰지 않는다). 금액 원본은 빼고 비율 · 상태만
                   "traits": [{k: v for k, v in t.items() if k != "average"} for t in profile.get("traits", [])]}
        lines = [l for l in (key["lines"][1:] if key else []) if l not in profile["lines"]]
        front = [persona, *([{**key, "lines": [key["lines"][0], *lines]}] if lines else [])]
        tags = "".join(f" · {t['label']}" for t in profile.get("tags", []))
        extra = [f"나의 소비 유형은 '{profile['title']}'이에요{f'({tags[3:]})' if tags else ''}. {profile['desc']}", *profile["lines"],
                 *([profile["action"]] if profile.get("action") else [])]
    else:
        # 근거가 없으면 유형을 지어내지 않는다 — 유형 카드 없이 이번에 확인된 분석을 바로 보여 준다
        front = [key] if key else []
        extra = []
    detail_lines = [line for b in details if b["kind"] == "text" for line in b["lines"][1:]]
    view = [reply["view"][0], *front]
    if details:
        view.append({"kind": "details", "title": "자세한 소비 분석 보기", "blocks": details})
    return {**reply, "view": view, "lines": list(dict.fromkeys([*extra, *reply["lines"], *detail_lines]))}


def _merge_sections(blocks):
    """같은 소제목(예: '지난달과 비교')이 여러 번 나오면 첫 번째 칸으로 합친다. 같은 문장은 한 번만"""
    out, index = [], {}
    for b in blocks:
        title = b.get("title") if b["kind"] == "text" and b.get("tone") != "key" else None
        if title and title in index:
            first = out[index[title]]
            out[index[title]] = {**first, "lines": [first["lines"][0], *dict.fromkeys([*first["lines"][1:], *b["lines"][1:]])]}
            continue
        if title:
            index[title] = len(out)
        out.append(b)
    return out


def _story_answer(user, summary, goal, today, suffix):
    st, basis = summary["story"], summary["basis"]
    by_id = {f["id"]: f for f in st["findings"]}
    body, examples, reference_notes = _story_blocks(user, summary, goal, today, by_id, st)
    if goal == "habit":
        body = _habit_details(summary, body, today, by_id)
    blocks = [{"kind": "head", "title": f"{span(this_month(today))} · 소비 분석{suffix}"}, *_merge_sections(body)]
    notes = []
    if basis.get("clipped"):
        after = today + timedelta(days=1)
        notes.append(f"{after.month}월 {after.day}일 이후 내역은 아직 불러오지 않아 {today.month}월 {today.day}일까지 기준으로 분석했어요.")
    if basis.get("comparison_reason"):
        notes.append(basis["comparison_reason"])
    notes.extend(st.pop("limits", []) if goal == "reason" else [])
    if goal == "habit":
        notes.extend(note for f in _habit_findings(summary) for note in f.get("limits", []))
    shown = any(b["kind"] == "months" for b in blocks)
    forecast = by_id.get("FORECAST")
    forecast_shown = forecast and any(forecast["conclusion"] in b.get("lines", []) for b in blocks)
    projection = st.get("projection") if forecast_shown else None
    if projection and projection["method"] == "validated":
        report = projection["validation"]
        notes.append(f"예상 방식: 과거 소비로 여러 방식을 시험해 {trend.MODEL_NAMES[report['selected']['model']]}(최근 {report['selected']['lookback_days']}일 반영)을 골랐어요. "
                     f"따로 떼어 둔 {len(report['test_months'])}개월에서 실제 월말과 평균 {won(round(report['test_mae'], -3))} 차이가 났어요.")
    elif projection and projection["method"] == "pattern":
        notes.append("예상 방식: 자주 쓰는 항목은 최근 4주 하루 평균이 남은 날에도 이어진다고 보고, 한 달에 한두 번 사는 항목은 평소 한 달 금액까지만 더했어요."
                     + (f" 지난 {projection['checked']}개월에 같은 날짜 기준으로 맞춰 보니 실제 월말과 평균 {won(int(round(projection['error'], -3)))} 차이가 났어요."
                        if projection.get("checked") else ""))
    elif projection:
        notes.append("예상 방식: 방식을 시험해 볼 과거 달이 아직 적어서, 최근 4주 하루 평균이 이어진다고 보고 계산한 참고치예요.")
    if projection:
        notes.append("예상액에는 앞으로 새로 생길 큰 결제가 들어 있지 않아요.")
    if (shown or goal in ("forecast", "overview")) and not any(f["id"] == "FORECAST" for f in st["findings"]):
        reason = summary.get("forecast_reason") or summary.get("overall_trend", {}).get("reason")
        cause = (basis.get("comparison_reason") or "").split(". ")[0]
        if reason and cause and reason.startswith(cause):  # 수집 범위 설명은 비교 안내에 이미 있다 — 같은 긴 문장을 두 번 쓰지 않는다
            reason = reason[len(cause):].lstrip(". ").strip()
        if reason:
            notes.append(f"월말 전망: {reason}")
    if shown and st.get("chart"):
        notes.append(st["chart"]["basis"])
    if goal == "reason" and basis.get("comparison_available"):
        end = date.fromisoformat(basis["as_of"]).day
        notes.append(f"지난달 비교는 같은 날짜까지만 봐요 ({_md(basis['current']['from'])}~{end}일 vs "
                     f"{_md(basis['previous']['from'])}~{date.fromisoformat(_same_day(basis['previous']['from'], basis['as_of'])).day}일).")
    if _unclassified_note(summary):
        notes.append(_unclassified_note(summary))
    if _small_sample_note(summary):
        notes.append(_small_sample_note(summary))
    notes.extend(n for n in reference_notes if n.startswith(("ML 기준은", "남는 돈은")))
    if basis.get("scope"):
        notes.append(f"분석 범위: {basis['scope']} · {span(this_month(today))} · 고정지출 제외")
    reply = {"lines": list(dict.fromkeys(l for b in blocks if b["kind"] == "text" for l in b["lines"][1:])), "lead": [],
             "notices": list(dict.fromkeys(notes)), "view": blocks, "examples": examples}
    if st.get("over"):  # 예산을 넘을 전망 → [예산 조정하기] (어느 카테고리인지는 라우터에만 알린다)
        reply["suggest"] = ["budget_adjust"]
        reply["over"] = st["over"]
    return _habit_card(user, summary, reply, today) if goal == "habit" else reply

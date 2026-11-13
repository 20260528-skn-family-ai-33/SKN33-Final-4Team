"""DB 집계와 설명 후보 검증 → 질문 중심 서술 → 근거 대조. 원거래는 전송하지 않는다."""
import calendar
import re
from datetime import date, timedelta
from statistics import median

from django.core.exceptions import ValidationError
from django.db import DatabaseError

from budgets.scope import OUT_OF_BUDGET
from . import benchmark, lifestyle, narrative, reasoning, result as R, scope, snapshot, story, tools, trend
from .answer import NAMES, span, won
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
    states = [coverage(user, p)["state"] for p in periods]
    comparison_available = states == ["complete", "complete"]
    comparison_reason = None if comparison_available else (
        "지난달 같은 기간의 수집 상태를 확인하지 못해 지난달 비교는 빼고 분석했어요." if "error" in states else
        "이번 달 내역이 아직 다 모이지 않아 지난달 비교는 빼고 분석했어요." if states[0] != "complete" else
        "지난달 같은 기간 내역이 다 모이지 않아 지난달 비교는 빼고 분석했어요.")
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
                          "comparison_reason": comparison_reason,
                          "count_basis": "취소 차감 후 순금액이 양수인 소비 결제"},
               "current": current, "previous": previous, "categories": selected,
               "user_context": {"activity": request.analysis_activity, "planned": request.analysis_planned},
               "unclassified_amount": unclassified_amount}
    months = trend.history_months(today)
    history, complete_months, complete, source_history = [], [], False, []
    try:
        complete_months = [p for p in months if coverage(user, p)["state"] == "complete"]
        observed = Period(today - timedelta(days=27), today + timedelta(days=1))
        complete = coverage(user, observed)["state"] == "complete" and states[0] == "complete"
        windows = {28: complete}

        def checked(projection, current_rows, past_rows):
            if projection["state"] != "ready":
                return projection
            days = projection["validation"].get("selected", {}).get("lookback_days", 28)
            if days not in windows:
                windows[days] = coverage(user, Period(today - timedelta(days=days - 1), today + timedelta(days=1)))["state"] == "complete"
            if not windows[days]:
                return {**{k: v for k, v in projection.items() if k not in ("month_end", "remaining", "observed", "model")},
                        "state": "unavailable", "reason": f"선택한 예측 방식에 필요한 최근 {days}일 내역이 모두 모이지 않아 월말 예상액은 보류했어요."}
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
            summary["forecast_reason"] = ("이번 달 전체 수집 상태를 확인하지 못해 월말 예상액은 계산하지 않았어요." if states[0] == "error" else
                                          "이번 달 내역이 모두 모이지 않아 월말 예상액은 계산하지 않았어요.")
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
        *snapshot.large_share(rows[0], history),
        *snapshot.frequent(rows[0], today, periods[0].start, month_end_before),
        *(snapshot.weekend(rows[0], periods[0].start, today) if whole else []),
        *(snapshot.composition(rows[0]) if whole else []),
    ]
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
    codes = list(dict.fromkeys(code for f in summary.get("findings", []) for code in
                 ([f["category"]] if f.get("category") else f.get("categories", []))))[:2]
    choices = [(code, NAMES[code], summary.get("trends", {}).get(code, {})) for code in codes]
    if not choices:
        choices = [("overall", summary["basis"]["scope"], summary.get("overall_trend", {}))]
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
    return chosen


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
    ("전체 분석", "최근 몇 달 생활비 흐름, 매달 반복되는 소비, 이대로 쓰면 이번 달 말·다음 달에 얼마가 될지를 한 번에 봐요.",
     "전체적으로 분석해줘"),
    ("소비 습관", "외식·카페·배달처럼 매달 반복되는 소비가 생활비에서 얼마나 되는지, 줄이면 한 달에 얼마가 남는지 짚어요.",
     "내 소비 습관 어때?"),
    ("이번 달 말·다음 달 예상", "지금 쓰는 속도가 이어지면 이번 달 말과 다음 달에 얼마가 되는지, 예산을 넘을 항목이 있는지 봐요.",
     "이대로 쓰면 이번 달 얼마나 쓸까?"),
    ("지난달과 비교", "지난달 같은 기간보다 더 쓰거나 덜 쓴 이유를 항목별로 찾아요.", "이번 달 생활비 왜 이렇게 많이 나왔지?"),
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


def answer(user, decision, today, complete, question=None):
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
    request = decision.request or decision.interpretation
    question = question or (decision.explain or {}).get("question") or "소비 분석해줘"
    goal = getattr(request, "analysis_goal", None) or ("reason" if request.explain else "overview")
    if wants_calculation(asked) and summary.get("story"):
        reply = _calculation_reply(summary, today, suffix)
        if reply is not None:
            return reply, None
    if summary.get("story") and summary["story"]["findings"]:
        return _story_answer(user, summary, goal, today, suffix), None
    findings = select(summary, goal)
    forecast, notes = _outlook(summary)
    if forecast and goal in ("reason", "habit"):  # 이유 · 습관 질문에는 전망을 붙이지 않는다 (방식 안내만 하단에서 뺀다)
        forecast, notes = None, [n for n in notes if not n.startswith(("월말 전망 방식", "총지출 ="))]
    elif forecast and goal == "overview":  # 전반 분석에는 전망 한 문장만
        forecast = re.split(r"(?<=[.!?])\s+", forecast)[0]
    if not findings and not forecast:
        current = summary["current"]
        line = (f"이번 달 소비는 아직 {current['count']}건이라 패턴을 읽기엔 적어요. 내역이 조금 더 쌓이면 다시 분석해 볼게요."
                if current["count"] else "이번 달에 분석할 소비 내역이 아직 없어요.")
        notes = [n for n in (_unclassified_note(summary), f"분석 범위: {basis['scope']} · {span(this_month(today))} · 고정지출 제외") if n]
        return {"lines": [line], "lead": [], "notices": notes, "examples": [],
                "view": [{"kind": "head", "title": f"{span(this_month(today))} · 소비 분석{suffix}"}, {"kind": "text", "lines": [line]}]}, None
    data = narrative.payload(question, findings, forecast, goal) if findings else None
    composed = narrative.compose(data, complete) if data else None
    if composed is not None:
        lead, evidence, outlook = composed
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
    notes.extend(dict.fromkeys(t for f in findings for t in f.get("limits", [])))
    if _unclassified_note(summary):
        notes.append(_unclassified_note(summary))
    notes.append(f"분석 범위: {basis['scope']} · {span(this_month(today))} · 고정지출 제외")
    reference_lines, reference_notes = benchmark.for_user(user, summary, today) if goal != "reason" else ([], [])
    notes.extend(n for n in reference_notes if n.startswith(("ML 기준은", "남는 돈은")))
    actions = list(dict.fromkeys([*(a for f in findings for a in f.get("actions", [])),
                                  *(lifestyle.suggestions(findings) or summary.get('reference_actions', []))]))[:3]
    blocks = [{"kind": "head", "title": f"{span(this_month(today))} · 소비 분석{suffix}"}]
    blocks.append({"kind": "text", "title": "핵심 진단", "lines": ["핵심 진단", *lead], "tone": "key"})
    graph = chart(summary, today)
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
    return {"lines": list(dict.fromkeys(body)), "lead": [], "notices": list(dict.fromkeys(notes)), "view": blocks, "examples": []}, None


def _unclassified_note(summary):
    amount = summary["unclassified_amount"][0]
    return f"'확인 필요' 결제 {won(amount)}은 무엇에 쓴 돈인지 몰라 분석에서 뺐어요. 소비 내역에서 분류를 정하면 다음 분석부터 들어가요." if amount else None


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
    rows = sorted((c for c in summary["categories"] if abs(c["delta"]) >= 5000), key=lambda c: -abs(c["delta"]))[:5]
    return [{"name": c["name"], "value": story._m(c["current"]["amount"]), "note": f"지난달 {story._m(c['previous']['amount'])}",
             "badge": {"direction": "up" if c["delta"] > 0 else "down", "text": f"{'+' if c['delta'] > 0 else '-'}{story._m(abs(c['delta']))}"}}
            for c in rows]


def _story_blocks(user, summary, goal, today, by_id, st):
    """→ (blocks, examples, reference_notes). 메뉴에 맞는 재료가 없으면 overview 구성으로"""
    first = lambda i: (by_id[i]["evidence"] or [None])[0] if i in by_id else None
    others = [ASK[g] for g in ("habit", "forecast", "reason", "overview") if g != goal]
    if goal == "habit" and ("HABIT" in by_id or st.get("slot") or st.get("weekend")):
        lead = [by_id["HABIT"]["conclusion"]] if "HABIT" in by_id else [st.get("weekend") or st["slot"]]
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
        blocks += _section("앞으로", [l for l in f["evidence"] if l not in lead and not l.startswith("이번 달 말")])  # 핵심 진단의 월말 금액을 되풀이하지 않는다
        if st.get("over"):
            blocks += _section("해볼 행동", st["actions"])
        return blocks, ["왜 이렇게 계산된거야?", ASK["habit"], ASK["reason"]], []
    if goal == "reason":
        cause = next((x for x in select(summary, "reason") if x["kind"] in ("change_total", "large_share", "total")), None)
        if cause and summary["basis"].get("comparison_available"):
            lead = [cause["conclusion"]]
            blocks = [_key(lead)]
            rows = _change_rows(summary)
            if rows:
                blocks.append({"kind": "rows", "title": "항목별 변화 (지난달 같은 기간 대비)", "rows": rows})
            why = sorted((l for l in cause["evidence"] if l not in lead), key=lambda l: not l.startswith("이번 달 "))  # 총액 비교를 먼저
            why += [x["conclusion"] for x in select(summary, "reason") if x is not cause and x.get("comparable")
                    and x["kind"] not in ("total",)][:2]  # 지난달 대비 달라진 점만 (평소 모습 설명은 습관 답에서)
            blocks += _section("왜 달라졌나요", why)
            return blocks, [ASK["forecast"], ASK["habit"]], []
    # overview (또는 메뉴에 맞는 재료가 없을 때): 세 가지를 한 줄씩 + 월별 막대
    order = [i for i in ("TREND", "HABIT", "FORECAST") if i in by_id]
    lead = [by_id[i]["conclusion"] for i in order]
    blocks = [_key(lead)]
    if st["chart"]:
        blocks.append(st["chart"])
    for i, title in (("TREND", "소비 추이"), ("HABIT", "소비 습관"), ("FORECAST", "앞으로")):
        line = first(i)
        if line and line not in lead:
            blocks += _section(title, [line])
    return blocks, others[:3], []


def _story_answer(user, summary, goal, today, suffix):
    st, basis = summary["story"], summary["basis"]
    by_id = {f["id"]: f for f in st["findings"]}
    body, examples, reference_notes = _story_blocks(user, summary, goal, today, by_id, st)
    blocks = [{"kind": "head", "title": f"{span(this_month(today))} · 소비 분석{suffix}"}, *body]
    notes = []
    if basis.get("clipped"):
        after = today + timedelta(days=1)
        notes.append(f"{after.month}월 {after.day}일 이후 내역은 아직 불러오지 않아 {today.month}월 {today.day}일까지 기준으로 분석했어요.")
    shown = any(b["kind"] in ("months",) for b in blocks)  # 예상이 화면에 나온 답(전체 · 앞으로)에만 예상 방식 안내
    projection = st.get("projection") if shown else None
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
    if shown and st["findings"] and not any(f["id"] == "FORECAST" for f in st["findings"]):
        reason = summary.get("forecast_reason") or summary.get("overall_trend", {}).get("reason")
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
    notes.extend(n for n in reference_notes if n.startswith(("ML 기준은", "남는 돈은")))
    reply = {"lines": list(dict.fromkeys(l for b in blocks if b["kind"] == "text" for l in b["lines"][1:])), "lead": [],
             "notices": list(dict.fromkeys(notes)), "view": blocks, "examples": examples}
    if st.get("over"):  # 예산을 넘을 전망 → [예산 조정하기] (어느 카테고리인지는 라우터에만 알린다)
        reply["suggest"] = ["budget_adjust"]
        reply["over"] = st["over"]
    return reply

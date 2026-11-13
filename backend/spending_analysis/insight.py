"""DB 집계와 설명 후보 검증 → 질문 중심 서술 → 근거 대조. 원거래는 전송하지 않는다."""
from datetime import timedelta

from django.db import DatabaseError

from budgets.scope import OUT_OF_BUDGET
from . import lifestyle, narrative, reasoning, result as R, scope, tools, trend
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
                and request.include_fixed is not True
                and request.target != "budget_left" and (request.target == "overview" or request.explain)
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


def summarize(user, decision, today):
    request = decision.request or decision.interpretation
    items = decision.items or decision.explain["items"]
    where = items[0]
    periods = (this_month(today), previous_month_same_days(today))
    states = [coverage(user, p)["state"] for p in periods]
    comparison_available = states == ["complete", "complete"]
    missing = "과 ".join(label for label, state in zip(("이번 달", "지난달 같은 기간"), states) if state != "complete")
    comparison_reason = (f"{missing}의 수집 상태를 확인하지 못해 지난달과의 증감 비교는 보류했어요." if "error" in states else
                         f"{missing} 내역이 모두 모이지 않아 지난달과의 증감 비교는 보류했어요.") if not comparison_available else None
    living = request.analysis_scope == "living"
    source = [[r for r in tools.rows(user, p, {**where, "include_fixed": True}, include_activity=True)
               if r["transaction_type"] == "spending"] for p in periods]
    rows = [[r for r in side if r["funding"] != "fixed"
             and (not living or r["category"] not in OUT_OF_BUDGET)] for side in source]
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
                          "comparison_reason": comparison_reason,
                          "count_basis": "취소 차감 후 순금액이 양수인 소비 결제"},
               "current": current, "previous": previous, "categories": selected,
               "user_context": {"activity": request.analysis_activity, "planned": request.analysis_planned},
               "unclassified_amount": [sum(r["net_amount"] for r in side if r["category"] == "UNCLASSIFIED") for side in rows]}
    months = trend.history_months(today)
    history, complete_months, complete = [], [], False
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
        history = [r for r in source_history if r["funding"] != "fixed"
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
        if not requested and not any(where.get(k) for k in scope.FILTERS):
            all_current = [r for r in source[0] if r["funding"] != "fixed"]
            all_history = [r for r in source_history if r["funding"] != "fixed"]
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
    parts, notes = [], []
    overall = summary.get("overall_trend", {})
    if overall.get("state") == "ready":
        report = overall["validation"]
        selected = report["selected"]
        parts.append(f"반복 소비 흐름이 이어지면 월말 {summary['basis']['scope']} 예상액은 약 {won(overall['month_end'])}이에요. "
                     f"최근 {selected['lookback_days']}일의 반복 소비 흐름을 반영한 예상이에요.")
        notes.append(f"과거 소비로 예측 방법을 비교해 최근 {selected['lookback_days']}일의 소비를 반영하는 {trend.MODEL_NAMES[selected['model']]} 방식을 골랐어요. 별도 {len(report['test_months'])}개월에서 실제 월말 금액과 평균 {won(report['test_mae'])} 차이가 났어요. 이번 달에도 같은 정도로 맞는다는 뜻은 아니에요.")
        notes.append("월말 예상액은 최근 반복 소비가 유지되는 조건의 추정이며, 앞으로 새로 발생할 큰 결제는 포함하지 않았어요.")
    else:
        reason = summary.get("forecast_reason") or overall.get("reason") or "검증할 과거 자료가 부족해 월말 전망은 보류했어요."
        if "오차" in reason:
            notes.append(reason)
            reason = "과거 소비로 확인했을 때 예측이 충분히 잘 맞지 않아 월말 예상액은 보류했어요."
        parts.append(reason)
        notes.append(reason)
    total = summary.get("total_forecast", {})
    if total.get("state") == "ready":
        parts.append(f"고정지출을 포함한 월말 총지출 예상은 약 {won(total['month_end'])}이에요.")
        notes.append(f"총지출 계산: 실제 지출 {won(total['actual'])}(이미 낸 고정지출 {won(total['fixed_paid'])} 포함) + 남은 변동 소비 예상 {won(total['remaining_variable'])} + 미결제 등록 고정지출 {won(total['fixed_remaining'])}. 이미 낸 금액은 중복 합산하지 않았어요.")
    elif total.get("reason") and overall.get("state") == "ready":
        parts.append(total["reason"])
    return " ".join(parts), notes


def answer(user, decision, today, complete, question=None):
    try:
        summary, reason = summarize(user, decision, today)
    except (DatabaseError, tools.DbError):
        return None, "추가 분석 자료를 불러오지 못해 기존 조회·설명 결과를 보여드려요."
    if summary is None:
        return None, reason
    request = decision.request or decision.interpretation
    question = question or (decision.explain or {}).get("question") or "소비 분석해줘"
    goal = getattr(request, "analysis_goal", None) or ("reason" if request.explain else "overview")
    findings = summary["findings"]
    if goal == "habit":
        habits = [f for f in summary["candidates"] if f.get("habit") or f.get("persistence", {}).get("state") in ("sustained", "reverted")]
        if habits:
            findings = sorted(habits, key=lambda f: (f.get("persistence", {}).get("state") not in ("sustained", "reverted"), -f["impact"]))[:3]
            findings = [{**f, "conclusion": f["habit"] if f["kind"] == "usual" and f.get("habit") else f["conclusion"]} for f in findings]
    if goal == "reason" and summary["basis"]["comparison_available"]:
        delta = summary["current"]["amount"] - summary["previous"]["amount"]
        if delta <= 0 or not summary["total_material"]["meaningful"]:
            conclusion = (f"{summary['basis']['scope']} 전체 지출은 비교 기간보다 {won(abs(delta))} 줄었어요." if delta < -10000 else
                          f"{summary['basis']['scope']} 전체 지출이 의미 있게 늘었다고 볼 근거는 확인되지 않았어요.")
            findings = [dict(id="TOTAL", category=None, kind="total", impact=abs(delta), conclusion=conclusion,
                             evidence=[], outlook="", limits=[]), *findings[:2]]
    forecast, notes = _outlook(summary)
    if goal != "forecast" and all(f["kind"] in ("stable", "total") for f in findings) and summary.get("overall_trend", {}).get("state") != "ready":
        notes.append(forecast)
        forecast = None
    data = narrative.payload(question, findings, forecast, goal)
    selected = narrative.compose(data, complete)
    if selected is None:
        diagnosis = findings[0]["conclusion"]
        if goal == "forecast":
            selected = ([forecast], [f["conclusion"] for f in findings], [])
        else:
            evidence = [" ".join(f["evidence"]) for f in findings if f["evidence"]]
            for f in findings[1:]:
                if f["conclusion"] not in " ".join(evidence):
                    evidence.append(f["conclusion"])
            continuation = list(dict.fromkeys(f["outlook"] for f in findings if f["outlook"]))
            selected = ([diagnosis], evidence, [" ".join(t for t in [*continuation, forecast] if t)] if continuation or forecast else [])
        notes.insert(0, "자동으로 작성한 설명을 확인하지 못해, 계산으로 확인된 분석을 보여드려요.")
    basis = summary["basis"]
    important_limits = list(dict.fromkeys(t for f in findings for t in f["limits"]))
    notes.extend(important_limits)
    if basis["comparison_reason"]:
        notes.append(basis["comparison_reason"])
    unclassified = summary["unclassified_amount"]
    if any(unclassified):
        notes.append(f"미분류 금액: 이번 달 {won(unclassified[0])}, 비교 기간에 확인된 금액 {won(unclassified[1])}. 총액에는 포함하고 주요 카테고리에서는 제외했어요.")
    notes.append(f"분석 범위: {basis['scope']} · {span(this_month(today))} · 소비 패턴에서 고정지출 제외. 비교 기준은 지난달 같은 날짜까지예요.")
    for f in findings:
        threshold = f.get("material", {})
        if threshold.get("typical_change") is not None:
            name = NAMES.get(f.get("category"), basis["scope"])
            notes.append(f"{name} 개인 변동 기준: 과거 {threshold['history_months']}개월의 같은 시점 금액 차이 중간값 {won(round(threshold['typical_change']))}. 1만 원은 작은 금액을 과장하지 않기 위한 표시 하한이에요.")
    suffix = " · 일부 내역 기준" if basis["coverage"] != "complete" else ""
    blocks = [{"kind": "head", "title": f"{span(this_month(today))} · 소비 분석{suffix}"}]
    graph = chart(summary, today)
    for stage, title, lines in zip(STAGES, TITLES, selected):
        lines = list(dict.fromkeys(lines))
        if lines:
            blocks.append({"kind": "text", "title": title, "lines": [title, *lines]})
        if stage == "evidence" and graph:
            blocks.append(graph)
    return {"lines": list(dict.fromkeys(line for group in selected for line in group)), "lead": [],
            "notices": list(dict.fromkeys(notes)), "view": blocks, "examples": []}, None

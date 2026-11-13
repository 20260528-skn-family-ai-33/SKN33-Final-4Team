"""큰 결제를 분리한 주별 소비 추이와 조건부 월말 전망. 금액은 서버에서만 계산한다."""
import calendar
from collections import defaultdict
from datetime import timedelta
from statistics import median

from .periods import last_month

LOOKBACKS = (28, 56)
WEIGHTS = ("equal", "linear", "latest")
MIN_TUNING_MONTHS = 2
TEST_MONTHS = 2
MODEL_NAMES = {"average": "일평균", "weekday": "요일 패턴", "trend": "주별 추이"}
WEIGHT_NAMES = {"equal": "동일 가중치", "linear": "최근 주에 선형 가중치", "latest": "마지막 주만 반영"}


def history_months(today):
    """일반 조회의 3개월 한도와 별개인 내부 검증 범위. 외부 금융 조회를 요청하지 않는다."""
    months, end = [], today.replace(day=1)
    for _ in range(6):
        period = last_month(end)
        months.append(period)
        end = period.start
    return list(reversed(months))


def split_large(current, history):
    paid = [r["net_amount"] for r in (*history, *current) if r["net_amount"] > 0 and r["funding"] != "fixed"]
    typical = median(paid) if paid else 0
    # 큰 결제는 통계적 분리 대상일 뿐, 일회성 구매로 확정하지 않는다.
    threshold = max(100_000, typical * 3) if len(paid) >= 6 else 100_000
    def split(rows):
        large = [r for r in rows if r["funding"] != "fixed" and r["net_amount"] >= threshold]
        return large, [r for r in rows if r not in large]
    return split(current), split(history)


def _weekly(rows, end, days=28):
    start = end - timedelta(days=days - 1)
    weeks = [{"amount": 0, "count": 0, "dates": set(), "weekdays": [0] * 7} for _ in range(days // 7)]
    for row in rows:
        day, amount = row["occurred_on"], row["net_amount"]
        if start <= day <= end and amount > 0 and row["funding"] != "fixed":
            week = weeks[(day - start).days // 7]
            week["amount"] += amount
            week["count"] += 1
            week["dates"].add(day)
            week["weekdays"][day.weekday()] += amount
    return [{**w, "days": len(w["dates"]), "average": w["amount"] // w["count"] if w["count"] else None}
            for w in weeks]


def candidates():
    return [{"model": kind, "lookback_days": days, "weights": weights, "phi": phi}
            for days in LOOKBACKS for kind in MODEL_NAMES
            for weights in ((None,) if kind == "average" else WEIGHTS)
            for phi in ((None,) if kind != "trend" else tuple(n / 10 for n in range(11)))]


def _model(weeks, spec):
    amounts = [w["amount"] for w in weeks]
    if spec["model"] == "average":
        return {"daily_average": sum(amounts) / spec["lookback_days"]}
    weights = ([1] * len(weeks) if spec["weights"] == "equal" else list(range(1, len(weeks) + 1))
               if spec["weights"] == "linear" else [0] * (len(weeks) - 1) + [1])
    by_day = [sum(w["weekdays"][day] * weight for w, weight in zip(weeks, weights)) for day in range(7)]
    total = sum(by_day)
    slope = median(b - a for a, b in zip(amounts, amounts[1:])) if spec["model"] == "trend" else 0
    level = amounts[-1] if spec["model"] == "trend" else total / sum(weights)
    return {"level": level, "slope": slope, "phi": spec["phi"] or 0,
            "shares": [amount / total if total else 0 for amount in by_day]}


def _predict(model, horizon, weekday):
    if "daily_average" in model:
        return model["daily_average"]
    weeks, fraction = divmod(horizon, 7)
    phi = model["phi"]
    step = sum(phi ** n for n in range(1, weeks + 1)) + (fraction / 7) * phi ** (weeks + 1)
    return max(0, model["level"] + model["slope"] * step) * model["shares"][weekday]


def _remaining(weeks, spec, origin, end):
    model = _model(weeks, spec)
    return sum(_predict(model, h, (origin + timedelta(days=h)).weekday())
               for h in range(1, (end - origin).days + 1))


def _repeats(weeks):
    recent = weeks[-4:]
    return sum(w["days"] >= 2 for w in recent) >= 3 and sum(w["days"] for w in recent) >= 8 and recent[-1]["count"] > 0


def _folds(rows, today, months):
    """각 과거 월의 기준일을 현재와 월말까지 남은 일수가 같도록 잡는다."""
    remaining = calendar.monthrange(today.year, today.month)[1] - today.day
    tail, end = [], today.replace(day=1)
    for period in sorted(months, reverse=True):
        if period.end_before != end:
            break
        tail.append(period)
        end = period.start
    tail.reverse()
    folds = []
    for period in tail:
        origin = period.last_day - timedelta(days=remaining)
        if origin < period.start or origin - timedelta(days=max(LOOKBACKS) - 1) < end:
            continue
        visible = [r for r in rows if end <= r["occurred_on"] <= origin]
        (_, regular), _ = split_large(visible, [])
        weeks = {days: _weekly(regular, origin, days) for days in LOOKBACKS}
        if not _repeats(weeks[28]):
            continue
        observed = sum(r["net_amount"] for r in visible if period.start <= r["occurred_on"])
        actual = sum(r["net_amount"] for r in rows if period.start <= r["occurred_on"] < period.end_before)
        folds.append({"origin": origin, "end": period.last_day, "weeks": weeks, "observed": observed, "actual": actual})
    return folds


def _score(spec, folds):
    results = []
    for fold in folds:
        predicted = fold["observed"] + _remaining(fold["weeks"][spec["lookback_days"]], spec, fold["origin"], fold["end"])
        results.append({"as_of": fold["origin"].isoformat(), "actual_month_end": fold["actual"],
                        "predicted_month_end": round(predicted), "absolute_error": abs(predicted - fold["actual"])})
    mae = sum(r["absolute_error"] for r in results) / len(results)
    return mae, results


def _calibrate(rows, today, months):
    folds = _folds(rows, today, months)
    report = {"horizon_days": calendar.monthrange(today.year, today.month)[1] - today.day,
              "available_months": len(folds), "required_tuning_months": MIN_TUNING_MONTHS, "required_test_months": TEST_MONTHS}
    if len(folds) < MIN_TUNING_MONTHS + TEST_MONTHS:
        return {**report, "state": "insufficient", "reason": f"같은 남은 일수로 월말 검증을 할 수 있는 과거 기간이 {len(folds)}개월이에요. 방식 선택용 2개월과 별도 검증용 2개월이 확보되지 않아 예상액을 보류했어요."}
    tuning, testing = folds[:-TEST_MONTHS], folds[-TEST_MONTHS:]
    # 후보는 앞선 달의 MAE로만 선택한다. 뒤의 두 달은 선택에 사용하지 않는다.
    ranking = sorted([(spec, _score(spec, tuning)[0]) for spec in candidates()],
                     key=lambda x: (round(x[1], 6), list(MODEL_NAMES).index(x[0]["model"]),
                                    WEIGHTS.index(x[0]["weights"]) if x[0]["weights"] else -1,
                                    x[0]["lookback_days"], x[0]["phi"] or 0))
    selected, tuning_mae = ranking[0]
    baseline = next(spec for spec, _ in ranking if spec["model"] == "average")
    test_mae, test_results = _score(selected, testing)
    baseline_mae, _ = _score(baseline, testing)
    zero_mae = sum(abs(f["actual"] - f["observed"]) for f in testing) / len(testing)
    fixed_08 = {"model": "trend", "lookback_days": 28, "weights": "linear", "phi": 0.8}
    comparisons, seen = [], set()
    for spec, score in ranking:
        group = (spec["model"], spec["weights"])
        if group not in seen:
            comparisons.append({**spec, "tuning_mae": round(score), "test_mae": round(_score(spec, testing)[0])})
            seen.add(group)
    report.update(selected=selected, candidate_count=len(ranking), tuning_mae=round(tuning_mae), test_mae=round(test_mae),
                  baseline_mae=round(baseline_mae), no_future_spending_mae=round(zero_mae),
                  fixed_08_mae=round(_score(fixed_08, testing)[0]),
                  test_max_error=round(max(r["absolute_error"] for r in test_results)),
                  tuning_months=[f["origin"].isoformat()[:7] for f in tuning],
                  test_months=[f["origin"].isoformat()[:7] for f in testing], comparisons=comparisons,
                  test_results=[{**r, "absolute_error": round(r["absolute_error"])} for r in test_results])
    if round(test_mae, 6) > round(baseline_mae, 6) or round(test_mae, 6) >= round(zero_mae, 6):
        return {**report, "state": "failed", "reason": f"별도 {len(testing)}개월의 월말 검증 평균 오차가 {round(test_mae):,}원으로, 기준선보다 나은 예측을 확인하지 못해 예상액을 보류했어요."}
    return {**report, "state": "validated"}


def forecast(current, history, today, *, complete=True, months=()):
    """사용자·카테고리별 과거 월말 오차로 후보 선택 후, 분리한 두 달에서 검증한다."""
    if not complete:
        return {"state": "unavailable", "reason": "최근 4주 내역이 모두 수집되지 않아 월말 예상액은 계산하지 않았어요."}
    current = [r for r in current if r["funding"] != "fixed"]
    history = [r for r in history if r["funding"] != "fixed"]
    (large, regular), (_, prior_regular) = split_large(current, history)
    weeks = _weekly([*prior_regular, *regular], today)
    evidence = [{k: v for k, v in w.items() if k not in ("dates", "weekdays")} for w in weeks]
    delta = weeks[-1]["amount"] - weeks[0]["amount"]
    visible_change = abs(delta) >= 10000 and abs(delta) * 100 >= weeks[0]["amount"] * 10
    changes = [b["amount"] - a["amount"] for a, b in zip(weeks, weeks[1:])]
    direction = ("increase" if visible_change and delta > 0 and all(c >= 0 for c in changes) else
                 "decrease" if visible_change and delta < 0 and all(c <= 0 for c in changes) else "stable")
    base = {"weeks": evidence, "large_count": len(large), "large_amount": sum(r["net_amount"] for r in large), "weekly_direction": direction}
    if today.day == calendar.monthrange(today.year, today.month)[1]:
        return {**base, "state": "unavailable", "reason": "오늘이 월말이어서 예상액 대신 현재까지 확인한 실제 지출을 기준으로 봐주세요."}
    if sum(r["net_amount"] for r in current) < 0:
        return {**base, "state": "unavailable", "reason": "환불 영향으로 순지출이 음수여서 월말 예상액은 계산하지 않았어요."}
    if large and not any(w["count"] for w in weeks):
        return {**base, "state": "large_only", "remaining": 0,
                "reason": "큰 결제의 영향은 확인되지만 반복 소비가 없어 별도의 월말 예상액은 계산하지 않았어요."}
    if not _repeats(weeks):
        return {**base, "state": "unavailable", "reason": "최근 4주에 반복 소비가 충분히 관찰되지 않아 월말 예상액은 계산하지 않았어요."}
    validation = _calibrate([*history, *current], today, months)
    base["validation"] = validation
    if validation["state"] != "validated":
        return {**base, "state": "unavailable", "reason": validation["reason"]}
    spec = validation["selected"]
    fitted_weeks = _weekly([*prior_regular, *regular], today, spec["lookback_days"])
    last = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    future = _remaining(fitted_weeks, spec, today, last)
    observed = sum(r["net_amount"] for r in current)
    return {**base, "state": "ready", "observed": observed, "remaining": round(future),
            "month_end": round((observed + future) / 1000) * 1000,
            "model": spec["model"]}


def summarize(current, history, today, months, *, complete):
    current = [r for r in current if r["funding"] != "fixed"]
    history = [r for r in history if r["funding"] != "fixed"]
    grouped = [defaultdict(list), defaultdict(list)]
    for rows, groups in zip((current, history), grouped):
        for row in rows:
            groups[row["category"]].append(row)
    out = {}
    for code in sorted(set(grouped[0]) | set(grouped[1])):
        now, before = grouped[0][code], grouped[1][code]
        result = forecast(now, before, today, complete=complete, months=months)
        baseline_months = months[-3:]
        totals = [sum(r["net_amount"] for r in before if p.start <= r["occurred_on"] < p.end_before) for p in baseline_months]
        if totals:
            result["baseline"] = {"months": len(totals), "amount": round(median(totals)), "totals": totals,
                                  "periods": [p.start.isoformat()[:7] for p in baseline_months]}
        out[code] = result
    return out

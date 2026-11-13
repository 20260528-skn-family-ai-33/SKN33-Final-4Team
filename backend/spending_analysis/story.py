"""소비 분석 답의 뼈대: 소비 추이 → 소비 습관 → 앞으로 (11/13 사용자 확정 구성).

- 소비 추이: 완료된 지난 달들의 월 소비와 그 흐름(늘어남 · 줄어듦 · 비슷함), 매달 거의 고정인 항목과 달마다 출렁인 항목
- 소비 습관: 여러 달에 걸쳐 반복되는 소비 방식 (업종이 확인된 외식 · 카페 · 배달 · 장보기, 평일 점심 · 주말)
- 앞으로: 지금 패턴이 이어질 때 이번 달 말 · 다음 달 예상액과 생활비 예산 대비 차이, 먼저 넘을 항목
- 해볼 행동: 확인된 습관과 그 사람의 평균 결제액으로 계산되는 것만
- 다음에 해볼 것: 답을 보고 이어서 물을 만한 말 (버튼). 예산을 넘을 전망이면 예산 조정으로 바로 잇는다

원칙: 발견이 없으면 그 문장을 만들지 않는다 (판단 못 한 말 · 당연한 말 금지). 금액은 모두 서버 계산.
예산과 비교하는 금액은 예산과 같은 범위(생활비 카테고리, 고정지출 · 예산 밖 카테고리 제외)로만 센다.
"""
import calendar
from datetime import date, timedelta
from statistics import mean, median, pstdev

from budgets.scope import OUT_OF_BUDGET
from . import lifestyle, scope, trend
from .answer import NAMES, won
from .snapshot import josa

HABIT_KINDS = ("dining", "cafe", "delivery", "groceries")
HABIT_MIN_DAYS = 4          # 한 달에 이만큼 이상 결제한 달이
HABIT_MIN_SHARE = 0.75      # 완료된 달의 75% 이상이면 '매달 반복되는 습관'


def ieyo(word):
    """'식비예요' · '뷰티·미용이에요' (받침에 맞게)"""
    return word + ("이에요" if josa(word, "은는").endswith("은") else "예요")


def man(amount):
    """'114만 원' · '53만 원' · '8.5만 원' · '9,000원'. 100만 원 이상은 만 원 단위로 반올림"""
    amount = int(round(amount))
    if abs(amount) >= 1_000_000:
        return f"{round(amount / 10000):,}만 원"
    if abs(amount) >= 10000:
        return f"{round(amount / 10000, 1):g}만 원"
    return won(round(amount, -2))


def _living(rows, categories=None):
    """예산과 같은 범위: 고정지출 · 예산 밖 카테고리 · 환불(음수) 제외, 결제만"""
    return [r for r in rows if r["funding"] != "fixed" and r["category"] not in OUT_OF_BUDGET and r["category"] != "UNCLASSIFIED"
            and (not categories or r["category"] in categories)]  # '확인 필요'는 분석 대상이 아니다 (11/13 사용자 결정)


def _in(rows, period):
    return [r for r in rows if period.start <= r["occurred_on"] < period.end_before]


def _sum(rows):
    return sum(r["net_amount"] for r in rows)


def _by_category(rows):
    out = {}
    for r in rows:
        if r["category"] in NAMES and r["category"] != "UNCLASSIFIED":
            out[r["category"]] = out.get(r["category"], 0) + r["net_amount"]
    return out


def _finding(fid, kind, section, conclusion, evidence=(), **extra):
    return {"id": fid, "kind": kind, "section": section, "category": extra.pop("category", None), "subject": extra.pop("subject", "생활비"),
            "impact": extra.pop("impact", 0), "comparable": False, "conclusion": conclusion, "evidence": list(evidence),
            "outlook": "", "limits": [], "actions": extra.pop("actions", []), "story": True, **extra}


# ---------- 소비 추이 ----------

def monthly(history, months):
    """완료된 달마다 {'period', 'label', 'total', 'categories'} (최근 6개월 안, 수집이 완전한 달만)"""
    out = []
    for p in months:
        rows = _in(history, p)
        out.append({"period": p, "label": f"{p.start.month}월", "total": _sum(rows), "categories": _by_category(rows)})
    while out and out[0]["total"] <= 0:  # 소비 기록이 시작되기 전의 달은 0원 달로 세지 않는다
        out.pop(0)
    return out


def _direction(values):
    """월 소비 흐름. 처음과 끝의 차이가 평균의 10% 이상이고 대부분의 달이 같은 방향일 때만 늘어남 · 줄어듦"""
    if len(values) < 3:
        return "short"
    diffs = [b - a for a, b in zip(values, values[1:])]
    change = values[-1] - values[0]
    level = mean(values) or 1
    ups, downs = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
    # '매달 늘어요'라고 말하려면 거의 모든 달이 같은 방향이어야 한다 (오르내림을 추세로 부르지 않는다)
    allowed = 0 if len(diffs) <= 3 else 1
    if change >= level * 0.1 and ups >= len(diffs) - allowed:
        return "up"
    if change <= -level * 0.1 and downs >= len(diffs) - allowed:
        return "down"
    return "flat"


def trend_finding(series):
    if len(series) < 2:
        return None
    values = [m["total"] for m in series]
    path = " → ".join(f"{m['label']} {man(m['total'])}" for m in series)
    direction = _direction(values)
    if direction == "up":
        verdict = f"매달 늘고 있어요. {len(series)}개월 사이 {man(values[-1] - values[0])} 늘었어요."
    elif direction == "down":
        verdict = f"매달 줄고 있어요. {len(series)}개월 사이 {man(values[0] - values[-1])} 줄었어요."
    elif direction == "flat":
        verdict = "오르내림은 있지만 늘거나 줄어드는 추세는 아니에요."
    else:
        verdict = ""
    evidence = [f"{path}{'. ' + verdict if verdict else ''}"]
    # 매달 거의 고정인 큰 항목 vs 달마다 출렁인 항목
    codes = sorted({c for m in series for c in m["categories"]})
    stats = []
    for code in codes:
        amounts = [m["categories"].get(code, 0) for m in series]
        avg = mean(amounts)
        if avg <= 0:
            continue
        stats.append((code, amounts, avg, pstdev(amounts) / avg, pstdev(amounts)))
    total_avg = mean(values) or 1
    core = [s for s in stats if s[3] <= 0.15 and s[2] >= total_avg * 0.3 and min(s[1]) > 0]
    if core and len(series) >= 3:
        code, amounts = core[0][0], core[0][1]
        evidence.append(f"{josa(NAMES[code], '은는')} 매달 {man(min(amounts))}~{man(max(amounts))}으로 거의 고정이에요.")
    swing = sorted((s for s in stats if s not in core and s[4] >= 15000), key=lambda s: -s[4])[:2]
    if swing and direction in ("flat", "short") and len(series) >= 3:
        names = "·".join(NAMES[s[0]] for s in swing)
        evidence.append(f"달마다 달라진 건 {names}처럼 가끔 사는 항목이에요.")
    rising = [(code, amounts) for code, amounts, avg, *_ in stats
              if len(amounts) >= 3 and _direction(amounts) == "up" and amounts[-1] - amounts[0] >= max(20000, avg * 0.2)]
    if rising and direction != "up":
        code, amounts = max(rising, key=lambda x: x[1][-1] - x[1][0])
        evidence.append(f"{josa(NAMES[code], '은는')} {man(amounts[0])}에서 {man(amounts[-1])}으로 꾸준히 늘고 있어요.")
    lead = (f"지난 {len(series)}개월 한 달 생활비는 {man(min(values))}~{man(max(values))}으로 "
            + {"up": "점점 늘고 있어요.", "down": "점점 줄고 있어요.", "flat": "꾸준했어요.", "short": "이었어요."}[direction])
    if len(series) == 2:  # 두 달뿐이면 범위가 아니라 지난달이 그 전 달보다 어땠는지로 말한다
        a, b = series
        gap = b["total"] - a["total"]
        lead = (f"지난달({b['label']}) 생활비는 {josa(man(b['total']), '으로로')} {a['label']}({man(a['total'])})보다 {man(abs(gap))} "
                + ("늘었어요." if gap > 0 else "줄었어요.") if abs(gap) >= max(a["total"], b["total"]) * 0.1 else
                f"지난달({b['label']}) 생활비는 {josa(man(b['total']), '으로로')} {josa(a['label'] + '(' + man(a['total']) + ')', '과와')} 비슷했어요.")
        moved = sorted(((c, b["categories"].get(c, 0) - a["categories"].get(c, 0)) for c in {*a["categories"], *b["categories"]}),
                       key=lambda x: -abs(x[1]))
        moved = [(c, d) for c, d in moved if abs(d) >= 30000 and (d > 0) == (gap > 0)][:2]
        if moved and abs(gap) >= max(a["total"], b["total"]) * 0.1:
            evidence.append("가장 많이 달라진 항목은 " + ", ".join(f"{NAMES[c]}({'+' if d > 0 else '-'}{man(abs(d))})" for c, d in moved) + "예요.")
    return _finding("TREND", f"trend_{direction}", "소비 추이", lead, evidence, impact=total_avg, direction=direction)


# ---------- 소비 습관 ----------

def _habit_stats(history, series, current, today):
    """활동마다 완료된 달의 결제일 수 · 금액 · 식비 대비 비중, 평일 점심 비율"""
    stats = {}
    for kind in HABIT_KINDS:
        per_month = []
        for m in series:
            rows = [r for r in _in(history, m["period"]) if r.get("activity") == kind and r["net_amount"] > 0]
            days = len({r["occurred_on"] for r in rows})
            per_month.append({"days": days, "amount": _sum(rows), "rows": rows,
                              "category_total": m["categories"].get(lifestyle.INDUSTRIES[kind][0], 0)})
        months_with = [x for x in per_month if x["days"] >= HABIT_MIN_DAYS]
        if not series or len(months_with) < max(1, round(len(series) * HABIT_MIN_SHARE)):
            continue
        rows = [r for x in months_with for r in x["rows"]]
        lunch = sum(1 for r in rows if r["occurred_on"].weekday() < 5 and scope.slot_of(r["occurred_time"]) in ("lunch", "afternoon"))
        share = mean(x["amount"] / x["category_total"] for x in months_with if x["category_total"])
        stats[kind] = {"days": round(mean(x["days"] for x in months_with)), "amount": round(mean(x["amount"] for x in months_with)),
                       "average": round(_sum(rows) / len(rows)) if rows else 0, "share": share,
                       "weekday_lunch": lunch * 10 >= len(rows) * 7, "months": len(months_with), "of": len(series),
                       "category": lifestyle.INDUSTRIES[kind][0]}
    return stats


def _pct(ratio):
    value = round(ratio * 100 / 5) * 5
    return "거의 전부" if value >= 90 else "절반" if 45 <= value <= 55 else f"약 {value}%"


def habit_finding(history, series, current, today):
    stats = _habit_stats(history, series, current, today)
    if not stats:
        return None, {}
    ranked = sorted(stats.items(), key=lambda kv: -kv[1]["amount"])[:2]
    parts = [f"{'평일 점심 ' if s['weekday_lunch'] else ''}{lifestyle.LABELS[kind]}(월 {s['days']}일 안팎)" for kind, s in ranked]
    every = all(s["months"] == s["of"] for _, s in ranked)
    # 괄호가 모두 '안팎)'으로 끝나므로 조사는 받침 있는 형태(과 · 이)로 고정
    first = (f"{parts[0]}과 {parts[1]}" if len(parts) > 1 else parts[0]) + f"이 {'매달' if every else '대부분의 달에'} 반복돼요."
    evidence = [first]
    same_category = {s["category"] for _, s in ranked}
    if len(same_category) == 1:
        share = sum(s["share"] for _, s in ranked)
        if share >= 0.4:
            evidence[0] = first.rstrip(".") + f". {NAMES[same_category.pop()]}의 {josa(_pct(share), '이가')} {'이 둘이에요' if len(ranked) > 1 else '여기서 나와요'}."
    lead_kind, lead = ranked[0]
    names = f"{'평일 점심 ' if lead['weekday_lunch'] else ''}{lifestyle.LABELS[lead_kind]}"
    if len(ranked) > 1 and ranked[1][1]["category"] == lead["category"]:
        names = f"{josa(names, '과와')} {lifestyle.LABELS[ranked[1][0]]}"
    category = NAMES[lead["category"]]
    totals = [m["total"] for m in series if m["total"]]
    cat_share = mean(m["categories"].get(lead["category"], 0) / m["total"] for m in series if m["total"]) if totals else 0
    habit_share = sum(s["share"] for _, s in ranked if s["category"] == lead["category"])
    if cat_share >= 0.3 and habit_share >= 0.4:
        lead_text = f"생활비의 {josa(_pct(cat_share), '이가')} {category}이고, {category}의 {_pct(habit_share)}가 {ieyo(names)}."
    else:
        lead_text = f"{category}에서는 {names} 결제가 매달 반복돼요."
    return _finding("HABIT", "habit", "소비 습관", lead_text, evidence, category=lead["category"], impact=lead["amount"]), stats


def weekend_finding(history, series):
    if len(series) < 2:
        return None
    end_amount = day_amount = end_days = day_days = 0
    weekend_categories = {}
    for m in series:
        p = m["period"]
        for n in range((p.end_before - p.start).days):
            d = p.start + timedelta(days=n)
            if d.weekday() >= 5:
                end_days += 1
            else:
                day_days += 1
        for r in _in(history, p):
            if r["occurred_on"].weekday() >= 5:
                end_amount += r["net_amount"]
                weekend_categories[r["category"]] = weekend_categories.get(r["category"], 0) + r["net_amount"]
            else:
                day_amount += r["net_amount"]
    if not end_days or not day_days or day_amount <= 0:
        return None
    end_avg, day_avg = end_amount / end_days, day_amount / day_days
    top = [NAMES[c] for c, _ in sorted(weekend_categories.items(), key=lambda kv: -kv[1]) if c in NAMES and c != "UNCLASSIFIED"][:2]
    what = f"{'·'.join(top)} 위주로 " if top else ""
    if end_avg >= day_avg * 1.3:
        text = f"주말엔 {what}평일 하루의 {end_avg / day_avg:.1f}배를 써요 (주말 하루 {man(end_avg)}, 평일 하루 {man(day_avg)})."
    elif end_avg <= day_avg * 0.77:
        text = f"주말은 {what}평일보다 적게 써요 (주말 하루 {man(end_avg)}, 평일 하루 {man(day_avg)})."
    else:
        return None
    return _finding("WEEKEND", "weekend", "소비 습관", text, [text], impact=abs(end_avg - day_avg))


# ---------- 앞으로 ----------

def _usual(code, history, months):
    counts = [len([r for r in _in(history, p) if r["category"] == code and r["net_amount"] > 0]) for p in months]
    amounts = [_sum([r for r in _in(history, p) if r["category"] == code]) for p in months]
    return (median(counts) if counts else 0), (median(amounts) if amounts else 0)


def by_pattern(current, history, months, today, horizon_end=None, detail=None):
    """카테고리마다 소비 방식에 맞게 남은 기간을 예상한다 → (이번 달 말 합계, {코드: 예상}, 다음 달 합계)
    - 자주 쓰는 항목(한 달 3번 이상): 최근 4주 하루 평균이 남은 날에도 이어진다
    - 한 달에 한두 번 사는 항목: 평소 한 달 금액(완료된 달 중앙값)까지만 — 일찍 산 것을 매일 쓰는 것처럼 늘리지 않는다"""
    last = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    left = (last - today).days
    ny, nm, days_next = _next_month(today)
    codes = {r["category"] for r in (*current, *history)}
    month_end, per, next_total = 0, {}, 0
    for code in codes:
        spent = _sum([r for r in current if r["category"] == code])
        count, usual = _usual(code, history, months)
        recent = _sum([r for r in (*history, *current) if r["category"] == code and r["net_amount"] > 0
                       and today - timedelta(days=27) <= r["occurred_on"] <= today])
        if months and count <= 2:
            expected, nxt = max(spent, usual), usual
            way, daily = "occasional", None
        else:
            daily = recent / 28
            expected, nxt = spent + daily * left, daily * days_next
            way = "frequent"
        if detail is not None:  # '어떻게 계산했어?'에 항목별 계산식을 보여 주려고 남긴다
            detail[code] = {"way": way, "spent": spent, "daily": daily, "usual": usual, "expected": expected, "next": nxt}
        per[code] = expected
        month_end += expected
        next_total += nxt
    return month_end, per, next_total


def backtest(rows, months, day):
    """지난 완료된 달마다 같은 날짜까지만 보고 by_pattern 으로 예상해 실제 월말과 비교 → (달 수, 평균 차이). 앞선 달만 쓴다"""
    errors = []
    for i, p in enumerate(months):
        prior = months[max(0, i - 3):i]
        if len(prior) < 1:
            continue
        origin = min(p.start + timedelta(days=day - 1), p.last_day)
        current = [r for r in rows if p.start <= r["occurred_on"] <= origin]
        history = [r for r in rows if prior[0].start <= r["occurred_on"] < p.start]
        predicted, _, _ = by_pattern(current, history, prior, origin)
        errors.append(abs(predicted - _sum(_in(rows, p))))
    return len(errors), (mean(errors) if errors else None)


def project(current, history, today, months, complete, validated=None):
    """이번 달 말 예상. 검증된 방식(validated: trend.forecast 결과)이 있으면 그것을, 과거 월이 모자라 검증을 못 했으면
    최근 28일 하루 평균으로 계산한 참고치를 쓴다 (method 로 구분해 하단에 밝힌다). 검증에서 기준선보다 못하면 만들지 않는다"""
    if not complete:
        return None
    if months:  # 지난 달이 있으면 카테고리 소비 방식대로 예상하고, 지난 달들로 되짚어 맞춰 본다 (요구사항 6 · 7)
        detail = {}
        month_end, per, next_total = by_pattern(current, history, months, today, detail=detail)
        checked, error = backtest(history, months, today.day)
        (large, _), _ = trend.split_large(current, history)
        last = today.replace(day=calendar.monthrange(today.year, today.month)[1])
        return {"month_end": month_end, "observed": _sum(current), "remaining": month_end - _sum(current), "per": per,
                "next": next_total, "large": _sum(large), "method": "pattern", "checked": checked, "error": error,
                "detail": detail, "left": (last - today).days, "days_next": _next_month(today)[2], "usual_months": len(months)}
    result = validated if validated is not None else trend.forecast(current, history, today, complete=complete, months=months)
    if result.get("state") == "ready":
        return {"month_end": result["observed"] + result["remaining"], "observed": result["observed"], "remaining": result["remaining"],
                "large": result.get("large_amount", 0), "method": "validated", "validation": result["validation"]}
    validation = result.get("validation", {})
    if validation.get("state") == "failed" or not complete:
        return None
    (large, regular), (_, prior) = trend.split_large(current, history)
    weeks = trend._weekly([*prior, *regular], today)
    if sum(w["days"] for w in weeks) < 8:  # 최근 4주에 돈을 쓴 날이 8일도 안 되면 흐름이라 할 게 없다
        return None
    last = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    spec = {"model": "average", "lookback_days": 28, "weights": None, "phi": None}
    remaining = round(trend._remaining(weeks, spec, today, last)) if last > today else 0
    observed = _sum(current)
    return {"month_end": observed + remaining, "observed": observed, "remaining": remaining,
            "large": _sum(large), "method": "recent_average"}


def _next_month(today):
    y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return y, m, calendar.monthrange(y, m)[1]


def _category_month_end(code, current, history, months, today):
    """카테고리의 이번 달 말 예상. 자주 쓰는 항목은 최근 4주 하루 평균이 이어진다고 보고,
    한 달에 한두 번 사는 항목은 평소 한 달 금액(완료된 달의 중앙값)을 넘지 않게 본다 (일찍 산 것을 매일 쓰는 것처럼 늘리지 않는다)"""
    spent = _sum([r for r in current if r["category"] == code])
    last = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    recent = [r for r in (*history, *current) if r["category"] == code and today - timedelta(days=27) <= r["occurred_on"] <= today and r["net_amount"] > 0]
    monthly_counts = [len([r for r in _in(history, p) if r["category"] == code and r["net_amount"] > 0]) for p in months]
    usual = [ _sum([r for r in _in(history, p) if r["category"] == code]) for p in months]
    if monthly_counts and median(monthly_counts) <= 2:
        return max(spent, median(usual) if usual else spent)
    return spent + _sum(recent) / 28 * (last - today).days


def outlook_finding(projection, current, today, budget, habits, history=(), months=()):
    """이번 달 말 · 다음 달 예상과 예산 대비. budget = tools.budget_left 결과 (주기가 달력 달과 같을 때만 비교)"""
    if not projection:
        return None, None
    days_this = calendar.monthrange(today.year, today.month)[1]
    ny, nm, days_next = _next_month(today)
    month_end = int(round(projection["month_end"], -3))
    regular = projection["month_end"] - projection["large"]
    next_month = int(round(projection["next"] if projection.get("next") is not None else regular * days_next / days_this, -3))
    allocated = None
    if budget and budget.get("budget", {}).get("state") == "approved" and budget.get("budget", {}).get("allocated"):
        cycle = budget.get("cycle_period")
        if cycle and cycle.start == today.replace(day=1) and cycle.end_before == date(ny, nm, 1):
            allocated = budget["budget"]["allocated"]
    evidence, over_category = [], None
    if allocated:
        gap = month_end - allocated
        evidence.append(f"이번 달 말 약 {man(month_end)} (생활비 예산 {man(allocated)} 대비 {'+' if gap >= 0 else '-'}{man(abs(gap))})")
        next_gap = next_month - allocated
        evidence.append(f"다음 달({nm}월)도 지금처럼 쓰면 약 {man(next_month)}"
                        + (f", 예산보다 {man(next_gap)} 많아요." if next_gap > 10000 else
                           f", 예산 안이에요 (남는 돈 약 {man(-next_gap)})." if next_gap < -10000 else ", 예산과 비슷해요."))
        # 먼저 넘을 항목: 이번 달 쓴 돈 + 남은 기간 예상을 최근 비중대로 나눔
        cats = {c["category"]: c for c in budget.get("categories", [])}
        spent = _by_category(current)
        worst = None
        for code, info in cats.items():
            if not info["allocated"]:
                continue
            expected = projection["per"].get(code, 0) if projection.get("per") else _category_month_end(code, current, history, months, today)
            ratio = expected / info["allocated"]
            if ratio > 1.02 and expected - info["allocated"] >= 10000 and (worst is None or ratio > worst[1]):
                worst = (code, ratio, expected, info["allocated"])
        if worst:
            over_category = {"category": worst[0], "over_amount": round(worst[2] - worst[3], -3)}
            evidence.append(f"{'가장 먼저 넘을' if month_end > allocated else '예산을 넘을 것 같은'} 항목은 {ieyo(NAMES[worst[0]])} (예상 {man(worst[2])} / 예산 {man(worst[3])}).")
        lead = (f"이 흐름이면 이번 달은 약 {man(month_end)}, 생활비 예산({man(allocated)})보다 {man(gap)}쯤 넘을 거예요." if gap > 10000 else
                f"이 흐름이면 이번 달은 약 {josa(man(month_end), '으로로')} 생활비 예산({man(allocated)}) 안에서 끝날 거예요." if gap < -10000 else
                f"이 흐름이면 이번 달은 약 {josa(man(month_end), '으로로')} 생활비 예산({man(allocated)})과 비슷하게 끝나요.")
    else:
        evidence.append(f"이번 달 말 약 {man(month_end)} (지금까지 {man(projection['observed'])})")
        evidence.append(f"다음 달({nm}월)도 지금처럼 쓰면 약 {man(next_month)}이에요.")
        lead = f"이 흐름이면 이번 달 생활비는 약 {man(month_end)}이 될 거예요."
    finding = _finding("FORECAST", "forecast", "앞으로", lead, evidence, impact=month_end,
                       month_end=month_end, next_month=next_month, allocated=allocated, over=over_category, next_label=f"{nm}월")
    return finding, over_category


def actions(habits, outlook):
    """확인된 습관과 평균 결제액으로 계산되는 행동만. 예산을 넘을 전망이면 초과분과 비교해 준다"""
    out = []
    gap = None
    if outlook and outlook.get("allocated"):
        gap = outlook["month_end"] - outlook["allocated"]
    plans = {"dining": ("점심 외식을 주 1회 도시락으로 바꾸면", 4), "delivery": ("배달을 주 1회 집밥으로 바꾸면", 4),
             "cafe": ("카페·간식을 주 2회 건너뛰면", 8)}
    for kind, s in sorted(habits.items(), key=lambda kv: -kv[1]["amount"]):
        if kind not in plans or not s["average"]:
            continue
        text, times = plans[kind]
        if kind == "dining" and not s["weekday_lunch"]:
            text = "외식을 주 1회 집밥으로 바꾸면"
        saved = s["average"] * times
        line = f"{text} 한 달 약 {man(saved)} (평균 {man(s['average'])} × {times}회)"
        if gap and gap > 0:
            ratio = saved / gap
            line += " — " + ("이번 달 초과분을 다 메울 수 있어요." if ratio >= 1 else
                             f"이번 달 초과분의 {'절반 정도' if ratio >= 0.4 else '일부'}예요.")
        out.append(line + ("" if line.endswith(".") else "."))
        if len(out) == 2:
            break
    return out


def chart(series, projection, outlook, today):
    """월별 막대: 완료된 달(실제) + 이번 달(쓴 돈 + 남은 기간 예상) + 다음 달(예상). 예산선은 예산과 비교할 수 있을 때만"""
    if not series and not projection:
        return None
    bars = [{"label": m["label"], "actual": m["total"], "projected": 0, "kind": "actual"} for m in series]
    if projection:
        bars.append({"label": f"{today.month}월", "actual": projection["observed"], "projected": round(max(projection["month_end"] - projection["observed"], 0), -3),
                     "kind": "current"})
        if outlook:
            bars.append({"label": outlook["next_label"], "actual": 0, "projected": int(outlook["next_month"]), "kind": "next"})
    if len(bars) < 2:
        return None
    return {"kind": "months", "title": "월별 생활비", "bars": bars, "budget": outlook.get("allocated") if outlook else None,
            "basis": "생활비 카테고리 결제 기준 · 고정지출·예산 밖 지출 제외 · 진한 막대 = 실제, 빗금 = 지금 흐름이 이어질 때 예상"}


def examples(over, habits, top_category):
    """답을 보고 이어서 물을 만한 말 (버튼을 누르면 그 문장이 그대로 질문이 된다). 예산 조정은 따로 [예산 조정하기]로"""
    out = []
    if top_category:
        out.append(f"이번 달 {NAMES[top_category]} 자세히 보여줘")
    out.append("지난달이랑 비교해줘")
    if "dining" in habits or "cafe" in habits:
        out.append("외식이랑 카페에 얼마 썼어?" if "dining" in habits and "cafe" in habits else
                   "외식에 얼마 썼어?" if "dining" in habits else "카페에 얼마 썼어?")
    return out[:3]


def build(current_all, history_all, months, today, *, complete, budget, categories=None, validated=None):
    """→ {"findings": [...], "chart": {...}|None, "actions": [...], "examples": [...], "over": {...}|None, "method": str|None,
           "series": [...], "habits": {...}}
    current_all · history_all 은 고정지출이 빠진 소비 결제(범위 필터 전). 여기서 예산과 같은 생활비 범위로 다시 거른다"""
    current, history = _living(current_all, categories), _living(history_all, categories)
    series = monthly(history, months)
    months = [m["period"] for m in series]  # 소비 기록이 있는 완료된 달만 (그 전의 빈 달은 평소 금액 계산에서 뺀다)
    findings = []
    trend_f = trend_finding(series)
    if trend_f:
        findings.append(trend_f)
    habit_f, habits = habit_finding(history, series, current, today)
    if habit_f:
        findings.append(habit_f)
    weekend_f = weekend_finding(history, series)
    if weekend_f:
        findings.append(weekend_f)
    projection = project(current, history, today, months, complete, validated)
    outlook, over = outlook_finding(projection, current, today, budget, habits, history, months)
    if outlook:
        findings.append(outlook)
    spent = _by_category(current) or (series[-1]["categories"] if series else {})
    top = max(spent, key=spent.get) if spent else None
    return {"findings": findings, "chart": chart(series, projection, outlook, today), "actions": actions(habits, outlook),
            "examples": examples(over, habits, top), "over": over, "method": projection and projection["method"],
            "projection": projection and {k: v for k, v in projection.items() if k != "validation"} | (
                {"validation": projection["validation"]} if projection.get("validation") else {}),
            "series": [{"label": m["label"], "total": m["total"]} for m in series], "habits": habits,
            "habit_rows": habit_rows(habits), "slot": slot_line(history), "weekend": weekend_f and weekend_f["conclusion"],
            "forecast_rows": forecast_rows(projection, budget, today)}


# ---------- 메뉴별 답에만 쓰는 재료 (11/14: 네 메뉴가 같은 내용을 순서만 바꿔 보이지 않게) ----------

def habit_rows(habits):
    """소비 습관 답: 반복되는 소비마다 한 달 금액 · 횟수 · 한 번 평균"""
    ranked = sorted(habits.items(), key=lambda kv: -kv[1]["amount"])
    top = ranked[0][1]["amount"] if ranked else 1
    return [{"name": ("평일 점심 " if s["weekday_lunch"] else "") + lifestyle.LABELS[kind], "value": f"월 {_m(s['amount'])}",
             "note": f"월 {s['days']}일 · 한 번 {_m(s['average'])}", "ratio": round(s["amount"] / top * 100)} for kind, s in ranked]


def slot_line(history):
    """언제 쓰나요: 결제가 한 시간대에 몰려 있으면 그 시간대와 주로 쓰는 항목 (결제 20건 미만 · 고르게 퍼져 있으면 없음)"""
    paid = [r for r in history if r["net_amount"] > 0 and r.get("occurred_time")]
    if len(paid) < 20:
        return None
    counts = {}
    for r in paid:
        code = scope.slot_of(r["occurred_time"])
        counts[code] = counts.get(code, 0) + 1
    code, n = max(counts.items(), key=lambda kv: kv[1])
    share = n / len(paid)
    if share < 0.35:
        return None
    name, start, end = scope.SLOTS[code]
    in_slot = {}
    for r in paid:
        if scope.slot_of(r["occurred_time"]) == code:
            in_slot[r["category"]] = in_slot.get(r["category"], 0) + 1
    cat, k = max(in_slot.items(), key=lambda kv: kv[1])
    where = f"{name}({start}~{end + 1}시)"
    if cat in NAMES and k / n >= 0.5:
        return f"결제의 {josa(_pct(share), '이가')} {where}에 몰려 있고, 그중 {josa(_pct(k / n), '이가')} {ieyo(NAMES[cat])}."
    return f"결제의 {josa(_pct(share), '이가')} {where}에 몰려 있어요."


def forecast_rows(projection, budget, today):
    """앞으로 답: 항목별 이번 달 말 예상 — 예산이 달력 달과 같으면 예산 대비, 아니면 평소(지난 몇 달 가운데 값) 대비"""
    detail = (projection or {}).get("detail")
    if not detail:
        return []
    ny, nm, _ = _next_month(today)
    alloc = {}
    cycle = (budget or {}).get("cycle_period")
    if cycle and cycle.start == today.replace(day=1) and cycle.end_before == date(ny, nm, 1):
        alloc = {c["category"]: c["allocated"] for c in budget.get("categories", []) if c.get("allocated")}
    items = sorted(((c, d) for c, d in detail.items() if d["expected"] >= 1000 and c in NAMES), key=lambda x: -x[1]["expected"])[:5]
    top = items[0][1]["expected"] if items else 1
    rows = []
    for c, d in items:
        expected, direction = round(d["expected"], -3), None
        if alloc.get(c):
            ratio = expected / alloc[c]
            note = f"예산 {_m(alloc[c])}의 {round(ratio * 100)}%" + (" · 넘을 전망" if ratio > 1.02 else "")
            direction = "up" if ratio > 1.02 else None
        elif d["usual"]:
            gap = expected - d["usual"]
            if abs(gap) >= max(10000, d["usual"] * 0.15):
                note, direction = f"평소({_m(d['usual'])})보다 {'+' if gap > 0 else '-'}{_m(abs(gap))}", ("up" if gap > 0 else "down")
            else:
                note = f"평소({_m(d['usual'])})와 비슷"
        else:
            note = "평소 기록 없음"
        rows.append({"name": NAMES[c], "value": _m(expected), "note": note, "ratio": round(d["expected"] / top * 100),
                     **({"direction": direction} if direction else {})})
    return rows


# ---------- 계산 방법 ('왜 이렇게 계산됐어?') ----------

def _m(amount):
    """표 안의 짧은 금액: '9.6만' · '2,700원'"""
    text = man(amount)
    return text[:-2] if text.endswith("만 원") else text


def calculation(st, today):
    """방금 보인 숫자가 어떻게 나왔는지 — 화면에는 요약 · 방식 한 줄 · 항목별 표만, 나머지 기준은 하단 안내로.
    → {"lead": [...], "lines": [...], "rows": [{"name", "value", "note", "ratio"}], "notes": [...]} | None"""
    projection, series = st.get("projection"), st.get("series") or []
    notes = []
    if series:
        notes.append("월별 생활비 = 생활비 카테고리 결제를 달마다 더한 금액 ("
                     + " · ".join(f"{m['label']} {man(m['total'])}" for m in series) + "). 고정지출·예산 밖 지출·'확인 필요' 결제는 뺐어요.")
    if not projection:
        return {"lead": ["월별 생활비는 생활비 카테고리 결제를 달마다 더한 금액이에요."], "lines": [], "rows": [], "notes": notes} if series else None
    month_end, observed = int(round(projection["month_end"], -3)), projection["observed"]
    ny, nm, days_next = _next_month(today)
    detail = projection.get("detail")
    if projection["method"] != "pattern" or not detail:
        return {"lead": [f"이번 달 말 약 {man(month_end)} = 지금까지 쓴 {man(observed)} + 남은 기간 예상 {man(max(month_end - observed, 0))}"],
                "lines": ["최근 4주 동안 하루 평균 쓴 돈이 남은 날에도 이어진다고 보고 더했어요."], "rows": [], "notes": notes}
    left, next_month = projection["left"], int(round(projection["next"], -3))
    lead = [f"이번 달 말 약 {man(month_end)} = 지금까지 쓴 {man(observed)} + 남은 {left}일 예상 {man(max(month_end - observed, 0))}",
            f"다음 달({nm}월) 약 {man(next_month)} = 지금 속도로 {days_next}일을 쓴다고 본 금액"]
    lines = ["자주 쓰는 항목은 최근 4주 하루 평균으로, 한 달에 한두 번 사는 항목은 평소 한 달 금액까지만 넣었어요."]
    extra = sorted(((c, d["spent"] - d["usual"]) for c, d in detail.items() if d["way"] == "occasional" and d["spent"] - d["usual"] >= 10000),
                   key=lambda x: -x[1])
    if next_month < month_end and extra:  # 다음 달이 이번 달보다 적게 나온 이유 (가끔 사는 것을 이번 달에 평소보다 많이 샀다)
        lines.append(f"다음 달이 더 적은 건 이번 달에 {josa('·'.join(NAMES[c] for c, _ in extra[:2]), '을를')} 평소보다 많이 샀기 때문이에요.")
    items = sorted(((c, d) for c, d in detail.items() if d["expected"] >= 1000), key=lambda x: -x[1]["expected"])
    top = items[0][1]["expected"] if items else 1
    rows = []
    for c, d in items[:4]:
        if d["way"] == "frequent":
            note = f"{_m(d['spent'])} + 하루 {_m(round(d['daily'], -2))}×{left}일"
        elif d["spent"] >= d["usual"] and d["spent"] > 0:
            note = f"이미 {_m(d['spent'])} 씀"
        else:
            note = f"평소 한 달 {_m(d['usual'])}"
        rows.append({"name": NAMES[c], "value": f"{_m(round(d['expected'], -3))}", "note": note,
                     "ratio": round(d["expected"] / top * 100)})
    if len(items) > 4:
        rest = items[4:]
        rows.append({"name": f"그 밖 {len(rest)}개", "value": _m(round(sum(d["expected"] for _, d in rest), -3)), "note": None,
                     "ratio": round(sum(d["expected"] for _, d in rest) / top * 100)})
    if projection.get("checked"):
        notes.append(f"정확도: 같은 방식으로 지난 {projection['checked']}개월을 {today.day}일 기준으로 계산해 보니 실제 월말과 평균 "
                     f"{man(int(round(projection['error'], -3)))} 차이가 났어요.")
    if projection.get("usual_months"):
        notes.append(f"평소 한 달 금액 = 지난 {projection['usual_months']}개월 금액의 가운데 값.")
    notes.append("앞으로 새로 생길 큰 결제는 예상에 넣지 않았어요.")
    return {"lead": lead, "lines": lines, "rows": rows, "notes": notes}

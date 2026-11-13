"""거래 기록의 설명 후보 검증. 서로 겹치는 분해는 별개 장부로 보존한다."""
from collections import Counter, defaultdict
from datetime import timedelta
from fractions import Fraction as F
from itertools import permutations
from statistics import median

from . import lifestyle, scope, trend
from .answer import NAMES, won


def metrics(rows):
    paid = [r for r in rows if r["net_amount"] > 0]
    return dict(amount=sum(r["net_amount"] for r in rows), count=len(paid),
                days=len({r["occurred_on"] for r in paid}), paid=sum(r["net_amount"] for r in paid))


def _type(row):
    return row.get("activity") or "unknown"


def _change(amount):
    return f"{won(abs(amount))} {'늘었' if amount > 0 else '줄었'}어요" if amount else "달라지지 않았어요"


def _rounded(parts, total):
    out = {k: round(v) for k, v in parts.items()}
    if out:
        out[max(parts, key=lambda k: abs(parts[k]))] += total - sum(out.values())
    assert sum(out.values()) == total
    return out


def decompose(current, previous):
    """D(결제일 수)×Q(결제일당 건수)×P(평균액)의 Shapley 분해 + 유형 구성/동일 유형 금액.

    6개 적용 순서의 평균으로 교차효과를 한 번만 배분한다. 신규 유형의 과거 가격은
    관측되지 않았으므로 그 유형의 현재 금액을 써 구성 변화로만 처리한다.
    """
    a, b = metrics(current), metrics(previous)
    delta = a["amount"] - b["amount"]
    if not a["count"] or not b["count"]:
        return {"available": False, "delta": delta, "reason": "양쪽 기간의 양수 결제가 필요해요."}
    old = (F(b["days"]), F(b["count"], b["days"]), F(b["paid"], b["count"]))
    new = (F(a["days"]), F(a["count"], a["days"]), F(a["paid"], a["count"]))
    effects = [F(0)] * 3
    for order in permutations(range(3)):
        state = list(old)
        for index in order:
            before = state[0] * state[1] * state[2]
            state[index] = new[index]
            effects[index] += (state[0] * state[1] * state[2] - before) / 6
    grouped = []
    for rows in (previous, current):
        acc = defaultdict(lambda: [0, 0])
        for r in rows:
            if r["net_amount"] > 0:
                acc[_type(r)][0] += 1
                acc[_type(r)][1] += r["net_amount"]
        grouped.append(acc)
    mix = within = F(0)
    type_deltas = {}
    for kind in sorted(set(grouped[0]) | set(grouped[1])):
        c0, v0 = grouped[0][kind]
        c1, v1 = grouped[1][kind]
        p0, p1 = F(v0, c0) if c0 else F(v1, c1), F(v1, c1) if c1 else F(v0, c0)
        s0, s1 = F(c0, b["count"]), F(c1, a["count"])
        mix += (s1 - s0) * (p0 + p1) / 2
        within += (p1 - p0) * (s0 + s1) / 2
        type_deltas[kind] = v1 - v0
    multiplier = old[0] * old[1] / 3 + (new[0] * old[1] + old[0] * new[1]) / 6 + new[0] * new[1] / 3
    refunds = (a["amount"] - a["paid"]) - (b["amount"] - b["paid"])
    exact = dict(payment_days=effects[0], payments_per_day=effects[1],
                 type_mix=mix * multiplier, within_type_amount=within * multiplier, refunds=F(refunds))
    assert sum(exact.values()) == delta
    parts = _rounded(exact, delta)
    opposed = any(v * delta < 0 for v in parts.values())
    # 상쇄 또는 소액 순변화에서는 기여율을 만들지 않는다.
    shares = ({k: round(v * 100 / delta, 1) for k, v in parts.items()}
              if abs(delta) >= 10000 and not opposed else None)
    return dict(available=True, delta=delta, parts=parts, shares=shares, offsets=opposed,
                type_deltas=type_deltas, reconciled=sum(parts.values()) == delta,
                before=dict(days=b["days"], count=b["count"]), after=dict(days=a["days"], count=a["count"]))


def calendar_effect(current, previous, periods):
    counts = [Counter((p.start + timedelta(days=n)).weekday()
                     for n in range((p.end_before - p.start).days)) for p in periods]
    amounts = [Counter() for _ in periods]
    for acc, rows in zip(amounts, (current, previous)):
        for r in rows:
            acc[r["occurred_on"].weekday()] += r["net_amount"]
    exposure = behavior = F(0)
    for day in range(7):
        n1, n0 = counts[0][day], counts[1][day]
        if not n0 or not n1:
            return {"available": False}
        r1, r0 = F(amounts[0][day], n1), F(amounts[1][day], n0)
        exposure += (n1 - n0) * (r1 + r0) / 2
        behavior += (r1 - r0) * (n1 + n0) / 2
    delta = metrics(current)["amount"] - metrics(previous)["amount"]
    return {"available": True, **_rounded(dict(calendar=exposure, behavior=behavior), delta)}


def persistence(current, history, today, months, window_complete):
    """완전 수집된 7일 구간끼리 비교. 한 주 급증과 지속 신호를 분리한다."""
    (_, regular), (_, past) = trend.split_large(current, history)
    weeks = trend._weekly([*past, *regular], today, 28)
    if not window_complete:
        return {"state": "unknown", "reason": "최근 주별 수집 범위가 불완전해 지속 여부는 판단하지 않았어요."}
    baseline = []
    end = today - timedelta(days=28)
    first = min((p.start for p in months), default=end)
    while end - timedelta(days=6) >= first:
        start = end - timedelta(days=6)
        if all(any(p.start <= start + timedelta(days=n) < p.end_before for p in months) for n in range(7)):
            baseline.append(trend._weekly(past, end, 7)[0]["amount"])
        end -= timedelta(days=7)
    values = [w["amount"] for w in weeks]
    if len(baseline) < 4:
        return {"state": "unknown", "weeks": values, "reason": "평소 주별 변동 범위를 확인할 과거 자료가 부족해요."}
    low, high = min(baseline), max(baseline)
    state = ("sustained" if all(v > high for v in values[-3:]) else
             "reverted" if any(v > high for v in values[:-1]) and low <= values[-1] <= high else
             "usual" if all(low <= v <= high for v in values) else "mixed")
    return dict(state=state, weeks=values, baseline_range=[low, high], baseline_weeks=len(baseline))


def material(delta, historical_amounts, reference=0):
    """기존 1만 원 바닥은 표시 영향 한도. 개인 변동폭은 과거 월간 절대차의 중간값."""
    variation = median(abs(b - a) for a, b in zip(historical_amounts, historical_amounts[1:])) if len(historical_amounts) >= 4 else None
    floor = max(10000, variation if variation is not None else abs(reference) / 10)
    return {"meaningful": abs(delta) >= floor, "typical_change": variation,
            "history_months": len(historical_amounts), "amount_floor": 10000,
            "relative_floor_used": variation is None}


def analyze(current, previous, history, periods, months, *, comparable, window_complete):
    """모든 후보 검증 후 금액 영향·지속성·중복으로 발견을 고른다."""
    candidates = []
    total = metrics(current)["amount"] - metrics(previous)["amount"]
    unclassified = [sum(r["net_amount"] for r in rows if r["category"] == "UNCLASSIFIED") for rows in (current, previous)]
    classification_changed = comparable and any(unclassified) and F(unclassified[0], max(1, metrics(current)["paid"])) != F(unclassified[1], max(1, metrics(previous)["paid"]))
    categories = sorted({r["category"] for rows in (current, previous) for r in rows if r["category"] in NAMES and r["category"] != "UNCLASSIFIED"})
    for code in categories:
        now, before, past = ([r for r in rows if r["category"] == code] for rows in (current, previous, history))
        a, b = metrics(now), metrics(before)
        if not a["count"] and not b["count"]:
            continue
        (large, regular), (past_large, past_regular) = trend.split_large(now, past)
        before_large = [r for r in past_large if periods[1].start <= r["occurred_on"] < periods[1].end_before]
        before_regular = [r for r in past_regular if periods[1].start <= r["occurred_on"] < periods[1].end_before]
        if not past:
            (large, regular), (before_large, before_regular) = trend.split_large(now, before)
        historical = []
        for p in months:
            if (p.end_before - p.start).days < (periods[0].end_before - periods[0].start).days:
                continue
            end = min(p.start + timedelta(days=(periods[0].end_before - periods[0].start).days), p.end_before)
            historical.append(sum(r["net_amount"] for r in past_regular if p.start <= r["occurred_on"] < end))
        delta = a["amount"] - b["amount"]
        threshold = material(delta, historical, b["amount"])
        continuation = persistence(now, past, periods[0].last_day, months, window_complete)
        decomposition = decompose(regular, before_regular) if comparable else {"available": False}
        calendar = calendar_effect(regular, before_regular, periods) if comparable else {"available": False}
        large_delta = metrics(large)["amount"] - metrics(before_large)["amount"]
        regular_delta = metrics(regular)["amount"] - metrics(before_regular)["amount"]
        if comparable:
            assert large_delta + regular_delta == delta
        large_months = len({r["occurred_on"].strftime("%Y-%m") for r in past_large})
        limits = []
        if not comparable:
            limits.append("수집된 내역에서 보이는 패턴이며 전체 소비의 증감 원인은 아니에요.")
        if classification_changed:
            limits.append("미분류 비중도 달라져 카테고리 변화는 분류된 내역에 한정해 해석했어요.")
        name = NAMES[code]
        evidence = []
        kind = "observed"
        diagnosis = f"확인된 {name} 지출은 {won(a['amount'])}이에요."
        if comparable:
            direction = "늘었" if delta > 0 else "줄었"
            diagnosis = f"분류된 {name} 지출이 {won(abs(delta))} {direction}어요."
            if abs(large_delta) >= 10000 and large_delta * delta > 0 and not material(regular_delta, historical, metrics(before_regular)["amount"])["meaningful"]:
                kind = "large_payment"
                diagnosis = f"{name} 변화는 큰 결제의 영향이 크고, 나머지 소비에서는 뚜렷한 변화가 확인되지 않았어요."
            elif calendar.get("available") and abs(calendar["calendar"]) >= 10000 and abs(calendar["behavior"]) < 10000:
                kind = "calendar"
                diagnosis = f"{name} 합계 차이는 요일 수의 차이로 설명되며, 요일별 소비 강도는 비슷해요."
            elif decomposition.get("available") and threshold["meaningful"]:
                parts = decomposition["parts"]
                dominant = max(parts, key=lambda k: abs(parts[k]))
                kind = dominant
                if dominant == "within_type_amount" and ("unknown" in decomposition["type_deltas"]):
                    kind = "average_unknown"
                if not decomposition["offsets"] and parts[dominant] * delta > 0 and abs(parts[dominant]) * 2 >= abs(delta):
                    descriptions = {"payment_days": "결제한 날짜 수가 달라진 점", "payments_per_day": "소비한 날에 결제하는 횟수가 달라진 점",
                                    "type_mix": "돈을 쓰는 소비 유형의 비중이 달라진 점", "within_type_amount": "같은 소비 유형에서 한 번 결제하는 금액이 달라진 점", "refunds": "환불 금액이 달라진 점"}
                    diagnosis = f"{name} 지출이 달라진 주된 이유는 {descriptions[dominant]}이에요."
                else:
                    diagnosis = f"{name}에서는 증가·감소 요인이 함께 작용해 하나의 주된 요인으로 단정하기 어려워요."
                if kind == "average_unknown":
                    diagnosis = f"{name}의 건당 평균은 달라졌지만 업종 정보가 부족해 같은 유형의 금액 변화와 소비 구성 변화를 구분하기 어려워요."
            elif not threshold["meaningful"]:
                kind = "usual"
                diagnosis = (f"{name} 차이는 과거에 관측된 변동 폭 안에 있어 뚜렷한 습관 변화로 보기 어려워요." if threshold["typical_change"] is not None else
                             f"{name}에서는 금액 영향이 큰 변화가 확인되지 않았어요.")
            if not (large and metrics(large)["amount"] == a["amount"] and b["amount"] == 0):
                evidence.append(f"이번 기간 {name} 금액은 {won(a['amount'])}, 비교 기간 {name} 금액은 {won(b['amount'])}이에요.")
            if large or before_large:
                head = (f"큰 결제 {len(large)}건({won(metrics(large)['amount'])})이 이번 기간에 확인됐고" if not before_large else
                        f"큰 결제 금액 차이는 {won(large_delta)}이고")
                evidence.append(f"{head}, 큰 결제를 제외한 차이는 {won(regular_delta)}이에요.")
            if decomposition.get("available") and threshold["meaningful"] and kind not in ("calendar", "large_payment"):
                labels = {"payment_days": "결제한 날짜 수가 달라진 영향", "payments_per_day": "소비한 날의 결제 횟수가 달라진 영향",
                          "type_mix": "소비 유형별 비중이 달라진 영향", "within_type_amount": "같은 유형에서 한 번 결제하는 금액이 달라진 영향", "refunds": "환불 금액이 달라진 영향"}
                if "unknown" in decomposition["type_deltas"]:
                    labels["within_type_amount"] = "업종 미확인 내역을 포함한 유형별 평균 결제금액이 달라진 영향"
                nonzero = [(k, v) for k, v in decomposition["parts"].items() if v]
                evidence.extend(f"{labels[k]}으로 {won(abs(v))} {'증가' if v > 0 else '감소'}한 것으로 계산돼요." for k, v in nonzero)
        elif len({r["occurred_on"] for r in regular if r["net_amount"] > 0}) >= 3:
            diagnosis = f"확인된 {name} 결제는 여러 날짜에 걸쳐 있지만 평소보다 늘었는지는 아직 판단하기 어려워요."
        if large:
            if not comparable:
                evidence.append(f"큰 결제 {len(large)}건({won(metrics(large)['amount'])})을 분리했어요.")
            if large_months >= 3:
                evidence.append(f"이 카테고리의 큰 결제가 과거 {large_months}개 월에도 보여 일회성이라고 확정하지 않았어요.")
                dates = sorted({r["occurred_on"] for r in past_large})
                gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
                evidence.append(f"과거 큰 결제 사이 간격은 {min(gaps)}~{max(gaps)}일이었어요. 같은 구매인지까지 확인된 것은 아니에요.")
        types = defaultdict(lambda: [0, 0])
        for side, rows in enumerate((before_regular, regular)):
            for row in rows:
                types[_type(row)][side] += row["net_amount"]
        typed = sorted(((k, v[1] - v[0]) for k, v in types.items() if k != "unknown"), key=lambda kv: -abs(kv[1]))
        if comparable and not threshold["meaningful"] and not any(types.get("unknown", ())) and any(v >= 10000 for _, v in typed) and any(v <= -10000 for _, v in typed):
            kind = "type_offset"
            diagnosis = f"{name} 총액은 비슷하지만 확인된 소비 유형 사이의 증가와 감소가 상쇄돼 구성이 달라졌어요."
            evidence.append("같은 총액을 유형별로 나누면 " + ", ".join(f"{lifestyle.LABELS[k]} {v:+,}원" for k, v in typed if abs(v) >= 10000) + "이에요.")
        main_type = None
        if comparable and typed and threshold["meaningful"]:
            key, contribution = typed[0]
            other = regular_delta - contribution
            can_dominate = contribution * delta > 0 and (delta - contribution) * delta >= 0 and abs(contribution) * 2 >= abs(delta) and not classification_changed
            main_type = key if can_dominate else None
            evidence.append(f"{lifestyle.LABELS[key]} 관련 지출은 {_change(contribution)}. 그 밖의 소비 유형은 {_change(other)}.")
        # 시간대는 같은 증감의 다른 분할이다. 활동 이름으로 바꾸거나 금액 분해와 더하지 않는다.
        slots = defaultdict(lambda: [0, 0])
        slot_days = defaultdict(lambda: [set(), set()])
        for side, rows in enumerate((before_regular, regular)):
            for r in rows:
                slot = scope.slot_of(r["occurred_time"])
                slots[slot][side] += r["net_amount"]
                if r["net_amount"] > 0:
                    slot_days[slot][side].add(r["occurred_on"])
        if comparable and threshold["meaningful"]:
            changes = [(k, v[1] - v[0]) for k, v in slots.items() if k is not None]
            if changes:
                key, amount = max(changes, key=lambda kv: abs(kv[1]))
                rest = regular_delta - amount
                if amount * regular_delta > 0 and rest * regular_delta >= 0 and abs(amount) * 2 >= abs(regular_delta):
                    evidence.append(f"시간대별로 같은 차이를 보면 {scope.SLOTS[key][0]} 시간대 차이가 {amount:+,}원이에요.")
                    if not any(r.get("activity") for r in regular):
                        limits.append("시간대만으로 외식·배달 같은 구체적인 활동을 구분하지 않았어요.")
        elif not comparable:
            observed_slots = [(k, v[1]) for k, v in slots.items() if k is not None and len(slot_days[k][1]) >= 3
                              and v[1] * 2 >= metrics(regular)["paid"]]
            if observed_slots:
                key, amount = max(observed_slots, key=lambda kv: kv[1])
                evidence.append(f"확인된 {name} 내역은 {scope.SLOTS[key][0]} 시간대에 {len(slot_days[key][1])}일 동안 {won(amount)} 모여 있어요.")
        habit = None
        observed_types = sorted(((k, v) for k, v in types.items() if k != "unknown"), key=lambda kv: -kv[1][1])
        if observed_types and observed_types[0][1][1] * 2 >= metrics(regular)["paid"]:
            key = observed_types[0][0]
            dates = [len({r["occurred_on"] for r in side if r.get("activity") == key and r["net_amount"] > 0})
                     for side in (before_regular, regular)]
            if min(dates) >= 3:
                habit = f"업종이 확인된 {name} 내역에서는 양쪽 기간 모두 {lifestyle.LABELS[key]} 관련 결제가 여러 날짜에 걸쳐 있어 원래도 있던 소비 방식이에요."
            if not comparable and dates[1] >= 3:
                typed_rows = [r for r in regular if r.get("activity") == key and r["net_amount"] > 0]
                label = lifestyle.LABELS[key]
                if all(scope.slot_of(r["occurred_time"]) == "lunch" for r in typed_rows):
                    label = "점심 " + label
                    evidence = [e for e in evidence if not e.startswith(f"확인된 {name} 내역은")]
                diagnosis = f"수집된 {name} 내역에서는 업종이 확인된 {label} 관련 결제가 여러 날짜에 걸쳐 모여 있어요."
                evidence.append(f"확인된 {label} 관련 결제일은 {dates[1]}일이고 금액은 {won(observed_types[0][1][1])}이에요.")
        # 시간만으로 활동을 만들지 않는다. 유형은 identify()의 확인된 업종/플랫폼만 쓴다.
        focus = []
        if main_type:
            for side in (before_regular, regular):
                subset = [r for r in side if r.get("activity") == main_type and r["occurred_on"].weekday() < 5 and scope.slot_of(r["occurred_time"]) == "lunch"]
                focus.append(metrics(subset))
            if focus[1]["days"] > focus[0]["days"] and focus[1]["amount"] - focus[0]["amount"] >= 10000:
                evidence.append(f"업종이 확인된 평일 점심 {lifestyle.LABELS[main_type]} 결제일은 {focus[0]['days']}일에서 {focus[1]['days']}일이고, 이 부분 차이는 {focus[1]['amount'] - focus[0]['amount']:+,}원이에요.")
                focus_delta = focus[1]["amount"] - focus[0]["amount"]
                if kind == "payment_days" and delta > 0 and focus_delta * 2 >= delta and focus_delta <= delta:
                    pattern_rows = lambda rows: [r for r in rows if r.get("activity") == main_type and r["occurred_on"].weekday() < 5 and scope.slot_of(r["occurred_time"]) == "lunch"]
                    focus_parts = decompose(pattern_rows(now), pattern_rows(before))
                    if focus_parts["available"] and all(v >= 0 for v in focus_parts["parts"].values()) and focus_parts["parts"]["payment_days"] * 2 >= delta:
                        focused = persistence(pattern_rows(now), pattern_rows(past), periods[0].last_day, months, window_complete)
                        prefix = "최근 여러 주에 걸쳐 " if focused["state"] == "sustained" else ""
                        diagnosis = f"{prefix}평일 점심 {lifestyle.LABELS[main_type]} 관련 결제일이 늘어난 변화가 {name} 증가의 절반 이상을 설명해요."
        outlooks = {"sustained": "큰 결제를 제외한 소비가 최근 3주 연속 과거 주별 관측 상단보다 높아요. 증가가 여러 주 이어진 신호예요.",
                    "reverted": "한 주의 큰 증가 뒤 최근 주는 이전 관측 범위로 돌아왔어요. 급증을 월말까지 그대로 연장하지 않아요.",
                    "usual": "최근 주별 소비는 과거 관측 범위 안에 있어 반복 결제 자체를 새 습관 변화로 보지 않았어요.",
                    "mixed": "최근 주별 흐름이 엇갈려 증가가 계속된다고 단정하지 않았어요."}
        outlook = outlooks.get(continuation["state"], continuation.get("reason", ""))
        if continuation["state"] == "sustained" and kind == "usual":
            kind = "persistent_small"
            diagnosis = f"{name}의 금액 영향은 작지만 최근 여러 주에 걸친 증가 신호가 보여요."
        candidates.append(dict(id=f"F{len(candidates) + 1}", category=code, subject=name, kind=kind, impact=abs(delta) if comparable else a["amount"],
            comparable=comparable, material=threshold, conclusion=diagnosis, evidence=evidence, outlook=outlook,
            limits=limits, decomposition=decomposition, calendar=calendar, persistence=continuation,
            main_type=main_type, habit=habit, large_delta=large_delta, regular_delta=regular_delta, delta=delta))
    if comparable and abs(total) >= 10000 and not any(unclassified) and all(c["delta"] * total >= 0 for c in candidates):
        for c in candidates:
            c["global_share"] = round(c["delta"] * 100 / total, 1)
    total_history = [sum(r["net_amount"] for r in history if p.start <= r["occurred_on"] < p.start + (periods[0].end_before - periods[0].start))
                     for p in months if p.end_before - p.start >= periods[0].end_before - periods[0].start]
    total_material = material(total, total_history, metrics(previous)["amount"])
    large_change = sum(c.get("large_delta", 0) for c in candidates)
    regular_change = sum(c.get("regular_delta", 0) for c in candidates)
    if comparable and not any(unclassified) and total_material["meaningful"] and large_change * total > 0 and abs(regular_change) < 10000:
        large_categories = [c["category"] for c in candidates if c.get("large_delta", 0) * total > 0]
        continuation = persistence(current, history, periods[0].last_day, months, window_complete)
        candidates.append(dict(id="LARGE_TOTAL", category=None, categories=large_categories, subject="분석 범위 전체",
            kind="large_total", impact=abs(total), comparable=True, material=total_material, delta=total,
            large_delta=large_change, regular_delta=regular_change, persistence=continuation,
            conclusion="이번 지출 변화는 큰 결제의 영향이 커요. 나머지 소비는 지난달 같은 기간과 비슷해요.",
            evidence=[f"분석 범위 전체 금액은 이번 기간 {won(metrics(current)['amount'])}, 비교 기간 {won(metrics(previous)['amount'])}이에요.",
                      f"큰 결제에 쓴 금액은 {_change(large_change)}. 큰 결제를 제외한 소비는 {_change(regular_change)}."],
            outlook="이미 쓴 큰 결제 금액은 이번 달에 한 번만 반영하고, 남은 기간에는 반복 소비 흐름을 적용해 예상해요.", limits=[]))
    if comparable and any(unclassified) and total_material["meaningful"] and abs(unclassified[0] - unclassified[1]) * 2 >= abs(total):
        a, b = metrics(current), metrics(previous)
        candidates.append(dict(id="UNCATEGORIZED_TOTAL", category=None, kind="unclassified_change", impact=abs(total), comparable=True,
            material=total_material, delta=total, conclusion=f"분석 범위의 소비는 {won(abs(total))} {'늘었' if total > 0 else '줄었'}지만 분류되지 않은 내역의 영향이 커 특정 소비 유형의 변화로 설명하기 어려워요.",
            evidence=[f"결제한 날짜는 비교 기간 {b['days']}일, 이번 기간 {a['days']}일이에요. 결제 건수는 {b['count']}건과 {a['count']}건이에요."],
            decomposition=decompose(current, previous), persistence=persistence(current, history, periods[0].last_day, months, window_complete),
            outlook="총액과 날짜별 흐름은 확인할 수 있지만 소비 유형에 대한 해석은 제한했어요.", limits=[]))
    if not candidates and metrics(current)["count"]:
        a = metrics(current)
        candidates.append(dict(id="OBSERVED_TOTAL", category=None, kind="observed_total", impact=a["amount"], delta=total, comparable=False,
            conclusion=f"분류는 아직 확인되지 않았지만 수집된 소비 결제는 {a['days']}일에 걸쳐 {a['count']}건 있어요.",
            evidence=[], outlook="소비 유형과 평소보다 달라졌는지는 아직 판단하기 어려워요.",
            limits=["확인된 내역에 한정한 관측이며 전체 소비의 주된 원인으로 해석하지 않았어요."]))
    significant = [c for c in candidates if c.get("material", {}).get("meaningful") or c["kind"] in ("persistent_small", "type_offset", "observed_total")] if comparable else candidates
    if comparable and not total_material["meaningful"] and any(c.get("material", {}).get("meaningful") and c["delta"] > 0 for c in candidates) and any(c.get("material", {}).get("meaningful") and c["delta"] < 0 for c in candidates):
        changed = sorted((c for c in candidates if c.get("material", {}).get("meaningful")), key=lambda c: -abs(c["delta"]))
        candidates.append(dict(id="OFFSET", category=None, kind="offset", impact=sum(abs(c["delta"]) for c in changed),
            comparable=True, conclusion="총액은 비슷하지만 카테고리 증가와 감소가 서로 상쇄돼 소비 구성이 달라졌어요.",
            evidence=[f"{NAMES[c['category']]} {c['delta']:+,}원" for c in changed], outlook="총액이 비슷하다는 이유만으로 소비 방식까지 같다고 보지는 않았어요.", limits=[]))
        significant.insert(0, candidates[-1])
    # 가중 점수 대신 검증 가능성 → 지속 신호 → 실제 금액 영향. 최종 최대 세 발견만 표시한다.
    selected = sorted(significant, key=lambda c: (c["kind"] not in ("offset", "large_total", "unclassified_change"), not c["comparable"], c.get("persistence", {}).get("state") != "sustained", -c["impact"], c["id"]))[:3]
    if selected and selected[0]["kind"] == "large_total":
        selected = [selected[0], *[c for c in selected[1:] if c["kind"] != "large_payment"]]
    if not selected:
        selected = [dict(id="STABLE", category=None, kind="stable", impact=0, comparable=comparable,
            conclusion="확인 가능한 범위에서는 의미 있는 소비 변화가 확인되지 않았어요." if comparable else "현재 자료로는 소비 패턴을 설명할 근거가 부족해요.",
            evidence=[], outlook="", limits=[])]
    return dict(candidates=candidates, findings=selected, classification_changed=classification_changed,
                total_material=total_material,
                total_delta=total if comparable else None)

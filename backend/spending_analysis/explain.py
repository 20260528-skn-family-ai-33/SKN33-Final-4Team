"""'왜 늘었어?' — 직전 확정 분석의 거래 근거 (읽기 전용 · 금액 계산은 서버만)

- 근거는 일반 조회로 검증한 기간 · 카테고리 · 필터를 그대로 사용한다. 기존 결과는 확정(evidence=confirmed) 항목만 참조한다
  6 · 7번 = 이번 달 vs 지난달 같은 기간, 8 · 9번 = 가장 최근의 이웃한 두 달. 1 · 2 · 5 · 10번은 해당 기간의 구성을 설명한다
- 집계는 소비 분석과 같은 규칙: listed_transactions(같은 구매 제외) → transaction_item(취소 차감) → 소비 거래만
- 다시 조회한 기간이 완전 수집이 아니거나 합계가 기록된 확정 수치와 다르면 근거로 쓰지 않는다 (자료가 바뀜)
- 계산하는 것: 금액 · 증감 · 건수 · 평균 결제액 · 가맹점별 증감 (전체 범위면 카테고리별 증감)
  [임시] 건수 = 순금액이 0 보다 큰 소비 거래 (전액 취소는 세지 않는다)
  [임시] 가맹점 = finance.merchant_rules.normalize_merchant (공백만 정리한 이름 — 지점이 다르면 다른 곳). MEMO 20번 질문
- 원인 · 동기 · 생활 습관은 추측하지 않는다. 답변은 '거래에서 확인한 변화'와 '이유는 단정할 수 없음'을 나눠 쓴다

문장: 서버 템플릿만 (설명 문장용 LLM 없음, MEMO 31). 일반 '왜 늘었어?'는 짧게 — 증감 + 결제 건수 · 건당 평균 비교(어디서 늘었는지가 아니라 어떻게 늘었는지).
질문이 지표(건수 · 평균 · 시간대 · 요일 · 큰 결제 · 새로 간 곳 · 줄이기)를 말하면 그 근거를 대신 넣는다(METRICS).
원인 가설을 말한 질문만 B(plan, MEMO 26)로 확인하고, 실패하면 이 서버 설명 그대로 (다른 LLM 으로 우회하지 않는다)
"""
import re
from datetime import date, timedelta


from . import result as R
from . import tools
from .answer import NAMES, VIEW, _plain, day, josa, span, spans, won
from .budget import OUT_OF_BUDGET
from .clauses import CONNECTIVES
from . import scope as scopes
from .scope import DAY_NAMES, SLOTS, name as scope
from .periods import Period, previous_month_same_days, recent_months, recent_months_to_date, this_month

TOP = 3  # 가맹점 · 카테고리 증감은 변화 방향이 같은 큰 순서로 이만큼만
TEXT = {
    "head_up": "{name} 지출은 {previous}보다 {delta} 늘었어요. ↑ {rate}%",
    "head_down": "{name} 지출은 {previous}보다 {delta} 줄었어요. ↓ {rate}%",
    "head_from_zero": "{name} 지출은 {previous}엔 0원이었고 {current}에 {delta} 썼어요.",
    "head_same": "{name} 지출은 {previous}과 같아서 설명할 증감이 없어요.",
    # 두 기간 중 하나라도 '확인 필요'가 있으면 분류된 내역 기준 — 증감률을 단정하지 않는다 (MEMO 25-13)
    "head_up_classified": "{name} 지출은 분류된 내역 기준으로 {previous}보다 {delta} 늘었어요.",
    "head_down_classified": "{name} 지출은 분류된 내역 기준으로 {previous}보다 {delta} 줄었어요.",
    "classified": "{name}{particle} 분류된 내역 기준이에요. 확인 필요({current} {now} · {previous} {before})에 따라 증감이 달라질 수 있어요.",
    "basis_below": "위 변화는 거래 내역에서 확인한 것이라, 이것만으로 이유를 단정할 수는 없어요.",  # 안내는 맨 아래
    "count": "결제 건수는 {previous} {before}건, {current} {after}건이에요.",
    "average": "건당 평균 결제액은 {previous} {before}, {current} {after}이에요.",
    "big": "{current} 큰 결제(고정지출 제외)는 {items}이에요.",
    "reduce": "{what}는 {count}번, 건당 평균 {average}이에요. 한 달에 {times}번 줄이면 약 {saving} 덜 쓰게 돼요.",
    "instead_of": "{name} 기준이라 {word}만의 변화는 알 수 없어요.",
    # 구성 설명 (이번 달 결과 1 · 2 · 5 에 "왜 많이 나왔어?")
    "mix_head": "{period} {name}에 {amount} 썼어요.",
    "mix_top": "이번 달 가장 많이 쓴 {name} 기준으로 봤어요.",
    "mix_count": "결제는 {count}건, 건당 평균 {average}이에요.",
    "mix_merchants": "많이 쓴 곳은 {items}이에요.",
    "mix_categories": "카테고리별로는 {items} 순으로 많이 썼어요.",
    "mix_fixed": "이 중 {fixed}은 매달 나가는 고정지출이에요.",
    "mix_basis": "거래 내역에서 확인한 구성이에요. 이것만으로 이유를 단정할 수는 없어요.",
    "mix_none": "{period} {name}엔 쓴 돈이 없어서 설명할 구성이 없어요.",
}
REFUSE = {
    "no_basis": "이 결과는 비교한 기간이 없어 무엇이 늘거나 줄었는지 설명할 수 없어요. 지난달 비교나 최근 추이를 본 뒤 물어봐 주세요.",
    "incomplete": "다시 확인해 보니 그 기간 내역이 다 모이지 않아 근거를 만들 수 없어요.",
    "changed": "그 사이 내역이 바뀌어 앞의 결과로는 설명할 수 없어요. 다시 조회한 뒤 물어봐 주세요.",
    "no_pair": "이웃한 두 달 내역이 모두 모이지 않아 비교 근거를 만들 수 없어요.",
}


def _period(value):
    return Period(date.fromisoformat(value["from"]), date.fromisoformat(value["to"]) + timedelta(days=1))


def _basis(item):
    """확정 분석 항목 → (지금 기간, 비교 기간, 이름표, 기록된 금액) 또는 거절 사유"""
    facts, code = item["facts"], item.get("category")
    if item["type"] in (6, 7):
        amount = (lambda fig: fig["category"]["amount"] if code else fig["total"])  # noqa: E731
        names = item.get("labels")  # 달끼리 비교면 그 달 이름 ('9월' · '8월 같은 기간')
        return (_period(item["period"]), _period(item["compare_period"]),
                (names["current"], names["previous_same"]) if names else ("이번 달", "지난달 같은 기간"),
                (amount(facts["current"]), amount(facts["previous"])))
    if item["type"] in (8, 9):
        series = facts["series"]
        if len(series) < 2 or series[-1]["confirmed"] is None or series[-2]["confirmed"] is None:
            return "no_pair"
        b, a = series[-1], series[-2]
        # 이번 달 포함 추이는 달마다 같은 날짜까지라 실제 날짜 범위로 이름 붙인다
        name = span if item.get("include_current") else (lambda p: f"{p.start.month}월")  # noqa: E731
        names = (name(_period(b["period"])), name(_period(a["period"])))
        return _period(b["period"]), _period(a["period"]), names, (b["confirmed"], a["confirmed"])
    return "no_basis"


def _side(user, period, code, item=None):
    """한 기간 · 카테고리의 소비 거래 집계 (item 의 카드 결제만 · 가게 이름 조건 그대로 — tools.spend_query 의 detail).
    완전 수집이 아니거나 수집 판정을 못 읽으면 None"""
    try:
        block = tools.spend_query(user, period, code, where=item, detail=True)
    except tools.DbError:
        return None
    detail = block["confirmed"] and {**block["confirmed"]["detail"], "unclassified": block["confirmed"]["unclassified_amount"]}
    if detail:  # 요일 근거는 하루 평균으로 비교한다 → 기간 안 요일별 날 수
        days = [period.start + timedelta(days=n) for n in range((period.end_before - period.start).days)]
        detail = {**detail, "weekday_days": {w: sum(d.weekday() == w for d in days) for w in range(7)}}
    return detail


def _sides(user, periods, code, item):
    """기간 여러 개의 구성 근거를 더한다 (금액 · 건수 · 고정지출 · 가게 · 카테고리 합, 건당 평균은 합계 ÷ 건수로 다시). 하나라도 없으면 None"""
    sides = [_side(user, p, code, item) for p in periods]
    if any(s is None for s in sides):
        return None
    if len(sides) == 1:
        return sides[0]
    total = lambda key: {k: sum(s[key].get(k, 0) for s in sides) for s in sides for k in s[key]}  # noqa: E731
    paid = [s["average"]["exact"] for s in sides if s["average"]]
    return {"amount": sum(s["amount"] for s in sides), "count": sum(s["count"] for s in sides),
            "average": R.average(sum(e["numerator"] for e in paid), sum(e["denominator"] for e in paid)) if paid else None,
            "fixed": sum(s["fixed"] for s in sides), "merchants": total("merchants"), "categories": total("categories")}


def _moves(now, before, kind):
    """가게(카테고리)별 변화 → 많이 늘어난 곳 · 줄어든 곳 (각각 큰 순서 TOP 개, 같은 크기면 이름 순) + 전체와 잇는 합계.
    합계는 이름 없는 가게까지 모두 더한다 → 늘어난 합계 − 줄어든 합계 = 전체 증감 (문장이 어긋나지 않게)"""
    rows = [{"key": k, "name": NAMES[k] if kind == "category" else k, "before": before.get(k, 0), "after": now.get(k, 0),
             "delta": now.get(k, 0) - before.get(k, 0)} for k in set(now) | set(before)]
    order = lambda r: (-abs(r["delta"]), str(r["key"]))  # noqa: E731
    named = [r for r in rows if r["key"] is not None]
    return {"kind": kind, "up": sorted((r for r in named if r["delta"] > 0), key=order)[:TOP],
            "down": sorted((r for r in named if r["delta"] < 0), key=order)[:TOP],
            "up_total": sum(r["delta"] for r in rows if r["delta"] > 0),
            "down_total": -sum(r["delta"] for r in rows if r["delta"] < 0)}


def _mix(user, item, today):
    """구성 설명 근거: 이번 달 결과(1 · 2 · 5) 하나 → 그 기간의 금액 · 건수 · 평균 · 많이 쓴 곳 · 고정지출.
    카테고리 없는 전반 분석(5)은 가장 많이 쓴 카테고리 하나로 좁힌다 (동률이면 앞 순서). 기록된 금액과 다르면 changed"""
    # 여러 기간 조회(10번 최근 N개월 등)는 기록된 기간 목록을 그대로 — 이어 붙인 한 기간으로 바꾸면 사이 날짜가 끼어든다 (MEMO 25-13)
    if item.get("periods"):
        periods = [_period(p) for p in item["periods"]]
    elif item.get("period"):
        periods = [_period(item["period"])]
    else:
        periods = tools.query_periods(item, today) if item["type"] == 10 else [this_month(today)]
    period = Period(periods[0].start, periods[-1].end_before)
    code, facts, top = item.get("category"), item.get("facts") or {}, False
    if code is None and item["type"] == 5 and facts.get("top_spending"):
        code, top = facts["top_spending"][0]["category"], True
    side = _sides(user, periods, code, item)
    if side is None:
        return {"state": "incomplete"}
    recorded = facts.get("sum") if item["type"] == 10 else facts.get("by_category", {}).get(code, 0) if top else \
        (facts.get("category") or {}).get("amount") if code else facts.get("total")
    if recorded is not None and side["amount"] != recorded:
        return {"state": "changed"}
    biggest = lambda d: sorted(((k, v) for k, v in d.items() if k is not None and v > 0),  # noqa: E731
                               key=lambda kv: (-kv[1], str(kv[0])))[:TOP]
    return {"state": "ok", "kind": "mix", "category": code, "top": top, "period": period, "where": spans(periods),
            "word": _word({**item, "periods": periods}, period),
            "amount": side["amount"],
            "count": side["count"], "average": side["average"], "fixed": side["fixed"],
            "merchants": biggest(side["merchants"]), "categories": biggest(side["categories"]) if code is None else []}


def _row(key, name, before, after):
    return {"key": key, "name": name, "before": before, "after": after, "delta": after - before}


def _per_day(side, day):
    days = side["weekday_days"].get(day, 0)
    return R.average(side["weekdays"].get(day, 0), days)["value"] if days else 0


def _shift(now, before, delta):
    """전체 증감과 같은 방향으로 가장 크게 바뀐 시간대(합계) · 요일(하루 평균 = 요일 합계 ÷ 그 기간의 그 요일 수) 각 1개.
    요일은 두 기간의 요일 수가 달라 합계로 비교하지 않는다. 없으면 None"""
    sign = (delta > 0) - (delta < 0)
    slots = [_row(k, SLOTS[k][0], before["slots"].get(k, 0), now["slots"].get(k, 0))
             for k in set(now["slots"]) | set(before["slots"]) if k is not None]
    days = [_row(k, f"{DAY_NAMES[k]}요일", _per_day(before, k), _per_day(now, k)) for k in range(7)]
    pick = lambda rows, order: max((r for r in rows if r["delta"] * sign > 0),  # noqa: E731
                                   key=lambda r: (abs(r["delta"]), -order.index(r["key"])), default=None)
    out = {"slots": pick(slots, list(SLOTS)), "weekdays": _clear_day(days, sign)}
    return out if sign and any(out.values()) else None


DAY_MIN, DAY_GAP, DAY_RATIO = 1_000, 1_000, 1.5  # 요일 문장을 쓸 '뚜렷한 한 요일' 기준 (MEMO 23-18)


def _clear_day(days, sign):
    """같은 방향으로 하루 평균이 가장 크게 바뀐 요일 — 한 요일만 뚜렷할 때만. 1위 변화가 1,000원 이상이고, 2위보다 1,000원 이상 ·
    1.5배 이상 크다 (2위가 없으면 1위만 본다). 같은 값이 여럿이거나 차이가 작으면 None (요일 문장 · 카드 줄을 뺀다)"""
    ranked = sorted((abs(r["delta"]) for r in days if r["delta"] * sign > 0), reverse=True)
    if not ranked or ranked[0] < DAY_MIN:
        return None
    second = ranked[1] if len(ranked) > 1 else 0
    if second and (ranked[0] - second < DAY_GAP or ranked[0] < DAY_RATIO * second):
        return None
    return next(r for r in days if abs(r["delta"]) == ranked[0] and r["delta"] * sign > 0)


def regular_name(ev):
    """두 기간 모두 결제가 있는 가게 묶음 이름 — 횟수가 아니라 '비교 기간에도 간 곳' (26-19). 비교 기간이 지난달이면 '지난달에도 간 곳',
    추이처럼 다른 달이면 그 달 이름('8월에도 간 곳')"""
    if ev["labels"][1].startswith("지난달"):
        return "지난달에도 간 곳"
    return f"{ev['periods'][1].start.month}월에도 간 곳"  # 추이의 이웃한 달 (달마다 같은 날짜까지여도 달 이름으로)


def _visits(now, before):
    """새로 간 곳(이번에만) · 비교 기간에도 간 곳(두 기간 모두, regular_name) · 이번엔 안 간 곳(지난번에만). 이름 없는 결제도 넣어 합이 전체 증감과 같다"""
    new = [k for k in now if k not in before]
    gone = [k for k in before if k not in now]
    both = [k for k in now if k in before]
    return {"new_total": sum(now[k] for k in new), "new_count": sum(k is not None for k in new),
            "gone_total": sum(before[k] for k in gone), "gone_count": sum(k is not None for k in gone),
            "regular_delta": sum(now[k] - before[k] for k in both), "regular_count": sum(k is not None for k in both)}


def _reduce(now, before, moves, delta, category=None):
    """늘었을 때만: 늘어난 금액이 가장 큰 요인(시간대 · 가게 하나)의 이번 기간 건당 평균 × N번 (서버 계산값만).
    N = 그 요인 결제가 4번 이상이면 2, 아니면 1. 금액은 천 원 단위로 반올림해 '약'.
    제안하지 않는 경우: 범위가 줄이지 않는 카테고리(scope.NO_REDUCE — 의료·건강 · 교육 · 주거·통신) · 그 요인의 결제가 1번뿐.
    후보 계산에서도 NO_REDUCE 카테고리 결제는 뺀다 (전체 범위 설명에서 병원비 시간대를 줄이라고 하지 않게)"""
    if delta <= 0 or category in scopes.NO_REDUCE:
        return None
    slots = [_row(k, SLOTS[k][0], before["reducible_slots"].get(k, 0), now["reducible_slots"].get(k, 0))
             for k in set(now["reducible_slots"]) | set(before["reducible_slots"]) if k is not None]
    slot = max((r for r in slots if r["delta"] > 0), key=lambda r: (r["delta"], -list(SLOTS).index(r["key"])), default=None)
    merchant = moves["up"][0] if moves["kind"] == "merchant" and moves["up"] else None
    if merchant and not now["counts"].get(("reducible_merchant", merchant["key"])):
        merchant = None  # 줄이지 않는 카테고리(병원 등) 가게는 후보가 아니다
    candidates = [("slot", slot, ("reducible", slot and slot["key"])),
                  ("merchant", merchant, ("reducible_merchant", merchant and merchant["key"]))]
    kind, row, key = max(((k, r, c) for k, r, c in candidates if r), key=lambda krc: krc[1]["delta"], default=(None, None, None))
    count = now["counts"].get(key, 0) if row else 0
    if count < 2:  # 결제가 1번뿐이면 '한 달에 N번 줄이기'가 맞지 않는다
        return None
    avg = R.average(row["after"], count)["value"]
    times = 2 if count >= 4 else 1
    return {"kind": kind, "name": row["name"], "count": count, "average": avg, "times": times,
            "saving": (avg * times + 500) // 1000 * 1000 or avg * times}  # 천 원 단위 반올림(0.5 올림)


def _word(item, period):
    """구성 설명의 기간 이름 (이번 달 · 지난달 · 이번 주 · 지난주 · N월). 날짜 범위 · 최근 N개월은 날짜만"""
    kind = (item.get("query") or {}).get("period", "this_month")
    if kind == "month":
        return f"{period.start.month}월"
    if kind == "recent":  # 조회 답과 같은 이름 (answer._q_period)
        n = len(item.get("periods") or [period])
        return f"이번 달을 포함한 최근 {n}개월" if item.get("include_current") else f"최근 {n}개월"
    return {"this_month": "이번 달", "last_month": "지난달", "this_week": "이번 주", "last_week": "지난주"}.get(kind)


def evidence(user, item, today=None):
    """분석 항목 하나 → {"state": "ok", ...근거} 또는 {"state": 거절 사유}
    fresh(직전 결과에 없던 카테고리) = 이번 달 vs 지난달 같은 기간을 새로 비교 · 이번 달 결과(1 · 2 · 5) = 구성 설명"""
    if item.get("evidence") not in (None, "confirmed"):
        return {"state": "incomplete"}
    if item.get("fresh") and today is None:
        return {"state": "no_basis"}
    if item["type"] in (1, 2, 5) or (item["type"] == 10 and item.get("explain_kind") != "change"):
        return _mix(user, item, today)  # 범용 조회(10번)는 그 기간 · 조건 그대로 구성 설명 (11/2 2차 검수)
    if item.get("fresh") and item["type"] in (6, 7):
        basis = this_month(today), previous_month_same_days(today), ("이번 달", "지난달 같은 기간"), None
    elif item.get("fresh") and item["type"] in (8, 9):
        periods = (recent_months_to_date if item.get("include_current") else recent_months)(today, item["months_applied"])
        if len(periods) < 2:
            return {"state": "no_pair"}
        current, previous = periods[-1], periods[-2]
        label = span if item.get("include_current") else lambda p: f"{p.start.month}월"
        basis = current, previous, (label(current), label(previous)), None
    elif item.get("fresh"):
        return {"state": "no_basis"}
    else:
        basis = _basis(item)
    if isinstance(basis, str):
        return {"state": basis}
    current, previous, labels, recorded = basis
    code = item.get("category")
    now, before = _side(user, current, code, item), _side(user, previous, code, item)
    if now is None or before is None:
        return {"state": "incomplete"}
    if recorded is not None and (now["amount"], before["amount"]) != recorded:
        return {"state": "changed"}
    change = R.compare(now["amount"], before["amount"], True, True)
    # 카테고리 하나면 가게별, 전체면 카테고리별 변화
    moves = _moves(now["merchants"], before["merchants"], "merchant") if code else \
        _moves(now["categories"], before["categories"], "category")
    return {"state": "ok", "type": item["type"], "category": code, "labels": labels,
            "current": {k: now[k] for k in ("amount", "count", "average")},
            "previous": {k: before[k] for k in ("amount", "count", "average")},
            "delta": change["delta"], "rate": change["rate"], "moves": moves,
            "big": now["big"], "shift": _shift(now, before, change["delta"]), "visits": _visits(now["merchants"], before["merchants"]),
            "reduce": _reduce(now, before, moves, change["delta"], code),
            # 카테고리 설명이면 두 기간의 '확인 필요' 금액 (전체 범위는 합계에 이미 들어 있어 0)
            "unclassified": (now["unclassified"], before["unclassified"]) if code and code != R.UNCLASSIFIED else (0, 0),
            # B(MEMO 26): 주장 · 비교하며 묶기가 같은 집계를 다시 쓴다 (DB 를 다시 읽지 않음). 문장 · 카드에는 쓰지 않는다
            "sides": (now, before), "periods": (current, previous)}


def _changed(delta):
    """방향이 들어간 완성 단위: '42,000원 늘었어요' · '3,000원 줄었어요'"""
    return f"{won(abs(delta))} {'늘었어요' if delta > 0 else '줄었어요'}"


def _head(ev, name):
    cur, prev = ev["labels"]
    if ev["rate"] is None:
        return TEXT["head_from_zero"].format(name=name, previous=prev, current=cur, delta=won(ev["delta"]))
    if ev["delta"] == 0:
        return TEXT["head_same"].format(name=name, previous=prev)
    key = ("head_up" if ev["delta"] > 0 else "head_down") + ("_classified" if any(ev["unclassified"]) else "")
    return TEXT[key].format(name=name, previous=prev, delta=won(abs(ev["delta"])), rate=_plain(ev["rate"]))


def _visits_line(ev):
    """새로 간 곳 · 지난달에도 간 곳 · 안 간 곳으로 전체 증감을 잇는 한 문장 (세 합 = 전체 증감).
    한 묶음에서만 나왔으면 같은 숫자를 되풀이하지 않고 '늘어난 금액은 모두 지난달에도 간 곳에서 나왔어요.'"""
    v, parts, regular = ev["visits"], [], regular_name(ev)
    only = [name for name, amount in ((f"새로 간 곳({v['new_count']}곳)", v["new_total"]), (regular, v["regular_delta"]),
                                      (f"이번엔 안 간 곳({v['gone_count']}곳)", v["gone_total"])) if amount]
    if len(only) == 1:
        return f"{'늘어난' if ev['delta'] > 0 else '줄어든'} 금액은 모두 {only[0]}에서 나왔어요."
    if v["new_total"]:
        parts.append(f"새로 간 곳({v['new_count']}곳)에서 {won(v['new_total'])} 늘고")
    if v["regular_delta"]:
        parts.append(f"{regular}에서 {won(abs(v['regular_delta']))} {'늘고' if v['regular_delta'] > 0 else '줄고'}")
    if v["gone_total"]:
        parts.append(f"이번엔 안 간 곳({v['gone_count']}곳)에서 {won(v['gone_total'])} 줄고")
    if not parts:
        return None
    parts[-1] = parts[-1][:-1] + "어"  # 마지막 '늘고 · 줄고' → '늘어 · 줄어'
    return f"{', '.join(parts)}, 전체로는 {_changed(ev['delta'])}."


def _shift_line(ev):
    """'고정지출을 빼고 보면 늘어난 금액은 점심 시간대(+48,000원)에 가장 많이 몰렸고, 요일로는 목요일 하루 평균이 1,000원 늘었어요.'"""
    shift = ev["shift"]
    if not shift:
        return None
    up, slot, day_ = ev["delta"] > 0, shift["slots"], shift["weekdays"]
    day_part = day_ and f"{day_['name']} 하루 평균이 {_changed(day_['delta'])}"
    if slot:
        where = f"{slot['name']} 시간대({'+' if slot['delta'] > 0 else '−'}{won(abs(slot['delta']))})"
        head = f"고정지출을 빼고 보면 {'늘어난' if up else '줄어든'} 금액은 {where}{'에 가장 많이 몰렸' if up else '에서 가장 컸'}"
        return f"{head}고, 요일로는 {day_part}." if day_part else f"{head}어요."
    return f"고정지출을 빼고 보면 요일로는 {day_part}."


def _big_line(ev):
    big = [f"{b['name'] or '이름 없는 곳'} {won(b['amount'])}({day(b['day'])})" for b in ev["big"]]
    return TEXT["big"].format(current=ev["labels"][0], items=", ".join(big)) if big else None


def _reduce_line(ev, name):
    r = ev["reduce"]
    if not r:
        return None
    # '점심 시간대 식비 결제' · '목요일 카드 결제' · '과자서원 결제' (범위 이름 끝의 '결제'는 한 번만)
    core = "" if name == "전체" else name[:-2].strip() if name.endswith("결제") else name
    when = {"slot": f"{r['name']} 시간대", "weekday": r["name"], "merchant": r["name"]}[r["kind"]]
    what = " ".join(part for part in (when, "" if r["kind"] == "merchant" else core, "결제") if part)
    return TEXT["reduce"].format(what=what, count=r["count"], average=won(r["average"]), times=r["times"],
                                 saving=won(r["saving"]))


def _template(ev):
    """건수 · 평균 문장 (질문이 건수 · 평균을 물었을 때)"""
    cur, prev = ev["labels"]
    lines = [TEXT["count"].format(previous=prev, current=cur, before=ev["previous"]["count"], after=ev["current"]["count"])]
    if ev["previous"]["average"] and ev["current"]["average"]:
        lines.append(TEXT["average"].format(previous=prev, current=cur, before=won(ev["previous"]["average"]["value"]),
                                            after=won(ev["current"]["average"]["value"])))
    return lines


# 질문이 말한 지표 → 그 근거 문장 (말한 순서 아니라 이 순서로). 낱말은 해석 칸이 아닌 원문에서 본다
# shortcut: 낱말 목록으로만 고른다 — 해석이 지표 칸을 주게 되면 그걸로 바꾼다
METRICS = (
    (("건수", "몇 번", "몇 건", "자주", "횟수"), lambda ev, name: _template(ev)[:1]),
    (("평균", "건당", "한 번에"), lambda ev, name: _template(ev)[1:]),
    (("시간대", "요일", "언제", "몇 시"), lambda ev, name: [_shift_line(ev)]),
    (("큰 결제", "큰 돈", "큰 지출"), lambda ev, name: [_big_line(ev)]),
    (("새로 간", "처음 간", "안 간"), lambda ev, name: [_visits_line(ev)]),
    (("줄이", "아끼", "절약"), lambda ev, name: [_reduce_line(ev, name)]),
)
BRIEF = 2  # 일반 '왜 늘었어?'의 근거 문장 수 상한


def _brief(ev, name, question):
    """증감 뒤에 붙일 근거 1~2문장: 질문이 말한 지표가 있으면 그것, 없으면 결제 건수 · 건당 평균 비교
    (증감 = 건수 × 건당 금액 — 자주 써서인지 한 번에 많이 써서인지. 특정 가게 증감은 '어디서'라 기본으로 쓰지 않는다)"""
    said = [line for words, pick in METRICS if any(w in (question or "") for w in words) for line in pick(ev, name) if line]
    if said:
        return said[:BRIEF], any(w in (question or "") for w in METRICS[-1][0]), False
    return _template(ev), False, True


# 건수 · 건당 평균의 방향 → 둘을 엮은 한 줄 (증감 = 건수 × 건당 금액을 말로 옮긴 것 — 원인 추측 아님). 키 = (건수, 평균) 부호, 평균 없음 = None
MIX = {(1, 1): "더 자주, 한 번에 더 많이 결제했어요.", (1, -1): "한 번에 쓰는 금액은 줄었지만 더 자주 결제했어요.",
       (1, 0): "한 번에 쓰는 금액은 비슷하고 더 자주 결제했어요.", (-1, 1): "결제는 줄었지만 한 번에 쓰는 금액이 커졌어요.",
       (-1, -1): "덜 자주, 한 번에 더 적게 결제했어요.", (-1, 0): "한 번에 쓰는 금액은 비슷하고 결제가 줄었어요.",
       (0, 1): "결제 횟수는 같고 한 번에 쓰는 금액이 커졌어요.", (0, -1): "결제 횟수는 같고 한 번에 쓰는 금액이 작아졌어요.",
       (1, None): "결제 횟수가 늘었어요.", (-1, None): "결제 횟수가 줄었어요."}


def _sign(n):
    return (n > 0) - (n < 0)


def _mix_parts(ev):
    """건수 · 건당 평균 → (카드 줄, 한 줄 요약 | None). 평균은 두 기간 모두 결제가 있을 때만"""
    count = ev["current"]["count"] - ev["previous"]["count"]
    rows = [{"name": "결제 건수", "value": f"{ev['previous']['count']}건 → {ev['current']['count']}건",
             "note": f"{'+' if count >= 0 else '−'}{abs(count)}건", "direction": "up" if count > 0 else "down" if count < 0 else None}]
    avg = None
    if ev["previous"]["average"] and ev["current"]["average"]:
        before, after = ev["previous"]["average"]["value"], ev["current"]["average"]["value"]
        avg = _sign(after - before)
        rows.append({"name": "건당 평균", "value": f"{won(before)} → {won(after)}",
                     "note": f"{'+' if after >= before else '−'}{won(abs(after - before))}", "direction": {1: "up", -1: "down"}.get(avg)})
    return rows, MIX.get((_sign(count), avg))


def view(ev, name):
    """한 항목 증감 설명의 카드 머리 — 제목 + 큰 금액(증감). 아래 문장 블록은 부르는 쪽이 붙인다 (B · 짧은 설명 공통)"""
    prev, up = ev["labels"][1], ev["delta"] > 0
    note = "분류된 내역 기준" if any(ev["unclassified"]) else \
        VIEW["rate_up" if up else "rate_down"].format(rate=_plain(ev["rate"])) if ev["rate"] is not None else f"{prev}엔 0원"
    return [{"kind": "head", "title": f"{name} · {prev}{josa(prev, ('과', '와'))} 비교"},
            {"kind": "total", "caption": f"{prev}보다 {'늘어난' if up else '줄어든'} 금액", "amount": abs(ev["delta"]), "note": note,
             "direction": "up" if up else "down"}]


def _mix_answer(ev, item):
    """구성 설명 → (문장, 카드 블록). 설명 LLM 은 쓰지 않는다 (템플릿만 — 증감 설명용 프롬프트라)"""
    name = NAMES[ev["category"]] if ev["top"] else scope(item)
    label = f"{ev['word']}({ev['where']})" if ev["word"] else ev["where"]
    if ev["amount"] <= 0:
        return [TEXT["mix_none"].format(period=label, name=name)], None
    lines = [TEXT["mix_top"].format(name=name)] if ev["top"] else []
    lines.append(TEXT["mix_head"].format(period=label, name=name, amount=won(ev["amount"])))
    rows = []
    if ev["average"]:
        lines.append(TEXT["mix_count"].format(count=ev["count"], average=won(ev["average"]["value"])))
        rows += [{"name": "결제 건수", "value": f"{ev['count']}건"}, {"name": "건당 평균", "value": won(ev["average"]["value"])}]
    pairs = [(m, a) for m, a in ev["merchants"]] if ev["category"] else [(NAMES[c], a) for c, a in ev["categories"]]
    if pairs:
        listed = ", ".join(f"{k} {won(a)}" for k, a in pairs)
        lines.append(TEXT["mix_merchants" if ev["category"] else "mix_categories"].format(items=listed))
    if ev["fixed"] > 0:
        lines.append(TEXT["mix_fixed"].format(fixed=won(ev["fixed"])))
    lines.append(TEXT["mix_basis"])
    blocks = [{"kind": "head", "title": f"{name} · {ev['word'] or ev['where']} 쓴 곳"},
              {"kind": "total", "caption": f"{ev['where']} 쓴 금액", "amount": ev["amount"],
               "note": TEXT["mix_fixed"].format(fixed=won(ev["fixed"])) if ev["fixed"] > 0 else None}]
    if rows:
        blocks.append({"kind": "rows", "rows": rows})
    if pairs:
        blocks.append({"kind": "rows", "title": "많이 쓴 곳" if ev["category"] else "많이 쓴 카테고리",
                       "rows": [{"name": k, "amount": a, "ratio": str(R.percent(a, pairs[0][1])["value"])} for k, a in pairs]})
    texts = ([TEXT["mix_top"].format(name=name)] if ev["top"] else []) + [TEXT["mix_basis"]]
    return lines, blocks + [{"kind": "text", "lines": texts}]


def _planned(ev, item, question, plan, hypothesis=None):
    """B 한 번 → ({"lines", "view"} | None, trace). 근거 부족(거절 · 늘지 않음)이면 B 를 건너뛰고 템플릿의 거절 · 안내 문구로.
    계산하지 않은 절약액 · [예산 조정하기]는 붙이지 않는다. 확인된 지표에 맞는 다음 확인 항목만 제안한다."""
    from . import claims  # B 흐름이 이 모듈의 근거 · 금지어를 쓴다 — 서로 import 하지 않게 여기서
    C_FIXED = claims.FIXED
    if ev["state"] != "ok" or ev["delta"] <= 0:
        return None, {"stop": "insufficient", "fallback": "template"}
    said, trace = claims.run(ev, item, question, plan, hypothesis=hypothesis)
    if said is None:
        return None, trace
    name, extra = scope(item), []
    if item.get("instead_of"):
        extra.append(TEXT["instead_of"].format(name=name, word=item["instead_of"]))
    if any(ev["unclassified"]):
        cur_label, prev_label = ev["labels"]
        code_name = NAMES[ev["category"]]
        extra.append(TEXT["classified"].format(name=code_name, particle=josa(code_name, ("으로", "로")), current=cur_label,
                                               now=won(ev["unclassified"][0]), previous=prev_label, before=won(ev["unclassified"][1])))
    contract = trace.get("support") or {}
    tail = [contract["limitation"]] if contract.get("status") != "insufficient" and contract.get("limitation") else []
    if trace.get("next_check"):
        tail.append(trace["next_check"])
    lines = [*said, *tail, *extra]
    rows, shown = trace.pop("_rows", []), trace.pop("_shown", [])  # 금액이 든 카드 줄 — 개발용 분석 과정엔 남기지 않는다
    blocks = [] if contract.get("status") == "insufficient" else view(ev, name)
    fixed = [r.pop("fixed") for r in rows]
    if blocks and rows:  # 근거 숫자는 비교 줄로
        blocks.append({"kind": "rows", "title": "확인한 변화" + (" (고정지출 제외)" if all(fixed) else ""), "rows": rows})
    if blocks:  # 큰 금액 · 비교 줄과 같은 숫자를 되풀이하는 문장(범위 증감 · 줄로 옮긴 주장 하나뿐인 문장)은 카드 문장에서 뺀다
        core = lambda t: re.sub(rf"^(?:{'|'.join(CONNECTIVES)}) ", "", t.strip()).removeprefix(C_FIXED).rstrip(".")  # noqa: E731
        moved = {core(t) for t in shown}
        said = [line for line in said if core(line) not in moved]  # 두 주장을 이은 문장은 그대로
    text = [*said, *extra]
    # 확인 범위 한계 · 다음 확인 안내는 카드 아래 작은 글씨로 (글자만 보는 화면은 lines 그대로)
    return {"lines": lines, "view": blocks + ([{"kind": "text", "lines": text}] if text else []), "notices": tail}, trace


def answer(user, items, today=None, plan=None, question=None, hypothesis=None):
    """설명 요청의 항목들 → {"lines", "notices", "view"?, "suggest"?, "trace"?} (항목마다 근거 → 서버 문장)
    plan = B(MEMO 26) 계획 · 답 LLM complete — 가설 질문일 때만 부르는 쪽이 준다. 항목 하나의 증감 설명을 B 로 먼저 답하고,
    실패 · 시간 초과면 아래 짧은 서버 설명 그대로. B 를 거쳤으면 out["trace"] = 개발용 분석 과정"""
    lines, views, suggest, trace, notices = [], [], False, None, []
    for item in items:
        ev = evidence(user, item, today)
        name = scope(item)  # 세부 지출 대신 본 항목은 "식비 전체"
        if plan is not None and len(items) == 1 and ev.get("kind") != "mix":
            planned, trace = _planned(ev, item, question or "", plan, hypothesis=hypothesis)
            if planned is not None:
                lines += planned["lines"]
                views.append(planned["view"])
                notices += [n for n in planned["notices"] if n not in notices]
                continue
        if ev["state"] != "ok":
            lines.append(REFUSE[ev["state"]])
            continue
        if ev.get("kind") == "mix":
            said, blocks = _mix_answer(ev, item)
            lines += said
            views += [blocks] if blocks else []
            continue
        lines.append(_head(ev, name))
        if ev["delta"] == 0:
            continue
        body, reduce, default = _brief(ev, name, question)
        suggest = suggest or (reduce and bool(ev["reduce"]) and item.get("category") not in OUT_OF_BUDGET)  # 줄이기를 물었을 때만
        extra = []
        if item.get("instead_of"):
            extra.append(TEXT["instead_of"].format(name=name, word=item["instead_of"]))
        if any(ev["unclassified"]):
            cur_label, prev_label = ev["labels"]
            code_name = NAMES[ev["category"]]
            extra.append(TEXT["classified"].format(name=code_name, particle=josa(code_name, ("으로", "로")), current=cur_label,
                                                   now=won(ev["unclassified"][0]), previous=prev_label, before=won(ev["unclassified"][1])))
        notices = notices or [TEXT["basis_below"]]  # 단정 불가 안내는 맨 아래 작은 글씨로 한 번
        if default:  # 건수 · 평균은 비교 줄로, 둘을 엮은 한 줄 요약은 문장으로 (기간은 카드 제목에 한 번)
            rows, mix = _mix_parts(ev)
            lines += [*([mix] if mix else []), *body, *extra]
            blocks = [{"kind": "rows", "rows": rows}, *([{"kind": "text", "lines": [mix, *extra]}] if mix or extra else [])]
        else:
            lines += [*body, *extra]
            blocks = [{"kind": "text", "lines": [*body, *extra]}]
        views.append(view(ev, name) + blocks)  # 증감은 큰 금액 카드에 있어 문장으로 다시 쓰지 않는다
    out = {"lines": lines, "notices": notices}
    if len(items) == 1 and views:  # 한 항목 설명만 카드로 (여러 항목 · 거절은 문장 그대로)
        out["view"] = views[0]
    if suggest:  # 줄이는 방법을 보였으면 [예산 조정하기] 제안 (문구 · 선택지는 라우터 안내가 붙인다)
        out["suggest"] = ["budget_adjust"]
    if trace is not None:
        out["trace"] = trace
    return out

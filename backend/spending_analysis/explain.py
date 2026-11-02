"""'왜 늘었어?' — 직전 확정 분석의 거래 근거 (읽기 전용 · 금액 계산은 서버만)

- 근거는 직전 분석의 확정(evidence=confirmed) 항목 하나의 두 기간 · 카테고리로만 다시 조회해 만든다
  6 · 7번 = 이번 달 vs 지난달 같은 기간, 8 · 9번 = 가장 최근의 이웃한 두 달. 1~5번은 비교 기준이 없어 설명하지 않는다
- 집계는 소비 분석과 같은 규칙: listed_transactions(같은 구매 제외) → transaction_item(취소 차감) → 소비 거래만
- 다시 조회한 기간이 완전 수집이 아니거나 합계가 기록된 확정 수치와 다르면 근거로 쓰지 않는다 (자료가 바뀜)
- 계산하는 것: 금액 · 증감 · 건수 · 평균 결제액 · 가맹점별 증감 (전체 범위면 카테고리별 증감)
  [임시] 건수 = 순금액이 0 보다 큰 소비 거래 (전액 취소는 세지 않는다)
  [임시] 가맹점 = finance.merchant_rules.normalize_merchant (공백만 정리한 이름 — 지점이 다르면 다른 곳). MEMO 20번 질문
- 원인 · 동기 · 생활 습관은 추측하지 않는다. 답변은 '거래에서 확인한 변화'와 '이유는 단정할 수 없음'을 나눠 쓴다

문장: 기본은 서버 템플릿. 설명 LLM(complete)을 주면 근거 항목 이름과 키만 넘기고({F1} 자리표시), 돌아온 문장은
숫자 · 모르는 키 · 금지 표현이 없을 때만 서버 값을 끼워 쓴다. 하나라도 어긋나면 템플릿으로 돌아간다 (재시도 없음)
자리표시 바로 뒤 조사(을/를 · 은/는 · 이/가 · 과/와 · 으로/로)는 끼운 값의 받침에 맞춰 서버가 고친다
"""
import json
import re
from datetime import date, timedelta


from . import result as R
from . import tools
from .answer import NAMES, VIEW, _plain, day, josa, span, won
from .budget import OUT_OF_BUDGET
from .scope import DAY_NAMES, SLOTS, name as scope
from .periods import Period, previous_month_same_days, this_month

TOP = 3  # 가맹점 · 카테고리 증감은 변화 방향이 같은 큰 순서로 이만큼만
TEXT = {
    "head_up": "{name} 지출은 {previous}보다 {delta} 늘었어요. ↑ {rate}%",
    "head_down": "{name} 지출은 {previous}보다 {delta} 줄었어요. ↓ {rate}%",
    "head_from_zero": "{name} 지출은 {previous}엔 0원이었고 {current}에 {delta} 썼어요.",
    "head_same": "{name} 지출은 {previous}과 같아서 설명할 증감이 없어요.",
    "basis": "아래는 거래 내역에서 확인한 변화예요. 이것만으로 이유를 단정할 수는 없어요.",
    "basis_below": "위 변화는 거래 내역에서 확인한 것이라, 이것만으로 이유를 단정할 수는 없어요.",  # 카드: 안내가 맨 아래
    "count": "결제 건수는 {previous} {before}건, {current} {after}건이에요.",
    "average": "건당 평균 결제액은 {previous} {before}, {current} {after}이에요.",
    "moves_up": "많이 늘어난 {word}{eun} {items}이에요.",
    "moves_down": "줄어든 {word}{eun} {items}이에요.",
    "big": "{current} 큰 결제(고정지출 제외)는 {items}이에요.",
    "reduce": "{what}는 {count}번, 건당 평균 {average}이에요. 한 달에 {times}번 줄이면 약 {saving} 덜 쓰게 돼요.",
    "unnamed": "가맹점 이름이 없는 거래는 가맹점별 변화에서 뺐어요.",
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
# 설명 LLM 출력에서 막는 표현 — 숫자는 서버 값만, 소비 동기 · 습관 · 평가 추측 금지 (완전한 차단이 아니라 최소 검사)
BANNED = ("습관", "과소비", "아마", "것 같", "때문일", "탓", "충동", "낭비")
PROMPT = """너는 가계부 앱의 소비 증감 설명 문장을 쓴다. 입력 facts 는 서버가 계산한 근거 목록이다 [{key, label}].
규칙: 1~2문장. 결제 건수 · 건당 평균의 변화와 전체 증감만 쓴다 (가게 · 카테고리별 변화는 서버가 따로 쓴다). 숫자 · 금액 · 비율을 직접 쓰지 말고 반드시 {key} 자리표시로만 쓴다 (예: "{F2}").
label 에 '서술어 포함'이 있는 자리표시는 값 안에 '늘었어요 · 줄었어요'까지 들어 있다 → 그 자리표시로 문장을 끝내고 뒤에 말을 붙이지 않는다
(예: "전체 지출은 {F1}"). 목록 자리표시는 명사처럼 쓴다.
facts 에 없는 사실을 더하지 않는다. 소비 이유 · 동기 · 생활 습관을 추측하거나 평가하지 않는다.
입력의 scope 가 실제 조회 범위다. requested(사용자가 원래 말한 세부 지출)가 있으면 근거는 scope 전체의 것이므로
requested 만의 변화나 원인처럼 쓰지 않는다 (requested 라는 말을 문장에 쓰지 않는다).
JSON 하나로만 답한다: {"sentences": ["...", "..."]}"""
# 자리표시 바로 뒤 조사 → 받침에 맞는 쌍 (뒤가 띄어쓰기 · 문장부호 · 끝일 때만 — '이에요' 같은 서술은 건드리지 않는다)
PARTICLES = {p: pair for pair in (("을", "를"), ("은", "는"), ("이", "가"), ("과", "와"), ("으로", "로")) for p in pair}
PLACED = re.compile(r"\{(F\d+)\}(으로|로|을|를|은|는|이|가|과|와)?(?=[\s.,!?]|$)|\{(F\d+)\}")


def _period(value):
    return Period(date.fromisoformat(value["from"]), date.fromisoformat(value["to"]) + timedelta(days=1))


def _basis(item):
    """확정 분석 항목 → (지금 기간, 비교 기간, 이름표, 기록된 금액) 또는 거절 사유"""
    facts, code = item["facts"], item.get("category")
    if item["type"] in (6, 7):
        amount = (lambda fig: fig["category"]["amount"] if code else fig["total"])  # noqa: E731
        return (_period(item["period"]), _period(item["compare_period"]), ("이번 달", "지난달 같은 기간"),
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
    detail = block["confirmed"] and block["confirmed"]["detail"]
    if detail:  # 요일 근거는 하루 평균으로 비교한다 → 기간 안 요일별 날 수
        days = [period.start + timedelta(days=n) for n in range((period.end_before - period.start).days)]
        detail = {**detail, "weekday_days": {w: sum(d.weekday() == w for d in days) for w in range(7)}}
    return detail


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
    period = _period(item["period"]) if item.get("period") else this_month(today)
    code, facts, top = item.get("category"), item.get("facts") or {}, False
    if code is None and item["type"] == 5 and facts.get("top_spending"):
        code, top = facts["top_spending"][0]["category"], True
    side = _side(user, period, code, item)
    if side is None:
        return {"state": "incomplete"}
    recorded = facts.get("by_category", {}).get(code, 0) if top else \
        (facts.get("category") or {}).get("amount") if code else facts.get("total")
    if recorded is not None and side["amount"] != recorded:
        return {"state": "changed"}
    biggest = lambda d: sorted(((k, v) for k, v in d.items() if k is not None and v > 0),  # noqa: E731
                               key=lambda kv: (-kv[1], str(kv[0])))[:TOP]
    return {"state": "ok", "kind": "mix", "category": code, "top": top, "period": period, "amount": side["amount"],
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


def _visits(now, before):
    """새로 간 곳(이번에만) · 자주 가던 곳(두 기간 모두) · 이번엔 안 간 곳(지난번에만). 이름 없는 결제도 넣어 합이 전체 증감과 같다"""
    new = [k for k in now if k not in before]
    gone = [k for k in before if k not in now]
    both = [k for k in now if k in before]
    return {"new_total": sum(now[k] for k in new), "new_count": sum(k is not None for k in new),
            "gone_total": sum(before[k] for k in gone), "gone_count": sum(k is not None for k in gone),
            "regular_delta": sum(now[k] - before[k] for k in both), "regular_count": sum(k is not None for k in both)}


def _reduce(now, before, moves, delta):
    """늘었을 때만: 늘어난 금액이 가장 큰 요인(시간대 · 요일 · 가게 하나)의 이번 기간 건당 평균 × N번 (서버 계산값만).
    N = 그 요인 결제가 4번 이상이면 2, 아니면 1. 금액은 천 원 단위로 반올림해 '약'"""
    if delta <= 0:
        return None
    shift = _shift(now, before, delta) or {}
    # 요일은 하루 평균 변화라 합계(시간대 · 가게)와 크기를 견줄 수 없어 줄이는 방법 후보에서 뺀다
    candidates = [("slot", shift.get("slots")), ("merchant", moves["up"][0] if moves["kind"] == "merchant" and moves["up"] else None)]
    kind, row = max(((k, r) for k, r in candidates if r), key=lambda kr: kr[1]["delta"], default=(None, None))
    if row is None:
        return None
    count = now["counts"].get((kind, row["key"]), 0)
    if not count:
        return None
    avg = R.average(row["after"], count)["value"]
    times = 2 if count >= 4 else 1
    return {"kind": kind, "name": row["name"], "count": count, "average": avg, "times": times,
            "saving": (avg * times + 500) // 1000 * 1000 or avg * times}  # 천 원 단위 반올림(0.5 올림)


def evidence(user, item, today=None):
    """분석 항목 하나 → {"state": "ok", ...근거} 또는 {"state": 거절 사유}
    fresh(직전 결과에 없던 카테고리) = 이번 달 vs 지난달 같은 기간을 새로 비교 · 이번 달 결과(1 · 2 · 5) = 구성 설명"""
    if item.get("fresh") and today is None:
        return {"state": "no_basis"}
    if item.get("fresh"):
        basis = this_month(today), previous_month_same_days(today), ("이번 달", "지난달 같은 기간"), None
    elif item["type"] in (1, 2, 5):
        return _mix(user, item, today)
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
            "unnamed": bool(code) and (None in now["merchants"] or None in before["merchants"]),
            "big": now["big"], "shift": _shift(now, before, change["delta"]), "visits": _visits(now["merchants"], before["merchants"]),
            "reduce": _reduce(now, before, moves, change["delta"])}


def _changed(delta):
    """방향이 들어간 완성 단위: '42,000원 늘었어요' · '3,000원 줄었어요'"""
    return f"{won(abs(delta))} {'늘었어요' if delta > 0 else '줄었어요'}"


def _span(r):
    return f"{r['name']}({won(r['before'])} → {won(r['after'])})"


def facts(ev):
    """근거 → 문장 재료 [{key, label, text}]. text 는 서버가 만든 표기 그대로 (LLM 에는 label · key 만 — 가게 이름도 보내지 않는다).
    증감은 방향까지 든 완성 단위('42,000원 늘었어요')라 LLM 이 동사를 붙이지 않는다 (checked 가 확인)"""
    cur, prev = ev["labels"]
    out = [("이 범위(scope) 지출의 증감(서술어 포함)", _changed(ev["delta"])), (f"{prev} 결제 건수", f"{ev['previous']['count']}건"),
           (f"{cur} 결제 건수", f"{ev['current']['count']}건")]
    if ev["previous"]["average"] and ev["current"]["average"]:
        out += [(f"{prev} 건당 평균 결제액", won(ev["previous"]["average"]["value"])),
                (f"{cur} 건당 평균 결제액", won(ev["current"]["average"]["value"]))]
    # 많이 늘어난 곳 · 줄어든 곳 목록과 전체와 잇는 문장은 서버 템플릿이 늘 쓴다 (LLM 에 맡기면 빠뜨리거나 잇는 말이 흔들림)
    return [{"key": f"F{i}", "label": label, "text": text} for i, (label, text) in enumerate(out, 1)]


def _head(ev, name):
    cur, prev = ev["labels"]
    if ev["rate"] is None:
        return TEXT["head_from_zero"].format(name=name, previous=prev, current=cur, delta=won(ev["delta"]))
    if ev["delta"] == 0:
        return TEXT["head_same"].format(name=name, previous=prev)
    key = "head_up" if ev["delta"] > 0 else "head_down"
    return TEXT[key].format(name=name, previous=prev, delta=won(abs(ev["delta"])), rate=_plain(ev["rate"]))


def _visits_line(ev):
    """새로 간 곳 · 자주 가던 곳 · 안 간 곳으로 전체 증감을 잇는 한 문장 (세 합 = 전체 증감).
    한 묶음에서만 나왔으면 같은 숫자를 되풀이하지 않고 '늘어난 금액은 모두 자주 가던 곳에서 나왔어요.'"""
    v, parts = ev["visits"], []
    only = [name for name, amount in ((f"새로 간 곳({v['new_count']}곳)", v["new_total"]), ("자주 가던 곳", v["regular_delta"]),
                                      (f"이번엔 안 간 곳({v['gone_count']}곳)", v["gone_total"])) if amount]
    if len(only) == 1:
        return f"{'늘어난' if ev['delta'] > 0 else '줄어든'} 금액은 모두 {only[0]}에서 나왔어요."
    if v["new_total"]:
        parts.append(f"새로 간 곳({v['new_count']}곳)에서 {won(v['new_total'])} 늘고")
    if v["regular_delta"]:
        parts.append(f"자주 가던 곳에서 {won(abs(v['regular_delta']))} {'늘고' if v['regular_delta'] > 0 else '줄고'}")
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
    """건수 · 평균 문장 (설명 LLM 문장을 쓰면 이 자리를 대신한다)"""
    cur, prev = ev["labels"]
    lines = [TEXT["count"].format(previous=prev, current=cur, before=ev["previous"]["count"], after=ev["current"]["count"])]
    if ev["previous"]["average"] and ev["current"]["average"]:
        lines.append(TEXT["average"].format(previous=prev, current=cur, before=won(ev["previous"]["average"]["value"]),
                                            after=won(ev["current"]["average"]["value"])))
    return lines


def _evidence_lines(ev, name):
    """건수 · 평균 다음의 근거 — 템플릿 · 설명 LLM 어느 쪽이든 서버가 늘 같은 기준으로:
    큰 결제 → 많이 몰린 시간대 · 요일 → 새로 간 곳 · 자주 가던 곳으로 전체와 잇기 → (두 기간 모두 간 가게가 있을 때만) 가게별 보조 →
    (늘었을 때) 줄이는 방법 한 줄"""
    moves = ev["moves"]
    word = "곳" if moves["kind"] == "merchant" else "카테고리"
    lines = [_big_line(ev), _shift_line(ev), _visits_line(ev)]
    if moves["kind"] == "category" or ev["visits"]["regular_count"]:
        if moves["up"]:
            lines.append(TEXT["moves_up"].format(word=word, eun=josa(word, ("은", "는")), items=", ".join(map(_span, moves["up"]))))
        if moves["down"]:
            lines.append(TEXT["moves_down"].format(word=word, eun=josa(word, ("은", "는")), items=", ".join(map(_span, moves["down"]))))
    lines.append(_reduce_line(ev, name))
    return [line for line in lines if line]


def view(ev, name, body, extra):
    """한 항목 설명 → 카드 블록 (answer.view 와 같은 모양) — 문장과 같은 근거 · 같은 순서:
    큰 금액 = 증감 / 건수 · 평균 / 큰 결제 / 많이 몰린 때(시간대 · 요일) / 새로 간 곳 · 자주 가던 곳 · 안 간 곳 /
    (두 기간 모두 간 가게가 있을 때만) 많이 늘어난 곳 · 줄어든 곳 / 맨 아래 = 잇는 문장 · 줄이는 방법 · 단정 불가 안내"""
    cur, prev, moves, v = *ev["labels"], ev["moves"], ev["visits"]
    up = ev["delta"] > 0
    word = "곳" if moves["kind"] == "merchant" else "카테고리"
    note = VIEW["rate_up" if up else "rate_down"].format(rate=_plain(ev["rate"])) if ev["rate"] is not None else f"{prev}엔 0원"
    blocks = [{"kind": "head", "title": f"{name} · {prev}{josa(prev, ('과', '와'))} 비교"},
              {"kind": "total", "caption": f"{prev}보다 {'늘어난' if up else '줄어든'} 금액", "amount": abs(ev["delta"]), "note": note,
               "direction": "up" if up else "down"}]
    rows = [{"name": "결제 건수", "value": f"{ev['previous']['count']}건 → {ev['current']['count']}건"}]
    if ev["previous"]["average"] and ev["current"]["average"]:
        rows.append({"name": "건당 평균", "value": f"{won(ev['previous']['average']['value'])} → {won(ev['current']['average']['value'])}"})
    blocks.append({"kind": "rows", "title": f"거래에서 확인한 변화 ({prev} → {cur})", "rows": rows})
    if ev["big"]:
        blocks.append({"kind": "rows", "title": f"{cur} 큰 결제 (고정지출 제외)", "rows": [
            {"name": b["name"] or "이름 없는 곳", "amount": b["amount"], "note": day(b["day"])}
            for b in ev["big"]]})
    if ev["shift"]:
        way = "up" if up else "down"
        blocks.append({"kind": "rows", "title": "늘어난 금액이 몰린 때" if up else "줄어든 금액이 큰 때", "rows": [
            {"name": name, "value": f"{won(r['before'])} → {won(r['after'])}",
             "note": ("+" if r["delta"] > 0 else "−") + won(abs(r["delta"])), "direction": way}
            for name, r in ((ev["shift"]["slots"] and f"{ev['shift']['slots']['name']} 시간대", ev["shift"]["slots"]),
                            (ev["shift"]["weekdays"] and f"{ev['shift']['weekdays']['name']} 하루 평균", ev["shift"]["weekdays"])) if r]})
    visit_rows = [r for r in (
        v["new_total"] and {"name": f"새로 간 곳 ({v['new_count']}곳)", "value": "+" + won(v["new_total"]), "direction": "up"},
        v["regular_delta"] and {"name": f"자주 가던 곳 ({v['regular_count']}곳)",
                                "value": ("+" if v["regular_delta"] > 0 else "−") + won(abs(v["regular_delta"])),
                                "direction": "up" if v["regular_delta"] > 0 else "down"},
        v["gone_total"] and {"name": f"이번엔 안 간 곳 ({v['gone_count']}곳)", "value": "−" + won(v["gone_total"]), "direction": "down"},
    ) if r]
    if visit_rows:
        blocks.append({"kind": "rows", "title": "새로 간 곳 · 자주 가던 곳", "rows": visit_rows})
    if moves["kind"] == "category" or v["regular_count"]:
        for side, title, sign, way in (("up", f"많이 늘어난 {word}", "+", "up"), ("down", f"줄어든 {word}", "−", "down")):
            if moves[side]:
                blocks.append({"kind": "rows", "title": title, "rows": [
                    {"name": r["name"], "value": f"{won(r['before'])} → {won(r['after'])}", "note": sign + won(abs(r["delta"])),
                     "direction": way} for r in moves[side]]})
    said = [*(body or []), *[line for line in (_visits_line(ev), _reduce_line(ev, name)) if line]]
    return blocks + [{"kind": "text", "lines": [*said, TEXT["basis_below"], *extra]}]


def checked(raw, table):
    """설명 LLM 응답 → 서버 값을 끼운 문장 목록. 형식 · 숫자 · 모르는 키 · 금지 표현 · 자리표시 없는 문장이면 None"""
    try:
        sentences = json.loads(raw)["sentences"]
    except Exception:  # JSON 아님 · 칸 없음
        return None
    if not isinstance(sentences, list) or not 1 <= len(sentences) <= 3:
        return None
    out = []
    for s in sentences:
        if not isinstance(s, str) or re.search(r"[0-9%]", re.sub(r"\{F\d+\}", "", s)) or any(b in s for b in BANNED):
            return None
        keys = re.findall(r"\{(F\d+)\}", s)
        if not keys or any(k not in table for k in keys) or re.search(r"\{(?!F\d+\})", s):
            return None
        # 서술어까지 든 값('…늘었어요')의 자리표시 바로 뒤에 글자를 붙이면 문법이 깨진다 ('42,000원 늘었어요했어요') → 거절
        if any(table[k].endswith("요") and re.search(r"\{" + k + r"\}(?![.!?]|$)", s.strip()) for k in keys):
            return None
        s = s.strip() if s.strip()[-1:] in ".!?" else s.strip() + "."  # 마침표 없이 끝나면 붙인다
        out.append(PLACED.sub(lambda m: _place(table[m.group(1) or m.group(3)], m.group(2)), s))
    return out


def _place(value, particle):
    return value + (josa(value, PARTICLES[particle]) if particle else "")


def _mix_answer(ev, item):
    """구성 설명 → (문장, 카드 블록). 설명 LLM 은 쓰지 않는다 (템플릿만 — 증감 설명용 프롬프트라)"""
    name = NAMES[ev["category"]] if ev["top"] else scope(item)
    label = f"이번 달({span(ev['period'])})"
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
    blocks = [{"kind": "head", "title": f"{name} · 이번 달 쓴 곳"},
              {"kind": "total", "caption": f"{span(ev['period'])} 쓴 금액", "amount": ev["amount"],
               "note": TEXT["mix_fixed"].format(fixed=won(ev["fixed"])) if ev["fixed"] > 0 else None}]
    if rows:
        blocks.append({"kind": "rows", "rows": rows})
    if pairs:
        blocks.append({"kind": "rows", "title": "많이 쓴 곳" if ev["category"] else "많이 쓴 카테고리",
                       "rows": [{"name": k, "amount": a, "ratio": str(R.percent(a, pairs[0][1])["value"])} for k, a in pairs]})
    texts = ([TEXT["mix_top"].format(name=name)] if ev["top"] else []) + [TEXT["mix_basis"]]
    return lines, blocks + [{"kind": "text", "lines": texts}]


def answer(user, items, complete=None, today=None):
    """설명 요청의 항목들 → {"lines", "notices", "explain": {llm, rejected, failed}} (항목마다 근거 → 문장)
    llm = 설명 LLM 을 쓰게 했는지, rejected = 응답을 검사에서 버림, failed = 호출 실패 · 상한 (둘 다 템플릿으로 답함, 개발 표시용)"""
    lines, rejected, failed, views, suggest = [], False, False, [], False
    for item in items:
        ev = evidence(user, item, today)
        name = scope(item)  # 세부 지출 대신 본 항목은 "식비 전체"
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
        lines.append(TEXT["basis"])
        body = None
        if complete is not None:
            table = facts(ev)
            payload = {"scope": name, "requested": item.get("instead_of"), "direction": "up" if ev["delta"] > 0 else "down",
                       "facts": [{"key": f["key"], "label": f["label"]} for f in table]}
            try:
                raw = complete(PROMPT, json.dumps(payload, ensure_ascii=False))
            except Exception:  # 호출 오류 · 상한 — 종류를 모른다, 템플릿으로 (재시도 없음)
                failed = True
            else:
                body = checked(raw, {f["key"]: f["text"] for f in table})
                rejected = rejected or body is None
        lines += (body or _template(ev)) + _evidence_lines(ev, name)
        suggest = suggest or (bool(ev["reduce"]) and item.get("category") not in OUT_OF_BUDGET)  # 예산 범위 밖엔 조정 버튼 없음
        extra = []
        if item.get("instead_of"):
            extra.append(TEXT["instead_of"].format(name=name, word=item["instead_of"]))
        if ev["unnamed"] and (ev["moves"]["up"] or ev["moves"]["down"]):
            extra.append(TEXT["unnamed"])
        lines += extra
        views.append(view(ev, name, body, extra))
    out = {"lines": lines, "notices": [], "explain": {"llm": complete is not None, "rejected": rejected, "failed": failed}}
    if len(items) == 1 and views:  # 한 항목 설명만 카드로 (여러 항목 · 거절은 문장 그대로)
        out["view"] = views[0]
    if suggest:  # 줄이는 방법을 보였으면 [예산 조정하기] 제안 (문구 · 선택지는 라우터 안내가 붙인다)
        out["suggest"] = ["budget_adjust"]
    return out

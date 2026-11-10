"""소비 설명 B — 서버 근거·적용 범위, 제한된 조회 계획과 답변 검사.

- 주장은 서버만 만든다: 지금 "왜 늘었어?" 근거(explain.evidence)를 그대로 옮긴 것이라 숫자가 템플릿 설명과 같다.
  {"id", "kind", "axis", "subject", "metric", "direction", "rank", "share", "text", "conditions"}
  kind = change(증감) · absent(확인 범위 안에서 없음) · unknown(확인 못 함), axis = scope · count · average · time_slot · weekday ·
  visit · merchant · category, share = 전체 증감 대비 몫 구간(small · part · most · all), text = 자리표시({C3})에 끼울 서버 값
- LLM 에 보내는 것은 for_llm(): id · 종류 · 묶음 이름(시간대 · 요일 · 카테고리 · 가게 순번) · 방향 · 순위 · 몫 · 조건 플래그. 금액 · 가게 이름 · 날짜 없음
- 비교하며 묶기(compare_group)는 첫 조회가 읽은 두 기간 집계(ev["sides"])를 다시 묶는다 → 기간 목록 · 필터가 원래 조회와 같다(DB 다시 안 읽음)
- 답 검사(check): 실제 자리표시와 선언한 id의 일치 · 질문의 대상/지표/기간 · 필수 근거 · 연결 말 허용 목록. 실패하면 답 전체를 버린다(None)
- 흐름(run): 첫 주장 → 서버 멈춤(데이터 이유만) → 계획 LLM(추가 조회 0~2개 + 이유) → 서버 인자 검사 · 묶기 → 답 LLM → 검사.
  실패 · 시간 초과면 None → 부르는 쪽(explain.answer)이 지금 템플릿 설명으로 답한다. trace = 개발용 분석 과정(금액 · 가게 이름 없음)
"""
import json
import re
import time

from . import result as R
from . import investigation as I
from . import supplement
from .answer import NAMES, josa, won
from .clauses import validate as validate_clause
from .explain import _changed, _per_day, _visits_line, regular_name
from .scope import DAY_NAMES, ONLINE, SLOTS, name as scope_name, online, plain

MAX_QUERIES = 2  # 질문 하나의 추가 조회 상한 (26-3)
TIMEOUT = 12  # 초 — B 한 번(계획 + 답)이 이보다 오래 걸리면 그 요청만 템플릿 (26-12 ④, 목표 중간값 6초)
ANSWER_MIN = 3  # 초 — 답 호출에 남겨 둘 최소 시간. 계획 호출의 대기 한도 = 남은 시간 − 이것, 답 차례에 이보다 적게 남으면 답 호출 없이 템플릿
AXES = ("category", "merchant", "time_slot", "weekday")  # 비교하며 묶기 기준 — 서버가 받는 by 값은 이것뿐
VISITS = (("new", "새로 간 곳"), ("regular", None), ("gone", "이번엔 안 간 곳"))  # None = explain.regular_name(ev) ('지난달에도 간 곳')


def _share(part, total):
    """전체 증감 대비 몫 구간 (같은 방향일 때만). small < 30% ≤ part < 50% ≤ most < 100% ≤ all"""
    if not total or part * total <= 0:
        return None
    ratio = part / total
    return "all" if ratio >= 1 else "most" if ratio >= 0.5 else "part" if ratio >= 0.3 else "small"


def _dir(delta):
    return "up" if delta > 0 else "down" if delta < 0 else "same"


def _conditions(ev, item, fixed_excluded=False):
    current, previous = ev["periods"]
    return {"coverage": "complete",  # evidence 는 두 기간 모두 완전 수집일 때만 ok (아니면 incomplete 로 멈춘다)
            "unclassified": any(ev["unclassified"]),
            "search_scope": item.get("merchant") and f"가게 이름에 '{item['merchant']}'",
            "period_match": (current.end_before - current.start) == (previous.end_before - previous.start),
            "fixed_excluded": fixed_excluded,
            # 근거의 적용 범위는 서버에 남긴다. LLM에는 for_llm의 익명 상대 정보만 보낸다.
            "basis": {"periods": [(p.start.isoformat(), p.end_before.isoformat()) for p in (current, previous)],
                      "scope": scope_name(item), "category": item.get("category"),
                      "filters": {k: item[k] for k in ("payment", "merchant", "merchant_names", "weekdays", "time_slots",
                                                       "amount_min", "amount_max", "include_fixed") if k in item}}}


class _Ids:
    def __init__(self, start=1):
        self.n = start - 1

    def __call__(self):
        self.n += 1
        return f"C{self.n}"


# ---------- 절 (자리표시 하나 = 서버가 만든 완성 문장, 26-16) ----------
# 방향 · 조사 · 단위까지 서버가 붙인다 — 답 LLM 은 절을 고르고 순서 · 연결 말만 정한다. 표현은 지금 템플릿 설명과 같은 규칙(늘었어요 · 줄었어요 · 같아요)
AXIS_WORD = {"time_slot": "시간대", "weekday": "요일", "merchant": "가게", "category": "카테고리"}
FIXED = "고정지출을 빼고 보면 "  # 시간대 · 요일 절 머리 — 답 하나에 한 번만 남긴다 (check)
DAY_GROUPS = (("주말", (5, 6)), ("평일", (0, 1, 2, 3, 4)))  # 여러 요일 묶음 가설(주말마다 …)을 확인 · 부정할 묶음 주장


def _verb(direction, metric):
    if direction == "same":
        return "같아요"
    if metric == "average":
        return "올랐어요" if direction == "up" else "내렸어요"
    return "늘었어요" if direction == "up" else "줄었어요"


def _moved(noun, before, after, unit, direction, metric, delta=True, top=None):
    """'건당 평균은 12,000원에서 15,000원으로 올랐어요.' · '저녁 시간대는 168,000원에서 150,000원으로 18,000원 줄었어요.'
    top = 같은 방향 1위인 묶음 이름(시간대 · 가게 …) → '… 84,000원 늘어 시간대 중 가장 많이 늘었어요.'"""
    b, a = unit(before), unit(after)
    head = f"{noun}{josa(noun, ('은', '는'))}"
    if direction == "same":
        return f"{head} {b}{josa(b, ('으로', '로'))} 같아요."
    gap = f" {unit(abs(after - before))}" if delta else ""
    verb = _verb(direction, metric)
    if top:
        return f"{head} {b}에서 {a}{josa(a, ('으로', '로'))}{gap} {verb[:-3]}어 {top} 중 가장 많이 {verb}."
    return f"{head} {b}에서 {a}{josa(a, ('으로', '로'))}{gap} {verb}."


def _share_tail(share, total_direction):
    """전체 증감의 절반 이상인 묶음만 한 마디 (서버 값으로만)"""
    return f" 전체 {'증가분' if total_direction == 'up' else '감소분'}의 절반 이상이에요." if share == "most" else ""


def first_claims(ev, item):
    """explain.evidence(ok, 증감) → 첫 주장 목록. 범위 증감 · 건수 · 건당 평균 · 가장 크게 바뀐 시간대 · 요일 · 새로 간 곳/자주 가던 곳 ·
    가게(카테고리)별 늘어난 · 줄어든 곳 — 모두 템플릿 설명과 같은 값. text = 그 주장의 완성 문장(자리표시 자리에 들어감)"""
    nid, delta, out = _Ids(), ev["delta"], []
    base = _conditions(ev, item)
    total = _dir(delta)
    # 확인 필요가 있으면 증감률을 단정하지 않는다 (25-13) — 범위 주장에 률을 싣지 않음
    rate = None if base["unclassified"] or ev["rate"] is None else ev["rate"]["value"]
    name, prev = scope_name(item), ev["labels"][1]
    basis = "분류된 내역 기준으로 " if base["unclassified"] else ""
    out.append({"id": nid(), "kind": "change", "axis": "scope", "subject": name, "metric": "amount", "direction": total,
                "rank": None, "share": None, "rate": rate, "conditions": base, "size": abs(delta),
                "text": f"{name} 지출은 {basis}{prev}보다 {won(abs(delta))} {_verb(total, 'amount')}."})
    b, a = ev["previous"], ev["current"]
    out.append({"id": nid(), "kind": "change", "axis": "count", "subject": "결제 건수", "metric": "count",
                "direction": _dir(a["count"] - b["count"]), "rank": None, "share": None, "conditions": base,
                "text": _moved("결제 건수", b["count"], a["count"], lambda n: f"{n}건", _dir(a["count"] - b["count"]), "count", False)})
    if b["average"] and a["average"]:
        way = _dir(a["average"]["value"] - b["average"]["value"])
        out.append({"id": nid(), "kind": "change", "axis": "average", "subject": "건당 평균", "metric": "average",
                    "direction": way, "rank": None, "share": None, "conditions": base,
                    "text": _moved("건당 평균", b["average"]["value"], a["average"]["value"], won, way, "average", False)})
    fixed = _conditions(ev, item, fixed_excluded=True)
    shift = ev["shift"] or {}
    for axis, key, noun in (("time_slot", "slots", lambda r: f"{r['name']} 시간대"), ("weekday", "weekdays", lambda r: f"{r['name']} 하루 평균")):
        r = shift.get(key)
        if r:
            share = _share(r["delta"], delta) if axis == "time_slot" else None
            out.append({"id": nid(), "kind": "change", "axis": axis, "subject": r["name"],
                        "metric": "amount" if axis == "time_slot" else "day_average", "direction": _dir(r["delta"]), "rank": 1,
                        "share": share, "conditions": fixed, "size": abs(r["delta"]),
                        "text": FIXED + _moved(noun(r), r["before"], r["after"], won, _dir(r["delta"]), "amount",
                                                          top=AXIS_WORD[axis]) + _share_tail(share, total)})
    v = ev["visits"]
    for key, place in VISITS:
        place = place or regular_name(ev)
        part = {"new": v["new_total"], "regular": v["regular_delta"], "gone": -v["gone_total"]}[key]
        if part:
            n = {"new": v["new_count"], "regular": v["regular_count"], "gone": v["gone_count"]}[key]
            share = _share(part, delta)
            out.append({"id": nid(), "kind": "change", "axis": "visit", "subject": place, "metric": "amount",
                        "direction": _dir(part), "rank": None, "share": share, "conditions": base, "size": abs(part),
                        "text": f"{place}({n}곳)에서 {won(abs(part))} {_verb(_dir(part), 'amount')}." + _share_tail(share, total)})
    moves = ev["moves"]
    if moves["kind"] == "category" or v["regular_count"]:  # 템플릿과 같은 조건에서만 가게(카테고리)별 줄
        for side in ("up", "down"):
            for r, rank, alone in _ranked(moves[side]):
                out.append(_group_claim(nid(), moves["kind"], r, rank, delta, base, alone=alone))
    # 기초 근거도 같은 대상 코드로 식별한다. 이름은 표시 전용이다.
    for claim in out:
        if claim["axis"] in ("scope", "count", "average"):
            claim["target"] = "scope"
        elif claim["axis"] == "visit":
            claim["target"] = next((f"visit:{key}" for key, label in VISITS if claim["subject"] == (label or regular_name(ev))), None)
        elif claim["axis"] in ("time_slot", "weekday") and (
                item.get("include_fixed") or (claim["axis"] == "time_slot" and any(
                    side.get("metric_groups", {}).get("time_slot", {}).get(None, {}).get("count", 0) for side in ev["sides"]))):
            # 기존 시간대 요약은 고정지출 제외다. 포함 요청·시각 누락 때는 정밀 조회로 확인한다.
            claim["target"] = None
        else:
            claim["target"] = next((key for key, spec in I.TARGETS.items()
                                    if spec["by"] == claim["axis"] and spec["label"] == claim["subject"]), None)
    return out


def _ranked(rows):
    """증감 크기 순서 줄 → [(줄, 순위, 혼자 1위인지)]. 같은 크기는 같은 순위 — '가장'은 1위가 하나일 때만 (템플릿의 '뚜렷한 한 요일' 규칙과 같은 뜻)"""
    out, rank, last = [], 0, None
    for n, r in enumerate(rows, 1):
        if abs(r["delta"]) != last:
            rank, last = n, abs(r["delta"])
        out.append((r, rank))
    firsts = sum(1 for _, k in out if k == 1)
    return [(r, k, k == 1 and firsts == 1) for r, k in out]


def _group_claim(cid, axis, r, rank, total, conditions, metric="amount", alone=True):
    """묶음 한 줄 → 주장. 가게 이름은 subject 에 두되 for_llm 이 순번으로 바꾼다 (이름은 서버가 끼우는 문장에만).
    alone = 1위가 혼자인지 (동률 1위면 '가장'을 쓰지 않는다)"""
    noun = {"time_slot": f"{r['name']} 시간대", "weekday": f"{r['name']} 하루 평균"}.get(axis, r["name"] or "이름 없는 곳")
    way, share = _dir(r["delta"]), _share(r["delta"], total)
    text = _moved(noun, r["before"], r["after"], won, way, "amount", top=AXIS_WORD.get(axis) if rank == 1 and alone else None) \
        + _share_tail(share, _dir(total) if total else way)
    if axis in ("time_slot", "weekday"):
        text = FIXED + text
    return {"id": cid, "kind": "change", "axis": axis, "subject": r["name"], "metric": metric, "direction": way,
            "rank": rank, "share": share, "text": text, "conditions": conditions, "size": abs(r["delta"])}


def compare_group(ev, item, by, focus=None, start=1):
    """비교하며 묶기: 두 기간 집계를 by 묶음으로 나눠 증감 순위 주장. 인자 검사는 여기서(서버) — by 는 AXES, focus 는 by="merchant" 의
    별칭 표 묶음(예: 배달) 하나만. 같은 방향(전체 증감 쪽) 변화만 순위(1 = 가장 큼), 반대 방향은 rank None.
    focus 로 찾은 결제가 없으면 absent 주장 하나 — 조건에 검색 범위(별칭 표 브랜드 이름)를 붙인다"""
    if by not in AXES:
        raise ValueError(f"묶을 수 없는 기준: {by!r}")
    if by == "category" and item.get("category"):
        raise ValueError("카테고리로 좁힌 조회는 카테고리로 묶지 않는다")
    if focus is not None and (by != "merchant" or not online(focus)[1]):  # 묶음('배달') · 브랜드('배민') 모두 별칭 표에 있어야
        raise ValueError(f"좁힐 수 없는 묶음: {focus!r}")
    now, before = ev["sides"]
    nid, delta, base = _Ids(start), ev["delta"], _conditions(ev, item, fixed_excluded=by in ("time_slot", "weekday"))
    if by == "weekday":  # 요일은 두 기간의 요일 수가 달라 하루 평균으로 (지금 설명과 같은 규칙)
        rows = [{"key": d, "name": f"{DAY_NAMES[d]}요일", "before": _per_day(before, d), "after": _per_day(now, d)} for d in range(7)]
    else:
        field = {"category": "categories", "merchant": "merchants", "time_slot": "slots"}[by]
        keys = [k for k in set(now[field]) | set(before[field]) if k is not None]
        name = {"category": lambda k: NAMES[k], "time_slot": lambda k: SLOTS[k][0]}.get(by, lambda k: k)
        rows = [{"key": k, "name": name(k), "before": before[field].get(k, 0), "after": now[field].get(k, 0)} for k in keys]
    if focus is not None:
        group, words = online(focus)
        rows = [r for r in rows if any(plain(w) in plain(r["key"]) for w in words)]
        if not rows:  # 브랜드로 물었으면(group 없음) 그 브랜드 표기만 찾은 것 — 범위도 그 표기로
            name = group or focus
            shown = list(ONLINE[group]["brands"])[:3] if group else list(words)
            scope = {**base, "search_scope": f"{name} 이름({' · '.join(shown)}{' 등' if group else ''})"}
            return [{"id": nid(), "kind": "absent", "axis": "merchant", "subject": name, "metric": "count", "direction": None,
                     "rank": None, "share": None, "conditions": scope,
                     "text": f"확인한 {scope['search_scope']} 범위에서는 결제가 없었어요."}]
    for r in rows:
        r["delta"] = r["after"] - r["before"]
    sign = (delta > 0) - (delta < 0)
    same = sorted((r for r in rows if r["delta"] * sign > 0), key=lambda r: (-abs(r["delta"]), str(r["key"])))
    other = sorted((r for r in rows if r["delta"] * sign < 0), key=lambda r: (-abs(r["delta"]), str(r["key"])))
    metric = "day_average" if by == "weekday" else "amount"
    out = [_group_claim(nid(), by, r, rank, delta if by != "weekday" else 0, base, metric, alone)
           for r, rank, alone in _ranked(same)]
    out += [_group_claim(nid(), by, r, None, 0, base, metric) for r in other]
    if focus is not None:
        # 한 브랜드의 증감으로 배달앱 전체를 판단하지 않도록 검색 범위 전체의 합계를 따로 둔다.
        group, words = online(focus)
        name = group or focus
        b, a = sum(r["before"] for r in rows), sum(r["after"] for r in rows)
        row = {"name": name, "before": b, "after": a, "delta": a - b}
        conditions = {**base, "search_scope": name}
        aggregate = _group_claim(nid(), by, row, None, delta, conditions)
        aggregate["text"] = "확인한 가게 이름 범위에서 " + aggregate["text"]
        out.append({**aggregate, "group": True})
    if by == "weekday":  # 주말 · 평일 묶음 하루 평균 (묶음 합계 ÷ 그 기간의 그 요일 수) — 순위 없음
        for name, days in DAY_GROUPS:
            per = [_group_day(side, days) for side in (before, now)]
            row = {"key": name, "name": name, "before": per[0], "after": per[1], "delta": per[1] - per[0]}
            out.append({**_group_claim(nid(), by, row, None, 0, base, metric), "group": True})
    return out


def _group_day(side, days):
    count = sum(side["weekday_days"].get(d, 0) for d in days)
    return R.average(sum(side["weekdays"].get(d, 0) for d in days), count)["value"] if count else 0


def target_claims(ev, item, hyp, start):
    """질문한 대상·지표 하나를 같은 기간/필터의 집계에서 계산한다. 외부 조회·범위 확장 없음."""
    target, metric = hyp["target"], hyp["metric"]
    spec = I.TARGETS[target]
    axis = spec["by"]
    if axis == "unavailable":
        return [], "unsupported_target"
    if not I.compatible(item, target):
        return [], "outside_scope"
    if axis in ("scope", "visit"):
        return [], "no_comparable_metric"
    sides = ev["sides"]
    if any("metric_groups" not in side for side in sides):
        return [], "missing_metric_data"
    if axis in ("time_slot", "merchant") and any(side["metric_groups"][axis].get(None, {}).get("count", 0) for side in sides):
        return [], "missing_time_or_merchant"
    values, counts = [], []
    for side in sides:
        cells = side["metric_groups"][axis]
        keys = [k for k in cells if k is not None and
                (any(plain(w) in plain(k) for w in spec["keys"]) if axis == "merchant" else k in spec["keys"])]
        amount = sum(cells[k]["amount"] for k in keys)
        count = sum(cells[k]["count"] for k in keys)
        counts.append(count)
        if metric == "average":
            if not count:
                return [], "empty_average"
            value = R.average(amount, count)["value"]
        elif metric == "day_average":
            days = sum(side["weekday_days"].get(d, 0) for d in spec["keys"])
            if not days:
                return [], "no_matching_days"
            value = R.average(amount, days)["value"]
        else:
            value = count if metric == "count" else amount
        values.append(value)
    after, before = values
    fixed = axis in ("time_slot", "weekday") and not item.get("include_fixed")
    cond = _conditions(ev, item, fixed_excluded=fixed)
    label = spec["label"]
    noun = label + {"amount": " 지출", "count": " 결제 건수", "average": " 건당 평균", "day_average": " 하루 평균"}[metric]
    text = _moved(noun, before, after, (lambda n: f"{n}건") if metric == "count" else won,
                  _dir(after - before), metric, delta=metric == "amount")
    kind = "change"
    if axis == "merchant":
        cond = {**cond, "search_scope": label}
        text = "확인한 가게 이름 범위에서 " + text
        if not any(counts):
            kind = "absent"
            text = f"확인한 {label} 이름 범위에서는 두 기간 모두 결제가 없었어요."
    if fixed:
        text = FIXED + text
    return [{"id": f"C{start}", "target": target, "metric": metric, "axis": hyp["axis"], "subject": label,
             "kind": kind, "direction": _dir(after - before), "rank": None, "share": None, "conditions": cond,
             "text": text, "before": before, "after": after}], "target_metric_compared"


def stop(ev, item, claims, used=0):
    """추가 조회 전 멈춤 판정(데이터 이유만) → 이유 | None. 이유: insufficient(근거 부족 · 늘지 않음) · nothing_more(더 볼 묶음 없음 ·
    상한). None 이면 계획 LLM 이 추가 조회 0~2개를 고른다 — 첫 결과로 답할 수 있으면 0개 + 이유 (26-12 ②)"""
    if ev.get("state") != "ok" or ev.get("kind") == "mix" or ev["delta"] <= 0:
        return "insufficient"  # 수집 미완 · 비교 기준 없음 · 바뀐 내역 · 늘지 않음 → 지금 거절 · 안내 문구
    if used >= MAX_QUERIES:
        return "nothing_more"
    now, before = ev["sides"]
    if now["count"] + before["count"] <= 1:
        return "nothing_more"  # 결제가 한 건뿐이면 묶어 볼 것이 없다
    if not candidates(ev, item, claims):
        return "nothing_more"
    return None


def candidates(ev, item, claims):
    """추가 조회로 새로 볼 수 있는 묶음 기준 — 첫 주장에 순위 전체가 없고(가장 큰 것 하나뿐) 범위와 같은 축이 아닌 것"""
    listed = {c["axis"] for c in claims if c["rank"] and c["rank"] > 1}
    return [a for a in AXES if a not in listed and not (a in ("category", "merchant") and item.get(a))]


def for_llm(claims):
    """계획 · 답 LLM 에 보낼 모양 — 금액 · 가게 이름 · 날짜 없이 id 와 상대 정보만 (23-11 · 26-10 ③)"""
    shops, out = {}, []
    for c in claims:
        subject = c["subject"]
        if c["axis"] == "merchant" and c["kind"] != "absent":
            subject = shops.setdefault(subject, f"가게 {len(shops) + 1}")
        cond = c["conditions"]
        out.append({"id": c["id"], "kind": c["kind"], "axis": c["axis"], "subject": subject, "metric": c["metric"],
                    "direction": c["direction"], "rank": c["rank"], "share": c["share"],
                    **({"target": c["target"]} if c.get("target") else {}),
                    "flags": [k for k in ("unclassified", "fixed_excluded") if cond[k]] + (["search_scope"] if cond["search_scope"] else [])
                    + ([] if cond["period_match"] else ["period_mismatch"])})
    return out


# ---------- 답 검사 (26-4 · 26-16) ----------
# 관측 사실로 원인·가격·행동을 긍정/부정하지 않는다. 예외 금지어 대신 허용된 연결 말만 검사한다.


HYPOTHESIS_AXES = ("count", "average", "time_slot", "weekday", "visit", "merchant", "category")
QUERY_TOP_SHARE = 0.2  # 추가 조회 1위를 꼭 쓸 최소 크기 (범위 전체 증감 대비)


def key_claims(claims):
    """가설 없는 '왜 늘었어?'에 꼭 인용할 핵심 요인 — 전체와 같은 방향으로 가장 크게 바뀐 묶음 주장(시간대 · 새로 간 곳/지난달에도 간 곳 · 가게 · 카테고리,
    금액 기준). 동률이면 모두 후보(그중 하나만 인용하면 된다). 요일(하루 평균)은 단위가 달라 뺀다"""
    total = claims[0]["direction"]
    pool = [c for c in claims if c["kind"] == "change" and c["axis"] in ("time_slot", "visit", "merchant", "category")
            and c["direction"] == total and not c.get("group") and c.get("size")]
    top = max((c["size"] for c in pool), default=None)
    return [c["id"] for c in pool if c["size"] == top]


def offsets(ev):
    """일부가 전체보다 클 때 붙일 맞물림 문장 (축마다 하나, 서버 값 · 템플릿 표현). 인용한 묶음 주장의 금액이 범위 전체 증감보다 크면
    반대로 움직인 쪽을 이어 전체와 맞춘다 — 새로 간 곳/지난달에도 간 곳은 템플릿의 잇는 문장 그대로(세 합 = 전체 증감).
    가게 · 카테고리는 전체 결제 기준이라 '전체로는'까지, 시간대는 고정지출을 뺀 기준이라 반대쪽 합계만"""
    now, before = ev["sides"]
    up = ev["delta"] > 0
    out = {"visit": _visits_line(ev)}
    # 시간대는 고정지출을 뺀 기준이라 전체와 맞물리지 않고, 앞에서 말한 줄어든 시간대와 겹쳐 읽힌다 → 붙이지 않는다 (26-19)
    for axis, field, word in (("merchant", "merchants", "곳"), ("category", "categories", "카테고리")):
        deltas = [now[field].get(k, 0) - before[field].get(k, 0) for k in set(now[field]) | set(before[field])]
        opposite = -sum(d for d in deltas if (d < 0 if up else d > 0))
        if not opposite:
            continue
        head = f"반대로 {'줄어든' if up else '늘어난'} {word}에서 {won(abs(opposite))}"
        out[axis] = f"{head} {'줄어' if up else '늘어'}, 전체로는 {_changed(ev['delta'])}."
    return {k: v for k, v in out.items() if v}


def _short(join):
    """범위 증감 절도 인용한 답이면 맞물림 문장의 ', 전체로는 ○원 늘었어요' 꼬리를 뗀다 (같은 숫자를 두 번 말하지 않게)"""
    return re.sub(r"(늘|줄)어, 전체로는 .*$", r"\1었어요.", join)


def _subject(value):
    """대상 비교는 정확히 일치하거나 서버의 별칭 표로 같은 대상일 때만. 부분 문자열은 쓰지 않는다."""
    group, words = online(value)
    return plain(group or (words[0] if words else value))


def _matches(claim, hyp):
    """지표와 대상이 같은 근거만 선택한다. 전체 건수/평균은 이름 붙은 부분 집합의 근거가 아니다."""
    if "target" in hyp:
        return (hyp["target"] != "unavailable" and claim.get("target") == hyp["target"]
                and claim["metric"] == hyp["metric"] and claim["kind"] in ("change", "absent"))
    if claim["axis"] != hyp["axis"] or claim["kind"] not in ("change", "absent"):
        return False
    subject = hyp.get("subject")
    if not subject:
        return hyp["axis"] in ("count", "average", "visit")
    actual = (claim.get("conditions", {}).get("basis") or {}).get("scope") \
        if hyp["axis"] in ("count", "average") else claim["subject"]
    return actual is not None and _subject(subject) == _subject(actual)


def support(claims, hypothesis):
    """서버가 판정한 답변 계약. 근거 없음도 정상 결과이며 전체 지표로 메우지 않는다."""
    if hypothesis is None:
        return {"status": "observation", "claims": [], "limitation": None}
    basis = claims[0].get("conditions", {}).get("basis")
    matched = [c["id"] for c in claims if _matches(c, hypothesis)
               and c.get("conditions", {}).get("basis") == basis]
    if not matched:
        limit = "질문하신 대상과 지표를 함께 확인할 비교 근거가 없어요. 전체 지출 통계만으로는 그 가설을 판단할 수 없어요."
        if hypothesis.get("target") == "unavailable":
            limit = f"{hypothesis['unavailable']}에 해당하는 내역을 지금 자료에서 구분할 수 없어요. 가게 이름이나 카테고리 전체 결제로 대신 판단하지 않을게요."
        return {"status": "insufficient", "claims": [], "limitation": limit}
    if all(c["kind"] == "absent" for c in claims if c["id"] in matched):
        return {"status": "observation", "claims": matched,
                "limitation": "확인한 가게 이름으로 기록된 결제에 한정한 결과예요. 다른 이름으로 기록된 결제까지 없었다는 뜻은 아니에요."}
    limit = "거래에서 확인한 변화예요. 이것만으로 실제 원인이나 구매 행동을 단정할 수는 없어요."
    if hypothesis.get("metric") == "count":
        limit = "결제 내역의 건수를 비교한 결과예요. 결제 건수만으로 방문·약속 횟수나 구매한 개수를 알 수는 없어요."
    if hypothesis.get("metric", hypothesis["axis"]) == "average":
        limit = "결제 한 건당 평균의 변화예요. 구매한 품목이나 수량을 알 수 없어 상품 가격이 올랐는지는 판단할 수 없어요."
    return {"status": "observation", "claims": matched, "limitation": limit}


def next_check(claims, hypothesis, contract, cited_ids=()):
    """확인된 근거 다음에 볼 항목 하나. 절약액·원인·예산 변경 효과를 만들어내지 않는다."""
    if contract["status"] != "observation":
        return None
    matched = [c for c in claims if c["id"] in contract["claims"]]
    if hypothesis is None:
        return "예산을 조정하기 전, 늘어난 항목에 일시적인 큰 결제가 포함됐는지 먼저 확인해 보세요."
    if matched and all(c["kind"] == "absent" for c in matched):
        return "예산을 조정하기 전, 이번 내역에서 실제로 증가한 다른 항목을 확인해 보세요."
    metric = hypothesis.get("metric", hypothesis["axis"])
    counterpart = {"average": "count", "count": "average"}.get(metric)
    if counterpart and any(c["id"] in cited_ids and c.get("metric") == counterpart
                           and c.get("target") == hypothesis.get("target") for c in claims):
        return "예산을 조정하기 전, 반복되는 지출인지 일시적인 큰 결제인지 확인해 보세요."
    if metric == "average":
        return "같은 범위의 결제 건수도 늘었는지 함께 보면, 금액이 늘어난 부분을 더 좁혀 볼 수 있어요."
    if metric == "count":
        return "같은 범위의 건당 평균도 함께 보면, 횟수와 한 번에 쓴 금액의 변화를 나눠 볼 수 있어요."
    if matched and all(c["direction"] != "up" for c in matched):
        return "예산을 조정하기 전, 이번 내역에서 실제로 증가한 다른 항목을 확인해 보세요."
    return "같은 범위의 결제 건수와 건당 평균을 나눠 보면, 어떤 부분부터 조정할지 판단하는 데 도움이 돼요."


def check(raw, claims, hypothesis=None, must=(), joins=None):
    """답 LLM 응답 {"sentences": [{"text", "claims"}]} → (서버 절을 끼운 문장 목록 | None, 탈락 이유 목록)
    hypothesis = 계획 LLM이 읽은 가설 {axis, subject} | None. 같은 대상·지표·기간의 근거를 첫 문장에 쓰며 인과 판정은 하지 않는다.
    must = 꼭 인용할 주장 묶음 목록 [[id 후보 …], …] — 묶음마다 하나 이상 (가설 없을 때 핵심 요인 · 실행한 추가 조회의 1위, 26-18)
    joins = offsets(ev) — 인용한 묶음 주장이 전체 증감보다 크면 그 문장 뒤에 맞물림 문장을 붙인다"""
    try:
        sentences = json.loads(raw)["sentences"]
    except Exception:  # JSON 아님 · 칸 없음
        return None, ["format"]
    if not isinstance(sentences, list) or not 1 <= len(sentences) <= 4:
        return None, ["format"]
    known = {c["id"]: c for c in claims}
    reasons, out = [], []
    contract = support(claims, hypothesis)
    if contract["status"] == "insufficient":
        return None, ["unsupported_hypothesis"]
    for i, s in enumerate(sentences, 1):
        if not isinstance(s, dict) or not isinstance(s.get("text"), str) or not isinstance(s.get("claims"), list):
            return None, ["format"]
        text, ids = s["text"], s["claims"]
        why = validate_clause(text, ids, known)
        if why:
            reasons += [f"{i}:{w}" for w in why]
            continue
        filled = text.strip()  # 절은 아래 _joined 에서 끼운다 (맞물림 문장으로 바꿀 절이 있어서)
        out.append(re.sub(r"\.\s*\.", ".", re.sub(r"\s+", " ", filled)))
    cited = {i for s in sentences for i in re.findall(r"\{(C\d+)\}", s["text"])}
    if contract["claims"] and not set(contract["claims"]) & set(re.findall(r"\{(C\d+)\}", sentences[0]["text"])):
        reasons.append("0:hypothesis_first")
    for n, group in enumerate(must, 1):
        if group and not cited & set(group):
            reasons.append(f"0:missing_must{n}")  # 핵심 요인 · 고른 추가 조회의 1위를 빼지 않는다
    if reasons:
        return None, reasons
    out = _joined(sentences, out, known, claims, joins or {})
    seen = False  # '고정지출을 빼고 보면'은 답 전체에서 처음 한 번만
    for n, line in enumerate(out):
        while FIXED in line and seen:
            line = line.replace(FIXED, "", 1)
        if FIXED in line:
            head, _, tail = line.partition(FIXED)
            line, seen = head + FIXED + tail.replace(FIXED, ""), True
        out[n] = line
    return out, []


def _joined(sentences, texts, known, claims, joins):
    """자리표시 → 서버 절. 인용한 묶음 주장(같은 방향)이 범위 전체 증감보다 크면 그 절을 맞물림 문장으로 바꾼다(같은 숫자를 두 번 말하지 않게, 축마다
    한 번). 답이 같은 축의 반대 방향 주장도 인용했으면 겹쳐 읽혀 바꾸지 않는다 (26-19)"""
    whole, total = claims[0].get("size", 0), claims[0]["direction"]
    cited = [known[i] for s in sentences for i in s["claims"]]
    scope_cited = any(c["axis"] == "scope" for c in cited)
    joined, out, previous_axis = set(), [], None
    for text in texts:
        def place(m):
            c = known[m.group(1)]
            overlap = any(o["axis"] == c["axis"] and o["direction"] not in (total, None) for o in cited)
            if joins.get(c["axis"]) and c["axis"] not in joined and c["direction"] == total and c.get("size", 0) > whole and not overlap:
                joined.add(c["axis"])
                if c["axis"] == "visit" and scope_cited:
                    # 방문 유형은 같은 전체 거래를 나눈 분할이다. 세 유형을 모두 나열하는 대신
                    # 선택한 유형과 나머지 유형의 순변화를 서버 값으로 설명한다.
                    rest = won(c["size"] - whole)
                    verb = "줄어" if total == "up" else "늘어"
                    return f"{c['text']} 다른 방문 유형에서는 합계 {rest} {verb} 이 변화의 일부를 상쇄했어요."
                join = _short(joins[c["axis"]]) if scope_cited else joins[c["axis"]]
                # 새로 간 곳/지난달에도 간 곳 문장은 그 절의 금액까지 담아 절을 바꾸고, 가게 · 카테고리 문장은 반대쪽만 말해 절 뒤에 잇는다
                return join if c["axis"] == "visit" else f"{c['text']} {join}"
            return c["text"]
        axes = {known[i]["axis"] for i in re.findall(r"\{(C\d+)\}", text)}
        axis = next(iter(axes)) if len(axes) == 1 else None
        filled = re.sub(r"\.\s*\.", ".", re.sub(r"\s+", " ", re.sub(r"\{(C\d+)\}", place, text)))
        if axis in AXIS_WORD and previous_axis not in (None, "scope", axis):
            filled = f"같은 기간 내역을 {AXIS_WORD[axis]}별로 보면, {filled}"
        out.append(filled)
        previous_axis = axis
    return out


# ---------- 흐름 (26-1) ----------
PLAN_PROMPT = """소비 분석의 질문과 다음 확인을 정한다. 금액 계산·원인 단정은 하지 않는다.
question_hypothesis가 있으면 그대로 사용하고, 없으면 question에서 가설을 읽는다.
""" + I.INSTRUCTION + """
claims의 대상·지표로 이미 답할 수 있으면 queries=[]. 가설에 필요한 지표가 없으면 그 대상에 해당하는 by 하나만 요청한다.
서버는 같은 기간·필터에서 그 대상의 금액·횟수·평균을 계산한다. 지원하지 않는 대상에는 무관한 조회로 대체하지 않는다.
일반 설명은 candidates 안에서 현재 근거로 부족한 비교만 최대 max_queries개 고른다. 충분하면 0개. 질문에 없는 배달 등 특정 대상을 추측하지 않는다.
queries 원소는 {by: category|merchant|time_slot|weekday, focus: null 또는 사용자가 말한 온라인 묶음/브랜드}.
reason은 선택 이유 한 문장(금액·날짜·거래 가게 이름 제외).
JSON: {queries, reason, hypothesis}."""
ANSWER_PROMPT = """서버 문장 ID를 선택해 JSON으로 반환한다. sentences에는 자연어 답변을 작성하지 않는다.
각 sentences 원소의 text에는 선택한 ID의 자리표시만 그대로 쓴다. 예를 들어 C7을 선택하면 text는 정확히 "{C7}"이고 claims는 ["C7"]이다.
text에 주어·숫자·증감 설명·마침표를 작성하지 않는다. 서버가 자리표시 전체를 완성 문장으로 치환한다.
선택한 ID 하나당 sentences 원소 하나를 만들며, 전체 1~4개다. 동일 ID는 한 번만 선택한다.
must의 각 묶음에서 적어도 하나를 선택한다. support.claims가 있으면 그중 하나를 첫 원소에, 없으면 scope 주장을 첫 원소에 둔다.
나머지는 질문에 필요한 근거만 선택한다. 같은 거래를 나눠 본 수치를 별개 원인처럼 합치지 않는다.
""" + supplement.PROMPT_RULES + """
support.limitation이 있으면 서버가 답 아래에 그 문장을 붙인다. 같은 내용을 extra에 다시 쓰지 않는다.
출력 형식: {"sentences":[{"text":"{C7}","claims":["C7"]}],"extra":null,"extra_reason":null}"""
clock = time.monotonic  # 테스트가 바꿔 끼운다


def run(ev, item, question, complete, hypothesis=None):
    """B 한 번 → (문장 목록 | None, trace). None = 템플릿으로 (trace["fallback"] 에 이유). LLM 호출 오류는 재시도 없이 실패로.
    complete(system, user, timeout=초) — 호출마다 12초 안의 남은 시간을 대기 한도로 넘긴다"""
    started, claims = clock(), first_claims(ev, item)
    trace = {"stop": None, "plan": None, "hypothesis": None, "queries": [], "dropped": [], "used": [], "check": [], "fallback": None}
    trace["stop"] = stop(ev, item, claims)
    added = {}
    if trace["stop"] is None:
        allowed = candidates(ev, item, claims)
        resolved = I.resolve(I.internal(hypothesis) if hypothesis is not None else None, item)
        # 범위 전체의 금액 반복은 구체적인 가설이 아니다. 계획 단계가 원문에서
        # 가격/빈도 등의 후보를 다시 확인할 수 있게 하되 확정된 대상·지표는 보존한다.
        question_hypothesis = {k: resolved[k] for k in ("metric", "target", "unavailable")} if resolved else None
        try:
            raw = complete(PLAN_PROMPT, json.dumps({"question": question, "claims": for_llm(claims), "candidates": allowed,
                                                    "focus_groups": list(ONLINE), "max_queries": MAX_QUERIES,
                                                    "targets": I.catalog(), "question_hypothesis": question_hypothesis}, ensure_ascii=False),
                           timeout=_left(started) - ANSWER_MIN)
        except Exception:  # 해석하지 못한 가설을 '가설 없음'으로 처리하지 않는다
            return None, {**trace, "fallback": "plan_failed"}
        if clock() - started > TIMEOUT:
            return None, {**trace, "fallback": "timeout"}
        plan = _plan(raw)
        if plan is None:
            return None, {**trace, "fallback": "invalid_plan"}
        trace["plan"] = plan
        trace["hypothesis"] = resolved if resolved is not None else I.resolve(plan["hypothesis"], item)
        hyp = trace["hypothesis"]
        trace["query_decisions"] = []
        requested = plan["queries"]
        if hyp and "target" in hyp:
            # 질문한 대상·지표의 부족만 채운다. 불필요한 전체 목록 조회는 실행하지 않는다.
            present = support(claims, hyp)["status"] == "observation"
            more, reason = ([], "already_supported") if present else target_claims(ev, item, hyp, len(claims) + 1)
            trace["query_decisions"] = [{"query": q, "action": "skipped", "reason": reason} for q in requested]
            if more:
                spec = I.TARGETS[hyp["target"]]
                q = {"by": spec["by"], "focus": spec["label"], "metric": hyp["metric"], "target": hyp["target"]}
                trace["queries"].append(q)
                trace["query_decisions"].append({"query": q, "action": "executed", "reason": "missing_target_metric"})
                claims += more
                added[spec["by"]] = {c["id"] for c in more}
            trace["target_resolution"] = reason
            requested = []
        for q in requested[:MAX_QUERIES]:
            if q["by"] in [d["by"] for d in trace["queries"]] or not (q["by"] in allowed or (q["by"] == "merchant" and q["focus"])):
                trace["dropped"].append(q)
                continue
            if hyp is None and q["by"] == "weekday":
                # 일반 증가액 설명에는 하루 평균을 기여 금액으로 섞지 않는다. 요일 가설은 위의 대상 조회로 처리한다.
                trace["query_decisions"].append({"query": q, "action": "skipped", "reason": "different_metric"})
                continue
            if hyp is None and q["focus"]:
                trace["dropped"].append(q)
                trace["query_decisions"].append({"query": q, "action": "skipped", "reason": "unrequested_target"})
                continue
            try:
                more = compare_group(ev, item, q["by"], q["focus"], start=len(claims) + 1)
            except ValueError:  # 서버 인자 검사 탈락 — 그 조회만 버린다
                trace["dropped"].append(q)
                continue
            claims += more
            added[q["by"]] = {c["id"] for c in more}
            trace["queries"].append(q)
    if _left(started) < ANSWER_MIN:  # 답을 기다릴 시간이 모자라면 부르지 않고 템플릿 (사용자 대기가 12초를 넘지 않게)
        return None, {**trace, "fallback": "no_time"}
    contract = support(claims, trace["hypothesis"])
    if contract["status"] == "insufficient":
        limits = {
            "outside_scope": "질문한 대상은 앞서 조회한 범위 밖이에요. 해당 대상의 소비를 먼저 조회한 뒤 비교해 주세요.",
            "empty_average": "비교 기간 중 한쪽에 해당 대상의 결제가 없어 건당 평균을 비교할 수 없어요. 결제 없음은 평균 0원과 달라요.",
            "missing_time_or_merchant": "시간이나 가게 이름이 없는 내역이 있어 질문한 대상의 비교를 확정할 수 없어요.",
            "no_matching_days": "비교 기간에 해당 요일이 없어 하루 평균을 비교할 수 없어요.",
        }
        if trace.get("target_resolution") in limits:
            contract = {**contract, "limitation": limits[trace["target_resolution"]]}
    trace["support"] = contract
    if contract["status"] == "insufficient":
        # 답 LLM을 불러 근거 없는 결론을 만들게 하지 않는다. 확인 불가도 정상적인 분석 결과다.
        trace.update(resolution="insufficient", cited=[], unused_queries=list(added))
        return [contract["limitation"]], trace
    try:
        hyp, key = trace.get("hypothesis"), key_claims(claims)
        trace["key"] = key if hyp is None else []
        # 실행한 추가 조회마다 그 조회의 1위(동률 포함) · 범위 안에서 없음 주장 — 하나는 답에 써야 한다
        # 단, 변화가 범위 전체 증감의 20% 미만인 1위(작은 요인)는 필수가 아니다 — 안 쓰면 분석 과정의 '답에 안 쓴 조회'로만 (26-19)
        floor = claims[0].get("size", 0) * QUERY_TOP_SHARE
        tops = [[c["id"] for c in claims if c["id"] in ids and (c["kind"] == "absent" or
                 (c["metric"] == "amount" and c["rank"] == 1 and c.get("size", 0) >= floor))]
                for ids in added.values()]
        scope_claim = [claims[0]["id"]] if hyp is None else []
        trace["must"] = [g for g in [scope_claim, contract["claims"], trace["key"], *tops] if g]
        raw = complete(ANSWER_PROMPT, json.dumps({"question": question, "hypothesis": hyp, "must": trace["must"],
                                                  "support": contract,
                                                  "claims": for_llm(claims)}, ensure_ascii=False), timeout=_left(started))
    except Exception:
        return None, {**trace, "fallback": "answer_failed"}
    if clock() - started > TIMEOUT:
        return None, {**trace, "fallback": "timeout"}
    lines, why = check(raw, claims, trace.get("hypothesis"), trace.get("must", []), offsets(ev))
    trace["check"] = why
    if lines is None:
        return None, {**trace, "fallback": "rejected"}
    data = json.loads(raw)
    trace["used"] = [i for s in data["sentences"] for i in s["claims"]]
    # 고른 추가 조회를 답에 하나도 쓰지 않았으면 표시만 (탈락 아님 — 호출만 늘었다)
    trace["unused_queries"] = [by for by, ids in (added if trace["stop"] is None else {}).items() if not ids & set(trace["used"])]
    public = {c["id"]: c for c in for_llm(claims)}
    trace["cited"] = [{k: public[i][k] for k in ("axis", "kind", "subject", "metric")} for i in dict.fromkeys(trace["used"])]
    trace["next_check"] = next_check(claims, trace["hypothesis"], contract, trace["used"])
    # 보충(11/10): 서버 문장으로 다 답하지 못할 때 LLM 이 쓴 숫자 없는 문장. 탈락하면 보충만 버린다.
    # 서버가 다음 확인(next_check)을 붙이는 답에는 LLM 의 다음 질문 안내(next_step)를 겹쳐 붙이지 않는다
    # 서버 한계 문장(limitation)이 붙는 답에는 '서버 문장이 다루지 못함(uncovered)' 보충을 붙이지 않는다 — 같은 말 반복 (11/10 실제 평가 B2)
    extra, trace["extra_dropped"] = supplement.check(data.get("extra"), data.get("extra_reason"))
    if extra and data.get("extra_reason") == "next_step" and trace["next_check"]:
        extra, trace["extra_dropped"] = None, ["server_next_check"]
    if extra and data.get("extra_reason") == "uncovered" and contract.get("limitation"):
        extra, trace["extra_dropped"] = None, ["server_limitation"]
    trace["extra_reason"] = data.get("extra_reason") if extra else None
    return lines + ([extra] if extra else []), trace


def _left(started):
    return TIMEOUT - (clock() - started)


def _plan(raw):
    """계획 LLM 응답 → {queries, reason, hypothesis} 또는 None(형식 틀림 → 서버 설명으로 대체)"""
    try:
        data = json.loads(raw)
        queries = [{"by": q["by"], "focus": q.get("focus") or None} for q in data["queries"]]
        reason, hyp = data["reason"], data.get("hypothesis")
    except Exception:
        return None
    if not isinstance(reason, str) or re.search(r"[0-9]", reason) or not all(isinstance(q["by"], str) and
            (q["focus"] is None or isinstance(q["focus"], str)) for q in queries):
        return None
    if isinstance(hyp, dict) and "target" in hyp:
        try:
            hyp = I.internal(hyp)
        except (ValueError, TypeError):
            return None
        return {"queries": queries, "reason": reason, "hypothesis": hyp}
    # 과거 저장 응답 재생·기존 대역용 읽기 호환. 실제 LLM 스키마는 코드 기반 계약만 출력한다.
    if hyp is not None and not (isinstance(hyp, dict) and hyp.get("axis") in HYPOTHESIS_AXES
                                and (hyp.get("subject") is None or isinstance(hyp.get("subject"), str))):
        return None  # 잘못된 가설을 '가설 없음'으로 바꿔 성공 처리하지 않는다
    return {"queries": queries, "reason": reason, "hypothesis": hyp and {"axis": hyp["axis"], "subject": hyp.get("subject")}}

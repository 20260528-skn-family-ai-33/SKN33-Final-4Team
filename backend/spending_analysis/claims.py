"""B 1차 서버 부분 (MEMO 26절) — 증가 이유 · 가설 확인 질문의 주장(claim) · 비교하며 묶기 · 멈춤 판정 · 답 검사. LLM 호출 없음.

- 주장은 서버만 만든다: 지금 "왜 늘었어?" 근거(explain.evidence)를 그대로 옮긴 것이라 숫자가 템플릿 설명과 같다.
  {"id", "kind", "axis", "subject", "metric", "direction", "rank", "share", "text", "conditions"}
  kind = change(증감) · absent(확인 범위 안에서 없음) · unknown(확인 못 함), axis = scope · count · average · time_slot · weekday ·
  visit · merchant · category, share = 전체 증감 대비 몫 구간(small · part · most · all), text = 자리표시({C3})에 끼울 서버 값
- LLM 에 보내는 것은 for_llm(): id · 종류 · 묶음 이름(시간대 · 요일 · 카테고리 · 가게 순번) · 방향 · 순위 · 몫 · 조건 플래그. 금액 · 가게 이름 · 날짜 없음
- 비교하며 묶기(compare_group)는 첫 조회가 읽은 두 기간 집계(ev["sides"])를 다시 묶는다 → 기간 목록 · 필터가 원래 조회와 같다(DB 다시 안 읽음)
- 답 검사(check): 문장마다 쓴 주장 id · 증감 · 순위 · 몫 표현 · 범위 밖 부정 · 새 원인 · 평가 · 조건 꼬리. 하나라도 틀리면 답 전체를 버린다(None)
- 흐름(run): 첫 주장 → 서버 멈춤(데이터 이유만) → 계획 LLM(추가 조회 0~2개 + 이유) → 서버 인자 검사 · 묶기 → 답 LLM → 검사.
  실패 · 시간 초과면 None → 부르는 쪽(explain.answer)이 지금 템플릿 설명으로 답한다. trace = 개발용 분석 과정(금액 · 가게 이름 없음)
"""
import json
import re
import time

from . import result as R
from .answer import NAMES, josa, won
from .explain import BANNED, _changed, _per_day, _visits_line
from .scope import DAY_NAMES, ONLINE, SLOTS, name as scope_name, online, plain

MAX_QUERIES = 2  # 질문 하나의 추가 조회 상한 (26-3)
TIMEOUT = 12  # 초 — B 한 번(계획 + 답)이 이보다 오래 걸리면 그 요청만 템플릿 (26-12 ④, 목표 중간값 6초)
ANSWER_MIN = 3  # 초 — 답 호출에 남겨 둘 최소 시간. 계획 호출의 대기 한도 = 남은 시간 − 이것, 답 차례에 이보다 적게 남으면 답 호출 없이 템플릿
AXES = ("category", "merchant", "time_slot", "weekday")  # 비교하며 묶기 기준 — 서버가 받는 by 값은 이것뿐
VISITS = (("new", "새로 간 곳"), ("regular", "자주 가던 곳"), ("gone", "이번엔 안 간 곳"))
AVERAGE_WORDS = ("건당", "한 번에", "단가", "비싸", "비싼", "가격")  # 가설 축 말 (26-1 ②) — 기존 묶음 이름 표 + 지표 이름만, 질문별 규칙 없음
COUNT_WORDS = ("자주", "횟수", "건수", "여러 번", "많이 가", "많이 써서", "많이 시켜")
VISIT_WORDS = ("새로 간", "처음 간", "새 가게", "새로운 가게")


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
            "fixed_excluded": fixed_excluded}


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
    if by == "weekday":  # 주말 · 평일 묶음 하루 평균 (묶음 합계 ÷ 그 기간의 그 요일 수) — 순위 없음
        for name, days in DAY_GROUPS:
            per = [_group_day(side, days) for side in (before, now)]
            row = {"key": name, "name": name, "before": per[0], "after": per[1], "delta": per[1] - per[0]}
            out.append({**_group_claim(nid(), by, row, None, 0, base, metric), "group": True})
    return out


def _group_day(side, days):
    count = sum(side["weekday_days"].get(d, 0) for d in days)
    return R.average(sum(side["weekdays"].get(d, 0) for d in days), count)["value"] if count else 0


def hypothesis(text):
    """질문에 든 가설의 축 → {"axis", "name"} 또는 None. 기존 묶음 이름 표(시간대 · 요일 · 카테고리 · 온라인 별칭)와 지표 이름만 본다.
    판단에는 쓰지 않는다(26-12 ② — 첫 결과로 답할 수 있는지는 계획 LLM 이 정한다). 평가에서 계획 LLM 판단과 견줄 참고값"""
    said = text.replace(" ", "")
    for code, (name, _, _) in SLOTS.items():
        if name in said:
            return {"axis": "time_slot", "name": name}
    for i, d in enumerate(DAY_NAMES):
        if f"{d}요일" in said:
            return {"axis": "weekday", "name": f"{d}요일"}
    for group, spec in ONLINE.items():
        if any(plain(w) in plain(text) for w in (group, *spec["words"], *spec["brands"])):
            return {"axis": "merchant", "name": group, "focus": group}
    for code, name in NAMES.items():
        if name.replace("·", "") in said.replace("·", "") and code != "UNCLASSIFIED":
            return {"axis": "category", "name": name}
    for axis, words in (("average", AVERAGE_WORDS), ("count", COUNT_WORDS), ("visit", VISIT_WORDS)):
        if any(w.replace(" ", "") in said for w in words):
            return {"axis": axis, "name": None}
    return None


def stop(ev, item, text, claims, used=0):
    """추가 조회 전 멈춤 판정(데이터 이유만) → (이유 | None, 가설 참고값). 이유: insufficient(근거 부족 · 늘지 않음) · nothing_more(더 볼 묶음 없음 ·
    상한). None 이면 계획 LLM 이 추가 조회 0~2개를 고른다 — 첫 결과로 답할 수 있으면 0개 + 이유 (26-12 ②)"""
    guess = hypothesis(text)
    if ev.get("state") != "ok" or ev.get("kind") == "mix" or ev["delta"] <= 0:
        return "insufficient", guess  # 수집 미완 · 비교 기준 없음 · 바뀐 내역 · 늘지 않음 → 지금 거절 · 안내 문구
    if used >= MAX_QUERIES:
        return "nothing_more", guess
    now, before = ev["sides"]
    if now["count"] + before["count"] <= 1:
        return "nothing_more", guess  # 결제가 한 건뿐이면 묶어 볼 것이 없다
    if not candidates(ev, item, claims):
        return "nothing_more", guess
    return None, guess


def candidates(ev, item, claims):
    """추가 조회로 새로 볼 수 있는 묶음 기준 — 첫 주장에 순위 전체가 없고(가장 큰 것 하나뿐) 범위와 같은 축이 아닌 것"""
    listed = {c["axis"] for c in claims if c["rank"] and c["rank"] > 1}
    out = [a for a in AXES if a not in listed]
    if item.get("category"):
        out.remove("category")
    if item.get("merchant"):
        out.remove("merchant")
    return out


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
                    "flags": [k for k in ("unclassified", "fixed_excluded") if cond[k]] + (["search_scope"] if cond["search_scope"] else [])
                    + ([] if cond["period_match"] else ["period_mismatch"])})
    return out


# ---------- 답 검사 (26-4 · 26-16) ----------
# 답 LLM 이 직접 쓰는 말은 연결 말과 완곡한 가설 부정뿐 — 증감 · 순위 · 몫 · 금액은 서버 절(자리표시) 안에만 있다
CONNECTIVES = ("그다음으로", "그리고", "그런데", "아니요", "이어서", "다만", "반면", "특히", "한편", "먼저", "또한", "또", "네")
# 가설 부정은 '…라고 보기는 어려워요'만 — 같은 문장에 반대 방향(줄었어요 · 같아요) 또는 확인 범위 안에서 없음 주장을 인용했을 때 (26-12 ②)
SOFT_NEGATION = re.compile(r"[^.?!{}]*(?:라고|다고)\s*보기(?:는|엔|에는)?\s*어려워요[.!]?")
NEGATE = ("때문은 아니", "때문이 아니", "원인이 아니", "상관없", "관계없", "영향이 없", "영향은 없")
CAUSE = ("때문", "덕분", "원인", "영향", "탓")
EVALUATE = ("잘하", "잘 하", "아쉽", "걱정", "문제", "조심", "줄여야", "아껴")
REASONED = re.compile(r"((?<!에)서|니까|라서|므로|느라)\s*\S*\s*(늘|줄|증가|감소|올랐|많아)")


def _opposite(claim):
    return claim["kind"] == "absent" or claim["direction"] in ("down", "same")


def _groups():
    """새 원인 검사용 묶음 이름 — 시간대 · 요일 · 주말/평일 · 카테고리 · 온라인 묶음"""
    return ([n for n, _, _ in SLOTS.values()] + [f"{d}요일" for d in DAY_NAMES] + ["주말", "평일"]
            + [n for c, n in NAMES.items() if c != "UNCLASSIFIED"] + list(ONLINE))


HYPOTHESIS_AXES = ("count", "average", "time_slot", "weekday", "visit", "merchant", "category")
MAX_CLAUSES = 2  # 한 문장에 서버 절 2개까지


def key_claims(claims):
    """가설 없는 '왜 늘었어?'에 꼭 인용할 핵심 요인 — 전체와 같은 방향으로 가장 크게 바뀐 묶음 주장(시간대 · 새로 간 곳/자주 가던 곳 · 가게 · 카테고리,
    금액 기준). 동률이면 모두 후보(그중 하나만 인용하면 된다). 요일(하루 평균)은 단위가 달라 뺀다"""
    total = claims[0]["direction"]
    pool = [c for c in claims if c["kind"] == "change" and c["axis"] in ("time_slot", "visit", "merchant", "category")
            and c["direction"] == total and not c.get("group") and c.get("size")]
    top = max((c["size"] for c in pool), default=None)
    return [c["id"] for c in pool if c["size"] == top]


def offsets(ev):
    """일부가 전체보다 클 때 붙일 맞물림 문장 (축마다 하나, 서버 값 · 템플릿 표현). 인용한 묶음 주장의 금액이 범위 전체 증감보다 크면
    반대로 움직인 쪽을 이어 전체와 맞춘다 — 새로 간 곳/자주 가던 곳은 템플릿의 잇는 문장 그대로(세 합 = 전체 증감).
    가게 · 카테고리는 전체 결제 기준이라 '전체로는'까지, 시간대는 고정지출을 뺀 기준이라 반대쪽 합계만"""
    now, before = ev["sides"]
    up = ev["delta"] > 0
    out = {"visit": _visits_line(ev)}
    for axis, field, word in (("merchant", "merchants", "곳"), ("category", "categories", "카테고리"), ("time_slot", "slots", "시간대")):
        deltas = [now[field].get(k, 0) - before[field].get(k, 0) for k in set(now[field]) | set(before[field])]
        opposite = -sum(d for d in deltas if (d < 0 if up else d > 0))
        if not opposite:
            continue
        head = f"반대로 {'줄어든' if up else '늘어난'} {word}에서 {won(abs(opposite))}"
        out[axis] = f"{head} {'줄어' if up else '늘어'}, 전체로는 {_changed(ev['delta'])}." if axis != "time_slot" else \
            f"{head} {'줄었어요' if up else '늘었어요'}."
    return {k: v for k, v in out.items() if v}


def _matches(claim, hyp):
    """주장이 가설과 같은 축 · 대상인지 (대상이 없으면 축만). 대상은 서로 포함이면 같다고 본다 ('배민' ↔ '배민', '배달' ↔ '배달앱')"""
    if claim["axis"] != hyp["axis"] or claim["kind"] not in ("change", "absent"):
        return False
    return not hyp.get("subject") or hyp["subject"] in claim["subject"] or claim["subject"] in hyp["subject"]


def check(raw, claims, hypothesis=None, must=(), joins=None):
    """답 LLM 응답 {"sentences": [{"text", "claims"}]} → (서버 절을 끼운 문장 목록 | None, 탈락 이유 목록)
    hypothesis = 계획 LLM 이 읽은 가설 {axis, subject} | None — '네 · 아니요' · 가설 부정은 이것과 같은 축 · 대상 주장으로만 (26-17).
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
    for i, s in enumerate(sentences, 1):
        if not isinstance(s, dict) or not isinstance(s.get("text"), str) or not isinstance(s.get("claims"), list):
            return None, ["format"]
        text, ids = s["text"], s["claims"]
        why = _sentence(text, ids, known, hypothesis)
        if why:
            reasons += [f"{i}:{w}" for w in why]
            continue
        filled = re.sub(r"\{(C\d+)\}", lambda m: known[m.group(1)]["text"], text).strip()
        out.append(re.sub(r"\.\s*\.", ".", re.sub(r"\s+", " ", filled)))
    cited = {i for s in sentences for i in s["claims"]}
    for n, group in enumerate(must, 1):
        if group and not cited & set(group):
            reasons.append(f"0:missing_must{n}")  # 핵심 요인 · 고른 추가 조회의 1위를 빼지 않는다
    if reasons:
        return None, reasons
    whole, joined, extended = claims[0].get("size", 0), set(), []
    for line, s in zip(out, sentences):  # 일부 > 전체 → 그 문장 뒤에 맞물림 문장 (축마다 한 번)
        extended.append(line)
        for c in (known[i] for i in s["claims"]):
            if (joins or {}).get(c["axis"]) and c["axis"] not in joined and c["direction"] == claims[0]["direction"] \
                    and c.get("size", 0) > whole:
                extended.append(joins[c["axis"]])
                joined.add(c["axis"])
    out = extended
    seen = False  # '고정지출을 빼고 보면'은 답 전체에서 처음 한 번만
    for n, line in enumerate(out):
        while FIXED in line and seen:
            line = line.replace(FIXED, "", 1)
        if FIXED in line:
            head, _, tail = line.partition(FIXED)
            line, seen = head + FIXED + tail.replace(FIXED, ""), True
        out[n] = line
    return out, []


def _sentence(text, ids, known, hypothesis=None):
    if not ids or any(i not in known for i in ids):
        return ["claim_id"]
    why, used = [], [known[i] for i in ids]
    marks = re.findall(r"\{(C\d+)\}", text)
    if not marks:
        why.append("no_clause")  # 문장마다 서버 절 하나 이상
    if len(marks) > MAX_CLAUSES:
        why.append("too_many_clauses")
    if any(m not in ids for m in marks) or re.search(r"\{(?!C\d+\})", text):
        why.append("placeholder")
    bare = re.sub(r"\{C\d+\}", " ", text)
    soft = SOFT_NEGATION.findall(bare)
    rest = SOFT_NEGATION.sub(" ", bare)
    if re.search(r"[0-9%]", bare):
        why.append("number")
    if any(w in bare for w in NEGATE):
        why.append("negation")  # 단정한 부정 ('배달 때문은 아니에요')
    elif soft and not any(_opposite(c) and (hypothesis is None or _matches(c, hypothesis)) for c in used):
        why.append("negation")  # 근거(가설과 같은 축 · 대상의 반대 방향 · 범위 안에서 없음) 없는 완곡한 부정 — 주말이면 주말 묶음 주장
    elif any(w in rest for w in CAUSE) or REASONED.search(rest):
        why.append("cause")
    if any(w in bare for w in (*BANNED, *EVALUATE)):
        why.append("evaluation")
    yes, no = (re.search(r"(?<![가-힣])" + w + r"(?![가-힣])", rest) for w in ("네", "아니요"))
    if (yes or no) and hypothesis is None:
        why.append("yes_no")  # 가설이 없는 질문에 네 · 아니요
    elif yes and not any(_matches(c, hypothesis) and c["direction"] == "up" and c["rank"] in (1, None) for c in used):
        why.append("yes_no")  # '네' = 같은 축 · 대상이 늘었고 그 축 1위(동률 포함)일 때만
    elif no and not any(_matches(c, hypothesis) and _opposite(c) for c in used):
        why.append("yes_no")  # '아니요' = 같은 축 · 대상이 반대 방향 · 범위 안에서 없음일 때만
    left = re.sub(r"[\s,.!?~·…()]", "", rest)
    for word in CONNECTIVES:
        left = left.replace(word, "")
    if left:
        why.append("free_text")  # 연결 말 밖의 말 (증감 동사 · 순위 · 새 원인을 직접 쓰지 않는다)
    subjects = {c["subject"] for c in used} | {c["subject"] for c in known.values() if c["axis"] == "scope"}  # 물은 범위 이름은 늘 쓸 수 있다
    # 주말 · 평일은 그 안의 요일 주장을 인용했으면 쓸 수 있다
    subjects |= {name for name, days in (("주말", "토일"), ("평일", "월화수목금")) if any(s[:1] in days and s.endswith("요일") for s in subjects)}
    if any(g in bare and not any(g in s for s in subjects) for g in _groups()):
        why.append("new_group")  # 인용하지 않은 묶음 이름
    return list(dict.fromkeys(why))


# ---------- 흐름 (26-1) ----------
PLAN_PROMPT = """너는 가계부 앱에서 소비가 늘어난 이유 질문에 답하기 전에, 더 볼 묶음을 고른다.
입력: question(사용자 질문), claims(서버가 이미 확인한 주장 [{id, kind, axis, subject, direction, rank, share, flags}]),
candidates(더 볼 수 있는 묶음 기준), max_queries.
규칙: 질문에 답하는 데 claims 만으로 충분하면 queries 를 비운다. 부족하면 candidates 안에서 0~max_queries 개를 고른다.
by 는 category · merchant · time_slot · weekday 중 하나. 질문이 focus_groups 의 온라인 묶음(앱 · 브랜드 이름 포함)을 물으면 candidates 와 상관없이
by=merchant, focus 에 사용자가 쓴 말을 넣는다. 그 밖엔 focus=null.
같은 by 를 두 번 고르지 않는다. 금액 · 날짜 · 가게 이름을 쓰지 않는다. reason 은 고른 이유 한 문장(숫자 없이).
hypothesis: 질문이 특정 요인을 짐작하면(○○ 때문이야? · ○○해서 그래?) 그 요인의 축과 대상, 아니면(왜 늘었어? · 왜 이렇게 많아?) null.
axis 는 count(자주 · 횟수) · average(값 · 가격 · 단가) · time_slot · weekday(요일 · 주말 · 평일) · visit(새로 간 곳) · merchant(앱 · 브랜드 · 가게) · category 중 하나,
subject 는 시간대 · 요일 · 주말 · 평일 · 카테고리 이름 · 사용자가 쓴 앱 · 브랜드 이름, 정할 대상이 없으면 null.
JSON 하나로만 답한다: {"queries": [{"by": "...", "focus": null}], "reason": "...", "hypothesis": {"axis": "...", "subject": null} 또는 null}"""
ANSWER_PROMPT = """너는 가계부 앱에서 소비가 늘어난 이유 질문에 2~4문장으로 답한다. 입력 claims 는 서버가 확인한 주장이다.
자리표시 {C3} 하나는 서버가 만든 완성 문장(주어 · 금액 · 증감 · 마침표까지)으로 바뀐다. 너는 문장을 고르고 순서를 정해, 앞에 연결 말
(네 · 아니요 · 다만 · 반면 · 특히 · 그리고 · 또 · 또한 · 한편 · 먼저 · 그다음으로 · 이어서)만 붙인다. 그 밖의 말 · 숫자 · 증감 동사 · 가게 이름은 쓰지 않는다.
예: "네, {C3}" · "다만 {C2}" · "{C1} 특히 {C4}"
규칙: hypothesis(서버가 받은 가설 {axis, subject})가 있으면 첫 문장에서 그 축 · 대상의 주장으로 먼저 답한다. 주말 · 평일 가설은 subject 가 같은 묶음 주장을 쓴다.
'네'는 그 주장이 늘었고(direction=up) 그 축에서 rank=1(또는 rank 없음)일 때만, '아니요'는 반대 방향(down · same)이거나 kind=absent 일 때만 쓴다.
반대 방향 · absent 이면 '아니요' 대신 '○○라고 보기는 어려워요.'(○○ = 질문의 가설을 짧게)를 그 자리표시 앞에 쓸 수 있다. '때문은 아니에요' · '원인은' 같은 단정은 쓰지 않는다.
hypothesis 가 null 이면 '네 · 아니요'를 쓰지 않고 첫 문장은 axis=scope 주장(전체 증감)으로 시작한다.
must 는 꼭 쓸 주장 묶음 목록이다 — 묶음마다 id 하나 이상을 반드시 쓴다(가장 크게 바뀐 요인 · 더 조회한 묶음의 1위). 2~4문장 안에 모두 넣는다.
한 문장에 자리표시는 2개까지. 그다음 질문에 맞는 주장만 골라 잇는다. 문장마다 쓴 주장 id 를 claims 칸에 적는다(자리표시로 쓴 것 모두). 평가 · 조언하지 않는다.
JSON 하나로만 답한다: {"sentences": [{"text": "...", "claims": ["C1"]}]}"""
clock = time.monotonic  # 테스트가 바꿔 끼운다


def run(ev, item, question, complete):
    """B 한 번 → (문장 목록 | None, trace). None = 템플릿으로 (trace["fallback"] 에 이유). LLM 호출 오류는 재시도 없이 실패로.
    complete(system, user, timeout=초) — 호출마다 12초 안의 남은 시간을 대기 한도로 넘긴다"""
    started, claims = clock(), first_claims(ev, item)
    trace = {"stop": None, "plan": None, "hypothesis": None, "queries": [], "dropped": [], "used": [], "check": [], "fallback": None}
    trace["stop"], _ = stop(ev, item, question, claims)
    added = {}
    if trace["stop"] is None:
        allowed = candidates(ev, item, claims)
        try:
            raw = complete(PLAN_PROMPT, json.dumps({"question": question, "claims": for_llm(claims), "candidates": allowed,
                                                    "focus_groups": list(ONLINE), "max_queries": MAX_QUERIES}, ensure_ascii=False),
                           timeout=_left(started) - ANSWER_MIN)
        except Exception:  # 계획 실패 = 추가 조회 0개로 답 (26-5)
            raw = None
        if clock() - started > TIMEOUT:
            return None, {**trace, "fallback": "timeout"}
        plan = _plan(raw)
        trace["plan"] = plan
        trace["hypothesis"] = (plan or {}).get("hypothesis")
        for q in (plan or {}).get("queries", [])[:MAX_QUERIES]:
            if q["by"] in [d["by"] for d in trace["queries"]] or not (q["by"] in allowed or (q["by"] == "merchant" and q["focus"])):
                trace["dropped"].append(q)
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
    try:
        hyp, key = trace.get("hypothesis"), key_claims(claims)
        trace["key"] = key if hyp is None else []
        # 실행한 추가 조회마다 그 조회의 1위(동률 포함) · 범위 안에서 없음 주장 — 하나는 답에 써야 한다
        tops = [[c["id"] for c in claims if c["id"] in ids and (c["rank"] == 1 or c["kind"] == "absent")] for ids in added.values()]
        trace["must"] = [g for g in [trace["key"], *tops] if g]
        raw = complete(ANSWER_PROMPT, json.dumps({"question": question, "hypothesis": hyp, "must": trace["must"],
                                                  "claims": for_llm(claims)}, ensure_ascii=False), timeout=_left(started))
    except Exception:
        return None, {**trace, "fallback": "answer_failed"}
    if clock() - started > TIMEOUT:
        return None, {**trace, "fallback": "timeout"}
    lines, why = check(raw, claims, trace.get("hypothesis"), trace.get("must", []), offsets(ev))
    trace["check"] = why
    if lines is None:
        return None, {**trace, "fallback": "rejected"}
    trace["used"] = [i for s in json.loads(raw)["sentences"] for i in s["claims"]]
    # 고른 추가 조회를 답에 하나도 쓰지 않았으면 표시만 (탈락 아님 — 호출만 늘었다)
    trace["unused_queries"] = [by for by, ids in (added if trace["stop"] is None else {}).items() if not ids & set(trace["used"])]
    by_id = {c["id"]: c for c in claims}
    trace["cited"] = [{"axis": by_id[i]["axis"], "kind": by_id[i]["kind"]} for i in dict.fromkeys(trace["used"])]  # 평가용 (금액 · 이름 없음)
    return lines, trace


def _left(started):
    return TIMEOUT - (clock() - started)


def _plan(raw):
    """계획 LLM 응답 → {"queries": [{by, focus}], "reason"} 또는 None(형식 틀림 → 추가 조회 0개)"""
    try:
        data = json.loads(raw)
        queries = [{"by": q["by"], "focus": q.get("focus") or None} for q in data["queries"]]
        reason, hyp = data["reason"], data.get("hypothesis")
    except Exception:
        return None
    if not isinstance(reason, str) or re.search(r"[0-9]", reason) or not all(isinstance(q["by"], str) for q in queries):
        return None
    if hyp is not None and not (isinstance(hyp, dict) and hyp.get("axis") in HYPOTHESIS_AXES
                                and (hyp.get("subject") is None or isinstance(hyp.get("subject"), str))):
        hyp = None  # 모르는 축 · 형식이면 가설 없음으로 (네 · 아니요를 못 쓴다)
    if hyp is not None and hyp["axis"] in ("count", "average", "visit"):
        hyp = {**hyp, "subject": None}  # 이름 붙은 대상이 없는 축 — LLM 이 붙인 대상('식비' 등)은 버린다 (e08)
    return {"queries": queries, "reason": reason, "hypothesis": hyp and {"axis": hyp["axis"], "subject": hyp.get("subject")}}

"""이번 달 내역만으로 할 수 있는 분석 (지난달 비교 · 과거 자료가 없어도 나온다).

reasoning.analyze 가 '지난달과 무엇이 달라졌나'를 본다면, 여기는 '지금 돈이 어떻게 나가고 있나'를 본다.
- 예산 속도: 예산 주기가 지난 비율보다 예산을 빨리 쓰는 카테고리 · 이미 넘은 카테고리 · 여유 있는 카테고리
- 큰 결제 비중: 몇 건의 큰 결제가 이번 달 소비에서 차지하는 몫
- 자주 하는 소비: 업종이 확인된 카페 · 배달 · 외식 결제가 며칠에 걸쳐 반복되는지
- 주말 · 평일: 지난 날짜 수로 나눈 하루 평균의 차이
- 주요 카테고리: 한 카테고리에 소비가 쏠린 정도

원칙 (요구사항 1 · 2 · 9 · 12)
- 발견이 있을 때만 결과를 만든다. '판단하기 어려워요' 같은 문장은 만들지 않는다 — 조건이 안 되면 그 분석을 빼고 끝
- 당연한 말(여러 날 나눠 썼다 · 결제가 반복된다)은 결론으로 쓰지 않는다
- 숫자는 근거로만, 결론은 해석으로. 금액은 모두 서버 계산 값
- 행동 제안은 확인된 소비 방식과 그 사람의 금액으로 계산되는 것만 (남은 하루 금액 · 줄인 횟수 × 평균 결제액)
- 가게 이름은 쓰지 않는다 (카테고리 · 업종 묶음 이름만)
"""
from datetime import timedelta

from . import lifestyle, scope, trend
from .answer import NAMES, won

PACE_GAP = 0.2        # 예산 사용 비율이 주기 경과 비율보다 이만큼 앞서면 '빠르다'
PACE_MIN_USED = 0.4   # 그리고 예산의 40% 이상을 썼을 때만 (초반 몇 건으로 과장하지 않게)
PACE_MIN_AHEAD = 20000
SLACK_GAP = 0.25      # 경과 비율보다 이만큼 덜 썼고
SLACK_MIN = 30000     # 남은 돈이 이만큼 이상이면 '여유 있는 카테고리'
LARGE_MIN = 50000
FREQUENT = {"cafe": 6, "delivery": 4, "dining": 8}   # 이번 달 결제일이 이만큼 이상이면 '자주 하는 소비'


def josa(word, pair):
    """받침에 맞는 조사. pair = '은는' · '이가' · '을를' · '과와' · '으로로'. 한글이 아니면 뒤쪽(받침 없음)"""
    tail = word.rstrip(" )")[-1:] if word.rstrip(" )") else ""
    if tail.isdigit() or tail == "%":  # 숫자 · % 는 읽는 소리로: 1·7·8 은 ㄹ 받침, 0·3·6 은 받침 있음, 퍼센트 · 2·4·5·9 는 없음
        final = 8 if tail in "178" else 1 if tail in "036" else 0
    else:
        last = next((ch for ch in reversed(word) if "가" <= ch <= "힣"), None)
        final = last and (ord(last) - 0xAC00) % 28
    if pair == "으로로":
        return word + ("으로" if final and final != 8 else "로")
    return word + (pair[0] if final else pair[1])


def short(amount):
    """화면에서 읽기 쉬운 금액. 1만 원 이상은 '12.3만 원', 그 아래는 '8,000원'"""
    amount = int(round(amount))
    if abs(amount) >= 10000:
        value = round(amount / 10000, 1)
        return f"{value:g}만 원"
    return won(amount)


def _paid(rows):
    return [r for r in rows if r["net_amount"] > 0 and r["funding"] != "fixed"]


def _finding(fid, kind, section, *, conclusion, evidence=(), impact=0, category=None, subject=None, actions=(), visual=None, **extra):
    out = dict(id=fid, kind=kind, section=section, category=category, subject=subject or (NAMES.get(category) if category else "분석 범위 전체"),
               impact=impact, comparable=False, conclusion=conclusion, evidence=list(evidence), outlook="", limits=[],
               actions=list(actions), visual=visual, snapshot=True)
    out.update(extra)
    return out


# ---------- 예산 속도 ----------

def budget_pace(budget, today, categories=None, rows=()):
    """budget = tools.budget_left(user, None, today, breakdown=True). 확정 잔액(confirmed)이 없으면 아무것도 만들지 않는다
    rows = 이번 달 변동 소비 (결제 건수로 '한 달에 한두 번 사는 것'을 속도 판단에서 빼려고)"""
    if not budget or not budget.get("confirmed") or not budget.get("categories"):
        return []
    cycle = budget["cycle_period"]
    total_days = (cycle.end_before - cycle.start).days
    elapsed_days = (today - cycle.start).days + 1
    days_left = max(budget.get("days_left", total_days - elapsed_days), 0)
    if total_days <= 0 or elapsed_days <= 0:
        return []
    elapsed = elapsed_days / total_days
    counts = {}
    for r in _paid(rows):
        if r["occurred_on"] >= cycle.start:
            counts[r["category"]] = counts.get(r["category"], 0) + 1
    rows = [r for r in budget["categories"] if r["allocated"] > 0 and (not categories or r["category"] in categories)]
    over, fast, slack = [], [], []
    for r in rows:
        used = r["spent"] / r["allocated"]
        if r["over"] > 0:
            over.append((r["over"], r, used))
        elif (elapsed >= 0.25 and counts.get(r["category"], 0) >= 3  # 주기 초반 · 한두 번 사는 항목은 속도로 보지 않는다
              and used - elapsed >= PACE_GAP and used >= PACE_MIN_USED and r["spent"] - r["allocated"] * elapsed >= PACE_MIN_AHEAD):
            fast.append((r["spent"] - r["allocated"] * elapsed, r, used))
        elif elapsed - used >= SLACK_GAP and r["remaining"] >= SLACK_MIN:
            slack.append((r["remaining"], r, used))
    visual = {"kind": "pace", "title": "예산 사용", "elapsed": round(elapsed * 100),
              "caption": f"예산 주기 {elapsed_days}일째 · {round(elapsed * 100)}% 지남",
              "rows": [{"code": r["category"], "name": NAMES[r["category"]], "spent": r["spent"], "allocated": r["allocated"],
                        "ratio": round(r["spent"] * 100 / r["allocated"]),
                        "state": "over" if r["over"] else "fast" if any(r is f[1] for f in fast) else "slack" if any(r is s[1] for s in slack) else "ok"}
                       for r in sorted(rows, key=lambda r: -r["spent"] / r["allocated"])]}
    out = []
    if over or fast:
        over.sort(key=lambda x: -x[0])
        fast.sort(key=lambda x: -x[0])
        lead = over[0] if over else fast[0]
        r, used = lead[1], lead[2]
        name = NAMES[r["category"]]
        if over:
            conclusion = f"{josa(name, '은는')} 주기의 {round(elapsed * 100)}%만 지났는데 예산을 이미 {short(r['over'])} 넘었어요."
        else:
            conclusion = f"{josa(name, '은는')} 주기의 {round(elapsed * 100)}%가 지났는데 예산의 {round(used * 100)}%를 썼어요."
        evidence = [f"{name} 예산 {won(r['allocated'])} 중 {won(r['spent'])}을 썼어요."
                    + (f" 남은 {days_left}일 동안 쓸 수 있는 돈은 {won(r['remaining'])}이에요." if not over and days_left else "")]
        others = [x for x in (*over, *fast) if x is not lead][:2]
        if others:
            evidence.append("예산보다 빨리 쓰는 항목: " + ", ".join(
                f"{NAMES[x[1]['category']]} {round(x[2] * 100)}%" for x in others) + f" (주기 {round(elapsed * 100)}% 지남)")
        if slack:
            slack.sort(key=lambda x: -x[0])
            evidence.append("반대로 " + ", ".join(f"{NAMES[x[1]['category']]}(남은 {short(x[1]['remaining'])})" for x in slack[:2])
                            + "에는 여유가 있어요.")
        actions = []
        per_day = r["remaining"] // days_left if days_left else 0
        if not over and per_day >= 1000 and counts.get(r["category"], 0) >= 5:  # 자주 쓰는 항목만 '하루 얼마' 제안
            actions.append(f"남은 {days_left}일 동안 {josa(name, '을를')} 하루 {short(per_day)} 안으로 쓰면 예산 안에서 끝나요.")
        if slack and (over or fast):
            actions.append(f"{name} 예산이 모자라면 여유 있는 {NAMES[slack[0][1]['category']]}에서 옮기는 안을 예산 조정에서 바로 계산해 볼 수 있어요.")
        impact = r["over"] if over else lead[0]
        out.append(_finding("PACE", "budget_fast", "예산 속도", category=r["category"], conclusion=conclusion,
                            evidence=evidence, impact=impact + 10 ** 9, actions=actions, visual=visual))
    elif budget["confirmed"] and not categories:
        total = budget["confirmed"]
        used = total["spent"] / total["allocated"] if total["allocated"] else None
        if used is not None:
            conclusion = (f"생활비는 예산 속도에 맞게 쓰고 있어요. 주기의 {round(elapsed * 100)}%가 지났고 예산의 {round(used * 100)}%를 썼어요."
                          if used <= elapsed + 0.1 else
                          f"생활비를 예산보다 조금 빠르게 쓰고 있어요. 주기의 {round(elapsed * 100)}%가 지났는데 예산의 {round(used * 100)}%를 썼어요.")
            evidence = [f"생활비 예산 {won(total['allocated'])} 중 {won(total['spent'])}을 썼어요."]
            if slack:
                slack.sort(key=lambda x: -x[0])
                evidence.append(", ".join(f"{NAMES[x[1]['category']]}(남은 {short(x[1]['remaining'])})" for x in slack[:2]) + "는 아직 여유가 커요.")
            out.append(_finding("PACE", "budget_ok", "예산 속도", conclusion=conclusion, evidence=evidence,
                                impact=0, visual=visual))
    return out


# ---------- 큰 결제 ----------

def large_share(rows, history=()):
    paid = _paid(rows)
    total = sum(r["net_amount"] for r in paid)
    if len(paid) < 5 or total <= 0:
        return []
    (large, _), _ = trend.split_large(paid, list(history))
    large = [r for r in large if r["net_amount"] >= LARGE_MIN] or [r for r in sorted(paid, key=lambda r: -r["net_amount"])[:2]
                                                                   if r["net_amount"] >= max(LARGE_MIN, total * 0.15)]
    if not large or len(large) > 3:
        return []
    amount = sum(r["net_amount"] for r in large)
    share = amount / total
    if share < 0.3:
        return []
    names = sorted({"분류 전 결제" if r["category"] == "UNCLASSIFIED" else NAMES[r["category"]] for r in large if r["category"] in NAMES})
    rest = [r for r in paid if r not in large]
    rest_days = len({r["occurred_on"] for r in rest}) or 1
    conclusion = (f"큰 결제 {len(large)}건({short(amount)})이 이번 달 소비의 {round(share * 100)}%예요. "
                  f"이 결제들이 총액을 크게 끌어올렸어요.")
    evidence = [f"{'·'.join(names)}{'예요' if names == ['분류 전 결제'] else '에서 나온 결제예요'}. 이 {len(large)}건을 빼면 나머지 소비는 {won(total - amount)}이에요."]
    if rest:
        evidence[0] += f" 돈을 쓴 날 기준 하루 {short((total - amount) / rest_days)} 꼴이에요."
    return [_finding("LARGE", "large_share", "큰 결제와 반복 소비", conclusion=conclusion, evidence=evidence,
                     impact=amount, large_amount=amount)]


# ---------- 자주 하는 소비 (업종이 확인된 것만) ----------

def frequent(rows, today, period_start, period_end_before):
    paid = [r for r in _paid(rows) if r.get("activity") in FREQUENT]
    out = []
    elapsed = (today - period_start).days + 1
    remaining_weeks = max((period_end_before - today - timedelta(days=1)).days // 7, 0)
    for kind, minimum in FREQUENT.items():
        typed = [r for r in paid if r["activity"] == kind]
        days = {r["occurred_on"] for r in typed}
        if len(days) < minimum:
            continue
        amount = sum(r["net_amount"] for r in typed)
        average = amount // len(typed)
        label = lifestyle.LABELS[kind]
        weekday_lunch = sum(1 for r in typed if r["occurred_on"].weekday() < 5 and scope.slot_of(r["occurred_time"]) in ("lunch", "afternoon"))
        when = "평일 점심 무렵 " if weekday_lunch * 10 >= len(typed) * 7 else ""
        conclusion = f"{when}{label} 결제가 {elapsed}일 중 {len(days)}일로 거의 습관처럼 이어지고 있어요." if len(days) * 2 >= elapsed else \
            f"{when}{label}에 {elapsed}일 동안 {len(days)}일, {short(amount)}을 썼어요."
        evidence = [f"한 번에 평균 {won(average)}, 이번 달 {label} 합계는 {won(amount)}이에요."]
        actions = []
        if remaining_weeks >= 1:
            saved = average * 2 * remaining_weeks
            actions.append(f"{label}를 주 2번만 줄이면 남은 {remaining_weeks}주 동안 약 {short(saved)}을 덜 써요 (평균 결제액 기준).")
        out.append(_finding(f"FREQ_{kind.upper()}", "frequent", "생활 패턴", category=typed[0]["category"],
                            subject=label, conclusion=conclusion, evidence=evidence, impact=amount, actions=actions,
                            activity=kind))
    return out


# ---------- 주말 · 평일 ----------

def weekend(rows, period_start, today):
    paid = _paid(rows)
    days = [period_start + timedelta(days=n) for n in range((today - period_start).days + 1)]
    weekend_days = [d for d in days if d.weekday() >= 5]
    weekdays = [d for d in days if d.weekday() < 5]
    if len(weekend_days) < 2 or len(weekdays) < 4 or not paid:
        return []
    amounts = {True: 0, False: 0}
    by_category = {}
    for r in paid:
        is_weekend = r["occurred_on"].weekday() >= 5
        amounts[is_weekend] += r["net_amount"]
        if is_weekend:
            by_category[r["category"]] = by_category.get(r["category"], 0) + r["net_amount"]
    end_avg, day_avg = amounts[True] / len(weekend_days), amounts[False] / len(weekdays)
    if day_avg <= 0 or end_avg < day_avg * 1.5 or end_avg - day_avg < 10000:
        return []
    ratio = end_avg / day_avg
    conclusion = f"주말 하루에 평일의 {ratio:.1f}배를 쓰고 있어요."
    evidence = [f"주말 하루 평균 {won(round(end_avg, -2))}, 평일 하루 평균 {won(round(day_avg, -2))}이에요 (지난 날짜 기준)."]
    top = max(by_category.items(), key=lambda kv: kv[1], default=None)
    if top and top[0] in NAMES and top[1] * 10 >= amounts[True] * 4:
        evidence.append(f"주말 소비의 {round(top[1] * 100 / amounts[True])}%가 {NAMES[top[0]]}예요.")
    return [_finding("WEEKEND", "weekend", "생활 패턴", conclusion=conclusion, evidence=evidence,
                     impact=round((end_avg - day_avg) * len(weekend_days)))]


# ---------- 주요 카테고리 ----------

def composition(rows):
    paid = [r for r in _paid(rows) if r["category"] in NAMES and r["category"] != "UNCLASSIFIED"]
    total = sum(r["net_amount"] for r in paid)
    if total <= 0:
        return []
    sums = {}
    for r in paid:
        sums[r["category"]] = sums.get(r["category"], 0) + r["net_amount"]
    ranked = sorted(sums.items(), key=lambda kv: -kv[1])
    code, amount = ranked[0]
    share = amount / total
    if share < 0.5 or len(ranked) < 3:  # 두세 항목뿐이면 '쏠림'이 아니다
        return []
    second = ranked[1]
    conclusion = f"이번 달 소비의 {round(share * 100)}%가 {NAMES[code]}에 몰려 있어요."
    evidence = [f"{NAMES[code]} {won(amount)}, 다음으로 많은 {josa(NAMES[second[0]], '은는')} {won(second[1])}({round(second[1] * 100 / total)}%)이에요."]
    return [_finding("SHARE", "composition", "주요 카테고리", category=code, conclusion=conclusion, evidence=evidence,
                     impact=amount * 0.2)]


# ---------- 지난달 같은 기간과의 차이 (어느 카테고리가 · 왜) ----------

def change_total(current, previous, previous_full, material):
    """지난달 같은 기간보다 의미 있게 달라졌을 때: 차이를 만든 카테고리와 그 성격
    previous_full = 지난달 전체 변동 소비 (한 달에 한두 번 사는 것이 이번 달에 일찍 나왔는지 보려고)"""
    if not material.get("meaningful"):
        return []
    def by_category(rows):
        out = {}
        for r in rows:
            if r["category"] in NAMES and r["category"] != "UNCLASSIFIED":
                out[r["category"]] = out.get(r["category"], 0) + r["net_amount"]
        return out
    now, before, full = by_category(current), by_category(previous), by_category(previous_full)
    total = sum(r["net_amount"] for r in current) - sum(r["net_amount"] for r in previous)
    deltas = sorted(((code, now.get(code, 0) - before.get(code, 0)) for code in set(now) | set(before)), key=lambda kv: -abs(kv[1]))
    same_way = [(c, d) for c, d in deltas if d * total > 0 and abs(d) >= 10000][:3]
    if not same_way:
        return []
    explained = sum(d for _, d in same_way)
    word = "더" if total > 0 else "덜"
    if total > 0:  # 늘어난 돈을 큰 결제 몇 건이 대부분 설명하면 그게 답이다 (요구사항 5: 큰 결제 vs 반복 소비 먼저 구분)
        big = [r for r in current if r["net_amount"] >= max(100000, total * 0.3)]
        big_sum = sum(r["net_amount"] for r in big)
        if big and len(big) <= 3 and big_sum * 10 >= total * 6:
            cats = "·".join(sorted({NAMES[r["category"]] for r in big if r["category"] in NAMES}))
            rest = total - big_sum
            conclusion = (f"지난달 같은 기간보다 {short(total)} 더 썼는데, 그 대부분이 {cats} 큰 결제 {len(big)}건({short(big_sum)})이에요. "
                          + ("이 결제를 빼면 지난달과 거의 같아요." if abs(rest) < max(10000, total * 0.15) else
                             f"이 결제를 빼도 {short(abs(rest))} {'더' if rest > 0 else '덜'} 썼어요."))
            evidence = [f"이번 달 {won(sum(r['net_amount'] for r in current))}, 지난달 같은 기간 {won(sum(r['net_amount'] for r in previous))}이에요."]
            return [_finding("CHANGE", "change_total", "지난달과 비교", conclusion=conclusion, evidence=evidence,
                             impact=abs(total), comparable=True, large_amount=big_sum)]
    names = ", ".join(f"{NAMES[c]}({'+' if d > 0 else '-'}{short(abs(d))})" for c, d in same_way)
    conclusion = f"지난달 같은 기간보다 {short(abs(total))} {word} 썼어요. {names}에서 {'늘어난' if total > 0 else '줄어든'} 몫이 대부분이에요." \
        if abs(explained) * 10 >= abs(total) * 6 else \
        f"지난달 같은 기간보다 {short(abs(total))} {word} 썼어요. 가장 크게 달라진 건 {names}예요."
    evidence = []
    if total > 0:
        counts = {}
        for r in current:
            if r["net_amount"] > 0:
                counts[r["category"]] = counts.get(r["category"], 0) + 1
        early = [c for c, d in same_way if d > 0 and counts.get(c, 0) <= 2 and full.get(c, 0) >= now.get(c, 0)]
        if early:
            evidence.append(f"{josa('·'.join(NAMES[c] for c in early), '은는')} 지난달에도 한 달 동안 그만큼 썼던 항목이에요. "
                            f"이번 달엔 평소보다 일찍 산 것에 가까워요.")
    evidence.append(f"이번 달 {won(sum(r['net_amount'] for r in current))}, 지난달 같은 기간 {won(sum(r['net_amount'] for r in previous))}이에요.")
    return [_finding("CHANGE", "change_total", "지난달과 비교", conclusion=conclusion, evidence=evidence,
                     impact=abs(total), comparable=True)]

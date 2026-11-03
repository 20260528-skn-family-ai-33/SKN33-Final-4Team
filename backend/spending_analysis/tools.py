"""소비 분석 조회 도구 (MEMO 23절 1단계) — 거래 읽기 함수 하나 + 도구 3개. 읽기 전용 (예산 · 거래 · 주기를 만들거나 바꾸지 않음)

- rows: 거래를 읽는 유일한 함수. 같은 구매의 계좌 쪽 제외(listed_transactions) → 기간 → 범위 거르기(scope 칸: 카드 결제만 · 가게 이름)
  → 목록 한 줄 모양(transaction_item, 취소 차감 순금액). 비소비 제외는 합계 단계(R.figures · spending_summary)가 한다
- spend_query: 한 기간 소비 조회 → {"period", "coverage", "observed", "confirmed"} (complete 일 때만 confirmed, 0원으로 바꾸지 않음)
- period_compare: 두 기간 비교 → {"current", "previous", "comparison"} (기간 짝은 부르는 쪽 규칙: 이번 달 vs 지난달 같은 날짜까지)
- budget_left: 승인 예산 잔액 (예산 주기 기준) — 3 · 4번
1단계는 기존 1~9번 · 설명 근거가 이 도구를 부르게만 했다. 요일 · 시간대 · 금액 조건, 건수 · 평균 · 묶기 같은 새 계산은 아직 열지 않음
수집 판정 · 승인 예산 조회의 DB 실패는 DbError (부르는 쪽이 state="error" 로 바꾼다)
"""
from datetime import timedelta

from budgets.scope import living_spent, outside_spent
from finance.api import listed_transactions, transaction_item
from finance.merchant_rules import normalize_merchant
from finance.models import SourceKind
from finance.spending import spending_summary

from . import result as R, scope
from .budget import approved_budget
from .coverage import coverage
from .periods import (budget_period, last_month, last_week, month_of, recent_months, recent_months_to_date, resolve_range,
                      this_month, this_week)

LOOKBACK = 3  # 가게 이름 검색: 이번 달 + 완료된 최근 3개월 안에 그 이름의 결제가 있어야 검색한다


class DbError(Exception):
    """approved_budget · coverage 가 돌려준 error 상태"""


def _ok(found):
    if found["state"] == "error":
        raise DbError(found.get("reason"))
    return found


# ---------- 거래 읽기 ----------

def _named(tx, words):
    """가게 이름 검색: 공백 · 대소문자를 무시하고 이름에 그 말(다른 표기 포함) 중 하나가 들어 있으면 (카드 = 가맹점명, 계좌 = 적요)
    다른 표기('STARBUCKS' 등)는 해석 LLM 이 가게 이름에서 떠올린 것 — 사용자의 거래 내역은 LLM 에 보내지 않는다
    shortcut: 이름 포함만 본다 — 표기 목록에 없는 이름은 못 묶는다, 가맹점 규칙(MEMO 20번)이 정해지면 그 규칙으로"""
    return any(w in scope.plain(name) for name in (tx.merchant_name, tx.source_description) for w in words)


def _words(word, names=()):
    """찾을 표기: 부른 말 + LLM 이 떠올린 다른 표기 + 온라인 서비스 별칭 표 (배달 → 배달앱 브랜드 전부)"""
    return [w for w in {scope.plain(word), *(scope.plain(n) for n in (*(names or ()), *scope.online(word)[1]))} if w]


def rows(user, period, where=None, stats=None, category=None):
    """기간 거래 → 목록 한 줄 모양. where(분석 항목 · 범위 칸)로 거른다:
    payment(card · bank) · merchant(가게 이름) · weekdays(요일) · time_slots(시간대) · amount_min · amount_max(한 건 순금액)
    시간대 조건이 있으면 시각 없는 거래는 빼고, stats 를 주면 뺀 소비 건수를 stats["no_time"] 에 더한다
    요일 · 시간대 조건이나 묶기(10번 group_by)면 고정지출을 기본으로 뺀다(include_fixed 면 포함) — 뺀 금액은 stats["fixed_excluded"]"""
    where = where or {}
    found = listed_transactions(user).filter(occurred_on__gte=period.start, occurred_on__lt=period.end_before)
    if where.get("payment"):
        found = found.filter(source_kind={"card": SourceKind.CARD, "bank": SourceKind.BANK}[where["payment"]])
    if where.get("merchant"):
        words = _words(where["merchant"], where.get("merchant_names"))
        found = [tx for tx in found if _named(tx, words)]
    items = [transaction_item(tx) for tx in found]
    days, slots = where.get("weekdays"), where.get("time_slots")
    low, high = where.get("amount_min"), where.get("amount_max")
    if days:
        items = [r for r in items if r["occurred_on"].weekday() in days]
    if slots:
        missing = [r for r in items if r["occurred_time"] is None]
        if stats is not None:
            stats["no_time"] = stats.get("no_time", 0) + sum(r["transaction_type"] == "spending" for r in missing)
        items = [r for r in items if r["occurred_time"] is not None and scope.slot_of(r["occurred_time"]) in slots]
    if scope.excludes_fixed(where):
        fixed = [r for r in items if r["transaction_type"] == "spending" and r["funding"] == "fixed"]
        if stats is not None:
            # 안내할 금액 · 이름은 답과 같은 조건 — 카테고리로 좁힌 조회면 그 카테고리의 고정지출만 (11/2: 지우 지난주 식비에 월세가 세어졌다)
            fixed = [r for r in fixed if category is None or r["category"] == category]
            stats["fixed_excluded"] = stats.get("fixed_excluded", 0) + sum(r["net_amount"] for r in fixed)
            names = stats.setdefault("fixed_names", [])
            names += [r["merchant_name"] for r in fixed if r["merchant_name"] and r["merchant_name"] not in names]
        items = [r for r in items if not (r["transaction_type"] == "spending" and r["funding"] == "fixed")]
    if low is not None:
        items = [r for r in items if r["net_amount"] >= low]
    if high is not None:
        items = [r for r in items if r["net_amount"] <= high]
    return items


def has_merchant(user, word, today, names=()):
    """그 말(또는 다른 표기)이 이름에 들어간 소비 결제가 이번 달 + 최근 LOOKBACK 개월에 있는지 (가게 이름으로 찾을지 정할 때 — 읽기만)"""
    words = _words(word, names)
    if not words:
        return False
    start = recent_months(today, LOOKBACK)[0].start
    found = listed_transactions(user).filter(occurred_on__gte=start, occurred_on__lte=today, transaction_type="spending")
    return any(_named(tx, words) for tx in found)


# ---------- 도구 ----------

def amount(fig, category):
    return fig["category"]["amount"] if category else fig["total"]


def _detail(items, category, include_fixed=False):
    """설명 근거용 소비 집계: 금액 · 결제 건수(순금액 > 0) · 건당 평균 · 가게별 · 카테고리별 · 고정지출 합
    [임시] 건수 = 순금액이 0 보다 큰 소비 거래 (전액 취소는 세지 않는다), 가게 = normalize_merchant (MEMO 20번)"""
    picked = [r for r in items if r["transaction_type"] == "spending" and (category is None or r["category"] == category)]
    paid = [r for r in picked if r["net_amount"] > 0]
    metrics = {axis: {} for axis in ("merchant", "category", "time_slot", "weekday")}
    for r in paid:
        dimensions = [("merchant", normalize_merchant(r["merchant_name"])), ("category", r["category"])]
        if include_fixed or r["funding"] != "fixed":
            dimensions += [("time_slot", scope.slot_of(r["occurred_time"])), ("weekday", r["occurred_on"].weekday())]
        for axis, key in dimensions:
            cell = metrics[axis].setdefault(key, {"amount": 0, "count": 0})
            cell["amount"] += r["net_amount"]
            cell["count"] += 1
    merchants, categories, slots, days, counts, reducible = {}, {}, {}, {}, {}, {}
    for r in picked:
        name = normalize_merchant(r["merchant_name"])
        merchants[name] = merchants.get(name, 0) + r["net_amount"]
        categories[r["category"]] = categories.get(r["category"], 0) + r["net_amount"]
        if r["funding"] == "fixed":  # 시간대 · 요일 · 큰 결제 · 줄이는 방법은 생활 소비만 (고정지출 기본 제외 규칙과 같게)
            continue
        slot, day = scope.slot_of(r["occurred_time"]), r["occurred_on"].weekday()
        slots[slot] = slots.get(slot, 0) + r["net_amount"]
        days[day] = days.get(day, 0) + r["net_amount"]
        if r["net_amount"] > 0:  # 줄이는 방법의 건당 평균용 결제 건수 (가게 · 시간대 · 요일)
            for key in (("merchant", name), ("slot", slot), ("weekday", day)):
                counts[key] = counts.get(key, 0) + 1
        if r["category"] not in scope.NO_REDUCE:  # 줄이는 방법 후보는 줄일 수 있는 카테고리의 결제만
            reducible[slot] = reducible.get(slot, 0) + r["net_amount"]
            if r["net_amount"] > 0:
                counts["reducible", slot] = counts.get(("reducible", slot), 0) + 1
                counts["reducible_merchant", name] = counts.get(("reducible_merchant", name), 0) + 1
    big = sorted((r for r in paid if r["funding"] != "fixed"),
                 key=lambda r: (-r["net_amount"], r["occurred_on"], r["occurred_time"] or ""))[:3]
    return {"amount": sum(r["net_amount"] for r in picked), "count": len(paid),
            "average": R.average(sum(r["net_amount"] for r in paid), len(paid)),
            "merchants": merchants, "categories": categories, "slots": slots, "weekdays": days, "counts": counts,
            "reducible_slots": reducible, "metric_groups": metrics,
            "big": [{"name": normalize_merchant(r["merchant_name"]), "amount": r["net_amount"], "day": r["occurred_on"]} for r in big],
            "fixed": sum(r["net_amount"] for r in picked if r["funding"] == "fixed")}


def spend_query(user, period, category=None, *, where=None, overview=False, detail=False):
    """한 기간 소비 조회. category = 카테고리 금액 · 전체 대비 비중, overview = 상위 카테고리 · 비중(5번),
    detail = 설명 근거 집계(observed 에 'detail')"""
    cov, stats = _ok(coverage(user, period)), {}
    items = rows(user, period, where, stats, category)
    fig = R.figures(items)
    if category:
        fig["category"] = R.category_view(fig, category)
    if overview:
        fig["top_spending"], fig["shares"] = R.top_spending(fig["by_category"]), R.shares(fig)
    if detail:
        fig["detail"] = _detail(items, category, bool((where or {}).get("include_fixed")))
    out = {"period": period, "coverage": cov, "observed": fig, "confirmed": fig if cov["state"] == "complete" else None}
    return {**out, "fixed_excluded": stats["fixed_excluded"], "fixed_names": stats["fixed_names"]} if "fixed_excluded" in stats else out


def period_compare(user, current, previous, category=None, *, where=None):
    """두 기간 비교. 두 기간 모두 complete 일 때만 확정 증감 (R.compare)"""
    cur = spend_query(user, current, category, where=where)
    prev = spend_query(user, previous, category, where=where)
    done = (cur["confirmed"] is not None, prev["confirmed"] is not None)
    comparison = {"total": R.compare(cur["observed"]["total"], prev["observed"]["total"], *done)}
    if category:
        comparison["category"] = R.compare(amount(cur["observed"], category), amount(prev["observed"], category), *done)
    else:  # chat.md categories[].prevDiff · prevDiffRate (6번 필수). '확인 필요'는 unclassifiedAmount 한 줄이라 표에서 뺀다
        now, before = cur["observed"]["by_category"], prev["observed"]["by_category"]
        comparison["categories"] = {c: R.compare(now.get(c, 0), before.get(c, 0), *done) for c in R.CATEGORY_ORDER
                                    if c != R.UNCLASSIFIED and (c in now or c in before)}
    return {"current": cur, "previous": prev, "comparison": comparison}


def budget_left(user, category, today, breakdown=False):
    """3 · 4번. 승인 예산이 있어도 사용액 범위가 complete 가 아니면 confirmed=None (확정 잔액 없음)
    days_left = 사용액을 센 다음 날부터 주기 끝까지 일수 (하루에 쓸 수 있는 금액용). breakdown = 4번에 카테고리별 잔액도"""
    found = _ok(approved_budget(user, today))
    out = {"budget": {"state": found["state"]}}
    if found["state"] == "no_cycle":
        return out
    cycle = found["cycle"]
    span = budget_period(cycle["starts_on"], cycle["ends_before"], today)
    cov = _ok(coverage(user, span["spent"]))
    living = spending_summary(rows(user, span["spent"]), living_only=True)
    # 예산 범위 밖 카테고리(주거·통신 · 여행·숙박)는 사용액 · 남은 돈 · 하루 금액에서 뺀다 (11/3 확정 — 11/2 2차의 '포함'을 바꿈).
    # 그 금액은 '예산 밖 지출' 안내용으로 카테고리별로 남긴다 (홈 · 예산 화면과 같은 budgets.scope 계산)
    outside = outside_spent(living)
    spent = living["by_category"].get(category, 0) if category else living_spent(living)
    out.update(cycle_period=span["cycle"], spent_period=span["spent"], coverage=cov,
               observed={"spent": spent}, confirmed=None, days_left=(span["cycle"].end_before - span["spent"].end_before).days,
               outside_spent={} if category else outside,
               # 같은 예산 기간의 '확인 필요' 생활비 결제 — 카테고리 잔액 · 하루 금액은 분류된 결제 기준이라 안내용 (MEMO 25-13)
               unclassified=living["by_category"].get(R.UNCLASSIFIED, 0))
    if found["state"] == "approved":  # no_approved 면 초안 · 프로필 목표 금액을 담지 않는다
        allocated = found["living"].get(category) if category else found["living_total"]
        out["budget"].update({k: found["budget"][k] for k in ("budget_id", "version_no", "approved_at")},
                             allocated=allocated)
        if cov["state"] == "complete" and allocated is not None:
            out["confirmed"] = R.budget_numbers(allocated, spent, None if category else found["spare"])
            if breakdown:  # 승인 예산에 배정된 카테고리만, 화면 순서대로
                out["categories"] = [{"category": c, **R.budget_numbers(found["living"][c], living["by_category"].get(c, 0))}
                                     for c in R.CATEGORY_ORDER if found["living"].get(c) is not None]
    return out


# ---------- 범용 조회 (10번 · MEMO 23-1 spend_query 의 계산 · 묶기) ----------

MEASURES = ("sum", "count", "avg", "daily_avg", "max")
GROUPS = ("category", "merchant", "weekday", "time_slot", "month")
TOP_MAX = 5
QUERY_PERIODS = ("this_month", "last_month", "recent", "this_week", "last_week", "month", "range")


def check_query(item):
    """10번 항목의 query 칸 검사 (잘못되면 ValueError). 개월 수 · 이번 달 포함은 항목 칸(analyze.validate)"""
    q = item.get("query")
    if not isinstance(q, dict) or set(q) - {"period", "measure", "group_by", "top_n"}:
        raise ValueError(f"10번 query 칸: {item!r}")
    if q.get("period") not in QUERY_PERIODS or q.get("measure") not in MEASURES:
        raise ValueError(f"query 기간 · 계산: {q!r}")
    group, top = q.get("group_by"), q.get("top_n")
    if group is not None and (group not in GROUPS or q["measure"] not in ("sum", "count")):
        raise ValueError(f"묶기는 합계 · 건수와만 (평균 · 최대는 묶지 않음): {q!r}")
    if group == "month" and q["period"] != "recent":
        raise ValueError(f"월별 묶기는 최근 N개월만: {q!r}")
    if top is not None and (type(top) is not int or not 1 <= top <= TOP_MAX or group is None):
        raise ValueError(f"top_n 은 1~{TOP_MAX}, 묶기와 함께: {q!r}")


def query_periods(item, today):
    """범용 조회와 구성 설명이 공유하는 기간 해석. 중간 날짜를 채워 이어 붙이지 않는다."""
    period = item["query"]["period"]
    if period in ("month", "range"):
        found = month_of(today, item.get("month")) if period == "month" else \
            resolve_range(today, item.get("date_from"), item.get("date_to"))["period"]
        if found is None:  # 대화 계층이 범위를 먼저 확인한다 — 여기 오면 잘못된 항목
            raise ValueError(f"볼 수 있는 범위 밖의 기간: {item!r}")
        return [found]
    simple = {"this_month": this_month, "last_month": last_month, "this_week": this_week, "last_week": last_week}
    if period in simple:
        return [simple[period](today)]
    span = recent_months_to_date if item.get("include_current") else recent_months
    return span(today, item["months_applied"])


def _key(r, group, period):
    if group == "category":
        return r["category"]
    if group == "merchant":
        return normalize_merchant(r["merchant_name"])
    if group == "weekday":
        return r["occurred_on"].weekday()
    if group == "time_slot":
        return scope.slot_of(r["occurred_time"])
    return period.start.month


def _group_name(group, key):
    if group == "category":
        return scope.CATEGORY_NAMES.get(key, key)
    if group == "weekday":
        return f"{scope.DAY_NAMES[key]}요일"
    if group == "time_slot":
        return scope.SLOTS[key][0]
    if group == "month":
        return f"{key}월"
    return key


def _groups(picked, q):
    """묶음별 합계 · 건수. 요일 · 시간대 · 월은 정해진 순서, 카테고리 · 가게는 큰 순서(건수를 물으면 건수 → 금액 → 이름).
    top_n 이 있으면 큰 순서로 그만큼 (정해진 순서 묶음도 큰 순서로)"""
    group, by_count = q["group_by"], q["measure"] == "count"
    acc, order = {}, []
    for period, r in picked:
        key = _key(r, group, period)
        if key is None:  # 가게 이름 없음 · 시각 없음은 묶음에서 뺀다
            continue
        if key not in acc:
            acc[key] = {"key": key, "name": _group_name(group, key), "sum": 0, "count": 0}
            order.append(key)
        acc[key]["sum"] += r["net_amount"]
        acc[key]["count"] += r["net_amount"] > 0
    fixed = {"weekday": range(7), "time_slot": list(scope.SLOTS), "month": order}.get(group)
    if fixed is not None and q.get("top_n") is None:
        return [acc[k] for k in fixed if k in acc]
    ranked = sorted(acc.values(), key=lambda g: ((-g["count"], -g["sum"]) if by_count else (-g["sum"], -g["count"]), str(g["name"])))
    return ranked[:q["top_n"]] if q.get("top_n") else ranked


def _biggest(paid):
    """가장 큰 1건 (동률이면 날짜 → 시각 → 가게 이름 순 첫 건). 없으면 None"""
    big = min(paid, key=lambda r: (-r["net_amount"], r["occurred_on"], r["occurred_time"] or "", r["merchant_name"] or ""),
              default=None)
    return big and {k: big[k] for k in ("occurred_on", "occurred_time", "merchant_name", "category", "net_amount", "funding")}


def query(user, item, today):
    """10번 범용 조회 → {"periods", "states", "coverage", "observed", "confirmed", "no_time", "days"}
    모든 기간이 complete 일 때만 confirmed. 소비만 · 카테고리 · 범위 조건은 rows · 여기서. 금액은 순금액(취소 차감)"""
    q, category, stats = item["query"], item.get("category"), {"no_time": 0, "fixed_excluded": 0, "fixed_names": []}
    periods, states, picked, unclassified = query_periods(item, today), [], [], 0
    for period in periods:
        states.append(_ok(coverage(user, period))["state"])
        spent = [r for r in rows(user, period, item, stats, category) if r["transaction_type"] == "spending"]
        picked += [(period, r) for r in spent if category is None or r["category"] == category]
        # 카테고리로 좁힌 조회: 같은 조건의 '확인 필요' 금액 (분류가 정해지면 답이 달라질 수 있다는 안내용)
        unclassified += sum(r["net_amount"] for r in spent if category and r["category"] == R.UNCLASSIFIED)
    paid = [r for _, r in picked if r["net_amount"] > 0]
    # 하루 평균 분모 = 조건에 맞는 날짜 수(쓴 돈이 없는 날 포함). 요일 조건이면 그 요일인 날만, 시간대는 날짜 수를 줄이지 않는다
    days = sum(1 for p in periods for n in range((p.end_before - p.start).days)
               if not item.get("weekdays") or (p.start + timedelta(n)).weekday() in item["weekdays"])
    total = sum(r["net_amount"] for _, r in picked)
    living = sum(r["net_amount"] for _, r in picked if r["funding"] != "fixed")
    fig = {"sum": total, "count": len(paid), "avg": R.average(sum(r["net_amount"] for r in paid), len(paid)),
           "daily_avg": R.average(total, days), "daily_avg_living": R.average(living, days), "fixed": total - living,
           "max": _biggest(paid), "groups": _groups(picked, q) if q.get("group_by") else None}
    if fig["max"] and fig["max"]["funding"] == "fixed":  # 가장 큰 1건이 고정지출이면 그걸 뺀 가장 큰 지출도
        fig["max_living"] = _biggest([r for r in paid if r["funding"] != "fixed"])
    state = "complete" if all(s == "complete" for s in states) else "none" if all(s == "none" for s in states) else "partial"
    clipped = q["period"] == "range" and resolve_range(today, item.get("date_from"), item.get("date_to"))["state"] == "clipped"
    return {"periods": periods, "states": states, "coverage": {"state": state}, "observed": fig, "clipped": clipped,
            "confirmed": fig if state == "complete" else None, "no_time": stats["no_time"], "days": days,
            "unclassified": unclassified if category != R.UNCLASSIFIED else 0,
            "fixed_excluded": stats["fixed_excluded"] if scope.excludes_fixed(item) else None, "fixed_names": stats["fixed_names"]}

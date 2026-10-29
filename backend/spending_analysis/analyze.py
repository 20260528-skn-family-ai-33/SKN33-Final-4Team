"""소비 분석 1~9번 내부 결과 — 읽기 전용 실행 (예산 · 거래 · 주기를 만들거나 바꾸지 않음, 잠금 없음)

입력: 라우터 Decision.items 한 항목 {type, category, months_requested?, months_applied?} + 명시적 기준 날짜 · 조회 시각.
라우터의 run_items(items, fetch) 에는 fetch = lambda item: analyze(user, item, today=..., as_of=...) 로 끼운다(연결은 다음 단계).

기간마다 블록 {"period", "coverage", "observed", "confirmed"}:
- observed = 관측된 거래 합계(수집이 불완전해도 있음) / confirmed = coverage 가 complete 일 때만 같은 값, 아니면 None
- 수집 판정 · 승인 예산 조회 · 거래 조회의 DB 실패는 state="error" (0원 · 거래 없음으로 바꾸지 않음)
1 · 2 · 5 = 이번 달, 3 · 4 = 승인 예산 주기의 시작일 ~ 기준일 생활비, 6 · 7 = 이번 달 vs 지난달 같은 날짜, 8 · 9 = 완료된 N개월 (include_current 면 이번 달 포함 N개월, 달마다 1일 ~ 기준일과 같은 일자)
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.db import DatabaseError

from finance.api import listed_transactions, transaction_item
from finance.spending import spending_summary

from . import result as R
from .budget import approved_budget
from .coverage import coverage
from .periods import budget_period, previous_month_same_days, recent_months, recent_months_to_date, this_month

KST = ZoneInfo("Asia/Seoul")
ITEM_KEYS = {"type", "category", "months_requested", "months_applied", "include_current", "instead_of"}
CATEGORY_TYPES, MONTH_TYPES = {2, 3, 7, 9}, {8, 9}


class _DbError(Exception):
    """approved_budget · coverage 가 돌려준 error 상태"""


def _ok(found):
    if found["state"] == "error":
        raise _DbError(found.get("reason"))
    return found


def validate(item, today, as_of):
    """잘못된 입력은 ValueError. 보정하지 않는다 (개월 수 기본값 · 상한은 라우터 책임)"""
    if type(today) is not date:
        raise ValueError("기준 날짜는 date")
    if not isinstance(as_of, datetime) or as_of.utcoffset() is None:
        raise ValueError("조회 시각은 시간대가 있는 datetime")
    if as_of.astimezone(KST).date() < today:
        raise ValueError("조회 시각이 기준 날짜보다 앞선다")
    if not isinstance(item, dict) or set(item) - ITEM_KEYS:
        raise ValueError(f"알 수 없는 항목 칸: {item!r}")
    kind, category = item.get("type"), item.get("category")
    if type(kind) is not int or not 1 <= kind <= 9:
        raise ValueError(f"분석 유형은 1~9 정수: {kind!r}")
    if kind in CATEGORY_TYPES:
        if category not in R.CATEGORY_ORDER or (kind == 3 and category == R.UNCLASSIFIED):
            raise ValueError(f"{kind}번에 쓸 수 없는 카테고리: {category!r}")
    elif category is not None:
        raise ValueError(f"{kind}번은 카테고리를 받지 않는다")
    if kind in MONTH_TYPES:
        recent_months(today, item.get("months_applied"))  # 1~3 정수 검사
        requested = item.get("months_requested")
        if requested is not None and type(requested) is not int:
            raise ValueError(f"months_requested 는 정수 또는 None: {requested!r}")
    elif item.get("months_applied") is not None or item.get("months_requested") is not None:
        raise ValueError(f"{kind}번은 개월 수를 받지 않는다")
    if item.get("include_current") not in (None, True) or (item.get("include_current") and kind not in MONTH_TYPES):
        raise ValueError(f"이번 달 포함은 8 · 9번에만 true: {item!r}")
    if item.get("instead_of") is not None and (not isinstance(item["instead_of"], str) or category is None):
        raise ValueError(f"instead_of 는 카테고리 항목의 원래 말(문자열): {item!r}")


def _items(user, period):
    rows = listed_transactions(user).filter(occurred_on__gte=period.start, occurred_on__lt=period.end_before)
    return [transaction_item(tx) for tx in rows]


def _block(user, period, category=None, overview=False):
    cov = _ok(coverage(user, period))
    fig = R.figures(_items(user, period))
    if category:
        fig["category"] = R.category_view(fig, category)
    if overview:
        fig["top_spending"], fig["shares"] = R.top_spending(fig["by_category"]), R.shares(fig)
    return {"period": period, "coverage": cov, "observed": fig,
            "confirmed": fig if cov["state"] == "complete" else None}


def _amount(fig, category):
    return fig["category"]["amount"] if category else fig["total"]


def _budget(user, category, today):
    """3 · 4번. 승인 예산이 있어도 사용액 범위가 complete 가 아니면 confirmed=None (확정 잔액 없음)"""
    found = _ok(approved_budget(user, today))
    out = {"budget": {"state": found["state"]}}
    if found["state"] == "no_cycle":
        return out
    cycle = found["cycle"]
    span = budget_period(cycle["starts_on"], cycle["ends_before"], today)
    cov = _ok(coverage(user, span["spent"]))
    living = spending_summary(_items(user, span["spent"]), living_only=True)
    spent = living["by_category"].get(category, 0) if category else living["spent_total"]
    out.update(cycle_period=span["cycle"], spent_period=span["spent"], coverage=cov,
               observed={"spent": spent}, confirmed=None)
    if found["state"] == "approved":  # no_approved 면 초안 · 프로필 목표 금액을 담지 않는다
        allocated = found["living"].get(category) if category else found["living_total"]
        out["budget"].update({k: found["budget"][k] for k in ("budget_id", "version_no", "approved_at")},
                             allocated=allocated)
        if cov["state"] == "complete" and allocated is not None:
            out["confirmed"] = R.budget_numbers(allocated, spent, None if category else found["spare"])
    return out


def _run(user, kind, category, item, today):
    if kind in (1, 2, 5):
        return {"current": _block(user, this_month(today), category, overview=kind == 5)}
    if kind in (3, 4):
        return _budget(user, category, today)
    if kind in (6, 7):
        cur = _block(user, this_month(today), category)
        prev = _block(user, previous_month_same_days(today), category)
        done = (cur["confirmed"] is not None, prev["confirmed"] is not None)
        comparison = {"total": R.compare(cur["observed"]["total"], prev["observed"]["total"], *done)}
        if category:
            comparison["category"] = R.compare(_amount(cur["observed"], category), _amount(prev["observed"], category), *done)
        else:  # chat.md categories[].prevDiff · prevDiffRate (6번 필수). '확인 필요'는 unclassifiedAmount 한 줄이라 표에서 뺀다
            now, before = cur["observed"]["by_category"], prev["observed"]["by_category"]
            comparison["categories"] = {c: R.compare(now.get(c, 0), before.get(c, 0), *done) for c in R.CATEGORY_ORDER
                                        if c != R.UNCLASSIFIED and (c in now or c in before)}
        return {"current": cur, "previous": prev, "comparison": comparison}
    span = recent_months_to_date if item.get("include_current") else recent_months  # 포함이면 달마다 같은 날짜까지
    months = [_block(user, p, category) for p in span(today, item["months_applied"])]
    # 빠진 달도 목록에 남긴다. 불완전한 달의 확정 자리는 None (0원 아님)
    series = [{"period": m["period"], "state": m["coverage"]["state"], "observed": _amount(m["observed"], category),
               "confirmed": _amount(m["confirmed"], category) if m["confirmed"] else None} for m in months]
    return {"months": months, "series": series, "stats": R.trend_stats(series)}


def analyze(user, item, *, today, as_of):
    """→ {"item", "type", "category", "today", "as_of", "state": "ok" | "error", ...유형별 결과}"""
    validate(item, today, as_of)
    kind, category = item["type"], item.get("category")
    head = {"item": dict(item), "type": kind, "category": category, "today": today, "as_of": as_of}
    try:
        body = _run(user, kind, category, item, today)
    except (DatabaseError, _DbError):
        return {**head, "state": "error", "reason": "db_error"}
    return {**head, "state": "ok", **body}

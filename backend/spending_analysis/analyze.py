"""소비 분석 1~9번 내부 결과 — 읽기 전용 실행 (예산 · 거래 · 주기를 만들거나 바꾸지 않음, 잠금 없음)

입력: 라우터 Decision.items 한 항목 {type, category, months_requested?, months_applied?} + 명시적 기준 날짜 · 조회 시각.
라우터의 run_items(items, fetch) 에는 fetch = lambda item: analyze(user, item, today=..., as_of=...) 로 끼운다(연결은 다음 단계).

기간마다 블록 {"period", "coverage", "observed", "confirmed"}:
- observed = 관측된 거래 합계(수집이 불완전해도 있음) / confirmed = coverage 가 complete 일 때만 같은 값, 아니면 None
- 수집 판정 · 승인 예산 조회 · 거래 조회의 DB 실패는 state="error" (0원 · 거래 없음으로 바꾸지 않음)
조회는 tools(거래 읽기 rows + 도구 spend_query · period_compare · budget_left, MEMO 23절)가 하고 여기는 1~9번을 그 호출로 바꾼다.
10 = 범용 조회(tools.query): 기간(이번 달 · 지난달 전체 · 최근 N개월) + 조건(scope 칸) + 계산(합계 · 건수 · 평균 · 하루 평균 · 최대 1건) + 묶기 · 상위 N
1 · 2 · 5 = 이번 달 (1 · 5 는 카드 아래 막대용 living_budget = 4번 결과를 함께 — 기간이 달라 본문과 섞지 않음), 3 · 4 = 승인 예산 주기의 시작일 ~ 기준일 생활비, 6 · 7 = 이번 달 vs 지난달 같은 날짜, 8 · 9 = 완료된 N개월 (include_current 면 이번 달 포함 N개월, 달마다 1일 ~ 기준일과 같은 일자)
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.db import DatabaseError

from . import result as R, scope, tools
from .periods import previous_month_same_days, recent_months, recent_months_to_date, this_month
from .tools import DbError

KST = ZoneInfo("Asia/Seoul")
ITEM_KEYS = {"type", "category", "months_requested", "months_applied", "include_current", "instead_of", "breakdown", "query",
             "month", "date_from", "date_to", "include_fixed", *scope.FILTERS}
CATEGORY_TYPES, MONTH_TYPES = {2, 3, 7, 9}, {8, 9}


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
    if type(kind) is not int or not 1 <= kind <= 10:
        raise ValueError(f"분석 유형은 1~10 정수: {kind!r}")
    if kind == 10:  # 범용 조회: 카테고리는 있어도 없어도, 개월 수는 최근 N개월일 때만
        tools.check_query(item)
        if category is not None and category not in R.CATEGORY_ORDER:
            raise ValueError(f"10번 카테고리: {category!r}")
        if (item["query"]["period"] == "recent") != (item.get("months_applied") is not None):
            raise ValueError(f"10번 개월 수는 최근 N개월에만: {item!r}")
        if (item["query"]["period"] == "month") != (item.get("month") is not None) \
                or (item["query"]["period"] == "range") != (item.get("date_from") is not None):
            raise ValueError(f"10번 특정 달 · 날짜 범위 칸: {item!r}")
    elif any(item.get(k) is not None for k in ("month", "date_from", "date_to")):
        raise ValueError(f"특정 달 · 날짜 범위는 10번에만: {item!r}")
    if item.get("include_fixed") not in (None, True):
        raise ValueError(f"include_fixed 는 true 만: {item!r}")
    if kind in CATEGORY_TYPES:
        if category not in R.CATEGORY_ORDER or (kind == 3 and category == R.UNCLASSIFIED):
            raise ValueError(f"{kind}번에 쓸 수 없는 카테고리: {category!r}")
    elif kind != 10 and category is not None:
        raise ValueError(f"{kind}번은 카테고리를 받지 않는다")
    if kind in MONTH_TYPES or (kind == 10 and item.get("months_applied") is not None):
        recent_months(today, item.get("months_applied"))  # 1~3 정수 검사
        requested = item.get("months_requested")
        if requested is not None and type(requested) is not int:
            raise ValueError(f"months_requested 는 정수 또는 None: {requested!r}")
    elif item.get("months_applied") is not None or item.get("months_requested") is not None:
        raise ValueError(f"{kind}번은 개월 수를 받지 않는다")
    if item.get("include_current") not in (None, True) or (item.get("include_current") and kind not in MONTH_TYPES
                                                             and item.get("months_applied") is None):
        raise ValueError(f"이번 달 포함은 최근 N개월(8 · 9 · 10번)에만 true: {item!r}")
    if item.get("instead_of") is not None and (not isinstance(item["instead_of"], str) or category is None):
        raise ValueError(f"instead_of 는 카테고리 항목의 원래 말(문자열): {item!r}")
    scope.check(item, kind)
    if item.get("breakdown") not in (None, True) or (item.get("breakdown") and kind != 4):
        raise ValueError(f"breakdown(카테고리별 잔액)은 4번(생활비 남은 예산)에만 true: {item!r}")


def _living_budget(user, today):
    """1 · 5번 카드 아래 '이번 예산 기간 · 생활비' 막대. 못 읽으면 None — 본문 답은 그대로 둔다"""
    try:
        return tools.budget_left(user, None, today)
    except (DatabaseError, DbError):
        return None


def _run(user, kind, category, item, today):
    if kind in (1, 2, 5):
        out = {"current": tools.spend_query(user, this_month(today), category, where=item, overview=kind == 5)}
        if kind != 2 and not scope.filters(item):  # 거른 조회엔 생활비 막대를 붙이지 않는다
            out["living_budget"] = _living_budget(user, today)
        return out
    if kind == 10:
        return tools.query(user, item, today)
    if kind in (3, 4):
        return tools.budget_left(user, category, today, breakdown=bool(item.get("breakdown")))
    if kind in (6, 7):
        return tools.period_compare(user, this_month(today), previous_month_same_days(today), category, where=item)
    span = recent_months_to_date if item.get("include_current") else recent_months  # 포함이면 달마다 같은 날짜까지
    months = [tools.spend_query(user, p, category, where=item) for p in span(today, item["months_applied"])]
    # 빠진 달도 목록에 남긴다. 불완전한 달의 확정 자리는 None (0원 아님)
    series = [{"period": m["period"], "state": m["coverage"]["state"], "observed": tools.amount(m["observed"], category),
               "confirmed": tools.amount(m["confirmed"], category) if m["confirmed"] else None} for m in months]
    return {"months": months, "series": series, "stats": R.trend_stats(series)}


def analyze(user, item, *, today, as_of):
    """→ {"item", "type", "category", "today", "as_of", "state": "ok" | "error", ...유형별 결과}"""
    validate(item, today, as_of)
    kind, category = item["type"], item.get("category")
    head = {"item": dict(item), "type": kind, "category": category, "today": today, "as_of": as_of}
    try:
        body = _run(user, kind, category, item, today)
    except (DatabaseError, DbError):
        return {**head, "state": "error", "reason": "db_error"}
    return {**head, "state": "ok", **body}

"""범용 조회(10번) 독립 기대값 — design.json 만 읽어 따로 계산한다 (앱 코드 import 없음 · DB 없음). expected.json 은 그대로

    cd backend && ../.venv/bin/python -I spending_fixture/make_expected_query.py     → data/expected_query.json

계산 규칙 (앱과 같은 뜻, 코드는 따로): 목록 = 같은 구매의 계좌 쪽(excluded_same_purchase) 제외 · 소비(spending)만 ·
순금액 = 금액 − 취소 금액 · 건수 = 순금액 > 0 · 평균 = 원 단위 반올림(0.5 올림) · 기준 시각 2026-10-29 (이번 달 = 10/1~10/29, 29일) ·
시간대 = 새벽 0~5 · 아침 6~10 · 점심 11~13 · 오후 14~17 · 저녁 18~20 · 밤 21~23시 · 요일 0=월.
가게 묶음 순서: 건수를 물으면 건수 → 금액 → 이름, 그 밖엔 금액 → 건수 → 이름. 가장 큰 지출 동률은 날짜 → 시각 → 이름.
수집 상태 = coverage_plan 의 성공 기록만: 회원의 계좌 · 카드 모두 기간을 빈 날 없이 덮으면 complete, 덮은 날이 하나도 없으면 none, 그 밖 partial.
고정지출 = funding "fixed". 민재(B): 같은 구매 · 부분 취소 · 전액 취소 / 지우(C): 일부 수집 · 확인 필요(UNCLASSIFIED) · 자료 없는 달.
[판 2026-10-30.2] 요일 · 시간대 조건이나 묶기(~별 · 상위)는 고정지출을 기본으로 뺀다 (사용자 결정 10/30 — 월세 같은 자동이체가
'화요일 · 아침'을 덮지 않게). 총액 · 카테고리 · 금액 범위 · 건수 · 평균 · 최대는 포함 그대로. 바뀐 값: 요일별 · 시간대별 · 가게 상위 ·
카테고리별 건수 (판 .1 에서 포함 계산). 지정 기간: 이번 주 = 월 ~ 기준일, 지난주 = 월 ~ 일, 특정 달 · 날짜 범위.
[판 2026-11-02.1] 달보다 짧은 기간(이번 주 · 지난주 · 날짜 범위) 총액도 고정지출을 빼고 같은 기간 고정지출을 따로 (바뀐 값: 이번 주 ·
날짜 범위 합계). 특정 달 · 이번 달 · 지난달 총액은 그대로 포함.
"""
import json
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent / "data"
from datetime import timedelta

THIS = (date(2026, 10, 1), date(2026, 10, 29))
LAST = (date(2026, 9, 1), date(2026, 9, 30))
SLOT = [("dawn", 0, 5), ("morning", 6, 10), ("lunch", 11, 13), ("afternoon", 14, 17), ("evening", 18, 20), ("night", 21, 23)]


def half_up(total, n):
    return (2 * total + n) // (2 * n)


def spending(design, persona, span, **cond):
    out = []
    for r in design["transaction_design_rows"]:
        day = date.fromisoformat(r["occurred_on"])
        if r["persona"] != persona or r["excluded_same_purchase"] or r["transaction_type"] != "spending" \
                or not span[0] <= day <= span[1]:
            continue
        net, hour = r["amount_won"] - r["canceled_amount_won"], int(r["occurred_time"][:2])
        slot = next(name for name, a, b in SLOT if a <= hour <= b)
        if cond.get("category") and r["category"] != cond["category"]:
            continue
        if cond.get("weekdays") and day.weekday() not in cond["weekdays"]:
            continue
        if cond.get("slots") and slot not in cond["slots"]:
            continue
        if cond.get("source") and r["source_kind"] != cond["source"]:
            continue
        if cond.get("merchant") and cond["merchant"] not in (r["merchant_name"] or ""):
            continue
        if cond.get("min") is not None and net < cond["min"]:
            continue
        if cond.get("no_fixed") and r["funding"] == "fixed":
            continue
        out.append({"day": day, "time": r["occurred_time"][:5], "net": net, "slot": slot, "fixed": r["funding"] == "fixed",
                    "merchant": " ".join((r["merchant_name"] or "").split()), "category": r["category"]})
    return out


def state(design, persona, span):
    """기간 수집 상태 (coverage_plan 성공 기록만)"""
    days = {span[0] + timedelta(n) for n in range((span[1] - span[0]).days + 1)}
    assets = {c["asset"] for c in design["coverage_plan"] if c["persona"] == persona}
    covered = {a: set() for a in assets}
    for c in design["coverage_plan"]:
        if c["persona"] == persona and c["status"] == "success":
            a, b = date.fromisoformat(c["covered_from"]), date.fromisoformat(c["covered_to"])
            covered[c["asset"]] |= {a + timedelta(n) for n in range((b - a).days + 1)} & days
    if not any(covered.values()):
        return "none"
    return "complete" if all(days <= v for v in covered.values()) else "partial"


def biggest(rows):
    r = min((r for r in rows if r["net"] > 0), key=lambda r: (-r["net"], r["day"], r["time"], r["merchant"]))
    return [r["day"].isoformat(), r["time"], r["merchant"], r["net"]]


def total(rows):
    return sum(r["net"] for r in rows)


def count(rows):
    return sum(r["net"] > 0 for r in rows)


def groups(rows, key, by_count=False):
    acc = {}
    for r in rows:
        g = acc.setdefault(r[key], {"name": r[key], "sum": 0, "count": 0})
        g["sum"] += r["net"]
        g["count"] += r["net"] > 0
    order = (lambda g: (-g["count"], -g["sum"], g["name"])) if by_count else (lambda g: (-g["sum"], -g["count"], g["name"]))
    return sorted(acc.values(), key=order)


def build(design):
    a = lambda span=THIS, **c: spending(design, "A", span, **c)  # noqa: E731
    days = (THIS[1] - THIS[0]).days + 1
    paid_food = [r for r in a(category="FOOD") if r["net"] > 0]
    weekday = {g["name"]: g for g in groups([{**r, "wd": r["day"].weekday()} for r in a(no_fixed=True)], "wd")}
    slot = {g["name"]: g for g in groups(a(no_fixed=True), "slot")}
    a_living = [r for r in a() if not r["fixed"]]
    out = {"A": {
        "as_of": "2026-10-29", "this_month_days": days, "rule": "2026-11-02.1",
        "fixed_this_month": total([r for r in a() if r["fixed"]]),
        "주말 식비 얼마 썼어?": {"sum": total(a(category="FOOD", weekdays=(5, 6), no_fixed=True))},
        "자주 간 가게 3곳 알려줘": {"groups": [[g["name"], g["count"], g["sum"]]
                                       for g in groups(a(no_fixed=True), "merchant", by_count=True)[:3]]},
        "밤에 쓴 돈 얼마야?": {"sum": total(a(slots=("night",), no_fixed=True))},
        "야식으로 얼마 썼어?": {"sum": total(a(slots=("night", "dawn"), no_fixed=True))},
        # [판 2026-11-02.1] 달보다 짧은 기간(이번 주 · 지난주 · 날짜 범위) 총액은 고정지출을 빼고, 뺀 금액 · 이름을 따로
        "이번 주 얼마 썼어?": {"sum": total(a((date(2026, 10, 26), THIS[1]), no_fixed=True)),
                          "fixed": total([r for r in a((date(2026, 10, 26), THIS[1])) if r["fixed"]]),
                          "fixed_names": sorted({r["merchant"] for r in a((date(2026, 10, 26), THIS[1])) if r["fixed"]})},
        "지난주 식비 얼마야?": {"sum": total(a((date(2026, 10, 19), date(2026, 10, 25)), category="FOOD", no_fixed=True))},
        "8월 식비 얼마 썼어?": {"sum": total(a((date(2026, 8, 1), date(2026, 8, 31)), category="FOOD"))},
        "10월 3일부터 10일까지 얼마 썼어?": {"sum": total(a((date(2026, 10, 3), date(2026, 10, 10)), no_fixed=True))},
        "이번 달 제일 큰 지출 뭐야?": {"max": biggest(a()), "max_living": biggest(a_living)},
        "하루 평균 얼마 썼어?": {"daily_avg": half_up(total(a()), days), "daily_avg_living": half_up(total(a_living), days),
                          "days": days},
        "과자서원 몇 번 갔어?": {"count": count(a(merchant="과자서원")), "sum": total(a(merchant="과자서원"))},
        "지난달 식비 얼마 썼어?": {"sum": total(a(LAST, category="FOOD"))},
        "계좌이체로 쓴 돈 얼마야?": {"sum": total(a(source="bank"))},
        "만 원 넘게 쓴 거 몇 번이야?": {"count": count(a(min=10_000))},
        "식비 한 번에 평균 얼마 써?": {"avg": half_up(sum(r["net"] for r in paid_food), len(paid_food))},
        "요일별 소비 보여줘": {"groups": [[f"{'월화수목금토일'[d]}요일", weekday[d]["sum"], weekday[d]["count"]]
                                    for d in range(7) if d in weekday]},
        "시간대별로 보여줘": {"groups": [[name, slot[code]["sum"], slot[code]["count"]] for code, name in
                                   (("dawn", "새벽"), ("morning", "아침"), ("lunch", "점심"), ("afternoon", "오후"),
                                    ("evening", "저녁"), ("night", "밤")) if code in slot]},
    }}
    b = lambda span=THIS, **c: spending(design, "B", span, **c)  # noqa: E731
    fashion = b(category="FASHION_SHOPPING")
    paid = [r for r in fashion if r["net"] > 0]
    b_living = [r for r in b() if not r["fixed"]]
    out["B"] = {  # 같은 구매(계좌 쪽 제외) · 부분 취소(순금액) · 전액 취소(건수 0) 가 들어간 패션 · 쇼핑
        "state": state(design, "B", THIS),
        "패션 쇼핑 몇 건 결제했어?": {"count": count(fashion), "sum": total(fashion)},
        "패션 쇼핑 한 번에 평균 얼마야?": {"avg": half_up(sum(r["net"] for r in paid), len(paid))},
        "이번 달 제일 큰 지출 뭐야?": {"max": biggest(b()), "max_living": biggest(b_living)},
        "가게별 상위 3곳 보여줘": {"groups": [[g["name"], g["sum"], g["count"]] for g in groups(b(no_fixed=True), "merchant")[:3]]},
        "요일별로 몇 번 결제했어?": {"groups": [[f"{'월화수목금토일'[g['name']]}요일", g["count"], g["sum"]] for g in
                                       sorted(groups([{**r, "wd": r["day"].weekday()} for r in b(no_fixed=True)], "wd"),
                                              key=lambda g: g["name"])]},
        "하루 평균 얼마 썼어?": {"daily_avg": half_up(total(b()), days), "daily_avg_living": half_up(total(b_living), days)},
    }
    c = lambda span=THIS, **k: spending(design, "C", span, **k)  # noqa: E731
    out["C"] = {  # 이번 달 = 일부 수집(10/20~ 만 성공 기록), 지난달 = 자료 없음, 확인 필요(UNCLASSIFIED) 결제 포함
        "state": state(design, "C", THIS), "last_month_state": state(design, "C", LAST),
        "이번 달 결제 몇 건이야?": {"count": count(c()), "sum": total(c())},
        "카테고리별로 몇 번 썼어?": {"groups": [[g["name"], g["count"], g["sum"]]
                                         for g in groups(c(no_fixed=True), "category", by_count=True)]},
        "가게별 상위 3곳 보여줘": {"groups": [[g["name"], g["sum"], g["count"]] for g in groups(c(no_fixed=True), "merchant")[:3]]},
        "이번 달 제일 큰 지출 뭐야?": {"max": biggest(c())},
        "지난달 식비 얼마 썼어?": {"sum": total(c(LAST, category="FOOD")), "rows": len(c(LAST, category="FOOD"))},
    }
    return out


if __name__ == "__main__":
    data = build(json.loads((HERE / "design.json").read_text(encoding="utf-8")))
    (HERE / "expected_query.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False)[:1500])

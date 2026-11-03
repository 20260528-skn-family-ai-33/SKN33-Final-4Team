"""소비 분석 내부 결과 형식 — 순수 계산 (DB · LLM 없음). 금액은 원 정수, 비율은 Decimal
최종 표시 문구 · 반올림 표기 방식은 다음 단계(답변 템플릿) 몫이다. 여기는 내부 수치 계약만.
"""
from decimal import Decimal
from importlib import import_module

from finance.spending import spending_summary

# 기존 카테고리 순서(DB 시드 display_order 와 같은 순서). 동률 1위 정렬에 쓴다
CATEGORY_ORDER = [code for code, *_ in import_module("budgets.migrations.0003_seed_categories").CATEGORIES]
UNCLASSIFIED = "UNCLASSIFIED"


def percent(part, whole):
    """part / whole × 100 → {"value": 소수 둘째 자리 HALF_UP Decimal, "exact": {numerator, denominator}}. whole 이 0 이면 None
    반올림은 정수 산술로 한 번만 한다 (Decimal 나눗셈의 유효자리 반올림을 거치지 않게). 원금 exact 는 반올림하지 않는다"""
    if whole == 0:
        return None
    n = part * 10_000
    q, r = divmod(abs(n), abs(whole))
    q += 2 * r >= abs(whole)  # HALF_UP: 0.5 이상은 0 에서 먼 쪽으로
    sign = -1 if (n < 0) != (whole < 0) else 1
    return {"value": Decimal(sign * q).scaleb(-2), "exact": {"numerator": part * 100, "denominator": whole}}


def average(total, count):
    """원 정수 평균 total / count → {"value": 원 단위 HALF_UP 정수, "exact": {numerator, denominator}}. count 0 이면 None
    percent 와 같은 정수 산술 반올림 (0.5 원 이상은 0 에서 먼 쪽으로)"""
    if count == 0:
        return None
    q, r = divmod(abs(total), count)
    q += 2 * r >= count
    return {"value": q if total >= 0 else -q, "exact": {"numerator": total, "denominator": count}}


def trend_stats(series):
    """최근 N개월 series → 완전 수집 월만으로 월평균 · 바로 이웃한 달끼리 증감. 불완전 달은 0원으로 넣지 않고 excluded 로 남긴다
    - average: 비교 가능한 달이 2개 이상일 때만 (1개면 평균 · 추세라고 하지 않는다)
    - changes: 이웃한 두 달이 모두 완전할 때만 (사이에 빠진 달이 있으면 건너뛰어 비교하지 않는다). 이전 0원 처리는 compare 그대로
    진행 중인 이번 달은 series 에 없다 (recent_months 가 완료된 달만 준다)"""
    used = [s for s in series if s["confirmed"] is not None]
    changes = [{"previous": a["period"], "current": b["period"], **compare(b["confirmed"], a["confirmed"], True, True)}
               for a, b in zip(series, series[1:]) if a["confirmed"] is not None and b["confirmed"] is not None]
    return {"used": [s["period"] for s in used], "excluded": [{"period": s["period"], "state": s["state"]}
                                                              for s in series if s["confirmed"] is None],
            "average": average(sum(s["confirmed"] for s in used), len(used)) if len(used) >= 2 else None,
            "changes": changes}


def figures(items):
    """transaction_item 목록 → 05 독립 기대값과 같은 칸 이름의 합계. 합산은 spending_summary 가 한다"""
    total, living = spending_summary(items), spending_summary(items, living_only=True)
    by_category = {k: v for k, v in total["by_category"].items() if v}
    return {"total": total["spent_total"], "fixed_amount": total["spent_total"] - living["spent_total"],
            "living_amount": living["spent_total"], "by_category": by_category,
            "unclassified_amount": by_category.get(UNCLASSIFIED, 0), "needs_review_count": total["needs_review_count"]}


def category_view(fig, code):
    """선택 카테고리 금액. total · fixed_amount 는 fig 그대로(전체 소비 기준, chat.md), 비율 분모도 전체 소비"""
    amount = fig["by_category"].get(code, 0)
    return {"code": code, "amount": amount, "share": percent(amount, fig["total"])}


def top_spending(by_category):
    """가장 많이 쓴 카테고리. UNCLASSIFIED 제외, 동률이면 모두(카테고리 순서). 분류된 소비가 없으면 None"""
    classified = {k: v for k, v in by_category.items() if k != UNCLASSIFIED and v > 0}
    if not classified:
        return None
    top = max(classified.values())
    return [{"category": c, "amount": top} for c in CATEGORY_ORDER if classified.get(c) == top]


def shares(fig):
    """카테고리별 비율. 분모는 미분류를 포함한 전체 소비. 전체 0원이면 빈 dict"""
    return {c: percent(fig["by_category"][c], fig["total"]) for c in CATEGORY_ORDER if c in fig["by_category"]}


def compare(current, previous, current_complete, previous_complete):
    """두 기간 모두 complete 일 때만 확정 증감. 이전 0원이면 증감액은 내고 증감률은 None (chat.md prevDiffRate)
    previous_incomplete = 기존 API reason 'previous_incomplete' 와 같은 뜻. current_incomplete 는 내부 구분(API 코드 없음)"""
    available = current_complete and previous_complete
    delta = current - previous if available else None
    return {"available": available, "previous_incomplete": not previous_complete,
            "current_incomplete": not current_complete,
            "delta": delta, "rate": percent(delta, previous) if available else None}


def budget_numbers(allocated, spent, spare=None):
    """완전한 자료의 예산 비교. 부호 있는 차이 보존, remaining = max(차이, 0), over = max(−차이, 0)
    spare(여윳돈)를 주면 budgets.overview 와 같은 식으로 여윳돈 충당을 더한다 (생활비 전체만)"""
    difference = allocated - spent
    out = {"allocated": allocated, "spent": spent, "difference": difference,
           "remaining": max(difference, 0), "over": max(-difference, 0)}
    if spare is not None:
        covered = min(out["over"], spare)
        out.update(covered_by_spare=covered, spare_remaining=spare - covered, shortfall=out["over"] - covered)
    return out

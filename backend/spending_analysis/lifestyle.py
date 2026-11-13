"""생활 패턴의 근거: 확인된 업종과 정확한 플랫폼 표기만 사용한다."""
from finance.merchant_rules import store_type

from . import scope

INDUSTRIES = {
    "dining": ("FOOD", {"한식", "한식당", "일반한식", "한식음식점", "한식음식점업", "일반음식점", "음식점", "일식", "중식", "양식", "분식", "패스트푸드"}),
    "delivery": ("FOOD", {"배달앱", "배달음식"}),
    "groceries": ("FOOD", {"식료품점", "식료품 소매업", "청과물", "정육점", "채소/과일 소매업"}),
    "cafe": ("FOOD", {"카페", "커피전문점", "커피숍", "제과점", "베이커리"}),
    "cinema": ("CULTURE_LEISURE", {"영화관", "영화상영관", "영화상영업"}),
    "performance": ("CULTURE_LEISURE", {"공연장", "공연예매", "공연시설 운영업"}),
    "exercise": ("CULTURE_LEISURE", {"헬스장", "헬스클럽", "체력단련시설", "요가/필라테스"}),
}
LABELS = {"dining": "외식", "delivery": "배달", "groceries": "장보기", "cafe": "카페·간식",
          "cinema": "영화", "performance": "공연", "exercise": "운동시설", "unknown": "잘 모르겠어요"}


def identify(category, industry, merchant):
    name = scope.plain(merchant)
    if category == "FOOD" and name and any(name == scope.plain(word)
            for names in scope.ONLINE["배달앱"]["brands"].values() for word in names):
        return "delivery"
    industry = store_type(industry)
    return next((kind for kind, (code, names) in INDUSTRIES.items()
                 if category == code and industry in names), None)


def summarize(sides, metrics):
    paid = [[r for r in rows if r["net_amount"] > 0 and r["funding"] != "fixed"] for rows in sides]
    coverage = []
    for rows in paid:
        total = sum(r["net_amount"] for r in rows)
        known = sum(r["net_amount"] for r in rows if r.get("activity"))
        coverage.append(100 if not total else known * 100 // total)
    groups = {}
    for kind in (*INDUSTRIES, "lunch_dining"):
        subsets = [[r for r in rows if r.get("activity") == ("dining" if kind == "lunch_dining" else kind)
                    and (kind != "lunch_dining" or scope.slot_of(r["occurred_time"]) == "lunch")] for rows in paid]
        if any(subsets):
            groups[kind] = [{**metrics(rows), "days": len({r["occurred_on"] for r in rows})} for rows in subsets]
    return {"known_amount_percent": coverage, "types": groups}


def options(question):
    if question["kind"] == "activity":
        kinds = ("dining", "delivery", "groceries", "cafe", "unknown") if question["category"] == "FOOD" else ("cinema", "performance", "exercise", "unknown")
        return [{"value": f"analysis_{kind}", "label": LABELS[kind] if kind == "unknown" else f"{LABELS[kind]} 위주였어요",
                 "set": {"analysis_activity": kind}} for kind in kinds]
    return [{"value": "analysis_planned", "label": "계획한 지출이었어요", "set": {"analysis_planned": True}},
            {"value": "analysis_unplanned", "label": "예상 밖의 지출이었어요", "set": {"analysis_planned": False}},
            {"value": "analysis_cancel", "label": "추천은 괜찮아요", "set": {"followup": "cancel"}}]

from django.shortcuts import render

from . import mock

PENDING = "확인 필요"


def transactions(request):
    """거래 내역 (하단 탭 '거래'). ?category=식비 로 카테고리만 골라 보기"""
    selected = request.GET.get("category", "전체")

    def keep(tx):
        if selected == "전체":
            return True
        if selected == PENDING:
            return tx["kind"] == "pending"
        return tx.get("cat") == selected

    days = []
    for day in mock.TX_DAYS:
        items = [tx for tx in day["items"] if keep(tx)]
        if items:
            days.append({"date": day["date"], "items": items, "total": sum(tx["amount"] for tx in items)})

    return render(request, "finance/txs.html", {
        "month": mock.MONTH,
        "recent": mock.RECENT,
        "pending_count": mock.PENDING_COUNT,
        "categories": mock.CATEGORIES,
        "selected": selected,
        "pending_label": PENDING,
        "days": days,
    })

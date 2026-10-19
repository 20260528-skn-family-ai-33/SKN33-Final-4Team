from django.http import Http404
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


def _find_tx(tx_id):
    """mock 거래 목록에서 id로 거래와 날짜를 찾는다"""
    for day in mock.TX_DAYS:
        for tx in day["items"]:
            if tx["id"] == tx_id:
                return tx, day["date"]
    raise Http404("거래를 찾을 수 없어요")


def transaction_popup(request, tx_id):
    """팝업: 거래 상세"""
    tx, date = _find_tx(tx_id)
    return render(request, "finance/_transaction.html", {"tx": tx, "date": date, "categories": mock.CATEGORIES})


def tx_pick_popup(request, tx_id):
    """팝업: 어떤 카테고리인가요?"""
    tx, _ = _find_tx(tx_id)
    return render(request, "finance/_tx_pick.html", {"tx": tx, "categories": mock.CATEGORIES})

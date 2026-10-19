"""
finance 앱 API: 거래 내역을 JSON으로 준다. ?category=식비 로 골라 보기
"""
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import mock

PENDING = "확인 필요"  # AI가 카테고리를 확신 못 한 거래 묶음 이름


def filter_days(selected):
    """고른 카테고리의 거래만 남기고, 날짜별 합계를 다시 계산한다"""
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
    return days


@api_view(["GET"])
def transactions(request):
    """GET /api/finance/transactions/?category=식비 — 거래 내역 데이터"""
    selected = request.query_params.get("category", "전체")
    return Response({
        "month": mock.MONTH,
        "recent": mock.RECENT,
        "pending_count": mock.PENDING_COUNT,
        "pending_label": PENDING,
        "categories": mock.CATEGORIES,
        "selected": selected,
        "days": filter_days(selected),
    })

"""
finance 앱 API: 거래 내역을 JSON으로 준다. ?category=식비 로 골라 보기
"""
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import mock

PENDING = "확인 필요"  # AI가 카테고리를 확신 못 한 거래 묶음 이름
NOT_SPENDING = ("income", "transfer")  # 월급 입금 · 남에게 보낸 이체 — 목록엔 보이지만 소비 합계엔 안 들어감 (V①)


def net_amount(tx):
    """취소를 뺀 실제 소비 금액 (전체 취소 = 0, 부분취소 = 금액 - 취소 금액). 입금 · 이체는 원래 금액"""
    if tx.get("cancel") == "full":
        return 0
    return tx["amount"] - tx.get("cancel_amount", 0)


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
        items = [{**tx, "net": net_amount(tx)} for tx in day["items"] if keep(tx)]
        if items:
            # 날짜 합계는 소비만 (입금 · 이체 제외)
            total = sum(tx["net"] for tx in items if tx["kind"] not in NOT_SPENDING)
            days.append({"date": day["date"], "items": items, "total": total})
    return days


@api_view(["GET"])
def transactions(request):
    """16. GET /api/finance/transactions/?category=식비 — 거래 내역 데이터
    2026-10-24 껍데기: 명세(transactions.md) 예시 칸 period · summary · items 를 더했다(혜리).
    month · recent · pending_count · pending_label · categories · selected · days 는 지금 거래 화면(TransactionsPage.jsx)이
    쓰는 옛 칸이라 남겨 둔다 → 화면을 명세 모양으로 바꾸는 PR에서 함께 지운다(transactions.md '정해진 것 10/21')"""
    from .api_shell import example  # 명세 예시 응답

    selected = request.query_params.get("category", "전체")
    return Response({
        **example("transactions_list"),
        "month": mock.MONTH,
        "recent": mock.RECENT,
        "pending_count": mock.PENDING_COUNT,
        "pending_label": PENDING,
        "categories": mock.CATEGORIES,
        "selected": selected,
        "days": filter_days(selected),
    })

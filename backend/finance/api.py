"""
finance 앱 API: 거래 내역을 JSON으로 준다. ?category=식비 로 골라 보기 (화면과 같은 규칙)
"""
from rest_framework.decorators import api_view
from rest_framework.response import Response

from config.api import camel

from . import mock
from .views import PENDING, filter_days


@api_view(["GET"])
def transactions(request):
    """GET /api/finance/transactions/?category=식비 — 거래 내역 데이터"""
    selected = request.query_params.get("category", "전체")
    return Response(camel({
        "month": mock.MONTH,
        "recent": mock.RECENT,
        "pending_count": mock.PENDING_COUNT,
        "pending_label": PENDING,
        "categories": mock.CATEGORIES,
        "selected": selected,
        "days": filter_days(selected),
    }))

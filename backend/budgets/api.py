"""
budgets 앱 API: React 화면에 필요한 데이터를 JSON으로 준다.
지금은 mock.py(가짜 데이터)를 그대로 보낸다. DB가 생기면 이 파일 안쪽만 바꾸면 된다.
"""
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import mock


def _must_pay():
    """'꼭 나갈 돈' — link 는 누르면 갈 React 화면 주소 (없으면 null = 카드 청구서 팝업)"""
    return [{**p, "link": p.get("link")} for p in mock.MUST_PAY]


@api_view(["GET"])
def home(request):
    """GET /api/home/ — 홈 화면 데이터"""
    return Response({
        "period": mock.PERIOD,
        "home": mock.HOME,
        "must_pay": _must_pay(),
        # H-06: 1차는 항상 null(예산 API 는 ML 후). 시안은 mock.BUDGET 이 None 일 때만 null 을 보내 안내 카드를 보여 준다
        "budget": None if mock.BUDGET is None else {"sample": True},
    })


@api_view(["GET"])
def budget(request):
    """GET /api/budgets/ — 카테고리 예산 화면 데이터"""
    return Response({
        "period": mock.PERIOD,
        "monthly": mock.MONTHLY,
        "month_split": mock.MONTH_SPLIT,
        "categories": mock.CATEGORIES,
        "living_used": mock.HOME["living_used"],
        "living_budget": mock.HOME["living_budget"],
        "unclassified": mock.UNCLASSIFIED,
    })


@api_view(["GET"])
def cushion(request):
    """GET /api/budgets/cushion/ — 머니 쿠션 화면 데이터"""
    return Response({"cushion": mock.CUSHION})

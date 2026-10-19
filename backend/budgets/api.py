"""
budgets 앱 API: 화면(views.py)과 같은 데이터를 HTML 대신 JSON으로 준다.
지금은 mock.py(가짜 데이터)를 그대로 보낸다. DB가 생기면 이 파일 안쪽만 바꾸면 된다.
"""
from django.urls import reverse
from rest_framework.decorators import api_view
from rest_framework.response import Response

from config.api import camel

from . import mock


def _must_pay():
    """'꼭 나갈 돈' — link 에 적힌 주소 이름(budgets:fixed)을 진짜 주소(/budgets/fixed/)로 바꿔서 보낸다"""
    return [{**p, "link": reverse(p["link"]) if p.get("link") else None} for p in mock.MUST_PAY]


@api_view(["GET"])
def home(request):
    """GET /api/home/ — 홈 화면 데이터"""
    return Response(camel({
        "period": mock.PERIOD,
        "home": mock.HOME,
        "must_pay": _must_pay(),
    }))


@api_view(["GET"])
def budget(request):
    """GET /api/budgets/ — 카테고리 예산 화면 데이터"""
    return Response(camel({
        "period": mock.PERIOD,
        "monthly": mock.MONTHLY,
        "month_split": mock.MONTH_SPLIT,
        "categories": mock.CATEGORIES,
        "living_used": mock.HOME["living_used"],
        "living_budget": mock.HOME["living_budget"],
        "unclassified": mock.UNCLASSIFIED,
    }))


@api_view(["GET"])
def cushion(request):
    """GET /api/budgets/cushion/ — 머니 쿠션 화면 데이터"""
    return Response(camel({"cushion": mock.CUSHION}))

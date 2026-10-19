from django.shortcuts import render

from . import mock


def home(request):
    """홈 (하단 탭 가운데)"""
    return render(request, "budgets/home.html", {
        "period": mock.PERIOD,
        "h": mock.HOME,
        "must_pay": mock.MUST_PAY,
    })


def budget(request):
    """카테고리 예산 (하단 탭 '예산')"""
    return render(request, "budgets/budget.html", {
        "period": mock.PERIOD,
        "monthly": mock.MONTHLY,
        "split": mock.MONTH_SPLIT,
        "categories": mock.CATEGORIES,
        "living_used": mock.HOME["living_used"],
        "living_budget": mock.HOME["living_budget"],
        "unclassified": mock.UNCLASSIFIED,
    })


def fund(request):
    """머니 쿠션 (하단 탭 '쿠션')"""
    return render(request, "budgets/fund.html", {"c": mock.CUSHION})

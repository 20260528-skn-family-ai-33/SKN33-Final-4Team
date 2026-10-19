"""
API 주소 목록 (리액트가 데이터를 받아 가는 곳)
- 모든 주소 앞에 /api/ 가 붙는다. 예: /api/home/
- 화면(HTML) 주소와 똑같은 모양에 /api 만 앞에 붙였다.
- 브라우저로 열면 DRF가 보기 좋은 화면으로 JSON을 보여 준다. 규칙은 docs/API_RULES.md
"""
from django.urls import path

from budgets import api as budgets_api
from finance import api as finance_api

from . import api_common

app_name = "api"

urlpatterns = [
    # 공통
    path("csrf/", api_common.csrf, name="csrf"),
    # 화면 데이터
    path("home/", budgets_api.home, name="home"),
    path("budgets/", budgets_api.budget, name="budget"),
    path("budgets/cushion/", budgets_api.cushion, name="cushion"),
    path("finance/transactions/", finance_api.transactions, name="transactions"),
]

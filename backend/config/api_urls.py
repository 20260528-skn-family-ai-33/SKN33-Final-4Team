"""
API 주소 목록 (리액트가 데이터를 받아 가는 곳)
- 모든 주소 앞에 /api/ 가 붙는다. 예: /api/home/
- 화면(HTML) 주소와 똑같은 모양에 /api 만 앞에 붙였다.
- 브라우저로 열면 DRF가 보기 좋은 화면으로 JSON을 보여 준다. 규칙은 docs/API_RULES.md
- 주소 속 ID 자리는 <uuid:이름> 으로 쓴다. 예: path("budgets/fixed/<uuid:recurring_id>/", ...)
  → abc 처럼 형식이 틀리면 아래 맨 끝 줄로 가서 JSON 404 (10-21 C-11)
- 맨 끝 줄(not_found)은 항상 마지막에 둔다. 새 주소는 그 위에 추가한다
"""
from django.urls import path, re_path

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
    # ↑ 새 주소는 이 위에 추가
    # 맞는 주소가 없으면 JSON 404 (항상 맨 끝)
    re_path(r"^.*$", api_common.not_found, name="not_found"),
]

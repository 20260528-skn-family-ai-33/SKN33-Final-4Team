"""
머니쿠션 주소 목록 (안내 데스크)
- 각 앱의 urls.py 를 여기에 연결한다.
"""
from django.contrib import admin
from django.urls import include, path

from budgets import views as budget_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", budget_views.home, name="home"),
    path("", include("users.urls")),
    path("budgets/", include("budgets.urls")),
    path("finance/", include("finance.urls")),
    path("chat/", include("chat.urls")),
    # API: 리액트가 데이터(JSON)를 받아 가는 주소. 목록은 config/api_urls.py
    path("api/", include("config.api_urls")),
]

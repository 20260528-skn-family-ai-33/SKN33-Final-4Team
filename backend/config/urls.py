"""
머니쿠션 주소 목록 (안내 데스크)
- 화면은 React(frontend/)가 그린다. 장고는 데이터만 준다.
- /api/...  : React 가 데이터를 받아 가는 곳. 목록은 config/api_urls.py, 규칙은 docs/API_RULES.md
- /admin/   : 장고 관리자 화면 (DB 확인용)
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("config.api_urls")),
]

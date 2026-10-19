from django.urls import path

from config.screens import screen

from . import views

app_name = "budgets"

urlpatterns = [
    path("", views.budget, name="budget"),
    path("cushion/", views.fund, name="fund"),
    # 첫 예산 설정 흐름: 월급 → 고정지출 → 방향 → 지킬 돈 → 추천 예산
    path("salary/", screen("budgets/salary.html", next_url="budgets:fixed"), name="salary"),
    path("fixed/", screen("budgets/fixed.html"), name="fixed"),
    path("direction/", screen("budgets/direction.html"), name="direction"),
    path("protect/", screen("budgets/protect.html", next_url="budgets:recommend"), name="protect"),
    path("recommend/", screen("budgets/recommend.html", next_url="home"), name="recommend"),
]

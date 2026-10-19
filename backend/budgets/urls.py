from django.urls import path

from . import views

app_name = "budgets"

urlpatterns = [
    path("", views.budget, name="budget"),
    path("cushion/", views.fund, name="fund"),
]

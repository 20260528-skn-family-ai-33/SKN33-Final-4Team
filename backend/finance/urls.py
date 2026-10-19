from django.urls import path

from . import views

app_name = "finance"

urlpatterns = [
    path("transactions/", views.transactions, name="txs"),
]

from django.urls import path

from config.screens import screen

from . import views

app_name = "finance"

urlpatterns = [
    path("transactions/", views.transactions, name="txs"),
    # 계좌 연결 흐름: 은행 선택 → 기관 인증 → 계좌 선택 → 카드 선택 → 연결 결과 → 대출 확인
    path("connect/", screen("finance/connect.html"), name="connect"),
    path("connect/auth/", screen("finance/connect_auth.html"), name="connect_auth"),
    path("connect/accounts/", screen("finance/connect_accounts.html"), name="connect_accounts"),
    path("connect/cards/", screen("finance/connect_cards.html"), name="connect_cards"),
    path("connect/done/", screen("finance/connect_done.html"), name="connect_done"),
    path("loans/", screen("finance/loan_check.html"), name="loan_check"),
    path("connections/", screen("finance/connections.html"), name="connections"),
]

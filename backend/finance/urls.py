from django.urls import path

from config.screens import popup, screen

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
    # 팝업
    path("popup/transactions/<int:tx_id>/", views.transaction_popup, name="pop_transaction"),
    path("popup/transactions/<int:tx_id>/category/", views.tx_pick_popup, name="pop_tx_pick"),
    path("popup/filter/", popup("finance/_filter.html"), name="pop_filter"),
    path("popup/accounts/sh1/", popup("finance/_acct_sh1.html"), name="pop_acct_sh1"),
    path("popup/accounts/kb1/", popup("finance/_acct_kb1.html"), name="pop_acct_kb1"),
    path("popup/accounts/card/", popup("finance/_acct_card.html"), name="pop_acct_card"),
]

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
from chat import api as chat_api
from finance import api as finance_api
from finance import api_connect as finance_connect
from finance import api_loans as finance_loans
from finance import api_cards as finance_cards

from users import api as users_api

from . import api_common, api_docs

app_name = "api"

urlpatterns = [
    # Swagger: 확정 계약 조회와 현재 서버 GET 확인을 분리한다.
    path("schema/", api_docs.contract_schema, name="schema"),
    path("schema/runtime/", api_docs.runtime_schema, name="schema-runtime"),
    path("docs/", api_docs.MoneyCushionSwaggerView.as_view(url_name="api:schema"), name="swagger-ui"),
    path(
        "docs/runtime/",
        api_docs.MoneyCushionSwaggerView.as_view(url_name="api:schema-runtime", runtime=True),
        name="swagger-ui-runtime",
    ),
    # 공통
    path("csrf/", api_common.csrf, name="csrf"),

    # 인증
    path("auth/signup/", users_api.signup, name="signup"),
    path("auth/login/", users_api.login, name="login"),
    path("auth/logout/", users_api.logout, name="logout"),
    path("auth/me/", users_api.me, name="me"),

    # 화면 데이터
    path("home/", budgets_api.home, name="home"),
    path("budgets/", budgets_api.budget, name="budget"),
    path("budgets/cushion/", budgets_api.cushion, name="cushion"),
    path("finance/transactions/", finance_api.transactions, name="transactions"),

    # ---- 나은: 고정지출 19~24 · 정기 저축 25~30 (구현) ----
    path("budgets/fixed/", budgets_api.fixed_list, name="fixed-list"),
    path("budgets/fixed/confirm/", budgets_api.fixed_confirm, name="fixed-confirm"),
    path("budgets/fixed/ignore/", budgets_api.fixed_ignore, name="fixed-ignore"),
    path("budgets/fixed/<uuid:recurring_id>/", budgets_api.fixed_detail, name="fixed-detail"),
    path("budgets/savings/", budgets_api.savings_list, name="savings-list"),
    path("budgets/savings/confirm/", budgets_api.savings_confirm, name="savings-confirm"),
    path("budgets/savings/ignore/", budgets_api.savings_ignore, name="savings-ignore"),
    path("budgets/savings/<uuid:saving_id>/", budgets_api.savings_detail, name="savings-detail"),

    # ----- 혜리: 금융 동의 · 연결 6~10 · 거래 17 · 18 · 챗봇 32~34 (실제 구현: 6~10 · 17 · 18 · 32 · 33 / 껍데기: 34) -----
    path("finance/consents/", finance_connect.consents, name="finance-consents"),  # 6 POST
    path("finance/connect/accounts/", finance_connect.connect_accounts, name="finance-connect-accounts"),  # 7 GET · 8 PUT
    path("finance/connections/", finance_connect.connections, name="finance-connections"),  # 9 GET
    path("finance/connections/<uuid:connection_id>/", finance_connect.connection_disconnect, name="finance-connection"),  # 10 DELETE
    path("finance/transactions/<uuid:transaction_id>/", finance_api.transaction_detail, name="transaction-detail"),  # 17 GET · 18 PATCH
    path("chat/conversations/<uuid:conversation_id>/", chat_api.conversation, name="chat-conversation"),  # 32 GET
    path("chat/messages/", chat_api.messages, name="chat-messages"),  # 33 POST
    path("tasks/<uuid:task_id>/", chat_api.task, name="task"),  # 34 GET (껍데기)
    # ----- 혜리 끝 -----

    path("finance/loans/", finance_loans.loans, name="loan-list"),
    path("finance/loans/<uuid:loan_id>/", finance_loans.loan_detail, name="loan-detail",),
    path("finance/connect/cards/", finance_cards.connect_cards, name="card-candidates"),
    path("finance/card-bills/<uuid:bill_id>/", finance_cards.card_bill, name="card-bill"),
    # ↑ 새 주소는 이 위에 추가
    # 맞는 주소가 없으면 JSON 404 (항상 맨 끝)
    re_path(r"^.*$", api_common.not_found, name="not_found"),
]

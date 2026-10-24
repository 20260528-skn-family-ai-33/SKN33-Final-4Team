"""금융 6~10 · 거래 17~18 목업 (2026-10-24).

finance-connect.md · transactions.md의 응답 모양으로 만든 고정 샘플이다.
DB 저장, 입력 검증, 소유권 확인, 멱등성은 구현하지 않는다.
실제 기능 구현 시 api.py의 목업 뷰와 함께 교체한다.
"""

ACCOUNT_ID = "00000000-0000-4000-8000-000000000001"
TRANSACTION_ID = "00000000-0000-4000-8000-000000000002"
CONNECTION_ID = "00000000-0000-4000-8000-000000000005"
CARD_ID = "00000000-0000-4000-8000-000000000006"
EXAMPLE_TIME = "2026-10-24T10:00:00+09:00"

CONSENTS_EXAMPLE = {
    "items": [
        {
            "consentId": "00000000-0000-4000-8000-000000000007",
            "purpose": "finance_inquiry",
            "productScope": "bank",
            "documentVersion": "v1",
            "grantedAt": EXAMPLE_TIME,
        },
        {
            "consentId": "00000000-0000-4000-8000-000000000008",
            "purpose": "finance_inquiry",
            "productScope": "card",
            "documentVersion": "v1",
            "grantedAt": EXAMPLE_TIME,
        },
    ]
}

ACCOUNTS_EXAMPLE = {
    "items": [{
        "accountId": ACCOUNT_ID,
        "connectionId": CONNECTION_ID,
        "displayName": "국민 급여통장 · ****5678",
        "organizationName": "국민은행",
        "maskedNumber": "****5678",
        "name": "급여통장",
        "status": "connected",
        "balance": 1240000,
        "isSelected": True,
        "fetchedAt": EXAMPLE_TIME,
    }]
}

CONNECTIONS_EXAMPLE = {
    "items": [{
        "connectionId": CONNECTION_ID,
        "businessType": "bank",
        "organizationName": "국민은행",
        "alias": None,
        "status": "connected",
        "connectedAt": EXAMPLE_TIME,
        "lastAuthenticatedAt": EXAMPLE_TIME,
        "accountCount": 1,
        "cardCount": 0,
        "accountIds": [ACCOUNT_ID],
        "cardIds": [],
    }]
}

# 기존 mock.TX_DAYS의 김밥천국 거래를 상세 응답으로 표현한 별도 샘플.
# 16번 기존 화면용 목록의 정수 id 대신 위 TRANSACTION_ID로 상세를 확인한다.
TRANSACTION_EXAMPLE = {
    "transactionId": TRANSACTION_ID,
    "occurredOn": "2026-10-11",
    "occurredTime": "12:10",
    "merchantName": "김밥천국 역삼점",
    "amount": 18000,
    "cancel": {"state": "none", "amount": 0},
    "netAmount": 18000,
    "category": "FOOD",
    "categoryName": "식비",
    "classifyState": "suggested",
    "paymentCard": {
        "cardId": CARD_ID,
        "displayName": "신한 Deep Dream · ****1234",
        "status": "connected",
    },
    "paymentAccount": None,
    "funding": "living",
    "fixedExpenseId": None,
    "direction": "out",
    "transactionType": "spending",
    "installmentMonths": 0,
    "suggestion": None,
    "classifiedBy": "rule",
    "updatedAt": EXAMPLE_TIME,
}

TRANSACTION_UPDATED_EXAMPLE = {
    **TRANSACTION_EXAMPLE,
    "classifyState": "confirmed",
    "classifiedBy": "user",
}

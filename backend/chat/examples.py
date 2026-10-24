"""챗봇 32~34 고정 목업. DB 저장 · LLM 호출 · 실제 작업 실행은 하지 않는다."""

CONVERSATION_ID = "00000000-0000-4000-8000-000000000003"
TASK_ID = "00000000-0000-4000-8000-000000000004"
EXAMPLE_TIME = "2026-10-24T10:00:00+09:00"

# 거래 목록 전체를 집계한 값이 아닌, 소비 분석 카드 표시용 독립 샘플이다.
SPENDING_CARD = {
    "kind": "spending",
    "scope": "all",
    "period": {"from": "2026-10-01", "to": "2026-10-24"},
    "compare": None,
    "comparison": {"available": False, "reason": "not_requested"},
    "total": 18000,
    "fixedAmount": 0,
    "previousTotal": None,
    "categories": [{
        "category": "FOOD",
        "categoryName": "식비",
        "amount": 18000,
        "previousAmount": None,
        "ratio": 100,
        "prevDiff": None,
        "prevDiffRate": None,
    }],
    "topSpending": {
        "basis": "category",
        "items": [{"category": "FOOD", "categoryName": "식비", "amount": 18000, "ratio": 100}],
    },
    "unclassifiedAmount": 0,
    "lines": ["목업 예시예요. 이번 달 식비로 18,000원을 썼어요."],
    "updatedAt": EXAMPLE_TIME,
}

MESSAGES = [
    {
        "messageId": "00000000-0000-4000-8000-000000000009",
        "role": "user",
        "contentType": "text",
        "content": {"text": "소비 분석", "choices": []},
        "taskId": None,
        "createdAt": EXAMPLE_TIME,
    },
    {
        "messageId": "00000000-0000-4000-8000-000000000010",
        "role": "bot",
        "contentType": "card",
        "content": SPENDING_CARD,
        "taskId": None,
        "createdAt": "2026-10-24T10:00:01+09:00",
    },
]

CONVERSATION_EXAMPLE = {
    "conversationId": CONVERSATION_ID,
    "status": "active",
    "createdAt": EXAMPLE_TIME,
    "messages": MESSAGES,
}

MESSAGE_SENT_EXAMPLE = {
    "conversationId": CONVERSATION_ID,
    "route": {"intent": "spending", "slots": {}, "fallback": False},
    "messages": MESSAGES,
    "task": None,  # 소비 분석은 즉시 응답. 34번은 별도 샘플 작업으로 확인한다.
}

TASK_EXAMPLE = {
    "taskId": TASK_ID,
    "taskType": "budget_adjust",
    "status": "running",
    "conversationId": None,
    "createdAt": EXAMPLE_TIME,
    "startedAt": "2026-10-24T10:00:01+09:00",
    "completedAt": None,
    "result": None,
    "error": None,
}

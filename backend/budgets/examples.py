"""
껍데기 API 예시 응답 (2026-10-24)
- 고정지출: docs/api/fixed.md 의 예시 JSON 을 글자 그대로 옮김
- 정기 저축: savings.md 에 전체 예시가 없는 곳은 '공통 한 건 형태' 예시로 만든 값
진짜 기능을 만들면 이 파일은 지운다.
"""
import json

# 19 목록 보기 — fixed.md '4. 고정지출 목록 보기' 예시
FIXED_LIST_EXAMPLE = json.loads("""
{
  "items": [
    {
      "recurringId": "b3e26d97-05f6-4a12-bcde-953a2f7b0184",
      "name": "통신비",
      "amount": 55000,
      "paymentDay": 20,
      "paymentAccount": {
        "accountId": "27d3dbb3-1ee8-43b7-9134-9e8ff01991d4",
        "displayName": "국민 급여통장 · ****5678",
        "status": "connected"
      },
      "paymentCard": null,
      "category": "HOUSING_TELECOM",
      "categoryName": "주거·통신",
      "status": "suggested",
      "sourceType": "detected",
      "confirmedAt": null,
      "evidence": {
        "transactionId": "e29c7514-07a1-49f2-b7ab-b2fb1acba264",
        "merchantName": "통신사",
        "date": "2026-10-20",
        "amount": 55000
      },
      "evidenceSuggestion": null
    },
    {
      "recurringId": "d4e8c728-6103-42d0-a9c7-17e5a694c01f",
      "name": "OTT",
      "amount": 15000,
      "paymentDay": 5,
      "paymentAccount": null,
      "paymentCard": {
        "cardId": "6a213d92-887b-4ce1-a395-cd1e6e7482f0",
        "displayName": "신한 Deep Dream · ****1234",
        "status": "connected"
      },
      "category": "CULTURE_LEISURE",
      "categoryName": "문화·여가",
      "status": "confirmed",
      "sourceType": "manual",
      "confirmedAt": "2026-10-21T17:35:00+09:00",
      "evidence": null,
      "evidenceSuggestion": {
        "count": 1,
        "candidates": [
          {
            "transactionId": "c8c60113-7ac4-4cc2-998e-6bbbf5404f10",
            "merchantName": "구독 서비스",
            "date": "2026-10-05",
            "amount": 15000
          }
        ]
      }
    },
    {
      "recurringId": "a7c1d0e2-3b4f-4c68-9a12-8f30d5e1b6c4",
      "name": "월세",
      "amount": 700000,
      "paymentDay": 25,
      "paymentAccount": {
        "accountId": "27d3dbb3-1ee8-43b7-9134-9e8ff01991d4",
        "displayName": "국민 급여통장 · ****5678",
        "status": "connected"
      },
      "paymentCard": null,
      "category": "HOUSING_TELECOM",
      "categoryName": "주거·통신",
      "status": "confirmed",
      "sourceType": "manual",
      "confirmedAt": "2026-10-21T17:30:00+09:00",
      "evidence": null,
      "evidenceSuggestion": null
    }
  ],
  "summary": {
    "confirmedMonthlyTotal": 715000,
    "suggestedCount": 1
  }
}
""")

# 20 직접 추가 (201) — fixed.md '1. 고정지출 직접 추가' 예시
FIXED_CREATED_EXAMPLE = json.loads("""
{
  "recurringId": "a7c1d0e2-3b4f-4c68-9a12-8f30d5e1b6c4",
  "name": "월세",
  "amount": 700000,
  "paymentDay": 25,
  "paymentAccount": {
    "accountId": "27d3dbb3-1ee8-43b7-9134-9e8ff01991d4",
    "displayName": "국민 급여통장 · ****5678",
    "status": "connected"
  },
  "paymentCard": null,
  "category": "HOUSING_TELECOM",
  "categoryName": "주거·통신",
  "status": "confirmed",
  "sourceType": "manual",
  "confirmedAt": "2026-10-21T17:30:00+09:00",
  "evidence": null
}
""")

# 21 수정 (200) — fixed.md '2. 고정지출 수정' 예시
FIXED_UPDATED_EXAMPLE = json.loads("""
{
  "recurringId": "a7c1d0e2-3b4f-4c68-9a12-8f30d5e1b6c4",
  "name": "월세",
  "amount": 720000,
  "paymentDay": 26,
  "paymentAccount": {
    "accountId": "27d3dbb3-1ee8-43b7-9134-9e8ff01991d4",
    "displayName": "국민 급여통장 · ****5678",
    "status": "connected"
  },
  "paymentCard": null,
  "category": "HOUSING_TELECOM",
  "categoryName": "주거·통신",
  "status": "confirmed",
  "sourceType": "manual",
  "confirmedAt": "2026-10-21T17:30:00+09:00",
  "evidence": null
}
""")

# 23 후보 확정 — fixed.md '5. 후보 확정' 예시
FIXED_CONFIRM_EXAMPLE = json.loads("""
{
  "items": [
    {
      "recurringId": "b3e26d97-05f6-4a12-bcde-953a2f7b0184",
      "name": "통신비",
      "amount": 55000,
      "paymentDay": 20,
      "paymentAccount": {
        "accountId": "27d3dbb3-1ee8-43b7-9134-9e8ff01991d4",
        "displayName": "국민 급여통장 · ****5678",
        "status": "connected"
      },
      "paymentCard": null,
      "category": "HOUSING_TELECOM",
      "categoryName": "주거·통신",
      "status": "confirmed",
      "sourceType": "detected",
      "confirmedAt": "2026-10-21T18:00:00+09:00",
      "evidence": {
        "transactionId": "e29c7514-07a1-49f2-b7ab-b2fb1acba264",
        "merchantName": "통신사",
        "date": "2026-10-20",
        "amount": 55000
      }
    }
  ]
}
""")

# 24 후보 무시 — fixed.md '6. 후보 무시' 예시
FIXED_IGNORE_EXAMPLE = json.loads("""
{
  "items": [
    {
      "recurringId": "b3e26d97-05f6-4a12-bcde-953a2f7b0184",
      "name": "통신비",
      "amount": 55000,
      "paymentDay": 20,
      "paymentAccount": {
        "accountId": "27d3dbb3-1ee8-43b7-9134-9e8ff01991d4",
        "displayName": "국민 급여통장 · ****5678",
        "status": "connected"
      },
      "paymentCard": null,
      "category": "HOUSING_TELECOM",
      "categoryName": "주거·통신",
      "status": "ignored",
      "sourceType": "detected",
      "confirmedAt": null,
      "evidence": {
        "transactionId": "e29c7514-07a1-49f2-b7ab-b2fb1acba264",
        "merchantName": "통신사",
        "date": "2026-10-20",
        "amount": 55000
      }
    }
  ]
}
""")

# 25 목록 보기 — savings.md '1.' (후보 1 + 확정 1, 최소 금액 = 확정 합계)
SAVINGS_LIST_EXAMPLE = json.loads("""
{
  "items": [
    {
      "savingId": "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
      "name": "주택청약",
      "amount": 200000,
      "transferDay": 10,
      "fromAccount": {
        "accountId": "27d3dbb3-1ee8-43b7-9134-9e8ff01991d4",
        "displayName": "국민 급여통장 · ****5678",
        "status": "connected"
      },
      "toAccount": {
        "accountId": "4b5c6d7e-8f90-4a1b-9c2d-3e4f5a6b7c8d",
        "displayName": "주택청약 · ****3456",
        "status": "connected"
      },
      "status": "suggested",
      "sourceType": "detected",
      "confirmedAt": null,
      "evidenceCount": 3,
      "lastTransferOn": "2026-10-10"
    },
    {
      "savingId": "6f1d2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a5b",
      "name": "신한 자유적금",
      "amount": 300000,
      "transferDay": 25,
      "fromAccount": {
        "accountId": "27d3dbb3-1ee8-43b7-9134-9e8ff01991d4",
        "displayName": "국민 급여통장 · ****5678",
        "status": "connected"
      },
      "toAccount": {
        "accountId": "9c2e7b10-5a4f-4d8e-b3c1-7f6a5e4d3c2b",
        "displayName": "신한 자유적금 · ****9012",
        "status": "connected"
      },
      "status": "confirmed",
      "sourceType": "detected",
      "confirmedAt": "2026-10-21T18:00:00+09:00",
      "evidenceCount": 3,
      "lastTransferOn": "2026-09-25"
    }
  ],
  "summary": {
    "minimumMonthly": 300000,
    "suggestedCount": 1
  }
}
""")

# 26 직접 추가 (201) — savings.md '2.' 요청 예시를 confirmed · manual · evidenceCount 0 으로
SAVINGS_CREATED_EXAMPLE = json.loads("""
{
  "savingId": "8e7d6c5b-4a39-4281-9f0e-1d2c3b4a5968",
  "name": "청약",
  "amount": 100000,
  "transferDay": 10,
  "fromAccount": null,
  "toAccount": null,
  "status": "confirmed",
  "sourceType": "manual",
  "confirmedAt": "2026-10-21T17:30:00+09:00",
  "evidenceCount": 0,
  "lastTransferOn": null
}
""")

# 27 수정 (200) — savings.md '3.' 요청 { amount: 350000 } 의 결과
SAVINGS_UPDATED_EXAMPLE = json.loads("""
{
  "savingId": "6f1d2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a5b",
  "name": "신한 자유적금",
  "amount": 350000,
  "transferDay": 25,
  "fromAccount": {
    "accountId": "27d3dbb3-1ee8-43b7-9134-9e8ff01991d4",
    "displayName": "국민 급여통장 · ****5678",
    "status": "connected"
  },
  "toAccount": {
    "accountId": "9c2e7b10-5a4f-4d8e-b3c1-7f6a5e4d3c2b",
    "displayName": "신한 자유적금 · ****9012",
    "status": "connected"
  },
  "status": "confirmed",
  "sourceType": "detected",
  "confirmedAt": "2026-10-21T18:00:00+09:00",
  "evidenceCount": 3,
  "lastTransferOn": "2026-09-25"
}
""")

# 29 후보 확정 — savings.md '5.' { items: [한 건 형태] }
SAVINGS_CONFIRM_EXAMPLE = json.loads("""
{
  "items": [
    {
      "savingId": "6f1d2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a5b",
      "name": "신한 자유적금",
      "amount": 300000,
      "transferDay": 25,
      "fromAccount": {
        "accountId": "27d3dbb3-1ee8-43b7-9134-9e8ff01991d4",
        "displayName": "국민 급여통장 · ****5678",
        "status": "connected"
      },
      "toAccount": {
        "accountId": "9c2e7b10-5a4f-4d8e-b3c1-7f6a5e4d3c2b",
        "displayName": "신한 자유적금 · ****9012",
        "status": "connected"
      },
      "status": "confirmed",
      "sourceType": "detected",
      "confirmedAt": "2026-10-21T18:00:00+09:00",
      "evidenceCount": 3,
      "lastTransferOn": "2026-09-25"
    }
  ]
}
""")

# 30 후보 무시 — savings.md '6.' { items: [한 건 형태] }
SAVINGS_IGNORE_EXAMPLE = json.loads("""
{
  "items": [
    {
      "savingId": "6f1d2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a5b",
      "name": "신한 자유적금",
      "amount": 300000,
      "transferDay": 25,
      "fromAccount": {
        "accountId": "27d3dbb3-1ee8-43b7-9134-9e8ff01991d4",
        "displayName": "국민 급여통장 · ****5678",
        "status": "connected"
      },
      "toAccount": {
        "accountId": "9c2e7b10-5a4f-4d8e-b3c1-7f6a5e4d3c2b",
        "displayName": "신한 자유적금 · ****9012",
        "status": "connected"
      },
      "status": "ignored",
      "sourceType": "detected",
      "confirmedAt": null,
      "evidenceCount": 3,
      "lastTransferOn": "2026-09-25"
    }
  ]
}
""")

"""
가짜 데이터 (DB 연결 전까지 API 가 돌려주는 값)
- 프로토타입 '거래 내역' 화면의 10월 거래를 그대로 적어둔 것. 계산하지 않는다.
- kind: "normal" 일반 거래 / "pending" AI가 카테고리를 확신 못 한 거래
"""

MONTH = {"label": "10월", "used": 470_000, "budget_label": "112만원", "pct": 42}
RECENT = {"count": 12, "total": 365_700}
PENDING_COUNT = 4

# 2026-10-20 분류 기준: 10개 + 기타 (사용자가 고를 수 있는 목록). 미분류는 화면에 "확인 필요"로 따로 보여 준다
CATEGORIES = ["식비", "주거·통신", "교통·차량", "생활용품", "패션·쇼핑", "의료·건강", "뷰티·미용", "교육", "문화·여가", "여행·숙박", "기타"]

TX_DAYS = [
    {"date": "10월 11일 · 오늘", "items": [
        {"id": 0, "kind": "normal", "icon": "utensils", "name": "점심 식사", "cat": "식비", "amount": 18_000},
        {"id": 1, "kind": "normal", "icon": "scissors", "name": "올리브영", "cat": "뷰티·미용", "amount": 40_000},
    ]},
    {"date": "10월 10일", "items": [
        {"id": 2, "kind": "normal", "icon": "utensils", "name": "친구들과 저녁", "cat": "식비", "amount": 60_000},
        {"id": 10, "kind": "pending", "name": "(주)케이에스넷", "amount": 23_000,
         "why": "카드 결제대행사 이름만 있어 가게를 특정하지 못했어요.", "picks": ["식비", "생활용품"]},
    ]},
    {"date": "10월 9일", "items": [
        {"id": 3, "kind": "pending", "name": "GS25 역삼점", "amount": 6_800,
         "why": "편의점은 여러 물건을 팔아서 무엇을 샀는지 알 수 없어요.", "picks": ["식비", "생활용품"]},
        {"id": 4, "kind": "normal", "icon": "bus", "name": "교통카드 충전", "cat": "교통·차량", "amount": 30_000},
    ]},
    {"date": "10월 8일", "items": [
        {"id": 5, "kind": "pending", "name": "다이소", "amount": 12_000,
         "why": "여러 물건을 파는 가게라 무엇을 샀는지 알 수 없어요.", "picks": ["생활용품", "패션·쇼핑"]},
    ]},
    {"date": "10월 7일", "items": [
        {"id": 6, "kind": "normal", "icon": "house", "name": "관리비", "cat": "주거·통신", "amount": 60_000},
    ]},
    {"date": "10월 6일", "items": [
        {"id": 11, "kind": "pending", "name": "네이버페이 결제", "amount": 15_900,
         "why": "간편결제라 실제 가맹점 정보가 없어요.", "picks": ["생활용품", "패션·쇼핑"]},
    ]},
    {"date": "10월 5일", "items": [
        {"id": 7, "kind": "normal", "icon": "ticket", "name": "영화 관람", "cat": "문화·여가", "amount": 28_000},
        {"id": 8, "kind": "normal", "icon": "utensils", "name": "동네 정육점", "cat": "식비", "amount": 42_000},
    ]},
    {"date": "10월 3일", "items": [
        {"id": 9, "kind": "normal", "icon": "scissors", "name": "미용실", "cat": "뷰티·미용", "amount": 30_000},
    ]},
]

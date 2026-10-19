"""
가짜 데이터 (3단계 화면 옮기기용)
- 프로토타입 '거래 내역' 화면의 10월 거래를 그대로 적어둔 것. 계산하지 않는다.
- kind: "normal" 일반 거래 / "pending" AI가 카테고리를 확신 못 한 거래
"""

MONTH = {"label": "10월", "used": 470_000, "budget_label": "112만원", "pct": 42}
RECENT = {"count": 12, "total": 365_700}
PENDING_COUNT = 2

CATEGORIES = ["식비", "주거/통신", "교통/차량", "생활용품", "건강/미용", "여가/관계"]

TX_DAYS = [
    {"date": "10월 11일 · 오늘", "items": [
        {"id": 0, "kind": "normal", "icon": "utensils", "name": "점심 식사", "cat": "식비", "amount": 18_000},
        {"id": 1, "kind": "normal", "icon": "heart", "name": "올리브영", "cat": "건강/미용", "amount": 40_000},
    ]},
    {"date": "10월 10일", "items": [
        {"id": 2, "kind": "normal", "icon": "users", "name": "친구들과 저녁", "cat": "여가/관계", "amount": 60_000},
        {"id": 10, "kind": "pending", "name": "(주)케이에스넷", "amount": 23_000,
         "why": "카드 결제대행사 이름만 있어 가게를 특정하지 못했어요.", "picks": ["식비", "생활용품"]},
    ]},
    {"date": "10월 9일", "items": [
        {"id": 3, "kind": "normal", "icon": "store", "name": "편의점", "cat": "식비", "amount": 6_800},
        {"id": 4, "kind": "normal", "icon": "bus", "name": "교통카드 충전", "cat": "교통/차량", "amount": 30_000},
    ]},
    {"date": "10월 8일", "items": [
        {"id": 5, "kind": "normal", "icon": "shopping-basket", "name": "다이소", "cat": "생활용품", "amount": 12_000},
    ]},
    {"date": "10월 7일", "items": [
        {"id": 6, "kind": "normal", "icon": "building", "name": "관리비", "cat": "주거/통신", "amount": 60_000},
    ]},
    {"date": "10월 6일", "items": [
        {"id": 11, "kind": "pending", "name": "네이버페이 결제", "amount": 15_900,
         "why": "간편결제라 실제 가맹점 정보가 없어요.", "picks": ["생활용품", "여가/관계"]},
    ]},
    {"date": "10월 5일", "items": [
        {"id": 7, "kind": "normal", "icon": "film", "name": "영화 관람", "cat": "여가/관계", "amount": 28_000},
        {"id": 8, "kind": "normal", "icon": "shopping-cart", "name": "장보기", "cat": "식비", "amount": 42_000},
    ]},
    {"date": "10월 3일", "items": [
        {"id": 9, "kind": "normal", "icon": "scissors", "name": "미용실", "cat": "건강/미용", "amount": 30_000},
    ]},
]

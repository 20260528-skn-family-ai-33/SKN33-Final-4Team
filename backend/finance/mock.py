"""
가짜 데이터 (DB 연결 전까지 API 가 돌려주는 값)
- 프로토타입 '거래 내역' 화면의 10월 거래를 그대로 적어둔 것. 계산하지 않는다.
- kind: "normal" 일반 거래 / "pending" AI가 카테고리를 확신 못 한 거래
- name 은 실제 CODEF 응답처럼: 카드 = 가맹점명(resMemberStoreName), 계좌 = 적요(resAccountDesc)
- via = 결제 수단 (카드명·계좌) / cancel = "full" 취소 · "partial" 부분취소 (cancel_amount = 취소된 금액)
- 10/22 결정 V① · W · AA⑤⑥ (docs/api/transactions.md):
  kind "income" = 월급 입금(H-01로 찾은 월급만) / "transfer" = 남에게 보낸 이체. 둘 다 카테고리가 없고 소비 합계에 안 들어간다
  original = "transfer" → 원래 이체였는데 사용자가 [소비로 분류하기]로 카테고리를 고른 거래 (되돌릴 수 있음)
  funding = "fixed" + fixed_name → 고정지출로 연결된 거래 ('고정' 표시, 생활비 사용액에서 빠짐)
"""

MONTH = {"label": "10월", "used": 470_000, "budget_label": "112만원", "pct": 42}
RECENT = {"count": 14, "total": 385_700}  # 취소분을 뺀 금액
PENDING_COUNT = 4

# 2026-10-20 분류 기준: 10개 + 기타 (사용자가 고를 수 있는 목록). 미분류는 화면에 "확인 필요"로 따로 보여 준다
CATEGORIES = ["식비", "주거·통신", "교통·차량", "생활용품", "패션·쇼핑", "의료·건강", "뷰티·미용", "교육", "문화·여가", "여행·숙박", "기타"]

TX_DAYS = [
    {"date": "10월 11일 · 오늘", "items": [
        {"id": 0, "kind": "normal", "icon": "utensils", "name": "김밥천국 역삼점", "cat": "식비", "amount": 18_000, "via": "신한 Deep Dream 체크"},
        {"id": 1, "kind": "normal", "icon": "scissors", "name": "CJ올리브영 강남역점", "cat": "뷰티·미용", "amount": 40_000, "via": "신한 Mr.Life 신용"},
        {"id": 12, "kind": "normal", "icon": "shirt", "name": "무신사 스토어", "cat": "패션·쇼핑", "amount": 89_000, "via": "신한 Mr.Life 신용",
         "cancel": "full", "cancel_amount": 89_000},
    ]},
    {"date": "10월 10일", "items": [
        {"id": 2, "kind": "normal", "icon": "utensils", "name": "육전식당 역삼점", "cat": "식비", "amount": 60_000, "via": "신한 Mr.Life 신용"},
        {"id": 14, "kind": "transfer", "icon": "send", "name": "이하늘", "amount": 30_000, "via": "국민 급여통장 · ****5678"},
        {"id": 10, "kind": "pending", "name": "(주)케이에스넷", "amount": 23_000, "via": "신한 Deep Dream 체크",
         "why": "카드 결제대행사 이름만 있어 가게를 특정하지 못했어요.", "picks": ["식비", "생활용품"]},
    ]},
    {"date": "10월 9일", "items": [
        {"id": 3, "kind": "pending", "name": "GS25 역삼점", "amount": 6_800, "via": "신한 Deep Dream 체크",
         "why": "편의점은 여러 물건을 팔아서 무엇을 샀는지 알 수 없어요.", "picks": ["식비", "생활용품"]},
        {"id": 4, "kind": "normal", "icon": "bus", "name": "티머니 충전", "cat": "교통·차량", "amount": 30_000, "via": "신한 Deep Dream 체크"},
    ]},
    {"date": "10월 8일", "items": [
        {"id": 5, "kind": "pending", "name": "다이소 강남역점", "amount": 12_000, "via": "신한 Deep Dream 체크",
         "why": "여러 물건을 파는 가게라 무엇을 샀는지 알 수 없어요.", "picks": ["생활용품", "패션·쇼핑"]},
        {"id": 13, "kind": "normal", "icon": "graduation-cap", "name": "교보문고 강남점", "cat": "교육", "amount": 32_000, "via": "신한 Mr.Life 신용",
         "cancel": "partial", "cancel_amount": 12_000},
    ]},
    {"date": "10월 7일", "items": [
        {"id": 6, "kind": "normal", "icon": "house", "name": "아파트관리비 자동이체", "cat": "주거·통신", "amount": 60_000, "via": "국민 ****5678 계좌"},
    ]},
    {"date": "10월 6일", "items": [
        {"id": 11, "kind": "pending", "name": "네이버페이 결제", "amount": 15_900, "via": "신한 Deep Dream 체크",
         "why": "간편결제라 실제 가맹점 정보가 없어요.", "picks": ["생활용품", "패션·쇼핑"]},
    ]},
    {"date": "10월 5일", "items": [
        {"id": 7, "kind": "normal", "icon": "ticket", "name": "CGV 강남", "cat": "문화·여가", "amount": 28_000, "via": "신한 Mr.Life 신용"},
        {"id": 15, "kind": "normal", "icon": "film", "name": "NETFLIX.COM", "cat": "문화·여가", "amount": 15_000, "via": "신한 Deep Dream 체크",
         "funding": "fixed", "fixed_name": "구독료"},
        {"id": 8, "kind": "normal", "icon": "utensils", "name": "한우마을 정육점", "cat": "식비", "amount": 42_000, "via": "신한 Deep Dream 체크"},
    ]},
    {"date": "10월 4일", "items": [
        {"id": 16, "kind": "normal", "icon": "send", "name": "김민수", "cat": "식비", "amount": 24_000, "via": "국민 급여통장 · ****5678",
         "original": "transfer"},
    ]},
    {"date": "10월 3일", "items": [
        {"id": 9, "kind": "normal", "icon": "scissors", "name": "라헤어 역삼점", "cat": "뷰티·미용", "amount": 30_000, "via": "신한 Deep Dream 체크"},
    ]},
    {"date": "10월 1일", "items": [
        {"id": 17, "kind": "income", "icon": "banknote", "name": "급여", "amount": 2_600_000, "via": "국민 급여통장 · ****5678"},
    ]},
]

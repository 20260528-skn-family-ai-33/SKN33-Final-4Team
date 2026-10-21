"""
가짜 데이터 (DB 연결 전까지 API 가 돌려주는 값)
- 프로토타입(머니쿠션_MVP_프로토타입.html) 화면에 나오던 숫자를 그대로 적어둔 것.
- 계산하지 않는다. DB 설계·계산 엔진이 생기면 이 파일 대신 진짜 데이터를 쓴다.
"""

PERIOD = {
    "label": "10.01 — 10.31",
    "dday": 21,
    "updated": "오늘 09:30 기준",
    "plan_name": "생활 안정형",
    "version": "v1",
}

HOME = {
    "balance_total": 2_800_000,
    "account_count": 2,
    "cushion_now": 300_000,
    "cushion_goal": 8_400_000,
    "cushion_bar": 3.57,  # 막대 길이(%)
    "living_used": 470_000,
    "living_budget": 1_120_000,
    "living_pct": 42,
    "living_left": 650_000,
    "daily": 30_900,
    "spare": 265_000,
    "pending_count": 4,
}

# 홈 '꼭 나갈 돈'
MUST_PAY = [
    {"icon": "credit-card", "name": "신한카드 청구서", "sub": "10.15 출금예정 · 신한 ****1234", "amount": 420_000},
    {"icon": "repeat", "name": "통신비", "sub": "매월 20일 · 고정지출", "amount": 55_000, "link": "/budgets/fixed"},
    {"icon": "repeat", "name": "월세", "sub": "매월 25일 · 고정지출", "amount": 700_000, "link": "/budgets/fixed"},
]

# 카테고리 예산 '한 달 나눠보기'
MONTHLY = 2_800_000
MONTH_SPLIT = [
    {"name": "고정지출", "amount": 770_000},
    {"name": "저축", "amount": 410_000},
    {"name": "생활비", "amount": 1_120_000},
    {"name": "비정기지출 준비금", "amount": 78_000, "popup": "irregular"},
    {"name": "머니 쿠션 적립", "amount": 210_000},
    {"name": "여윳돈", "amount": 212_000},
]

# 생활비 카테고리 — 2026-10-20 분류 기준 10개 + 기타 (기타도 예산 칸을 준다)
# minAmount: 카테고리 최소 금액 (2026-10-21, 설정 안 했으면 None). locked(자물쇠 = 금액 고정)와 다르다
CATEGORIES = [
    {"name": "식비", "icon": "utensils", "used": 150_000, "budget": 330_000, "pct": 45, "left": 180_000, "locked": False, "minAmount": 300_000},
    {"name": "주거·통신", "icon": "house", "used": 60_000, "budget": 110_000, "pct": 55, "left": 50_000, "locked": False, "minAmount": None},
    {"name": "교통·차량", "icon": "bus", "used": 35_000, "budget": 90_000, "pct": 39, "left": 55_000, "locked": False, "minAmount": 80_000},
    {"name": "생활용품", "icon": "shopping-basket", "used": 15_000, "budget": 60_000, "pct": 25, "left": 45_000, "locked": False, "minAmount": None},
    {"name": "패션·쇼핑", "icon": "shirt", "used": 30_000, "budget": 80_000, "pct": 38, "left": 50_000, "locked": False, "minAmount": None},
    {"name": "의료·건강", "icon": "stethoscope", "used": 20_000, "budget": 50_000, "pct": 40, "left": 30_000, "locked": False, "minAmount": None},
    {"name": "뷰티·미용", "icon": "scissors", "used": 40_000, "budget": 60_000, "pct": 67, "left": 20_000, "locked": False, "minAmount": None},
    {"name": "교육", "icon": "graduation-cap", "used": 20_000, "budget": 50_000, "pct": 40, "left": 30_000, "locked": False, "minAmount": None},
    {"name": "문화·여가", "icon": "ticket", "used": 80_000, "budget": 170_000, "pct": 47, "left": 90_000, "locked": False, "minAmount": None},
    {"name": "여행·숙박", "icon": "plane", "used": 0, "budget": 60_000, "pct": 0, "left": 60_000, "locked": False, "minAmount": None},
    {"name": "기타", "icon": "ellipsis", "used": 20_000, "budget": 60_000, "pct": 33, "left": 40_000, "locked": False, "minAmount": None},
]
UNCLASSIFIED = {"count": 4, "amount": 57_700}  # 화면에는 "확인 필요"로 보인다 (편의점·다이소 등)

# 머니 쿠션
CUSHION = {
    "now": 300_000,
    "goal": 8_400_000,
    "goal_label": "월급×3",
    "bar": 3.57,
    "pct": 4,
    "floor": 200_000,
    "use_rule": "내 승인 후 허용",
    "remain": 8_100_000,
    "per_pay": 210_000,
    "eta": "39회 · 약 39개월",
}

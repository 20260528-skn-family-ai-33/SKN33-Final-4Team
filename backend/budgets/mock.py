"""
가짜 데이터 (3단계 화면 옮기기용)
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
    "pending_count": 2,
}

# 홈 '꼭 나갈 돈'
MUST_PAY = [
    {"icon": "credit-card", "name": "신한카드 청구서", "sub": "10.15 출금예정 · 신한 ****1234", "amount": 420_000},
    {"icon": "repeat", "name": "통신비", "sub": "매월 20일 · 고정지출", "amount": 55_000, "link": "budgets:fixed"},
    {"icon": "repeat", "name": "월세", "sub": "매월 25일 · 고정지출", "amount": 700_000, "link": "budgets:fixed"},
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

# 생활비 카테고리
CATEGORIES = [
    {"name": "식비", "icon": "utensils", "used": 150_000, "budget": 400_000, "pct": 38, "left": 250_000, "locked": False},
    {"name": "주거/통신", "icon": "house", "used": 60_000, "budget": 120_000, "pct": 50, "left": 60_000, "locked": False},
    {"name": "교통/차량", "icon": "bus", "used": 35_000, "budget": 100_000, "pct": 35, "left": 65_000, "locked": False},
    {"name": "생활용품", "icon": "shopping-basket", "used": 25_000, "budget": 80_000, "pct": 31, "left": 55_000, "locked": False},
    {"name": "건강/미용", "icon": "heart-pulse", "used": 70_000, "budget": 120_000, "pct": 58, "left": 50_000, "locked": False},
    {"name": "여가/관계", "icon": "users", "used": 130_000, "budget": 300_000, "pct": 43, "left": 170_000, "locked": False},
]
UNCLASSIFIED = {"count": 2, "amount": 38_900}

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

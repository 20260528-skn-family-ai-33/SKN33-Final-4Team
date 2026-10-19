// 가짜 데이터 (장고 budgets/mock.py 를 그대로 옮김)
// 계산하지 않는다. 6단계(장고 API)에서 진짜 데이터로 바꾼다

export const PERIOD = {
  label: '10.01 — 10.31',
  dday: 21,
  updated: '오늘 09:30 기준',
  planName: '생활 안정형',
  version: 'v1',
}

export const HOME = {
  balanceTotal: 2_800_000,
  accountCount: 2,
  cushionNow: 300_000,
  cushionGoal: 8_400_000,
  cushionBar: 3.57, // 막대 길이(%)
  livingUsed: 470_000,
  livingBudget: 1_120_000,
  livingPct: 42,
  livingLeft: 650_000,
  daily: 30_900,
  spare: 265_000,
  pendingCount: 2,
}

// 홈 '꼭 나갈 돈'. link 가 있으면 그 화면으로 이동
export const MUST_PAY = [
  { icon: 'CreditCard', name: '신한카드 청구서', sub: '10.15 출금예정 · 신한 ****1234', amount: 420_000 },
  { icon: 'Repeat', name: '통신비', sub: '매월 20일 · 고정지출', amount: 55_000, link: '/budgets/fixed' },
  { icon: 'Repeat', name: '월세', sub: '매월 25일 · 고정지출', amount: 700_000, link: '/budgets/fixed' },
]

// 카테고리 예산 '한 달 나눠보기'
export const MONTHLY = 2_800_000
export const MONTH_SPLIT = [
  { name: '고정지출', amount: 770_000 },
  { name: '저축', amount: 410_000 },
  { name: '생활비', amount: 1_120_000 },
  { name: '비정기지출 준비금', amount: 78_000, popup: 'irregular' },
  { name: '머니 쿠션 적립', amount: 210_000 },
  { name: '여윳돈', amount: 212_000 },
]

// 생활비 카테고리
export const CATEGORIES = [
  { name: '식비', icon: 'Utensils', used: 150_000, budget: 400_000, pct: 38, left: 250_000, locked: false },
  { name: '주거/통신', icon: 'House', used: 60_000, budget: 120_000, pct: 50, left: 60_000, locked: false },
  { name: '교통/차량', icon: 'Bus', used: 35_000, budget: 100_000, pct: 35, left: 65_000, locked: false },
  { name: '생활용품', icon: 'ShoppingBasket', used: 25_000, budget: 80_000, pct: 31, left: 55_000, locked: false },
  { name: '건강/미용', icon: 'HeartPulse', used: 70_000, budget: 120_000, pct: 58, left: 50_000, locked: false },
  { name: '여가/관계', icon: 'Users', used: 130_000, budget: 300_000, pct: 43, left: 170_000, locked: false },
]
export const UNCLASSIFIED = { count: 2, amount: 38_900 }

// 머니 쿠션
export const CUSHION = {
  now: 300_000,
  goal: 8_400_000,
  goalLabel: '월급×3',
  bar: 3.57,
  pct: 4,
  floor: 200_000,
  useRule: '내 승인 후 허용',
  remain: 8_100_000,
  perPay: 210_000,
  eta: '39회 · 약 39개월',
}

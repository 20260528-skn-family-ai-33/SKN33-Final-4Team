// 소비 분석 카드 시안 예시값 — POST /api/chat/messages/ 응답의 bot 메시지 content (docs/api/chat.md)
// 서버가 집계하고 문장은 템플릿으로 만든다. 챗봇 API 가 생기면 응답의 content 를 그대로 SpendingCard 에 넘긴다
// 숫자 맞추기: total 1,320,000 = 카테고리 합 1,285,000 + 확인 필요 35,000 / fixedAmount 770,000 = 월세 700,000 + 통신비 55,000 + 구독료 15,000
const CATS = [
  ['HOUSING_TELECOM', '주거·통신', 755_000, 755_000],
  ['FOOD', '식비', 230_000, 210_000],
  ['CULTURE_LEISURE', '문화·여가', 88_000, 60_000],
  ['BEAUTY', '뷰티·미용', 70_000, 90_000],
  ['TRANSPORT_CAR', '교통·차량', 60_000, 55_000],
  ['ETC', '기타', 42_000, 50_000],
  ['FASHION_SHOPPING', '패션·쇼핑', 40_000, 30_000],
]
const TOTAL = 1_320_000
const category = ([code, name, amount, prev]) => ({
  category: code, categoryName: name, amount, ratio: Math.round((amount / TOTAL) * 100),
  previousAmount: prev, prevDiff: amount - prev, prevDiffRate: prev > 0 ? Math.round(((amount - prev) / prev) * 100) : null,
})
const base = {
  kind: 'spending',
  period: { from: '2026-10-01', to: '2026-10-21' },
  compare: { from: '2026-09-01', to: '2026-09-21' },   // 이번 달이 안 끝나서 같은 날짜까지 (결정 C)
  comparison: { available: true, reason: null },
  total: TOTAL,
  fixedAmount: 770_000,
  previousTotal: 1_250_000,
  unclassifiedAmount: 35_000,
  updatedAt: '2026-10-21T16:20:00+09:00',
}

// "소비를 분석해주세요" → 전체
export const SPENDING_ALL = {
  ...base,
  scope: 'all',
  categories: CATS.map(category),
  topSpending: { basis: 'category', items: [category(CATS[0])] },
  lines: [
    '10월 1일~21일에 132만 원 썼어요. 월세 같은 고정지출 77만 원을 빼면 생활비는 55만 원이에요.',
    '9월 같은 기간보다 7만 원(6%) 더 썼어요.',
    '분류된 소비 중 주거·통신에 가장 많이 썼어요.',
  ],
}

// "지난달보다 식비 얼마 썼어?" → 카테고리 하나
export const SPENDING_FOOD = {
  ...base,
  scope: 'category',
  categories: [category(CATS[1])],
  topSpending: null,
  lines: ['9월 같은 기간보다 식비를 2만 원(10%) 더 썼어요.'],
}

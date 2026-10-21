// 예산 조정 도우미 공통 값·문장 만들기 (AdjustForm · 통합 챗봇이 같이 쓴다)

import { comma, won } from './money.js'

// 예산을 조정할 수 있는 카테고리 (10개 + 기타) — utils/categories.js
export { BUDGET_CATS as CATS } from './categories.js'

// 받침 있으면 '을', 없으면 '를' (식비를 / 문화·여가를 / 주거·통신을)
export function josa(word, withFinal = '을', without = '를') {
  const code = word ? word.charCodeAt(word.length - 1) - 0xac00 : -1
  if (code < 0 || code > 11171) return without
  return code % 28 ? withFinal : without
}

// 원 단위 금액을 말로: 50000 → '5만원' / 12345 → '12,345원'
export const manwon = (amount) => (amount % 10_000 === 0 ? `${comma(amount / 10_000)}만원` : won(amount))

// 고른 값을 문장으로: { 식비, 50000, up } → "식비를 5만원 늘리고 싶어요" (amount 단위: 원)
export const adjustSentence = ({ cat, amount, dir }) =>
  `${cat}${josa(cat)} ${manwon(amount)} ${dir === 'up' ? '늘리고' : '줄이고'} 싶어요`

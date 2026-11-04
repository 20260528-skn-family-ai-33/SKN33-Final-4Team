// 예산 밖 지출 한 줄 — 홈 · 예산 화면 (docs/api/budgets.md outOfBudget). 계산에는 안 넣고 안내만, 0원이면 숨긴다
import { won } from '../utils/money.js'
import { OUT_OF_BUDGET, catName } from '../utils/categories.js'

export default function OutOfBudgetLine({ value, className = 'mc-note' }) {
  if (!value?.total) return null
  return (
    <p className={className} style={{ padding: 0 }}>
      예산 밖 지출 {won(value.total)} · {OUT_OF_BUDGET.map(catName).join(', ')}은 생활비에서 빼고 따로 보여드려요
    </p>
  )
}

// 이전 기준(11/3 전)으로 승인한 예산 — 생활비 총액에 주거·통신 · 여행·숙박 배정이 들어 있어 카테고리 합계와 다르다 (N-F1)
export function OldScopeNote({ amount, className = 'mc-note' }) {
  if (!amount) return null
  return (
    <p className={className} role="note" style={{ padding: 0 }}>
      이전 기준으로 승인한 예산이에요. 주거·통신 · 여행·숙박 배정 {won(amount)}이 생활비에 들어 있어서 카테고리 합계와 달라요. 새 기준으로 추천을 다시 받아 주세요.
    </p>
  )
}

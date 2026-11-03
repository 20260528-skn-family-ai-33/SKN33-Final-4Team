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

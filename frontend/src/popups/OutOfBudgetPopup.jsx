// 팝업: 예산 밖 지출 — 홈 계좌 잔액 아래 '예산 밖 지출' 줄 (11/3: 파란 카드 안 안내 문장을 여기로)
// props: value = 홈 budget.outOfBudget { total, categories: [{ category, spent }] } (docs/api/home.md · budgets.md)
import Popup from './Popup.jsx'
import { won } from '../utils/money.js'
import { OUT_OF_BUDGET, catName } from '../utils/categories.js'

export default function OutOfBudgetPopup({ value }) {
  return (
    <Popup label="예산 밖 지출">
      <div className="mc-eyebrow">이번 급여 주기</div>
      <h1>예산 밖 지출</h1>
      <div className="mc-card">
        <div className="mc-money" style={{ fontSize: 24 }}>{won(value.total)}</div>
        {value.categories.filter((c) => c.spent > 0).map((c) => (
          <div className="mc-keyvalue" key={c.category}><span>{catName(c.category)}</span><strong>{won(c.spent)}</strong></div>
        ))}
        <p className="mc-note" style={{ paddingBottom: 0 }}>
          {OUT_OF_BUDGET.map(catName).join(' · ')}은 생활비 예산에 넣지 않고 따로 보여 드려요. 하루에 쓸 수 있는 돈 · 남은 생활비 계산에는 들어가지 않아요.
        </p>
      </div>
    </Popup>
  )
}

// 팝업: 하루에 쓸 수 있는 돈의 계산 — 홈 "계산 근거" (홈 budget 값 그대로, docs/api/home.md)
// 남은 생활비 = 승인 생활비 − 오늘까지 쓴 생활비(고정지출 · 예산 밖 지출 제외). 내일부터 남은 날로 나눈다 (채팅과 같은 기준)
import Popup from './Popup.jsx'
import { won } from '../utils/money.js'

export default function CalcPopup({ budget }) {
  return (
    <Popup>
      <h2>하루에 쓸 수 있는 돈의 계산</h2>
      <div className="mc-keyvalue"><span>이번 주기 생활비</span><strong>{won(budget.livingBudget)}</strong></div>
      <div className="mc-keyvalue"><span>남은 생활비</span><strong>{won(budget.livingLeft)}</strong></div>
      <div className="mc-keyvalue"><span>내일부터 남은 날</span><strong>{budget.daysLeft}일</strong></div>
      <p className="mc-note">
        {budget.daily == null
          ? '오늘이 이번 주기 마지막 날이라 하루 금액을 나누지 않아요.'
          : `생활비 ${won(budget.livingLeft)} ÷ 남은 ${budget.daysLeft}일 = 하루 ${won(budget.daily)}`}<br />
        고정지출과 예산 밖 지출(주거·통신, 여행·숙박)은 생활비에서 빼고 계산해요.
      </p>
    </Popup>
  )
}

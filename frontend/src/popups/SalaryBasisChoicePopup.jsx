// 가운데 창: 이번 주기를 받은 월급 기준으로 짠 뒤(월급 차이 안내 [이번 예산 다시 짜기]) 월급을 '이번 주기부터' 바꿀 때 (11/13 검수)
// 이번 주기 예산을 새 월급으로 다시 짤지, 받은 월급 기준을 그대로 둘지 고른다 → PUT salary keepIncomeBasis (docs/api/salary.md)
// props: received = 이번 주기 기준(받은 월급) · next = 새로 입력한 월 실수령액 · onPick(keep: boolean) — 창을 닫는 건 부르는 쪽에서
import Popup from './Popup.jsx'
import { won } from '../utils/money.js'

export default function SalaryBasisChoicePopup({ received, next, onPick }) {
  const choices = [
    [false, `${won(next)}으로 다시 짜기`, '이번 주기도 새 월급으로 지킬 돈과 예산을 다시 정해요'],
    [true, `받은 ${won(received)} 유지`, '이번 주기는 받은 월급으로 쓰고, 다음 주기부터 새 월급이 기준이 돼요'],
  ]
  return (
    <Popup label="이번 주기 예산 기준 고르기" dialog>
      <h2>이번 주기 예산은 받은 월급 기준이에요</h2>
      <p className="mc-muted" style={{ margin: '6px 0 0' }}>이번 주기는 실제 받은 {won(received)} 기준으로 짜여 있어요.<br />{won(next)} 기준으로 바꿀까요?</p>
      <div className="mc-apply-choices">
        {choices.map(([keep, label, sub]) => (
          <button key={String(keep)} type="button" className="mc-choice" onClick={() => onPick(keep)}>
            <strong>{label}</strong><span className="mc-muted">{sub}</span>
          </button>
        ))}
      </div>
    </Popup>
  )
}

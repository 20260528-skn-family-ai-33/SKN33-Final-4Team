// 팝업: 쓸 수 있는 돈의 계산 (장고 budgets/_calc.html) — 홈 "계산 근거"
import Popup from './Popup.jsx'

const ROWS = [
  ['연결 계좌 잔액', '2,800,000원'],
  ['남은 확정 지출', '−1,175,000원'],
  ['남은 생활비', '−650,000원'],
  ['저축 배정', '−410,000원'],
  ['쿠션 배정', '−300,000원'],
  ['추가 소비 여유', '265,000원'],
]

export default function CalcPopup() {
  return (
    <Popup>
      <h2>쓸 수 있는 돈의 계산</h2>
      {ROWS.map(([k, v]) => <div className="mc-keyvalue" key={k}><span>{k}</span><strong>{v}</strong></div>)}
      <p className="mc-note">
        생활비 650,000원 ÷ 남은 21일 = 하루 30,900원<br />
        생활비·저축은 지킬 돈 설정값 · 10월 11일 09:30 자료 기준<br />
        남은 확정 지출 = 카드 청구서(조회된 금액)·10월 남은 고정지출 · 확인 필요 항목은 빼고 계산
      </p>
    </Popup>
  )
}

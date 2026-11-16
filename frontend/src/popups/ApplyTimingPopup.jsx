// 가운데 창: 바꾼 내용을 언제부터 적용할지 고르기 (11/12)
// 월급 · 급여일 저장(SalaryPage), 고정지출 추가 · 수정 · 그만두기 뒤 [저장](FixedPage 관리 화면)에서 쓴다
// props: onPick('current_cycle' | 'next_cycle') — 고르면 바로 부른다. 창을 닫는 건 부르는 쪽에서
import Popup from './Popup.jsx'

const APPLY = [
  ['current_cycle', '이번 주기부터', '지킬 돈과 이번 예산을 바로 다시 정해요'],
  ['next_cycle', '다음 주기부터', '지금 예산은 그대로 두고 다음 급여일부터 반영돼요'],
]

export default function ApplyTimingPopup({ onPick }) {
  return (
    <Popup label="적용 시점 고르기" dialog>
      <h2>언제부터 적용할까요?</h2>
      <div className="mc-apply-choices">
        {APPLY.map(([k, label, sub]) => (
          <button key={k} type="button" className="mc-choice" onClick={() => onPick(k)}><strong>{label}</strong><span className="mc-muted">{sub}</span></button>
        ))}
      </div>
    </Popup>
  )
}

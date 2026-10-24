// 팝업: 비정기지출 준비금 — 예산 "비정기지출 준비금 ›"
// ⚠️ 10/24 결정 AB로 1차에서 뺌 (2차). 지금은 어디서도 열지 않는다 — 2차에 BudgetPage 에서 다시 연결
import Popup from './Popup.jsx'

const ITEMS = [
  { name: '실손보험 연납', sub: '240,000원 · 2027.04 · 남은 급여 6회', monthly: '월 40,000원' },
  { name: '설 명절 경조사', sub: '150,000원 · 2027.02 · 남은 급여 4회', monthly: '월 38,000원' },
]

export default function IrregularPopup() {
  return (
    <Popup>
      <h2>비정기지출 준비금</h2>
      <p className="mc-muted">정기적이지 않은 큰 지출을 급여마다 나눠 모아요.</p>
      {ITEMS.map((it) => (
        <div className="mc-priv-row" key={it.name}>
          <div><b>{it.name}</b><small>{it.sub}</small></div>
          <strong>{it.monthly}</strong>
        </div>
      ))}
      <div className="mc-keyvalue"><span>합계</span><strong>월 78,000원</strong></div>
      <p className="mc-note">금액과 날짜가 확정된 항목만 합계에 넣어요. 시안 예시 데이터예요.</p>
    </Popup>
  )
}

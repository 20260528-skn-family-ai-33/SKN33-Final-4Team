// 팝업: 거래 상세 (장고 finance/_transaction.html) — 거래 목록에서 거래 한 줄 누르기
// props: tx = 누른 거래, date = 그 거래의 날짜, categories = 카테고리 목록
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { won } from '../utils/money.js'

export default function TransactionPopup({ tx, date, categories }) {
  const { closePopup } = usePopup()

  return (
    <Popup label="거래 상세" sub>
      <div className="mc-eyebrow">거래 상세</div>
      <h1>{tx.name}</h1>
      <div className="mc-card">
        <div className="mc-money">{won(tx.amount)}</div>
        <div className="mc-keyvalue"><span>거래일</span><strong>{date}</strong></div>
        <div className="mc-keyvalue"><span>결제 계좌</span><strong>신한 · 1234</strong></div>
        <div className="mc-keyvalue"><span>카테고리</span><strong>{tx.cat}</strong></div>
        <div className="mc-keyvalue"><span>분류</span><strong>AI 자동 분류</strong></div>
      </div>
      <div className="mc-card">
        <h2>카테고리 수정</h2>
        {/* 고르면 닫힌다 · 저장은 DB 연결 후 */}
        <div className="mc-pend-pick">
          {categories.map((cat) => (
            <button type="button" key={cat} onClick={closePopup}>{cat === tx.cat && '✓ '}{cat}</button>
          ))}
        </div>
        <p className="mc-note" style={{ paddingBottom: 0 }}>고른 카테고리는 이후 같은 가맹점 분류에 반영돼요.</p>
      </div>
    </Popup>
  )
}

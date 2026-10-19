// 팝업: 어떤 카테고리인가요? (장고 finance/_tx_pick.html) — "카테고리 확인 필요" 거래의 "다른 카테고리"
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { won } from '../utils/money.js'

// props: tx = 고를 거래, categories = 카테고리 목록
export default function TxPickPopup({ tx, categories }) {
  const { closePopup } = usePopup()

  return (
    <Popup label="카테고리 고르기">
      <h2>어떤 카테고리인가요?</h2>
      <p className="mc-muted" style={{ margin: '4px 0 12px' }}>{tx.name} · {won(tx.amount)}</p>
      <div className="mc-lock-list">
        {categories.map((cat) => (
          <button type="button" className="mc-lock-opt" key={cat} onClick={closePopup}><span className="mc-grow">{cat}</span></button>
        ))}
      </div>
    </Popup>
  )
}

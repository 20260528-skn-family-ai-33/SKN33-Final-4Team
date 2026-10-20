// 팝업: 거래 상세 — 거래 목록에서 거래 한 줄 누르기
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
        <div className="mc-money">{tx.cancel === 'full' ? <s>{won(tx.amount)}</s> : won(tx.net ?? tx.amount)}</div>
        {tx.cancel && (
          <p className="mc-cancel-box" role="note">
            {tx.cancel === 'full'
              ? '카드사에서 취소된 거래예요. 소비에서 빠졌어요.'
              : `${won(tx.amount)} 중 ${won(tx.cancelAmount)}이 취소됐어요. 소비에는 ${won(tx.net)}만 들어가요.`}
          </p>
        )}
        <div className="mc-keyvalue"><span>거래일</span><strong>{date}</strong></div>
        <div className="mc-keyvalue"><span>결제 수단</span><strong>{tx.via ?? '신한 · 1234'}</strong></div>
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

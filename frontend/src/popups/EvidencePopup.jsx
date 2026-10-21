// 팝업: 어떤 결제인가요? — 고정지출과 비슷한 카드 결제가 여러 건일 때 하나 고르기 (10/21 결정, C-14 · C-16)
// props: item = 고정지출 (item.suggestion.candidates = 서버가 찾은 결제, 출금일과 가까운 순 최대 5건)
// 고르면 PATCH /api/budgets/fixed/<id>/ { evidenceTransactionId } → 그 결제를 고정비로 옮기고 가맹점을 기억 (C-12)
// '이 중에 없어요' = 요청 없음. 다음 달 새 결제가 오면 다시 물어본다
// 다른 고정지출에 이미 연결된 결제는 서버가 빼고 준다
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { won } from '../utils/money.js'

export default function EvidencePopup({ item }) {
  const { closePopup } = usePopup()
  const { count, candidates } = item.suggestion

  return (
    <Popup label="결제 고르기">
      <h2>{item.name}은(는) 어떤 결제인가요?</h2>
      <p className="mc-muted" style={{ margin: '4px 0 12px' }}>
        매월 {item.day}일 · {won(item.amount)}과 비슷한 결제 {count}건{count > candidates.length ? ` 중 가까운 ${candidates.length}건` : ''}
      </p>
      <div className="mc-lock-list">
        {candidates.map((t) => (
          // 저장은 API 연결 후: apiSend(`/api/budgets/fixed/${item.id}/`, 'PATCH', { evidenceTransactionId: t.id })
          <button type="button" className="mc-lock-opt" key={t.id} onClick={closePopup}>
            <span className="mc-grow">{t.date} {t.merchant}</span><strong>{won(t.amount)}</strong>
          </button>
        ))}
        <button type="button" className="mc-lock-opt" onClick={closePopup}><span className="mc-grow">이 중에 없어요</span></button>
      </div>
    </Popup>
  )
}

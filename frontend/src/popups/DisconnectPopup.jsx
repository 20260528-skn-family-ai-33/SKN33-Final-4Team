// 팝업: 연결 해제 확인 — 연결 상세(계좌 · 카드사)의 [연결 해제]
// props: inst = 기관 이름, kind = 'bank' | 'card', connectionId = 해제할 연결, onDone() = 해제한 뒤(목록 새로고침)
//        related = 이 연결의 계좌 · 카드로 나가는 고정지출 · 정기 저축
//   related 는 화면이 연결 목록의 accountIds · cardIds(결정 Z③)로 고정지출 · 저축 목록에서 골라 만든다
//   [{ id, type: 'fixed' | 'saving', name, line, amount }]
// 결정 F (docs/api/finance-connect.md '연결 해제'):
//   - 해제: DELETE /api/finance/connections/<id>/ → 204. 지난 거래는 남고, 해제 전 거래는 소비에 계속 들어간다(N⑧)
//   - 같이 그만할지 물어본다. 기본은 체크 안 함. 체크한 것만 기존 중지 API 로 그만한다
//       고정지출 DELETE /api/budgets/fixed/<id>/ · 정기 저축 DELETE /api/budgets/savings/<id>/
//   - 체크 안 한 것은 남고, 결제 수단이 '연결 해제됨 · 다시 고르기'로 보인다
import { useState } from 'react'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { won } from '../utils/money.js'
import { apiSend } from '../utils/api.js'

// relatedMissing = 고정지출 · 저축 목록을 못 받아서 related 가 비었을 수 있음 → 물어보지 못했다고 알린다
export default function DisconnectPopup({ inst, kind = 'bank', connectionId, onDone, related = [], relatedMissing = false }) {
  const { closePopup } = usePopup()
  const [stop, setStop] = useState(() => new Set())   // 같이 그만할 항목 id
  const toggle = (id) => setStop((prev) => {
    const next = new Set(prev)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })
  const what = kind === 'card' ? '카드' : '계좌'
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  // 순서: 체크한 고정지출 · 저축 중지 → 연결 해제 (11/03 S2). 중지가 실패하면 해제하지 않고 멈춘다
  const onDisconnect = async () => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      for (const x of related.filter((r) => stop.has(`${r.type}-${r.id}`))) {
        await apiSend(`/api/budgets/${x.type === 'saving' ? 'savings' : 'fixed'}/${x.id}/`, 'DELETE')
      }
      await apiSend(`/api/finance/connections/${connectionId}/`, 'DELETE')
      onDone?.()
      closePopup()
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  return (
    <Popup label="연결 해제">
      <h2>{inst} 연결을 해제할까요?</h2>
      <ul className="mc-disc-list">
        <li>이 {kind === 'card' ? '카드사' : '은행'}의 잔액 · 거래를 더 불러오지 않아요.</li>
        <li>지금까지 불러온 거래는 남고, 소비 분석에도 그대로 들어가요.</li>
        <li>다시 연결하면 이어서 불러와요.</li>
      </ul>
      <p className="mc-note">기존 데이터까지 삭제하려면 마이 &gt; 동의·데이터 관리에서 해당 범위의 조회 동의를 철회해 주세요. 같은 범위의 다른 기관에서 불러온 데이터도 함께 삭제돼요.</p>

      {related.length > 0 && (
        <div className="mc-disc-rel">
          <h3>이 {what}로 나가는 돈도 같이 그만할까요?</h3>
          <p className="mc-muted">체크한 것만 그만해요. 체크하지 않으면 그대로 남고, 결제 수단만 '연결 해제됨'으로 보여요.</p>
          {related.map((x) => (
            <label key={`${x.type}-${x.id}`} className="mc-disc-item">
              <input type="checkbox" checked={stop.has(`${x.type}-${x.id}`)} onChange={() => toggle(`${x.type}-${x.id}`)} />
              <span className="mc-grow">
                <strong>{x.name}</strong> <em className="mc-disc-tag">{x.type === 'saving' ? '정기 저축' : '고정지출'}</em>
                <small>{x.line}</small>
              </span>
              <b>{won(x.amount)}</b>
            </label>
          ))}
        </div>
      )}

      {relatedMissing && (
        <p className="mc-note">고정지출 · 정기 저축 목록을 불러오지 못해 같이 그만할 항목을 보여 드리지 못했어요. 해제해도 그 항목은 그대로 남아요.</p>
      )}
      {error && <p className="mc-error" role="alert">{error}</p>}
      <div className="mc-inline mc-disc-act">
        <button type="button" className="mc-secondary" onClick={closePopup}>취소</button>
        <button type="button" className="mc-secondary mc-danger" disabled={busy} onClick={onDisconnect}>
          {stop.size > 0 ? `${stop.size}건 그만하고 연결 해제` : '연결 해제'}
        </button>
      </div>
    </Popup>
  )
}

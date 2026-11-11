// 팝업: 어떤 결제인가요? — 고정지출과 비슷한 카드 결제가 여러 건일 때 하나 고르기 (10/21 결정, C-14 · C-16)
// props: item = 고정지출 (GET /api/budgets/fixed/ 의 한 건. item.evidenceSuggestion.candidates = 서버가 찾은 결제, 출금일과 가까운 순 최대 5건)
//        onSaved = 고른 뒤 목록 다시 받기
// 고르면 PATCH /api/budgets/fixed/<id>/ { evidenceTransactionId, evidenceDueId(계좌 회차가 있을 때) } → 그 결제를 고정비로 옮기고 가맹점을 기억 (C-12)
// '이 중에 없어요' = 요청 없음. 다음 달 새 결제가 오면 다시 물어본다
// 다른 고정지출에 이미 연결된 결제는 서버가 빼고 준다
// late = 미납 확인 필요 회차에서 열 때 (결정 G). 늦게 낸 출금(±3일 밖)을 고른다. 같은 PATCH, '아직 안 냈어요' = 요청 없음
import { useState } from 'react'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { won } from '../utils/money.js'
import { pickEvidence, shortDate } from '../utils/fixed.js'
import { toast } from '../utils/toast.js'

export default function EvidencePopup({ item, late = false, onSaved }) {
  const { closePopup } = usePopup()
  const { count, candidates, dueDate } = item.evidenceSuggestion
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const pick = async (id) => {
    if (saving) return
    setSaving(true)
    setError('')
    try {
      await pickEvidence(item, id)
      onSaved?.()
      closePopup()
      toast('거래를 연결했어요')
    } catch (err) {
      setError(err.fields?.evidenceTransactionId?.[0] ?? err.message)
      setSaving(false)
    }
  }

  return (
    <Popup label="결제 고르기">
      <h2>{late ? `${item.name}, 언제 냈나요?` : `${item.name}은(는) 어떤 결제인가요?`}</h2>
      <p className="mc-muted" style={{ margin: '4px 0 12px' }}>
        {late
          ? `${shortDate(dueDate)} 예정 · ${won(item.amount)} 뒤에 나간 비슷한 출금 ${count}건`
          : `매월 ${item.paymentDay}일 · ${won(item.amount)}과 비슷한 결제 ${count}건${count > candidates.length ? ` 중 가까운 ${candidates.length}건` : ''}`}
      </p>
      <div className="mc-lock-list">
        {candidates.map((t) => (
          <button type="button" className="mc-lock-opt" key={t.transactionId} disabled={saving} onClick={() => pick(t.transactionId)}>
            <span className="mc-grow">{shortDate(t.date)} {t.merchantName}</span><strong>{won(t.amount)}</strong>
          </button>
        ))}
        <button type="button" className="mc-lock-opt" disabled={saving} onClick={closePopup}><span className="mc-grow">{late ? '아직 안 냈어요' : '이 중에 없어요'}</span></button>
      </div>
      {error && <p className="mc-error" role="alert">{error}</p>}
    </Popup>
  )
}

// 카드 청구서 한 장 '앞으로 얼마를 더 내야 하나요?' 확인 (11/06) — 고정지출 확인 · 지킬 돈 · 홈 청구서 상세에서 같이 쓴다
// bill = 서버 bill_status (cardBills[].bills[] · 청구서 상세 paymentCheck · docs/api/cards.md '납부 확인')
// PUT /api/finance/card-bills/<billId>/payment-check/ { answer: unpaid|paid|partial|later, remainingAmount? }
// 거래를 고르지 않아도 저장된다. 자동으로 확인했으면(계좌 출금) 결과와 [수정]만. 사용자 확인은 '사용자 확인'으로 표시
// 실패하면 고른 답 · 입력 금액을 그대로 두고 오류를 보여 준다. [거래로 확인하기] = 보조 경로 (CardPaymentPopup)
import { useState } from 'react'
import MoneyInput from './MoneyInput.jsx'
import { usePopup } from '../popups/context.js'
import CardPaymentPopup from '../popups/CardPaymentPopup.jsx'
import { apiSend } from '../utils/api.js'
import { won } from '../utils/money.js'
import { shortDate } from '../utils/fixed.js'

const RECHECK = {
  bill_changed: '확인한 뒤 청구액이 바뀌었어요. 다시 확인해 주세요.',
  transactions_conflict: '확인한 내용과 계좌 출금 기록이 달라요. 다시 확인해 주세요.',
}

export default function CardBillCheck({ bill, issuerName, onSaved }) {
  const { openPopup } = usePopup()
  const [editing, setEditing] = useState(false)
  const [partial, setPartial] = useState(null)   // 일부 냈어요 입력 중인 금액 (숫자 문자열)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const month = bill.billingMonth ? `${Number(bill.billingMonth.slice(5))}월 ` : ''
  const title = `${issuerName} · ${month}청구액 ${won(bill.totalAmount)}`
  const transactions = () => openPopup(<CardPaymentPopup key={bill.billId} billId={bill.billId} onSaved={onSaved} />)

  const save = async (answer, remainingAmount) => {
    if (busy) return
    setBusy(true); setError('')
    try {
      await apiSend(`/api/finance/card-bills/${bill.billId}/payment-check/`, 'PUT',
        { answer, ...(answer === 'partial' ? { remainingAmount } : {}) })
      setEditing(false); setPartial(null)
      onSaved?.()
    } catch (err) {
      setError(err.fields ? Object.values(err.fields).flat().join(' ') : err.status ? err.message : '서버에 연결하지 못했어요. 다시 시도해 주세요.')
    } finally { setBusy(false) }
  }

  const head = <p style={{ margin: '0 0 4px' }}><strong>{title}</strong>{bill.dueDate && <span className="mc-muted"> · 결제일 {shortDate(bill.dueDate)}</span>}</p>
  if (bill.status === 'no_charge') return <div className="mc-bill-check">{head}<p className="mc-muted" style={{ margin: 0 }}>낼 금액이 없어요.</p></div>
  if (bill.status === 'total_mismatch') {
    // 원래 청구액을 모르면 '앞으로 낼 금액'도 물을 수 없다 (0원이라고 낼 금액 없음으로 보지 않음, 11/06)
    return <div className="mc-bill-check" role="note">{head}<p className="mc-muted" style={{ margin: 0 }}>카드사 청구 총액이 청구서 상세 합계와 달라 원래 청구액을 확인하고 있어요. 지금은 납부 금액을 정하지 않아요.</p></div>
  }
  const done = !editing && (bill.status === 'paid_by_transactions' || bill.status === 'user_confirmed')
  if (done) {
    return (
      <div className="mc-bill-check">
        {head}
        <div className="mc-row">
          <span>앞으로 낼 금액 <strong>{won(bill.remainingAmount)}</strong> · <span className="mc-muted">{bill.status === 'paid_by_transactions' ? '계좌 출금으로 확인' : '사용자 확인'}</span></span>
          <button type="button" className="mc-link" onClick={bill.status === 'paid_by_transactions' ? transactions : () => setEditing(true)}>수정</button>
        </div>
      </div>
    )
  }
  const later = bill.userCheck?.answer === 'later' && !editing
  return (
    <div className="mc-bill-check" role="group" aria-label={`${issuerName} 납부 확인`}>
      {head}
      <p style={{ margin: '0 0 8px' }}>{bill.status === 'recheck' ? RECHECK[bill.recheckReason] : later ? '나중에 확인하기로 했어요. 지금 확인해도 돼요.' : '이 카드값은 얼마나 납부하셨나요?'}</p>
      <div className="mc-inline" style={{ flexWrap: 'wrap' }}>
        <button type="button" className="mc-secondary" disabled={busy} onClick={() => save('unpaid')}>아직 안 냈어요</button>
        <button type="button" className="mc-secondary" disabled={busy} onClick={() => save('paid')}>전부 냈어요</button>
        <button type="button" className="mc-secondary" disabled={busy} aria-expanded={partial !== null} onClick={() => setPartial(partial ?? '')}>일부 냈어요</button>
        {!later && <button type="button" className="mc-link" disabled={busy} onClick={() => save('later')}>나중에 확인</button>}
      </div>
      {partial !== null && (
        <div className="mc-inline" style={{ marginTop: 8 }}>
          <label className="mc-field" style={{ flex: 1 }}>앞으로 더 낼 금액
            <MoneyInput value={partial} onChange={setPartial} aria-label="앞으로 더 낼 금액" />
          </label>
          <button type="button" className="mc-primary" disabled={busy || partial === ''} onClick={() => save('partial', Number(partial))}>{busy ? '저장 중…' : '저장'}</button>
        </div>
      )}
      {error && <p className="mc-error" role="alert">{error}</p>}
      <button type="button" className="mc-link" style={{ marginTop: 6 }} onClick={transactions}>거래로 확인하기</button>
      {editing && <button type="button" className="mc-link" onClick={() => { setEditing(false); setPartial(null); setError('') }}>그만두기</button>}
    </div>
  )
}

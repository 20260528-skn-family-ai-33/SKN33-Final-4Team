// 카드 청구서 한 장 '앞으로 얼마를 더 내야 하나요?' 확인 (11/06) — 고정지출 확인 · 지킬 돈 · 홈 청구서 상세에서 같이 쓴다
// bill = 서버 bill_status (cardBills[].bills[] · 청구서 상세 paymentCheck · docs/api/cards.md '납부 확인')
// PUT /api/finance/card-bills/<billId>/payment-check/ { answer: unpaid|paid|partial|later, remainingAmount? }
// 거래를 고르지 않아도 저장된다. 자동으로 확인했으면(계좌 출금) 결과와 [수정]만. 사용자 확인은 '사용자 확인'으로 표시
// 실패하면 고른 답 · 입력 금액을 그대로 두고 오류를 보여 준다. [거래로 확인하기] = 보조 경로 (CardPaymentPopup)
// 11/12 모양: 카드사 한 줄(아이콘 · 이름 · 청구월/결제일 · 오른쪽 청구액) → 질문 → 답 3개 한 줄 → 아래 작은 링크(거래로 확인 · 나중에 확인). 확인한 뒤엔 연한 상자 한 줄
import { useState } from 'react'
import Icon from './Icon.jsx'
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

  const head = <BillHead issuerName={issuerName} sub={`${month}청구${bill.dueDate ? ` · 결제일 ${shortDate(bill.dueDate)}` : ''}`} amount={bill.totalAmount} />
  if (bill.status === 'no_charge') return <div className="mc-bill-check">{head}<p className="mc-bill-q">낼 금액이 없어요.</p></div>
  if (bill.status === 'total_mismatch') {
    // 원래 청구액을 모르면 '앞으로 낼 금액'도 물을 수 없다 (0원이라고 낼 금액 없음으로 보지 않음, 11/06)
    return <div className="mc-bill-check" role="note">{head}<p className="mc-bill-q">카드사 청구 총액이 청구서 상세 합계와 달라 원래 청구액을 확인하고 있어요. 지금은 납부 금액을 정하지 않아요.</p></div>
  }
  const done = !editing && (bill.status === 'paid_by_transactions' || bill.status === 'user_confirmed')
  if (done) {
    return (
      <div className="mc-bill-check">
        {head}
        <div className="mc-bill-result">
          <span>앞으로 낼 금액 <strong>{won(bill.remainingAmount)}</strong></span>
          <span className="mc-bill-st is-confirmed">{bill.status === 'paid_by_transactions' ? '계좌 출금으로 확인' : '사용자 확인'}</span>
          <button type="button" className="mc-link" onClick={bill.status === 'paid_by_transactions' ? transactions : () => setEditing(true)}>수정</button>
        </div>
      </div>
    )
  }
  const later = bill.userCheck?.answer === 'later' && !editing
  return (
    <div className="mc-bill-check" role="group" aria-label={`${issuerName} 납부 확인`}>
      {head}
      <p className="mc-bill-q">{bill.status === 'recheck' ? RECHECK[bill.recheckReason] : later ? '나중에 확인하기로 했어요. 지금 확인해도 돼요.' : '이 카드값, 얼마나 냈나요?'}</p>
      <div className="mc-bill-answers">
        <button type="button" disabled={busy} onClick={() => save('unpaid')}>아직 안 냈어요</button>
        <button type="button" disabled={busy} onClick={() => save('paid')}>전부 냈어요</button>
        <button type="button" disabled={busy} aria-expanded={partial !== null} onClick={() => setPartial(partial ?? '')}>일부 냈어요</button>
      </div>
      {partial !== null && (
        <div className="mc-bill-partial">
          <label className="mc-field">앞으로 더 낼 금액
            <MoneyInput value={partial} onChange={setPartial} aria-label="앞으로 더 낼 금액" placeholder="0" />
          </label>
          <button type="button" className="mc-primary" disabled={busy || partial === ''} onClick={() => save('partial', Number(partial))}>{busy ? '저장 중…' : '저장'}</button>
        </div>
      )}
      {error && <p className="mc-error" role="alert">{error}</p>}
      <div className="mc-bill-links">
        <button type="button" className="mc-link" onClick={transactions}>거래로 확인하기</button>
        {editing ? <button type="button" className="mc-link" onClick={() => { setEditing(false); setPartial(null); setError('') }}>그만두기</button>
          : !later && <button type="button" className="mc-link" disabled={busy} onClick={() => save('later')}>나중에 확인</button>}
      </div>
    </div>
  )
}

// 카드사 한 줄 머리 (11/12): [카드 아이콘] 카드사 이름 + 작은 설명 · 오른쪽 청구액. 청구서가 없는 카드사(고정지출 확인)도 같은 모양으로 쓴다
export function BillHead({ issuerName, sub, amount = null }) {
  return (
    <div className="mc-bill-head">
      <span className="mc-tile" aria-hidden="true"><Icon name="CreditCard" /></span>
      <span className="mc-grow"><strong>{issuerName}</strong>{sub && <small>{sub}</small>}</span>
      {amount !== null && <strong className="mc-bill-amt">{won(amount)}</strong>}
    </div>
  )
}

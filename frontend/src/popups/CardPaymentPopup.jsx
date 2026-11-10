// 팝업: 카드 청구서 납부 거래 확인 — 저장된 내 계좌 출금 중 이 청구서를 낸 거래를 여러 건 골라 연결 · 연결 취소 (11/06)
// GET  /api/finance/card-bills/<billId>/payments/?from=&to=   청구서(bill) · 연결한 출금(linked) · 후보(items)
// POST 같은 주소 { transactionIds: [...], confirmReclassify }  · DELETE .../payments/<txId>/ 연결 취소 (자동으로 다시 붙지 않음)
// 대출과 달리 '다 냈어요'가 없다: 남은 금액 = 청구 총액 − 확인된 납부액 (서버 계산). 기본 기간은 결제일 35일 전부터 (선결제 · 분할 납부)
// 저장 실패하면 고른 출금은 그대로 두고 오류를 보여 준다. docs/api/cards.md
// props: billId = 카드 청구서 ID, onSaved = 연결 · 취소한 뒤 부를 함수 (고정지출 화면 새로고침)
import { useState } from 'react'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import PaymentTxLine from '../components/PaymentTxLine.jsx'
import { won } from '../utils/money.js'
import { catName } from '../utils/categories.js'
import { apiSend, newRequestKey, useApi } from '../utils/api.js'
import { CARD_PAY_STATE, TYPE, effectText, releasedText } from '../utils/paymentText.js'

const fromLabel = (r) => `${TYPE[r.fromType] ?? r.fromType}${r.fromCategory ? ` · ${catName(r.fromCategory)}` : ''}`
const errorText = (err, kept) => err.fields ? Object.values(err.fields).flat().join(' ')
  : err.status ? err.message : `서버에 연결하지 못했어요. ${kept} 다시 시도해 주세요.`

export default function CardPaymentPopup({ billId, onSaved }) {
  const { closePopup } = usePopup()
  const [range, setRange] = useState(null)
  const [draft, setDraft] = useState(null)
  const query = range ? `?from=${range.from}&to=${range.to}` : ''
  const { data, error, reload } = useApi(`/api/finance/card-bills/${billId}/payments/${query}`)
  const [picked, setPicked] = useState([])
  const [agree, setAgree] = useState(false)
  const [busy, setBusy] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [requestKey, setRequestKey] = useState(newRequestKey)
  const [done, setDone] = useState('')

  if (error && !data) return <Popup label="카드값 납부 확인"><p className="mc-error" role="alert">{error.status ? error.message : '납부 정보를 불러오지 못했어요.'}</p><button type="button" className="mc-secondary" onClick={() => (range ? setRange(null) : reload())}>{range ? '기본 기간으로 돌아가기' : '다시 시도'}</button></Popup>
  if (!data) return <Popup label="카드값 납부 확인"><p role="status">불러오는 중…</p></Popup>
  const { bill, linked, items = [] } = data
  const chosen = items.filter((t) => picked.includes(t.transactionId))
  const changes = chosen.filter((t) => t.reclassify)
  const needsAgree = changes.some((t) => t.reclassify.needsConfirm)
  const pickedSum = chosen.reduce((sum, t) => sum + t.amount, 0)

  const toggle = (id) => {
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]))
    setAgree(false); setSaveError(''); setRequestKey(newRequestKey())
  }
  const after = (message) => { setDone(message); setPicked([]); setAgree(false); setRequestKey(newRequestKey()); reload(); onSaved?.() }
  const send = async () => {
    if (busy || chosen.length === 0) return
    setBusy(true); setSaveError(''); setDone('')
    try {
      await apiSend(`/api/finance/card-bills/${billId}/payments/`, 'POST',
        { transactionIds: chosen.map((t) => t.transactionId), confirmReclassify: Boolean(needsAgree && agree) }, { requestKey })
      after(`출금 ${chosen.length}건을 이 청구서에 연결했어요.`)
    } catch (err) {
      setSaveError(errorText(err, '고른 출금은 그대로예요.'))
    } finally { setBusy(false) }
  }
  const unlink = async (t) => {
    if (busy) return
    setBusy(true); setSaveError(''); setDone('')
    try {
      const result = await apiSend(`/api/finance/card-bills/${billId}/payments/${t.transactionId}/`, 'DELETE')
      after(releasedText(result?.released))
    } catch (err) {
      setSaveError(errorText(err, '연결과 분류는 그대로예요.'))
    } finally { setBusy(false) }
  }
  const search = (e) => { e.preventDefault(); if (draft?.from && draft?.to) { setRange(draft); setPicked([]) } }

  return (
    <Popup label="카드값 납부 확인" sub>
      <div className="mc-eyebrow">카드값 납부 확인</div>
      <h1>{bill.issuerName ?? '카드'} 청구서</h1>
      <div className="mc-card">
        <div className="mc-keyvalue"><span>결제일</span><strong>{bill.dueDate ?? '확인 필요'}</strong></div>
        <div className="mc-keyvalue"><span>청구 총액</span><strong>{won(bill.totalAmount)}</strong></div>
        <div className="mc-keyvalue"><span>확인된 납부액</span><strong>{won(bill.paidAmount)}</strong></div>
        <div className="mc-keyvalue"><span>남은 납부액</span><strong>{won(bill.remainingAmount)}</strong></div>
        <div className="mc-keyvalue"><span>상태</span><strong>{CARD_PAY_STATE[bill.paymentState] ?? '낼 금액 없음'}</strong></div>
        {bill.reviewReason === 'due_date_unknown' && <p className="mc-note" role="note" style={{ paddingBottom: 0 }}>결제일 확인 필요 · 청구서에 결제일이 없어 출금을 찾을 기간을 정할 수 없어요. 카드 관리에서 청구서를 다시 불러와 주세요.</p>}
        {bill.reviewReason === 'payment_canceled' && <p className="mc-note" role="note" style={{ paddingBottom: 0 }}>연결한 출금이 취소됐어요. 실제로 낸 출금을 다시 확인해 주세요.</p>}
        {bill.paidAmount > bill.totalAmount && <p className="mc-note" style={{ paddingBottom: 0 }}>청구 총액보다 {won(bill.paidAmount - bill.totalAmount)} 더 연결됐어요. 다른 납부의 출금이 섞였는지 확인해 주세요.</p>}
      </div>

      {linked.length > 0 && <>
        <h3 className="mc-loan-h">연결한 출금</h3>
        <div className="mc-lock-list">{linked.map((t) => (
          <div key={t.transactionId} className="mc-lock-opt" style={{ flexWrap: 'wrap' }} aria-label={`연결한 출금 ${t.description ?? ''}`}>
            <PaymentTxLine t={t} />
            <small>{t.source === 'user' ? '직접 연결' : '자동 연결'}</small>
            <button type="button" className="mc-link" disabled={busy} onClick={() => unlink(t)}>연결 취소</button>
            {effectText(t.cancelEffect) && <small className="mc-muted" style={{ flexBasis: '100%' }}>{effectText(t.cancelEffect)} 다시 연결하기 전까지 자동으로 붙지 않아요.</small>}
          </div>))}
        </div>
      </>}

      {!bill.canLink
        ? <p className="mc-note">이 청구서에는 지금 출금을 연결할 수 없어요. 카드 관리에서 청구서를 다시 불러온 뒤 확인해 주세요. 연결한 출금은 위에서 취소할 수 있어요.</p>
        : <>
          <h3 className="mc-loan-h">내 계좌 출금 <small>{data.searchFrom} ~ {data.searchTo}</small></h3>
          <p className="mc-muted" style={{ marginTop: 0 }}>이 청구서를 낸 출금을 모두 골라 주세요. 나눠 냈다면 여러 건을 고를 수 있어요.</p>
          {items.length === 0 && <p className="mc-note">이 기간에 고를 수 있는 출금이 없어요. 기간을 넓혀 찾아보세요.</p>}
          <div className="mc-lock-list">{items.map((t) => (
            <button type="button" key={t.transactionId} className="mc-lock-opt" aria-pressed={picked.includes(t.transactionId)} disabled={busy} onClick={() => toggle(t.transactionId)}
              style={{ flexWrap: 'wrap' }}>
              <PaymentTxLine t={t} />
              {t.hints.otherIssuer && <small className="mc-muted" style={{ flexBasis: '100%' }}>적요에 다른 카드사 이름이 있어요</small>}
            </button>))}
          </div>
          <form onSubmit={search} className="mc-loan-2" aria-label="찾을 기간">
            <label className="mc-field">시작일<input type="date" value={draft?.from ?? data.searchFrom ?? ''} onChange={(e) => setDraft({ from: e.target.value, to: draft?.to ?? data.searchTo })} /></label>
            <label className="mc-field">끝일<input type="date" value={draft?.to ?? data.searchTo ?? ''} onChange={(e) => setDraft({ from: draft?.from ?? data.searchFrom, to: e.target.value })} /></label>
            <button type="submit" className="mc-secondary" disabled={busy}>이 기간으로 찾기</button>
          </form>
        </>}

      {chosen.length > 0 && <div className="mc-card" aria-label="고른 출금">
        <div className="mc-keyvalue"><span>고른 출금 {chosen.length}건</span><strong>{won(pickedSum)}</strong></div>
        <div className="mc-keyvalue"><span>연결하면 남은 납부액</span><strong>{won(Math.max(0, bill.remainingAmount - pickedSum))}</strong></div>
        {changes.length > 0 && (needsAgree
          ? <label className="mc-field mc-check"><input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
              분류가 바뀌어요: {changes.map((t) => `${t.description || '적요 없음'} ‘${fromLabel(t.reclassify)}’ → ‘카드값’`).join(', ')}. 소비 합계에서 빠져요.</label>
          : <p className="mc-note" style={{ padding: 0 }}>고른 이체가 ‘카드값’으로 바뀌어요. 연결을 취소하면 되돌려요.</p>)}
        <button type="button" className="mc-primary" disabled={busy || (needsAgree && !agree)} onClick={send}>{busy ? '저장 중…' : '이 청구서에 연결'}</button>
      </div>}
      {saveError && <p className="mc-error" role="alert">{saveError}</p>}
      {done && <p className="mc-note" role="status">{done}</p>}
      <button type="button" className="mc-link" onClick={closePopup}>닫기</button>
    </Popup>
  )
}

// 팝업: 대출 납부 거래 확인 — 저장된 내 계좌 출금 중 이번 회차에 낸 거래를 골라 '다 냈어요' · '일부만 냈어요'로 확인 (11/06)
// GET  /api/finance/loan-dues/<dueId>/payments/?from=&to=   회차 · 연결한 거래(linked) · 후보(items)
// POST 같은 주소 { transactionId, settlement: full|partial, confirmReclassify }  · DELETE .../payments/<txId>/ 연결 취소
//   연결한 출금의 cancelEffect.kind = choose(원래 분류를 모름, 예전 자동 연결)면 먼저 이체 · 소비(카테고리)를 고르게 하고
//   DELETE 본문 { restoreType, restoreCategory } 로 연결 해제와 분류 변경을 한 번에 (먼저 풀면 거래 화면에서 사라짐). 안내 문구는 응답 released 그대로
// 금액이 달라도 · 적요에 기관 이름이 없어도 후보로 보여 준다 (예정액 · 실제 금액 · 날짜 · 계좌 · 적요를 같이). docs/api/loans.md 12
// props: dueId = 대출 회차 ID, onSaved = 저장 · 취소한 뒤 부를 함수(홈 · 대출 화면 새로고침)
import { useState } from 'react'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { won } from '../utils/money.js'
import { toast } from '../utils/toast.js'
import { ETC, SPEND_CATS, catName } from '../utils/categories.js'
import { apiSend, newRequestKey, useApi } from '../utils/api.js'
import { TYPE, effectText, releasedText } from '../utils/paymentText.js'
import PaymentTxLine from '../components/PaymentTxLine.jsx'

const STATE = { unpaid: '아직 확인 안 됨', partial: '일부만 냈어요', paid: '이번 납부 완료', needs_review: '출금 확인 필요' }
const SETTLED = { full: '다 냈어요', partial: '일부', null: '자동 연결' }
const fromLabel = (r) => `${TYPE[r.fromType] ?? r.fromType}${r.fromCategory ? ` · ${catName(r.fromCategory)}` : ''}`
const PICK = [...SPEND_CATS, ETC]
// base = '예정액' · '남은 금액' (일부만 낸 뒤 다른 출금을 고르면 서버가 남은 금액과 비교해 준다, 11/9)
const diffText = (d, base = '예정액') => d === 0 ? `${base}과 같아요` : `${base}보다 ${won(Math.abs(d))} ${d > 0 ? '많아요' : '적어요'}`

export default function LoanPaymentPopup({ dueId, onSaved }) {
  const { closePopup } = usePopup()
  const [range, setRange] = useState(null)        // null = 기본 기간 (납부일 ±7일)
  const [draft, setDraft] = useState(null)        // 기간 입력 중인 값
  const query = range ? `?from=${range.from}&to=${range.to}` : ''
  const { data, error, reload } = useApi(`/api/finance/loan-dues/${dueId}/payments/${query}`)
  const [picked, setPicked] = useState(null)
  const [agree, setAgree] = useState(false)
  const [busy, setBusy] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [requestKey, setRequestKey] = useState(newRequestKey)
  const [releasing, setReleasing] = useState(null)   // 분류를 고르는 중인 연결 { id, type, category }

  if (error && !data) return <Popup label="대출 납부 거래 확인"><p className="mc-error" role="alert">{error.status ? error.message : '납부 정보를 불러오지 못했어요.'}</p><button type="button" className="mc-secondary" onClick={() => (range ? setRange(null) : reload())}>{range ? '기본 기간으로 돌아가기' : '다시 시도'}</button></Popup>
  if (!data) return <Popup label="대출 납부 거래 확인"><p role="status">불러오는 중…</p></Popup>
  const { due, linked, items = [] } = data
  const choice = [...linked, ...items].find((t) => t.transactionId === picked)
  const needsAgree = choice?.reclassify?.needsConfirm
  // 이 출금을 고르기 전 남은 금액 = 실제 − 차이 (서버 amountDifference 가 이미 연결한 다른 출금을 뺀 기준)
  const remaining = choice ? choice.amount - choice.amountDifference : null

  const pick = (id) => { setPicked(id); setAgree(false); setSaveError(''); setRequestKey(newRequestKey()) }
  const after = (message) => { toast(message); setPicked(null); setAgree(false); setRequestKey(newRequestKey()); reload(); onSaved?.() }
  const send = async (settlement) => {
    if (busy || !choice) return
    setBusy(true); setSaveError('')
    try {
      await apiSend(`/api/finance/loan-dues/${dueId}/payments/`, 'POST',
        { transactionId: choice.transactionId, settlement, confirmReclassify: Boolean(needsAgree && agree) }, { requestKey })
      after(settlement === 'full' ? '납부 완료로 저장했어요' : '일부 납부로 저장했어요')
    } catch (err) {
      setSaveError(err.fields ? Object.values(err.fields).flat().join(' ') : err.status ? err.message : '서버에 연결하지 못했어요. 고른 내용은 그대로예요. 다시 시도해 주세요.')
    } finally { setBusy(false) }
  }
  const unlink = async (t) => {
    if (busy) return
    const choose = t.cancelEffect?.kind === 'choose'
    if (choose && releasing?.id !== t.transactionId) { setReleasing({ id: t.transactionId, type: '', category: '' }); setSaveError(''); return }
    if (choose && (!releasing.type || (releasing.type === 'spending' && !releasing.category))) { setSaveError('이 거래를 무엇으로 둘지 골라 주세요.'); return }
    setBusy(true); setSaveError('')
    try {
      const body = choose ? { restoreType: releasing.type, ...(releasing.type === 'spending' ? { restoreCategory: releasing.category } : {}) } : undefined
      const result = await apiSend(`/api/finance/loan-dues/${dueId}/payments/${t.transactionId}/`, 'DELETE', body)
      setReleasing(null)
      after(releasedText(result?.released))  // 분류를 되돌렸는지는 그대로 알린다 (실제 복원 결과대로)
    } catch (err) {
      setSaveError(err.fields ? Object.values(err.fields).flat().join(' ') : err.status ? err.message : '서버에 연결하지 못했어요. 연결과 분류는 그대로예요. 다시 시도해 주세요.')
    } finally { setBusy(false) }
  }
  const search = (e) => { e.preventDefault(); if (draft?.from && draft?.to) { setRange(draft); setPicked(null) } }

  return (
    <Popup label="대출 납부 거래 확인" sub>
      <div className="mc-eyebrow">대출 납부 거래 확인</div>
      <h1>{due.name}</h1>
      <div className="mc-card">
        <div className="mc-keyvalue"><span>납부일</span><strong>{due.dueDate}</strong></div>
        <div className="mc-keyvalue"><span>예정액</span><strong>{won(due.expectedAmount)}</strong></div>
        <div className="mc-keyvalue"><span>연결한 출금 합계</span><strong>{won(due.paidAmount)}</strong></div>
        <div className="mc-keyvalue"><span>상태</span><strong>{STATE[due.paymentState] ?? '확인 필요'}</strong></div>
        <p className="mc-note" style={{ paddingBottom: 0 }}>‘다 냈어요’는 이번 회차만 완료예요. 대출 전체 상환이나 월 상환 예정액은 바뀌지 않아요.</p>
      </div>

      {linked.length > 0 && <>
        <h3 className="mc-loan-h">연결한 출금</h3>
        <div className="mc-lock-list">{linked.map((t) => (
          <div key={t.transactionId} className="mc-lock-opt mc-linked" aria-label={`연결한 출금 ${t.description ?? ''}`}>
            <div className="mc-linked-main"><PaymentTxLine t={t} /></div>
            <div className="mc-linked-act">
              <span className="mc-linked-tag">{SETTLED[t.settlement] ?? '일부'}</span>
              <button type="button" className="mc-link" disabled={busy} onClick={() => pick(t.transactionId)}>정정</button>
              <button type="button" className="mc-link" disabled={busy} onClick={() => unlink(t)}>연결 취소</button>
            </div>
            {effectText(t.cancelEffect) && <small className="mc-muted">{effectText(t.cancelEffect)}</small>}
            {releasing?.id === t.transactionId && <fieldset aria-label="취소한 뒤 분류" style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
              <label className="mc-field mc-check"><input type="radio" name="restore-type" checked={releasing.type === 'transfer'} onChange={() => setReleasing({ ...releasing, type: 'transfer', category: '' })} />남에게 보낸 이체</label>
              <label className="mc-field mc-check"><input type="radio" name="restore-type" checked={releasing.type === 'spending'} onChange={() => setReleasing({ ...releasing, type: 'spending' })} />소비</label>
              {releasing.type === 'spending' && <label className="mc-field">카테고리
                <select value={releasing.category} onChange={(e) => setReleasing({ ...releasing, category: e.target.value })}>
                  <option value="">고르기</option>{PICK.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
                </select></label>}
              <button type="button" className="mc-secondary" disabled={busy} onClick={() => unlink(t)}>연결 취소하고 이 분류로 두기</button>
              <button type="button" className="mc-link" disabled={busy} onClick={() => setReleasing(null)}>그만두기</button>
            </fieldset>}
          </div>))}
        </div>
      </>}

      <h3 className="mc-loan-h">내 계좌 출금 <small>{data.searchFrom} ~ {data.searchTo}</small></h3>
      {items.length === 0 && <p className="mc-note">이 기간에 고를 수 있는 출금이 없어요. 기간을 넓혀 찾아보세요.</p>}
      <div className="mc-lock-list">{items.map((t) => (
        <button type="button" key={t.transactionId} className="mc-lock-opt" aria-pressed={picked === t.transactionId} disabled={busy} onClick={() => pick(t.transactionId)}>
          <PaymentTxLine t={t} />
        </button>))}
      </div>
      <form onSubmit={search} className="mc-loan-2 mc-range" aria-label="찾을 기간">
        <label className="mc-field">시작일<input type="date" value={draft?.from ?? data.searchFrom} onChange={(e) => setDraft({ from: e.target.value, to: draft?.to ?? data.searchTo })} /></label>
        <label className="mc-field">끝일<input type="date" value={draft?.to ?? data.searchTo} onChange={(e) => setDraft({ from: draft?.from ?? data.searchFrom, to: e.target.value })} /></label>
        <button type="submit" className="mc-secondary" disabled={busy}>이 기간으로 찾기</button>
      </form>

      {choice && <div className="mc-card" aria-label="고른 출금">
        <div className="mc-keyvalue"><span>예정액</span><strong>{won(due.expectedAmount)}</strong></div>
        {remaining !== due.expectedAmount && <>
          <div className="mc-keyvalue"><span>이미 연결한 출금</span><strong>{won(due.expectedAmount - remaining)}</strong></div>
          <div className="mc-keyvalue"><span>남은 금액</span><strong>{won(remaining)}</strong></div>
        </>}
        <div className="mc-keyvalue"><span>{remaining !== due.expectedAmount ? '이번 출금' : '실제 출금'}</span><strong>{won(choice.amount)}</strong></div>
        <p className="mc-note" style={{ padding: 0 }}>{diffText(choice.amountDifference, remaining !== due.expectedAmount ? '남은 금액' : '예정액')}{choice.hints.nearDueDate ? '' : ' · 납부일과 멀어요'}</p>{/* 11/9: 맞는 근거(연결 계좌 등)는 후보 순서에만 쓰고 문구에서 뺌 — 주의할 것만 */}
        {choice.reclassify && (needsAgree
          ? <label className="mc-field mc-check"><input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
              ‘{fromLabel(choice.reclassify)}’ → ‘대출 상환’으로 바꿔요 (소비 합계에서 빠져요)</label>
          : <p className="mc-note" style={{ padding: 0 }}>‘{fromLabel(choice.reclassify)}’ → ‘대출 상환’으로 바꿔요</p>)}
        <button type="button" className="mc-primary" disabled={busy || (needsAgree && !agree)} onClick={() => send('full')}>이번 납부를 전부 냈어요</button>
        <button type="button" className="mc-secondary" disabled={busy || (needsAgree && !agree)} onClick={() => send('partial')}>일부만 냈어요</button>
      </div>}
      {saveError && <p className="mc-error" role="alert">{saveError}</p>}
      <button type="button" className="mc-link" onClick={closePopup}>닫기</button>
    </Popup>
  )
}

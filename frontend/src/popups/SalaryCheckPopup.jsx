// 팝업: 월급 확인 (R12) — 급여 구간의 입금을 골라 '월급이 맞아요 / 아니에요'. 분할 급여는 여러 건을 골라 합계로 확인
// GET  /api/budgets/salary/deposits/   구간(이번 회차 · 다음 회차 조기)별 입금 · 지금 자동 인식 기준
// POST /api/budgets/salary/decisions/  { transactionIds, decision: confirmed|rejected|none, usePattern }  · DELETE /api/budgets/salary/pattern/ 자동 인식 끄기
// 같은 구간에서만 함께 고를 수 있다. 확인하면 그 회차의 월급 = 고른 입금 목록 (docs/api/salary.md)
// props: onSaved = 저장 뒤 부를 함수 (홈 새로고침)
import { useState } from 'react'
import Popup from './Popup.jsx'
import { won } from '../utils/money.js'
import { apiSend, useApi } from '../utils/api.js'
import { toast } from '../utils/toast.js'
import PaymentTxLine from '../components/PaymentTxLine.jsx'

const TAG = { confirmed: '월급으로 확인함', auto: '자동 인식', rejected: '월급 아니라고 함', recheck: '거래 또는 확인 근거가 바뀌어서 다시 확인 필요' }
const mmdd = (iso) => `${iso.slice(5, 7)}.${iso.slice(8, 10)}`
// 월급으로 감지된 입금: 서버 후보 · 이미 월급으로 본 것 · 다시 확인할 것 (월급 아니라고 한 것은 뺀다)
const detected = (t) => t.isCandidate || ['confirmed', 'auto', 'recheck'].includes(t.decision)

export default function SalaryCheckPopup({ onSaved }) {
  const { data, error, reload } = useApi('/api/budgets/salary/deposits/')
  const [pick, setPick] = useState(null)        // { cycle, ids: [] } — 한 구간 안에서만
  const [usePattern, setUsePattern] = useState(false)
  const [busy, setBusy] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [showPattern, setShowPattern] = useState(false)  // 자동 인식 기준은 원할 때만 펼친다
  const [showAll, setShowAll] = useState(false)  // 감지된 입금이 있으면 처음엔 그것만 보여 준다

  if (error && !data) return <Popup label="월급 확인"><p className="mc-error" role="alert">{error.status ? error.message : '입금 목록을 불러오지 못했어요.'}</p><button type="button" className="mc-secondary" onClick={reload}>다시 시도</button></Popup>
  if (!data) return <Popup label="월급 확인"><p role="status">불러오는 중…</p></Popup>

  const all = data.windows.flatMap((w) => w.items)
  const picked = all.filter((t) => pick?.ids.includes(t.transactionId))
  const single = picked.length === 1 ? picked[0] : null
  const onlyDetected = !showAll && all.some(detected)
  const hiddenCount = onlyDetected ? all.filter((t) => !detected(t)).length : 0
  const windows = data.windows.map((w) => ({ w, shown: onlyDetected ? w.items.filter(detected) : w.items }))
    .filter(({ shown }) => !onlyDetected || shown.length > 0)
  const toggle = (w, t) => {
    // 다른 구간을 누르면 그 구간에서 지금 월급으로 인정된 입금부터 다시 고른다 (확인은 그 회차 목록 전체를 바꾼다)
    const base = pick?.cycle === w.payCycleOn ? pick.ids : w.items.filter((i) => i.decision === 'confirmed' || i.decision === 'auto').map((i) => i.transactionId)
    const ids = base.includes(t.transactionId) ? base.filter((id) => id !== t.transactionId) : [...base, t.transactionId]
    setPick({ cycle: w.payCycleOn, ids }); setSaveError('')
    const only = ids.length === 1 ? all.find((i) => i.transactionId === ids[0]) : null
    setUsePattern(Boolean(only?.isPattern))
  }
  const send = async (method, url, body, done) => {
    if (busy) return
    setBusy(true); setSaveError('')
    try {
      await apiSend(url, method, body)
      setPick(null); setUsePattern(false); toast(done); reload(); onSaved?.()  // 11/9: 창 안 문장 대신 위에 잠깐 뜨는 토스트
    } catch (err) {
      setSaveError(err.fields ? Object.values(err.fields).flat().join(' ') : err.status ? err.message : '서버에 연결하지 못했어요. 다시 시도해 주세요.')
    } finally { setBusy(false) }
  }
  const decide = (decision) => send('POST', '/api/budgets/salary/decisions/',
    { transactionIds: pick.ids, decision, ...(decision === 'confirmed' ? { usePattern } : {}) },
    decision === 'confirmed' ? '월급으로 저장했어요.' : decision === 'rejected' ? '월급이 아닌 입금으로 저장했어요.' : '판단을 지웠어요.')

  return (
    <Popup label="월급 확인" sub>
      <div className="mc-eyebrow">월급 확인</div>
      <h1>어느 입금이 월급인가요?</h1>

      {windows.map(({ w, shown }) => (
        <section key={w.payCycleOn} aria-label={`${mmdd(w.payCycleOn)} 월급`}>
          <h3 className="mc-loan-h">{mmdd(w.payCycleOn)} 회차 월급 <small>{mmdd(w.from)} ~ {mmdd(w.to)}</small></h3>
          {shown.length === 0 && <p className="mc-note">이 기간에 고를 수 있는 입금이 없어요.</p>}
          <div className="mc-lock-list">{shown.map((t) => (
            <button type="button" key={t.transactionId} className="mc-lock-opt" style={{ flexWrap: 'wrap' }} disabled={busy}
              aria-pressed={Boolean(pick?.ids.includes(t.transactionId))} onClick={() => toggle(w, t)}>
              <PaymentTxLine t={t} />
              {(t.decision || t.isCandidate) && <small className="mc-muted" style={{ flexBasis: '100%' }}>
                {t.decision ? TAG[t.decision] : '월급일 수 있어요 · 확인 필요'}{t.isPattern ? ' · 자동 인식 기준' : ''}
              </small>}
            </button>))}
          </div>
        </section>
      ))}
      {hiddenCount > 0 && <button type="button" className="mc-link" onClick={() => setShowAll(true)}>다른 입금 {hiddenCount}건도 보기 ›</button>}

      {picked.length > 0 && <div className="mc-card">
        <div className="mc-keyvalue"><span>고른 입금 {picked.length}건</span><strong>{won(picked.reduce((s, t) => s + t.amount, 0))}</strong></div>
        {single && !single.patternUnusableReason && (
          <label className="mc-field mc-check mc-salary-auto"><input type="checkbox" checked={usePattern} onChange={(e) => setUsePattern(e.target.checked)} />
            다음 달부터 비슷한 입금은 자동으로 월급으로 볼게요</label>)}
        {single?.patternUnusableReason && <p className="mc-note">{single.patternUnusableReason}. 다음 달에도 직접 확인해 주세요.</p>}
        {picked.length > 1 && <p className="mc-note">여러 건은 합계로 확인해요. 분할 급여는 자동 인식 기준으로 쓸 수 없어요.</p>}
        <button type="button" className="mc-primary" disabled={busy} onClick={() => decide('confirmed')}>월급이 맞아요</button>
        <button type="button" className="mc-secondary" disabled={busy} onClick={() => decide('rejected')}>월급이 아니에요</button>
        {picked.some((t) => t.decision === 'confirmed' || t.decision === 'rejected') &&
          <button type="button" className="mc-link" disabled={busy} onClick={() => decide('none')}>판단 지우기</button>}
      </div>}

      <button type="button" className="mc-link" aria-expanded={showPattern} onClick={() => setShowPattern((v) => !v)}>
        자동 인식 기준 {showPattern ? '접기' : '보기'}
      </button>
      {showPattern && (data.pattern ? <div className="mc-card">
        <div className="mc-lock-opt"><PaymentTxLine t={data.pattern} /></div>
        <p className="mc-note">같은 입금이 들어오면 월급으로 자동 인식해요.</p>
        <button type="button" className="mc-link" disabled={busy} onClick={() => send('DELETE', '/api/budgets/salary/pattern/', undefined, '자동 인식을 껐어요. 지난 확인 기록은 그대로예요.')}>자동 인식 끄기</button>
      </div> : <p className="mc-note">아직 없어요. 월급 한 건을 확인할 때 자동 인식을 켤 수 있어요.</p>)}

      {saveError && <p className="mc-error" role="alert">{saveError}</p>}
    </Popup>
  )
}

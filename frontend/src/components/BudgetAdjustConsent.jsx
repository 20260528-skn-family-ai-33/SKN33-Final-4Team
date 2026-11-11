import { useState } from 'react'
import { apiSend } from '../utils/api.js'

export default function BudgetAdjustConsent({ scope = 'adjustment', onChange }) {
  const [state, setState] = useState(null)
  const [open, setOpen] = useState(false)
  const [agreed, setAgreed] = useState(false)
  const [health, setHealth] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const label = scope === 'design' ? '예산 분배' : '예산 조정'
  const path = scope === 'design' ? '/api/budgets/ai-consent/' : '/api/budgets/adjustment-ai-consent/'
  const load = async () => {
    setOpen(true); setBusy(true); setError(''); setAgreed(false); setHealth(false)
    try { setState(await apiSend(path, 'GET')) } catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }
  const save = async (revoke = false) => {
    if (busy || !state || (!revoke && (!state.available || !agreed || !health))) return
    setBusy(true); setError('')
    try {
      const next = await apiSend(path, revoke ? 'DELETE' : 'PUT', revoke ? { confirm: true } : {
        agreed, healthAgreed: health, documentVersion: state.documentVersion, documentHash: state.documentHash,
        healthDocumentVersion: state.healthDocumentVersion, healthDocumentHash: state.healthDocumentHash,
      })
      setState(next); setAgreed(false); setHealth(false); onChange?.(next)
    } catch (err) {
      setError(err.message)
      if (err.code === 'document_outdated') { setState(null); setAgreed(false); setHealth(false) }
      if (err.code === 'budget_ai_unavailable') { setState({ ...state, available: false }); setAgreed(false); setHealth(false) }
    }
    finally { setBusy(false) }
  }
  return <section className="mc-card mc-budget-consent" aria-label={`${label} AI 전송 동의`}>
    <button type="button" className="mc-link" disabled={busy} onClick={() => open ? setOpen(false) : load()} aria-expanded={open}>
      {label} AI 동의 관리 {open ? '접기' : '열기'}
    </button>
    {open && <>
      <p className="mc-note">분배와 조정의 동의는 각각 받아요. 동의만으로 예산은 바뀌지 않아요.</p>
      {!state && !busy && <button type="button" className="mc-btn" onClick={load}>동의 문서 다시 불러오기</button>}
      {[state?.document, state?.healthDocument].filter(Boolean).map((doc) => <details key={doc.title}>
        <summary>{doc.title}</summary>
        {doc.sections.map((section) => <div key={section.h}><strong>{section.h}</strong>{section.items.map((item) => <p key={item}>{item}</p>)}</div>)}
      </details>)}
      {state?.available === false && <p className="mc-note">현재 이 AI 기능이 꺼져 있어 새 동의는 받지 않아요. 기존 동의는 철회할 수 있어요.</p>}
      {state?.status === 'granted' ? <>
        <p>이 기능의 현재 문서에 동의했어요.</p>
      </> : state?.available && <>
        <label><input type="checkbox" checked={agreed} disabled={busy} onChange={(e) => setAgreed(e.target.checked)} /> {state.document.label}</label>
        <label><input type="checkbox" checked={health} disabled={busy} onChange={(e) => setHealth(e.target.checked)} /> {state.healthDocument.label}</label>
        <button type="button" className="mc-btn" disabled={busy || !agreed || !health} onClick={() => save()}>이 기능의 AI 전송에 동의</button>
      </>}
      {(state?.canRevoke || state?.status === 'granted') && <button type="button" className="mc-btn" disabled={busy} onClick={() => save(true)}>이 기능의 AI 전송 동의 철회</button>}
      {error && <p role="alert" className="mc-error">{error}</p>}
      {busy && <p role="status">확인 중이에요.</p>}
    </>}
  </section>
}

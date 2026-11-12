// 예산 AI 동의만 관리한다. 체크하거나 저장해도 추천·외부 호출을 자동 실행하지 않는다.
// 동의 · 철회 결과는 화면 위에 잠깐 떴다 사라지는 안내(토스트)로 알린다. 철회는 안내를 본 뒤 [철회하기]를 한 번 더 눌러야 한다 (11/11)
import { useCallback, useEffect, useRef, useState } from 'react'
import { apiSend } from '../utils/api.js'
import { usePopup } from '../popups/context.js'
import { toast } from '../utils/toast.js'
import TermsPopup from '../popups/TermsPopup.jsx'

const URL = '/api/budgets/ai-consent/'
const BADGES = { granted: '동의함', revoked: '철회함', needs_renewal: '다시 동의 필요' }

// onChange(data): 동의 상태를 읽거나 바꿀 때마다 알려 준다 (11/12, 예산 짜기 전 동의 단계의 버튼 문구용)
export default function BudgetAIConsent({ onChange }) {
  const { openPopup } = usePopup()
  const [data, setData] = useState(null)
  const [checked, setChecked] = useState(false)
  const [healthChecked, setHealthChecked] = useState(false)
  const [busy, setBusy] = useState(false)
  const [asking, setAsking] = useState(false)  // 철회 확인 단계
  const [error, setError] = useState('')
  const working = useRef(false)
  const revokeOnly = data?.available !== true && ['granted', 'needs_renewal'].includes(data?.status)
  const load = useCallback(() => apiSend(URL, 'GET').then(next => {
      setData(next)
      setError('')
    }, () => {
      setError('예산 AI 동의 상태를 불러오지 못했어요. 기본 예산 추천은 이용할 수 있어요.')
    }), [])
  useEffect(() => { load() }, [load])
  const changed = useRef(onChange)
  changed.current = onChange
  useEffect(() => { if (data) changed.current?.(data) }, [data])
  const save = async () => {
    if (working.current || !data) return
    const revoke = data.status === 'granted' || revokeOnly
    if (!revoke && (!checked || !healthChecked)) return
    working.current = true
    setBusy(true)
    setError('')
    try {
      const body = revoke ? { confirm: true } : { agreed: true,
        documentVersion: data.documentVersion, documentHash: data.documentHash,
        healthAgreed: true, healthDocumentVersion: data.healthDocumentVersion,
        healthDocumentHash: data.healthDocumentHash }
      setData(await apiSend(URL, revoke ? 'DELETE' : 'PUT', body))
      setChecked(false)
      setHealthChecked(false)
      setAsking(false)
      toast(revoke ? '예산 AI 전송 동의를 철회했어요' : 'AI 전송에 동의했어요')
    } catch (err) {
      if (['document_outdated', 'budget_ai_unavailable'].includes(err.code)) {
        setChecked(false)
        setHealthChecked(false)
        await load()
      }
      setError(err.code === 'document_outdated' ? '동의 내용이 바뀌었어요. 다시 확인해 주세요.'
        : '동의 처리 결과를 확인하지 못했어요. 다시 확인해 주세요.')
    } finally {
      working.current = false
      setBusy(false)
    }
  }
  const errorNotice = error && <div role="alert"><p className="mc-error">{error}</p><button type="button" className="mc-link" disabled={busy}
    onClick={() => { setChecked(false); setHealthChecked(false); load() }}>동의 상태 다시 확인</button></div>
  if (!data) return errorNotice
  if (data?.available !== true && data && !revokeOnly) return null
  // 모양은 계좌 연결 · 회원가입 동의와 같다 (11/11): 카드 + 상태 배지 + [체크박스 · 동의 이름 · 보기] 줄. 문구와 동작은 그대로
  const granted = data.status === 'granted'
  const badge = revokeOnly ? null : BADGES[data.status]
  // 동의 이름 앞에 [필수] · [선택] — 서버 문서의 required 값 그대로 (회원가입 · 계좌 연결 동의 이름과 같은 표기)
  const kind = (doc) => doc.required === false ? '[선택]' : '[필수]'
  const openDoc = (doc, onAgree) => openPopup(<TermsPopup doc={doc} onAgree={granted || revokeOnly ? undefined : onAgree} />)
  const rows = [
    ['전송 항목·보관 안내 보기', '예산 AI 외부 전송에 동의해요', data.document, checked, setChecked],
    ['건강 관련 요약 처리 안내 보기', '의료·건강 요약의 AI 처리에 별도로 동의해요', data.healthDocument, healthChecked, setHealthChecked],
  ]
  return <section className="mc-card mc-consent-card mc-ai-consent" aria-label="예산 AI 전송 동의">
    <div className="mc-row">
      <h2>예산 AI 전송 동의 · 선택</h2>
      {badge && <span className="mc-badge">{badge}</span>}
    </div>
    <p className="mc-muted">{revokeOnly ? '외부 AI 추천은 현재 중단되어 있어요. 기존 전송 동의는 철회할 수 있어요.'
      : '동의하면 카테고리별 소비 요약과 예산 설정을 OpenAI로 보낼 수 있어요. 거절하거나 철회해도 기본 예산 추천을 이용할 수 있어요.'}</p>
    {granted || revokeOnly ? <>
      <div className="mc-ai-consent-docs">
        {rows.map(([open, , doc]) => <div className="mc-terms-row" key={open}>
          <button type="button" className="mc-terms-open" aria-haspopup="dialog" aria-label={open} onClick={() => openDoc(doc)}>
            {open.replace(/ 보기$/, '')}<span>보기</span>
          </button>
        </div>)}
      </div>
      {asking ? <>
        <p className="mc-note">철회해도 승인한 예산과 금융자료는 그대로 유지해요.</p>
        <button type="button" className="mc-secondary" disabled={busy} onClick={save}>{busy ? '처리 중…' : '철회하기'}</button>
        <button type="button" className="mc-link" disabled={busy} onClick={() => setAsking(false)}>취소</button>
      </> : <button type="button" className="mc-link" disabled={busy} onClick={() => setAsking(true)}>예산 AI 전송 동의 철회</button>}
    </> : <>
      {data.status === 'needs_renewal' && <p className="mc-note">동의 내용이 바뀌어 다시 확인이 필요해요.</p>}
      {rows.map(([open, label, doc, value, set]) => <div className="mc-terms-row" key={open}>
        <input type="checkbox" aria-label={`${kind(doc)} ${label}`} checked={value} disabled={busy} onChange={event => set(event.target.checked)} />
        <button type="button" className="mc-terms-open" aria-haspopup="dialog" aria-label={open} onClick={() => openDoc(doc, () => set(true))}>
          {kind(doc)} {label}<span>보기</span>
        </button>
      </div>)}
      <p className="mc-note">두 항목에 동의하면 외부 AI 추천을 사용할 수 있어요. 동의하지 않아도 기본 추천은 그대로 이용해요.</p>
      <button type="button" className="mc-secondary" disabled={busy || !checked || !healthChecked} onClick={save}>동의 저장</button>
    </>}
    {errorNotice}
  </section>
}

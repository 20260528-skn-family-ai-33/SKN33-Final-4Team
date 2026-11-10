// 예산 AI 동의만 관리한다. 체크하거나 저장해도 추천·외부 호출을 자동 실행하지 않는다.
import { useCallback, useEffect, useRef, useState } from 'react'
import { apiSend } from '../utils/api.js'
import { usePopup } from '../popups/context.js'
import TermsPopup from '../popups/TermsPopup.jsx'

const URL = '/api/budgets/ai-consent/'

export default function BudgetAIConsent() {
  const { openPopup } = usePopup()
  const [data, setData] = useState(null)
  const [checked, setChecked] = useState(false)
  const [healthChecked, setHealthChecked] = useState(false)
  const [busy, setBusy] = useState(false)
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
  const errorNotice = error && <div role="alert"><p>{error}</p><button type="button" className="mc-link" disabled={busy}
    onClick={() => { setChecked(false); setHealthChecked(false); load() }}>동의 상태 다시 확인</button></div>
  if (!data) return errorNotice
  if (data?.available !== true && data && !revokeOnly) return null
  return <section className="mc-note" aria-label="예산 AI 전송 동의">
    <h3>예산 AI 전송 동의 · 선택</h3>
    <p>{revokeOnly ? '외부 AI 추천은 현재 중단되어 있어요. 기존 전송 동의는 철회할 수 있어요.'
      : '동의하면 카테고리별 소비 요약과 예산 설정을 OpenAI로 보낼 수 있어요. 거절하거나 철회해도 기본 예산 추천을 이용할 수 있어요.'}</p>
    {data && <>
      <button type="button" className="mc-link" onClick={() => openPopup(<TermsPopup doc={data.document}
        onAgree={revokeOnly ? undefined : () => setChecked(true)} />)}>전송 항목·보관 안내 보기</button>
      <button type="button" className="mc-link" onClick={() => openPopup(<TermsPopup doc={data.healthDocument}
        onAgree={revokeOnly ? undefined : () => setHealthChecked(true)} />)}>건강 관련 요약 처리 안내 보기</button>
      {data.status === 'granted' || revokeOnly ? <>
        {!revokeOnly && <p role="status">예산 AI 전송에 동의했어요.</p>}
        <button type="button" className="mc-link" disabled={busy} onClick={save}>예산 AI 전송 동의 철회</button>
        <p>철회해도 승인한 예산과 금융자료는 그대로 유지해요.</p>
      </> : <>
        {data.status === 'needs_renewal' && <p>동의 내용이 바뀌어 다시 확인이 필요해요.</p>}
        {data.status === 'revoked' && <p role="status">예산 AI 전송 동의를 철회했어요.</p>}
        <label><input type="checkbox" checked={checked} disabled={busy}
          onChange={event => setChecked(event.target.checked)} />예산 AI 외부 전송에 동의해요</label>
        <label><input type="checkbox" checked={healthChecked} disabled={busy}
          onChange={event => setHealthChecked(event.target.checked)} />의료·건강 요약의 AI 처리에 별도로 동의해요</label>
        <p>두 항목에 동의하면 외부 AI 추천을 사용할 수 있어요. 동의하지 않아도 기본 추천은 그대로 이용해요.</p>
        <button type="button" className="mc-link" disabled={busy || !checked || !healthChecked} onClick={save}>동의 저장</button>
      </>}
    </>}
    {errorNotice}
  </section>
}

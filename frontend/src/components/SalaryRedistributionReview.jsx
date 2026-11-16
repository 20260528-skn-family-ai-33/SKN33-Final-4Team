import { useRef, useState } from 'react'
import Layout from './Layout.jsx'
import ApiStatus from './ApiStatus.jsx'
import BudgetAdjustment, { BudgetChanges } from './BudgetAdjustment.jsx'
import SalaryReviewActions from './SalaryReviewActions.jsx'
import BudgetApprovalPopup from '../popups/BudgetApprovalPopup.jsx'
import { usePopup } from '../popups/context.js'
import { apiSend, newRequestKey, useApi } from '../utils/api.js'
import { won } from '../utils/money.js'

export default function SalaryRedistributionReview({ context, fallback }) {
  const conversationId = context.budgetReturn?.conversationId
  const path = `/api/budgets/salary-review/${conversationId ? `?conversationId=${encodeURIComponent(conversationId)}` : ''}`
  const { data, error: loadError, reload } = useApi(path)
  const [percent, setPercent] = useState(null)
  const [preview, setPreview] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [approval, setApproval] = useState(null)
  const [approved, setApproved] = useState(false)
  const inFlight = useRef(false)
  const request = useRef(null)
  const { openPopup } = usePopup()

  if (data && !data.proposal && ['no_budget', 'no_increase'].includes(data.status)) return fallback
  if (!data) return <Layout title="월급 변경 후 예산 검토" showBack>
    <ApiStatus error={loadError} onRetry={reload} />
    <SalaryReviewActions context={context} note={Boolean(loadError)} />
  </Layout>

  const value = percent ?? String(data.savingPercent ?? '')
  const valid = value.trim() !== '' && Number.isInteger(Number(value)) && Number(value) >= 0 && Number(value) <= 100
  const ready = data.status === 'ready'
  const shown = preview ?? (data.proposal ? data : null)
  const proposal = shown?.proposal
  const saved = approved || proposal?.state === 'saved'
  const previewCurrent = proposal && shown.basisHash === data.basisHash && shown.savingPercent === Number(value)

  const calculate = async (event) => {
    event.preventDefault()
    if (!ready || !valid || inFlight.current || approval || saved) return
    const body = { basisHash: data.basisHash, savingPercent: Number(value), ...(conversationId ? { conversationId } : {}) }
    if (!request.current || JSON.stringify(request.current.body) !== JSON.stringify(body)) {
      request.current = { body, key: newRequestKey() }
    }
    inFlight.current = true
    setBusy(true)
    setError('')
    try {
      const result = await apiSend('/api/budgets/salary-review/', 'POST', body, { requestKey: request.current.key })
      if (!result?.proposal?.proposalId) throw new Error(result?.message || '예산 미리보기를 확인하지 못했어요. 다시 계산해 주세요.')
      setPreview({ ...result, basisHash: data.basisHash, savingPercent: Number(value) })
    } catch (err) {
      setError(err.fields?.savingPercent?.[0] ?? err.message)
      if (err.status >= 400 && err.status < 500 && err.status !== 408) request.current = null
      if (err.code === 'expired' || err.fields?.basisHash) reload()
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }

  const confirm = () => {
    if (!previewCurrent || busy || saved || proposal.canApprove === false) return
    openPopup(<BudgetApprovalPopup proposal={proposal} request={approval}
      onPending={setApproval}
      onApproved={(result) => { setPreview({ ...shown, proposal: result }); setApproval(null); setApproved(true) }}
      onRejected={(_, err, ended) => {
        setApproval(null)
        setError(err.message)
        if (ended) { setPreview(null); request.current = null; reload() }
      }} />)
  }

  const stage = saved ? 'saved' : previewCurrent ? 'preview' : 'pending'
  return <Layout title="월급 변경 후 예산 검토" showBack>
    <h1>{saved ? proposal.effectiveOn ? '다음 주기 예산을 예약했어요' : '새 예산을 반영했어요' : '늘어난 월급을 나눠 볼까요?'}</h1>
    {/* 11/16 UI 정리: 같은 뜻의 안내(월급 저장됨 · 기존 예산 유지 · 이번 예산 그대로)를 한 줄로 */}
    {!saved && <p className="mc-note mc-sr-sub">{data.effectiveOn
      ? `${data.effectiveOn}부터의 예산을 검토해요. 이번 예산은 그대로예요.`
      : '월급은 저장됐어요. 새 안을 확정하기 전까지 기존 예산은 그대로예요.'}</p>}
    {Number.isFinite(data.incomeBefore) && Number.isFinite(data.incomeAfter) && <section className="mc-card mc-sr-flow" aria-label="월급 증가분 배분 순서">
      <div className="mc-sr-income">
        <span>월 실수령액 {won(data.incomeBefore)} → {won(data.incomeAfter)}</span>
        <div className="mc-keyvalue mc-sr-increase"><span>늘어난 금액</span><strong>{won(data.increase)}</strong></div>
      </div>
      <ol className="mc-sr-steps">
        {data.requestedAmount > 0 && <li>
          <div className="mc-keyvalue"><span>기존 요청에 먼저 배정</span><strong>{won(data.requestedAmount)}</strong></div>
          {data.requestChanges?.length > 0 && <BudgetChanges changes={data.requestChanges} />}
        </li>}
        <li><div className="mc-keyvalue"><span>추가로 나눌 금액</span><strong>{Number.isFinite(data.remainingAmount) ? won(data.remainingAmount) : '요청 확인 후 계산'}</strong></div></li>
      </ol>
    </section>}
    {data.message && <p className={ready ? 'mc-note' : 'mc-error'} role={ready ? 'note' : 'alert'}>{data.message}</p>}
    {ready && !saved && <form className="mc-form mc-sr-form" onSubmit={calculate}>
      <label className="mc-field" htmlFor="salary-review-saving">남은 증가분 중 저축할 비율</label>
      <div className="mc-cat-input-wrap">
        <input id="salary-review-saving" className="mc-cat-input" type="number" inputMode="numeric" min="0" max="100" step="1"
          value={value} disabled={busy || Boolean(approval)} aria-describedby="salary-review-saving-help"
          onChange={(event) => { setPercent(event.target.value); setError('') }} />
        <span>%</span>
      </div>
      <p id="salary-review-saving-help" className="mc-note">지금 승인된 예산의 저축 비율은 {data.previousSavingPercent ?? data.savingPercent}%예요.</p>
      <details className="mc-sr-how">
        <summary>어떻게 나누나요?</summary>
        <p>고정지출을 뺀 금액 기준으로, 보관한 요청을 먼저 반영해요. 저축을 정한 뒤 남은 금액은 기존 생활비·월 머니쿠션·여윳돈의 비율로 나누고, 직접 요청한 항목의 금액은 그대로 둬요.</p>
      </details>
      <button type="submit" className="mc-primary" disabled={!valid || busy || Boolean(approval)}>{busy ? '계산하는 중…' : '이 비율로 예산 미리보기'}</button>
    </form>}
    {error && <p className="mc-error" role="alert">{error}</p>}
    {previewCurrent && <BudgetAdjustment content={{ budgetAdjustment: {
      status: 'replied', canApprove: !saved && proposal.canApprove !== false, proposal, embedded: true,
    } }} active={!busy && !saved} onApprove={confirm} />}
    {approval && !saved && <p className="mc-note" role="status">확정 결과를 아직 확인하지 못했어요. 같은 변경안으로 다시 확인해 주세요.</p>}
    <SalaryReviewActions context={context} busy={busy} stage={ready || saved ? stage : undefined} />
  </Layout>
}

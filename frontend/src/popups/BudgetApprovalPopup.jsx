import { useRef, useState } from 'react'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { BudgetChanges, BudgetTransfers, BudgetSettings, RecoveryAllocations } from '../components/BudgetAdjustment.jsx'
import { allocationName } from '../utils/adjust.js'
import { won } from '../utils/money.js'
import { apiSend, newRequestKey } from '../utils/api.js'
import { approvalPath } from '../utils/budgetApproval.js'

export default function BudgetApprovalPopup({ proposal, request: previous, onPending, onApproved, onRejected }) {
  const { closePopup } = usePopup()
  const [keep, setKeep] = useState(previous?.body.keepForNextCycle ?? Boolean(proposal.keepForNextCycle))
  const [pending, setPending] = useState(previous ?? null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [rejected, setRejected] = useState(false)
  const inFlight = useRef(false)
  const items = proposal.persistenceItems ?? []

  const approve = async () => {
    if (inFlight.current || rejected) return
    const request = pending ?? {
      path: approvalPath(proposal),
      body: { keepForNextCycle: proposal.effectiveOn ? false : keep }, key: newRequestKey(), proposal,
    }
    inFlight.current = true
    setBusy(true)
    setPending(request)
    setError('')
    try {
      onPending(request)
      const saved = await apiSend(request.path, 'POST', request.body, { requestKey: request.key })
      if (saved?.adjustment?.proposalId !== proposal.proposalId || saved.adjustment.state !== 'saved' || !saved.adjustment.savedRef) {
        throw new Error('확정 결과를 정상적으로 받지 못했어요.')
      }
      onApproved(saved.adjustment)
      closePopup()
    } catch (err) {
      if (err.status >= 400 && err.status < 500 && err.status !== 408) {
        setPending(null)
        const ended = err.code === 'expired' || err.status === 404 || err.code === 'idempotency_conflict'
        setRejected(ended)
        onRejected(proposal, err, ended)
      }
      setError(err.code === 'expired' && !proposal.sourceTaskId
        ? '조건이 바뀌어 이 변경안이 만료됐어요. 닫은 뒤 조정을 다시 요청해 주세요.' : err.message)
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }

  return <Popup label="예산 확정 확인" sub>
    <h1>{proposal.effectiveOn ? '다음 주기 변경을 예약할까요?' : '이 예산으로 바꿀까요?'}</h1>
    <p className="mc-muted">{proposal.effectiveOn ? `${proposal.effectiveOn}부터 ${proposal.oneOff ? '해당 주기의 예산에만' : '월급·배분 설정에'} 반영해요. 이번 예산은 그대로예요.` : '확정하면 아래 금액과 설정을 반영해요.'}</p>
    {proposal.effectiveOn && <p className="mc-muted">기존에 예약한 변경이 있으면 이번 변경으로 교체해요.</p>}
    {proposal.oneOff && <p>이번 계획은 해당 주기에만 적용해요. 평소 저축·생활비 배분 설정은 바꾸지 않아요.</p>}
    {proposal.expense && <p>따로 마련한 돈 {won(proposal.expense.setAside)} + 예산에서 마련할 돈 {won(proposal.expense.additional)} = 지출 예정액 {won(proposal.expense.total)}</p>}
    {proposal.incomeChange && proposal.incomeChange.before !== proposal.incomeChange.after && <p>월 실수령액 {won(proposal.incomeChange.before)} → <strong>{won(proposal.incomeChange.after)}</strong></p>}
    <BudgetTransfers transfers={proposal.transfers} />
    <BudgetChanges changes={proposal.changes} />
    {(proposal.shortfalls ?? []).map((row) => <p key={row.category} className="mc-budget-shortfall">{allocationName(row.category)}은 {won(row.appliedAmount)}만 반영하며 {won(row.missingAmount)}은 부족한 채로 남아요.</p>)}
    <BudgetSettings proposal={proposal} />
    <RecoveryAllocations proposal={proposal} />
    {!proposal.effectiveOn && items.length > 0 && <div className="mc-budget-keep">
      <label><input type="checkbox" checked={keep} disabled={busy || pending !== null || rejected} onChange={(e) => setKeep(e.target.checked)} /> 다음 달에도 이 금액 유지</label>
      <p>{proposal.sourceTaskId ? '선택하면 아래 생활비·저축·월 쿠션 금액을 다음 예산의 설정에도 반영해요.'
        : '직접 요청한 항목만 다음 예산의 목표로 저장해요. 잠금은 아니에요.'}</p>
      {items.map((item) => <div className="mc-keyvalue" key={item.category}><span>{allocationName(item.category)}</span><strong>{won(item.amount)}</strong></div>)}
    </div>}
    {error && <p className="mc-error" role="alert">{error}</p>}
    {pending && !busy && <p className="mc-note">저장 여부를 아직 확인하지 못했어요. 같은 요청으로 다시 확인해 주세요.</p>}
    {!rejected && <button type="button" className="mc-primary" disabled={busy} onClick={approve}>{busy ? '확정하는 중…' : pending ? '같은 요청으로 다시 확인' : proposal.effectiveOn ? '예약 확정' : '예산 확정'}</button>}
    <button type="button" className="mc-btn" onClick={closePopup}>{rejected ? '닫고 다시 조정하기' : '닫기'}</button>
  </Popup>
}

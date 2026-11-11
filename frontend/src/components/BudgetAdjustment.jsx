// 금액·추천 근거는 서버 검수 결과 그대로 표시한다. 펼침은 조회·선택·저장을 호출하지 않는다.
// 답변 전체를 다른 답변과 같은 말풍선(mc-bubble) 안에 그린다 (11/11). 모양은 styles/chat.css
import { adjustSentence, allocationName, ENDED_ADJUSTMENTS as TERMINAL } from '../utils/adjust.js'
import { useState } from 'react'
import { won } from '../utils/money.js'
import MoneyInput from './MoneyInput.jsx'

const PLAN_STATUS = { ready: '반영 가능', partial: '일부 반영', needs_destination: '받을 항목 선택 필요',
  needs_source: '재원 선택 필요', needs_data: '자료 확인 필요', needs_policy: '준비 중',
  needs_confirmation: '금액 단위 확인 필요', infeasible: '현재 이용 불가', unchanged: '변경 없음' }

export function BudgetChanges({ changes = [] }) {
  return <dl className="mc-budget-changes">{changes.map((row) => (
    <div key={row.category}>
      <dt>{allocationName(row.category)}</dt>
      <dd>{won(row.before)} <span aria-label="변경 후">→</span> <strong>{won(row.after)}</strong>
        {row.diff !== 0 && row.diff != null && <small>{row.diff > 0 ? '+' : '−'}{won(Math.abs(row.diff))}</small>}
      </dd>
    </div>
  ))}</dl>
}

export function BudgetTransfers({ transfers = [] }) {
  if (!transfers.length) return null
  return <div className="mc-budget-transfers" aria-label="돈이 옮겨가는 내역">
    {transfers.map((row, index) => <p key={index}>{allocationName(row.from)} → {allocationName(row.to)} <strong>{won(row.amount)}</strong></p>)}
  </div>
}

export function RecoveryAllocations({ proposal }) {
  if (!proposal.sourceTaskId) return null
  return <details className="mc-budget-details"><summary>카테고리별 배정액</summary>
    <dl className="mc-budget-changes">
      {Object.entries(proposal.allocations ?? {}).map(([category, amount]) =>
        <div key={category}><dt>{allocationName(category)}</dt><dd>{won(amount)}</dd></div>)}
    </dl>
  </details>
}

function Shortfalls({ rows = [] }) {
  return rows.map((row) => <p key={row.category} className="mc-budget-shortfall">
    {allocationName(row.category)} 요청 {won(row.requestedAmount)} · 가능 {won(row.appliedAmount)} · <strong>부족 {won(row.missingAmount)}</strong>
  </p>)
}

export function BudgetSettings({ proposal }) {
  return <>
    {(proposal.unlockCategories ?? []).map((code) => <p key={code} className="mc-note">승인 시 {allocationName(code)} 잠금 해제 · 다음 주기에도 이어져요.</p>)}
    {(proposal.targetItems ?? []).filter((item) => item.amount === null).map((item) =>
      <p key={item.category} className="mc-note">승인 시 {allocationName(item.category)} 유지 목표 해제</p>)}
  </>
}

function Explanation({ value, detail }) {
  if (!value) return null
  const lines = [...new Set([value.why, value.effect, value.next].filter(Boolean))]
  return <>
    {value.brief && <p className="mc-budget-reason">{value.brief}</p>}
    {lines.length > 0 && <details className="mc-budget-details" open={detail}><summary>이유 자세히 보기</summary>
      {lines.map((line) => <p key={line}>{line}</p>)}
    </details>}
  </>
}

function Plan({ plan, choice, active, onPick, detail }) {
  const [expanded, setExpanded] = useState(Boolean(detail))
  const changes = plan.result?.changes ?? []
  const headline = changes.filter((row) => row.direct || ['SAVINGS', 'CUSHION_MONTHLY', 'SPARE'].includes(row.category))
  const reason = plan.assessment?.reasons?.[0] ?? plan.assessment?.cautions?.[0] ?? plan.lines?.[0]
  const zeroes = (plan.assessment?.facts ?? []).filter((fact) => (fact.availableAfter ?? fact.remaining) === 0)
  return <article className={`mc-budget-plan${plan.recommended ? ' is-recommended' : ''}`} aria-label={plan.label}>
    <div className="mc-budget-plan-head"><h3>{plan.label}</h3><span>{plan.recommended ? '추천' : plan.status === 'partial' ? '일부만 가능' : '선택 가능'}</span></div>
    <p className="mc-budget-outcome">{PLAN_STATUS[plan.status] ?? '확인 필요'}{['ready', 'partial'].includes(plan.status) && ` · ${won(plan.amount)}`}</p>
    {plan.maximum != null && <p className="mc-note">이 방법으로 옮길 수 있는 최대 금액 {won(plan.maximum)}</p>}
    <BudgetChanges changes={headline} />
    <BudgetTransfers transfers={plan.result?.transfers} />
    <Shortfalls rows={plan.result?.shortfalls} />
    {reason && <p className="mc-budget-reason">{reason}</p>}
    {zeroes.map((fact) => <p className="mc-budget-warning" key={fact.category}>
      주의: {allocationName(fact.category)} {fact.availableAfter != null ? '가용액' : '남은 예산'} 0원
    </p>)}
    {(plan.lines ?? []).filter((line) => line.includes('비교 재원에서 제외')).map((line) => <p className="mc-budget-warning" key={line}>{line}</p>)}
    <details className="mc-budget-details" open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary>{expanded ? '예산 변화 접기' : '예산 변화 보기'}</summary>
      <BudgetChanges changes={changes} />
      {(plan.assessment?.reasons ?? []).map((line) => <p key={line}>{line}</p>)}
      {(plan.assessment?.cautions ?? []).filter((line) => line !== reason).map((line) => <p key={line}>{line}</p>)}
    </details>
    {choice && <button type="button" className="mc-btn mc-budget-select" disabled={!active} aria-label={`${plan.label} 선택`} onClick={() => onPick(choice)}>이 안 선택</button>}
  </article>
}

function Comparison({ comparison, choices, active, onPick, readOnly = false, detail }) {
  const plans = (comparison.plans ?? []).filter((plan) => ['ready', 'partial'].includes(plan.status) && plan.result?.changes?.length)
  if (!plans.length) return null
  return <section className="mc-budget-comparison" aria-label={readOnly ? '다른 범위 예산 비교' : '세 가지 조정안 비교'}>
    <h2>{readOnly ? '다른 방법으로 바꾸면 이렇게 돼요' : '바뀌는 예산 미리보기'}</h2>
    <p className="mc-budget-outcome">{comparison.amountWasGiven ? '요청한 금액' : comparison.basisKind === 'forecast_shortfall' ? '최근 지출을 참고해 제안하는 금액' : '옮길 수 있는 범위에서 계산한 예시'} {won(comparison.basisAmount)}</p>
    {!comparison.amountWasGiven && comparison.basisKind !== 'forecast_shortfall' && <p className="mc-budget-reason">현재 예산에서 옮길 수 있는 돈으로 만든 예시예요. 원하는 금액으로 바꿀 수 있어요.</p>}
    {readOnly && <p className="mc-budget-reason">비교만 해요. 선택한 재원은 그대로예요.</p>}
    {plans.map((plan) => <Plan key={plan.id} plan={plan} active={active} onPick={onPick} detail={detail}
      choice={readOnly ? null : choices?.find((choice) => choice.value === `plan_${plan.id}` || choice.value === `financial_${plan.id}`)} />)}
    {!comparison.recommendation?.planId && comparison.recommendation?.reasons?.[0] &&
      <p className="mc-budget-reason">{comparison.recommendation.reasons[0]}</p>}
  </section>
}

function AdjustmentAmount({ value, active, onSend }) {
  const [digits, setDigits] = useState('')
  const amount = Number(digits)
  const direction = value.direction === 'up' ? '늘릴' : '줄일'
  const maximum = Number.isSafeInteger(value.maxAmount) && value.maxAmount >= 0 ? value.maxAmount : value.direction === 'down' ? 0 : 9999999999999
  const changeAmount = (next) => setDigits(next === '' ? '' : String(Math.min(Number(next), maximum)))
  const valid = Number.isSafeInteger(amount) && amount > 0 && amount % 5000 === 0 && amount <= maximum
  return <form className="mc-budget-amount" onSubmit={(event) => {
    event.preventDefault()
    if (active && valid && onSend) onSend(adjustSentence({ cat: allocationName(value.category), amount, dir: value.direction }))
  }}>
    <fieldset disabled={!active || !onSend}>
      <legend>{allocationName(value.category)} 예산을 얼마 {direction}까요?</legend>
      <label className="mc-budget-amount-input">
        <MoneyInput aria-label={`${direction} 금액 (원)`} value={digits} onChange={changeAmount} placeholder="금액 입력" />
        <span>원</span>
      </label>
      <div className="mc-budget-amount-steps">
        <button type="button" className="mc-btn" disabled={amount === 0} onClick={() => setDigits(String(Math.max(0, amount - 10000)))}>−10,000원</button>
        <button type="button" className="mc-btn" disabled={amount + 10000 > maximum} onClick={() => changeAmount(String(amount + 10000))}>+10,000원</button>
      </div>
      {digits && amount % 5000 !== 0 && <p className="mc-note" role="status">5,000원 단위로 입력해 주세요.</p>}
      {value.direction === 'down' && <p className="mc-note">{value.maxAmount == null ? '최신 예산을 확인하려면 조정을 다시 요청해 주세요.' : maximum === 0 ? '현재 줄일 수 있는 금액이 없어요.' : `최대 ${won(maximum)}까지 줄일 수 있어요.`}</p>}
      <button type="submit" className="mc-primary" disabled={!valid}>미리보기</button>
    </fieldset>
  </form>
}

function DistributionInput({ value, active, onSend, onCancel }) {
  const [rows, setRows] = useState((value.values ?? []).map((row) => ({ category: row.category, digits: String(row.amount) })))
  const sources = value.sources ?? [{ category: value.source, amount: value.amount }]
  const total = rows.reduce((sum, row) => sum + Number(row.digits), 0)
  const valid = rows.length > 0 && total === value.amount && rows.every((row) => value.categories.includes(row.category)
    && Number(row.digits) > 0 && Number(row.digits) % 5000 === 0)
    && new Set(rows.map((row) => row.category)).size === rows.length
  const update = (index, patch) => setRows(rows.map((row, i) => i === index ? { ...row, ...patch } : row))
  const changed = !value.values || rows.length !== value.values.length
    || rows.some((row) => !value.values.some((initial) => initial.category === row.category && initial.amount === Number(row.digits)))
  return <form className="mc-budget-amount mc-budget-distribution" onSubmit={(event) => {
    event.preventDefault()
    if (active && valid && changed && onSend) onSend(`기존 배분 대신 ${sources.map((row) => `${allocationName(row.category)} 예산을 ${row.amount}원`).join(', ')} 줄여 ${rows.map((row) => `${allocationName(row.category)}에 ${Number(row.digits)}원`).join(', ')} 나눠줘.`)
  }}>
    <fieldset disabled={!active || !onSend}>
      <legend>어디에 나눌까요?</legend>
      <div className="mc-budget-distribution-total">
        <div><span>{sources.map((row) => allocationName(row.category)).join('·')}에서 줄인 돈</span><strong>{won(value.amount)}</strong></div>
        <span className={total === value.amount ? 'is-complete' : ''} role="status">{total === value.amount ? '모두 나눴어요' : `${won(value.amount - total)} 남음`}</span>
      </div>
      <div className="mc-budget-category-picks" aria-label="받을 항목 선택">
        {value.categories.map((code) => {
          const selected = rows.some((row) => row.category === code)
          return <button key={code} type="button" aria-pressed={selected} onClick={() => setRows(selected
            ? rows.filter((row) => row.category !== code) : [...rows, { category: code, digits: '' }])}>
            {selected && <span aria-hidden="true">✓ </span>}{allocationName(code)}
          </button>
        })}
      </div>
      {!rows.length && <p className="mc-note">받을 항목을 골라 주세요. 여러 개 골라도 돼요.</p>}
      {rows.map((row, index) => {
        const amount = Number(row.digits)
        const maximum = value.amount - total + amount
        const changeAmount = (next) => update(index, { digits: next === '' ? '' : String(Math.min(Number(next), maximum)) })
        return <div key={row.category} className="mc-budget-distribution-row">
          <strong>{allocationName(row.category)}</strong>
          <div className="mc-budget-distribution-stepper">
            <button type="button" aria-label={`${index + 1}번 금액 10,000원 줄이기`} disabled={!amount} onClick={() => changeAmount(String(Math.max(0, amount - 10000)))}>−1만</button>
            <label><MoneyInput aria-label={`${allocationName(row.category)}에 나눌 금액 (원)`} value={row.digits} onChange={changeAmount} placeholder="0" /><span>원</span></label>
            <button type="button" aria-label={`${index + 1}번 금액 10,000원 늘리기`} disabled={amount + 10000 > maximum} onClick={() => changeAmount(String(amount + 10000))}>+1만</button>
          </div>
        </div>
      })}
      {rows.some((row) => Number(row.digits) % 5000 !== 0) && <p className="mc-note">5,000원 단위로 입력해 주세요.</p>}
      <button type="submit" className="mc-primary" disabled={!valid || !changed}>나눈 예산 미리보기</button>
      {onCancel && <button type="button" className="mc-link mc-budget-edit-cancel" onClick={onCancel}>수정 취소</button>}
    </fieldset>
  </form>
}

export default function BudgetAdjustment({ content, active, onPick, onApprove, onSend, navigation }) {
  const [editing, setEditing] = useState(false)
  const adjustment = content.budgetAdjustment
  const proposal = adjustment.proposal
  const state = proposal?.state ?? adjustment.status
  const canApprove = active && adjustment.canApprove && proposal?.state === 'awaiting_approval'
  const hasComparison = (adjustment.comparison?.plans ?? []).some((plan) => ['ready', 'partial'].includes(plan.status) && plan.amount > 0 && plan.result?.changes?.length)
  const hasPreview = proposal || hasComparison
  const amountInput = !hasPreview && !TERMINAL.has(state) && adjustment.amountInput
  const distributionInput = !TERMINAL.has(state) && adjustment.distributionInput
  const options = hasPreview
    ? (navigation?.choices ?? content.choices ?? []).filter((choice) => ['recommend', 'compare'].includes(choice.value)) : []
  return <div className="mc-bubble mc-budget-adjustment">
    {adjustment.recommendationAdvice && <section aria-label="추천하는 이유" className="mc-budget-advice">
      <p><strong>{adjustment.recommendationAdvice.title}</strong></p>
      <ul>{adjustment.recommendationAdvice.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
    </section>}
    {adjustment.requestSummary && !amountInput && !distributionInput && !TERMINAL.has(state) && <p className="mc-budget-reason" aria-label="이해한 요청">{adjustment.requestSummary}</p>}
    {amountInput && <AdjustmentAmount key={`${content.turn}-${amountInput.category}-${amountInput.direction}`} value={amountInput} active={active} onSend={onSend} />}
    {distributionInput && <DistributionInput key={content.turn} value={distributionInput} active={active} onSend={onSend} />}
    {!proposal && !hasComparison && !amountInput && !adjustment.recommendationAdvice && (content.lines ?? (content.text ? [content.text] : [])).filter((line) => !distributionInput || !line.endsWith('어디에 나눌까요?')).map((line, i) => <p key={i}>{line}</p>)}
    {hasComparison && <Comparison comparison={adjustment.comparison} choices={content.choices} active={active} onPick={onPick} detail={adjustment.detail} />}
    {proposal && <section className="mc-budget-proposal" aria-label="예산 변경안">
      <h2>{state === 'saved' ? '예산에 반영했어요' : state === 'expired' ? '만료된 변경안이에요' : '예산 변경안'}</h2>
      {proposal.previewRevision > 1 && <p>{proposal.previewRevision}차 조정안</p>}
      {(proposal.revisionSummary ?? []).map((line) => <p className="mc-budget-reason" key={line}>{line}</p>)}
      <BudgetTransfers transfers={proposal.transfers} />
      <BudgetChanges changes={proposal.changes} />
      <Shortfalls rows={proposal.shortfalls} />
      <BudgetSettings proposal={proposal} />
      <RecoveryAllocations proposal={proposal} />
      {adjustment.status === 'awaiting_input' && (content.lines ?? []).map((line, index) => <p key={index} role="status">{line}</p>)}
      {proposal.state === 'awaiting_approval' && adjustment.distributionEdit && !adjustment.phase && editing && <DistributionInput
        key={proposal.proposalId} value={adjustment.distributionEdit} active={active} onSend={onSend} onCancel={() => setEditing(false)} />}
      {state === 'expired' && <p className="mc-budget-warning">조건이 바뀌었어요. 원하는 조정을 다시 입력해 주세요.</p>}
      {['canceled', 'rejected', 'failed'].includes(state) && <p className="mc-budget-warning">이 변경안은 적용할 수 없어요. 원하는 조정을 다시 입력해 주세요.</p>}
      {adjustment.canApprove && state === 'awaiting_approval' && !editing && <button type="button" className="mc-primary" disabled={!canApprove} onClick={() => onApprove(proposal)}>예산 확정하기</button>}
      {proposal.state === 'awaiting_approval' && adjustment.distributionEdit && !adjustment.phase && !editing && <button
        type="button" className="mc-link mc-budget-edit-toggle" aria-expanded={false} disabled={!active || !onSend}
        onClick={() => setEditing(true)}>배분 수정</button>}
    </section>}
    {adjustment.alternatives && <Comparison comparison={adjustment.alternatives} readOnly detail={adjustment.detail} />}
    {adjustment.alternativesNotice && <p className="mc-budget-warning">{adjustment.alternativesNotice}</p>}
    {!TERMINAL.has(state) && <Explanation value={amountInput || distributionInput ? { ...adjustment.explanation, brief: null } : adjustment.explanation} detail={adjustment.detail} />}
    {(content.notices ?? []).map((line) => <p className="mc-budget-warning" key={line}>{line}</p>)}
    {options.length > 0 && !TERMINAL.has(state) && <div className="mc-budget-navigation" aria-label="예산 조정 보기 옵션">
      {options.map((choice) => <button key={choice.value} type="button" className="mc-link"
        disabled={!(navigation?.active ?? active)} title="현재 대화 조건으로 확인해요"
        onClick={() => (navigation?.onPick ?? onPick)(choice)}>{choice.label}</button>)}
    </div>}
  </div>
}

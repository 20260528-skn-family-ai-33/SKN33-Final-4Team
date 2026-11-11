// 금액·추천 근거는 서버 검수 결과 그대로 표시한다. 펼침은 조회·선택·저장을 호출하지 않는다.
// 답변 전체를 다른 답변과 같은 말풍선(mc-bubble) 안에 그린다 (11/11). 모양은 styles/chat.css
import { allocationName, ENDED_ADJUSTMENTS as TERMINAL } from '../utils/adjust.js'
import { won } from '../utils/money.js'

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
  const changes = plan.result?.changes ?? []
  const headline = changes.filter((row) => row.direct || ['SAVINGS', 'CUSHION_MONTHLY', 'SPARE'].includes(row.category))
  const reason = plan.assessment?.reasons?.[0] ?? plan.assessment?.cautions?.[0] ?? plan.lines?.[0]
  const zeroes = (plan.assessment?.facts ?? []).filter((fact) => (fact.availableAfter ?? fact.remaining) === 0)
  return <article className={`mc-budget-plan${plan.recommended ? ' is-recommended' : ''}`} aria-label={plan.label}>
    <div className="mc-budget-plan-head"><h3>{plan.label}</h3><span>{plan.assessment?.label ?? PLAN_STATUS[plan.status] ?? '확인 필요'}</span></div>
    <p className="mc-budget-outcome">{PLAN_STATUS[plan.status] ?? '확인 필요'}{['ready', 'partial'].includes(plan.status) && ` · ${won(plan.amount)}`}</p>
    {plan.maximum != null && <p className="mc-note">이 재원 조합의 최대 조정 가능액 {won(plan.maximum)}</p>}
    <BudgetChanges changes={headline} />
    <Shortfalls rows={plan.result?.shortfalls} />
    {reason && <p className="mc-budget-reason">{reason}</p>}
    {zeroes.map((fact) => <p className="mc-budget-warning" key={fact.category}>
      주의: {allocationName(fact.category)} {fact.availableAfter != null ? '가용액' : '남은 예산'} 0원
    </p>)}
    {(plan.lines ?? []).filter((line) => line.includes('비교 재원에서 제외')).map((line) => <p className="mc-budget-warning" key={line}>{line}</p>)}
    <details className="mc-budget-details" open={detail}><summary>예산 변화 보기</summary>
      {changes.length ? <BudgetChanges changes={changes} /> : <p>아직 검수된 전후 금액이 없어요. 필요한 항목을 먼저 확인해 주세요.</p>}
      {(plan.assessment?.reasons ?? []).map((line) => <p key={line}>{line}</p>)}
      {(plan.assessment?.cautions ?? []).filter((line) => line !== reason).map((line) => <p key={line}>{line}</p>)}
    </details>
    {choice && <button type="button" className="mc-btn mc-budget-select" disabled={!active} aria-label={`${plan.label} 선택`} onClick={() => onPick(choice)}>이 안 선택</button>}
  </article>
}

function Comparison({ comparison, choices, active, onPick, readOnly = false, detail }) {
  return <section className="mc-budget-comparison" aria-label={readOnly ? '다른 범위 예산 비교' : '세 가지 조정안 비교'}>
    <h2>{readOnly ? '범위별로 비교해 보세요' : '비교하고 정하기'}</h2>
    <p className="mc-budget-outcome">{comparison.amountWasGiven ? '요청 금액' : comparison.basisKind === 'forecast_shortfall' ? '지출 추정에 따른 증액 제안' : '비교 기준액'} {won(comparison.basisAmount)}</p>
    {!comparison.amountWasGiven && comparison.basisKind !== 'forecast_shortfall' && <p className="mc-budget-reason">차이를 보기 위한 금액이며, 필요한 금액의 추천은 아니에요.</p>}
    {readOnly && <p className="mc-budget-reason">비교만 해요. 선택한 재원은 그대로예요.</p>}
    {(comparison.plans ?? []).map((plan) => <Plan key={plan.id} plan={plan} active={active} onPick={onPick} detail={detail}
      choice={readOnly ? null : choices?.find((choice) => choice.value === `plan_${plan.id}` || choice.value === `financial_${plan.id}`)} />)}
    {!comparison.recommendation?.planId && comparison.recommendation?.reasons?.[0] &&
      <p className="mc-budget-reason">{comparison.recommendation.reasons[0]}</p>}
  </section>
}

export default function BudgetAdjustment({ content, active, onPick, onApprove, navigation }) {
  const adjustment = content.budgetAdjustment
  const proposal = adjustment.proposal
  const state = proposal?.state ?? adjustment.status
  const canApprove = active && adjustment.canApprove && proposal?.state === 'awaiting_approval'
  // 어디서 빠졌는지도 펼치지 않고 보이게: 직접 바꾼 항목 · 저축/쿠션/여윳돈 + 줄어드는 항목 (11/11)
  const visible = (proposal?.changes ?? []).filter((row) => row.direct || row.diff < 0 || ['SAVINGS', 'CUSHION_MONTHLY', 'SPARE'].includes(row.category))
  const options = (navigation?.choices ?? content.choices ?? []).filter((choice) => ['recommend', 'compare'].includes(choice.value))
  return <div className="mc-bubble mc-budget-adjustment">
    {!proposal && !adjustment.comparison && (content.lines ?? (content.text ? [content.text] : [])).map((line, i) => <p key={i}>{line}</p>)}
    {adjustment.comparison && <Comparison comparison={adjustment.comparison} choices={content.choices} active={active} onPick={onPick} detail={adjustment.detail} />}
    {proposal && <section className="mc-budget-proposal" aria-label="예산 변경안">
      <h2>{state === 'saved' ? '예산에 반영했어요' : state === 'expired' ? '만료된 변경안이에요' : '예산 변경안'}</h2>
      {proposal.previewRevision > 1 && <p>{proposal.previewRevision}차 조정안</p>}
      {(proposal.revisionSummary ?? []).map((line) => <p className="mc-budget-reason" key={line}>{line}</p>)}
      <BudgetChanges changes={visible} />
      <Shortfalls rows={proposal.shortfalls} />
      <BudgetSettings proposal={proposal} />
      <details className="mc-budget-details" open={adjustment.detail}><summary>예산 변화 보기</summary><BudgetChanges changes={proposal.changes} /></details>
      <RecoveryAllocations proposal={proposal} />
      {state === 'expired' && <p className="mc-budget-warning">조건이 바뀌었어요. 원하는 조정을 다시 입력해 주세요.</p>}
      {['canceled', 'rejected', 'failed'].includes(state) && <p className="mc-budget-warning">이 변경안은 적용할 수 없어요. 원하는 조정을 다시 입력해 주세요.</p>}
      {adjustment.canApprove && state === 'awaiting_approval' && <button type="button" className="mc-primary" disabled={!canApprove} onClick={() => onApprove(proposal)}>예산 확정하기</button>}
    </section>}
    {adjustment.alternatives && <Comparison comparison={adjustment.alternatives} readOnly detail={adjustment.detail} />}
    {adjustment.alternativesNotice && <p className="mc-budget-warning">{adjustment.alternativesNotice}</p>}
    {!TERMINAL.has(state) && <Explanation value={adjustment.explanation} detail={adjustment.detail} />}
    {(content.notices ?? []).map((line) => <p className="mc-budget-warning" key={line}>{line}</p>)}
    {options.length > 0 && !TERMINAL.has(state) && <div className="mc-budget-navigation" aria-label="예산 조정 보기 옵션">
      {options.map((choice) => <button key={choice.value} type="button" className="mc-link"
        disabled={!(navigation?.active ?? active)} title="현재 대화 조건으로 확인해요"
        onClick={() => (navigation?.onPick ?? onPick)(choice)}>{choice.value === 'recommend' ? '추천안 보기' : '비교하기'}</button>)}
    </div>}
  </div>
}

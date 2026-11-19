// 금액·추천 근거는 서버 검수 결과 그대로 표시한다. 펼침은 조회·선택·저장을 호출하지 않는다.
// 답변 전체를 다른 답변과 같은 말풍선(mc-bubble) 안에 그린다 (11/11). 모양은 styles/chat.css
import { adjustSentence, allocationName, manwon, isVisibleAdjustmentPlan, ENDED_ADJUSTMENTS as TERMINAL } from '../utils/adjust.js'
import { useId, useState } from 'react'
import { won } from '../utils/money.js'
import MoneyInput from './MoneyInput.jsx'
import { PATHS } from '../routes.js'
import Popup from '../popups/Popup.jsx'
import { usePopup } from '../popups/context.js'

const PLAN_STATUS = { ready: '반영 가능', partial: '일부 반영', needs_destination: '받을 항목 선택 필요',
  needs_source: '재원 선택 필요', needs_data: '자료 확인 필요', needs_policy: '준비 중',
  needs_confirmation: '금액 단위 확인 필요', infeasible: '현재 이용 불가', unchanged: '변경 없음' }

const dayText = (iso) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? '')
  return m ? `${Number(m[2])}월 ${Number(m[3])}일` : iso
}

// 맨 위 요약 (11/16 UI 정리): 이해한 요청은 굵은 한 줄, 적용 시점 · 지금 예산 유지 안내는 그 아래 작은 한 줄.
// 같은 뜻의 안내 문장(…부터의 예산을 미리 조정해요 · 확정 전 미리보기 · …부터 반영해요)을 여러 칸에 반복하지 않는다
function Summary({ request, effectiveOn, oneOff, keep }) {
  const meta = [effectiveOn && `${dayText(effectiveOn)} 시작 주기부터${oneOff ? ' 그 주기에만' : ''} 적용`,
    effectiveOn ? '이번 예산은 그대로예요' : keep && '확정 전까지 지금 예산은 그대로예요'].filter(Boolean)
  return <>
    {request && <p className="mc-budget-summary-request" aria-label="이해한 요청">{request}</p>}
    {meta.length > 0 && <p className="mc-budget-summary-meta">{meta.join(' · ')}</p>}
  </>
}

// 월급 증가분 배분안 (11/16 UI 정리): '월급 증가분 → 항목 금액' 줄을 늘어놓지 않고 받는 항목 · 금액 표 + 합계 한 줄
function SalaryShares({ transfers = [] }) {
  const rows = transfers.filter((row) => row.amount > 0).sort((a, b) => b.amount - a.amount)
  if (!rows.length) return null
  const total = rows.reduce((sum, row) => sum + row.amount, 0)
  return <div className="mc-budget-shares" aria-label="월급 증가분을 나누는 내역">
    <p className="mc-budget-shares-total"><span>늘어난 월급에서 나누는 돈</span><strong>{won(total)}</strong></p>
    <dl>{rows.map((row) => <div key={`${row.to}-${row.amount}`}>
      <dt>{allocationName(row.to)}</dt><dd>+{won(row.amount)}</dd>
    </div>)}</dl>
  </div>
}

// 대화 안 [월급 증가분 배분 검토] (11/16 UI 정리): 무엇을 하게 되는지 세 단계로만 보여 준다
function SalaryAllocation({ request, active, onReview }) {
  return <section className="mc-budget-salary-review" aria-label="월급 증가분 배분">
    <p className="mc-budget-salary-title">늘어난 월급을 나눌 차례예요</p>
    <ol className="mc-budget-steps">
      <li>{request ? `${request} 먼저 반영` : '보관한 요청 먼저 반영'}</li>
      <li>남은 증가분 중 저축할 비율 정하기</li>
      <li>나머지는 생활비·월 머니쿠션·여윳돈에 나누기</li>
    </ol>
    <button type="button" className="mc-primary" disabled={!active || !onReview} onClick={onReview}>월급 증가분 배분 검토</button>
  </section>
}

// 잦은 조정 잔소리 (11/18 시안 C3): 서버 문장('이번 급여 주기에 예산을 N회 조정했어요. 다시 조정할까요?')의 첫 문장을
// 말풍선 위 주황 경고 한 줄로, 나머지는 원래 질문 그대로. 글자는 서버 문장 그대로 (화면은 계산하지 않음)
function NagLines({ lines }) {
  const text = lines.join(' ')
  const cut = text.indexOf('. ')
  const [warn, rest] = cut < 0 ? [text, ''] : [text.slice(0, cut + 1), text.slice(cut + 2)]
  return <>
    <p className="mc-budget-nag" role="note"><span aria-hidden="true">⚠ </span>{warn}</p>
    {rest && <p>{rest}</p>}
  </>
}

// 증감액 색: 늘면 빨강(mc-fx-up) · 줄면 파랑(mc-fx-down) — 소비 분석과 같은 규칙, 부호(+ · −)로도 구분 (11/17)
export function BudgetChanges({ changes = [] }) {
  return <dl className="mc-budget-changes">{changes.map((row) => (
    <div key={row.category}>
      <dt>{allocationName(row.category)}</dt>
      <dd><span>{won(row.before)}</span> <span aria-label="변경 후">→</span> <strong>{won(row.after)}</strong>
        {row.diff !== 0 && row.diff != null && <small className={row.diff > 0 ? 'mc-fx-up' : 'mc-fx-down'}>{row.diff > 0 ? '+' : '−'}{won(Math.abs(row.diff))}</small>}
      </dd>
    </div>
  ))}</dl>
}

export function BudgetTransfers({ transfers = [], salaryReview = false }) {
  if (!transfers.length) return null
  return <div className="mc-budget-transfers" aria-label="돈이 옮겨가는 내역">
    {transfers.map((row, index) => <p key={index}>{salaryReview && row.from === 'SPARE' ? '월급 증가분' : allocationName(row.from)} → {allocationName(row.to)} <strong>{won(row.amount)}</strong></p>)}
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
    {(proposal.targetItems ?? []).filter((item) => item.amount === null).map((item) =>
      <p key={item.category} className="mc-note">승인 시 {allocationName(item.category)} 유지 목표 해제</p>)}
  </>
}

function Explanation({ value, detail, shown = [] }) {
  if (!value) return null
  const normalize = (line) => line.trim().replace(/\s+/g, ' ')
  const seen = shown.filter(Boolean).map(normalize)
  const remaining = (line) => (line ?? '').split(/(?<=[.!?])\s+|\n+/).filter((part) => {
    const text = normalize(part)
    if (!text || seen.some((prior) => prior.includes(text))) return false
    seen.push(text)
    return true
  }).join(' ')
  const brief = remaining(value.brief)
  const lines = []
  for (const [label, line] of [['상황', value.why], ['변경되는 내용', value.effect], ['다음에 할 일', value.next]]) {
    const text = remaining(line)
    if (text) lines.push([label, text])
  }
  return <>
    {brief && <p className="mc-budget-reason">{brief}</p>}
    {lines.length > 0 && <details className="mc-budget-details" open={detail}><summary>{value.title || '조정 전 확인할 내용'}</summary>
      <dl className="mc-budget-explanation">{lines.map(([label, line]) => <div key={label}><dt>{label}</dt><dd>{line}</dd></div>)}</dl>
    </details>}
  </>
}

// 추천 카드용 짧은 변경표: 늘어나는 항목 먼저, '50,000원 → 10,000원' 아래 증감 '−40,000원'
function CompactChanges({ changes = [] }) {
  const rows = [...changes].sort((a, b) => (b.diff > 0) - (a.diff > 0))
  return <dl className="mc-budget-compact">{rows.map((row) => (
    <div key={row.category} className={row.diff > 0 ? 'is-up' : undefined}>
      <dt>{allocationName(row.category)}</dt>
      {/* 변경 전 · → · 변경 후 · 증감을 칸으로 나눠 줄마다 시작 위치를 맞춘다 (11/17) */}
      {/* 11/17: 금액은 모두 원 단위 ('2만원' · '245,000원'이 섞이지 않게) */}
      <dd><span>{won(row.before)}</span> <span>→</span> <strong>{won(row.after)}</strong> <small className={row.diff > 0 ? 'mc-fx-up' : 'mc-fx-down'}>{row.diff > 0 ? '+' : '−'}{won(Math.abs(row.diff))}</small></dd>
    </div>
  ))}</dl>
}

// 적용 후 전체 예산 (11/17): 서버가 안마다 준 적용 후 배정액(result.allocations · buckets)과 변화(result.changes) 그대로 — 화면은 계산하지 않는다.
// 바뀐 줄 먼저(전 → 후 · 증감, 늘면 빨강 · 줄면 파랑), 나머지 항목은 금액만 옅게. 0원이고 안 바뀐 항목은 뺀다
function FullBudget({ plan, notes = [] }) {
  const after = { ...(plan.result?.allocations ?? {}), ...(plan.result?.buckets ?? {}) }
  const changed = new Map((plan.result?.changes ?? []).map((row) => [row.category, row]))
  const rows = Object.entries(after).filter(([code, amount]) => amount || changed.has(code))
  const ordered = [...rows.filter(([code]) => changed.has(code)), ...rows.filter(([code]) => !changed.has(code))]
  return <div className="mc-budget-full">
    <table>
      <thead><tr><th scope="col">항목</th><th scope="col">적용 후 예산</th></tr></thead>
      <tbody>{ordered.map(([code, amount]) => {
        const row = changed.get(code)
        return <tr key={code} className={row ? 'is-changed' : undefined}>
          <th scope="row">{allocationName(code)}</th>
          <td>{row && <span className="mc-budget-full-before">{won(row.before)} → </span>}<strong>{won(amount)}</strong>
            {row && Number.isFinite(row.diff) && <small className={row.diff > 0 ? 'mc-fx-up' : 'mc-fx-down'}>{row.diff > 0 ? '+' : '−'}{won(Math.abs(row.diff))}</small>}</td>
        </tr>
      })}</tbody>
    </table>
    {notes.map((line) => <p key={line} className="mc-note">{line}</p>)}
  </div>
}

// 카드 안 '적용 후 전체 예산 보기 ›' → 가운데 팝업. 팝업 틀이 없거나(테스트 · 지난 화면) 전체 배정액이 없으면 fallback(예전 접기)을 그린다
function FullBudgetButton({ plan, notes, onSelect, selectLabel, fallback = null }) {
  const popups = usePopup()
  if (!popups || !plan.result?.allocations) return fallback
  const open = () => popups.openPopup(<Popup label={`${plan.label} 적용 후 전체 예산`} dialog>
    <div className="mc-budget-full-pop">
      <h2>{plan.label}</h2>
      <p className="mc-note">이 안을 적용하면 이렇게 돼요. 확정 전까지 예산은 바뀌지 않아요.</p>
      <FullBudget plan={plan} notes={notes} />
      {onSelect && <button type="button" className="mc-budget-select" onClick={() => { popups.closePopup(); onSelect() }}>{selectLabel}</button>}
    </div>
  </Popup>)
  return <button type="button" className="mc-budget-full-open" aria-haspopup="dialog" onClick={open}>적용 후 전체 예산 보기 ›</button>
}

// 11/17: 카드는 '고르기'만 한다 (서버로 보내지 않음). 보내기는 비교 아래 버튼 하나 — 카드마다 반복되던 선택 버튼 · 금액 바꾸기를 한 번만
// onChoose 가 있으면 제목 줄이 라디오 버튼, 카드 빈 곳을 눌러도 고른다 (펼치기 · 링크 · 버튼 누름은 제외)
function PlanHead({ plan, picked, sent, onChoose, recommended }) {
  return <div className="mc-budget-plan-head">
    {onChoose
      ? <button type="button" role="radio" aria-checked={picked} className="mc-budget-plan-pick" onClick={onChoose}>
        <span className="mc-budget-radio" aria-hidden="true" /><span className="mc-budget-plan-title">{plan.label}</span>
      </button>
      : <h3>{plan.label}</h3>}
    <div className="mc-budget-plan-badges">
      {recommended && <span className="mc-budget-recommended">추천</span>}
      {sent && <span className="mc-budget-selected">✓ 선택한 안</span>}
    </div>
  </div>
}
const chooseFromCard = (onChoose) => onChoose && ((event) => { if (!event.target.closest('summary, details, button, a, input')) onChoose() })

function Plan({ plan, detail, picked, sent, onChoose, onSelect, selectLabel }) {
  const changes = plan.result?.changes ?? []
  const [summary, ...why] = plan.assessment?.reasons ?? []
  const cautions = (plan.assessment?.cautions ?? []).filter((line) => line !== summary)
  const missing = (plan.result?.shortfalls ?? []).reduce((sum, row) => sum + (row.missingAmount ?? 0), 0)
  return <article className={`mc-budget-plan${plan.recommended ? ' is-recommended' : ''}${picked ? ' is-selected' : ''}`} aria-label={plan.label} onClick={chooseFromCard(onChoose)}>
    <PlanHead plan={plan} picked={picked} sent={sent} onChoose={onChoose} recommended={plan.recommended} />
    {plan.status === 'partial' && missing > 0 && <p className="mc-budget-shortfall">일부만 가능 · <strong>{manwon(missing)} 부족</strong></p>}
    {summary && !plan.recommended && <p className="mc-budget-reason">{summary}</p>}
    {plan.recommended ? <CompactChanges changes={changes} /> : <FullBudgetButton plan={plan} onSelect={onSelect} selectLabel={selectLabel}
      fallback={<details className="mc-budget-details"><summary>예산 변화 보기</summary><CompactChanges changes={changes} /></details>} />}
    {cautions.slice(0, plan.recommended ? 2 : 1).map((line) => <p className="mc-budget-warning" key={line}>{line}</p>)}
    {why.length > 0 && <details className="mc-budget-details" open={Boolean(detail)}>
      <summary>추천 이유</summary>
      <ul className="mc-budget-why">{why.map((line) => <li key={line}>{line}</li>)}</ul>
    </details>}
    {plan.recommended && <FullBudgetButton plan={plan} onSelect={onSelect} selectLabel={selectLabel} />}
  </article>
}

// 예전 비교 카드(재정 추천·감액 비교). 서버 추천기 카드는 위 Plan 을 쓴다.
function LegacyPlan({ plan, detail, picked, sent, onChoose, onSelect, selectLabel }) {
  const [expanded, setExpanded] = useState(Boolean(detail))
  const choosingDestination = plan.status === 'needs_destination'
  const changes = plan.result?.changes ?? []
  const headline = changes.filter((row) => row.direct || ['SAVINGS', 'CUSHION_MONTHLY', 'SPARE'].includes(row.category))
  const reason = plan.assessment?.reasons?.[0] ?? plan.assessment?.cautions?.[0] ?? plan.lines?.[0]
  const zeroes = (plan.assessment?.facts ?? []).filter((fact) => (fact.availableAfter ?? fact.remaining) === 0)
  const rest = changes.filter((row) => !headline.includes(row))
  const notes = [...(plan.assessment?.reasons ?? []), ...(plan.assessment?.cautions ?? [])].filter((line) => line !== reason)
  return <article className={`mc-budget-plan${picked ? ' is-selected' : ''}`} aria-label={plan.label} onClick={chooseFromCard(onChoose)}>
    <PlanHead plan={plan} picked={picked} sent={sent} onChoose={onChoose} recommended={plan.recommended && !choosingDestination} />
    {choosingDestination ? <p className="mc-budget-reason">받을 항목과 금액을 직접 고를 수 있어요.</p> : <>
    <p className="mc-budget-outcome">{PLAN_STATUS[plan.status] ?? '확인 필요'}{['ready', 'partial'].includes(plan.status) && ` · ${won(plan.amount)}`}</p>
    {plan.maximum != null && <p className="mc-note">이 방법으로 옮길 수 있는 최대 금액 {won(plan.maximum)}</p>}
    {/* 11/17: 옮겨가는 요약을 표보다 먼저 (변경안 본문과 같은 순서) — 표의 한 줄로 읽히지 않게 */}
    <BudgetTransfers transfers={plan.result?.transfers} />
    <BudgetChanges changes={headline} />
    <Shortfalls rows={plan.result?.shortfalls} />
    {reason && <p className="mc-budget-reason">{reason}</p>}
    {zeroes.map((fact) => <p className="mc-budget-warning" key={fact.category}>
      주의: {allocationName(fact.category)} {fact.availableAfter != null ? '가용액' : '남은 예산'} 0원
    </p>)}
    {/* 11/17: 펼치면 위 주요 변화에 없는 줄만 (같은 줄을 두 번 보여 주지 않게). 더 볼 게 없으면 토글을 숨긴다 */}
    <FullBudgetButton plan={plan} notes={notes} onSelect={onSelect} selectLabel={selectLabel}
      fallback={(rest.length > 0 || notes.length > 0) && <details className="mc-budget-details" open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)}>
        <summary>{expanded ? '나머지 변화 접기' : '나머지 변화 보기'}</summary>
        {rest.length > 0 && <BudgetChanges changes={rest} />}
        {notes.map((line) => <p key={line}>{line}</p>)}
      </details>} />
    </>}
  </article>
}

function amountEditLabel(plan, label) {
  if (label && !['금액 바꾸기', '금액 직접 입력', '조정 금액 바꾸기'].includes(label)) return label
  const direct = (plan.result?.changes ?? []).filter((change) => change.direct)
  const change = direct[0]
  const diff = change?.after - change?.before
  return direct.length === 1 && change.category && Number.isFinite(diff) && diff !== 0
    ? `${allocationName(change.category)} ${diff > 0 ? '늘릴' : '줄일'} 금액 바꾸기` : '요청 금액 바꾸기'
}

function Comparison({ comparison, choices, active, onPick, readOnly = false, detail }) {
  const selectionHelp = useId()
  const plans = (comparison.plans ?? []).filter((plan) => isVisibleAdjustmentPlan(plan, choices))
  const choiceOf = (plan) => readOnly ? null : choices?.find((choice) => choice.value === `plan_${plan.id}` || choice.value === `financial_${plan.id}`)
  const choosable = plans.filter(choiceOf)
  // 추천 카드가 있으면 처음부터 골라 둔다 — 추천대로 할 때는 아래 버튼 한 번이면 된다. 추천이 없으면(비슷한 방법들) 사용자가 고를 때까지 비워 둔다 (고를 안이 하나뿐이면 그 안)
  const [picked, setPicked] = useState(() => (choosable.find((plan) => plan.recommended && plan.status !== 'needs_destination') ?? (choosable.length === 1 ? choosable[0] : null))?.id ?? null)
  const [sent, setSent] = useState(null)
  const pickedPlan = choosable.find((plan) => plan.id === picked)
  if (!plans.length) return null
  // 서버 추천기(headline 있음): 이해한 요청 → 추천안 → 고르기, 한 형식으로 짧게 보여 준다.
  const advised = typeof comparison.headline === 'string'
  const edits = readOnly ? [] : (choices ?? []).filter((choice) => ['manual', 'manual_living'].includes(choice.value))
  return <section className="mc-budget-comparison" aria-label={readOnly ? '다른 범위 예산 비교' : '예산 조정안 비교'}>
    {!readOnly && <p className="mc-budget-example-intro" id={selectionHelp}>
      <span className="mc-budget-tag">미리보기</span>안을 골라도 확정 전까지 예산은 바뀌지 않아요.
    </p>}
    {advised ? <>
      {comparison.lead && <p className="mc-budget-lead">{comparison.lead}</p>}
      <p className="mc-budget-headline">{comparison.headline}</p>
    </> : <>
      {readOnly && <h2>다른 방법으로 바꾸면 이렇게 돼요</h2>}
      <p className="mc-budget-outcome">{comparison.amountWasGiven ? '요청한 금액' : comparison.basisKind === 'forecast_shortfall' ? '최근 지출을 참고해 제안하는 금액' : '옮길 수 있는 범위에서 계산한 예시'} {won(comparison.basisAmount)}</p>
      {!comparison.amountWasGiven && comparison.basisKind !== 'forecast_shortfall' && <p className="mc-budget-reason">현재 예산에서 옮길 수 있는 돈으로 만든 예시예요. 원하는 금액으로 바꿀 수 있어요.</p>}
    </>}
    {comparison.notice && <p className="mc-budget-reason" role="note">{comparison.notice}</p>}
    {readOnly && <p className="mc-budget-reason">비교만 해요. 선택한 재원은 그대로예요.</p>}
    {/* 11/17: 추천을 못 정한 이유('비슷한 방법들이라 …')는 카드를 읽기 전에 — 어떻게 고르면 되는지 안내라 맨 아래가 아니라 위에 */}
    {!comparison.recommendation?.planId && comparison.recommendation?.reasons?.[0] &&
      <p className="mc-budget-reason mc-budget-choose-help">{comparison.recommendation.reasons[0]}</p>}
    <div className="mc-budget-plan-list" role={choosable.length ? 'radiogroup' : undefined} aria-label={choosable.length ? '조정안 고르기' : undefined}>
      {plans.map((plan) => {
        const Card = advised && plan.status !== 'needs_destination' ? Plan : LegacyPlan
        const canChoose = Boolean(choiceOf(plan)) && active && Boolean(onPick)
        return <Card key={plan.id} plan={plan} detail={detail} picked={picked === plan.id} sent={sent === plan.id}
          onChoose={canChoose ? () => setPicked(plan.id) : null}
          onSelect={canChoose ? () => { setPicked(plan.id); setSent(plan.id); onPick(choiceOf(plan)) } : null}
          selectLabel={plan.recommended && plan.status !== 'needs_destination' ? '추천대로 선택하기' : '이 배분으로 선택하기'} />
      })}
    </div>
    {(choosable.length > 0 || edits.length > 0) && <div className="mc-budget-plan-actions">
      {choosable.length > 0 && <button type="button" className="mc-budget-select" disabled={!active || !onPick || !pickedPlan} aria-describedby={selectionHelp}
        onClick={() => { setSent(pickedPlan.id); onPick(choiceOf(pickedPlan)) }}>
        {!pickedPlan ? '위에서 안을 골라 주세요' : sent === pickedPlan.id ? '✓ 선택한 안' : pickedPlan.recommended && pickedPlan.status !== 'needs_destination' ? '추천대로 선택하기' : '이 배분으로 선택하기'}
      </button>}
      {edits.length > 0 && <div className="mc-chipbar" role="group" aria-label="조정안 수정">
        {edits.map((choice) => <button key={choice.value} type="button" disabled={!active || !onPick}
          onClick={() => onPick(choice)}>{choice.value === 'manual' ? amountEditLabel(pickedPlan ?? plans[0], choice.label) : choice.label}</button>)}
      </div>}
    </div>}
  </section>
}

function SalaryChange({ active, onEdit }) {
  return <section className="mc-budget-salary" aria-label="월급 변경">
    <button type="button" className="mc-primary" disabled={!active || !onEdit} onClick={onEdit}>마이페이지에서 월급 수정하기</button>
  </section>
}

function AdjustmentAmount({ value, active, onSend }) {
  const inputHelp = useId()
  const [digits, setDigits] = useState('')
  const amount = Number(digits)
  const direction = value.direction === 'up' ? '늘릴' : '줄일'
  const maximum = Number.isSafeInteger(value.maxAmount) && value.maxAmount >= 0 ? value.maxAmount : value.direction === 'down' ? 0 : 9999999999999
  const before = value.beforeAmount
  const after = Number.isSafeInteger(before) && before >= 0 && Number.isSafeInteger(amount) && amount <= maximum
    ? before + (value.direction === 'up' ? amount : -amount) : null
  const changeAmount = (next) => setDigits(next === '' || value.direction === 'up' ? next : String(Math.min(Number(next), maximum)))
  const valid = Number.isSafeInteger(amount) && amount > 0 && amount % 5000 === 0 && amount <= maximum
  return <form className="mc-budget-amount" onSubmit={(event) => {
    event.preventDefault()
    if (active && valid && onSend) onSend(adjustSentence({ cat: allocationName(value.category), amount, dir: value.direction }))
  }}>
    <fieldset disabled={!active || !onSend}>
      <legend>{allocationName(value.category)} 예산을 얼마 {direction}까요?</legend>
      <p className="mc-note" aria-label="변경 전후 예산" aria-live="polite">
        변경 전 {Number.isSafeInteger(before) ? won(before) : '확인 필요'} → 변경 후 <strong>{Number.isSafeInteger(after) && after >= 0 ? won(after) : '계산 대기'}</strong>
      </p>
      <label className="mc-budget-amount-input">
        <MoneyInput aria-label={`${direction} 금액 (원)`} aria-describedby={inputHelp} value={digits} onChange={changeAmount} placeholder="금액 직접 입력" />
        <span>원</span>
      </label>
      <p id={inputHelp} className="mc-note">금액 칸을 눌러 직접 입력하거나, 아래 버튼으로 바꿀 수 있어요.</p>
      <div className="mc-budget-amount-steps">
        <button type="button" className="mc-btn" disabled={amount === 0} onClick={() => setDigits(String(Math.max(0, amount - 10000)))}>−10,000원</button>
        <button type="button" className="mc-btn" disabled={amount + 10000 > maximum} onClick={() => changeAmount(String(amount + 10000))}>+10,000원</button>
      </div>
      {digits && amount % 5000 !== 0 && <p className="mc-note" role="status">5,000원 단위로 입력해 주세요.</p>}
      {value.direction === 'up' && amount > maximum && <p className="mc-note" role="alert">현재 다른 예산의 총액 {won(maximum)}을 넘어요. 금액을 줄여 주세요.</p>}
      {value.direction === 'down' && <p className="mc-note">{value.maxAmount == null ? '최신 예산을 확인하려면 조정을 다시 요청해 주세요.' : maximum === 0 ? '현재 줄일 수 있는 금액이 없어요.' : `최대 ${won(maximum)}까지 줄일 수 있어요.`}</p>}
      <button type="submit" className="mc-primary" disabled={!valid}>미리보기</button>
    </fieldset>
  </form>
}

// '140,000원' → '14만', '85,000원' → '8.5만' (칩 안 최대 금액. 넘겨 말하지 않게 내림)
const shortWon = (n) => (n >= 10000 ? `${Math.floor(n / 1000) / 10}만` : won(n))

// '○○ 어디까지 늘릴 수 있어?' 답 (11/17, 서버 advisor.source_capacity_lines 문장): 한도는 위 상자, 재원은 이름 | 최대 금액 줄, 생활비 세부 항목은 그 아래 작은 줄.
// 글자는 서버 그대로이고 ' · ' 구분 기호만 화면에서 뺀다. 그 밖의 문장(참고 금액 안내 등)은 아래 작은 글씨
const CAPACITY_HEAD = /^(.+) · (확인된 증액 한도) (.+)$/
const CAPACITY_ROW = /^(.+?) · 최대 (.+)$/
const CAPACITY_SUB = /^· (.+?) (\d.*원)$/  // '· 문화·여가 4만 5천 원' → 이름 · 금액 (금액은 첫 숫자부터)
// 11/20: 첫 줄이 한도가 아니라 안내 문장('… 충분해 보여요')이어도 재원 줄이 있으면 같은 모양으로 보여 준다
const CAPACITY_STATUS = /^([^\d·]+?) · (.+)$/  // 재원 이름(숫자 없음) · 금액 대신 사유
const isCapacity = (lines) => CAPACITY_HEAD.test(lines[0] ?? '') || lines.some((line) => CAPACITY_ROW.test(line))
// '17만 5천 원' → 175000. 막대 길이에만 쓴다 (금액 계산은 서버, 화면은 비율 표시만)
function wonOf(text) {
  const m = text.replace(/,/g, '').match(/^(?:(\d+)억)?\s*(?:(\d+)만)?\s*(?:(\d+)천)?\s*(\d+)?\s*원/)
  if (!m || !m.slice(1).some(Boolean)) return null
  const [, eok = 0, man = 0, cheon = 0, rest = 0] = m
  return eok * 1e8 + man * 1e4 + cheon * 1e3 + Number(rest)
}
function CapacityLines({ lines }) {
  const head = lines[0]?.match(CAPACITY_HEAD)
  const lead = [], rows = [], notes = []
  for (const line of head ? lines.slice(1) : lines) {
    const row = line.match(CAPACITY_ROW), sub = line.match(CAPACITY_SUB), status = rows.length && line.match(CAPACITY_STATUS)
    if (row) rows.push({ name: row[1], amount: row[2], value: wonOf(row[2]), subs: [] })
    else if (status) rows.push({ name: status[1], status: status[2], value: null, subs: [] })  // '저축 · 사용 가능한 잔액 미확인'
    else if (sub && rows.length) rows.at(-1).subs.push({ name: sub[1], amount: sub[2] })
    else (rows.length ? notes : lead).push(line)
  }
  const top = Math.max(0, ...rows.map((row) => row.value ?? 0))
  return <div className="mc-budget-capacity">
    {lead.map((line) => <p key={line} className="mc-budget-capacity-lead">{line}</p>)}
    {head && <div className="mc-budget-distribution-total"><div><span>{head[1]} {head[2]}</span><strong>{head[3]}</strong></div></div>}
    {rows.length > 0 && <section className="mc-budget-capacity-box" aria-label="가져올 수 있는 곳">
      <p className="mc-budget-capacity-title">어디서 얼마나 옮길 수 있나요</p>
      <ol className="mc-budget-capacity-rows">
        {rows.map((row, index) => <li key={row.name} className={row.name === '저축' ? 'is-last' : undefined}>
          <span className="mc-budget-capacity-no" aria-hidden="true">{index + 1}</span>
          <span className="mc-budget-capacity-name">{row.name}</span>
          {row.status ? <span className="mc-budget-capacity-status">{row.status}</span> : <strong className="mc-budget-capacity-amount">최대 {row.amount}</strong>}
          {top > 0 && row.value != null && <span className="mc-budget-capacity-bar" aria-hidden="true"><i style={{ width: `${Math.max(4, Math.round(row.value / top * 100))}%` }} /></span>}
          {row.name === '저축' && <p className="mc-budget-capacity-last">마지막 대안</p>}
          {row.subs.length > 0 && <ul className="mc-budget-capacity-subs" aria-label={`${row.name} 세부 항목`}>
            {row.subs.map((sub) => <li key={sub.name}>{sub.name} <b>{sub.amount}</b></li>)}
          </ul>}
        </li>)}
      </ol>
    </section>}
    {notes.map((line) => <p key={line} className="mc-note">{line}</p>)}
  </div>
}

function DistributionInput({ value, active, onSend, onCancel }) {
  const inputHelp = useId()
  const funding = Boolean(value.target)
  const categories = value.categories.filter((code) => code !== 'ETC')
  const [rows, setRows] = useState((value.values ?? []).filter((row) => categories.includes(row.category))
    .map((row) => ({ category: row.category, digits: String(row.amount) })))
  const allocated = rows.filter((row) => Number(row.digits) > 0)
  const sources = value.sources ?? [{ category: value.source, amount: value.amount }]
  const before = value.before ?? {}
  const total = rows.reduce((sum, row) => sum + Number(row.digits), 0)
  // 받는 항목(funding)은 처음부터 목표 결과로 — 아직 안 나눴다고 '460,000원 → 460,000원'처럼 보이지 않게 (11/17)
  const fixedChanges = (funding ? [{ category: value.target, amount: -value.amount }] : sources)
    .filter((row) => Number.isSafeInteger(before[row.category]))
    .map((row) => ({ category: row.category, before: before[row.category], after: before[row.category] - row.amount, diff: -row.amount }))
  const valid = allocated.length > 0 && total === value.amount && rows.every((row) => categories.includes(row.category)
    && Number.isSafeInteger(Number(row.digits)) && Number(row.digits) >= 0 && Number(row.digits) % 5000 === 0
    && (!funding || Number(row.digits) <= value.maximums?.[row.category]))
    && new Set(rows.map((row) => row.category)).size === rows.length
  const update = (index, patch) => setRows(rows.map((row, i) => i === index ? { ...row, ...patch } : row))
  const changed = funding || !value.values || allocated.length !== value.values.filter((row) => row.amount > 0).length
    || allocated.some((row) => !value.values.some((initial) => initial.category === row.category && initial.amount === Number(row.digits)))
  return <form className="mc-budget-amount mc-budget-distribution" onSubmit={(event) => {
    event.preventDefault()
    if (active && valid && changed && !value.needsRefresh && onSend) onSend(funding
      ? `기존 요청 대신 ${allocated.map((row) => `${allocationName(row.category)} 예산을 ${Number(row.digits)}원`).join(', ')} 줄여 ${allocationName(value.target)} 예산을 ${value.amount}원 늘려줘.`
      : `기존 배분 대신 ${sources.map((row) => `${allocationName(row.category)} 예산을 ${row.amount}원`).join(', ')} 줄여 ${allocated.map((row) => `${allocationName(row.category)}에 ${Number(row.digits)}원`).join(', ')} 나눠줘.`)
  }}>
    <fieldset disabled={!active || !onSend}>
      <legend>{funding ? '어느 항목을 줄일까요?' : '어디에 나눌까요?'}</legend>
      <div className="mc-budget-distribution-total">
        <div><span>{funding ? `${allocationName(value.target)}에 더할 돈` : `${sources.map((row) => allocationName(row.category)).join('·')}에서 줄인 돈`}</span><strong>{won(value.amount)}</strong></div>
        <span className={total === value.amount ? 'is-complete' : ''}>{won(value.amount - total)} 남음</span>
      </div>
      {fixedChanges.length > 0 && <div aria-label="현재 예산과 조정 후 예산">
        <p className="mc-note">현재 예산 → 조정 후 예산</p>
        <BudgetChanges changes={fixedChanges} />
      </div>}
      <p className="mc-note">{funding ? '줄여도 괜찮은 항목' : '받을 항목'}을 골라 주세요. 여러 개 골라도 돼요.{funding && ' 숫자는 줄일 수 있는 최대예요.'}</p>
      {/* 11/17: 항목은 작은 칩으로 한데 모으고, 고른 항목의 금액은 아래 상자 한 곳에서만 조정한다 */}
      <div className="mc-budget-chip-picks" aria-label={funding ? '줄일 항목 선택' : '받을 항목 선택'}>
        {categories.map((code) => {
          const selected = rows.some((row) => row.category === code)
          const limit = funding ? value.maximums?.[code] : null  // 이 항목에서 줄일 수 있는 최대 (서버 값)
          // 고르면 '남은 금액'과 '이 항목 최대' 중 작은 값을 5,000원 단위로 미리 채운다 (한 항목으로 충분하면 한 번에 끝)
          const fill = Math.floor(Math.min(value.amount - total, limit ?? value.amount) / 5000) * 5000
          return <button key={code} type="button" aria-pressed={selected} disabled={!selected && limit === 0} onClick={() => setRows(selected
            ? rows.filter((row) => row.category !== code) : [...rows, { category: code, digits: fill > 0 ? String(fill) : '' }])}>
            <span className="mc-pick-name">{allocationName(code)}</span>
            {Number.isSafeInteger(limit) && <small>{limit > 0 ? `최대 ${shortWon(limit)}` : '없음'}</small>}
          </button>
        })}
      </div>
      {rows.length > 0 && <div className="mc-budget-picked" aria-label="고른 항목 금액">
        {/* 칸이 세는 것 = 이 항목에서 줄일 금액(재원 고르기) / 이 항목이 받을 금액(나누기). 이름표가 없으면 ±1만이 예산을 늘리는지 헷갈린다 */}
        <p id={inputHelp} className="mc-fx-sr">금액을 직접 입력하거나 −1만·+1만 버튼으로 바꿔 주세요. 비워 두거나 0원인 항목은 바뀌지 않아요.</p>
        {rows.map((row, index) => {
          const kind = funding ? '줄일 금액' : '받을 금액'
          const amount = Number(row.digits)
          const maximum = Math.min(value.amount - total + amount, funding ? value.maximums[row.category] : value.amount)
          const changeAmount = (next) => update(index, { digits: next === '' ? '' : String(Math.min(Number(next), maximum)) })
          return <div key={row.category} className="mc-budget-distribution-row">
            <div className="mc-budget-picked-name">
              <strong>{allocationName(row.category)}</strong>
              {/* 11/17: '현재 → 조정 후'를 함께 — +1만을 누르면 오른쪽 숫자가 줄어드는(줄일 때) 것이 바로 보이게 */}
              <small className="mc-budget-distribution-change" aria-live="polite">
                {Number.isSafeInteger(before[row.category]) ? `${won(before[row.category])} → ${won(before[row.category] + (funding ? -amount : amount))}` : '계산 대기'}
              </small>
            </div>
            <div className="mc-budget-picked-amount">
            <span className="mc-budget-picked-kind" aria-hidden="true">{kind}</span>
            <div className="mc-budget-distribution-stepper">
              <button type="button" aria-label={`${allocationName(row.category)} ${kind} 10,000원 줄이기`} disabled={!amount} onClick={() => changeAmount(String(Math.max(0, amount - 10000)))}>−1만</button>
              <label><MoneyInput aria-label={`${allocationName(row.category)} ${kind} (원)`} aria-describedby={inputHelp} value={row.digits} onChange={changeAmount} placeholder="직접 입력" /><span>원</span></label>
              <button type="button" aria-label={`${allocationName(row.category)} ${kind} 10,000원 늘리기`} disabled={amount + 10000 > maximum} onClick={() => changeAmount(String(amount + 10000))}>+1만</button>
            </div>
            </div>
          </div>
        })}
        {total === value.amount && <small className="mc-budget-picked-help">다른 항목에 넣으려면 먼저 배분한 금액을 줄여 주세요.</small>}
      </div>}
      {rows.some((row) => Number(row.digits) % 5000 !== 0) && <p className="mc-note">5,000원 단위로 입력해 주세요.</p>}
      {value.needsRefresh
        ? <p className="mc-note">최신 지출을 확인한 뒤 변경안을 만들 수 있어요. <a href={PATHS.connections}>계좌·카드 연결 관리에서 내역 확인하기</a></p>
        : <>
          {value.preview && <p className="mc-note">최근 지출 내역이 아직 다 모이지 않아 지금까지 확인된 기록으로 계산해요.</p>}
          {/* 11/17: 남은 금액은 미리보기 버튼과 함께 화면 아래에 붙여 둔다 — 말풍선이 길어 위 상자가 안 보일 때도 얼마 남았는지 보이게 */}
          <div className="mc-budget-pick-foot">
            <p role="status" className={total === value.amount ? 'is-complete' : undefined}>
              {won(value.amount - total)} 남음<small> / {won(value.amount)}</small>
            </p>
            <button type="submit" className="mc-primary" disabled={!valid || !changed}>나눈 예산 미리보기</button>
          </div>
        </>}
      {onCancel && <button type="button" className="mc-link mc-budget-edit-cancel" onClick={onCancel}>수정 취소</button>}
    </fieldset>
  </form>
}

export default function BudgetAdjustment({ content, active, onPick, onApprove, onSend, onSalaryEdit, onSalaryReview, navigation }) {
  const [editing, setEditing] = useState(false)
  const adjustment = content.budgetAdjustment
  const proposal = adjustment.proposal
  const state = proposal?.state ?? adjustment.status
  const scheduleTitle = { scheduled: '다음 주기 변경을 예약했어요', applied: '예약한 설정을 반영했어요',
    replaced: '새 예약으로 교체된 변경안이에요', needs_review: '예약 내용을 다시 확인해 주세요' }[proposal?.scheduleStatus]
  const guidance = proposal && TERMINAL.has(state) ? null : adjustment.guidance
  const canApprove = active && adjustment.canApprove && proposal?.canApprove !== false && proposal?.state === 'awaiting_approval'
  const hasComparison = (adjustment.comparison?.plans ?? []).some((plan) => isVisibleAdjustmentPlan(plan, content.choices))
  const hasPreview = proposal || hasComparison
  const amountInput = !hasPreview && !TERMINAL.has(state) && adjustment.amountInput
  const distributionInput = !TERMINAL.has(state) && adjustment.distributionInput
  const reasonLines = amountInput && guidance?.reason ? guidance.reason.split('\n').filter(Boolean) : []
  const inputNotice = (amountInput || distributionInput) && adjustment.explanation?.title === '추천에서 제외한 방법과 이유'
    ? adjustment.explanation.brief : null
  const options = hasPreview
    ? (navigation?.choices ?? content.choices ?? []).filter((choice) => ['recommend', 'compare'].includes(choice.value)) : []
  const salaryAllocation = adjustment.phase === 'salary_allocation_review' && !TERMINAL.has(state)
  const showRequest = !amountInput && !distributionInput && !TERMINAL.has(state) && !salaryAllocation
  const pendingLines = guidance
    ? guidance.message && !['error', 'explanation'].includes(guidance.kind) ? [guidance.message] : []
    : content.lines ?? []
  return <div className="mc-bubble mc-budget-adjustment">
    {/* embedded = 월급 변경 후 예산 검토 화면 안의 미리보기 (화면 위에 같은 안내가 이미 있다) */}
    {!adjustment.embedded && <Summary request={showRequest ? guidance?.context ?? adjustment.requestSummary : null}
      effectiveOn={adjustment.planning?.effectiveOn ?? proposal?.effectiveOn} oneOff={adjustment.planning?.oneOff ?? proposal?.oneOff}
      keep={state === 'awaiting_approval' && !hasComparison} />}
    {adjustment.planning && (adjustment.planning.expense || adjustment.planning.incomeBefore !== adjustment.planning.incomeAfter) && <div role="note" className="mc-budget-facts">
      {adjustment.planning.expense && <p>필요한 돈 {won(adjustment.planning.expense.total)} · 따로 마련한 돈 {won(adjustment.planning.expense.setAside)} · 예산에서 추가로 마련할 돈 {won(adjustment.planning.expense.additional)}</p>}
      {adjustment.planning.incomeBefore !== adjustment.planning.incomeAfter && <p>월 실수령액 {won(adjustment.planning.incomeBefore)} → <strong>{won(adjustment.planning.incomeAfter)}</strong></p>}
    </div>}
    {adjustment.recommendationAdvice && <section aria-label="추천하는 이유" className="mc-budget-advice">
      <p><strong>{adjustment.recommendationAdvice.title}</strong></p>
      <ul>{adjustment.recommendationAdvice.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
    </section>}
    {!guidance && inputNotice && <p className="mc-budget-reason" role="note">{inputNotice}</p>}
    {guidance?.message && !salaryAllocation && (guidance.kind === 'error' || (!amountInput && !distributionInput && !hasPreview && !adjustment.recommendationAdvice)
      || (guidance.kind === 'explanation' && !adjustment.recommendationAdvice)) && (() => {
      const lines = guidance.message.split('\n').filter(Boolean)
      // 11/17: 한도 안내(설명 단계로 오는 '○○ · 확인된 증액 한도 …')도 표 모양으로
      return isCapacity(lines) ? <CapacityLines lines={lines} />
        : adjustment.phase === 'adjustment_frequency' ? <NagLines lines={lines} />
        : lines.map((line, index) => <p key={index} className={adjustment.phase === 'increase_review' ? 'mc-budget-warning' : undefined}>{line}</p>)
    })()}
    {guidance?.reason && (isCapacity(reasonLines) ? <CapacityLines lines={reasonLines} />
      : <p className="mc-budget-reason" role="note">{guidance.reason}</p>)}
    {adjustment.increaseWarning && !TERMINAL.has(state) && <p className="mc-budget-warning" role="note">{adjustment.increaseWarning}</p>}
    {(adjustment.phase === 'salary_handoff' || adjustment.salaryChange) && !TERMINAL.has(state)
      && <SalaryChange active={active} onEdit={onSalaryEdit} />}
    {salaryAllocation && <SalaryAllocation request={guidance?.context ?? adjustment.requestSummary} active={active} onReview={onSalaryReview} />}
    {amountInput && <AdjustmentAmount key={`${content.turn}-${amountInput.category}-${amountInput.direction}`} value={amountInput} active={active} onSend={onSend} />}
    {distributionInput && <DistributionInput key={content.turn} value={distributionInput} active={active} onSend={onSend} />}
    {!guidance && !proposal && !hasComparison && !amountInput && !salaryAllocation && !adjustment.recommendationAdvice && (() => {
      const lines = (content.lines ?? (content.text ? [content.text] : [])).filter((line) => !distributionInput || !line.endsWith('어디에 나눌까요?'))
      return isCapacity(lines) ? <CapacityLines lines={lines} />
        : adjustment.phase === 'adjustment_frequency' ? <NagLines lines={lines} /> : lines.map((line, i) => <p key={i}>{line}</p>)
    })()}
    {hasComparison && <Comparison comparison={adjustment.comparison} choices={content.choices} active={active} onPick={onPick} detail={adjustment.detail} />}
    {proposal && <section className="mc-budget-proposal" aria-label="예산 변경안">
      <h2>{state === 'saved' ? scheduleTitle ?? (proposal.effectiveOn ? '다음 주기 변경을 예약했어요' : '예산에 반영했어요') : state === 'expired' ? '만료된 변경안이에요'
        : proposal.salaryReview ? '월급 증가분 배분안' : '예산 변경안'}</h2>
      {proposal?.verificationNotice && <p className="mc-budget-reason">{proposal.verificationNotice}</p>}
      {proposal.scheduleStatus === 'needs_review' && <p role="alert">예약 후 설정이 달라져 자동 반영하지 않았어요. 최신 조건으로 다시 조정해 주세요.</p>}
      {proposal.previewRevision > 1 && <p>{proposal.previewRevision}차 조정안</p>}
      {(proposal.revisionSummary ?? []).map((line) => <p className="mc-budget-reason" key={line}>{line}</p>)}
      {proposal.salaryReview && proposal.transfers?.length > 0 ? <>
        <SalaryShares transfers={proposal.transfers} />
        <details className="mc-budget-details"><summary>항목별 예산 변화 보기</summary><BudgetChanges changes={proposal.changes} /></details>
      </> : <>
        <BudgetTransfers transfers={proposal.transfers} />
        <BudgetChanges changes={proposal.changes} />
      </>}
      {proposal.effectiveOn && !['replaced', 'needs_review'].includes(proposal.scheduleStatus) && state !== 'saved'
        && <p className="mc-budget-footnote">{proposal.oneOff ? '그 주기에만 반영하고 평소 배분 설정은 유지해요. ' : ''}새 예산은 적용될 때 확인된 수입·지출로 다시 계산해요.</p>}
      <Shortfalls rows={proposal.shortfalls} />
      <BudgetSettings proposal={proposal} />
      <RecoveryAllocations proposal={proposal} />
      {adjustment.status === 'awaiting_input' && pendingLines.map((line, index) => <p key={index} role="status">{line}</p>)}
      {proposal.state === 'awaiting_approval' && adjustment.distributionEdit && !adjustment.phase && editing && <DistributionInput
        key={proposal.proposalId} value={adjustment.distributionEdit} active={active} onSend={onSend} onCancel={() => setEditing(false)} />}
      {state === 'expired' && <p className="mc-budget-warning">조건이 바뀌었어요. 원하는 조정을 다시 입력해 주세요.</p>}
      {['canceled', 'rejected', 'failed'].includes(state) && <p className="mc-budget-warning">이 변경안은 적용할 수 없어요. 원하는 조정을 다시 입력해 주세요.</p>}
      {(adjustment.canApprove || !adjustment.phase) && state === 'awaiting_approval' && !editing && <button type="button" className="mc-primary" disabled={!canApprove} onClick={() => onApprove(proposal)}>{proposal.effectiveOn ? '다음 주기 변경 예약하기' : '예산 확정하기'}</button>}
      {proposal.state === 'awaiting_approval' && adjustment.distributionEdit && !adjustment.phase && !editing && <button
        type="button" className="mc-link mc-budget-edit-toggle" aria-expanded={false} disabled={!active || !onSend}
        onClick={() => setEditing(true)}>수정할래요</button>}
    </section>}
    {adjustment.alternatives && <Comparison comparison={adjustment.alternatives} readOnly detail={adjustment.detail} />}
    {adjustment.alternativesNotice && <p className="mc-budget-warning">{adjustment.alternativesNotice}</p>}
    {guidance?.next && <p>{guidance.next}</p>}
    {guidance?.details?.length > 0 && <details className="mc-budget-details" open={guidance.kind === 'explanation'}>
      <summary>추가로 확인할 내용</summary>{guidance.details.map((line, index) => <p key={index}>{line}</p>)}
    </details>}
    {!guidance && !TERMINAL.has(state) && adjustment.explanation?.title !== '금액 입력 방법' && <Explanation value={amountInput || distributionInput ? { ...adjustment.explanation, brief: null } : adjustment.explanation}
      detail={adjustment.detail} shown={[...(content.lines ?? []), inputNotice].filter(Boolean)} />}
    {(content.notices ?? []).map((line) => <p className="mc-budget-warning" key={line}>{line}</p>)}
    {options.length > 0 && !TERMINAL.has(state) && <div className="mc-budget-navigation" aria-label="예산 조정 보기 옵션">
      {options.map((choice) => <button key={choice.value} type="button" className="mc-link"
        disabled={!(navigation?.active ?? active)} title="현재 대화 조건으로 확인해요"
        onClick={() => (navigation?.onPick ?? onPick)(choice)}>{choice.label}</button>)}
    </div>}
  </div>
}

// 월급 설정 — 급여는 매달만 지원 (2026-10-19 결정, 주급은 MVP에서 제외)
// GET · PUT /api/budgets/salary/ (docs/api/salary.md). 저장에 성공해야 다음 화면으로 간다 (11/03 S1)
// 마이에서 현재 월급을 저장한 뒤에는 사용자가 선택해야 예산 검토를 시작한다
// 이미 등록한 급여일 · 월 실수령액을 바꾸고 [저장]을 누르면 가운데 창에서 이번 주기부터 / 다음 주기부터를 고른다 (applyFrom, 11/12)
//   이번 주기부터 → 저장 완료와 예산 검토 버튼 · 다음 주기부터 → 마이(또는 홈)로 + 토스트
//   단 첫 예산 승인 전(온보딩)에는 묻지 않고 바로 적용한다 — 서버도 생략하면 current_cycle 로 처리 (11/12 피드백)
// 11/12 피드백: 월 실수령액은 기본 만원 단위로 받고(285 → 2,850,000원), 칸 안 [만원|원] 전환으로 원 단위로도 정확히 받는다
//   상태(income)는 늘 원 단위 숫자 문자열 — 서버에 보내는 값은 그대로. 저장된 금액이 만원으로 안 떨어지면 원 단위로 연다
//   단위만 바꾸면 금액은 그대로 둔다 (2,453,322원 → 만원 칸엔 245 → 다시 원 칸엔 2,453,322). 만원 칸에서 고쳐야 만원 단위로 바뀐다
// 11/12 피드백: 급여일은 드롭다운 대신 숫자로 입력 (1~31). 숫자만 2자리까지 받고, 범위를 벗어나면 칸 아래에 바로 알려 준다
// 11/13 검수: 이번 주기를 받은 월급 기준(incomeBasis)으로 짠 뒤 실수령액을 이번 주기부터 바꾸면, 한 번 더 묻는다(SalaryBasisChoicePopup)
//   keepIncomeBasis 선택은 보존하고, 저장 후 예산 검토는 별도 버튼으로 시작한다
//   첫 예산 승인 전(적용 시점을 안 묻고 바로 이번 주기)에도 묻는다. 급여일도 이번 주기부터 바꾸면 주기가 새로 생겨 유지할 수 없으니 묻지 않는다(서버가 기준을 푼다)
//   29 · 30일은 그날이 없는 달에 말일로 계산한다는 안내를 붙인다 (API_RULES '매월 N일', utils/days.js dayIn)
//   칸 안 [직접 입력|말일] 전환. 말일 = 급여일 31 (31일이 없는 달은 말일이라 31 이 곧 매달 말일). 서버에는 31 을 보내고, 칸은 '말일'로 잠근다
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import MoneyInput from '../components/MoneyInput.jsx'
import StepBar from '../components/StepBar.jsx'
import ApiStatus from '../components/ApiStatus.jsx'
import ApplyTimingPopup from '../popups/ApplyTimingPopup.jsx'
import SalaryBasisChoicePopup from '../popups/SalaryBasisChoicePopup.jsx'
import { usePopup } from '../popups/context.js'
import { toast } from '../utils/toast.js'
import { apiSend, useApi } from '../utils/api.js'
import { dayBad, paydayText } from '../utils/days.js'
import DayInput from '../components/DayInput.jsx'
import { won } from '../utils/money.js'
import { PATHS } from '../routes.js'
import SalaryReviewActions from '../components/SalaryReviewActions.jsx'
import { salaryReviewContext, salaryReviewUrl } from '../utils/salaryReview.js'

const MAN = 10000
const MAN_DIGITS = 9  // 만원 칸은 9자리(9,999억 원)까지
const toMan = (won) => (won === '' ? '' : String(Math.floor(Number(won) / MAN)))  // 원 숫자 → 만원 숫자 (만원 아래 버림)

export default function SalaryPage() {
  const { data, error } = useApi('/api/budgets/salary/')
  const context = salaryReviewContext(useLocation())
  if (!data) return <Layout title="월급 설정" showBack><ApiStatus error={error} /><SalaryReviewActions context={context} note /></Layout>
  return <SalaryForm saved={data} />
}

function SalaryForm({ saved }) {
  const navigate = useNavigate()
  const location = useLocation()
  const context = salaryReviewContext(location)
  const fromChat = Boolean(context.budgetReturn)
  const manage = fromChat || context.salaryReview || location.state?.from === 'my'
  const [savedSalary, setSavedSalary] = useState(context.salaryReview)
  const { openPopup, closePopup } = usePopup()
  const pending = saved.pendingPaydayChange  // { payday, effectiveOn } | null
  const pendingIncome = saved.pendingIncomeChange  // { takeHomeIncome, effectiveOn } | null (11/12)
  const shownIncome = (fromChat ? null : pendingIncome?.takeHomeIncome) ?? saved.takeHomeIncome
  const [income, setIncome] = useState(shownIncome == null ? '' : String(shownIncome))  // 원 단위
  const [unit, setUnit] = useState(shownIncome % MAN ? 'won' : 'man')  // 'man' = 만원 · 'won' = 원 (null % MAN 은 0)
  const man = unit === 'man'
  const onMan = (d) => { const v = d.slice(0, MAN_DIGITS); setIncome(v === '' ? '' : String(Number(v) * MAN)) }
  const switchUnit = () => setUnit(man ? 'won' : 'man')  // 금액(원)은 그대로, 보여 주는 단위만 바꾼다
  // 첫 예산을 승인했는지 (GET /api/auth/me/ onboardingCompleted). 승인 전이면 급여일을 바꿔도 적용 시점을 묻지 않는다
  const me = useApi('/api/auth/me/')
  const budgetStarted = me.data?.onboardingCompleted === true
  // 이미 등록한 월급을 고치는데 첫 예산 승인 여부를 아직 모르면 저장을 잠근다 (모르고 보내면 묻지 않고 보내져 서버가 applyFrom 400)
  const waitMe = saved.payday != null && me.loading
  const [payday, setPayday] = useState(String((fromChat ? null : pending?.payday) ?? saved.payday ?? ''))
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  // 처음 등록이면 바로 적용. 첫 예산을 승인한 뒤 화면을 열 때 값(예약이 있으면 예약 값)에서 바꿨으면 적용 시점을 묻는다
  const edited = Number(income) !== shownIncome || Number(payday) !== (pending?.payday ?? saved.payday)
  const asks = budgetStarted && saved.payday != null && edited

  // applyFrom: 창에서 고른 값. 안 물었는데 예약이 있으면 next_cycle 로 보내 예약을 그대로 둔다
  // keepIncomeBasis: 받은 월급 기준을 둘지 고른 경우만 (true · false)
  const save = async (applyFrom, keepIncomeBasis) => {
    setBusy(true)
    setError('')
    try {
      await apiSend('/api/budgets/salary/', 'PUT', {
        takeHomeIncome: Number(income),
        payday: Number(payday),
        ...(applyFrom ? { applyFrom } : {}),
        ...(keepIncomeBasis !== undefined ? { keepIncomeBasis } : {}),
        ...(fromChat ? { preserveUnchangedSchedules: true } : {}),
      })
      if ((manage && applyFrom !== 'next_cycle') || (asks && applyFrom === 'current_cycle')) {
        const next = { ...context, salaryReview: true }
        setSavedSalary(true)
        setBusy(false)
        navigate(salaryReviewUrl(PATHS.salary, next), { replace: true, state: { from: 'my', ...next } })
        return
      }
      if (asks) {
        toast('다음 주기부터 바뀌어요')
        navigate(manage ? PATHS.my : PATHS.home, { replace: true })
      } else if (manage) navigate(PATHS.my, { replace: true })  // 안 물은 경우 = 바꾼 게 없거나 첫 예산 승인 전이라 '승인한 예산은 그대로' 안내가 맞지 않다
      else navigate(PATHS.fixed)
    } catch (err) {
      const first = Object.values(err.fields ?? {}).flat().find((m) => typeof m === 'string')
      setError(first ?? err.message)
      setBusy(false)
    }
  }

  const onSubmit = async (e) => {
    e.preventDefault()
    if (busy) return
    const amount = Number(income)
    if (!income || amount < 1) { setError('월 실수령액을 입력해 주세요.'); return }
    if (payday === '') { setError('급여일을 입력해 주세요.'); return }
    if (dayBad(payday)) { setError('급여일은 1~31 중에서 입력해 주세요.'); return }
    // 받은 월급 기준이 걸린 주기에서 실수령액을 이번 주기부터 바꾸면 기준을 하나 더 고른다
    // (새 월급이 받은 금액과 같거나 급여일도 바꾸면 고를 게 없다 — 서버가 기준을 푼다)
    const basis = saved.incomeBasis
    const asksBasis = basis != null && amount !== saved.takeHomeIncome && amount !== basis && Number(payday) === saved.payday
    const pickBasis = (applyFrom) => openPopup(
      <SalaryBasisChoicePopup received={basis} next={amount} onPick={(keep) => { closePopup(); save(applyFrom, keep) }} />,
    )
    if (fromChat) {
      if (asksBasis) pickBasis('current_cycle')
      else save('current_cycle')
    } else if (asks) openPopup(<ApplyTimingPopup onPick={(k) => { closePopup(); if (k === 'current_cycle' && asksBasis) pickBasis(k); else save(k) }} />)
    else if (pending || pendingIncome) save('next_cycle')
    else if (asksBasis) pickBasis(undefined)  // 첫 예산 승인 전: 시점 없이 바로 이번 주기로 바뀐다
    else save(undefined)
  }

  if (savedSalary) {
    const next = { ...context, salaryReview: true }
    return <Layout title="월급 설정" showBack>
      <h1>월급은 저장됐어요.</h1>
      <p className="mc-muted">월 실수령액 {won(Number(income))} · 매월 {paydayText(Number(payday))}</p>
      <p className="mc-note">새 예산을 승인하기 전까지 기존 승인 예산은 그대로예요.</p>
      <div className="mc-form">
        <button type="button" className="mc-primary" onClick={() => navigate(salaryReviewUrl(PATHS.protect, next), {
          state: { from: 'my', refreshRecommendation: true, ...next },
        })}>새 조건으로 예산 검토</button>
        <SalaryReviewActions context={next} />
      </div>
    </Layout>
  }

  return (
    <Layout title="월급 설정" showBack foot={<button className="mc-primary" form="mc-form" type="submit" disabled={busy || waitMe}>{busy ? '저장 중…' : manage ? '저장' : '저장하고 계속'}</button>}>
      {!manage && <StepBar step={3} />}
      <div className="mc-eyebrow">월급 설정</div>
      <h1>월급과 급여일을 알려주세요</h1>
      <p className="mc-muted">급여일부터 다음 급여일 전날까지가 한 예산 주기예요.</p>
      {fromChat && <p className="mc-note">현재 적용된 월급을 수정해요. 저장만으로 예산을 다시 계산하지 않아요.</p>}
      <form className="mc-form" id="mc-form" onSubmit={onSubmit}>
        {/* 월 실수령액 → 아래 급여일 (11/12). 단위(만원 · 원)는 월급 칸 안 오른쪽 끝의 작은 전환 버튼으로 고른다 */}
        <div className="mc-card mc-salary-row">
          <label className="mc-field">월 실수령액
            <span className="mc-unitinput mc-unit-toggle">
              {man ? <MoneyInput id="mc-salary" value={toMan(income)} onChange={onMan} placeholder="280" />
                : <MoneyInput id="mc-salary" value={income} onChange={setIncome} placeholder="2,800,000" />}
              <span className="mc-unit-switch" role="group" aria-label="금액 단위">
                {[['man', '만원'], ['won', '원']].map(([k, label]) => (
                  <button key={k} type="button" aria-pressed={unit === k} onClick={() => { if (unit !== k) switchUnit() }}>{label}</button>
                ))}
              </span>
            </span>
          </label>
          {/* 급여일: 칸 안 [직접 입력|말일] 전환 (11/12, 공용 DayInput — 고정지출 · 저축 · 대출 날짜 칸과 같은 모양) */}
          <label className="mc-field">급여일
            <DayInput id="mc-payday" what="급여일" value={payday} onChange={(d) => { setPayday(d); setError('') }} />
          </label>
        </div>
        {pending && (
          <p className="mc-note">
            {pending.effectiveOn.slice(5).replace('-', '월 ')}일부터 급여일이 매월 {paydayText(pending.payday)}로 바뀌어요. 지금은 매월 {paydayText(saved.payday)} 기준이에요.
          </p>
        )}
        {pendingIncome && (
          <p className="mc-note">
            {pendingIncome.effectiveOn.slice(5).replace('-', '월 ')}일부터 월 실수령액이 {won(pendingIncome.takeHomeIncome)}으로 바뀌어요. 지금은 {won(saved.takeHomeIncome)} 기준이에요.
          </p>
        )}
        {fromChat && ((pending && Number(payday) !== saved.payday) || (pendingIncome && Number(income) !== saved.takeHomeIncome)) && (
          <p className="mc-note">지금 바꾸는 항목은 즉시 적용하고, 해당 항목의 다음 주기 변경 예약은 취소해요. 수정하지 않은 항목의 예약은 유지해요.</p>
        )}
        <div className="mc-error" role="alert">{error}</div>
      </form>
      <SalaryReviewActions context={context} busy={busy} />
    </Layout>
  )
}

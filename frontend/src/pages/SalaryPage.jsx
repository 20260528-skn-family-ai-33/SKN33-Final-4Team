// 월급 설정 — 급여는 매달만 지원 (2026-10-19 결정, 주급은 MVP에서 제외)
// GET · PUT /api/budgets/salary/ (docs/api/salary.md). 저장에 성공해야 다음 화면으로 간다 (11/03 S1)
// 마이에서 들어오면(location.state.from === 'my') 단계 표시줄 없이 저장 뒤 마이로 돌아간다
// 이미 등록한 급여일을 바꾸면 이번 주기부터 / 다음 주기부터 중 하나를 꼭 고른다 (paydayApplyFrom, 10/28 결정)
//   단 첫 예산 승인 전(온보딩)에는 묻지 않고 바로 적용한다 — 서버도 생략하면 current_cycle 로 처리 (11/12 피드백)
// 11/12 피드백: 월 실수령액은 기본 만원 단위로 받고(285 → 2,850,000원), 칸 안 [만원|원] 전환으로 원 단위로도 정확히 받는다
//   상태(income)는 늘 원 단위 숫자 문자열 — 서버에 보내는 값은 그대로. 저장된 금액이 만원으로 안 떨어지면 원 단위로 연다
//   단위만 바꾸면 금액은 그대로 둔다 (2,453,322원 → 만원 칸엔 245 → 다시 원 칸엔 2,453,322). 만원 칸에서 고쳐야 만원 단위로 바뀐다
// 11/12 피드백: 급여일은 드롭다운 대신 숫자로 입력 (1~31). 숫자만 2자리까지 받고, 범위를 벗어나면 칸 아래에 바로 알려 준다
//   29 · 30일은 그날이 없는 달에 말일로 계산한다는 안내를 붙인다 (API_RULES '매월 N일', utils/days.js dayIn)
//   칸 안 [직접 입력|말일] 전환. 말일 = 급여일 31 (31일이 없는 달은 말일이라 31 이 곧 매달 말일). 서버에는 31 을 보내고, 칸은 '말일'로 잠근다
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import MoneyInput from '../components/MoneyInput.jsx'
import StepBar from '../components/StepBar.jsx'
import ApiStatus from '../components/ApiStatus.jsx'
import { apiSend, useApi } from '../utils/api.js'
import { LAST_DAY, paydayText } from '../utils/days.js'
import { PATHS } from '../routes.js'

const APPLY = [['current_cycle', '이번 주기부터'], ['next_cycle', '다음 주기부터']]
const MAN = 10000
const MAN_DIGITS = 9  // 만원 칸은 9자리(9,999억 원)까지
const dayDigits = (s) => s.replace(/\D/g, '').replace(/^0+/, '').slice(0, 2)  // 숫자만, 앞 0 빼고 2자리까지
const dayOk = (d) => d !== '' && Number(d) >= 1 && Number(d) <= 31
const toMan = (won) => (won === '' ? '' : String(Math.floor(Number(won) / MAN)))  // 원 숫자 → 만원 숫자 (만원 아래 버림)

export default function SalaryPage() {
  const { data, error } = useApi('/api/budgets/salary/')
  if (!data) return <Layout title="월급 설정" showBack><ApiStatus error={error} /></Layout>
  return <SalaryForm saved={data} />
}

function SalaryForm({ saved }) {
  const navigate = useNavigate()
  const manage = useLocation().state?.from === 'my'
  const pending = saved.pendingPaydayChange  // { payday, effectiveOn } | null
  const [income, setIncome] = useState(saved.takeHomeIncome == null ? '' : String(saved.takeHomeIncome))  // 원 단위
  const [unit, setUnit] = useState(saved.takeHomeIncome % MAN ? 'won' : 'man')  // 'man' = 만원 · 'won' = 원 (null % MAN 은 0)
  const man = unit === 'man'
  const onMan = (d) => { const v = d.slice(0, MAN_DIGITS); setIncome(v === '' ? '' : String(Number(v) * MAN)) }
  const switchUnit = () => setUnit(man ? 'won' : 'man')  // 금액(원)은 그대로, 보여 주는 단위만 바꾼다
  // 첫 예산을 승인했는지 (GET /api/auth/me/ onboardingCompleted). 승인 전이면 급여일을 바꿔도 적용 시점을 묻지 않는다
  const budgetStarted = useApi('/api/auth/me/').data?.onboardingCompleted === true
  const [payday, setPayday] = useState(String(pending?.payday ?? saved.payday ?? ''))  // 입력 글자 그대로 (숫자만)
  const dayBad = payday !== '' && !dayOk(payday)  // 입력했는데 1~31 이 아님 → 칸 아래에 바로 안내
  // [말일] = 급여일 31 로 보내고 칸은 잠근다. 저장된 31 은 말일로 연다 (직접 31 을 치면 숫자 그대로 두고 아래 안내만)
  const [lastDay, setLastDay] = useState(() => (pending?.payday ?? saved.payday) === LAST_DAY)
  const [applyFrom, setApplyFrom] = useState(pending ? 'next_cycle' : null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  // 처음 등록이면 바로 적용. 이미 등록한 급여일과 다르면 적용 시점을 묻는다
  const changing = budgetStarted && saved.payday != null && dayOk(payday) && Number(payday) !== saved.payday

  const onSubmit = async (e) => {
    e.preventDefault()
    if (busy) return
    const amount = Number(income)
    if (!income || amount < 1) { setError('월 실수령액을 입력해 주세요.'); return }
    if (payday === '') { setError('급여일을 입력해 주세요.'); return }
    if (dayBad) { setError('급여일은 1~31 중에서 입력해 주세요.'); return }
    if (changing && !applyFrom) { setError('이번 주기부터인지 다음 주기부터인지 골라 주세요.'); return }
    setBusy(true)
    setError('')
    try {
      await apiSend('/api/budgets/salary/', 'PUT', {
        takeHomeIncome: amount,
        payday: Number(payday),
        ...(changing ? { paydayApplyFrom: applyFrom } : {}),
      })
      if (manage) navigate(PATHS.my, { replace: true, state: { budgetNote: true } })
      else navigate(PATHS.fixed)
    } catch (err) {
      const first = Object.values(err.fields ?? {}).flat().find((m) => typeof m === 'string')
      setError(first ?? err.message)
      setBusy(false)
    }
  }

  return (
    <Layout title="월급 설정" showBack foot={<button className="mc-primary" form="mc-form" type="submit" disabled={busy}>{busy ? '저장 중…' : manage ? '저장' : '저장하고 계속'}</button>}>
      {!manage && <StepBar step={3} />}
      <div className="mc-eyebrow">월급 설정</div>
      <h1>월급과 급여일을 알려주세요</h1>
      <p className="mc-muted">급여일부터 다음 급여일 전날까지가 한 예산 주기예요.</p>
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
          {/* 급여일: 칸 안 [직접 입력|말일] 전환 (11/12, 월급 [만원|원] 과 같은 모양) */}
          <label className="mc-field">급여일
            <span className="mc-unitinput mc-unit-toggle mc-day-toggle">
              <input id="mc-payday" className={dayBad ? 'mc-bad' : undefined} type="text" inputMode="numeric" autoComplete="off"
                placeholder="25" value={lastDay ? '말일' : payday} disabled={lastDay} aria-invalid={dayBad}
                aria-describedby={dayBad ? 'mc-payday-error' : undefined}
                onChange={(e) => { setPayday(dayDigits(e.target.value)); setError('') }} />
              {/* '일'은 입력한 숫자 바로 뒤에 붙는다: 같은 글자를 안 보이게 깔고 그 뒤에 '일' (빈 칸이면 예시 25 뒤) */}
              {!lastDay && <span className="mc-day-suffix" aria-hidden="true"><span>{payday || '25'}</span>일</span>}
              <span className="mc-unit-switch" role="group" aria-label="급여일 입력 방식">
                {[[false, '직접 입력'], [true, '말일']].map(([last, label]) => (
                  <button key={label} type="button" aria-pressed={lastDay === last}
                    onClick={() => { if (lastDay !== last) { setLastDay(last); setPayday(last ? String(LAST_DAY) : ''); setError('') } }}>{label}</button>
                ))}
              </span>
            </span>
          </label>
        </div>
        {dayBad && <p className="mc-field-error" id="mc-payday-error" role="alert">급여일은 1~31 중에서 입력해 주세요.</p>}
        {dayOk(payday) && Number(payday) >= 29 && !lastDay && <p className="mc-note">{payday}일이 없는 달은 말일을 급여일로 계산해요.</p>}
        {pending && (
          <p className="mc-note">
            {pending.effectiveOn.slice(5).replace('-', '월 ')}일부터 급여일이 매월 {paydayText(pending.payday)}로 바뀌어요. 지금은 매월 {paydayText(saved.payday)} 기준이에요.
          </p>
        )}
        {changing && (
          <div className="mc-card">
            <h2>바뀐 급여일은 언제부터 적용할까요?</h2>
            <div className="mc-seg">
              {APPLY.map(([k, label]) => (
                <button key={k} type="button" aria-pressed={applyFrom === k} onClick={() => setApplyFrom(k)}>{label}</button>
              ))}
            </div>
          </div>
        )}
        <div className="mc-error" role="alert">{error}</div>
      </form>
    </Layout>
  )
}

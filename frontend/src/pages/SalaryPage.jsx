// 월급 설정 — 급여는 매달만 지원 (2026-10-19 결정, 주급은 MVP에서 제외)
// GET · PUT /api/budgets/salary/ (docs/api/salary.md). 저장에 성공해야 다음 화면으로 간다 (11/03 S1)
// 이미 등록한 급여일을 바꾸면 이번 주기부터 / 다음 주기부터 중 하나를 꼭 고른다 (paydayApplyFrom, 10/28 결정)
import { useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import MoneyInput from '../components/MoneyInput.jsx'
import StepBar from '../components/StepBar.jsx'
import ApiStatus from '../components/ApiStatus.jsx'
import { apiSend, useApi } from '../utils/api.js'
import { MONTH_DAYS } from '../utils/days.js'
import { PATHS } from '../routes.js'

const APPLY = [['current_cycle', '이번 주기부터'], ['next_cycle', '다음 주기부터']]

export default function SalaryPage() {
  const { data, error } = useApi('/api/budgets/salary/')
  if (!data) return <Layout title="월급 설정" showBack><ApiStatus error={error} /></Layout>
  return <SalaryForm saved={data} />
}

function SalaryForm({ saved }) {
  const navigate = useNavigate()
  const pending = saved.pendingPaydayChange  // { payday, effectiveOn } | null
  const [income, setIncome] = useState(saved.takeHomeIncome == null ? '' : String(saved.takeHomeIncome))
  const [payday, setPayday] = useState(pending?.payday ?? saved.payday ?? '')
  const [applyFrom, setApplyFrom] = useState(pending ? 'next_cycle' : null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  // 처음 등록이면 바로 적용. 이미 등록한 급여일과 다르면 적용 시점을 묻는다
  const changing = saved.payday != null && payday !== '' && Number(payday) !== saved.payday

  const onSubmit = async (e) => {
    e.preventDefault()
    if (busy) return
    const amount = Number(income)
    if (!income || amount < 1) { setError('월 실수령액을 입력해 주세요.'); return }
    if (payday === '') { setError('급여일을 골라 주세요.'); return }
    if (changing && !applyFrom) { setError('이번 주기부터인지 다음 주기부터인지 골라 주세요.'); return }
    setBusy(true)
    setError('')
    try {
      await apiSend('/api/budgets/salary/', 'PUT', {
        takeHomeIncome: amount,
        payday: Number(payday),
        ...(changing ? { paydayApplyFrom: applyFrom } : {}),
      })
      navigate(PATHS.fixed)
    } catch (err) {
      const first = Object.values(err.fields ?? {}).flat().find((m) => typeof m === 'string')
      setError(first ?? err.message)
      setBusy(false)
    }
  }

  return (
    <Layout title="월급 설정" showBack foot={<button className="mc-primary" form="mc-form" type="submit" disabled={busy}>{busy ? '저장 중…' : '저장하고 계속'}</button>}>
      <StepBar step={3} />
      <div className="mc-eyebrow">월급 설정</div>
      <h1>월급과 급여일을 알려주세요</h1>
      <p className="mc-muted">급여일부터 다음 급여일 전날까지가 한 예산 주기예요.</p>
      <form className="mc-form" id="mc-form" onSubmit={onSubmit}>
        <label className="mc-field">월 실수령액<MoneyInput value={income} onChange={setIncome} placeholder="2,800,000" /></label>
        <label className="mc-field">급여일
          <select value={payday} onChange={(e) => setPayday(e.target.value === '' ? '' : Number(e.target.value))}>
            <option value="">고르기</option>
            {MONTH_DAYS.map((d) => <option key={d} value={d}>매월 {d}일</option>)}
          </select>
        </label>
        {pending && (
          <p className="mc-note">
            {pending.effectiveOn.slice(5).replace('-', '월 ')}일부터 급여일이 매월 {pending.payday}일로 바뀌어요. 지금은 매월 {saved.payday}일 기준이에요.
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

// 월급 설정 (장고: budgets/templates/budgets/salary.html)
import { useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import StepBar from '../components/StepBar.jsx'
import { MONTH_DAYS } from '../utils/days.js'
import { PATHS } from '../routes.js'

const CYCLES = ['매달', '매주']

export default function SalaryPage() {
  const navigate = useNavigate()
  const [cycle, setCycle] = useState('매달')

  const onSubmit = (e) => {
    e.preventDefault()
    navigate(PATHS.fixed)  // 저장은 API 연결 후
  }

  return (
    <Layout title="월급 설정" showBack foot={<button className="mc-primary" form="mc-form" type="submit">저장하고 계속</button>}>
      <StepBar step={3} />
      <div className="mc-eyebrow">월급 설정</div>
      <h1>월급과 급여일을 알려주세요</h1>
      <p className="mc-muted">급여일부터 다음 급여일 전날까지가 한 예산 주기예요.</p>
      <h2 style={{ marginTop: 20 }}>급여 주기</h2>
      <div className="mc-tabs">
        {CYCLES.map((c) => <button key={c} type="button" aria-pressed={cycle === c} onClick={() => setCycle(c)}>{c}</button>)}
      </div>
      <form className="mc-form" id="mc-form" onSubmit={onSubmit}>
        <label className="mc-field">월 실수령액<input inputMode="numeric" min="0" name="salary" step="1" type="number" defaultValue={2_800_000} /></label>
        <label className="mc-field">급여일<select>{MONTH_DAYS.map((d) => <option key={d}>{d}일</option>)}</select></label>
        <div className="mc-error" role="alert"></div>
      </form>
    </Layout>
  )
}

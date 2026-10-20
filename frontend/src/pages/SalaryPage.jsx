// 월급 설정 — 급여는 매달만 지원 (2026-10-19 결정, 주급은 MVP에서 제외)
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import StepBar from '../components/StepBar.jsx'
import { MONTH_DAYS } from '../utils/days.js'
import { PATHS } from '../routes.js'

export default function SalaryPage() {
  const navigate = useNavigate()

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
      <form className="mc-form" id="mc-form" onSubmit={onSubmit}>
        <label className="mc-field">월 실수령액<input inputMode="numeric" min="0" name="salary" step="1" type="number" defaultValue={2_800_000} /></label>
        <label className="mc-field">급여일<select>{MONTH_DAYS.map((d) => <option key={d}>매월 {d}일</option>)}</select></label>
        <div className="mc-error" role="alert"></div>
      </form>
    </Layout>
  )
}

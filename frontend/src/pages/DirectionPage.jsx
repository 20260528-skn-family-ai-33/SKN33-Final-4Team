// 예산 산정 방향
// 방향을 고르면 버튼 바로 아래에 지킬 돈 추천액 미리보기가 열린다 (다음 화면과 같은 계산)
import { Fragment, useState } from 'react'
import { useNavigate, useLocation } from 'react-router'
import Layout from '../components/Layout.jsx'
import StepBar from '../components/StepBar.jsx'
import { won } from '../utils/money.js'
import { CUSHION_GOAL, DIRS, monthsToGoal, recommendFor } from '../utils/direction.js'
import { PATHS } from '../routes.js'

const pct = (rate) => Math.round(rate * 100)

function Preview({ dir }) {
  const [name] = DIRS[dir]
  const r = recommendFor(dir)
  return (
    <div className="mc-card mc-direction-preview" aria-live="polite">
      <div className="mc-eyebrow">선택한 방향 미리보기</div>
      <h2>{name} · 지킬 돈</h2>
      <div className="mc-protected-values">
        <div className="mc-protected-item">
          <div className="mc-row"><span>생활비</span><strong>{won(r.livingBudget)}</strong></div>
          <p className="mc-projection">하루 약 <b>{won(Math.floor(r.livingBudget / 30 / 100) * 100)}</b>을 쓸 수 있어요.<small>한 달 30일 기준 · 다음 단계에서 카테고리별로 나눠요</small></p>
        </div>
        <div className="mc-protected-item">
          <div className="mc-row"><span>저축 목표액</span><strong>{won(r.savingFloor)}</strong></div>
          <p className="mc-projection">1년 모으면 <b>{won(r.savingFloor * 12)}</b>이 모여요.<small>매달 같은 금액을 저축하는 기준 · 이자 제외</small></p>
        </div>
        <div className="mc-protected-item">
          <div className="mc-row"><span>월 쿠션 적립금</span><strong>{won(r.cushionMonthly)}</strong></div>
          <p className="mc-projection">약 <b>{monthsToGoal(r.cushionMonthly)}개월</b>이면 쿠션 목표 {won(CUSHION_GOAL)}에 닿아요.<small>목표 = 월급의 3배</small></p>
        </div>
      </div>
      <p className="mc-note" style={{ paddingBottom: 0 }}>
        월급에서 고정지출을 뺀 {won(r.available)} 기준<br />
        생활비 {pct(r.livingRate)}% · 저축 {pct(r.savingRate)}% · 남는 돈의 절반을 쿠션에 적립해요.
      </p>
    </div>
  )
}

export default function DirectionPage() {
  const navigate = useNavigate()
  const savingsMin = useLocation().state?.savingsMin ?? 0   // 저축 확인에서 고른 저축 최소 금액
  const [dir, setDir] = useState(null)   // DIRS 순번
  const [error, setError] = useState('')

  const onNext = () => {
    if (dir === null) { setError('예산 산정 방향을 하나 선택해 주세요.'); return }
    navigate(PATHS.protect, { state: { dir, savingsMin } })  // 저장은 API 연결 후
  }

  return (
    <Layout title="예산 산정 방향" showBack foot={<button type="button" className="mc-primary" onClick={onNext}>다음</button>}>
      <StepBar step={5} />
      <div className="mc-eyebrow">예산 산정 방향</div>
      <h1>어떤 방향으로 나눌까요?</h1>
      <p className="mc-muted">고른 방향에 맞춰 지킬 돈과 카테고리 예산을 추천해요.</p>
      {DIRS.map(([name, desc], i) => (
        <Fragment key={name}>
          <button type="button" className="mc-choice" aria-pressed={dir === i} onClick={() => { setDir(i); setError('') }}>
            <strong>{name}</strong><span className="mc-muted">{desc}</span>
          </button>
          {dir === i && <Preview dir={i} />}
        </Fragment>
      ))}
      {error && <p className="mc-error" role="alert">{error}</p>}
      <p className="mc-note">언제든 마이에서 바꿀 수 있어요.</p>
    </Layout>
  )
}

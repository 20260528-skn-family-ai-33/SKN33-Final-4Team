// 지킬 돈 설정
// 생활비 · 저축 목표액 · 월 쿠션 적립금을 정한다. 금액을 고치면 아래 안내와 합계가 바로 다시 계산된다
// 생활비 = 한 달 생활비 전체 (별도 '최소 생활비'는 2026-10-15 기획에서 뺐다. 다음 화면에서 카테고리별로 나눈다)
// 쿠션은 '보호 하한' 대신 매달 적립할 금액으로 정한다 (2026-10-21)
// 항목 옆 % = 배분 가능한 금액(월급 - 고정지출) 중 비율. 금액을 고치면 같이 바뀐다
// 추천값은 앞 화면(예산 산정 방향)에서 고른 방향으로 계산 — utils/direction.js
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import MoneyInput from '../components/MoneyInput.jsx'
import StepBar from '../components/StepBar.jsx'
import { won } from '../utils/money.js'
import { CUSHION_GOAL, DIRS, FIXED, IRREGULAR, MONTHLY, monthsToGoal, recommendFor } from '../utils/direction.js'
import { PATHS } from '../routes.js'

const BALANCE = 2_800_000   // 연결 계좌 잔액 (시안값)
const CUSHION_REQUIRED = BALANCE < CUSHION_GOAL   // 잔액 < 월급×3 이면 쿠션 필수
const ROWS = [['livingBudget', '생활비'], ['savingFloor', '저축 목표액'], ['cushionMonthly', '월 쿠션 적립금']]

// 칸 아래 한 줄 안내
function hint(key, value) {
  if (!Number.isFinite(value) || value < 0) return '0 이상의 금액을 입력해 주세요.'
  if (key === 'livingBudget') return <>하루 약 <b>{won(Math.floor(value / 30 / 100) * 100)}</b>을 쓸 수 있어요. <span>(30일 기준)</span></>
  if (key === 'savingFloor') return <>1년 모으면 <b>{won(value * 12)}</b>이 모여요. <span>(매달 같은 금액 · 이자 제외)</span></>
  if (value === 0) return CUSHION_REQUIRED ? '잔액이 월급의 3배보다 적어서 쿠션을 꼭 적립해야 해요.' : '쿠션을 적립하지 않아요.'
  return <>약 <b>{monthsToGoal(value)}개월</b>이면 쿠션 목표 {won(CUSHION_GOAL)}에 닿아요.</>
}

export default function ProtectPage() {
  const navigate = useNavigate()
  const dir = useLocation().state?.dir   // 예산 산정 방향에서 넘겨받은 순번
  const RECOMMEND = recommendFor(dir)
  const reset = () => Object.fromEntries(ROWS.map(([k]) => [k, String(RECOMMEND[k])]))
  // 칸에 적힌 글자 그대로 기억 (빈 칸도 허용하려고 문자열로)
  const [values, setValues] = useState(reset)
  const [error, setError] = useState('')
  const num = (k) => (values[k] === '' ? NaN : Number(values[k]))

  const available = Math.max(0, MONTHLY - FIXED)
  const total = ROWS.reduce((sum, [k]) => sum + num(k), 0) + IRREGULAR
  const left = available - total   // 남으면 여윳돈 (아직 어느 예산에도 배정 안 된 돈)
  const bad = !Number.isFinite(total) || ROWS.some(([k]) => num(k) < 0)
  const share = (v) => (Number.isFinite(v) && v >= 0 && available > 0 ? `${Math.round((v / available) * 100)}%` : '–')

  const onSubmit = (e) => {
    e.preventDefault()
    if (bad) { setError('금액을 확인해 주세요.'); return }
    if (left < 0) { setError('배분 가능한 금액을 넘었어요. 입력 금액을 줄여 주세요.'); return }
    if (CUSHION_REQUIRED && num('cushionMonthly') === 0) { setError('월 쿠션 적립금을 입력해 주세요.'); return }
    // 고친 금액을 추천 예산 확인으로 넘긴다 (저장은 API 연결 후)
    navigate(PATHS.recommend, { state: { dir, ...Object.fromEntries(ROWS.map(([k]) => [k, num(k)])), spare: left } })
  }

  return (
    <Layout title="지킬 돈 설정" showBack foot={<button className="mc-primary" form="mc-form" type="submit">추천 예산 보기</button>}>
      <StepBar step={6} />
      <div className="mc-eyebrow">지킬 돈 설정</div>
      <h1>먼저 지킬 돈을 정해요</h1>
      <p className="mc-muted">추천 금액을 확인하고, 바꾸고 싶은 칸만 고치세요.</p>
      <form className="mc-form" id="mc-form" onSubmit={onSubmit}>
        <div className="mc-card" style={{ marginBottom: 0 }}>
          <div className="mc-row">
            <h2>추천안 · {DIRS[dir]?.[0] ?? '기본'}</h2>
            <button type="button" className="mc-link" onClick={() => { setValues(reset()); setError('') }}>추천값으로</button>
          </div>
          <div className="mc-protected-values">
            {ROWS.map(([k, label]) => {
              const edited = num(k) !== RECOMMEND[k]  // 추천값과 다르면 "추천 ○원" 을 보여 준다
              return (
                <div className="mc-protected-item" key={k}>
                  <label className="mc-protect-line" htmlFor={`mc-p-${k}`}>
                    <span>{label}<b className="mc-protect-pct" aria-label={`배분 가능한 금액의 ${share(num(k))}`}>{share(num(k))}</b><small className="mc-cat-rec" hidden={!edited} id={`mc-p-rec-${k}`}>추천 {won(RECOMMEND[k])}</small></span>
                    <span className="mc-cat-input-wrap">
                      <MoneyInput aria-label={label} className={edited ? 'mc-cat-input mc-protect-input is-edited' : 'mc-cat-input mc-protect-input'}
                        id={`mc-p-${k}`} name={k}
                        value={values[k]} onChange={(digits) => { setValues({ ...values, [k]: digits }); setError('') }} />
                      <span>원</span>
                    </span>
                  </label>
                  <p aria-live="polite" className="mc-projection" id={`mc-hint-${k}`}>{hint(k, num(k))}</p>
                </div>
              )
            })}
          </div>
        </div>

        <div aria-live="polite" className="mc-card" id="mc-protectsum" style={{ marginTop: 14 }}>
          {bad ? <p className="mc-error">금액을 확인해 주세요.</p> : <>
            <strong>{won(total)}</strong> <span className="mc-muted">/ 배분 가능한 금액 {won(available)}</span>
            <p className="mc-muted" style={{ margin: '2px 0 0', fontSize: 12 }}>비정기지출 준비금 {won(IRREGULAR)}({share(IRREGULAR)}) 포함</p>
            <p className={left < 0 ? 'mc-error' : 'mc-muted'} style={{ margin: '4px 0 0' }}>
              {left === 0 ? '배분 가능한 금액에 딱 맞게 나눴어요.'
                : left < 0 ? `배분 가능한 금액보다 ${won(-left)} 많아요. 입력 금액을 줄여 주세요.`
                : `남는 ${won(left)}(${share(left)})은 여윳돈으로 둬요.`}
            </p>
          </>}
        </div>

        <div className="mc-card mc-cjudge">
          <div className="mc-row"><h2>머니 쿠션</h2><span className="mc-badge">{CUSHION_REQUIRED ? '필수' : '선택'}</span></div>
          <div className="mc-row" style={{ margin: '10px 0 2px' }}><span className="mc-muted">목표액</span><strong>{won(CUSHION_GOAL)}</strong></div>
          <p className="mc-note" style={{ padding: 0 }}>갑작스러운 지출에 대비해 월급의 3배만큼 모아 두는 돈이에요.</p>
        </div>
        <p className="mc-note">월급 {won(MONTHLY)}에서 고정지출 {won(FIXED)}을 뺀 금액으로 계산했어요.</p>
        {error && <p className="mc-error" role="alert">{error}</p>}
      </form>
    </Layout>
  )
}

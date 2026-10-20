// 지킬 돈 설정
// 금액을 고치면 아래 안내(1년 얼마, 여유 금액)가 바로 다시 계산된다
// 최소 생활비는 2026-10-15 기획에서 뺐다 (생활비는 전체 생활비 안에서 카테고리별로 나눈다)
// 추천값·월급·고정지출은 시안 예시값 — 추천 API 가 생기면 바꾼다
import { useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import StepBar from '../components/StepBar.jsx'
import { won } from '../utils/money.js'
import { PATHS } from '../routes.js'

const MONTHLY = 2_800_000   // 월급
const FIXED = 770_000       // 고정지출 합계
const IRREGULAR = 78_000    // 비정기지출 준비금
const RECOMMEND = { savingFloor: 410_000, cushionFloor: 200_000 }
const ROWS = [['savingFloor', '저축 목표액'], ['cushionFloor', '쿠션 보호 하한']]

// 칸 아래 한 줄 안내
function hint(key, value) {
  if (!Number.isFinite(value) || value < 0) return '0 이상의 금액을 입력해 주세요.'
  if (key === 'savingFloor') return <>1년 모으면 <b>{won(value * 12)}</b>이 모여요. <span>(매달 같은 금액 · 이자 제외)</span></>
  return <>갑작스러운 지출에 대비해 <b>{won(value)}</b>은 남겨 두는 거예요.</>
}

export default function ProtectPage() {
  const navigate = useNavigate()
  // 칸에 적힌 글자 그대로 기억 (빈 칸도 허용하려고 문자열로)
  const [values, setValues] = useState(() => Object.fromEntries(ROWS.map(([k]) => [k, String(RECOMMEND[k])])))
  const num = (k) => (values[k] === '' ? NaN : Number(values[k]))

  const available = Math.max(0, MONTHLY - FIXED)
  const total = num('savingFloor') + num('cushionFloor') + IRREGULAR
  const left = available - total
  const bad = !Number.isFinite(total) || ROWS.some(([k]) => num(k) < 0)

  const onSubmit = (e) => {
    e.preventDefault()
    navigate(PATHS.recommend)  // 저장은 API 연결 후
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
            <h2>추천안 · 기본</h2>
            <button type="button" className="mc-link" onClick={() => setValues(Object.fromEntries(ROWS.map(([k]) => [k, String(RECOMMEND[k])])))}>추천값으로</button>
          </div>
          <div className="mc-protected-values">
            {ROWS.map(([k, label]) => {
              const edited = num(k) !== RECOMMEND[k]  // 추천값과 다르면 "추천 ○원" 을 보여 준다
              return (
                <div className="mc-protected-item" key={k}>
                  <label className="mc-protect-line" htmlFor={`mc-p-${k}`}>
                    <span>{label}<small className="mc-cat-rec" hidden={!edited} id={`mc-p-rec-${k}`}>추천 {won(RECOMMEND[k])}</small></span>
                    <span className="mc-cat-input-wrap">
                      <input aria-label={label} className={edited ? 'mc-cat-input mc-protect-input is-edited' : 'mc-cat-input mc-protect-input'}
                        id={`mc-p-${k}`} inputMode="numeric" min="0" name={k} step="10000" type="number"
                        value={values[k]} onChange={(e) => setValues({ ...values, [k]: e.target.value })} />
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
            <p className="mc-muted" style={{ margin: '2px 0 0', fontSize: 12 }}>비정기지출 준비금 {won(IRREGULAR)} 포함</p>
            <p className={left < 0 ? 'mc-error' : 'mc-muted'} style={{ margin: '4px 0 0' }}>
              {left === 0 ? '배분 가능한 금액에 딱 맞게 나눴어요.'
                : left < 0 ? `배분 가능한 금액보다 ${won(-left)} 많아요. 입력 금액을 줄여 주세요.`
                : `지킬 돈을 남기고 ${won(left)}의 여유가 있어요.`}
            </p>
          </>}
        </div>

        <div className="mc-card mc-cjudge">
          <div className="mc-row"><h2>머니 쿠션</h2><span className="mc-badge">필수</span></div>
          <p className="mc-muted" style={{ margin: '4px 0 8px' }}>연결 계좌 잔액 2,800,000원 &lt; 월급×3 8,400,000원</p>
          <p className="mc-note" style={{ padding: 0 }}>잔액이 월급×3보다 적어서 머니 쿠션을 꼭 배정해요. 목표는 월급×3이에요.</p>
        </div>
        <p className="mc-note">월급 {won(MONTHLY)}에서 고정지출 {won(FIXED)}을 뺀 금액으로 계산했어요.</p>
        <div className="mc-error" role="alert"></div>
      </form>
    </Layout>
  )
}

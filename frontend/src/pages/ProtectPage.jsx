// 지킬 돈 설정 — GET · PUT /api/budgets/protect/ (docs/api/budgets.md '지킬 돈')
// 생활비 · 저축 목표액 · 월 쿠션 적립금을 정한다. 추천 금액 · 배분 기준 · 부족 판정은 서버가 계산해서 준다
// 생활비 = 한 달 생활비 전체 (별도 '최소 생활비'는 2026-10-15 기획에서 뺐다. 다음 화면에서 카테고리별로 나눈다)
// 쿠션은 '보호 하한' 대신 매달 적립할 금액으로 정한다 (2026-10-21)
// 항목 옆 % = 배분 가능한 금액(월급 - 고정지출) 중 비율. 금액을 고치면 같이 바뀐다
// 남는 돈은 여윳돈. 저축이에요 합계 > 추천 저축이면 서버가 저축을 먼저 떼고 다시 나눈 추천을 준다 (recommended.rebalanced)
// [추천 예산 보기] = 저장(PUT) 후 추천 예산 확인으로 가서 새 추천을 바로 만든다
import { useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import MoneyInput from '../components/MoneyInput.jsx'
import StepBar from '../components/StepBar.jsx'
import ApiStatus from '../components/ApiStatus.jsx'
import { won } from '../utils/money.js'
import { apiSend, useApi } from '../utils/api.js'
import { HOUSING } from '../utils/direction.js'
import { PATHS } from '../routes.js'

const ROWS = [['livingBudget', '생활비'], ['savingFloor', '저축 목표액'], ['cushionMonthly', '월 쿠션 적립금']]
const RATE_LABELS = [['living', '생활비'], ['saving', '저축'], ['spare', '여윳돈'], ['cushion', '쿠션']]
// 빠진 값 → 어디서 채우는지
const MISSING = {
  housingType: ['자취 여부', PATHS.profile, '내 정보 입력하기'],
  takeHomeIncome: ['월 실수령액', PATHS.salary, '급여 입력하기'],
  payday: ['급여일', PATHS.salary, '급여 입력하기'],
}

// 칸 아래 한 줄 안내
function hint(key, value, data) {
  if (!Number.isFinite(value) || value < 0) return '0 이상의 금액을 입력해 주세요.'
  if (key === 'livingBudget') return <>하루 약 <b>{won(Math.floor(value / 30 / 100) * 100)}</b>을 쓸 수 있어요. <span>(30일 기준)</span></>
  if (key === 'savingFloor' && value < data.savingsMin) return `저축 확인에서 고른 ${won(data.savingsMin)}보다 적게 정할 수 없어요.`
  if (key === 'savingFloor') return <>1년 모으면 <b>{won(value * 12)}</b>이 모여요. <span>(매달 같은 금액 · 이자 제외)</span></>
  if (value === 0) return data.cushion.required ? '잔액이 월급의 3배보다 적어서 쿠션을 꼭 적립해야 해요.' : '쿠션을 적립하지 않아요.'
  const need = data.cushion.target - (data.cushion.balanceTotal ?? 0)  // 지금 잔액에서 더 모아야 할 돈
  if (need <= 0) return <>지금 잔액으로 쿠션 목표 {won(data.cushion.target)}를 이미 채웠어요.</>
  return <>약 <b>{Math.ceil(need / value)}개월</b>이면 쿠션 목표 {won(data.cushion.target)}에 닿아요. <span>(지금 잔액 기준)</span></>
}

export default function ProtectPage() {
  const navigate = useNavigate()
  const { data, error: loadError } = useApi('/api/budgets/protect/')
  const [edits, setValues] = useState(null)  // 고친 칸 글자 그대로 (빈 칸도 허용하려고 문자열로). 안 고쳤으면 null
  const [error, setError] = useState('')
  const [fieldErrors, setFieldErrors] = useState({})
  const [busy, setBusy] = useState(false)


  if (!data) return <Layout title="지킬 돈 설정" showBack><ApiStatus error={loadError} /></Layout>

  const { base, blocked, recommended: rec, cushion } = data
  const start = data.saved ?? rec  // 저장한 값이 있으면 그 값, 없으면 추천값
  const values = edits ?? (start ? Object.fromEntries(ROWS.map(([k]) => [k, String(start[k])])) : null)
  const housingName = HOUSING[data.housingType]?.name
  const rateLine = data.rates ? RATE_LABELS.map(([k, n]) => `${n} ${data.rates[k]}%`).join(' · ') : ''
  const num = (k) => (!values || values[k] === '' ? NaN : Number(values[k]))
  const available = base?.available ?? 0
  const total = ROWS.reduce((sum, [k]) => sum + num(k), 0)
  const left = available - total   // 남으면 여윳돈 (아직 어느 예산에도 배정 안 된 돈)
  const bad = !Number.isFinite(total) || ROWS.some(([k]) => num(k) < 0)
  const share = (v) => (Number.isFinite(v) && v >= 0 && available > 0 ? `${Math.round((v / available) * 100)}%` : '–')
  const ready = data.missing.length === 0 && !blocked && rec && values

  const onSubmit = async (e) => {
    e.preventDefault()
    if (!ready || busy) return
    if (bad) { setError('금액을 확인해 주세요.'); return }
    setBusy(true)
    setError('')
    setFieldErrors({})
    try {
      const saved = await apiSend('/api/budgets/protect/', 'PUT', Object.fromEntries(ROWS.map(([k]) => [k, num(k)])))
      // 저장 뒤 새 추천을 바로 만든다. 생활비가 바뀌어 풀린 자물쇠(P-13)는 다음 화면에서 알려 준다
      navigate(PATHS.recommend, { state: { autoRecommend: true, locksReleased: saved.locksReleased } })
    } catch (err) {
      setError(err.fields?.nonFieldErrors?.[0] ?? err.message)
      setFieldErrors(err.fields ?? {})
    } finally {
      setBusy(false)
    }
  }

  const foot = <button className="mc-primary" form="mc-form" type="submit" disabled={!ready || busy}>{busy ? '저장하는 중…' : '추천 예산 보기'}</button>

  return (
    <Layout title="지킬 돈 설정" showBack foot={foot}>
      <StepBar step={5} />
      <div className="mc-eyebrow">지킬 돈 설정</div>
      <h1>먼저 지킬 돈을 정해요</h1>
      {ready && <p className="mc-muted">{housingName} 비율로 나눈 추천 금액이에요. 바꾸고 싶은 칸만 고치세요.</p>}

      {data.missing.length > 0 && (
        <div className="mc-card" role="alert">
          <h2>먼저 입력해야 할 정보가 있어요</h2>
          <p className="mc-muted" style={{ margin: '6px 0 0' }}>{data.missing.map((m) => MISSING[m]?.[0] ?? m).join(' · ')}이 있어야 지킬 돈을 나눌 수 있어요.</p>
          <button type="button" className="mc-secondary" onClick={() => navigate(MISSING[data.missing[0]]?.[1] ?? PATHS.salary)}>
            {MISSING[data.missing[0]]?.[2] ?? '입력하러 가기'}
          </button>
        </div>
      )}

      {blocked?.reason === 'card_bill_pending' && (
        <div className="mc-card" role="alert">
          <h2>카드 청구서를 확인하고 있어요</h2>
          <p className="mc-muted" style={{ margin: '6px 0 0' }}>첫 카드 청구서에서 미리 뗄 돈을 알아야 고정비를 정할 수 있어요. 카드 내역을 다시 불러온 뒤 확인해 주세요.</p>
          <button type="button" className="mc-secondary" onClick={() => navigate(PATHS.connections)}>연결 관리로 가기</button>
        </div>
      )}

      {blocked && blocked.reason !== 'card_bill_pending' && (
        <div className="mc-card" role="alert">
          <h2>{blocked.reason === 'fixed_over_income' ? '고정지출만으로 월급을 다 써요' : '저축과 고정지출만으로 월급을 다 써요'}</h2>
          <p className="mc-muted" style={{ margin: '6px 0 0' }}>
            고정지출 {won(base.fixedTotal)}{blocked.reason === 'savings_over_available' && <> + 꼭 할 저축 {won(data.savingsMin)}</>}이
            월급 {won(base.takeHomeIncome)}보다 {blocked.shortfall > 0 ? <><b className="mc-error">{won(blocked.shortfall)}</b> 많아요.</> : '남는 돈이 없어요.'} 생활비를 나눌 수 없어서 하나를 줄여야 해요.
          </p>
          <div className="mc-two" style={{ marginTop: 12, gap: 8 }}>
            <button type="button" className="mc-secondary" onClick={() => navigate(PATHS.savings)}>정기 저축 고치기</button>
            <button type="button" className="mc-secondary" onClick={() => navigate(PATHS.fixed)}>고정지출 확인</button>
          </div>
        </div>
      )}

      {rec?.rebalanced && !blocked && (
        <div className="mc-card" aria-live="polite">
          <div className="mc-row"><h2>저축을 먼저 뗐어요</h2><span className="mc-badge">다시 나눔</span></div>
          <p className="mc-muted" style={{ margin: '6px 0 0' }}>
            꼭 할 저축 <b>{won(data.savingsMin)}</b>이 추천 저축 {won(rec.ratioSavingFloor)}보다 커요.
            저축을 먼저 떼고 남은 <b>{won(rec.restAmount)}</b>을 생활비 : 여윳돈 : 쿠션 = {data.rates.living} : {data.rates.spare} : {data.rates.cushion}로 다시 나눴어요.
          </p>
        </div>
      )}

      <form className="mc-form" id="mc-form" onSubmit={onSubmit}>
        {ready && <>
        <div className="mc-card" style={{ marginBottom: 0 }}>
          <div className="mc-row">
            <h2>추천안 · {housingName} 기준{rec.rebalanced ? ' · 저축 먼저' : ''}</h2>
            <button type="button" className="mc-link" onClick={() => { setValues(Object.fromEntries(ROWS.map(([k]) => [k, String(rec[k])]))); setError(''); setFieldErrors({}) }}>추천값으로</button>
          </div>
          <div className="mc-protected-values">
            {ROWS.map(([k, label]) => {
              const edited = num(k) !== rec[k]  // 추천값과 다르면 "추천 ○원" 을 보여 준다
              return (
                <div className="mc-protected-item" key={k}>
                  <label className="mc-protect-line" htmlFor={`mc-p-${k}`}>
                    <span>{label}<b className="mc-protect-pct" aria-label={`배분 가능한 금액의 ${share(num(k))}`}>{share(num(k))}</b><small className="mc-cat-rec" hidden={!edited} id={`mc-p-rec-${k}`}>추천 {won(rec[k])}</small></span>
                    <span className="mc-cat-input-wrap">
                      <MoneyInput aria-label={label} className={edited ? 'mc-cat-input mc-protect-input is-edited' : 'mc-cat-input mc-protect-input'}
                        id={`mc-p-${k}`} name={k}
                        value={values[k]} onChange={(digits) => { setValues({ ...values, [k]: digits }); setError(''); setFieldErrors({}) }} />
                      <span>원</span>
                    </span>
                  </label>
                  <p aria-live="polite" className={fieldErrors[k] ? 'mc-projection mc-error' : 'mc-projection'} id={`mc-hint-${k}`}>{fieldErrors[k]?.[0] ?? hint(k, num(k), data)}</p>
                </div>
              )
            })}
          </div>
        </div>

        <div aria-live="polite" className="mc-card" id="mc-protectsum" style={{ marginTop: 14 }}>
          {bad ? <p className="mc-error">금액을 확인해 주세요.</p> : <>
            <strong>{won(total)}</strong> <span className="mc-muted">/ 배분 가능한 금액 {won(available)}</span>
            <p className={left < 0 ? 'mc-error' : 'mc-muted'} style={{ margin: '4px 0 0' }}>
              {left === 0 ? '배분 가능한 금액에 딱 맞게 나눴어요.'
                : left < 0 ? `배분 가능한 금액보다 ${won(-left)} 많아요. 입력 금액을 줄여 주세요.`
                : `남는 ${won(left)}(${share(left)})은 여윳돈으로 둬요.`}
            </p>
          </>}
        </div>
        </>}

        {cushion.target !== null && (
          <div className="mc-card mc-cjudge">
            <div className="mc-row"><h2>머니 쿠션</h2><span className="mc-badge">{cushion.required ? '필수' : '선택'}</span></div>
            <div className="mc-row" style={{ margin: '10px 0 2px' }}><span className="mc-muted">목표액</span><strong>{won(cushion.target)}</strong></div>
            <p className="mc-note" style={{ padding: 0 }}>갑작스러운 지출에 대비해 월급의 3배만큼 모아 두는 돈이에요.</p>
          </div>
        )}
        {ready && (
          <p className="mc-note">
            월급 {won(base.takeHomeIncome)}에서 고정지출 {won(base.fixedTotal)}을 뺀 금액을 {housingName} 비율({rateLine})로 나눴어요.
            {rec?.rebalanced && ' 꼭 할 저축이 더 크면 저축을 먼저 떼고 나머지를 같은 비율로 나눠요.'} 자취 여부는 내 정보에서 바꿀 수 있어요.
          </p>
        )}
        {error && <p className="mc-error" role="alert">{error}</p>}
      </form>
    </Layout>
  )
}

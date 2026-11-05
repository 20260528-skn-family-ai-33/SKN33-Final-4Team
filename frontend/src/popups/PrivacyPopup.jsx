// 실제 동의 상태 조회 · 범위별 철회와 삭제 · 회원 탈퇴
import { useRef, useState } from 'react'
import { Navigate, useNavigate } from 'react-router'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { apiSend, useApi } from '../utils/api.js'
import { PATHS } from '../routes.js'

const LABELS = { bank: '계좌 조회', card: '카드 조회', loan: '대출 조회' }
const STATES = { granted: '동의함', revoked: '철회함', not_granted: '동의 안 함' }
const dateText = (value) => value ? new Date(value).toLocaleDateString('ko-KR', { timeZone: 'Asia/Seoul' }) : ''

export default function PrivacyPopup({ onChanged }) {
  const query = useApi('/api/finance/consents/')
  const navigate = useNavigate()
  const { closePopup } = usePopup()
  const [step, setStep] = useState(null)
  const [password, setPassword] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const working = useRef(false)
  const choose = (next) => { setStep(next); setError(''); setMessage(''); setPassword(''); setConfirmed(false) }
  const submit = async (event) => {
    event.preventDefault()
    if (working.current || !step || (step === 'withdraw' && (!password || !confirmed))) return
    working.current = true
    setBusy(true)
    setError('')
    try {
      if (step === 'withdraw') {
        const result = await apiSend('/api/auth/me/', 'DELETE', { password, confirm: true })
        if (result?.ok !== true) throw new Error('탈퇴 결과를 확인하지 못했어요. 다시 확인해 주세요.')
        try { localStorage.removeItem('moneycushion.savedEmail') } catch { /* 브라우저 저장소가 차단돼도 탈퇴는 완료됨 */ }
        closePopup()
        navigate(PATHS.login, { replace: true })
      } else {
        await apiSend(`/api/finance/consents/${step}/`, 'DELETE', { confirm: true })
        setMessage(`${LABELS[step]} 동의를 철회하고 해당 데이터와 관련 예산·분석 기록을 삭제했어요.`)
        setStep(null)
        query.reload()
        onChanged?.()
      }
    } catch (err) {
      setError(Object.values(err.fields ?? {}).flat()[0] || (err.status ? err.message : '처리 결과를 확인하지 못했어요. 연결 상태를 확인한 뒤 다시 시도해 주세요.'))
    } finally {
      setPassword('')
      working.current = false
      setBusy(false)
    }
  }

  if (query.error?.code === 'not_authenticated') return <Navigate to={PATHS.login} replace />
  return <Popup label="동의·데이터 관리">
    <h2>동의·데이터 관리</h2>
    {step ? <form className="mc-form" onSubmit={submit}>
      <h3>{step === 'withdraw' ? '회원 탈퇴 · 전체 데이터 삭제' : `${LABELS[step]} 동의 철회`}</h3>
      <p>{step === 'withdraw'
        ? '머니쿠션 회원정보, 금융 내역, 예산, 동의 기록과 대화 기록을 모두 삭제하고 로그아웃합니다. 삭제한 데이터는 되돌릴 수 없어요.'
        : '해당 범위에서 조회한 금융 데이터와 이를 바탕으로 자동 탐지한 고정지출·저축 항목을 삭제합니다. 삭제한 데이터는 되돌릴 수 없어요.'}</p>
      {step !== 'withdraw' && <>
        <p className="mc-note">데이터가 섞여 있는 예산·쿠션 기록과 대화·분석 기록도 초기화되어 예산을 다시 설정해야 해요. 직접 등록한 항목은 남기고 삭제된 데이터와의 연결을 해제해요.</p>
        {step === 'bank' && <p className="mc-note">계좌 조회 동의를 철회하면 새 예산 설정·추천·적용이 중단돼요. 다시 이용하려면 동의 후 계좌를 연결해 주세요.</p>}
        {['bank', 'loan'].includes(step) && <p className="mc-note">계좌 또는 대출 조회 동의가 남아 있으면, 해당 조회에 필요한 은행 인증 연결은 유지해요.</p>}
      </>}
      {step === 'withdraw' && <>
        <label className="mc-field">머니쿠션 로그인 비밀번호
          <input name="withdrawPassword" type="password" autoComplete="current-password" value={password} disabled={busy} onChange={(event) => setPassword(event.target.value)} />
        </label>
        <label><input type="checkbox" checked={confirmed} disabled={busy} onChange={(event) => setConfirmed(event.target.checked)} /> 전체 데이터 삭제를 확인했어요.</label>
      </>}
      <button type="submit" className="mc-primary" disabled={busy || (step === 'withdraw' && (!password || !confirmed))}>
        {busy ? '처리 중…' : step === 'withdraw' ? '탈퇴하고 전체 데이터 삭제' : '철회하고 데이터 삭제'}
      </button>
      <button type="button" className="mc-link" disabled={busy} onClick={() => choose(null)}>취소</button>
    </form> : <>
      {query.error ? <div role="alert">
        <p className="mc-error">동의 상태를 불러오지 못했어요.</p>
        <button type="button" className="mc-link" onClick={query.reload}>동의 상태 다시 불러오기</button>
      </div> : !query.data ? <p role="status">동의 상태를 불러오는 중이에요.</p> : query.data.items.map((item) => <div className="mc-priv-row" key={item.productScope}>
        <div><b>{LABELS[item.productScope]} · {item.required ? '필수' : '선택'}</b>
          <small>{item.grantedAt ? `${dateText(item.grantedAt)} 동의 · ${item.documentVersion}` : '동의 기록 없음'}{item.revokedAt ? ` · ${dateText(item.revokedAt)} 철회` : ''}</small>
        </div>
        <span className="mc-badge">{STATES[item.status]}</span>
        {item.status === 'granted' && <button type="button" className="mc-link" onClick={() => choose(item.productScope)}>{LABELS[item.productScope]} 철회</button>}
      </div>)}
      {query.data && <button type="button" className="mc-link" onClick={() => { closePopup(); navigate(PATHS.connect) }}>조회 동의·연결 관리하기</button>}
      <button type="button" className="mc-link mc-unlink" onClick={() => choose('withdraw')}>회원 탈퇴 · 데이터 삭제</button>
      {message && <p role="status">{message}</p>}
    </>}
    {error && <p className="mc-error" role="alert">{error}</p>}
  </Popup>
}

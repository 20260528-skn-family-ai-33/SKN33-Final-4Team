import { useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { apiSend } from '../utils/api.js'
import { PATHS } from '../routes.js'

export default function WithdrawPopup() {
  const navigate = useNavigate()
  const { closePopup } = usePopup()
  const [password, setPassword] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const working = useRef(false)

  const submit = async (event) => {
    event.preventDefault()
    if (working.current || !password || !confirmed) return
    working.current = true
    setBusy(true)
    setError('')
    try {
      const result = await apiSend('/api/auth/me/', 'DELETE', { password, confirm: true })
      if (result?.ok !== true) throw new Error('탈퇴 결과를 확인하지 못했어요. 다시 확인해 주세요.')
      try { localStorage.removeItem('moneycushion.savedEmail') } catch { /* 저장소가 차단돼도 탈퇴는 완료됨 */ }
      closePopup()
      navigate(PATHS.login, { replace: true })
    } catch (err) {
      setError(Object.values(err.fields ?? {}).flat()[0] || (err.status ? err.message : '처리 결과를 확인하지 못했어요. 연결 상태를 확인한 뒤 다시 시도해 주세요.'))
    } finally {
      setPassword('')
      working.current = false
      setBusy(false)
    }
  }

  return <Popup label="회원 탈퇴">
    <h2>회원 탈퇴 · 전체 데이터 삭제</h2>
    <p>머니쿠션 회원정보, 금융 내역, 예산, 동의 기록과 대화 기록을 모두 삭제하고 로그아웃합니다. 삭제한 데이터는 되돌릴 수 없어요.</p>
    <form className="mc-form" onSubmit={submit}>
      <label className="mc-field">머니쿠션 로그인 비밀번호
        <input name="withdrawPassword" type="password" autoComplete="current-password" value={password} disabled={busy} onChange={(event) => setPassword(event.target.value)} />
      </label>
      <label><input type="checkbox" checked={confirmed} disabled={busy} onChange={(event) => setConfirmed(event.target.checked)} /> 전체 데이터 삭제를 확인했어요.</label>
      {error && <p className="mc-error" role="alert">{error}</p>}
      <button type="submit" className="mc-primary" disabled={busy || !password || !confirmed}>{busy ? '처리 중…' : '탈퇴하고 전체 데이터 삭제'}</button>
      <button type="button" className="mc-link" disabled={busy} onClick={closePopup}>취소</button>
    </form>
  </Popup>
}

import { useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { apiSend } from '../utils/api.js'
import { PATHS } from '../routes.js'
import { clearConnectDraft } from '../utils/connectDraft.js'

export default function WithdrawPopup() {
  const navigate = useNavigate()
  const { closePopup } = usePopup()
  const [password, setPassword] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [pending, setPending] = useState(null)
  const working = useRef(false)

  const submit = async (event) => {
    event.preventDefault()
    if (working.current || !password || !confirmed) return
    working.current = true
    setBusy(true)
    setError('')
    try {
      const result = await apiSend('/api/auth/me/', 'DELETE', { password, confirm: true })
      if (result?.ok !== true && !['processing', 'retry_required'].includes(result?.status)) throw new Error('탈퇴 결과를 확인하지 못했어요. 다시 확인해 주세요.')
      try { localStorage.removeItem('moneycushion.savedEmail') } catch { /* 저장소가 차단돼도 탈퇴는 완료됨 */ }
      clearConnectDraft()  // 탈퇴(접수 포함) = 로그아웃. 계좌 연결에서 고르던 기관을 지운다 (11/12)
      // 접수됐지만 삭제가 남음(R21): 로그아웃 · 로그인 차단 상태. 완료라고 하지 않고 안내만 보인다
      if (result?.ok !== true) { setPending(result.status); return }
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

  if (pending) return <Popup label="회원 탈퇴">
    <h2>회원 탈퇴 접수</h2>
    <p role="status">탈퇴를 접수하고 로그아웃했어요. 아직 탈퇴가 완료되지는 않았어요. {pending === 'retry_required' ? '금융기관 인증 연결 또는 데이터 삭제를 마무리하지 못했어요. 자동으로 다시 시도하지 않으며, 운영 담당자가 남은 삭제를 처리하면 완료돼요.' : '금융기관 인증 연결과 데이터를 삭제하는 중이에요. 이번 처리에서 끝나지 않으면 운영 담당자가 남은 삭제를 처리해요.'} 그때까지 이 계정으로는 로그인할 수 없어요.</p>
    <button type="button" className="mc-primary" onClick={() => { closePopup(); navigate(PATHS.login, { replace: true }) }}>로그인 화면으로</button>
  </Popup>
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

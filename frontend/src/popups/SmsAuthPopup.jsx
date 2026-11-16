// 팝업: 문자 인증 — 로그인 뒤 기관이 휴대폰 인증을 더 요구할 때 (CODEF 추가 인증, CF-03002)
// props: inst = 기관 이름, phoneHint = '010-****-1234' (서버가 아직 null 로 줌), onSubmit(code) / onCancel() = 인증 취소
//        notice = 다시 묻는 이유 (예: '인증번호가 맞지 않아요.')
// 닫기 × · 바깥 · Esc 로 닫아도 취소로 친다 (11/14: 다시 연결이 결과를 기다리다 멈추지 않게)
import { useEffect, useRef, useState } from 'react'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { josa } from '../utils/adjust.js'

export default function SmsAuthPopup({ inst, phoneHint, notice, onSubmit, onCancel }) {
  const { closePopup } = usePopup()
  const [code, setCode] = useState('')
  const [error, setError] = useState(notice ?? '')
  const answered = useRef(false)
  const alive = useRef(false)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      // 개발 모드는 한 번 껐다 바로 다시 켜서, 진짜로 닫혔을 때만 취소로 친다
      setTimeout(() => { if (!alive.current && !answered.current) onCancel?.() })
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps -- 열고 닫힐 때만

  const submit = (e) => {
    e.preventDefault()
    if (code.length !== 6) { setError('인증번호 6자리를 입력해 주세요.'); return }
    answered.current = true
    onSubmit(code)
    closePopup()
  }
  const cancel = () => { answered.current = true; onCancel?.(); closePopup() }

  return (
    <Popup label={`${inst} 문자 인증`}>
      <h2>문자로 받은 인증번호를 입력해 주세요</h2>
      <p className="mc-muted" style={{ margin: '4px 0 0' }}>
        {phoneHint ? <>{inst}{josa(inst, '이', '가')} <b>{phoneHint}</b>(으)로 인증번호를 보냈어요.</> : <>{inst}{josa(inst, '이', '가')} 등록된 휴대폰으로 인증번호를 보냈어요.</>}
      </p>
      <form className="mc-form" onSubmit={submit}>
        <label className="mc-field">인증번호 6자리
          <input autoComplete="one-time-code" inputMode="numeric" maxLength={6} placeholder="123456" value={code}
            onChange={(e) => { setCode(e.target.value.replace(/\D/g, '')); setError('') }} />
        </label>
        {error && <p className="mc-error" role="alert">{error}</p>}
        <button type="submit" className="mc-primary">확인</button>
        <button type="button" className="mc-secondary" onClick={cancel}>인증 취소</button>
      </form>
      <p className="mc-note">3분 안에 입력해야 해요. 시간이 지나면 다시 연결해 주세요.</p>
    </Popup>
  )
}

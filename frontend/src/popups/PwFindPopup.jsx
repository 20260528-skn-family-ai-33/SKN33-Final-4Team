// 팝업: 비밀번호 재설정 — 로그인 "비밀번호를 잊으셨나요?"
import Popup from './Popup.jsx'

export default function PwFindPopup() {
  return (
    <Popup>
      <h2>비밀번호 재설정</h2>
      <p className="mc-muted">가입한 이메일로 6자리 인증 코드를 보내요.</p>
      <label className="mc-field">이메일<input autoComplete="email" placeholder="you@example.com" type="email" /></label>
      {/* 메일 보내기는 회원 기능을 만든 뒤에 */}
      <button type="button" className="mc-primary">인증 코드 보내기</button>
    </Popup>
  )
}

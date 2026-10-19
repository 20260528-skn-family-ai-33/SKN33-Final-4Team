// 로그인 (장고: users/templates/users/login.html)
// 지금은 아무거나 넣어도 홈으로 간다. 진짜 로그인은 users.User 모델을 만든 뒤에 붙인다
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import { usePopup } from '../popups/context.js'
import PwFindPopup from '../popups/PwFindPopup.jsx'
import { PATHS } from '../routes.js'

export default function LoginPage() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()

  const onSubmit = (e) => {
    e.preventDefault()      // 페이지 새로고침 막기
    navigate(PATHS.home)    // 로그인 API 가 생기면 여기서 확인한다
  }

  return (
    <Layout title="로그인">
      <div className="mc-eyebrow">MONEY CUSHION</div>
      <h1>다시 만나서 반가워요</h1>
      <form className="mc-form" id="mc-form" onSubmit={onSubmit}>
        <label className="mc-field">이메일<input autoComplete="email" name="loginEmail" placeholder="you@example.com" type="email" /></label>
        <label className="mc-field">비밀번호<input autoComplete="current-password" name="loginPw" type="password" /></label>
        <div className="mc-check-row">
          <label className="mc-field mc-check"><input name="autoLogin" type="checkbox" />자동 로그인</label>
          <label className="mc-field mc-check"><input name="saveId" type="checkbox" />아이디 저장</label>
        </div>
        <div className="mc-error" role="alert"></div>
        <div className="mc-foot"><button className="mc-primary" type="submit">로그인</button></div>
      </form>
      <button type="button" className="mc-link" onClick={() => openPopup(<PwFindPopup />)}>비밀번호를 잊으셨나요?</button>
      <div className="mc-card" style={{ marginTop: 22, textAlign: 'center' }}>
        <p className="mc-muted">머니쿠션이 처음이신가요?</p>
        <button type="button" className="mc-secondary" onClick={() => navigate(PATHS.signup)}>회원가입</button>
      </div>
    </Layout>
  )
}

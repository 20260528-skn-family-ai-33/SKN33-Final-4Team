// 회원가입 (장고: users/templates/users/signup.html)
// 비밀번호 조건(영문·숫자·특수문자·8자)과 확인 칸 일치 여부를 입력하는 즉시 보여 준다
import { useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import { PATHS } from '../routes.js'

const PW_RULES = [
  ['영문', (p) => /[A-Za-z]/.test(p)],
  ['숫자', (p) => /[0-9]/.test(p)],
  ['특수문자', (p) => /[^A-Za-z0-9\s]/.test(p)],
  ['8자 이상', (p) => p.length >= 8],
]

export default function SignupPage() {
  const navigate = useNavigate()
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')

  // 확인 칸: 비어 있으면 아무 말 안 함 / 같으면 ok / 다르면 bad
  const match = !pw2 ? '' : pw === pw2 ? 'ok' : 'bad'
  const matchText = { '': '', ok: '비밀번호가 일치해요.', bad: '비밀번호가 일치하지 않아요.' }[match]

  const onSubmit = (e) => {
    e.preventDefault()
    navigate(PATHS.onboard)  // 가입 API 가 생기면 여기서 저장한다
  }

  return (
    <Layout
      title="회원가입"
      showBack
      foot={<button className="mc-primary" form="mc-form" type="submit">가입하고 시작하기</button>}
    >
      <div className="mc-eyebrow">회원가입</div>
      <h1>머니쿠션 계정을 만들어요</h1>
      <form className="mc-form" id="mc-form" onSubmit={onSubmit}>
        <label className="mc-field">이름(닉네임)<input name="suName" placeholder="홍길동" type="text" /></label>
        <label className="mc-field">이메일<input autoComplete="email" name="suEmail" placeholder="you@example.com" type="email" /></label>
        <label className="mc-field">비밀번호
          <input aria-describedby="mc-pwrules" autoComplete="new-password" name="suPw" placeholder="영문·숫자·특수문자 포함 8자 이상" type="password"
            value={pw} onChange={(e) => setPw(e.target.value)} />
        </label>
        <ul aria-label="비밀번호 조건" aria-live="polite" className="mc-pw-rules" id="mc-pwrules">
          {PW_RULES.map(([label, test]) => <li key={label} className={test(pw) ? 'ok' : ''}>{label}</li>)}
        </ul>
        <label className="mc-field">비밀번호 확인
          <input aria-describedby="mc-pwmatch" autoComplete="new-password" name="suPw2" placeholder="비밀번호를 한 번 더 입력" type="password"
            value={pw2} onChange={(e) => setPw2(e.target.value)} className={match === 'bad' ? 'mc-bad' : undefined} aria-invalid={match === 'bad'} />
        </label>
        <p aria-live="polite" className={`mc-pw-match ${match}`} id="mc-pwmatch">{matchText}</p>
        <div className="mc-card">
          <label className="mc-field mc-check"><input name="agreeTerms" type="checkbox" />[필수] 서비스 이용약관 동의</label>
          <label className="mc-field mc-check"><input name="agreePrivacy" type="checkbox" />[필수] 개인정보 수집·이용 동의</label>
          <p className="mc-note" style={{ paddingBottom: 0 }}>계좌 조회 동의는 계좌 연결 단계에서 따로 받아요.</p>
        </div>
        <div className="mc-error" role="alert"></div>
      </form>
    </Layout>
  )
}

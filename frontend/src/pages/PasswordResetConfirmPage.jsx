// 비밀번호 찾기 2단계: 메일 링크(/reset-password?uid=…&token=…)로 들어와 새 비밀번호를 정한다
// 규칙은 회원가입과 같다 (utils/password.js). 확인 칸은 화면에서만 비교하고 서버엔 password 한 번만 보낸다
import { useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import Layout from '../components/Layout.jsx'
import { PATHS } from '../routes.js'
import { apiSend } from '../utils/api.js'
import { PW_MAX, PW_RULES, pwOk } from '../utils/password.js'

export default function PasswordResetConfirmPage() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const uid = params.get('uid') ?? ''
  const token = params.get('token') ?? ''
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  const [pwError, setPwError] = useState('')
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const submittingRef = useRef(false)

  const match = !pw2 ? '' : pw === pw2 ? 'ok' : 'bad'
  const matchText = { '': '', ok: '비밀번호가 일치해요.', bad: '비밀번호가 일치하지 않아요.' }[match]

  const onSubmit = async (e) => {
    e.preventDefault()
    if (submittingRef.current) return
    setError('')
    setPwError('')

    if (!pwOk(pw)) return setPwError('비밀번호 조건을 확인해 주세요.')
    if (pw !== pw2) return setPwError('비밀번호가 일치하지 않아요.')

    submittingRef.current = true
    setIsSubmitting(true)
    try {
      await apiSend('/api/auth/password-reset/confirm/', 'POST', { uid, token, password: pw })
      setDone(true)
    } catch (err) {
      setPwError(err.fields?.password?.join(' ') ?? '')
      setError(err.status ? err.message : '서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.')
    } finally {
      submittingRef.current = false
      setIsSubmitting(false)
    }
  }

  if (done) {
    return (
      <Layout title="비밀번호 재설정">
        <h1>비밀번호를 바꿨어요</h1>
        <p className="mc-muted" role="status">새 비밀번호로 다시 로그인해 주세요. 다른 기기의 로그인은 끊겼어요.</p>
        <div className="mc-foot">
          <button type="button" className="mc-primary" onClick={() => navigate(PATHS.login, { replace: true })}>
            로그인하러 가기
          </button>
        </div>
      </Layout>
    )
  }

  // 링크 값이 빠졌으면 입력받을 필요 없이 다시 요청하게 한다
  if (!uid || !token) {
    return (
      <Layout title="비밀번호 재설정">
        <h1>링크를 확인할 수 없어요</h1>
        <p className="mc-muted">메일의 링크를 그대로 열었는지 확인하거나, 비밀번호 찾기를 다시 해 주세요.</p>
        <div className="mc-foot">
          <button type="button" className="mc-primary" onClick={() => navigate(PATHS.passwordReset, { replace: true })}>
            비밀번호 찾기 다시 하기
          </button>
        </div>
      </Layout>
    )
  }

  return (
    <Layout title="비밀번호 재설정">
      <h1>새 비밀번호를 정해 주세요</h1>
      <form className="mc-form" onSubmit={onSubmit} aria-busy={isSubmitting}>
        <label className="mc-field">
          새 비밀번호
          <input
            aria-describedby={pwError ? 'reset-pwrules reset-pw-error' : 'reset-pwrules'}
            aria-invalid={Boolean(pwError)}
            autoComplete="new-password"
            name="resetPw"
            type="password"
            required
            maxLength={PW_MAX}
            disabled={isSubmitting}
            value={pw}
            onChange={(e) => { setPw(e.target.value); setPwError('') }}
          />
        </label>

        <ul aria-label="비밀번호 조건" aria-live="polite" className="mc-pw-rules" id="reset-pwrules">
          {PW_RULES.map(([label, test]) => (
            <li key={label} className={test(pw) ? 'ok' : ''}>{label}</li>
          ))}
        </ul>

        <label className="mc-field">
          새 비밀번호 확인
          <input
            aria-describedby="reset-pwmatch"
            aria-invalid={match === 'bad'}
            autoComplete="new-password"
            name="resetPw2"
            type="password"
            required
            maxLength={PW_MAX}
            disabled={isSubmitting}
            value={pw2}
            className={match === 'bad' ? 'mc-bad' : undefined}
            onChange={(e) => { setPw2(e.target.value); setPwError('') }}
          />
        </label>

        <p aria-live="polite" className={`mc-pw-match ${match}`} id="reset-pwmatch">{matchText}</p>

        {pwError && <p id="reset-pw-error" className="mc-error" role="alert">{pwError}</p>}

        <div className="mc-error" role="alert">{error}</div>

        <div className="mc-foot">
          <button className="mc-primary" type="submit" disabled={isSubmitting}>
            {isSubmitting ? '바꾸는 중…' : '비밀번호 바꾸기'}
          </button>
        </div>
      </form>
    </Layout>
  )
}

// 비밀번호 찾기 1단계: 이메일을 넣으면 재설정 링크를 메일로 보낸다
// 서버는 가입 여부와 상관없이 똑같이 답하므로(가입 여부 노출 방지) 화면도 "보냈어요" 한 가지로만 안내한다
import { useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import { PATHS } from '../routes.js'
import { apiSend } from '../utils/api.js'

export default function PasswordResetPage() {
  const navigate = useNavigate()
  const [sentTo, setSentTo] = useState('')
  const [error, setError] = useState('')
  const [fieldErrors, setFieldErrors] = useState({})
  const [isSubmitting, setIsSubmitting] = useState(false)
  const submittingRef = useRef(false)

  const onSubmit = async (e) => {
    e.preventDefault()
    if (submittingRef.current) return

    const email = String(new FormData(e.currentTarget).get('resetEmail') ?? '').trim()
    submittingRef.current = true
    setIsSubmitting(true)
    setError('')
    setFieldErrors({})

    try {
      await apiSend('/api/auth/password-reset/', 'POST', { email })
      setSentTo(email)
    } catch (err) {
      setFieldErrors(err.fields ?? {})
      setError(err.status ? err.message : '서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.')
    } finally {
      submittingRef.current = false
      setIsSubmitting(false)
    }
  }

  if (sentTo) {
    return (
      <Layout title="비밀번호 찾기" showBack>
        <h1>메일을 확인해 주세요</h1>
        <p className="mc-muted" role="status">
          {sentTo} 로 가입한 계정이 있으면 비밀번호 재설정 링크를 보냈어요.<br />
          링크는 1시간 동안 쓸 수 있어요. 메일이 안 보이면 스팸함도 확인해 주세요.
        </p>
        <div className="mc-foot">
          <button type="button" className="mc-primary" onClick={() => navigate(PATHS.login, { replace: true })}>
            로그인으로 돌아가기
          </button>
        </div>
      </Layout>
    )
  }

  return (
    <Layout title="비밀번호 찾기" showBack>
      <h1>비밀번호를 잊으셨나요?</h1>
      <p className="mc-muted">가입한 이메일을 적으면 새 비밀번호를 정할 수 있는 링크를 보내 드려요.</p>
      <form className="mc-form" onSubmit={onSubmit} aria-busy={isSubmitting}>
        <label className="mc-field">
          이메일
          <input
            autoComplete="email"
            name="resetEmail"
            placeholder="you@example.com"
            type="email"
            required
            maxLength={255}
            disabled={isSubmitting}
            aria-invalid={Boolean(fieldErrors.email)}
            aria-describedby={fieldErrors.email ? 'reset-email-error' : undefined}
          />
        </label>

        {fieldErrors.email && (
          <p id="reset-email-error" className="mc-field-error" role="alert">
            {fieldErrors.email.join(' ')}
          </p>
        )}

        <div className="mc-error" role="alert">{error}</div>

        <div className="mc-foot">
          <button className="mc-primary" type="submit" disabled={isSubmitting}>
            {isSubmitting ? '보내는 중…' : '재설정 링크 받기'}
          </button>
        </div>
      </form>
    </Layout>
  )
}

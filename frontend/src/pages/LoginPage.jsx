// 로그인
// 지금은 아무거나 넣어도 홈으로 간다. 진짜 로그인은 users.User 모델을 만든 뒤에 붙인다
// 로그인할 때는 비밀번호 길이 · 조합을 검사하지 않는다 (규칙이 바뀌어도 기존 회원이 로그인, 결정기록 X ⑨)
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import { usePopup } from '../popups/context.js'
import PwFindPopup from '../popups/PwFindPopup.jsx'
import { PATHS } from '../routes.js'

import { useRef, useState } from 'react'
import { apiSend } from '../utils/api.js'

const SAVED_EMAIL_KEY = 'moneycushion.savedEmail'

function readSavedEmail() {
  try {
    return window.localStorage.getItem(SAVED_EMAIL_KEY) ?? ''
  } catch {
    return ''
  }
}

export default function LoginPage() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()
  const [savedEmail] = useState(readSavedEmail)
  const [error, setError] = useState('')
  const [fieldErrors, setFieldErrors] = useState({})
  const [isSubmitting, setIsSubmitting] = useState(false)
  const submittingRef = useRef(false)

  const onSubmit = async (e) => {
    e.preventDefault()

    if (submittingRef.current) return

    const form = new FormData(e.currentTarget)

    submittingRef.current = true
    setIsSubmitting(true)
    setError('')
    setFieldErrors({})

    try {
      const user = await apiSend('/api/auth/login/', 'POST', {
        email: String(form.get('loginEmail') ?? '').trim(),
        password: String(form.get('loginPw') ?? ''),
      })

      if (typeof user?.onboardingCompleted !== 'boolean') {
        setError('로그인 응답을 확인할 수 없어요. 다시 시도해 주세요.')
        return
      }

      try {
        if (form.get('saveId') === 'on') {
          window.localStorage.setItem(SAVED_EMAIL_KEY, user.email)
        } else {
          window.localStorage.removeItem(SAVED_EMAIL_KEY)
        }
      } catch {
        window.alert('로그인은 성공했지만 아이디 저장 설정을 반영하지 못했어요.')
      }

      const destination = user.onboardingCompleted
        ? PATHS.home
        : PATHS.onboard

      navigate(destination, { replace: true })
    } catch (err) {
      setFieldErrors(err.fields ?? {})
      setError(
        err.status
          ? err.message
          : '서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.'
      )
    } finally {
      submittingRef.current = false
      setIsSubmitting(false)
    }
  }

  return (
    <Layout title="로그인">
      <div className="mc-eyebrow">MONEY CUSHION</div>
      <h1>다시 만나서 반가워요</h1>
      <form
        className="mc-form"
        id="mc-form"
        onSubmit={onSubmit}
        aria-busy={isSubmitting}
      >
        <label className="mc-field">
          이메일
          <input
            autoComplete="email"
            name="loginEmail"
            defaultValue={savedEmail}
            placeholder="you@example.com"
            type="email"
            required
            maxLength={255}
            disabled={isSubmitting}
            aria-invalid={Boolean(fieldErrors.email)}
            aria-describedby={
              fieldErrors.email ? 'login-email-error' : undefined
            }
          />
        </label>

        {fieldErrors.email && (
          <p
            id="login-email-error"
            className="mc-field-error"
            role="alert"
          >
            {fieldErrors.email.join(' ')}
          </p>
        )}

        <label className="mc-field">
          비밀번호
          <input
            autoComplete="current-password"
            name="loginPw"
            type="password"
            required
            disabled={isSubmitting}
            aria-invalid={Boolean(fieldErrors.password)}
            aria-describedby={
              fieldErrors.password ? 'login-password-error' : undefined
            }
          />
        </label>

        {fieldErrors.password && (
          <p
            id="login-password-error"
            className="mc-field-error"
            role="alert"
          >
            {fieldErrors.password.join(' ')}
          </p>
        )}

        <div className="mc-check-row">
          <label className="mc-field mc-check">
            <input
              name="saveId"
              type="checkbox"
              defaultChecked={Boolean(savedEmail)}
              disabled={isSubmitting}
              onChange={(e) => {
                if (!e.target.checked) {
                  try {
                    window.localStorage.removeItem(SAVED_EMAIL_KEY)
                  } catch {
                    window.alert('저장된 이메일을 지우지 못했어요. 브라우저의 사이트 데이터 설정을 확인해 주세요.')
                  }
                }
              }}
            />
            아이디 저장
          </label>
        </div>

        <div className="mc-error" role="alert">
          {error}
        </div>

        <div className="mc-foot">
          <button
            className="mc-primary"
            type="submit"
            disabled={isSubmitting}
          >
            {isSubmitting ? '로그인 중…' : '로그인'}
          </button>
        </div>
      </form>
      <button type="button" className="mc-link" onClick={() => openPopup(<PwFindPopup />)}>비밀번호를 잊으셨나요?</button>
      <div className="mc-card" style={{ marginTop: 22, textAlign: 'center' }}>
        <p className="mc-muted">머니쿠션이 처음이신가요?</p>
        <button type="button" className="mc-secondary" onClick={() => navigate(PATHS.signup)}>회원가입</button>
      </div>
    </Layout>
  )
}

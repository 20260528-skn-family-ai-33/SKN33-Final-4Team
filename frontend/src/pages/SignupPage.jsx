// 회원가입
// 비밀번호 조건과 확인 칸 일치 여부를 입력하는 즉시 보여 준다
// 비밀번호 규칙 (10/22 결정기록 X): 8~25자, 영문 · 숫자 · 특수문자 중 2가지 이상. 확인 칸은 화면에서만 비교하고 서버엔 password 한 번만 보낸다
// 약관 이름을 누르면 상세 팝업(TermsPopup)이 열리고, 필수 약관에 모두 동의해야 가입된다
import { useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import TermsPopup from '../popups/TermsPopup.jsx'
import { SIGNUP_TERMS, TERMS } from '../popups/termsText.js'
import { usePopup } from '../popups/context.js'
import { PATHS } from '../routes.js'
import { apiSend } from '../utils/api.js'

const PW_MIN = 8
const PW_MAX = 25
// 영문 · 숫자 · 특수문자 중 몇 가지를 썼는지
const pwKinds = (p) => [/[A-Za-z]/, /[0-9]/, /[^A-Za-z0-9\s]/].filter((re) => re.test(p)).length
const PW_RULES = [
  [`${PW_MIN}~${PW_MAX}자`, (p) => p.length >= PW_MIN && p.length <= PW_MAX],
  ['영문 · 숫자 · 특수문자 중 2가지 이상', (p) => pwKinds(p) >= 2],
]
const pwOk = (p) => PW_RULES.every(([, test]) => test(p))

export default function SignupPage() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  // 약관 동의: { terms: true/false, privacy: true/false } — termsText.js 에 약관을 추가하면 저절로 늘어난다
  const everyTerm = (on) => Object.fromEntries(SIGNUP_TERMS.map((k) => [k, on]))
  const [agree, setAgree] = useState(() => everyTerm(false))
  const [error, setError] = useState('')
  const [pwError, setPwError] = useState('')   // 비밀번호 칸 아래에 보이는 오류
  const termsCard = useRef(null)
  const allAgreed = Object.values(agree).every(Boolean)

  const setOne = (key, on) => {
    setAgree((a) => ({ ...a, [key]: on }))
    setTermsError('')
  }

  const setAll = (on) => {
    setAgree(everyTerm(on))
    setTermsError('')
  }
  // 팝업 안 "동의하고 닫기"를 누르면 그 약관 체크박스가 켜진다
  const openTerms = (key) => openPopup(<TermsPopup kind={key} onAgree={() => setOne(key, true)} />)

  // 확인 칸: 비어 있으면 아무 말 안 함 / 같으면 ok / 다르면 bad
  const match = !pw2 ? '' : pw === pw2 ? 'ok' : 'bad'
  const matchText = { '': '', ok: '비밀번호가 일치해요.', bad: '비밀번호가 일치하지 않아요.' }[match]

  const onSubmit = async (e) => {
    e.preventDefault()

    if (submittingRef.current) return

    setError('')
    setFieldErrors({})
    setPwError('')
    setTermsError('')

    const form = new FormData(e.currentTarget)
    const nickname = String(form.get('suName') ?? '').trim()
    const email = String(form.get('suEmail') ?? '').trim()

    if (!nickname) {
      setFieldErrors({ nickname: ['이름(닉네임)을 입력해 주세요.'] })
      return
    }

    if (!pwOk(pw)) {
      setPwError('비밀번호 조건을 확인해 주세요.')
      return
    }

    if (pw !== pw2) {
      setPwError('비밀번호가 일치하지 않아요.')
      return
    }

    if (!allAgreed) {
      setTermsError('필수 약관에 모두 동의해 주세요.')
      termsCard.current?.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      })
      return
    }

    const consents = SIGNUP_TERMS
      .filter((key) => agree[key])
      .map((key) => ({
        purpose: key,
        documentVersion: TERMS[key].version,
      }))

    submittingRef.current = true
    setIsSubmitting(true)

    try {
      const user = await apiSend('/api/auth/signup/', 'POST', {
        email,
        password: pw,
        nickname,
        consents,
      })

      if (!user?.userId || user.onboardingCompleted !== false) {
        setError('가입 응답을 확인할 수 없어요. 로그인 상태를 확인해 주세요.')
        return
      }

      navigate(PATHS.onboard, { replace: true })
    } catch (err) {
      setFieldErrors(err.fields ?? {})
      setPwError(err.fields?.password?.join(' ') ?? '')

      if (err.fields?.consents) {
        setTermsError('필수 약관과 문서 버전을 확인해 주세요.')
      }

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

  const [fieldErrors, setFieldErrors] = useState({})
  const [termsError, setTermsError] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const submittingRef = useRef(false)
  return (
    <Layout
      title="회원가입"
      showBack
      foot={
        <button
          className="mc-primary"
          form="mc-form"
          type="submit"
          disabled={isSubmitting}
        >
          {isSubmitting ? '가입 중…' : '가입하고 시작하기'}
        </button>
      }
    >
      <div className="mc-eyebrow">회원가입</div>
      <h1>머니쿠션 계정을 만들어요</h1>

      <form
        className="mc-form"
        id="mc-form"
        onSubmit={onSubmit}
        aria-busy={isSubmitting}
      >
        <fieldset
          disabled={isSubmitting}
          aria-label="회원가입 정보"
          style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}
        >
          <label className="mc-field">
            이름(닉네임)
            <input
              name="suName"
              placeholder="홍길동"
              type="text"
              required
              maxLength={100}
              aria-invalid={Boolean(fieldErrors.nickname)}
              aria-describedby={
                fieldErrors.nickname ? 'signup-nickname-error' : undefined
              }
            />
          </label>

          {fieldErrors.nickname && (
            <p id="signup-nickname-error" className="mc-field-error" role="alert">
              {fieldErrors.nickname.join(' ')}
            </p>
          )}

          <label className="mc-field">
            이메일
            <input
              autoComplete="email"
              name="suEmail"
              placeholder="you@example.com"
              type="email"
              required
              maxLength={255}
              aria-invalid={Boolean(fieldErrors.email)}
              aria-describedby={
                fieldErrors.email ? 'signup-email-error' : undefined
              }
            />
          </label>

          {fieldErrors.email && (
            <p id="signup-email-error" className="mc-field-error" role="alert">
              {fieldErrors.email.join(' ')}
            </p>
          )}

          <label className="mc-field">
            비밀번호
            <input
              aria-describedby={
                pwError ? 'mc-pwrules signup-pw-error' : 'mc-pwrules'
              }
              aria-invalid={Boolean(pwError)}
              autoComplete="new-password"
              name="suPw"
              placeholder="8~25자, 영문 · 숫자 · 특수문자 중 2가지 이상"
              type="password"
              required
              maxLength={PW_MAX}
              value={pw}
              onChange={(e) => {
                setPw(e.target.value)
                setPwError('')
              }}
            />
          </label>

          <ul
            aria-label="비밀번호 조건"
            aria-live="polite"
            className="mc-pw-rules"
            id="mc-pwrules"
          >
            {PW_RULES.map(([label, test]) => (
              <li key={label} className={test(pw) ? 'ok' : ''}>
                {label}
              </li>
            ))}
          </ul>

          <label className="mc-field">
            비밀번호 확인
            <input
              aria-describedby={
                pwError ? 'mc-pwmatch signup-pw-error' : 'mc-pwmatch'
              }
              autoComplete="new-password"
              name="suPw2"
              placeholder="비밀번호를 한 번 더 입력"
              type="password"
              required
              maxLength={PW_MAX}
              value={pw2}
              onChange={(e) => {
                setPw2(e.target.value)
                setPwError('')
              }}
              className={match === 'bad' ? 'mc-bad' : undefined}
              aria-invalid={match === 'bad'}
            />
          </label>

          <p
            aria-live="polite"
            className={`mc-pw-match ${match}`}
            id="mc-pwmatch"
          >
            {matchText}
          </p>

          {pwError && (
            <p id="signup-pw-error" className="mc-error" role="alert">
              {pwError}
            </p>
          )}

          <div className="mc-card" ref={termsCard}>
            <label className="mc-field mc-check mc-terms-all">
              <input
                type="checkbox"
                checked={allAgreed}
                onChange={(e) => setAll(e.target.checked)}
              />
              약관 전체 동의
            </label>

            {SIGNUP_TERMS.map((key) => [key, TERMS[key]]).map(([key, doc]) => (
              <div className="mc-terms-row" key={key}>
                <input
                  aria-label={doc.label}
                  checked={agree[key]}
                  name={`agree_${key}`}
                  type="checkbox"
                  onChange={(e) => setOne(key, e.target.checked)}
                />
                <button
                  type="button"
                  className="mc-terms-open"
                  aria-haspopup="dialog"
                  onClick={() => openTerms(key)}
                >
                  {doc.label}<span>보기</span>
                </button>
              </div>
            ))}

            {termsError && (
              <p className="mc-error mc-terms-err" role="alert">
                {termsError}
              </p>
            )}

            <p className="mc-note" style={{ paddingBottom: 0 }}>
              계좌 조회 동의는 계좌 연결 단계에서 따로 받아요.
            </p>
          </div>
        </fieldset>

        <div className="mc-error" role="alert">
          {error}
        </div>
      </form>
    </Layout>
  )
}

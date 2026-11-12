// 회원가입
// 비밀번호 조건과 확인 칸 일치 여부를 입력하는 즉시 보여 준다
// 비밀번호 규칙 (10/22 결정기록 X): 8~25자, 영문 · 숫자 · 특수문자 중 2가지 이상. 확인 칸은 화면에서만 비교하고 서버엔 password 한 번만 보낸다
// 약관 이름을 누르면 상세 팝업(TermsPopup)이 열리고, 필수 약관에 모두 동의해야 가입된다
// 약관 원문은 서버(GET /api/consent-documents/)에서 받는다. 못 받으면 가입 버튼을 눌러도 보내지 않는다
import { useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import PasswordInput from '../components/PasswordInput.jsx'
import { useLoggedInRedirect } from '../utils/useLoggedInRedirect.js'
import TermsPopup from '../popups/TermsPopup.jsx'
import { useConsentDocs } from '../popups/consentDocs.js'
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
  useLoggedInRedirect()  // 이미 로그인했으면 시작하기 · 홈으로 (가입 직후 뒤로 가기 등)
  const { openPopup } = usePopup()
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  // 예산 AI 같은 선택 동의가 회원가입 필수 약관에 섞이지 않게 목적도 구분한다.
  const terms = useConsentDocs((d) => d.productScope === null && ['terms', 'privacy'].includes(d.purpose))
  const [error, setError] = useState('')
  const [pwError, setPwError] = useState('')   // 비밀번호 칸 아래에 보이는 오류
  const termsCard = useRef(null)

  const setOne = (doc, on) => {
    terms.setOne(doc, on)
    setTermsError('')
  }

  const setAll = (on) => {
    terms.setAll(on)
    setTermsError('')
  }
  // 팝업 안 "동의하고 닫기"를 누르면 그 약관 체크박스가 켜진다
  const openTerms = (doc) => openPopup(<TermsPopup doc={doc.document} onAgree={() => setOne(doc, true)} />)

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
      setFieldErrors({ nickname: ['이름을 입력해 주세요.'] })
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

    if (!terms.docs) {
      setTermsError('약관을 불러온 뒤 동의해 주세요.')
      return
    }

    if (!terms.allAgreed) {
      setTermsError('필수 약관에 모두 동의해 주세요.')
      termsCard.current?.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      })
      return
    }

    // 화면에 보여 준 버전 그대로 보낸다. 해시는 서버가 그 버전의 원문으로 만든다
    const consents = terms.docs
      .filter(terms.isAgreed)
      .map((doc) => ({
        purpose: doc.purpose,
        documentVersion: doc.version,
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

      if (err.fields?.consents || err.code === 'document_outdated') {
        // 약관이 바뀌었을 수 있다 → 새로 받아 다시 확인받는다 (체크는 지워진다)
        terms.reload()
        setTermsError('약관이 바뀌었을 수 있어요. 새 약관을 확인하고 다시 동의해 주세요.')
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
            이름
            <input
              name="suName"
              placeholder="홍길동"
              type="text"
              required
              maxLength={100}
              aria-invalid={Boolean(fieldErrors.nickname)}
              aria-describedby={
                fieldErrors.nickname ? 'signup-nickname-hint signup-nickname-error' : 'signup-nickname-hint'
              }
            />
            {/* 저축 후보 감지가 이체 적요의 이름과 비교한다 (11/4) */}
            <span id="signup-nickname-hint" className="mc-muted" style={{ display: 'block', marginTop: 6 }}>
              은행 거래내역에 나오는 실명으로 적어 주세요.<br />
              내 계좌끼리 옮긴 돈을 소비로 잘못 세지 않도록 확인할 때 써요.
            </span>
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
            <PasswordInput
              aria-describedby={
                pwError ? 'mc-pwrules signup-pw-error' : 'mc-pwrules'
              }
              aria-invalid={Boolean(pwError)}
              autoComplete="new-password"
              name="suPw"
              placeholder="사용할 비밀번호를 입력해 주세요"
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
            <PasswordInput
              aria-describedby={
                pwError ? 'mc-pwmatch signup-pw-error' : 'mc-pwmatch'
              }
              autoComplete="new-password"
              name="suPw2"
              placeholder="위와 같은 비밀번호를 입력해 주세요"
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
            {terms.error ? (
              <div role="alert">
                <p className="mc-error">약관을 불러오지 못했어요. 약관을 확인해야 가입할 수 있어요.</p>
                <button type="button" className="mc-link" onClick={terms.reload}>다시 불러오기</button>
              </div>
            ) : !terms.docs ? (
              <p className="mc-muted">약관을 불러오는 중…</p>
            ) : (
              <>
                <label className="mc-field mc-check mc-terms-all">
                  <input
                    type="checkbox"
                    checked={terms.allAgreed}
                    onChange={(e) => setAll(e.target.checked)}
                  />
                  약관 전체 동의
                </label>

                {terms.docs.map((doc) => (
                  <div className="mc-terms-row" key={doc.purpose}>
                    <input
                      aria-label={doc.document.label}
                      checked={terms.isAgreed(doc)}
                      name={`agree_${doc.purpose}`}
                      type="checkbox"
                      onChange={(e) => setOne(doc, e.target.checked)}
                    />
                    <button
                      type="button"
                      className="mc-terms-open"
                      aria-haspopup="dialog"
                      onClick={() => openTerms(doc)}
                    >
                      {doc.document.label}<span>보기</span>
                    </button>
                  </div>
                ))}
              </>
            )}

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

// 마이 — 탭 화면이 아니라 하단 탭 없음
// 요약은 저장된 실제 데이터만 표시한다. 조회 실패와 미설정은 0원으로 바꾸지 않는다.
import { useRef, useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import { usePopup } from '../popups/context.js'
import PrivacyPopup from '../popups/PrivacyPopup.jsx'
import WithdrawPopup from '../popups/WithdrawPopup.jsx'
import HelpPopup from '../popups/HelpPopup.jsx'
import { PATHS } from '../routes.js'
import { apiSend, useApi } from '../utils/api.js'
import { won } from '../utils/money.js'
import { paydayText } from '../utils/days.js'
import { clearConnectDraft } from '../utils/connectDraft.js'
import { saveId } from '../utils/chatConversation.js'

const MENU = [
  { icon: 'IdCard', name: '내 정보', sub: '나이·자취 여부·우선순위 등', to: PATHS.profile, state: { from: 'my' } },
  { icon: 'Banknote', name: '월급·급여일', summary: 'salary', to: PATHS.salary, state: { from: 'my' } },
  { icon: 'Repeat', name: '고정지출', summary: 'fixed', to: PATHS.fixed, state: { from: 'my' } },  // 고정지출 관리 모드 (확정 목록 · 중지 · 미납 고르기)
  { icon: 'PiggyBank', name: '정기 저축', summary: 'savings', to: PATHS.savings, state: { from: 'my' } },  // 저축 확인 화면을 관리 모드로 연다 (PR #29 리뷰)
  { icon: 'ShieldCheck', name: '지킬 돈', summary: 'protect', to: PATHS.protect, state: { from: 'my' } },
  { icon: 'Wallet', name: '카테고리 예산', sub: '추천 예산 확인·수정', to: PATHS.budget },
  { icon: 'Link', name: '연결 관리', summary: 'connections', to: PATHS.connections },
]

// '2026-11-25' → '11월 25일'
const monthDay = (iso) => `${Number(iso.slice(5, 7))}월 ${Number(iso.slice(8, 10))}일`

function summaryText(key, data) {
  const item = data[key]
  if (key === 'salary') {
    const current = `${item.takeHomeIncome == null ? '월급 미설정' : `월 ${won(item.takeHomeIncome)}`} · ${item.payday == null ? '급여일 미설정' : `매월 ${paydayText(item.payday)}`}`
    // 다음 주기부터 바뀔 값은 줄을 바꿔 따로 (11/12). 날짜가 같으면 한 줄로: '11월 10일부터 매월 10일 · 월 3,000,000원'
    const pay = item.pendingPaydayChange, income = item.pendingIncomeChange
    const changes = {}
    if (pay) (changes[pay.effectiveOn] ??= []).push(`매월 ${paydayText(pay.payday)}`)
    if (income) (changes[income.effectiveOn] ??= []).push(`월 ${won(income.takeHomeIncome)}`)
    const dates = Object.keys(changes).sort()
    if (!dates.length) return current
    return <>{current}{dates.map((d) => <span key={d} className="mc-my-pending">{monthDay(d)}부터 {changes[d].join(' · ')}</span>)}</>
  }
  if (key === 'fixed' || key === 'savings') return `${item.count}건 · ${won(item.monthlyTotal)}`
  // 11/9: 한 줄이 길어 쿠션은 빼고 생활비 · 저축만 (쿠션은 지킬 돈 화면에서)
  if (key === 'protect') return [['livingBudget', '생활비'], ['savingsFloor', '저축']]
    .map(([field, label]) => `${label} ${item[field] == null ? '미설정' : won(item[field])}`).join(' · ')
  return `은행 ${item.bankCount}곳 · 계좌 ${item.accountCount}개 · 카드 ${item.cardCount}장`
}

export default function MyPage() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()
  const { state: navState } = useLocation()

  const { data: user, error: userError } = useApi('/api/auth/me/')
  const summary = useApi('/api/my/')
  const [logoutError, setLogoutError] = useState('')
  const [isLoggingOut, setIsLoggingOut] = useState(false)
  const logoutRef = useRef(false)

  const onLogout = async () => {
    if (logoutRef.current) return

    logoutRef.current = true
    setIsLoggingOut(true)
    setLogoutError('')

    try {
      const result = await apiSend('/api/auth/logout/', 'POST')

      if (result?.ok !== true) {
        setLogoutError('로그아웃 결과를 확인할 수 없어요. 다시 시도해 주세요.')
        return
      }

      clearConnectDraft()  // 계좌 연결에서 고르던 기관을 다음 사람이 보지 않게 (11/12)
      saveId(null)
      navigate(PATHS.login, { replace: true })
    } catch (err) {
      setLogoutError(
        err.status
          ? err.message
          : '서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.'
      )
    } finally {
      logoutRef.current = false
      setIsLoggingOut(false)
    }
  }

  if (userError?.code === 'not_authenticated' || summary.error?.code === 'not_authenticated') {
    return <Navigate to={PATHS.login} replace />
  }

  if (userError) {
    return (
      <Layout title="마이메뉴" showBack>
        <p className="mc-error" role="alert">
          {userError.status
            ? userError.message
            : '회원 정보를 불러오지 못했어요. 연결 상태를 확인해 주세요.'}
        </p>
        <button
          type="button"
          className="mc-secondary"
          onClick={() => window.location.reload()}
        >
          다시 시도
        </button>
      </Layout>
    )
  }

  if (!user) {
    return (
      <Layout title="마이메뉴" showBack>
        <p role="status">회원 정보를 불러오는 중이에요.</p>
      </Layout>
    )
  }

  return (
    <Layout title="마이메뉴" showBack>
      <div className="mc-card">
        <div className="mc-row">
          <span className="mc-tile"><Icon name="UserRound" /></span>
          <div style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>
            <h2>{user.nickname}</h2>
            <span className="mc-muted">{user.email}</span>
          </div>
          <button type="button" className="mc-link" onClick={() => navigate(PATHS.myInfo)}>내 정보 보기 ›</button>
        </div>
      </div>

      {/* 내 정보 · 월급을 고치고 돌아왔을 때. 승인 예산은 자동으로 다시 나누지 않는다 (salary.md '저장과 연동').
          저장한 지킬 돈이 그대로면 월급이 바뀌어도 생활비가 안 바뀌어서 추천 화면이 아니라 지킬 돈으로 보낸다 */}
      {navState?.budgetNote && (
        <p className="mc-note" role="status">
          승인한 예산은 그대로예요. 바뀐 내용으로 다시 나누려면{' '}
          <button type="button" className="mc-link" onClick={() => navigate(PATHS.protect, { state: { from: 'my' } })}>예산 다시 추천받기</button>
        </p>
      )}

      <div className="mc-row mc-section"><h2>내 돈 관리</h2></div>
      <div className="mc-card">
        {MENU.map((m) => (
          <button type="button" className="mc-listrow" key={m.name} onClick={() => navigate(m.to, { state: m.state })}>
            <span className="mc-tile"><Icon name={m.icon} /></span>
            <span className="mc-grow"><strong>{m.name}</strong><span className="mc-muted">{m.summary ? summary.error ? '요약을 불러오지 못했어요' : summary.data ? summaryText(m.summary, summary.data) : '불러오는 중…' : m.sub}</span></span>
            <span className="mc-right"> <span className="mc-muted">›</span></span>
          </button>
        ))}
      </div>

      {summary.error && <div role="alert">
        <p className="mc-error">내 돈 관리 요약을 불러오지 못했어요.</p>
        <button type="button" className="mc-link" onClick={summary.reload}>요약 다시 불러오기</button>
      </div>}
      <section aria-labelledby="my-saved-policies-title">
        <div className="mc-row mc-section"><h2 id="my-saved-policies-title">청년 정책</h2></div>
        <div className="mc-card">
          <button type="button" className="mc-listrow" onClick={() => navigate(PATHS.myPolicies)}>
            <span className="mc-tile"><Icon name="Landmark" /></span>
            <span className="mc-grow"><strong>저장한 정책</strong><span className="mc-muted">관심 있는 청년 정책 모아보기</span></span>
            <span className="mc-right"><span className="mc-muted">›</span></span>
          </button>
        </div>
      </section>

      <div className="mc-row mc-section"><h2>앱 설정</h2></div>
      <div className="mc-card">
        <button type="button" className="mc-listrow" onClick={() => openPopup(<PrivacyPopup onChanged={summary.reload} />)}>동의·데이터 관리　›</button>
        <button type="button" className="mc-listrow" onClick={() => openPopup(<HelpPopup />)}>도움말　›</button>
        <button
          type="button"
          className="mc-listrow"
          onClick={onLogout}
          disabled={isLoggingOut}
        >
          {isLoggingOut ? '로그아웃 중…' : '로그아웃　›'}
        </button>
        <button type="button" className="mc-listrow" disabled={isLoggingOut} onClick={() => openPopup(<WithdrawPopup />)}>회원 탈퇴　›</button>

        {logoutError && (
          <p className="mc-error" role="alert">
            {logoutError}
          </p>
        )}
      </div>
    </Layout>
  )
}

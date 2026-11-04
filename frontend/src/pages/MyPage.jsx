// 마이 — 탭 화면이 아니라 하단 탭 없음
// 오른쪽 글자(월 280만원 등)는 시안 예시값 — API 설계 후 useApi 로 바꾼다
import { useRef, useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import { usePopup } from '../popups/context.js'
import PrivacyPopup from '../popups/PrivacyPopup.jsx'
import HelpPopup from '../popups/HelpPopup.jsx'
import { PATHS } from '../routes.js'
import { apiSend, useApi } from '../utils/api.js'

const MENU = [
  { icon: 'IdCard', name: '내 정보', sub: '나이·자취 여부·우선순위 등', to: PATHS.profile, state: { from: 'my' } },
  { icon: 'Banknote', name: '월급·급여일', sub: '월 280만원 · 매월 1일', to: PATHS.salary, state: { from: 'my' } },
  { icon: 'Repeat', name: '고정지출', sub: '5건 · 962,000원', to: PATHS.fixed, state: { from: 'my' } },  // 고정지출 관리 모드 (확정 목록 · 중지 · 미납 고르기)
  { icon: 'PiggyBank', name: '정기 저축', sub: '2건 · 400,000원', to: PATHS.savings, state: { from: 'my' } },  // 저축 확인 화면을 관리 모드로 연다 (PR #29 리뷰)
  { icon: 'ShieldCheck', name: '지킬 돈', sub: '생활비 1,120,000원 · 저축 410,000원 · 쿠션 210,000원', to: PATHS.protect, state: { from: 'my' } },
  { icon: 'Wallet', name: '카테고리 예산', sub: '추천 예산 확인·수정', to: PATHS.budget },
  { icon: 'Link', name: '연결 관리', sub: '은행 2곳 · 계좌 2개 · 카드 2장', to: PATHS.connections },
]

export default function MyPage() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()
  const { state: navState } = useLocation()

    const { data: user, error: userError } = useApi('/api/auth/me/')
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

  if (userError?.code === 'not_authenticated') {
    return <Navigate to={PATHS.login} replace />
  }

  if (userError) {
    return (
      <Layout title="마이" showBack>
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
      <Layout title="마이" showBack>
        <p role="status">회원 정보를 불러오는 중이에요.</p>
      </Layout>
    )
  }

  return (
    <Layout title="마이" showBack>
      <div className="mc-card">
        <div className="mc-row">
          <span className="mc-tile"><Icon name="UserRound" /></span>
          <div style={{ flex: 1 }}>
            <h2>{user.nickname}</h2>
            <span className="mc-muted">{user.email}</span>
          </div>
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
            <span className="mc-grow"><strong>{m.name}</strong><span className="mc-muted">{m.sub}</span></span>
            <span className="mc-right"> <span className="mc-muted">›</span></span>
          </button>
        ))}
      </div>

      <div className="mc-row mc-section"><h2>앱 설정</h2></div>
      <div className="mc-card">
        <button type="button" className="mc-listrow" onClick={() => openPopup(<PrivacyPopup />)}>동의·데이터 관리　›</button>
        <button type="button" className="mc-listrow" onClick={() => openPopup(<HelpPopup />)}>도움말　›</button>
        <button
          type="button"
          className="mc-listrow"
          onClick={onLogout}
          disabled={isLoggingOut}
        >
          {isLoggingOut ? '로그아웃 중…' : '로그아웃　›'}
        </button>

        {logoutError && (
          <p className="mc-error" role="alert">
            {logoutError}
          </p>
        )}
      </div>
      <button type="button" className="mc-link" onClick={() => navigate(PATHS.login)}>처음 사용하는 흐름 다시 보기</button>
    </Layout>
  )
}

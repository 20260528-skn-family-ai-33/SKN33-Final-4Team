// 마이 — 탭 화면이 아니라 하단 탭 없음
// 오른쪽 글자(월 280만원 등)는 시안 예시값 — API 설계 후 useApi 로 바꾼다
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import { usePopup } from '../popups/context.js'
import PrivacyPopup from '../popups/PrivacyPopup.jsx'
import HelpPopup from '../popups/HelpPopup.jsx'
import { PATHS } from '../routes.js'

const MENU = [
  { icon: 'IdCard', name: '내 정보', sub: '나이·우선순위 등', to: PATHS.profile },
  { icon: 'Banknote', name: '월급·급여일', sub: '월 280만원 · 매월 1일', to: PATHS.salary },
  { icon: 'Repeat', name: '고정지출', sub: '3건 · 770,000원', to: PATHS.fixed },
  { icon: 'Compass', name: '예산 산정 방향', sub: '선택 전', to: PATHS.direction },
  { icon: 'ShieldCheck', name: '지킬 돈', sub: '생활비 1,120,000원 · 저축 410,000원', to: PATHS.protect },
  { icon: 'Wallet', name: '카테고리 예산', sub: '추천 예산 확인·수정', to: PATHS.budget },
  { icon: 'Link', name: '연결 관리', sub: '은행 2곳 · 계좌 2개 · 카드 2장', to: PATHS.connections },
]

export default function MyPage() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()

  return (
    <Layout title="마이" showBack>
      <div className="mc-card">
        <div className="mc-row">
          <span className="mc-tile"><Icon name="UserRound" /></span>
          <span style={{ flex: 1 }}><h2>머니쿠션 사용자</h2><span className="mc-muted">첫 돈 관리, 나에게 맞는 속도로</span></span>
        </div>
      </div>

      <div className="mc-row mc-section"><h2>내 돈 관리</h2></div>
      <div className="mc-card">
        {MENU.map((m) => (
          <button type="button" className="mc-listrow" key={m.name} onClick={() => navigate(m.to)}>
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
        {/* 로그아웃 API 는 로그인 기능과 함께 */}
        <button type="button" className="mc-listrow" onClick={() => navigate(PATHS.login)}>로그아웃　›</button>
      </div>
      <button type="button" className="mc-link" onClick={() => navigate(PATHS.login)}>처음 사용하는 흐름 다시 보기</button>
    </Layout>
  )
}

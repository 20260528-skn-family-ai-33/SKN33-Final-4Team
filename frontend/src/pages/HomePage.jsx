// 홈
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import { comma, won } from '../utils/money.js'
import { useApi } from '../utils/api.js'
import ApiStatus from '../components/ApiStatus.jsx'
import { PATHS } from '../routes.js'
import { usePopup } from '../popups/context.js'
import CalcPopup from '../popups/CalcPopup.jsx'
import CardBillPopup from '../popups/CardBillPopup.jsx'

export default function HomePage() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()
  const pendingUrl = `${PATHS.txs}?category=확인 필요`
  const { data, error } = useApi('/api/home/')  // 장고에게 홈 데이터 받아오기

  // 아직 못 받았으면 "불러오는 중…" (실패하면 안내)
  if (!data) return <Layout title="머니쿠션" activeTab="home"><ApiStatus error={error} /></Layout>
  const { period: PERIOD, home: h, mustPay: MUST_PAY } = data

  return (
    <Layout title="머니쿠션" activeTab="home">
      <div className="mc-home-heading">
        <div className="mc-home-heading-left">
          <div className="mc-eyebrow">{PERIOD.label} · 이번 급여 주기</div>
          <h2>오늘도 내 돈에 여유를</h2>
        </div>
        <div className="mc-home-heading-right">
          <span className="mc-badge">월급날 D−{PERIOD.dday}</span>
          <p className="mc-home-updated">
            {PERIOD.updated}
            <br />
            <button type="button" className="mc-link" onClick={() => openPopup(<CalcPopup />)}>계산 근거</button>
          </p>
        </div>
      </div>

      <div className="mc-card mc-bal">
        <div className="mc-row">
          <span className="mc-muted">입출금 계좌 잔액 총액</span>
          {/* 연결 관리 화면은 다음 단계에서 */}
          <button type="button" className="mc-link">계좌 {h.accountCount}개 ›</button>
        </div>
        <div className="mc-money mc-bal-n">{won(h.balanceTotal)}</div>
        <div className="mc-row mc-bal-cu">
          <span className="mc-muted">머니 쿠션 확보</span>
          <span>
            <strong>{won(h.cushionNow)}</strong> <span className="mc-muted">/ {won(h.cushionGoal)}</span>
          </span>
        </div>
        {/* style 은 문자열이 아니라 객체로 쓴다 */}
        <div className="mc-bar"><span style={{ width: `${h.cushionBar}%` }}></span></div>
      </div>

      <div className="mc-hero mc-budget-hero">
        <div className="mc-row">
          <span className="mc-hero-title">이번 주기 생활비 <em className="mc-use-badge">{h.livingPct}% 사용</em></span>
          <button type="button" className="mc-link" onClick={() => navigate(PATHS.txs)}>거래 보기 ›</button>
        </div>
        <div className="mc-budget-amount">
          <strong>{comma(h.livingUsed)}<small>원</small></strong>
          <span>/ {won(h.livingBudget)}</span>
        </div>
        <div className="mc-bar" role="progressbar" aria-label="이번 주기 생활비 사용률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={h.livingPct}>
          <span style={{ width: `${h.livingPct}%` }}></span>
        </div>
        <div className="mc-row mc-budget-remaining">
          <span>남은 생활비 <strong>{won(h.livingLeft)}</strong></span>
          <button type="button" className="mc-link" onClick={() => navigate(PATHS.assistant)}>예산 조정</button>
        </div>
      </div>

      <div className="mc-future-cards">
        <div className="mc-fc mc-fc-lime"><span>하루 생활비</span><strong>{won(h.daily)}</strong></div>
        <div className="mc-fc mc-fc-blue"><span>여윳돈</span><strong>{won(h.spare)}</strong></div>
      </div>

      {/* {조건 && 화면} = 조건이 참일 때만 보여주기 */}
      {h.pendingCount > 0 && (
        <button type="button" className="mc-pend-banner" onClick={() => navigate(pendingUrl)}>
          <span className="mc-pend-dot">{h.pendingCount}</span>
          <span className="mc-grow">
            <strong>카테고리를 확인해 주세요</strong>
            <small>AI가 확신하지 못한 거래 {h.pendingCount}건</small>
          </span>
          <span className="mc-muted">›</span>
        </button>
      )}

      <div className="mc-row mc-section"><h2>꼭 나갈 돈</h2></div>
      <div className="mc-card">
        {/* 목록.map(...) = 하나씩 꺼내서 그리기. key 는 항목마다 다른 값 */}
        {MUST_PAY.map((p) => (
          <button
            type="button"
            className="mc-listrow"
            key={p.name}
            onClick={() => (p.link ? navigate(p.link) : openPopup(<CardBillPopup />))  /* 링크가 없으면(카드 청구서) 팝업 */}
          >
            <span className="mc-tile"><Icon name={p.icon} /></span>
            <span className="mc-grow"><strong>{p.name}</strong><span className="mc-muted">{p.sub}</span></span>
            <span className="mc-right">{won(p.amount)} <span className="mc-muted">›</span></span>
          </button>
        ))}
      </div>

      <div className="mc-home-action">
        <button type="button" className="mc-action" onClick={() => navigate(PATHS.assistant)}>
          <Icon name="MessageCircle" /> 챗봇에게 내 소비 물어보기
        </button>
      </div>
    </Layout>
  )
}

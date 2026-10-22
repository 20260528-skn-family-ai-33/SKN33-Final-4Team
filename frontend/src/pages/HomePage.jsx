// 홈 (2026-10-20 배치 개선안)
// 위에서부터: 급여 주기 줄 → 확인할 거래 알림 → 오늘 쓸 수 있는 돈(파란 카드, 여윳돈 포함) → 계좌 잔액 → 꼭 나갈 돈
// 머니 쿠션은 쿠션 탭에서 따로 본다
// 되돌리기: 이 파일을 git 의 이전 버전으로 돌리고, app.css 맨 아래 "홈 개선안" 블록을 지우면 원래 화면으로 돌아간다
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

// 급여 주기 전체 일수: "10.01 — 10.31" → 31 (못 읽으면 null)
function cycleDays(label) {
  const m = label?.match(/(\d{1,2})\.(\d{1,2})\D+(\d{1,2})\.(\d{1,2})/)
  if (!m) return null
  const year = new Date().getFullYear()
  const start = new Date(year, m[1] - 1, m[2])
  let end = new Date(year, m[3] - 1, m[4])
  if (end < start) end = new Date(year + 1, m[3] - 1, m[4])  // 12월 → 1월로 넘어가는 주기
  return Math.round((end - start) / 86_400_000) + 1
}

// 기준선(%): 오늘까지 이만큼 썼으면 딱 맞는 지점 = 지난 날 수 ÷ 전체 날 수
function pacePct(label, dday) {
  const total = cycleDays(label)
  if (!total || dday == null) return null
  const passed = Math.min(Math.max(total - dday, 0), total)
  return Math.round((passed / total) * 100)
}

// 사용 속도 한마디: 적정(기준선)보다 2%p 넘게 차이 날 때만 빠르다/여유 있다고 말한다
function paceNote(usedPct, pace) {
  if (pace == null) return null
  const diff = Math.round(usedPct - pace)
  if (diff > 2) return { tone: 'fast', text: `적정보다 ${diff}%p 빨라요` }
  if (diff < -2) return { tone: 'slow', text: `적정보다 ${-diff}%p 여유 있어요` }
  return { tone: 'ok', text: '적정 속도예요' }
}

// 꼭 나갈 돈 상태 배지 (10/22 PR #26 리뷰, docs/api/home.md '미납 안내')
//   needs_review = 출금일 +3일이 지나도 출금을 못 찾음 → "미납 확인 필요 ›" (누르면 고정지출 화면의 [고르기])
//   unpaid 인데 출금일이 지남 = 실제 출금과 자동 연결(±3일)을 기다리는 중 → "출금 확인 중"
//   지난 주기 미납은 다음 월급날이 되면 서버가 목록에서 뺀다 (화면은 받은 것만 그린다)
function payBadge(p) {
  if (p.paymentState === 'needs_review') return { tone: 'review', text: '미납 확인 필요' }
  const today = new Date().toLocaleDateString('sv-SE')   // 'YYYY-MM-DD' (내 컴퓨터 날짜 기준)
  if (p.paymentState === 'unpaid' && p.dueDate && p.dueDate < today) return { tone: 'check', text: '출금 확인 중' }
  return null
}

export default function HomePage() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()
  const pendingUrl = `${PATHS.txs}?category=확인 필요`
  const { data, error } = useApi('/api/home/')  // 장고에게 홈 데이터 받아오기

  // 아직 못 받았으면 "불러오는 중…" (실패하면 안내)
  if (!data) return <Layout title="머니쿠션" activeTab="home"><ApiStatus error={error} /></Layout>
  const { period: PERIOD, home: h, mustPay: MUST_PAY } = data
  const pace = pacePct(PERIOD.label, PERIOD.dday)
  const note = paceNote(h.livingPct, pace)

  return (
    <Layout title="머니쿠션" activeTab="home">
      {/* 1. 급여 주기 · 기준 시각 한 줄 + 월급날 칩 */}
      <div className="mc-row mc-home2-period">
        <span className="mc-muted">{PERIOD.label} 급여 주기 · {PERIOD.updated}</span>
        <span className="mc-badge">월급날 D−{PERIOD.dday}</span>
      </div>

      {/* 2. 할 일: 확인할 거래 (있을 때만) */}
      {h.pendingCount > 0 && (
        <button type="button" className="mc-home2-todo" onClick={() => navigate(pendingUrl)}>
          <span className="mc-home2-todo-dot">{h.pendingCount}</span>
          <span className="mc-grow">카테고리를 확인할 거래 <strong>{h.pendingCount}건</strong></span>
          <span aria-hidden="true">›</span>
        </button>
      )}

      {/* 3. 오늘 쓸 수 있는 돈 (가장 중요한 숫자) + 사용 속도 + 여윳돈 */}
      <div className="mc-hero mc-budget-hero mc-home2-hero">
        <div className="mc-row">
          <span className="mc-hero-title">오늘 쓸 수 있는 돈</span>
          <button type="button" className="mc-link" onClick={() => openPopup(<CalcPopup />)}>계산 근거</button>
        </div>
        <div className="mc-budget-amount">
          <strong>{comma(h.daily)}<small>원</small></strong>
        </div>
        <p className="mc-home2-left">남은 생활비 <strong>{won(h.livingLeft)}</strong> · {PERIOD.dday}일 남음</p>
        <div className="mc-bar mc-home2-bar" role="progressbar" aria-label="이번 주기 생활비 사용률"
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={h.livingPct}
          aria-valuetext={`${h.livingPct}% 사용${note ? `, ${note.text}` : ''}`}>
          <span style={{ width: `${Math.min(h.livingPct, 100)}%` }}></span>
          {pace != null && <i className="mc-home2-pace" style={{ left: `${pace}%` }}></i>}
        </div>
        <div className="mc-row mc-home2-usage">
          <button type="button" className="mc-link mc-home2-used" onClick={() => navigate(PATHS.txs)}>
            {won(h.livingBudget)} 중 {h.livingPct}% 사용 ›
          </button>
          {note && <span className={`mc-home2-note is-${note.tone}`}>{note.text}</span>}
        </div>
        {/* 여윳돈 = 아직 어느 예산에도 안 나눈 돈 */}
        <div className="mc-row mc-home2-foot">
          <span className="mc-home2-spare">여윳돈 <strong>{won(h.spare)}</strong></span>
          <button type="button" className="mc-link" onClick={() => navigate(PATHS.assistant)}>예산 조정 ›</button>
        </div>
      </div>

      {/* 4. 입출금 계좌 잔액 (한 줄) */}
      <button type="button" className="mc-card mc-home2-bal" onClick={() => navigate(PATHS.connections)}>
        <span className="mc-muted">입출금 계좌 {h.accountCount}개</span>
        <strong>{won(h.balanceTotal)} <span className="mc-muted">›</span></strong>
      </button>

      <div className="mc-row mc-section"><h2>꼭 나갈 돈</h2></div>
      <div className="mc-card">
        {/* 목록.map(...) = 하나씩 꺼내서 그리기. key 는 항목마다 다른 값 */}
        {MUST_PAY.map((p) => {
          const badge = payBadge(p)
          return (
            <button
              type="button"
              className="mc-listrow"
              key={p.name}
              onClick={() => (p.link ? navigate(p.link) : openPopup(<CardBillPopup />))  /* 링크가 없으면(카드 청구서) 팝업 */}
            >
              <span className="mc-tile"><Icon name={p.icon} /></span>
              <span className="mc-grow">
                <strong>{p.name}</strong>
                <span className="mc-muted">{p.sub}</span>
                {badge && <span className={`mc-paystate is-${badge.tone}`}>{badge.text}{badge.tone === 'review' ? ' ›' : ''}</span>}
              </span>
              <span className="mc-right">{won(p.amount)} <span className="mc-muted">›</span></span>
            </button>
          )
        })}
      </div>

      <div className="mc-home-action">
        <button type="button" className="mc-action" onClick={() => navigate(PATHS.assistant)}>
          <Icon name="MessageCircle" /> 챗봇에게 내 소비 물어보기
        </button>
      </div>
    </Layout>
  )
}

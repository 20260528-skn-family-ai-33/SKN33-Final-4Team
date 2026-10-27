// 홈 (2026-10-20 배치 개선안)
// 위에서부터: 급여 주기 줄 → 확인할 거래 알림 → 오늘 쓸 수 있는 돈(파란 카드, 여윳돈 포함) → 계좌 잔액 → 꼭 나갈 돈
// 머니 쿠션은 쿠션 탭에서 따로 본다
// 되돌리기: 이 파일을 git 의 이전 버전으로 돌리고, app.css 맨 아래 "홈 개선안" 블록을 지우면 원래 화면으로 돌아간다
import { Navigate, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import { comma, won } from '../utils/money.js'
import { useApi } from '../utils/api.js'
import ApiStatus from '../components/ApiStatus.jsx'
import { PATHS } from '../routes.js'
import { usePopup } from '../popups/context.js'
import CalcPopup from '../popups/CalcPopup.jsx'

// '2026-10-24' → '10.24'
const mmdd = (iso) => (iso ? `${iso.slice(5, 7)}.${iso.slice(8, 10)}` : '')

// 기준 시각 '2026-10-21T09:30:00+09:00' → '10.21 09:30 기준' (없으면 빈 문자)
const asOfText = (iso) => (iso ? `${mmdd(iso)} ${iso.slice(11, 16)} 기준` : '')

// 급여 주기 전체 일수: 시작일 ~ 마지막 날 (못 읽으면 null)
function cycleDays(period) {
  if (!period) return null
  const start = new Date(period.startDate)
  const end = new Date(period.endDate)
  return Math.round((end - start) / 86_400_000) + 1
}

// 기준선(%): 오늘까지 이만큼 썼으면 딱 맞는 지점 = 지난 날 수 ÷ 전체 날 수
function pacePct(period) {
  const total = cycleDays(period)
  const dday = period?.dday
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

// 꼭 나갈 돈 종류별 아이콘 (home.md mustPay[].type)
const PAY_ICON = { recurring: 'repeat', card_bill: 'credit-card', loan: 'landmark' }

// 카드 청구서 금액이 아직 확정 전이면 "예상" 표시 (10/22 결정기록 Z, home.md mustPay[].isEstimated)
function payBadges(p) {
  const out = []
  if (p.isEstimated) out.push({ tone: 'est', text: '예상' })
  const state = payBadge(p)
  if (state) out.push(state)
  return out
}

export default function HomePage() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()
  const pendingUrl = `${PATHS.txs}?category=확인 필요`
  const { data, error } = useApi('/api/home/')  // 장고에게 홈 데이터 받아오기

  // 아직 못 받았으면 "불러오는 중…" (실패하면 안내)
  // 로그인이 풀렸으면 로그인 화면으로
  if (error?.code === 'not_authenticated') return <Navigate to={PATHS.login} replace />
  if (!data) return <Layout title="머니쿠션" activeTab="home"><ApiStatus error={error} /></Layout>
  // 응답 모양: docs/api/home.md (budget 은 ML 후까지 항상 null)
  const { period, balance, pendingCount, needsPaydayCount, mustPay, budget } = data
  const b = budget ?? {}  // 예산 칸 이름은 예산 API 가 정해지면 맞춘다
  const toFixed = () => navigate(PATHS.fixed, { state: { from: 'home' } })  // 고정지출 관리 화면으로
  const pace = pacePct(period)
  const note = paceNote(b.livingPct, pace)
  // 꼭 나갈 돈을 누르면: 고정지출 → 관리 화면
  //   카드 청구서 · 대출 상세는 아직 실제 데이터와 연결 전이라 누를 수 없게 둔다 (연결되면 sourceId 로 열기)
  const canOpen = (p) => p.type === 'recurring'

  return (
    <Layout title="머니쿠션" activeTab="home">
      {/* 1. 급여 주기 · 기준 시각 한 줄 + 월급날 칩 (급여일을 아직 안 넣었으면 period 가 null) */}
      {period && (
        <div className="mc-row mc-home2-period">
          <span className="mc-muted">
            {mmdd(period.startDate)} — {mmdd(period.endDate)} 급여 주기{balance.asOf ? ` · ${asOfText(balance.asOf)}` : ''}
          </span>
          <span className="mc-badge">월급날 D−{period.dday}</span>
        </div>
      )}
      {/* 월급 상태 (H-01): 늦게 오면 '아직', 다음 달 월급이 미리 오면 안내 */}
      {period?.salaryState === 'waiting' && <p className="mc-muted">월급이 아직 안 들어왔어요</p>}
      {period?.nextSalaryEarlyOn && (
        <p className="mc-muted">
          월급이 {mmdd(period.nextSalaryEarlyOn)}에 미리 들어왔어요. {mmdd(period.nextPayday)}부터 예산에 넣을게요
        </p>
      )}

      {/* 2. 할 일: 확인할 거래 (있을 때만) */}
      {pendingCount > 0 && (
        <button type="button" className="mc-home2-todo" onClick={() => navigate(pendingUrl)}>
          <span className="mc-home2-todo-dot">{pendingCount}</span>
          <span className="mc-grow">카테고리를 확인할 거래 <strong>{pendingCount}건</strong></span>
          <span aria-hidden="true">›</span>
        </button>
      )}
      {/* 할 일: 출금일을 모르는 고정지출 (H-05). 출금일을 정해야 '꼭 나갈 돈'에 나오고 출금 확인을 해 준다 */}
      {needsPaydayCount > 0 && (
        <button type="button" className="mc-home2-todo" onClick={toFixed}>
          <span className="mc-home2-todo-dot">{needsPaydayCount}</span>
          <span className="mc-grow">출금일을 확인할 고정지출 <strong>{needsPaydayCount}건</strong></span>
          <span aria-hidden="true">›</span>
        </button>
      )}

      {/* 3. 예산이 아직 없으면 (budget: null, H-06) 파란 카드 자리에 안내 카드 */}
      {budget === null ? (
        <div className="mc-card mc-home2-nobudget">
          <span className="mc-tile"><Icon name="Wallet" /></span>
          <h2>예산을 만들면 여기에 오늘 쓸 수 있는 돈이 보여요</h2>
          <p className="mc-muted">월급과 고정지출을 확인하고, 지킬 돈을 정하면 카테고리별 예산을 추천해 드려요.</p>
          <button type="button" className="mc-primary" onClick={() => navigate(PATHS.salary)}>예산 만들기</button>
        </div>
      ) : (
      /* 3. 오늘 쓸 수 있는 돈 (가장 중요한 숫자) + 사용 속도 + 여윳돈 */
      <div className="mc-hero mc-budget-hero mc-home2-hero">
        <div className="mc-row">
          <span className="mc-hero-title">오늘 쓸 수 있는 돈</span>
          <button type="button" className="mc-link" onClick={() => openPopup(<CalcPopup />)}>계산 근거</button>
        </div>
        <div className="mc-budget-amount">
          <strong>{comma(b.daily)}<small>원</small></strong>
        </div>
        <p className="mc-home2-left">남은 생활비 <strong>{won(b.livingLeft)}</strong> · {period?.dday}일 남음</p>
        <div className="mc-bar mc-home2-bar" role="progressbar" aria-label="이번 주기 생활비 사용률"
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={b.livingPct}
          aria-valuetext={`${b.livingPct}% 사용${note ? `, ${note.text}` : ''}`}>
          <span style={{ width: `${Math.min(b.livingPct, 100)}%` }}></span>
          {pace != null && <i className="mc-home2-pace" style={{ left: `${pace}%` }}></i>}
        </div>
        <div className="mc-row mc-home2-usage">
          <button type="button" className="mc-link mc-home2-used" onClick={() => navigate(PATHS.txs)}>
            {won(b.livingBudget)} 중 {b.livingPct}% 사용 ›
          </button>
          {note && <span className={`mc-home2-note is-${note.tone}`}>{note.text}</span>}
        </div>
        {/* 여윳돈 = 아직 어느 예산에도 안 나눈 돈 */}
        <div className="mc-row mc-home2-foot">
          <span className="mc-home2-spare">여윳돈 <strong>{won(b.spare)}</strong></span>
          <button type="button" className="mc-link" onClick={() => navigate(PATHS.assistant)}>예산 조정 ›</button>
        </div>
      </div>
      )}

      {/* 4. 입출금 계좌 잔액 (한 줄). 잔액을 모르면 0원 대신 '—' (H-02) */}
      <button type="button" className="mc-card mc-home2-bal" onClick={() => navigate(PATHS.connections)}>
        <span className="mc-muted">
          입출금 계좌 {balance.accountCount}개
          {balance.reloginCount > 0 && ' · 다시 연결 필요'}
          {balance.unknownCount > 0 && ' · 잔액을 못 불러온 계좌가 있어요'}
        </span>
        <strong>{balance.total == null ? '—' : won(balance.total)} <span className="mc-muted">›</span></strong>
      </button>

      <div className="mc-row mc-section"><h2>꼭 나갈 돈</h2></div>
      <div className="mc-card">
        {/* 목록.map(...) = 하나씩 꺼내서 그리기. key 는 항목마다 다른 값 */}
        {mustPay.length === 0 && <p className="mc-muted">이번 주기에 아직 나갈 돈이 없어요</p>}
        {mustPay.map((p) => {
          const badges = payBadges(p)
          const Row = canOpen(p) ? 'button' : 'div'
          return (
            <Row {...(canOpen(p) ? { type: 'button', onClick: toFixed } : {})} className="mc-listrow" key={p.dueId}>
              <span className="mc-tile"><Icon name={PAY_ICON[p.type] ?? 'repeat'} /></span>
              <span className="mc-grow">
                <strong>{p.name}</strong>
                <span className="mc-muted">
                  {mmdd(p.dueDate)} · {p.paymentAccount?.displayName ?? '결제 계좌 모름'}
                  {!canOpen(p) && ' · 상세 화면 준비 중'}
                </span>
                {badges.length > 0 && (
                  <span className="mc-paybadges">
                    {badges.map((b) => <span key={b.tone} className={`mc-paystate is-${b.tone}`}>{b.text}{b.tone === 'review' ? ' ›' : ''}</span>)}
                  </span>
                )}
              </span>
              <span className="mc-right">{won(p.amount)} {canOpen(p) && <span className="mc-muted">›</span>}</span>
            </Row>
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

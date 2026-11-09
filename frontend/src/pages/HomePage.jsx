// 홈 (2026-10-20 배치 개선안)
// 위에서부터: 급여 주기 줄 → 하루에 쓸 수 있는 돈(파란 카드, 여윳돈 포함) → 계좌 잔액 → 예산 밖 지출(있을 때만) → 꼭 나갈 돈
// 알림(확인할 거래 · 출금일 · 월급 안내)은 오른쪽 위 종 아이콘 팝업 (11/3)
// 머니 쿠션은 쿠션 탭에서 따로 본다
// 오른쪽 위 [새로고침] = 연결 전부 다시 불러오기 (docs/api/finance-connect.md) → 끝나면 홈 데이터를 다시 받는다
// 되돌리기: 이 파일을 git 의 이전 버전으로 돌리고, app.css 맨 아래 "홈 개선안" 블록을 지우면 원래 화면으로 돌아간다
import { useState } from 'react'
import { Navigate, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import { comma, won } from '../utils/money.js'
import { useApi } from '../utils/api.js'
import { runFinanceSync, syncLines } from '../utils/financeSync.js'
import ApiStatus from '../components/ApiStatus.jsx'
import { PATHS } from '../routes.js'
import { usePopup } from '../popups/context.js'
import CalcPopup from '../popups/CalcPopup.jsx'
import MustPayPopup from '../popups/MustPayPopup.jsx'
import LoanPaymentPopup from '../popups/LoanPaymentPopup.jsx'
import CardBillPopup from '../popups/CardBillPopup.jsx'
import NoticePopup from '../popups/NoticePopup.jsx'
import SalaryCheckPopup from '../popups/SalaryCheckPopup.jsx'
import OutOfBudgetPopup from '../popups/OutOfBudgetPopup.jsx'
import { OldScopeNote } from '../components/OutOfBudgetLine.jsx'
import UnreflectedCardNote from '../components/UnreflectedCardNote.jsx'

// 홈 왼쪽 위 로고 (11/3): cushion = 서비스 파란색, -ing = 서비스 라임색
const LOGO = <span className="mc-logo">cushion<span className="mc-logo-ing">-ing</span></span>

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
  if (p.userChecked) return { tone: 'check', text: '사용자 확인' }   // 카드 청구서: 앞으로 낼 금액을 사용자가 알려 줌 (11/06)
  // 카드 청구서는 출금을 못 찾았다고 미납으로 보지 않는다 (다른 경로로 냈을 수 있음)
  if (p.paymentState === 'needs_review') return { tone: 'review', text: p.type === 'card_bill' ? '납부 확인 필요' : '미납 확인 필요' }
  const today = new Date().toLocaleDateString('sv-SE')   // 'YYYY-MM-DD' (내 컴퓨터 날짜 기준)
  if (p.paymentState === 'unpaid' && p.dueDate && p.dueDate < today) return { tone: 'check', text: '출금 확인 중' }
  return null
}

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
  const { data, error, reload } = useApi('/api/home/')  // 장고에게 홈 데이터 받아오기
  const [sync, setSync] = useState({ busy: false, lines: null, error: '' })

  const refresh = async () => {
    setSync({ busy: true, lines: null, error: '' })
    try {
      const result = await runFinanceSync()
      setSync({ busy: false, lines: syncLines(result), error: '' })
      reload()
    } catch (err) {
      setSync({ busy: false, lines: null, error: err.message })
    }
  }

  // 아직 못 받았으면 "불러오는 중…" (실패하면 안내)
  // 로그인이 풀렸으면 로그인 화면으로
  if (error?.code === 'not_authenticated') return <Navigate to={PATHS.login} replace />
  if (!data) return <Layout title={LOGO} activeTab="home"><ApiStatus error={error} onRetry={reload} /></Layout>
  // 응답 모양: docs/api/home.md (budget = 이번 주기 승인 예산, 없으면 null)
  const { period, balance, pendingCount, needsPaydayCount, mustPay, budget } = data
  const b = budget ?? {}
  const toFixed = () => navigate(PATHS.fixed, { state: { from: 'home' } })  // 고정지출 관리 화면으로
  const pace = pacePct(period)
  const note = paceNote(b.livingPct, pace)
  // 카드 청구서 sourceId는 billId, 대출 sourceId는 loanId다.
  const openPay = (p, badges) => {
    const onManage = p.type === 'recurring' ? toFixed
      : p.type === 'card_bill' && p.sourceId ? () => openPopup(<CardBillPopup key={p.sourceId} billId={p.sourceId} onSaved={reload} />)
      : p.type === 'loan' && p.sourceId ? () => navigate(PATHS.loanCheck, { state: { from: 'home', loanId: p.sourceId } })
      : undefined
    const onCheckPayment = p.type === 'loan' && p.dueId ? () => openPopup(<LoanPaymentPopup key={p.dueId} dueId={p.dueId} onSaved={reload} />) : undefined
    openPopup(<MustPayPopup item={p} date={mmdd(p.dueDate)} badges={badges} onManage={onManage} onCheckPayment={onCheckPayment} />)
  }

  // 2. 알림 (11/3: 메인 카드 위 → 오른쪽 위 종 아이콘 팝업). 종에는 알림 개수
  //   월급 상태 (H-01): 늦게 오면 '아직', 다음 달 월급이 미리 오면 안내
  //   할 일: 확인할 거래 · 출금일을 모르는 고정지출 (H-05 — 출금일을 정해야 '꼭 나갈 돈'에 나오고 출금 확인을 해 준다)
  //   R12: 후보만 있으면 '확인 필요'(받음 · 아직으로 단정하지 않음), 자동 인식이면 맞는지 바꿀 수 있게
  const openSalary = () => openPopup(<SalaryCheckPopup onSaved={reload} />)
  const notices = [
    period?.salaryCheckCount > 0 && { key: 'salary-check', count: period.salaryCheckCount, text: '월급으로 보이는 입금이 있어요. 맞는지 확인해 주세요', go: openSalary },
    period?.salaryState === 'waiting' && { key: 'salary-wait', icon: 'Banknote', text: '월급이 아직 안 들어왔어요' },
    period?.salarySource === 'auto' && { key: 'salary-auto', icon: 'Banknote', text: `지난번 확인한 월급과 같아서 ${mmdd(period.salaryReceivedOn)} 입금을 월급으로 자동 인식했어요`, go: openSalary },
    period?.nextSalaryEarlyOn && { key: 'salary-early', icon: 'Banknote', text: `월급이 ${mmdd(period.nextSalaryEarlyOn)}에 미리 들어왔어요. ${mmdd(period.nextPayday)}부터 예산에 넣을게요` },
    pendingCount > 0 && { key: 'pending', count: pendingCount, text: `카테고리를 확인할 거래 ${pendingCount}건`, go: () => navigate(pendingUrl) },
    needsPaydayCount > 0 && { key: 'payday', count: needsPaydayCount, text: `출금일을 확인할 고정지출 ${needsPaydayCount}건`, go: toFixed },
  ].filter(Boolean)
  const headAction = (
    <>
      <button type="button" className="mc-icon" onClick={refresh} disabled={sync.busy} aria-busy={sync.busy} aria-label={sync.busy ? '새로고침 중' : '새로고침'}>
        <span className={sync.busy ? 'mc-conn-busy' : undefined}><Icon name="RefreshCw" /></span>
      </button>
      <button type="button" className="mc-icon mc-bell" onClick={() => openPopup(<NoticePopup items={notices} />)}
        aria-label={notices.length ? `알림 ${notices.length}개` : '알림'}>
        <Icon name="Bell" />
        {notices.length > 0 && <span className="mc-bell-n" aria-hidden="true">{notices.length}</span>}
      </button>
    </>
  )

  return (
    <Layout title={LOGO} activeTab="home" headAction={headAction}>
      {/* 새로고침 결과: 기관별로 실패할 수 있어서 한 줄씩. 다시 연결 · 카드 값 입력은 연결 관리에서 한다 */}
      {sync.error && <p className="mc-error" role="alert">{sync.error}</p>}
      {sync.lines?.map((l) => <p key={l.name} className={l.ok ? 'mc-note' : 'mc-error'} role="status">{l.name} · {l.text}</p>)}
      {sync.lines?.some((l) => !l.ok) && <button type="button" className="mc-link" onClick={() => navigate(PATHS.connections)}>연결 관리에서 확인하기 ›</button>}

      {/* 1. 급여 주기 + 월급날 칩 / 월급 상태 + 기준 시각 — 한 줄에 넷을 넣으면 폰 폭에서 줄바꿈이 깨져 두 줄로 (급여일을 아직 안 넣었으면 period 가 null) */}
      {period && (
        <div className="mc-home2-period">
          <div className="mc-row">
            <span className="mc-muted">{mmdd(period.startDate)} — {mmdd(period.endDate)} 급여 주기</span>
            <span className="mc-badge">다음 월급날 D−{period.dday}</span>
          </div>
          <div className="mc-row">
            <button type="button" className="mc-link" onClick={openSalary}>
              {period.salaryState === 'received' ? '월급 확인됨'
                : period.salaryState === 'needs_check' ? '월급 확인이 필요해요' : '월급 확인'} ›
            </button>
            {balance.asOf && <span className="mc-muted mc-home2-asof">{asOfText(balance.asOf)}</span>}
          </div>
        </div>
      )}

      <UnreflectedCardNote notes={b.cardNotes} onRecalculate={() => navigate(PATHS.protect, { state: { from: 'my', refreshRecommendation: true } })} />

      {/* 3. 예산이 아직 없으면 (budget: null, H-06) 파란 카드 자리에 안내 카드 */}
      {budget === null ? (
        <div className="mc-card mc-home2-nobudget">
          <span className="mc-tile"><Icon name="Wallet" /></span>
          <h2>예산을 만들면 여기에 하루에 쓸 수 있는 돈이 보여요</h2>
          <p className="mc-muted">월급과 고정지출을 확인하고, 지킬 돈을 정하면 카테고리별 예산을 추천해 드려요.</p>
          <button type="button" className="mc-primary" onClick={() => navigate(PATHS.salary)}>예산 만들기</button>
        </div>
      ) : (
      /* 3. 하루에 쓸 수 있는 돈 (가장 중요한 숫자) + 사용 속도 + 여윳돈. 마지막 날은 daily = null → '—' */
      <div className="mc-hero mc-budget-hero mc-home2-hero">
        <div className="mc-row">
          <span className="mc-hero-title">하루에 쓸 수 있는 돈</span>
          <button type="button" className="mc-link" onClick={() => openPopup(<CalcPopup budget={b} />)}>계산 근거</button>
        </div>
        <div className="mc-budget-amount">
          {b.daily == null ? <strong>—</strong> : <strong>{comma(b.daily)}<small>원</small></strong>}
        </div>
        <p className="mc-home2-left">남은 생활비 <strong>{won(b.livingLeft)}</strong> · 내일부터 {b.daysLeft}일 기준</p>
        <OldScopeNote amount={b.outOfBudgetAllocated} className="mc-home2-left" />
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

      {/* 4. 입출금 계좌 잔액 (한 줄). 잔액을 모르면 0원 대신 '—' (H-02)
          + 예산 밖 지출 (주거·통신 · 여행·숙박, 있을 때만) — 계산에 안 넣는 참고 금액이라 파란 카드 밖, 같은 카드 안 둘째 줄. 누르면 카테고리별 팝업 (11/3) */}
      <div className="mc-card mc-home2-balcard">
        <button type="button" className="mc-home2-bal" onClick={() => navigate(PATHS.connections)}>
          <span className="mc-muted">
            입출금 계좌 {balance.accountCount}개
            {balance.reloginCount > 0 && ' · 다시 연결 필요'}
            {balance.unknownCount > 0 && ' · 잔액을 못 불러온 계좌가 있어요'}
          </span>
          <strong>{balance.total == null ? '—' : won(balance.total)} <span className="mc-muted">›</span></strong>
        </button>
        {b.outOfBudget?.total > 0 && (
          <button type="button" className="mc-home2-bal" onClick={() => openPopup(<OutOfBudgetPopup value={b.outOfBudget} />)}>
            <span className="mc-muted">예산 밖 지출</span>
            <strong>{won(b.outOfBudget.total)} <span className="mc-muted">›</span></strong>
          </button>
        )}
      </div>
      <div className="mc-row mc-section"><h2>꼭 나갈 돈</h2></div>
      <div className="mc-card">
        {/* 목록.map(...) = 하나씩 꺼내서 그리기. key 는 항목마다 다른 값 */}
        {mustPay.length === 0 && <p className="mc-muted" style={{ textAlign: 'center' }}>이번 주기에 아직 나갈 돈이 없어요</p>}
        {/* 한 줄 = 이름 · 금액만. 날짜 · 계좌 · 상태는 누르면 팝업. 미납 확인 필요만 놓치지 않게 빨간 점 */}
        {mustPay.map((p) => {
          const badges = payBadges(p)
          const review = badges.some((b) => b.tone === 'review')
          return (
            <button type="button" onClick={() => openPay(p, badges)} className="mc-listrow" key={p.dueId}>
              <span className="mc-tile mc-tile-date">
                <small>{Number(p.dueDate.slice(5, 7))}월</small><b>{Number(p.dueDate.slice(8, 10))}</b>
              </span>
              <span className="mc-grow">
                <strong>{p.name}{review && <span className="mc-paydot" role="img" aria-label={p.type === 'card_bill' ? '납부 확인 필요' : '미납 확인 필요'}></span>}</strong>
              </span>
              <span className="mc-right">{won(p.amount)} <span className="mc-muted">›</span></span>
            </button>
          )
        })}
        {/* 이번 주기에 안 나가는 고정지출 · 출금일 모르는 것까지 전부 보는 곳 (목록이 비어도 보임) */}
        {/* 파란 버튼 · 카드 너비 90% · 가운데 (좌우 margin auto) */}
        <button type="button" className="mc-primary" style={{ width: '90%', margin: '16px auto 0', padding: '10px 0' }} onClick={toFixed}>고정지출 전체 보기</button>
      </div>

      <div className="mc-home-action">
        <button type="button" className="mc-action" onClick={() => navigate(PATHS.assistant)}>
          <Icon name="MessageCircle" /> 챗봇에게 내 소비 물어보기
        </button>
      </div>
    </Layout>
  )
}

// 홈 (11/17 재설계, 시안 B) — 홈이 답하는 질문 셋: 이번 주 얼마 쓸 수 있나 · 어떻게 쓰고 있나 · 곧 할 일
// 위에서부터 세 층, 아래로 갈수록 가볍다
//   첫 카드: 이번 주 쓸 수 있는 돈(가장 큰 숫자) + 사용률 원 그래프 · 이달 남은 생활비 막대 · 주기 끝 전망 한 줄
//   (11/17 피드백: 파란 배경이 너무 많아 흰 카드 + 진한 글자로. 파란색은 원 그래프 · 막대 · 링크에만)
//   많이 쓴 곳: 카테고리 아이콘 타일 3개 · 지난달 이맘때 대비 · 가장 큰 지출
//   할 일 목록: 곧 나갈 돈(7일) · 납부 확인 · 확인할 거래 · 입출금 계좌
// 하루 금액 · 여윳돈 · 예산 밖 지출은 홈에서 뺐다 (예산 화면에 있음, 서버 값 daily · spare 는 채팅이 쓴다)
// 알림(월급 · 예산 초과 · 확인할 거래 · 출금일)은 오른쪽 위 종 아이콘 팝업 (11/3) — 본문에 올린 항목도 종에서 빼지 않는다
// 오른쪽 위 [새로고침] = 연결 전부 다시 불러오기 (docs/api/finance-connect.md) → 끝나면 홈 데이터를 다시 받는다
// 응답 모양: docs/api/home.md
import { useCallback, useEffect, useRef, useState } from 'react'
import { Navigate, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import { comma, won } from '../utils/money.js'
import { useApi } from '../utils/api.js'
import { needsCardValues, runFinanceSync, syncLines } from '../utils/financeSync.js'
import ApiStatus from '../components/ApiStatus.jsx'
import { PATHS } from '../routes.js'
import { catIcon } from '../utils/categories.js'
import { usePopup } from '../popups/context.js'
import MustPayPopup from '../popups/MustPayPopup.jsx'
import LoanPaymentPopup from '../popups/LoanPaymentPopup.jsx'
import CardBillPopup from '../popups/CardBillPopup.jsx'
import NoticePopup from '../popups/NoticePopup.jsx'
import SalaryCheckPopup from '../popups/SalaryCheckPopup.jsx'
import SalaryGapPopup from '../popups/SalaryGapPopup.jsx'
import CardCredentialPopup from '../popups/CardCredentialPopup.jsx'
import UnreflectedCardNote from '../components/UnreflectedCardNote.jsx'
import SyncResultPopup from '../popups/SyncResultPopup.jsx'
import { toast } from '../utils/toast.js'
import GuideTour from '../components/GuideTour.jsx'
import TabHero from '../components/TabHero.jsx'
import { markGuideTourSeen, needsGuideTour, takeGuideReplay } from '../utils/guideTour.js'

// 홈 왼쪽 위 로고 (11/3): cushion = 서비스 파란색, -ing = 서비스 라임색 (홈에서는 머리 색에 맞춰 app.css 가 바꾼다)
const LOGO = <span className="mc-logo">cushion<span className="mc-logo-ing">-ing</span></span>

// '2026-10-24' → '10.24'
const mmdd = (iso) => (iso ? `${iso.slice(5, 7)}.${iso.slice(8, 10)}` : '')

function savedNoticeIds(key) {
  if (!key) return []
  try {
    const ids = JSON.parse(window.localStorage.getItem(key))
    return Array.isArray(ids) ? ids : []
  } catch {
    return []
  }
}

const DAY_MS = 86_400_000
const lunarDay = new Intl.DateTimeFormat('en-u-ca-dangi', { timeZone: 'Asia/Seoul', month: 'numeric', day: 'numeric' })

function upcomingSeason({ nextPayday, dday }) {
  // 급여일과 남은 일수에서 홈 API 기준의 서울 날짜를 복원한다.
  const today = Date.parse(nextPayday) - dday * DAY_MS
  if (new Date(today).toISOString().slice(5, 10) >= '12-18') return '연말'
  if (lunarDay.resolvedOptions().calendar !== 'dangi') return null
  for (let offset = 1; offset <= 14; offset += 1) {
    const date = new Date(today + offset * DAY_MS)
    const parts = lunarDay.formatToParts(date)
    const month = parts.find((part) => part.type === 'month')?.value
    const day = parts.find((part) => part.type === 'day')?.value
    if (month === '1' && day === '1') return '설날'
    if (month === '8' && day === '15') return '추석'
  }
  return null
}


// 화면 읽기용 금액: 215000 → '21만 5,000원'
function spokenWon(n) {
  const man = Math.floor(n / 10000)
  const rest = n % 10000
  if (!man) return won(rest)
  return rest ? `${man}만 ${comma(rest)}원` : `${man}만원`
}

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
  if (diff > 2) return `${diff}%p 빠름`
  if (diff < -2) return `${-diff}%p 여유`
  return '적정 속도'
}

// 이번 주 몫 중 쓴 비율. 몫이 0원이면 쓴 돈이 있을 때 100
const weekPct = (week) => (week.budget ? Math.round((week.spent / week.budget) * 100) : (week.spent ? 100 : 0))

// 지난달 이맘때 대비 한 줄 (vsPrev.diff = 이번 − 지난)
function prevText(diff) {
  if (diff > 0) return <>지난달 이맘때보다 <b>{won(diff)} 더</b> 썼어요</>
  if (diff < 0) return <>지난달 이맘때보다 <b>{won(-diff)} 덜</b> 썼어요</>
  return <>지난달 이맘때와 <b>같게</b> 썼어요</>
}

// 꼭 나갈 돈 상태 배지 (10/22 PR #26 리뷰, docs/api/home.md '미납 안내') — 팝업에 넘긴다
//   needs_review = 출금일 +3일이 지나도 출금을 못 찾음 / unpaid 인데 출금일이 지남 = 자동 연결(±3일) 기다리는 중
function payBadge(p) {
  if (p.userChecked) return { tone: 'check', text: '사용자 확인' }   // 카드 청구서: 앞으로 낼 금액을 사용자가 알려 줌 (11/06)
  if (p.paymentState === 'needs_review') return { tone: 'review', text: p.type === 'card_bill' ? '납부 확인 필요' : '미납 확인 필요' }
  const today = new Date().toLocaleDateString('sv-SE')   // 'YYYY-MM-DD' (내 컴퓨터 날짜 기준)
  if (p.paymentState === 'unpaid' && p.dueDate && p.dueDate < today) return { tone: 'check', text: '출금 확인 중' }
  return null
}

function payBadges(p) {
  const out = []
  if (p.isEstimated) out.push({ tone: 'est', text: '예상' })
  const state = payBadge(p)
  if (state) out.push(state)
  return out
}

// 이번 주 몫 사용률 원 그래프. 가운데(children)에 사용률 (100%를 넘으면 원은 가득 차고 숫자는 그대로)
function WeekRing({ pct, children }) {
  const len = 2 * Math.PI * 92
  return (
    <div className="mc-home3-ring">
      <svg viewBox="0 0 200 200" role="progressbar" aria-label="이번 주 몫 사용률"
        aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(pct, 100)} aria-valuetext={`${pct}% 사용`}>
        <circle cx="100" cy="100" r="92" className="t" />
        {pct > 0 && <circle cx="100" cy="100" r="92" className="v" strokeDasharray={`${(len * Math.min(pct, 100)) / 100} ${len}`} />}
      </svg>
      <div className="mc-home3-ringin">{children}</div>
    </div>
  )
}

// 막대 하나 (0~100%로 자름). tick = 세로 눈금 위치(%)
function Bar({ pct, label, tick, text }) {
  return (
    <div className="mc-home3-bar" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100}
      aria-valuenow={Math.min(pct, 100)} aria-valuetext={text}>
      <span style={{ width: `${Math.min(pct, 100)}%` }}></span>
      {tick != null && <i style={{ left: `${tick}%` }}></i>}
    </div>
  )
}

export default function HomePage() {
  const navigate = useNavigate()
  const { popup, openPopup } = usePopup()
  const pendingUrl = `${PATHS.txs}?category=확인 필요`
  const { data, error, reload } = useApi('/api/home/')  // 장고에게 홈 데이터 받아오기
  const [sync, setSync] = useState({ busy: false })
  // 쿠숑이 가이드 투어 (11/16): 첫 예산 승인 뒤 홈에 처음 왔거나, 마이메뉴에서 [다시 보기]를 눌렀을 때 (utils/guideTour.js)
  const me = useApi('/api/auth/me/')
  const readKey = me.data?.userId ? `moneycushion.homeNotices.${me.data.userId}` : null
  const [readNotices, setReadNotices] = useState(() => ({ key: readKey, ids: savedNoticeIds(readKey) }))
  if (readNotices.key !== readKey) {
    const saved = savedNoticeIds(readKey)
    setReadNotices({ key: readKey, ids: readNotices.key === null ? [...saved, ...readNotices.ids] : saved })
  }
  useEffect(() => {
    if (!readNotices.key) return
    try { window.localStorage.setItem(readNotices.key, JSON.stringify(readNotices.ids)) } catch { /* 화면에서는 읽은 상태를 유지 */ }
  }, [readNotices])
  const [tour, setTour] = useState(false)
  const homeReady = Boolean(data)
  useEffect(() => {
    if (homeReady && me.data && (takeGuideReplay() || needsGuideTour(me.data))) setTour(true)
  }, [homeReady, me.data])
  const closeTour = useCallback(() => {
    setTour(false)
    markGuideTourSeen(me.data)
  }, [me.data])

  // 11/9: 카드번호 · 비밀번호가 필요한 카드사(현대카드 등)는 실패 줄 대신 입력 팝업을 띄워 그 자리에서 다시 불러온다
  //   여러 곳이면 하나씩 차례로. 팝업을 닫으면 그 카드사는 이번에 안 불러온 채로 둔다
  // 새로고침 결과 (11/12): 불러온 기관은 토스트로 잠깐, 실패한 기관은 모아서 가운데 창으로 (홈 위에 줄로 남기지 않는다)
  const showFailed = (failed) => {
    if (failed.length) openPopup(<SyncResultPopup lines={failed} onManage={() => navigate(PATHS.connections)} />)
  }
  // 성공 줄은 토스트로 보여 주고, 실패 줄은 돌려준다 (줄이 많으면 조금 더 오래)
  const report = (result) => {
    const lines = syncLines(result)
    const done = lines.filter((l) => l.ok)
    if (done.length) toast(done.map((l) => `${l.name} · ${l.text}`).join('\n'), 2000 + (done.length - 1) * 800)
    return lines.filter((l) => !l.ok)
  }
  // 카드번호가 필요한 카드사는 하나씩 입력 팝업 → 다 끝나면 실패한 곳을 한 번에 (팝업은 한 번에 하나라서)
  // 입력 팝업을 그냥 닫으면(× · 바깥 · Esc) 남은 카드사는 묻지 않고, 모아 둔 실패만 보여 준다 (아래 효과)
  const waitingFailed = useRef(null)
  const askCard = (rows, failed) => {
    if (!rows.length) return showFailed(failed)
    const [item, ...rest] = rows
    waitingFailed.current = failed
    openPopup(<CardCredentialPopup item={item} onDone={(result) => {
      waitingFailed.current = null
      const more = report(result)
      reload()
      askCard(rest, [...failed, ...more])
    }} />)
  }
  useEffect(() => {
    if (popup || !waitingFailed.current) return
    const failed = waitingFailed.current
    waitingFailed.current = null
    showFailed(failed)
  }, [popup]) // eslint-disable-line react-hooks/exhaustive-deps -- 팝업이 닫힐 때만
  const refresh = async () => {
    setSync({ busy: true })
    try {
      const result = await runFinanceSync()
      const needCard = (result.items ?? []).filter(needsCardValues)
      const failed = report({ items: (result.items ?? []).filter((it) => !needsCardValues(it)) })
      setSync({ busy: false })
      reload()
      askCard(needCard, failed)
    } catch (err) {
      setSync({ busy: false })
      openPopup(<SyncResultPopup error={err.message} />)
    }
  }

  // 아직 못 받았으면 "불러오는 중…" (실패하면 안내). 로그인이 풀렸으면 로그인 화면으로
  if (error?.code === 'not_authenticated') return <Navigate to={PATHS.login} replace />
  if (!data) return <Layout title={LOGO} activeTab="home"><ApiStatus error={error} onRetry={reload} /></Layout>
  const { period, balance, cushionNotices, pendingCount, needsPaydayCount, mustPay, upcoming, budget } = data
  const b = budget ?? {}
  const toFixed = () => navigate(PATHS.fixed, { state: { from: 'home' } })  // 고정지출 관리 화면으로
  // 카드 청구서 sourceId는 billId, 대출 sourceId는 loanId다.
  const openPay = (p) => {
    const onManage = p.type === 'recurring' ? toFixed
      : p.type === 'card_bill' && p.sourceId ? () => openPopup(<CardBillPopup key={p.sourceId} billId={p.sourceId} onSaved={reload} />)
      : p.type === 'loan' && p.sourceId ? () => navigate(PATHS.loanCheck, { state: { from: 'home', loanId: p.sourceId } })
      : undefined
    const onCheckPayment = p.type === 'loan' && p.dueId ? () => openPopup(<LoanPaymentPopup key={p.dueId} dueId={p.dueId} onSaved={reload} />) : undefined
    openPopup(<MustPayPopup item={p} date={mmdd(p.dueDate)} badges={payBadges(p)} onManage={onManage} onCheckPayment={onCheckPayment} />)
  }

  // 알림 (11/3: 오른쪽 위 종 아이콘 팝업). 종에는 안 읽은 알림 개수
  //   월급 상태 (H-01 · R12) · 예산 초과 (11/11) · 자주 바꾸기 (11/13) · 확인할 거래 · 출금일 모르는 고정지출 (H-05)
  const biggest = (b.bigSpendings ?? []).reduce((max, s) => (!max || s.amount > max.amount ? s : max), null)
  const season = b.budgetId && period && upcomingSeason(period)
  const toCushion = () => navigate(PATHS.fund)
  const toAdjust = () => navigate(PATHS.assistant, {
    state: { budgetContext: { baseBudgetId: b.budgetId, mode: 'NORMAL_ADJUSTMENT' } },
  })
  const openSalary = () => openPopup(<SalaryCheckPopup onSaved={reload} />)
  // 11/13: 받은 월급이 등록한 실수령액과 꽤 다르면 (1만원 이상 · 3% 이상, 서버가 판단) 어떻게 할지 묻는다
  const gap = period?.salaryGap
  const gapText = gap && `월급이 ${won(Math.abs(gap.diff))} ${gap.diff < 0 ? '덜' : '더'} 들어왔어요`
  const openGap = () => openPopup(<SalaryGapPopup gap={gap} onDone={reload} />)
  const notices = [
    // 11/18 시안 N3: group 'act' = 지금 해 볼 일(act 글자 + tone 색) · 'check' = 확인해 주세요 · 갈 곳이 없으면 안내 (popups/NoticePopup.jsx)
    gap && { key: 'salary-gap', title: '월급 차이', icon: 'Banknote', text: `${gapText}. 예산을 조정할까요?`, go: openGap, group: 'act', act: '예산 조정하기', tone: 'amber' },
    b.budgetId && b.forecast && biggest && { key: 'big-spending', occurrence: biggest.transactionId,
      title: `${biggest.merchantName || biggest.categoryName || '생활비 결제'} · ${won(biggest.amount)}`, icon: 'Banknote',
      text: '생활비 예산을 넘길 수 있어요. 지금 조정할까요?', go: toAdjust, group: 'act', act: '쿠숑이와 조정하기', tone: 'amber' },
    cushionNotices?.depositDue && { key: 'cushion-deposit', title: '머니쿠션 적립', icon: 'PiggyBank',
      text: '쿠션 모으기 잊지 마세요!', go: toCushion, group: 'act', act: '쿠션 보기', tone: 'navy' },
    cushionNotices?.balanceLow && { key: 'cushion-balance', title: '머니쿠션 확인',
      text: '연결된 통장 잔액이 머니쿠션 추정액보다 적어요. 머니쿠션을 잘 모으고 있는지 확인해볼까요?', go: toCushion, group: 'check', icon: 'CircleAlert', tone: 'red' },
    season && { key: 'season-budget', occurrence: season, title: season, icon: 'Sparkles',
      text: `${season}이 다가오고 있어요. 지출에 대비해 같이 예산을 조정해볼까요?`, go: toAdjust, group: 'act', act: '예산 조정하기', tone: 'lime' },
    period?.salaryCheckCount > 0 && { key: 'salary-check', title: '월급 확인', count: period.salaryCheckCount, text: '월급으로 보이는 입금이 있어요. 맞는지 확인해 주세요', go: openSalary },
    period?.salaryState === 'waiting' && { key: 'salary-wait', title: '월급 입금', icon: 'Banknote', text: '월급이 아직 안 들어왔어요' },
    period?.salarySource === 'auto' && { key: 'salary-auto', title: '월급 자동 인식', icon: 'Banknote', text: `지난번 확인한 월급과 같아서 ${mmdd(period.salaryReceivedOn)} 입금을 월급으로 자동 인식했어요`, go: openSalary },
    period?.nextSalaryEarlyOn && { key: 'salary-early', title: '월급 입금', icon: 'Banknote', text: `월급이 ${mmdd(period.nextSalaryEarlyOn)}에 미리 들어왔어요. ${mmdd(period.nextPayday)}부터 예산에 넣을게요` },
    ...(b.overCategories ?? []).map((c) => ({ key: `over-${c.category}`, title: '예산 초과', icon: catIcon(c.categoryName), text: `${c.categoryName} 예산을 ${won(c.over)} 넘었어요`, go: () => navigate(PATHS.budget) })),
    b.nag && { key: 'nag', title: '예산 변경', mark: '!', text: b.nag, go: () => navigate(PATHS.budget) },
    pendingCount > 0 && { key: 'pending', title: '카테고리 확인', count: pendingCount, text: `카테고리를 확인할 거래 ${pendingCount}건`, go: () => navigate(pendingUrl) },
    needsPaydayCount > 0 && { key: 'payday', title: '출금일 확인', count: needsPaydayCount, text: `출금일을 확인할 고정지출 ${needsPaydayCount}건`, go: toFixed },
  ].filter(Boolean)
  const currentNoticeIds = notices.map((notice) => JSON.stringify([period?.startDate, notice.key, notice.occurrence, notice.count]))
  const unreadCount = currentNoticeIds.filter((id) => !readNotices.ids.includes(id)).length
  const openNotices = () => {
    setReadNotices({ key: readKey, ids: currentNoticeIds })
    openPopup(<NoticePopup items={notices} />)
  }
  const headAction = (
    <>
      <button type="button" className="mc-icon" onClick={refresh} disabled={sync.busy} aria-busy={sync.busy} aria-label={sync.busy ? '새로고침 중' : '새로고침'}>
        <span className={sync.busy ? 'mc-conn-busy' : undefined}><Icon name="RefreshCw" /></span>
      </button>
      <button type="button" className="mc-icon mc-bell" onClick={openNotices}
        aria-label={unreadCount ? `알림 ${unreadCount}개` : '알림'}>
        <Icon name="Bell" />
        {unreadCount > 0 && <span className="mc-bell-n" aria-hidden="true">{unreadCount}</span>}
      </button>
    </>
  )

  const top = b.topCategories
  const week = b.week
  const weekLeft = Number.isFinite(week?.budget) && Number.isFinite(week?.spent) ? week.budget - week.spent : null
  const forecast = b.forecast
  const pace = pacePct(period)
  const note = budget ? paceNote(b.livingPct, pace) : null
  const reviews = mustPay.filter((p) => p.paymentState === 'needs_review')  // 출금일 +3일이 지나도 출금을 못 찾은 회차

  return (
    <Layout title={LOGO} activeTab="home" headAction={headAction} overlay={tour ? <GuideTour onClose={closeTour} /> : null}>
      {/* 11/18 시안 B: 파란 띠에 이번 주 쓸 수 있는 돈을 문장으로 + 쿠숑이. 급여 주기 · 월급 확인은 띠 맨 위 한 줄 */}
      <TabHero
        eyebrow={period && <>
          <span>월급날 D−{period.dday}</span>
          <button type="button" className="mc-tabhero-pill" onClick={openSalary}>
            {period.salaryState === 'received' ? '월급 확인됨' : period.salaryState === 'needs_check' ? '월급 확인 필요' : '월급 확인'} ›
          </button>
        </>}
        title={budget === null ? <>예산을 만들면<br />이번 주에 쓸 수 있는<br />돈이 보여요</> : weekLeft === null ? <>이번 주 쓸 수 있는 돈<br /><em>—</em></> : <>
          이번 주는<br />
          <em className="mc-home3-big" aria-label={`이번 주 쓸 수 있는 돈 ${spokenWon(Math.abs(weekLeft))}${weekLeft < 0 ? ' 넘게 씀' : ''}`}>
            {comma(Math.abs(weekLeft))}원
          </em>{weekLeft < 0 ? <><br />넘게 썼어요</> : <> 더<br />쓸 수 있어요</>}
        </>}
        mood={weekLeft === null ? 'basic' : weekLeft < 0 ? 'stretch' : 'home'}
      />
      {/* 흰 시트: 이번 주 몫 사용률 원 그래프 · 이달 남은 생활비 · 월급날 전망 · 예산 화면으로 */}
      <section className="mc-tabsheet mc-home3-hero" aria-label="이번 주 예산">
        {budget === null ? (
          /* 예산이 아직 없으면 (H-06) */
          <div className="mc-home3-empty">
            <p className="mc-home3-sub">월급과 고정지출을 확인하고 지킬 돈을 정하면 카테고리별 예산을 추천해 드려요.</p>
            <button type="button" className="mc-home3-cta" onClick={() => navigate(PATHS.salary)}>예산 만들기</button>
          </div>
        ) : (
          <>
            {weekLeft !== null && <div className="mc-home3-week">
              <WeekRing pct={weekPct(b.week)}><b>{weekPct(b.week)}%</b></WeekRing>
              <div>
                <strong>이번 주 예산 {weekPct(b.week)}% 썼어요</strong>
                {note && <span className={`mc-home3-chip${note.endsWith('빠름') ? ' is-fast' : ''}`}>생활비 {note}</span>}
              </div>
            </div>}
            {/* 같은 크기 두 칸: 이달 남은 생활비 | 주기 끝 전망 (주기 첫날은 forecast = null → 오른쪽 칸 없음) */}
            <div className="mc-home3-stats">
              <div>
                <span>이달 남은 생활비</span>
                <strong className="mc-home3-mid">{won(b.livingLeft)}</strong>
                {/* 세로 눈금 = 오늘까지 이만큼 썼으면 딱 맞는 지점 */}
                <Bar pct={b.livingPct} tick={pace} label="이번 주기 생활비 사용률" text={`${b.livingPct}% 사용${note ? `, ${note}` : ''}`} />
              </div>
              {forecast && (
                <div className={forecast.gap < 0 ? 'is-short' : undefined}>
                  <span>이대로 쓰면 월급날</span>
                  <strong className="mc-home3-mid">{won(Math.abs(forecast.gap))}<em>{forecast.gap < 0 ? ' 부족' : ' 남음'}</em></strong>
                  <Bar pct={b.livingBudget ? Math.round((forecast.total * 100) / b.livingBudget) : 100} label="예상 지출 비율"
                    text={`예상 지출 ${won(forecast.total)}`} />
                </div>
              )}
            </div>
            {/* 11/18: 홈에서 예산 탭으로 바로 */}
            <button type="button" className="mc-tabsheet-go" onClick={() => navigate(PATHS.budget)}>
              예산 자세히 보기<Icon name="ChevronRight" size={18} />
            </button>
          </>
        )}
      </section>

      {/* 11/13: 월급 차이 — 덜 들어오면 금액을 빨강, 더 들어오면 파랑 */}
      {gap && (
        <button type="button" className="mc-home3-gap" onClick={openGap}>
          <span className="mc-tile"><Icon name="Banknote" /></span>
          <span className="mc-grow">
            <strong>월급이 <b className={gap.diff < 0 ? 'is-less' : 'is-more'}>{won(Math.abs(gap.diff))}</b> {gap.diff < 0 ? '덜' : '더'} 들어왔어요</strong>
            <small>이번 예산을 맞출까요?</small>
          </span>
          <span className="mc-muted" aria-hidden="true">›</span>
        </button>
      )}

      <UnreflectedCardNote notes={b.cardNotes} readyIds={b.cardBillsReady} onRecalculate={() => navigate(PATHS.protect, { state: { from: 'my', refreshRecommendation: true } })} />

      {/* 2. 어떻게 쓰고 있나: 카테고리 아이콘 타일 3개 + 지난달 대비 · 가장 큰 지출 (예산이 있을 때만) */}
      {budget && (
        <section className="mc-home3-sheet" aria-labelledby="home3-spend">
          <div className="mc-home3-sheethead">
            <div>
              <h2 id="home3-spend" className="mc-home3-eyebrow">이번 주기 많이 쓴 곳</h2>
            </div>
            <button type="button" className="mc-link" onClick={() => navigate(PATHS.budget)}>예산 보기 ›</button>
          </div>
          {Array.isArray(top) && (top.length === 0 ? <p className="mc-muted">아직 이번 주기 생활비 지출이 없어요</p> : (
            <div className="mc-home3-tiles">
              {top.map((c) => {
                const pct = c.allocated ? Math.round((c.spent * 100) / c.allocated) : null
                return (
                  <div key={c.category} className={`mc-home3-tile${c.over ? ' is-over' : ''}`}>
                    <span className="mc-tile"><Icon name={catIcon(c.categoryName)} /></span>
                    <span className="mc-home3-tile-name">{c.categoryName}</span>
                    <b>{won(c.spent)}</b>
                    <div className="mc-home3-minibar" aria-hidden="true"><span style={{ width: `${Math.min(pct ?? 100, 100)}%` }}></span></div>
                    {/* 11/18: 비율은 막대로 보이니 글은 넘었을 때만 */}
                    {c.over && <small>{won(c.spent - c.allocated)} 초과</small>}
                  </div>
                )
              })}
            </div>
          ))}
          {(b.vsPrev || biggest) && (
            <div className="mc-home3-facts">
              {b.vsPrev && <p><Icon name="ChartNoAxesCombined" size={18} /><span>{prevText(b.vsPrev.diff)}</span></p>}
              {biggest && (
                <button type="button" onClick={() => navigate(PATHS.txs)}>
                  <Icon name="Banknote" size={18} />
                  <span>가장 큰 지출은 {mmdd(biggest.occurredOn)} {biggest.merchantName || (biggest.kind === 'transfer' ? '보낸 돈' : biggest.categoryName ?? '지출')} <b>{won(biggest.amount)}</b></span>
                  <span className="mc-muted" aria-hidden="true">›</span>
                </button>
              )}
            </div>
          )}
        </section>
      )}

      {/* 3. 할 일: 눌러서 다른 화면으로 가는 줄 모음 */}
      <h2 className="mc-tabsec">챙겨볼 것</h2>
      <ul className="mc-home3-todo">
        {upcoming?.items?.length > 0 && (
          <li>
            <details className="mc-home3-due">
              <summary>
                <Icon name="Wallet" />
                <span className="mc-home3-todo-name">곧 나갈 돈<small>7일 안 · {upcoming.items.length}건</small></span>
                <b>{won(upcoming.total)}</b><span className="mc-home3-chev" aria-hidden="true">›</span>
              </summary>
              {upcoming.items.map((p) => (
                <button type="button" key={p.dueId} className="mc-home3-dueitem" onClick={() => openPay(p)}>
                  <span>{mmdd(p.dueDate)}</span>
                  <span className="mc-grow">{p.name}{p.isEstimated && <small> 예상</small>}</span>
                  <b>{won(p.amount)}</b>
                </button>
              ))}
              <button type="button" className="mc-link mc-home3-dueall" onClick={toFixed}>고정지출 전체 보기 ›</button>
            </details>
            {upcoming.shortfall > 0 && <p className="mc-home3-warn" role="status">입출금 계좌 잔액보다 {won(upcoming.shortfall)} 많아요</p>}
          </li>
        )}
        {reviews.map((p) => (
          <li key={p.dueId}>
            <button type="button" onClick={() => openPay(p)}>
              <Icon name="CircleAlert" />
              <span className="mc-home3-todo-name">{p.name}<small>{p.type === 'card_bill' ? '납부 확인 필요' : '미납 확인 필요'}</small></span>
              <b>{won(p.amount)}</b><span className="mc-home3-chev" aria-hidden="true">›</span>
            </button>
          </li>
        ))}
        {pendingCount > 0 && (
          <li>
            <button type="button" onClick={() => navigate(pendingUrl)}>
              <Icon name="CircleQuestionMark" />
              <span className="mc-home3-todo-name">확인할 거래</span>
              <b>{pendingCount}건<i className="mc-home3-dot"></i></b><span className="mc-home3-chev" aria-hidden="true">›</span>
            </button>
          </li>
        )}
        <li>
          {balance.accountCount === 0 ? (
            <button type="button" onClick={() => navigate(PATHS.connect)}>
              <Icon name="Landmark" />
              <span className="mc-home3-todo-name">입출금 계좌<small>연결된 계좌가 없어요</small></span>
              <b>연결하기</b><span className="mc-home3-chev" aria-hidden="true">›</span>
            </button>
          ) : (
            <button type="button" onClick={() => navigate(PATHS.connections)}>
              <Icon name="Landmark" />
              <span className="mc-home3-todo-name">
                입출금 계좌<small>{balance.accountCount}개{balance.reloginCount > 0 ? ' · 다시 연결 필요' : balance.unknownCount > 0 ? ' · 일부 못 불러옴' : ''}</small>
              </span>
              <b>{balance.total == null ? '—' : won(balance.total)}</b><span className="mc-home3-chev" aria-hidden="true">›</span>
            </button>
          )}
        </li>
      </ul>
    </Layout>
  )
}

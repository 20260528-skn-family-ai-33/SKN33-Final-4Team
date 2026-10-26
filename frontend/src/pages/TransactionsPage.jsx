// 거래 내역 — GET /api/finance/transactions/ (docs/api/transactions.md '거래 목록 보기')
// 주소 ?category=FOOD 처럼 카테고리 코드로 골라 본다. 고르는 일 · 합계는 장고 API 가 해서 보내 준다
// '전체'에는 소비 + 월급 입금 + 남에게 보낸 이체가 보인다 (10/22 결정 V①). 입금 · 이체는 카테고리가 없고 합계에 안 들어간다
//   이체를 누르면 [소비로 분류하기] (결정 W) · 고정지출로 연결된 거래(funding: fixed)는 '고정' 표시 (★8 · AA⑤)
// 기간은 달력월(이번 달 1일 ~ 오늘). '확인 필요'는 기간 없이 전체 (H-03 — 홈 숫자와 같게)
// 날짜별 묶음 · 날짜 합계는 화면에서 만든다 (API 는 평평한 목록)
import { useSearchParams } from 'react-router'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import { won } from '../utils/money.js'
import { useApi } from '../utils/api.js'
import { SPEND_CATS, ETC, PENDING, PICK_CATS, catIcon } from '../utils/categories.js'
import ApiStatus from '../components/ApiStatus.jsx'
import { usePopup } from '../popups/context.js'
import TransactionPopup from '../popups/TransactionPopup.jsx'
import TxPickPopup from '../popups/TxPickPopup.jsx'
import FilterPopup from '../popups/FilterPopup.jsx'

const CHIPS = [...SPEND_CATS, ETC]  // 카테고리 칩 (확인 필요는 따로)
// 다른 화면(홈 · 예산 · 소비 분석 카드)이 아직 ?category=확인 필요 처럼 이름으로 보내서, 이름이 오면 코드로 바꿔 읽는다
const CODE_BY_NAME = Object.fromEntries([...CHIPS, PENDING].map((c) => [c.name, c.code]))
const KIND = { spending: 'normal', income: 'income', transfer: 'transfer' }

// API 한 줄 → 화면 · 팝업이 쓰는 모양
function toView(item) {
  const kind = KIND[item.transactionType] ?? 'normal'
  return {
    id: item.transactionId,
    kind: item.classifyState === 'needs_review' ? 'pending' : kind,
    icon: kind === 'income' ? 'Banknote' : kind === 'transfer' ? 'Send' : catIcon(item.categoryName),
    name: item.merchantName,
    cat: item.categoryName,
    amount: item.amount,
    net: item.netAmount,
    cancel: item.cancel.state === 'none' ? null : item.cancel.state,  // null · 'partial' · 'full'
    cancelAmount: item.cancel.amount,
    via: (item.paymentCard ?? item.paymentAccount)?.displayName,
    funding: item.funding,
  }
}

// '2026-10-11' → '10월 11일' (오늘이면 ' · 오늘')
function dayLabel(iso) {
  const [, m, d] = iso.split('-').map(Number)
  const t = new Date()
  const isToday = t.getMonth() + 1 === m && t.getDate() === d && String(t.getFullYear()) === iso.slice(0, 4)
  return `${m}월 ${d}일${isToday ? ' · 오늘' : ''}`
}

// 날짜별로 묶고, 날짜 합계는 소비만 (입금 · 이체 제외)
function groupByDay(items) {
  const days = []
  for (const item of items) {
    let day = days.at(-1)
    if (!day || day.date !== item.occurredOn) days.push((day = { date: item.occurredOn, items: [], total: 0 }))
    day.items.push(toView(item))
    if (item.transactionType === 'spending') day.total += item.netAmount
  }
  return days
}

// 화면 위 제목: 달력월 = '10월', 직접 고른 기간 = '9.01 ~ 9.30', 전체 기간 = '전체 기간'
function periodLabel(period) {
  if (period.basis === 'all') return '전체 기간'
  const short = (iso) => iso && `${Number(iso.slice(5, 7))}.${iso.slice(8, 10)}`
  if (period.basis === 'month') return `${Number(period.from.slice(5, 7))}월`
  return `${short(period.from) ?? '처음'} ~ ${short(period.to)}`
}

export default function TransactionsPage() {
  // 주소의 ?category= 값(카테고리 코드)을 읽고 바꾸는 도구. 없으면 전체
  const [params, setParams] = useSearchParams()
  const raw = params.get('category') ?? ''
  const selected = CODE_BY_NAME[raw] ?? raw
  const pick = (code) => setParams(code ? { category: code } : {})
  const { openPopup } = usePopup()
  // 카테고리를 바꾸면 주소가 바뀌고 → 장고에게 다시 받아온다
  const { data, error } = useApi(`/api/finance/transactions/${selected ? `?category=${encodeURIComponent(selected)}` : ''}`)
  // '확인 필요' 건수 = 기간 없이 전체 (H-03). 칩 · 배너 숫자가 홈과 같아진다
  const { data: pending } = useApi('/api/finance/transactions/?category=UNCLASSIFIED')
  const pendingCount = pending?.summary.needsReviewCount ?? 0

  if (!data) return <Layout title="거래 내역" activeTab="txs"><ApiStatus error={error} /></Layout>
  const { period, summary, items } = data
  const days = groupByDay(items)

  return (
    <Layout title="거래 내역" activeTab="txs">
      <div className="mc-sumbar">
        {/* 달 이동은 다음 단계 (from · to 를 바꿔 다시 받기) */}
        <button type="button" className="mc-icon" aria-label="이전 달">‹</button>
        <strong className="mc-sumbar-m">{periodLabel(period)}</strong>
        <button type="button" className="mc-icon" aria-label="다음 달">›</button>
        {/* 이 목록의 소비 소계일 뿐 — 예산 · 남은 생활비는 홈 · 예산 화면 값 (급여 주기 기준이라 다름) */}
        <span className="mc-sumbar-v"><b>{won(summary.spentTotal)}</b><span className="mc-muted"> 소비</span></span>
      </div>

      {pendingCount > 0 && (
        <button type="button" className="mc-pend-banner" aria-pressed={selected === PENDING.code} onClick={() => pick(PENDING.code)}>
          <span className="mc-pend-dot">{pendingCount}</span>
          <span className="mc-grow">
            <strong>카테고리를 확인해 주세요</strong>
            <small>AI가 확신하지 못한 거래 {pendingCount}건 · 확인하면 소비 분석에 반영돼요</small>
          </span>
          <span className="mc-muted">›</span>
        </button>
      )}

      <div className="mc-chipbar mc-cat-filter" aria-label="카테고리 필터">
        <button type="button" className="mc-chip-warn" aria-pressed={selected === PENDING.code} onClick={() => pick(PENDING.code)}>
          {PENDING.name} {pendingCount}
        </button>
        <button type="button" aria-pressed={selected === ''} onClick={() => pick('')}>전체</button>
        {CHIPS.map((cat) => (
          <button type="button" key={cat.code} aria-pressed={selected === cat.code} onClick={() => pick(cat.code)}>{cat.name}</button>
        ))}
      </div>

      <div className="mc-row mc-tx-sum">
        <span className="mc-muted">거래 {items.length}건 · 소비 <strong>{won(summary.spentTotal)}</strong></span>
        {/* 달력 보기는 나중에 */}
        <div className="mc-tabs">
          <button type="button" aria-pressed="true">리스트</button>
          <button type="button" aria-pressed="false">달력</button>
        </div>
      </div>

      {days.length === 0 && (
        <div className="mc-card"><p className="mc-muted" style={{ margin: 0 }}>이 기간에 거래가 없어요.</p></div>
      )}
      {days.map((day) => (
        <div className="mc-card mc-tx-day" key={day.date}>
          <div className="mc-row"><p className="mc-muted" style={{ margin: 0 }}>{dayLabel(day.date)}</p>{day.total > 0 && <span className="mc-muted">{won(day.total)}</span>}</div>
          {day.items.map((tx) =>
            tx.kind === 'pending' ? (
              <div className="mc-tx-pend" key={tx.id}>
                <div className="mc-row">
                  <span className="mc-grow"><strong>{tx.name}</strong><span className="mc-pend-badge">카테고리 확인 필요</span></span>
                  <strong>{won(tx.net)}</strong>
                </div>
                {/* 확인 필요 이유 · 후보 카테고리는 거래 한 건 보기(17번 suggestion)가 연결되면 보여 준다 */}
                <p className="mc-pend-why">어디에 쓴 돈인가요?</p>
                <div className="mc-pend-pick">
                  <button type="button" onClick={() => openPopup(<TxPickPopup tx={tx} categories={PICK_CATS} />)}>카테고리 고르기</button>
                </div>
              </div>
            ) : (
              <button type="button" className={`mc-listrow${tx.cancel ? ` is-${tx.cancel}` : ''}${tx.kind !== 'normal' ? ` is-${tx.kind}` : ''}`} key={tx.id}
                onClick={() => openPopup(<TransactionPopup tx={tx} date={dayLabel(day.date)} categories={PICK_CATS} />)}>
                <span className="mc-tile"><Icon name={tx.icon} /></span>
                <span className="mc-grow">
                  <strong>{tx.name}{tx.funding === 'fixed' && <em className="mc-tx-tag is-fixed">고정</em>}</strong>
                  <span className="mc-muted">{tx.kind === 'income' ? '월급' : tx.kind === 'transfer' ? '이체 · 소비 아님' : tx.cat}{tx.via && ` · ${tx.via}`}</span>
                </span>
                <span className="mc-right">
                  {/* 취소: 금액에 줄 긋고 "취소" / 부분취소: 남은 금액 + 취소된 금액 */}
                  {tx.cancel === 'full' && <><s>{won(tx.amount)}</s> <em className="mc-cancel-tag">취소</em></>}
                  {tx.cancel === 'partial' && <>{won(tx.net)}<small className="mc-cancel-sub">부분취소 −{won(tx.cancelAmount)}</small></>}
                  {!tx.cancel && tx.kind === 'income' && <><span className="mc-tx-in">+{won(tx.amount)}</span> <span className="mc-muted">›</span></>}
                  {!tx.cancel && tx.kind === 'transfer' && <><span className="mc-tx-out">{won(tx.amount)}</span> <span className="mc-muted">›</span></>}
                  {!tx.cancel && tx.kind === 'normal' && <>{won(tx.amount)} <span className="mc-muted">›</span></>}
                </span>
              </button>
            )
          )}
        </div>
      ))}

      <button type="button" className="mc-link" onClick={() => openPopup(<FilterPopup />)}>필터 · 거래 유형</button>
    </Layout>
  )
}

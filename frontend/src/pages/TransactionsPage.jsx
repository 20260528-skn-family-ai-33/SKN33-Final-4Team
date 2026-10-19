// 거래 내역 (장고: finance/templates/finance/txs.html + finance/views.py 의 필터)
// 주소 ?category=식비 로 카테고리만 골라 본다 (장고와 같은 주소 규칙)
import { useSearchParams } from 'react-router'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import { won } from '../utils/money.js'
import { MONTH, RECENT, PENDING_COUNT, PENDING_LABEL, CATEGORIES, TX_DAYS } from '../mock/finance.js'
import { usePopup } from '../popups/context.js'
import TransactionPopup from '../popups/TransactionPopup.jsx'
import TxPickPopup from '../popups/TxPickPopup.jsx'
import FilterPopup from '../popups/FilterPopup.jsx'

// 고른 카테고리에 맞는 거래만 남기기 (장고 views.transactions 의 keep 함수)
function filterDays(selected) {
  const keep = (tx) =>
    selected === '전체' ? true : selected === PENDING_LABEL ? tx.kind === 'pending' : tx.cat === selected
  return TX_DAYS
    .map((day) => {
      const items = day.items.filter(keep)
      return { date: day.date, items, total: items.reduce((sum, tx) => sum + tx.amount, 0) }
    })
    .filter((day) => day.items.length > 0)
}

export default function TransactionsPage() {
  // 주소의 ?category= 값을 읽고 바꾸는 도구
  const [params, setParams] = useSearchParams()
  const selected = params.get('category') ?? '전체'
  const days = filterDays(selected)
  const pick = (cat) => setParams({ category: cat })
  const { openPopup } = usePopup()

  return (
    <Layout title="거래 내역" activeTab="txs">
      <div className="mc-sumbar">
        {/* 달 이동은 DB 연결 후 */}
        <button type="button" className="mc-icon" aria-label="이전 달">‹</button>
        <strong className="mc-sumbar-m">{MONTH.label}</strong>
        <button type="button" className="mc-icon" aria-label="다음 달">›</button>
        <span className="mc-sumbar-v"><b>{won(MONTH.used)}</b><span className="mc-muted"> / {MONTH.budgetLabel} · {MONTH.pct}%</span></span>
      </div>

      {PENDING_COUNT > 0 && (
        <button type="button" className="mc-pend-banner" aria-pressed={selected === PENDING_LABEL} onClick={() => pick(PENDING_LABEL)}>
          <span className="mc-pend-dot">{PENDING_COUNT}</span>
          <span className="mc-grow">
            <strong>카테고리를 확인해 주세요</strong>
            <small>AI가 확신하지 못한 거래 {PENDING_COUNT}건 · 확인하면 소비 분석에 반영돼요</small>
          </span>
          <span className="mc-muted">›</span>
        </button>
      )}

      <div className="mc-chipbar mc-cat-filter" aria-label="카테고리 필터">
        <button type="button" className="mc-chip-warn" aria-pressed={selected === PENDING_LABEL} onClick={() => pick(PENDING_LABEL)}>
          확인 필요 {PENDING_COUNT}
        </button>
        {['전체', ...CATEGORIES].map((cat) => (
          <button type="button" key={cat} aria-pressed={selected === cat} onClick={() => pick(cat)}>{cat}</button>
        ))}
      </div>

      <div className="mc-row mc-tx-sum">
        <span className="mc-muted">최근 거래 {RECENT.count}건 · <strong>{won(RECENT.total)}</strong></span>
        {/* 달력 보기는 나중에 */}
        <div className="mc-tabs">
          <button type="button" aria-pressed="true">리스트</button>
          <button type="button" aria-pressed="false">달력</button>
        </div>
      </div>

      {days.length === 0 && (
        <div className="mc-card"><p className="mc-muted" style={{ margin: 0 }}>이 카테고리의 거래가 없어요.</p></div>
      )}
      {days.map((day) => (
        <div className="mc-card mc-tx-day" key={day.date}>
          <div className="mc-row"><p className="mc-muted" style={{ margin: 0 }}>{day.date}</p><span className="mc-muted">{won(day.total)}</span></div>
          {day.items.map((tx) =>
            tx.kind === 'pending' ? (
              <div className="mc-tx-pend" key={tx.id}>
                <div className="mc-row">
                  <span className="mc-grow"><strong>{tx.name}</strong><span className="mc-pend-badge">카테고리 확인 필요</span></span>
                  <strong>{won(tx.amount)}</strong>
                </div>
                <p className="mc-pend-why">{tx.why} 어디에 쓴 돈인가요?</p>
                <div className="mc-pend-pick">
                  {tx.picks.map((p) => <button type="button" key={p}>{p}</button>)}
                  <button type="button" onClick={() => openPopup(<TxPickPopup tx={tx} />)}>다른 카테고리</button>
                </div>
              </div>
            ) : (
              <button type="button" className="mc-listrow" key={tx.id} onClick={() => openPopup(<TransactionPopup tx={tx} date={day.date} />)}>
                <span className="mc-tile"><Icon name={tx.icon} /></span>
                <span className="mc-grow"><strong>{tx.name}</strong><span className="mc-muted">{tx.cat}</span></span>
                <span className="mc-right">{won(tx.amount)} <span className="mc-muted">›</span></span>
              </button>
            )
          )}
        </div>
      ))}

      <button type="button" className="mc-link" onClick={() => openPopup(<FilterPopup />)}>필터 · 거래 유형</button>
    </Layout>
  )
}

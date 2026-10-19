// 연결 관리 — 마이 > 연결 관리
// 계좌 목록은 시안 예시값 — API 설계 후 useApi 로 바꾼다
import { Fragment } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import { usePopup } from '../popups/context.js'
import AccountPopup from '../popups/AccountPopup.jsx'
import CardBillPopup from '../popups/CardBillPopup.jsx'
import { won } from '../utils/money.js'
import { PATHS } from '../routes.js'

const ACCOUNTS = [
  { id: 'sh1', bank: '신한은행', short: '신한', name: '생활비통장', no: '1234', kind: '입출금', use: '생활비', balance: 340_000, updated: '오늘 09:30',
    fixed: [{ name: '구독료', day: 5, amount: 15_000 }] },
  { id: 'kb1', bank: '국민은행', short: '국민', name: '급여통장', no: '5678', kind: '입출금', use: '고정지출', balance: 2_460_000, updated: '오늘 09:30',
    fixed: [{ name: '월세', day: 25, amount: 700_000 }, { name: '통신비', day: 20, amount: 55_000 }] },
]

export default function ConnectionsPage() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()
  const toConnect = () => navigate(PATHS.connect)

  return (
    <Layout title="연결 관리" showBack>
      <h1>연결 관리</h1>
      <div className="mc-card">
        <div className="mc-row"><h2>은행 2곳 · 계좌 2개</h2><span className="mc-badge">필수 · 조회 전용</span></div>
        {ACCOUNTS.map((a) => (
          <Fragment key={a.id}>
            <p className="mc-inst-st">{a.bank} · <span className="ok">연결됨</span> · 마지막 조회 {a.updated}</p>
            <button type="button" className="mc-listrow" onClick={() => openPopup(<AccountPopup acct={a} />)}>
              <span className="mc-tile"><Icon name="Landmark" /></span>
              <span className="mc-grow"><strong>{a.short} ****{a.no}</strong><span className="mc-muted">{a.name} · {a.kind} · {a.use} · {a.updated}</span></span>
              <span className="mc-right">{won(a.balance)} <span className="mc-muted">›</span></span>
            </button>
            <button type="button" className="mc-link" onClick={toConnect}>+ {a.bank} 계좌 추가</button>
          </Fragment>
        ))}
        <button type="button" className="mc-link" onClick={toConnect}>+ 다른 은행 연결</button>
      </div>

      <div className="mc-card">
        <div className="mc-row"><h2>카드사 1곳 · 카드 2장</h2><span className="mc-hint">선택</span></div>
        <button type="button" className="mc-listrow" onClick={() => openPopup(<CardBillPopup />)}>
          <span className="mc-tile"><Icon name="CreditCard" /></span>
          <span className="mc-grow"><strong>신한카드</strong><span className="mc-muted">카드 2장 · 청구서 10.15 출금예정</span></span>
          <span className="mc-right">420,000원 <span className="mc-muted">›</span></span>
        </button>
        <button type="button" className="mc-listrow" onClick={toConnect}>카드사 추가 연결　›</button>
      </div>

      <div className="mc-card">
        <h2>대출 <span className="mc-hint">CODEF · 조회 전용</span></h2>
        <button type="button" className="mc-listrow" onClick={toConnect}>상환 중인 대출 연결　›</button>
      </div>
      <p className="mc-note">계좌를 누르면 별칭·선택 해제·은행 연결 해제를 할 수 있어요. 송금·결제는 실행하지 않아요.</p>
    </Layout>
  )
}

// 연결 관리 — 마이 > 연결 관리
// 계좌 목록은 시안 예시값 — API 설계 후 useApi 로 바꾼다
// status: ok = 연결됨 / relogin = 다시 연결 필요 (은행 비밀번호가 바뀌면 조회가 실패함 → CODEF 계정 수정 /v1/account/update)
import { Fragment, useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import { usePopup } from '../popups/context.js'
import AccountPopup from '../popups/AccountPopup.jsx'
import CardBillPopup from '../popups/CardBillPopup.jsx'
import ConnectLoginPopup from '../popups/ConnectLoginPopup.jsx'
import { mockConnect } from '../utils/mockConnect.js'
import { won } from '../utils/money.js'
import { PATHS } from '../routes.js'
import LoanAddPopup from '../popups/LoanAddPopup.jsx'

// 대출 (결정 P · Z①): 은행 연결로 불러온 대출 + 직접 등록한 대출. 1차 목록엔 상환 중인 대출만 (AA③)
// 대출 기관(캐피탈 등)을 따로 연결하면 연결 목록에 businessType: loan 으로 함께 보인다
const LOANS = [
  { id: 'L1', name: '신용대출', org: 'KB국민은행', source: '은행 조회', balance: 6_420_000, pay: '매월 25일 · 310,500원' },
  { id: 'L2', name: '학자금대출', org: '한국장학재단', source: '직접 등록', balance: 3_150_000, pay: '월 상환액 확인 필요' },
]

const ACCOUNTS = [
  { id: 'sh1', bank: '신한은행', short: '신한', name: '저축예금', nickname: null, no: '1234', kind: '입출금', use: '생활비', balance: 340_000, updated: '오늘 09:30', status: 'ok',
    fixed: [{ name: '구독료', day: 5, amount: 15_000 }] },
  { id: 'kb1', bank: '국민은행', short: '국민', name: 'KB국민ONE통장', nickname: '급여통장', no: '5678', kind: '입출금', use: '고정지출', balance: 2_460_000, updated: '10.17 09:30', status: 'relogin',
    fixed: [{ name: '월세', day: 25, amount: 700_000 }, { name: '통신비', day: 20, amount: 55_000 }],
    savings: [{ name: '신한 자유적금', day: 25, amount: 300_000 }] },
]

export default function ConnectionsPage() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()
  const toConnect = () => navigate(PATHS.connect)
  // 은행별 연결 상태 { 국민은행: { status: 'relogin' | 'busy' | 'ok' | 'fail', error?, updated? } }
  const [conn, setConn] = useState(() => Object.fromEntries(ACCOUNTS.map((a) => [a.bank, { status: a.status, updated: a.updated }])))
  const setBank = (bank, patch) => setConn((c) => ({ ...c, [bank]: { ...c[bank], ...patch } }))

  // 다시 연결: 기관 로그인 팝업을 그대로 다시 쓴다
  const relogin = (bank) => openPopup(
    <ConnectLoginPopup inst={bank} onSubmit={async (form) => {
      setBank(bank, { status: 'busy', error: null })
      const r = await mockConnect(bank, form)
      setBank(bank, r.status === 'ok' ? { status: 'ok', updated: '방금' } : { status: 'fail', error: r.message ?? '다시 시도해 주세요.' })
    }} />,
  )

  return (
    <Layout title="연결 관리" showBack>
      <h1>연결 관리</h1>
      <div className="mc-card">
        <div className="mc-row"><h2>은행 2곳 · 계좌 2개</h2><span className="mc-badge">필수 · 조회 전용</span></div>
        {ACCOUNTS.map((a) => {
          const c = conn[a.bank]
          const stale = c.status !== 'ok'
          return (
          <Fragment key={a.id}>
            <p className="mc-inst-st">{a.bank} · {stale
              ? <span className="mc-conn-fail">다시 연결이 필요해요</span>
              : <span className="ok">연결됨</span>} · 마지막 조회 {c.updated}</p>
            {stale && (
              <div className="mc-relogin" role="note">
                <p>{c.status === 'fail' ? `연결 실패 · ${c.error}` : `${a.bank} 비밀번호가 바뀌어 조회하지 못했어요. 다시 연결하면 최신 잔액과 거래를 불러와요.`}</p>
                <button type="button" className="mc-conn-btn" disabled={c.status === 'busy'} onClick={() => relogin(a.bank)}>
                  {c.status === 'busy' ? '연결하는 중…' : '다시 연결'}
                </button>
              </div>
            )}
            <button type="button" className="mc-listrow" onClick={() => openPopup(<AccountPopup acct={a} />)}>
              <span className="mc-tile"><Icon name="Landmark" /></span>
              <span className="mc-grow"><strong>{a.short} ****{a.no}</strong><span className="mc-muted">{a.nickname ?? a.name} · {a.kind} · {a.use} · {c.updated}</span></span>
              <span className="mc-right">{won(a.balance)}{stale && <small className="mc-cancel-sub">{c.updated} 기준</small>} <span className="mc-muted">›</span></span>
            </button>
            <button type="button" className="mc-link" onClick={toConnect}>+ {a.bank} 계좌 추가</button>
          </Fragment>
          )
        })}
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
        <div className="mc-row"><h2>대출 {LOANS.length}건</h2><span className="mc-hint">상환 중 · 조회 전용</span></div>
        {LOANS.map((l) => (
          <button type="button" className="mc-listrow" key={l.id} onClick={() => navigate(PATHS.loanCheck, { state: { from: 'my' } })}>
            <span className="mc-tile"><Icon name="Landmark" /></span>
            <span className="mc-grow"><strong>{l.name}</strong><span className="mc-muted">{l.org} · {l.source} · {l.pay}</span></span>
            <span className="mc-right">{won(l.balance)} <span className="mc-muted">›</span></span>
          </button>
        ))}
        <button type="button" className="mc-link" onClick={() => openPopup(<LoanAddPopup />)}>+ 조회되지 않은 대출 직접 등록</button>
        <button type="button" className="mc-listrow" onClick={toConnect}>대출 기관 연결　›</button>
      </div>
      <p className="mc-note">계좌를 누르면 별칭·선택 해제·은행 연결 해제를 할 수 있어요. 송금·결제는 실행하지 않아요.</p>
    </Layout>
  )
}

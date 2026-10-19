// 계좌 연결 3: 사용할 계좌 고르기
// 계좌 목록은 시안 예시값 — CODEF 보유계좌 조회 API 가 생기면 바꾼다
import { useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import AcctPick from '../components/AcctPick.jsx'
import { usePicked } from '../utils/usePicked.js'
import { won } from '../utils/money.js'
import { PATHS } from '../routes.js'

const USES = ['생활비', '고정지출', '저축', '쿠션']
const BANKS = [
  { bank: '신한은행', accounts: [
    { id: 'sh1', name: '생활비통장', no: '1234', kind: '입출금', balance: 340_000, use: '생활비' },
    { id: 'sh2', name: '자유적금', no: '7890', kind: '적금', balance: 1_200_000, note: '저축 계좌예요. 생활비 잔액에는 더하지 않아요.' },
  ] },
  { bank: '국민은행', accounts: [
    { id: 'kb1', name: '급여통장', no: '5678', kind: '입출금', balance: 2_460_000, use: '고정지출' },
    { id: 'kb2', name: '마이너스통장', no: '3344', kind: '마이너스통장', balance: -150_000, note: '한도 3,000,000원는 내 돈이 아니라서 잔액에 더하지 않아요.' },
  ] },
]
// 입출금 계좌는 처음부터 골라 둔다
const FIRST_PICKED = BANKS.flatMap((b) => b.accounts).filter((a) => a.use).map((a) => a.id)
const FIRST_USES = Object.fromEntries(BANKS.flatMap((b) => b.accounts).filter((a) => a.use).map((a) => [a.id, a.use]))

export default function ConnectAccountsPage() {
  const navigate = useNavigate()
  const picked = usePicked(FIRST_PICKED)
  const [uses, setUses] = useState(FIRST_USES)  // { sh1: '생활비', kb1: '고정지출' }

  return (
    <Layout
      title="계좌 연결"
      showBack
      foot={<button type="button" className="mc-primary" onClick={() => navigate(PATHS.connectCards)}>선택한 계좌 {picked.list.length}개로 계속</button>}
    >
      <div className="mc-eyebrow">계좌 연결</div>
      <h1>사용할 계좌를 골라 주세요</h1>
      <p className="mc-muted">연결한 은행에서 조회한 내 계좌예요. 예산에 쓸 계좌만 고르세요.</p>
      {BANKS.map((b) => (
        <div className="mc-card" key={b.bank}>
          <div className="mc-row"><h2>{b.bank}</h2><span className="mc-badge">연결 완료</span></div>
          {b.accounts.map((a) => (
            <AcctPick key={a.id} on={picked.has(a.id)} onToggle={() => picked.toggle(a.id)}
              name={a.name} sub={`****${a.no} · ${a.kind}`} amount={won(a.balance).replace('-', '−')} negative={a.balance < 0}>
              {a.use ? (
                <div className="mc-acctuse">
                  <span>용도</span>
                  {USES.map((u) => (
                    <button key={u} type="button" aria-pressed={uses[a.id] === u} onClick={() => setUses({ ...uses, [a.id]: u })}>{u}</button>
                  ))}
                </div>
              ) : (
                <p className="mc-acctpick-note">{a.note}</p>
              )}
            </AcctPick>
          ))}
        </div>
      ))}
      <p className="mc-note">조회 가능한 계좌만 보여요. 같은 은행 계좌를 나중에 추가해도 다시 인증하지 않아요.</p>
      <div className="mc-error" role="alert"></div>
    </Layout>
  )
}

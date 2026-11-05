// 계좌 연결 5: 연결 결과
// 연결 목록(GET /api/finance/connections/) · 고른 계좌(GET /api/finance/connect/accounts/)를 그대로 보여 준다 (11/03 S2)
// [다시 불러오기] = POST /api/finance/sync/ → 작업 결과의 기관별 성공 · 실패를 그대로 보여 준다 (실패를 0건으로 보이지 않기)
// [다음] = 월급 입력. 대출이 있으면 선택적으로 상환 정보 확인·등록 후 월급 입력으로 진행한다.
import { useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import InstRow from '../components/InstRow.jsx'
import ApiStatus from '../components/ApiStatus.jsx'
import { won } from '../utils/money.js'
import { useApi } from '../utils/api.js'
import { runFinanceSync, syncLines } from '../utils/financeSync.js'
import { PATHS } from '../routes.js'

export default function ConnectDonePage() {
  const navigate = useNavigate()
  const conns = useApi('/api/finance/connections/')
  const accts = useApi('/api/finance/connect/accounts/')
  const [sync, setSync] = useState({ busy: false, lines: null, error: '' })

  const refresh = async () => {
    setSync({ busy: true, lines: null, error: '' })
    try {
      const result = await runFinanceSync()
      setSync({ busy: false, lines: syncLines(result), error: '' })
      conns.reload()
      accts.reload()
    } catch (err) {
      setSync({ busy: false, lines: null, error: err.message })
    }
  }

  if (!conns.data || !accts.data) return <Layout title="계좌 연결" showBack><ApiStatus error={conns.error ?? accts.error} /></Layout>

  const banks = conns.data.items.filter((c) => c.businessType === 'bank')
  const cards = conns.data.items.filter((c) => c.businessType === 'card')
  const selected = accts.data.items.filter((a) => a.isSelected)
  const cardCount = cards.reduce((n, c) => n + c.cardCount, 0)

  return (
    <Layout title="계좌 연결" showBack foot={<button type="button" className="mc-primary" onClick={() => navigate(PATHS.salary)}>다음</button>}>
      <div className="mc-eyebrow">계좌 연결</div>
      <h1>연결을 마쳤어요</h1>
      <p className="mc-muted">은행 {banks.length}곳 · 고른 계좌 {selected.length}개 · 카드사 {cards.length}곳 · 카드 {cardCount}장이 연결돼 있어요.</p>
      <div className="mc-card">
        <h2>고른 계좌</h2>
        {selected.length === 0 && <p className="mc-muted">고른 계좌가 없어요.</p>}
        {selected.map((a) => (
          <InstRow key={a.accountId} name={a.displayName} sub={a.status === 'relogin' ? '다시 연결 필요 · 마지막으로 받은 잔액' : a.organizationName}
            right={<strong>{a.balance == null ? '확인 안 됨' : won(a.balance)}</strong>} />
        ))}
      </div>
      <div className="mc-card">
        <h2>연결한 기관</h2>
        {conns.data.items.map((c) => (
          <InstRow key={c.connectionId} icon={c.businessType === 'card' ? 'CreditCard' : 'Landmark'} name={c.organizationName}
            sub={c.status === 'relogin' ? '다시 연결이 필요해요' : c.businessType === 'card' ? `카드 ${c.cardCount}장` : `계좌 ${c.accountCount}개`}
            subClass={c.status === 'relogin' ? 'mc-conn-fail' : ''} />
        ))}
        <button type="button" className="mc-secondary" disabled={sync.busy} onClick={refresh}>{sync.busy ? '불러오는 중…' : '거래 다시 불러오기'}</button>
        {sync.lines && sync.lines.map((l) => (
          <p key={l.name} className={l.ok ? 'mc-note' : 'mc-error'} role={l.ok ? undefined : 'alert'}>{l.name} · {l.text}</p>
        ))}
        {sync.error && <p className="mc-error" role="alert">{sync.error}</p>}
      </div>
      <button type="button" className="mc-secondary" onClick={() => navigate(PATHS.loanCheck)}>대출 상환 정보 확인·등록</button>
      <p className="mc-note">거래 화면에서 불러온 거래를 확인할 수 있어요. 카드번호가 필요한 카드사(현대카드)는 연결 관리에서 다시 불러와 주세요.</p>
    </Layout>
  )
}

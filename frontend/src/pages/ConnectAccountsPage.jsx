// 계좌 연결 3: 사용할 계좌 고르기
// GET · PUT /api/finance/connect/accounts/ (docs/api/finance-connect.md '계좌 후보 보기' · '계좌 선택 저장', 11/03 S2)
//   - 저장된 계좌만 읽는다(CODEF 를 부르지 않음). 후보 = 연결이 살아 있는 은행의 입출금 계좌
//   - 고른 계좌 전체 목록을 보낸다. 1개 이상 (빈 목록 400). 하나라도 틀리면 서버가 전부 저장하지 않는다
//   - 계좌 용도 · 종류 지정은 서버가 받지 않아서 이 화면에서 받지 않는다 (미지원)
// 지금 수집은 기관 단위라 선택 전에 그 은행 계좌의 거래도 이미 불러와 있다. 선택은 관리 대상(잔액 · 월급 찾기)만 정한다
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import Mascot from '../components/Mascot.jsx'
import AcctPick from '../components/AcctPick.jsx'
import ApiStatus from '../components/ApiStatus.jsx'
import { usePicked } from '../utils/usePicked.js'
import { won } from '../utils/money.js'
import { apiSend, useApi } from '../utils/api.js'
import { PATHS, afterAccountPick } from '../routes.js'

export default function ConnectAccountsPage() {
  const { data, error } = useApi('/api/finance/connect/accounts/')
  if (!data) return <Layout title="계좌 연결" showBack><ApiStatus error={error} /></Layout>
  return <AccountPick items={data.items} />
}

function AccountPick({ items }) {
  const navigate = useNavigate()
  const { state } = useLocation()
  const picked = usePicked(items.filter((a) => a.isSelected).map((a) => a.accountId))
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const banks = [...new Set(items.map((a) => a.organizationName))]

  const next = async () => {
    if (busy) return
    // 계좌는 1개 이상 골라야 한다 (10/22 결정 K). 카드는 0개도 됨
    if (picked.list.length === 0) { setError('예산에 쓸 계좌를 1개 이상 골라 주세요.'); return }
    setBusy(true)
    setError('')
    try {
      await apiSend('/api/finance/connect/accounts/', 'PUT', { selectedAccountIds: picked.list })
      const to = afterAccountPick(state)
      // 연결 관리로 돌아갈 때는 계좌 선택 화면을 기록에서 지워 뒤로 가기가 다시 이 화면으로 오지 않게 한다
      navigate(to, { replace: to === PATHS.connections })
    } catch (err) {
      setError(err.fields?.selectedAccountIds?.[0] ?? err.message)
      setBusy(false)
    }
  }

  if (items.length === 0) {
    return (
      <Layout title="계좌 연결" showBack foot={<button type="button" className="mc-primary" onClick={() => navigate(PATHS.connect)}>은행 연결하기</button>}>
        <div className="mc-o2-line">
        <Mascot mood="basic" size={64} />
        <p className="mc-o2-bubble">아직 <b>연결된 계좌</b>가 없어요. 은행을 연결하면 여기서 고를 수 있어요.</p>
      </div>
      </Layout>
    )
  }

  return (
    <Layout
      title="계좌 연결"
      showBack
      foot={<button type="button" className="mc-primary" disabled={busy} onClick={next}>{busy ? '저장 중…' : `선택한 계좌 ${picked.list.length}개로 계속`}</button>}
    >
      <div className="mc-o2-line">
        <Mascot mood="planner" size={64} />
        <p className="mc-o2-bubble">예산에 쓸 <b>입출금 계좌</b>만 골라 주세요.</p>
      </div>
      {banks.map((bank) => {
        const list = items.filter((a) => a.organizationName === bank)
        const relogin = list.some((a) => a.status === 'relogin')
        return (
          <div className="mc-card" key={bank}>
            <div className="mc-row"><h2>{bank}</h2><span className="mc-badge">{relogin ? '다시 연결 필요' : '연결 완료'}</span></div>
            {list.map((a) => (
              <AcctPick key={a.accountId} on={picked.has(a.accountId)} onToggle={() => { picked.toggle(a.accountId); setError('') }}
                name={a.displayName} sub={[a.name, a.maskedNumber].filter(Boolean).join(' · ')}
                amount={a.balance == null ? '잔액 확인 안 됨' : won(a.balance).replace('-', '−')} negative={a.balance < 0} />
            ))}
          </div>
        )
      })}
      {error && <div className="mc-error" role="alert">{error}</div>}
    </Layout>
  )
}

// 계좌 연결 4: 사용할 카드 고르기
// GET · PUT /api/finance/connect/cards/ (docs/api/cards.md 6 · 7번, 11/03 S2)
//   - 저장된 카드만 읽는다(CODEF 를 부르지 않음). 카드는 카드사 연결 직후 서버가 불러 둔다
//   - 고른 카드 전체 목록을 보낸다. 빈 목록 = 전체 선택 해제(카드는 0개도 됨). 하나라도 틀리면 전부 저장하지 않는다
//   - 조회 실패를 '카드 없음'으로 보이지 않는다: 목록을 못 받으면 오류 카드, 받았는데 0건이면 '조회된 카드 없음'
import { useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import AcctPick from '../components/AcctPick.jsx'
import ApiStatus from '../components/ApiStatus.jsx'
import { usePicked } from '../utils/usePicked.js'
import { apiSend, useApi } from '../utils/api.js'
import { PATHS } from '../routes.js'

export default function ConnectCardsPage() {
  const { data, error } = useApi('/api/finance/connect/cards/')
  if (!data) return <Layout title="계좌 연결" showBack><ApiStatus error={error} /></Layout>
  return <CardPick items={data.items} />
}

function CardPick({ items }) {
  const navigate = useNavigate()
  // 처음 고르는 사람(선택된 카드가 하나도 없음)은 다 골라 두고 뺀다
  const first = items.some((c) => c.isSelected) ? items.filter((c) => c.isSelected) : items
  const picked = usePicked(first.map((c) => c.cardId))
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const save = async (ids) => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await apiSend('/api/finance/connect/cards/', 'PUT', { selectedCardIds: ids })
      navigate(PATHS.connectDone)
    } catch (err) {
      setError(err.fields?.selectedCardIds?.[0] ?? err.message)
      setBusy(false)
    }
  }

  return (
    <Layout title="계좌 연결" showBack
      foot={<button type="button" className="mc-primary" disabled={busy} onClick={() => save(picked.list)}>{busy ? '저장 중…' : `카드 ${picked.list.length}장으로 계속`}</button>}>
      <div className="mc-eyebrow">카드 연결 · 선택</div>
      <h1>사용할 카드를 골라 주세요</h1>
      <p className="mc-muted">연결한 카드사에서 불러온 내 카드예요. 나중에 골라도 돼요.</p>
      {items.length === 0 ? (
        <div className="mc-card">
          <h2>조회된 카드가 없어요</h2>
          <p className="mc-muted">연결한 카드사에서 불러온 카드가 없어요. 카드사 연결이 실패했다면 연결 결과 화면에서 다시 불러오기를 해 주세요.</p>
        </div>
      ) : (
        <div className="mc-card">
          {items.map((c) => (
            <AcctPick key={c.cardId} on={picked.has(c.cardId)} onToggle={() => { picked.toggle(c.cardId); setError('') }} name={c.displayName}
              sub={[c.cardType, c.cardStatus, c.status === 'relogin' && '다시 연결 필요'].filter(Boolean).join(' · ')} />
          ))}
        </div>
      )}
      <button type="button" className="mc-link" disabled={busy} onClick={() => save([])}>카드는 나중에 고를게요</button>
      {error && <div className="mc-error" role="alert">{error}</div>}
    </Layout>
  )
}

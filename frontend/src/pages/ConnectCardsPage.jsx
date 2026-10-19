// 계좌 연결 4: 사용할 카드 고르기
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import AcctPick from '../components/AcctPick.jsx'
import { usePicked } from '../utils/usePicked.js'
import { PATHS } from '../routes.js'

const CARDS = [
  { id: 'c1', name: '신한 Deep Dream', no: '1111', kind: '체크카드' },
  { id: 'c2', name: '신한 Mr.Life', no: '2222', kind: '신용카드' },
]

export default function ConnectCardsPage() {
  const navigate = useNavigate()
  const picked = usePicked(CARDS.map((c) => c.id))
  const next = () => navigate(PATHS.connectDone)

  return (
    <Layout title="계좌 연결" showBack foot={<button type="button" className="mc-primary" onClick={next}>다음</button>}>
      <div className="mc-eyebrow">카드 연결 · 선택</div>
      <h1>사용할 카드를 골라 주세요</h1>
      <p className="mc-muted">카드사에서 조회한 내 카드예요. 나중에 연결해도 돼요.</p>
      <div className="mc-card">
        <div className="mc-row"><h2>신한카드</h2><span className="mc-badge">연결 완료</span></div>
        {CARDS.map((c) => (
          <AcctPick key={c.id} on={picked.has(c.id)} onToggle={() => picked.toggle(c.id)} name={c.name} sub={`****${c.no} · ${c.kind}`} />
        ))}
      </div>
      <button type="button" className="mc-link" onClick={next}>카드는 나중에 연결할게요</button>
    </Layout>
  )
}

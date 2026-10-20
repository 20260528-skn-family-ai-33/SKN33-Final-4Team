// 계좌 연결 4: 사용할 카드 고르기
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import AcctPick from '../components/AcctPick.jsx'
import { usePicked } from '../utils/usePicked.js'
import { PATHS } from '../routes.js'

// 실제 응답처럼: 카드 종류(resCardType)는 카드사가 쓰는 글자 그대로 ("체크/본인", "본인신용", "가족카드" …)
// sleep = 휴면카드(resSleepYN) / state = 카드 상태(resState). 휴면은 처음에 빼 두고, 해지는 고를 수 없다
const CARDS = [
  { id: 'c1', name: '신한 Deep Dream', no: '1111', type: '체크/본인', state: '정상' },
  { id: 'c2', name: '신한 Mr.Life', no: '2222', type: '본인신용', state: '정상' },
  { id: 'c3', name: '신한 B.Big', no: '3333', type: '본인신용', state: '정상', sleep: true },
  { id: 'c4', name: '신한 Hi-Point', no: '4444', type: '가족카드', state: '해지' },
]
const usable = (c) => c.state !== '해지'

export default function ConnectCardsPage() {
  const navigate = useNavigate()
  const picked = usePicked(CARDS.filter((c) => usable(c) && !c.sleep).map((c) => c.id))
  const next = () => navigate(PATHS.connectDone)

  return (
    <Layout title="계좌 연결" showBack foot={<button type="button" className="mc-primary" onClick={next}>다음</button>}>
      <div className="mc-eyebrow">카드 연결 · 선택</div>
      <h1>사용할 카드를 골라 주세요</h1>
      <p className="mc-muted">카드사에서 조회한 내 카드예요. 나중에 연결해도 돼요.</p>
      <div className="mc-card">
        <div className="mc-row"><h2>신한카드</h2><span className="mc-badge">연결 완료</span></div>
        {CARDS.map((c) => usable(c) ? (
            <AcctPick key={c.id} dim={c.sleep} on={picked.has(c.id)} onToggle={() => picked.toggle(c.id)} name={c.name}
              sub={`****${c.no} · ${c.type}${c.sleep ? ' · 휴면' : ''}`}>
              {c.sleep && <p className="mc-acctpick-note">휴면 카드예요. 최근에 쓰지 않아 기본으로 빼 뒀어요. 쓰는 카드면 골라 주세요.</p>}
            </AcctPick>
        ) : (
          <div key={c.id} className="mc-card-off">
            <strong>{c.name}</strong>
            <small>****{c.no} · {c.type} · {c.state}된 카드라 내역을 불러오지 않아요</small>
          </div>
        ))}
      </div>
      <button type="button" className="mc-link" onClick={next}>카드는 나중에 연결할게요</button>
    </Layout>
  )
}

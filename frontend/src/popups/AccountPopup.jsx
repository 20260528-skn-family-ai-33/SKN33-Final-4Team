// 팝업: 연결 상세 — 연결 관리에서 계좌 · 카드 한 줄을 누르면 (11/9: 시안 목업 → 목록 API 값만 보여 주는 읽기 전용)
// props: kind = 'account' | 'card', item = GET /api/finance/connect/accounts/ · cards/ 의 한 항목, orgName = 기관 이름
//        related = 이 계좌 · 카드로 나가는 확정 고정지출 · 정기 저축 [{ id, type, name, amount, line }] (ConnectionsPage relatedTo), null = 못 불러옴
// 선택 바꾸기 · 연결 해제는 연결 관리 화면 버튼에서 한다 (여기서 따로 만들지 않음)
import Popup from './Popup.jsx'
import { won } from '../utils/money.js'
import { splitName } from '../utils/transactions.js'

const STATUS = { connected: '연결됨', relogin: '다시 연결 필요', disconnected: '연결 해제됨' }
const when = (iso) => (iso ? new Date(iso).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '조회 기록 없음')

export default function AccountPopup({ kind, item, orgName, related }) {
  const isCard = kind === 'card'
  const [name, last4] = splitName(item.displayName)
  const rows = [
    [isCard ? '카드번호' : '계좌번호', last4],
    !isCard && ['잔액', item.balance == null ? '잔액 모름' : won(item.balance)],
    !isCard && item.name && item.name !== name && ['상품명', item.name],
    isCard && item.cardStatus && ['카드 상태', item.cardStatus],
    isCard && item.cardType && ['카드 브랜드', item.cardType],
    ['관리', item.isSelected ? '관리 중' : '고르지 않음'],
    ['연결', STATUS[item.status] ?? item.status],
    ['마지막 조회', when(item.fetchedAt)],
  ].filter((r) => r && r[1])

  return (
    <Popup label="연결 상세" sub>
      <div className="mc-eyebrow">{orgName} · {isCard ? '카드' : '계좌'}</div>
      <h1>{name}</h1>
      <div className="mc-card">
        {rows.map(([k, v]) => <div className="mc-keyvalue" key={k}><span>{k}</span><strong>{v}</strong></div>)}
      </div>
      <div className="mc-card">
        <h2>이 {isCard ? '카드' : '계좌'}로 나가는 돈</h2>
        {related == null && <p className="mc-muted">고정지출 · 저축을 불러오지 못했어요.</p>}
        {related?.length === 0 && <p className="mc-muted">연결된 고정지출 · 저축이 없어요.</p>}
        {related?.map((r) => (
          <div className="mc-keyvalue" key={`${r.type}-${r.id}`}><span>{r.name}</span><strong className="mc-acct-due">{r.line.split(' · ')[0]}<br />{won(r.amount)}</strong></div>
        ))}
      </div>
    </Popup>
  )
}

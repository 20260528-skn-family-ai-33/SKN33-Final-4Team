// 계산 당시 미반영 사유를 표시한다. 청구서 조회만으로 승인 예산의 안내를 지우지 않는다.
// 화면에는 짧은 배너 한 줄만 두고, 자세한 설명과 청구서 확인하러 가기 버튼은 누르면 뜨는 시트에 둔다.
// 11/11: [청구서 확인하러 가기] → 가운데 창(BillCheckDialog)에서 그 카드사 청구서를 바로 불러오고, 새 청구서가 있을 때만 onRecalculate(지킬 돈 설정에서 재추천)
// 11/12: 서버가 '지금은 청구서가 나온 카드'(readyIds = cardBillsReady)를 주면 띠 문구를 바꾸고, 누르면 시트 · 확인 창 없이 바로 onRecalculate
// 11/12: 시트에 어느 카드사인지 목록으로 보여 준다. 안내에는 카드 ID만 있어서 연결 목록(GET /api/finance/connections/)의 카드사 이름,
//   카드 목록(GET /api/finance/connect/cards/)의 카드 이름으로 찾는다. 줄마다 카드사 이름 · 그 카드사의 해당 카드 이름을 보여 줘서 서로 구분된다
import Icon from './Icon.jsx'
import Popup from '../popups/Popup.jsx'
import { usePopup } from '../popups/context.js'
import { useApi } from '../utils/api.js'
import { splitName } from '../utils/transactions.js'
import BillCheckDialog from '../popups/BillCheckDialog.jsx'

function CardNoteSheet({ cardIds = [], onRecalculate, budgetStarted }) {
  const { openPopup } = usePopup()
  // 시트 대신 가운데 창을 띄운다 (팝업은 하나만 열려서 시트는 자연히 닫힘)
  const recalculate = () => openPopup(<BillCheckDialog cardIds={cardIds} onFound={onRecalculate} />)
  // 이 카드들을 가진 카드사 (같은 이름은 하나로) + 그 카드사의 해당 카드 이름. 연결 목록을 못 받았거나 받는 중이면 목록 없이 문장만
  const { data } = useApi('/api/finance/connections/')
  const { data: cardList } = useApi('/api/finance/connect/cards/')
  const cardName = Object.fromEntries((cardList?.items ?? []).map((c) => [c.cardId, c.displayName]))
  const issuers = []
  for (const c of data?.items ?? []) {
    const mine = (c.cardIds ?? []).filter((id) => cardIds.includes(id))
    if (c.businessType !== 'card' || !c.organizationName || !mine.length) continue
    const row = issuers.find((i) => i.name === c.organizationName) ?? issuers[issuers.push({ name: c.organizationName, cards: [] }) - 1]
    row.cards.push(...mine.map((id) => cardName[id]).filter(Boolean))
  }
  // 카드 이름 한 줄: 한 장이면 그대로('현대 M포인트 · ****1001'), 여러 장이면 번호 없이 '현대 M포인트 외 1장'
  const cardsLine = (cards) => (cards.length > 1 ? `${splitName(cards[0])[0]} 외 ${cards.length - 1}장` : cards[0])
  return (
    <Popup label="카드값 미반영 안내">
      {/* 꼭 나갈 돈 팝업과 같은 틀: 작은 머리말 + 제목 → 카드사 줄(연결 화면의 기관 줄) → 설명 두 문단 */}
      <div style={{ wordBreak: 'keep-all' }}>
        <div className="mc-eyebrow">카드값 미반영</div>
        <h2 style={{ textWrap: 'balance', margin: '4px 0 0' }}>청구서를 기다리는 카드사가 있어요</h2>
        <p className="mc-muted" style={{ margin: '4px 0 0' }}>이번 급여 주기 청구서가 아직 안 나왔어요.</p>
        {issuers.length > 0 && (
          <div className="mc-bill-issuers">
            {issuers.map((i) => (
              <div className="mc-inst" key={i.name}>
                <span className="mc-grow"><strong>{i.name}</strong>{i.cards.length > 0 && <small>{cardsLine(i.cards)}</small>}</span>
              </div>
            ))}
          </div>
        )}
        {/* 문단 끝에 한두 글자만 남지 않게 (text-wrap: pretty). '생활비가 많게 보일 수 있어요'는 띠에 이미 있어서 뺐다 */}
        <p className="mc-muted" style={{ margin: issuers.length ? 0 : '10px 0 0', textWrap: 'pretty' }}>
          {issuers.length ? '위 카드' : '그 카드'}로 지난 주기에 쓴 돈은 아직 예산에서 빼지 않았어요.
        </p>
        <p className="mc-muted" style={{ margin: '6px 0 0', textWrap: 'pretty' }}>청구서가 나오면 이번 급여 주기 안에 다시 추천받아 주세요.</p>
        <p className="mc-muted" style={{ margin: '2px 0 0', textWrap: 'pretty' }}>{budgetStarted ? '그때까지는 지금 예산이 그대로예요.' : '이 예산으로 먼저 시작해도 돼요.'}</p>
      </div>
      {onRecalculate && <button type="button" className="mc-primary" onClick={recalculate}>청구서 확인하러 가기</button>}
    </Popup>
  )
}

// compact: 한 줄 띠 (지킬 돈 화면처럼 이 안내보다 봐야 할 내용이 아래에 있을 때). action = 띠 오른쪽에 둘 버튼
// budgetStarted: 이미 시작한 예산이 있는지 (시트 문구). 기본은 다시 추천 경로가 있으면 있음 (홈 · 예산). 지킬 돈 화면은 첫 설정이면 없음
// readyIds: 그중 지금은 청구서가 나온 카드 (홈 budget.cardBillsReady · 예산 cardBillsReady)
export default function UnreflectedCardNote({ notes = [], readyIds = [], onRecalculate, budgetStarted = Boolean(onRecalculate), compact = false, action = null }) {
  const popup = usePopup()
  const cardIds = [...new Set(notes.filter((note) => note.reason === 'first_bill_unavailable').map((note) => note.cardId))]
  if (!cardIds.length) return null
  const openSheet = () => popup?.openPopup(<CardNoteSheet cardIds={cardIds} onRecalculate={onRecalculate} budgetStarted={budgetStarted} />)
  if (compact) {
    return (
      <div className="mc-min-warn mc-row" role="note" style={{ margin: '0 0 10px', padding: '6px 12px', wordBreak: 'keep-all' }}>
        <button type="button" onClick={openSheet} style={{ display: 'flex', alignItems: 'center', gap: 6, minHeight: 32, padding: 0, textAlign: 'left' }}>
          <Icon name="CreditCard" size={16} />
          <span>지난 카드값이 아직 반영 안 됐어요 ›</span>
        </button>
        {action}
      </div>
    )
  }
  // 청구서가 나온 카드가 하나라도 있으면 다시 추천받을 이유가 생겼다 → 같은 띠 모양으로 문구만 바꾸고 바로 이동
  if (onRecalculate && cardIds.some((id) => readyIds?.includes(id))) {
    return (
      <button type="button" className="mc-pend-banner" style={{ wordBreak: 'keep-all' }} onClick={onRecalculate}>
        <span style={{ color: 'var(--mc-alert)', display: 'flex' }}><Icon name="CreditCard" size={18} /></span>
        <span className="mc-grow">
          <strong>새 카드 청구서가 나왔어요</strong>
          <small>다시 추천받으면 지난 카드값이 예산에 반영돼요</small>
        </span>
        <span className="mc-muted">›</span>
      </button>
    )
  }
  return (
    <button type="button" className="mc-pend-banner" style={{ wordBreak: 'keep-all' }} onClick={openSheet}>
      <span style={{ color: 'var(--mc-alert)', display: 'flex' }}><Icon name="CreditCard" size={18} /></span>
      <span className="mc-grow">
        <strong>지난 카드값이 아직 반영 안 됐어요</strong>
        <small>생활비가 실제보다 많게 보일 수 있어요</small>
      </span>
      <span className="mc-muted">›</span>
    </button>
  )
}

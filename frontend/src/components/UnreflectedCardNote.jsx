// 계산 당시 미반영 사유를 표시한다. 청구서 조회만으로 승인 예산의 안내를 지우지 않는다.
// 화면에는 짧은 배너 한 줄만 두고, 자세한 설명과 청구서 확인하러 가기 버튼(지킬 돈 화면에서 청구서를 다시 불러온 뒤 재추천)은 누르면 뜨는 시트에 둔다.
import Icon from './Icon.jsx'
import Popup from '../popups/Popup.jsx'
import { usePopup } from '../popups/context.js'

function CardNoteSheet({ onRecalculate }) {
  const { closePopup } = usePopup()
  const recalculate = () => { closePopup(); onRecalculate() }
  return (
    <Popup label="카드값 미반영 안내">
      {/* 문장 하나가 휴대폰 폭에서 한 줄에 들어가게 짧게 나눈다 */}
      <div style={{ wordBreak: 'keep-all' }}>
        <h2 style={{ textWrap: 'balance', marginBottom: 8 }}>아직 청구서가 안 나온 카드사가 있어요</h2>
        <p className="mc-muted" style={{ margin: '2px 0' }}>그 카드로 지난 주기에 쓴 돈은 예산에서 빼지 않았어요.</p>
        <p className="mc-muted" style={{ margin: '2px 0' }}>청구서가 나오면 이번 급여 주기 안에 다시 추천받아 주세요.</p>
        <p className="mc-muted" style={{ margin: '2px 0' }}>{onRecalculate ? '다시 받기 전까지는 지금 예산이 그대로 유지돼요.' : '이 예산으로 먼저 시작해도 돼요.'}</p>
      </div>
      {onRecalculate && <button type="button" className="mc-primary" onClick={recalculate}>청구서 확인하러 가기</button>}
    </Popup>
  )
}

// compact: 한 줄 띠 (지킬 돈 화면처럼 이 안내보다 봐야 할 내용이 아래에 있을 때). action = 띠 오른쪽에 둘 버튼
export default function UnreflectedCardNote({ notes = [], onRecalculate, compact = false, action = null }) {
  const popup = usePopup()
  if (!notes.some((note) => note.reason === 'first_bill_unavailable')) return null
  const openSheet = () => popup?.openPopup(<CardNoteSheet onRecalculate={onRecalculate} />)
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

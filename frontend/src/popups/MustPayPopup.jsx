// 팝업: 꼭 나갈 돈 한 건 자세히 — 홈 목록은 이름 · 금액만, 나머지(나갈 날 · 계좌 · 상태)는 여기서 (11/3)
// props: item = GET /api/home/ 의 mustPay 한 건 (docs/api/home.md), date = '10.15' 처럼 짧은 날짜,
//        badges = 홈이 만든 상태 배지 [{tone, text}], onManage = 종류별 상세·관리 화면 열기
//        onCheckPayment = 대출: 납부 거래 확인 팝업 열기 (11/06, LoanPaymentPopup)
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { won } from '../utils/money.js'

const TYPE = { recurring: '고정지출', card_bill: '카드 청구서', loan: '대출 상환' }
const STATE = { unpaid: '아직 안 나갔어요', partial: '일부만 나갔어요', needs_review: '출금을 찾지 못했어요' }
// 배지마다 한 줄 설명 (home.md '미납 안내' · 결정기록 Z)
const WHY = {
  est: '카드 청구 금액이 아직 확정되지 않았어요. 카드를 더 쓰거나 취소하면 바뀔 수 있어요.',
  check: '출금일이 지났어요. 실제 출금과 연결되면 목록에서 빠져요.',
  review: '출금일 3일 뒤까지 맞는 출금을 찾지 못했어요. 고정지출 화면에서 실제로 나간 출금을 골라 주세요.',
}

export default function MustPayPopup({ item, date, badges, onManage, onCheckPayment }) {
  const { closePopup } = usePopup()
  const manage = () => { closePopup(); onManage() }
  const check = () => { closePopup(); onCheckPayment() }

  return (
    <Popup label="꼭 나갈 돈 자세히">
      <div className="mc-eyebrow">{TYPE[item.type] ?? '꼭 나갈 돈'}</div>
      <h1>{item.name}</h1>
      <div className="mc-card">
        <div className="mc-money" style={{ fontSize: 24 }}>{won(item.amount)}</div>
        {item.paymentState === 'partial' && <p className="mc-note" style={{ padding: '0 0 6px' }}>이미 나간 금액을 뺀 남은 금액이에요.</p>}
        <div className="mc-keyvalue"><span>나갈 날</span><strong>{date || '확인 필요'}</strong></div>
        <div className="mc-keyvalue"><span>결제 계좌</span><strong>{item.paymentAccount?.displayName ?? '결제 계좌 모름'}</strong></div>
        <div className="mc-keyvalue"><span>상태</span><strong>{STATE[item.paymentState] ?? '확인 필요'}</strong></div>
        {badges.map((b) => WHY[b.tone] && <p className="mc-note" style={{ paddingBottom: 0 }} key={b.tone}><strong>{b.text}</strong> · {b.tone === 'review' && item.type === 'loan' ? '출금일 3일 뒤까지 확인된 출금이 없어요. 납부 거래 확인에서 실제로 낸 출금을 골라 주세요.' : WHY[b.tone]}</p>)}
      </div>
      {onCheckPayment && <button type="button" className="mc-primary" onClick={check}>납부 거래 확인</button>}
      {onManage
        ? <button type="button" className={onCheckPayment ? 'mc-secondary' : 'mc-primary'} onClick={manage}>{item.type === 'card_bill' ? '청구서 상세 보기' : item.type === 'loan' ? '대출 상환 정보 확인' : item.paymentState === 'needs_review' ? '고정지출에서 출금 고르기' : '고정지출에서 관리하기'}</button>
        : <p className="mc-note">상세 화면은 준비 중이에요.</p>}
    </Popup>
  )
}

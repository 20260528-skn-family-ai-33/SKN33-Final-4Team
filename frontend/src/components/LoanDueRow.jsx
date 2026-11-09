// 대출 회차 한 장: '11월 9일 납부 · 금액' / 상태 칩 · [납부 거래 확인 ›] (11/9) — 대출 상세 · 전체 완납 팝업이 같이 쓴다
// props: d = recentDues 한 항목, onOpen = 누르면 납부 거래 확인 (없으면 누를 수 없는 줄), tag = 칩 문구를 바꿀 때 (예: '취소돼요')
import { won } from '../utils/money.js'

const DUE_STATE = { unpaid: '아직 확인 안 됨', partial: '일부만 냈어요', paid: '이번 납부 완료', needs_review: '출금 확인 필요' }
const day = (iso) => `${Number(iso.slice(5, 7))}월 ${Number(iso.slice(8, 10))}일`

export default function LoanDueRow({ d, onOpen, tag }) {
  const chip = tag ?? `${DUE_STATE[d.paymentState] ?? '확인 필요'}${d.paidAmount > 0 && d.paymentState !== 'paid' ? ` · ${won(d.paidAmount)}` : ''}`
  const body = <>
    <span className="mc-row"><strong>{day(d.dueDate)} 납부</strong><strong>{won(d.expectedAmount)}</strong></span>
    <span className="mc-row">
      <span className={`mc-due-chip is-${tag ? 'muted' : d.paymentState}`}>{chip}</span>
      {onOpen && <span className="mc-due-go">납부 거래 확인 ›</span>}
    </span>
  </>
  return onOpen
    ? <button type="button" className="mc-loan-due" onClick={onOpen}>{body}</button>
    : <div className="mc-loan-due">{body}</div>
}

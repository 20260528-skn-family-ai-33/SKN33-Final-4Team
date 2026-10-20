// 팝업: 연결 상세 · 신한카드 — 홈 "꼭 나갈 돈"의 카드 청구서
import { Unlink } from 'lucide-react'
import Popup from './Popup.jsx'
import RefreshRow from '../components/RefreshRow.jsx'
import { won } from '../utils/money.js'

const BILL = [
  ['총청구액', '420,000원'],
  ['결제예정일', '10.15'],
  ['출금예정일', '10.15'],
  ['결제계좌', '신한 ****1234'],
  ['· 일시불', '310,000원'],
]
// 청구서의 위험 신호 (CODEF 청구내역 resRevolving · resCashService · resCardLoan · resLateFee). 0원이 아닐 때만 보여 준다
// 시안 예시: 현금서비스 50,000원 (총청구액 420,000 = 일시불 310,000 + 할부 60,000 + 현금서비스 50,000)
const RISKS = [
  { name: '리볼빙', amount: 0, why: '이번 결제액 일부를 다음 달로 넘긴 돈이에요. 이자가 높고 빚이 쌓이기 쉬워요.' },
  { name: '현금서비스', amount: 50_000, why: '카드로 빌린 단기 대출이에요. 이자가 높아요.' },
  { name: '카드론', amount: 0, why: '카드로 빌린 장기 대출이에요. 신용점수에 영향을 줄 수 있어요.' },
  { name: '연체료', amount: 0, why: '지난달 카드값이 늦게 나가서 붙은 돈이에요.' },
]

export default function CardBillPopup() {
  const risks = RISKS.filter((r) => r.amount > 0)
  return (
    <Popup label="연결 상세" sub>
      <div className="mc-eyebrow">카드사 · 청구서 기준</div>
      <h1>신한카드</h1>
      <div className="mc-card">
        <h2>내 카드 2장</h2>
        <div className="mc-keyvalue"><span>신한 Deep Dream</span><strong>****1111 · 체크</strong></div>
        <div className="mc-keyvalue"><span>신한 Mr.Life</span><strong>****2222 · 신용</strong></div>
      </div>
      <div className="mc-card">
        <h2>이번 급여 주기 사용액</h2>
        <div className="mc-money" style={{ fontSize: 24 }}>152,000원</div>
        <p className="mc-note" style={{ padding: 0 }}>승인·취소 내역 기준이에요. 청구액과 달라요.</p>
      </div>
      <div className="mc-card">
        <h2>10월 청구서</h2>
        {BILL.map(([k, v]) => <div className="mc-keyvalue" key={k}><span>{k}</span><strong>{v}</strong></div>)}
        <div className="mc-keyvalue"><span>· 이번 회차 할부</span><strong>60,000원 <small className="mc-muted">(3개월 중 2회차 · 전체 180,000원)</small></strong></div>
        {risks.map((r) => <div className="mc-keyvalue mc-bill-risk-kv" key={r.name}><span>· {r.name}</span><strong>{won(r.amount)}</strong></div>)}
        {risks.length > 0 && (
          <div className="mc-bill-risk" role="note">
            <strong>이자가 높은 항목이 청구됐어요</strong>
            <ul>{risks.map((r) => <li key={r.name}><b>{r.name} {won(r.amount)}</b> · {r.why}</li>)}</ul>
          </div>
        )}
        <p className="mc-note" style={{ paddingBottom: 0 }}>카드사 청구서 하나로 묶어서 보여요. 출금일이 지나도 실제 납부를 확인하기 전에는 납부 완료로 바꾸지 않아요.</p>
      </div>
      {/* 별칭 수정 · 새로고침 · 연결 해제는 기능 연결 전 */}
      <div className="mc-card">
        <div className="mc-keyvalue mc-alias-kv"><span>별칭</span><strong><em>없음</em><button type="button" className="mc-alias-btn">수정</button></strong></div>
      </div>
      <div className="mc-card mc-acct-act">
        <RefreshRow target="신한카드" />
        <button type="button" className="mc-listrow mc-unlink"><Unlink size={20} aria-hidden="true" /><span>카드사 연결 해제</span></button>
      </div>
    </Popup>
  )
}

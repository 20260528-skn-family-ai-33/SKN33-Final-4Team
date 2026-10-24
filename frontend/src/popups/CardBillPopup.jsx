// 팝업: 연결 상세 · 신한카드 — 홈 "꼭 나갈 돈"의 카드 청구서 · 연결 관리의 카드사
// 청구서 칸은 docs/api/cards.md 10번 (GET /api/finance/card-bills/<id>/) 그대로 (결정 Z · AA①)
//   confirmationStatus: estimated 예상 · confirmed 확정 · unknown / null → "확정 여부 확인 필요"
//   scopeType: issuer 카드사 통합 · card 카드 한 장 · unknown / null
//   itemsState: complete 전부 · partial 일부 · unavailable 없음 / null. 일부만 있으면 항목 합계로 총액을 대신하지 않는다
//   billingMonth "YYYY-MM" · issuerName · fetchedAt (AA①)
// 1차 응답에서 뺀 것 (cards.md '이번 응답 제안에서 제외'): 리볼빙 · 현금서비스 · 카드론 · 연체료 위험 요약, 급여 주기 승인 기준 사용액
//   → 화면에도 보여 주지 않는다. 없는 값을 0원이나 '위험 없음'으로 보이지 않게
import { Unlink } from 'lucide-react'
import Popup from './Popup.jsx'
import RefreshRow from '../components/RefreshRow.jsx'
import { usePopup } from './context.js'
import DisconnectPopup from './DisconnectPopup.jsx'
import { won } from '../utils/money.js'

// 시안 예시값 — 카드 청구서 API 가 생기면 응답으로 바꾼다 (총청구액 420,000 = 일시불 360,000 + 할부 이번 회차 60,000)
const BILL = {
  issuerName: '신한카드',
  scopeType: 'issuer',
  billingMonth: '2026-10',
  totalAmount: 420_000,
  confirmationStatus: 'estimated',   // 홈 '꼭 나갈 돈'의 "예상" 배지와 같다
  fetchedAt: '오늘 09:30',
  paymentDueDate: '10.15',
  withdrawalDueDate: '10.15',
  paymentAccount: { displayName: '신한 저축예금 · ****1234', status: 'connected' },
  cards: [
    { cardId: 'c1', displayName: '신한 Deep Dream · ****1111', type: '체크' },
    { cardId: 'c2', displayName: '신한 Mr.Life · ****2222', type: '신용' },
  ],
  itemsState: 'complete',
  items: [
    { id: 'i1', itemType: 'one_time', merchantName: '예시 가전 매장', purchasedOn: '09.15', billedAmount: 360_000 },
    { id: 'i2', itemType: 'installment', merchantName: '예시 생활용품점', purchasedOn: '08.20', billedAmount: 60_000,
      originalPurchaseAmount: 180_000, installmentMonths: 3, installmentRound: 2 },
  ],
}
const STATUS = {
  estimated: ['예상', '청구 금액이 아직 확정되지 않았어요. 카드를 더 쓰거나 취소하면 바뀔 수 있어요.'],
  confirmed: ['확정', null],
  unknown: ['확인 필요', '카드사가 확정 여부를 알려 주지 않았어요.'],
}
const ITEM_TYPE = { one_time: '일시불', installment: '할부', fee: '수수료', interest: '이자', adjustment: '조정', unknown: '기타' }
const ITEMS_NOTE = {
  partial: '카드사가 구매 내역 일부만 보내 줬어요. 총액은 카드사 청구서 기준이에요.',
  unavailable: '카드사가 구매 내역을 보내 주지 않았어요.',
}

// 이 카드사 카드로 나가는 고정지출 → 해제 확인 창에서 같이 그만할지 묻는다 (결정 F)
const CARD_FIXED = [
  { id: 3, type: 'fixed', name: '구독료', line: '매월 5일 · 신한 Deep Dream · ****4321', amount: 15_000 },
  { id: 4, type: 'fixed', name: 'OTT', line: '매월 5일 · 신한 Deep Dream · ****4321', amount: 13_500 },
]

export default function CardBillPopup() {
  const { openPopup } = usePopup()
  const b = BILL
  const [label, why] = STATUS[b.confirmationStatus] ?? STATUS.unknown
  const month = Number(b.billingMonth.split('-')[1])

  return (
    <Popup label="연결 상세" sub>
      <div className="mc-eyebrow">카드사 · {b.scopeType === 'issuer' ? '카드사 통합 청구서' : b.scopeType === 'card' ? '카드 한 장 청구서' : '청구서'}</div>
      <h1>{b.issuerName ?? '카드사'}</h1>
      <div className="mc-card">
        <h2>내 카드 {b.cards?.length ?? 0}장</h2>
        {(b.cards ?? []).map((c) => (
          <div className="mc-keyvalue" key={c.cardId}><span>{c.displayName.split(' · ')[0]}</span><strong>{c.displayName.split(' · ')[1]} · {c.type}</strong></div>
        ))}
      </div>
      <div className="mc-card">
        <div className="mc-row">
          <h2>{month}월 청구서</h2>
          <span className={`mc-bill-st is-${b.confirmationStatus ?? 'unknown'}`}>{label}</span>
        </div>
        <div className="mc-money" style={{ fontSize: 24 }}>{won(b.totalAmount)}</div>
        {why && <p className="mc-note" style={{ padding: '0 0 6px' }}>{why}</p>}
        <div className="mc-keyvalue"><span>결제예정일</span><strong>{b.paymentDueDate ?? '확인 필요'}</strong></div>
        <div className="mc-keyvalue"><span>출금예정일</span><strong>{b.withdrawalDueDate ?? '확인 필요'}</strong></div>
        <div className="mc-keyvalue"><span>결제 계좌</span><strong>{b.paymentAccount?.displayName ?? '확인 필요'}</strong></div>
        <div className="mc-keyvalue"><span>조회</span><strong>{b.fetchedAt ?? '-'}</strong></div>
      </div>
      <div className="mc-card">
        <h2>구매 내역</h2>
        {b.items === null
          ? <p className="mc-muted" style={{ margin: 0 }}>{ITEMS_NOTE.unavailable}</p>
          : b.items.map((it) => (
            <div className="mc-keyvalue" key={it.id}>
              <span>{ITEM_TYPE[it.itemType]} · {it.merchantName ?? '이름 없음'}{it.purchasedOn && <small className="mc-muted"> {it.purchasedOn}</small>}</span>
              <strong>{it.billedAmount == null ? '금액 확인 필요' : won(it.billedAmount)}
                {it.installmentMonths && <small className="mc-muted"> ({it.installmentMonths}개월 중 {it.installmentRound}회차 · 전체 {won(it.originalPurchaseAmount)})</small>}
              </strong>
            </div>
          ))}
        {ITEMS_NOTE[b.itemsState] && b.items !== null && <p className="mc-note" style={{ paddingBottom: 0 }}>{ITEMS_NOTE[b.itemsState]}</p>}
        <p className="mc-note" style={{ paddingBottom: 0 }}>카드사 청구서 하나로 묶어서 보여요. 출금일이 지나도 실제 출금을 확인하기 전에는 납부 완료로 바꾸지 않아요.</p>
      </div>
      {/* 별칭 수정 · 새로고침은 기능 연결 전. 연결 해제는 확인 창까지 (결정 F) */}
      <div className="mc-card">
        <div className="mc-keyvalue mc-alias-kv"><span>별칭</span><strong><em>없음</em><button type="button" className="mc-alias-btn">수정</button></strong></div>
      </div>
      <div className="mc-card mc-acct-act">
        <RefreshRow target="신한카드" />
        <button type="button" className="mc-listrow mc-unlink" onClick={() => openPopup(<DisconnectPopup inst="신한카드" kind="card" related={CARD_FIXED} />)}><Unlink size={20} aria-hidden="true" /><span>카드사 연결 해제</span></button>
      </div>
    </Popup>
  )
}

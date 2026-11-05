// 홈의 청구서 sourceId로 저장된 상세를 조회한다. 구매 내역 일부의 합계로 총액을 대체하지 않는다.
import { Navigate, useNavigate } from 'react-router'
import Popup from './Popup.jsx'
import { useApi } from '../utils/api.js'
import { won } from '../utils/money.js'
import { PATHS } from '../routes.js'

const STATUS = {
  estimated: ['예상', '청구 금액이 아직 확정되지 않았어요. 카드를 더 쓰거나 취소하면 바뀔 수 있어요.'],
  confirmed: ['확정', null],
  unknown: ['확인 필요', '카드사가 확정 여부를 알려 주지 않았어요.'],
}
const ITEM_TYPE = { one_time: '일시불', installment: '할부', fee: '수수료', interest: '이자', adjustment: '조정', unknown: '기타' }
const money = (value) => value == null ? '금액 확인 필요' : won(value)
const when = (iso) => iso ? new Date(iso).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) : '조회 기록 없음'

export default function CardBillPopup({ billId }) {
  const navigate = useNavigate()
  const { data: b, error, reload } = useApi(`/api/finance/card-bills/${billId}/`)
  if (error?.code === 'not_authenticated') return <Navigate to={PATHS.login} replace />
  if (error || !b) return (
    <Popup label="카드 청구서 상세" sub>
      <h2>카드 청구서 상세</h2>
      {error ? <>
        <p className="mc-error" role="alert">{error.code === 'consent_required' ? '청구서를 보려면 카드 조회 동의가 필요해요.' : error.code === 'not_found' ? '청구서를 찾을 수 없어요. 홈에서 다시 불러온 뒤 확인해 주세요.' : error.status ? error.message : '청구서를 불러오지 못했어요. 다시 시도해 주세요.'}</p>
        {error.code === 'consent_required' ? <button type="button" className="mc-secondary" onClick={() => navigate(PATHS.connect)}>연결 화면에서 동의하기</button> : <button type="button" className="mc-secondary" onClick={reload}>다시 시도</button>}
      </> : <p role="status">청구서를 불러오는 중…</p>}
    </Popup>
  )
  const [label, why] = STATUS[b.confirmationStatus] ?? STATUS.unknown
  return (
    <Popup label="카드 청구서 상세" sub>
      <div className="mc-eyebrow">{b.scopeType === 'issuer' ? '카드사 통합 청구서' : b.scopeType === 'card' ? '카드 한 장 청구서' : '카드 청구서'}</div>
      <h1>{b.issuerName ?? '카드사 확인 필요'}</h1>
      <div className="mc-card">
        <h2>포함된 카드</h2>
        {b.cards == null ? <p className="mc-muted">포함된 카드 목록을 확인할 수 없어요.</p> : b.cards.length === 0 ? <p className="mc-muted">표시할 카드가 없어요.</p> : b.cards.map((card) => <div className="mc-keyvalue" key={card.cardId}><span>{card.displayName}</span><strong>{card.status === 'relogin' ? '다시 연결 필요' : card.status === 'disconnected' ? '연결 해제됨' : ''}</strong></div>)}
      </div>
      <div className="mc-card">
        <div className="mc-row"><h2>{b.billingMonth ?? '청구월 확인 필요'} 청구서</h2><span className={`mc-bill-st is-${b.confirmationStatus ?? 'unknown'}`}>{label}</span></div>
        <div className="mc-money" style={{ fontSize: 24 }}>{money(b.totalAmount)}</div>
        {why && <p className="mc-note">{why}</p>}
        <div className="mc-keyvalue"><span>결제예정일</span><strong>{b.paymentDueDate ?? '확인 필요'}</strong></div>
        <div className="mc-keyvalue"><span>출금예정일</span><strong>{b.withdrawalDueDate ?? '확인 필요'}</strong></div>
        <div className="mc-keyvalue"><span>결제 계좌</span><strong>{b.paymentAccount?.displayName ?? '확인 필요'}</strong></div>
        <div className="mc-keyvalue"><span>조회 시각</span><strong>{when(b.fetchedAt)}</strong></div>
      </div>
      <div className="mc-card">
        <h2>구매 내역</h2>
        {b.itemsState === 'partial' && <p className="mc-note">구매 내역 일부만 조회됐어요. 총청구액은 카드사 청구서 기준이에요.</p>}
        {b.items == null || b.itemsState === 'unavailable' ? <p className="mc-muted">구매 내역을 확보하지 못했어요.</p> : b.items.length === 0 ? <p className="mc-muted">{b.itemsState === 'complete' ? '구매 내역이 없어요.' : '조회된 구매 내역이 없어요. 전체 내역이 없다는 뜻은 아니에요.'}</p> : null}
        {(b.items ?? []).map((item) => (
          <div className="mc-keyvalue" key={item.billItemId}>
            <span>{ITEM_TYPE[item.itemType] ?? '기타'} · {item.merchantName ?? '이름 없음'}{item.purchasedOn && <small className="mc-muted"> {item.purchasedOn}</small>}{item.card && <small className="mc-muted"> · {item.card.displayName}</small>}</span>
            <strong>{money(item.billedAmount)}
              {item.installmentMonths != null && <small className="mc-muted"> ({item.installmentMonths}개월 · {item.installmentRound == null ? '회차 확인 필요' : `${item.installmentRound}회차`} · 전체 {money(item.originalPurchaseAmount)})</small>}
            </strong>
          </div>
        ))}
        <p className="mc-note">출금일이 지나도 실제 출금을 확인하기 전에는 납부 완료로 바꾸지 않아요.</p>
      </div>
    </Popup>
  )
}

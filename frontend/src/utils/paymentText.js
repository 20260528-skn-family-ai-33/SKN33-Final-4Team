// 납부 거래 확인 팝업 공통 문구 (대출 LoanPaymentPopup · 카드 CardPaymentPopup). 서버 cancelEffect · released 그대로 안내
import { catName } from './categories.js'

export const TYPE = { spending: '소비', transfer: '이체', card_payment: '카드값' }
export const typeLabel = (type, category) => `${TYPE[type] ?? type}${category ? ` · ${catName(category)}` : ''}`
// 연결 취소하면 분류가 어떻게 되는지 (서버 cancelEffect)
export const effectText = (e) => e?.kind === 'restore' ? `취소하면 분류가 ‘${typeLabel(e.toType, e.toCategory)}’로 돌아가요.`
  : e?.kind === 'keep' ? `취소해도 지금 분류 ‘${typeLabel(e.toType, e.toCategory)}’는 그대로예요.`
  : e?.kind === 'choose' ? '원래 분류 기록이 없어요. 취소할 때 이 거래를 무엇으로 둘지 골라 주세요.' : ''
// 응답 released → 실제로 한 일 그대로 안내 (복원하지 않았는데 복원했다고 하지 않음)
export const releasedText = (r) => !r ? '납부 연결을 취소했어요.'
  : r.classification === 'restored' ? `납부 연결을 취소하고 분류를 ‘${typeLabel(r.transactionType, r.category)}’로 되돌렸어요.`
  : r.classification === 'chosen' ? `납부 연결을 취소하고 분류를 ‘${typeLabel(r.transactionType, r.category)}’로 바꿨어요.`
  : '납부 연결을 취소했어요. 분류는 지금 값 그대로예요.'
// 카드 청구서 납부 상태 (서버 paymentState). unpaid 는 '안 냄'이 아니라 연결된 출금이 아직 없음
export const CARD_PAY_STATE = { unpaid: '출금 확인 중', partial: '일부 납부 확인', paid: '납부 완료', needs_review: '납부 여부 확인 필요' }

// 거래 API 한 줄 → 화면 · 팝업(TransactionPopup)이 쓰는 모양. 거래 내역 · 고정지출 화면이 같이 쓴다
import { SPEND_CATS, ETC, catIcon } from './categories.js'

export const CHIPS = [...SPEND_CATS, ETC]  // 카테고리 칩 (확인 필요는 따로) · 사용자가 거래 카테고리를 고를 때도 이 목록
const KIND = { spending: 'normal', income: 'income', transfer: 'transfer' }

// API 한 줄 → 화면 · 팝업이 쓰는 모양
export function toView(item) {
  const kind = KIND[item.transactionType] ?? 'normal'
  return {
    id: item.transactionId,
    kind: item.classifyState === 'needs_review' ? 'pending' : kind,
    icon: kind === 'income' ? 'Banknote' : kind === 'transfer' ? 'Send' : catIcon(item.categoryName),
    name: item.merchantName,
    cat: item.categoryName,
    amount: item.amount,
    net: item.netAmount,
    cancel: item.cancel.state === 'none' ? null : item.cancel.state,  // null · 'partial' · 'full'
    cancelAmount: item.cancel.amount,
    via: (item.paymentCard ?? item.paymentAccount)?.displayName,
    funding: item.funding,
  }
}


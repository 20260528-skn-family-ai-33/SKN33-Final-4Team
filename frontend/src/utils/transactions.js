// 거래 API 한 줄 → 화면 · 팝업(TransactionPopup)이 쓰는 모양. 거래 내역 · 고정지출 화면이 같이 쓴다
import { SPEND_CATS, ETC, catIcon } from './categories.js'

export const CHIPS = [...SPEND_CATS, ETC]  // 카테고리 칩 (확인 필요는 따로) · 사용자가 거래 카테고리를 고를 때도 이 목록
const KIND = { spending: 'normal', income: 'income', transfer: 'transfer', own_transfer: 'transfer', saving: 'transfer', not_spending: 'excluded' }  // excluded = '소비가 아니에요'로 뺀 소비 (11/13)  // 내 계좌 이체 판단이 있는 거래만 목록에 옴 (R11)

// '신한 주거래 우대통장 · ****0303' → ['신한 주거래 우대통장', '****0303'] (끝 4자리는 아래 줄 회색으로 쓰는 화면용)
export const splitName = (name) => {
  const at = name.lastIndexOf(' · ')
  return at < 0 ? [name, null] : [name.slice(0, at), name.slice(at + 3)]
}

// 목록 한 줄용 짧은 결제 수단: '신한 Deep Dream 체크 · ****1234' → '신한 1234' (상세 팝업은 전체 이름 via)
export function shortVia(name) {
  if (!name) return name
  const [label, tail] = name.split(' · ****')
  return [label.split(' ')[0], tail].filter(Boolean).join(' ')
}

// API 한 줄 → 화면 · 팝업이 쓰는 모양
export function toView(item) {
  const kind = KIND[item.transactionType] ?? 'normal'
  return {
    id: item.transactionId,
    kind: item.classifyState === 'needs_review' ? 'pending' : kind,
    icon: kind === 'income' ? 'Banknote' : kind === 'transfer' ? 'Send' : kind === 'excluded' ? 'CircleMinus' : catIcon(item.categoryName),
    name: item.merchantName,
    cat: item.categoryName,
    amount: item.amount,
    net: item.netAmount,
    cancel: item.cancel.state === 'none' ? null : item.cancel.state,  // null · 'partial' · 'full'
    cancelAmount: item.cancel.amount,
    via: (item.paymentCard ?? item.paymentAccount)?.displayName,
    viaShort: shortVia((item.paymentCard ?? item.paymentAccount)?.displayName),
    funding: item.funding,
    isSalary: item.isSalary === true,  // 사용자가 확인했거나 자동 인식된 월급만 (R12)
    salaryAuto: item.salaryReview === 'auto',  // 확인한 월급과 같은 패턴이라 자동 인식
    isOwn: item.transactionType === 'own_transfer' || item.transactionType === 'saving',
    isSaving: item.transactionType === 'saving',  // 자동 판단 뒤 저축으로 처리된 출금 (R11)
    ownRecheck: item.ownTransferRecheck === true,  // 늦게 온 거래로 자동 판단이 모호해짐
    ownTransfer: item.ownTransferReview ?? null,  // 내 계좌 이체 판단 suggested · auto · confirmed · rejected (R11)
  }
}


// 고정지출 화면 · 결제 고르기 팝업이 같이 쓰는 것 (docs/api/fixed.md)
import { apiSend } from './api.js'

// "2026-10-05" → "10.05"
export const shortDate = (iso) => iso.slice(5).replace('-', '.')

// 서버가 제안한 결제 · 출금을 근거 거래로 연결. 계좌 회차(dueId)가 있으면 그 회차에 붙인다
export function pickEvidence(item, transactionId) {
  const dueId = item.evidenceSuggestion?.dueId
  return apiSend(`/api/budgets/fixed/${item.recurringId}/`, 'PATCH',
    { evidenceTransactionId: transactionId, ...(dueId ? { evidenceDueId: dueId } : {}) })
}

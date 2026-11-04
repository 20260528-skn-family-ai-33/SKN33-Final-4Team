// 고정지출 · 저축 화면과 팝업이 같이 쓰는 것 (docs/api/fixed.md · savings.md)
import { useState } from 'react'
import { apiSend, useApi } from './api.js'
import { payStatus } from './payOptions.js'

export { payOptionsNote } from './payOptions.js'

// "2026-10-05" → "10.05"
export const shortDate = (iso) => iso.slice(5).replace('-', '.')

// 서버가 제안한 결제 · 출금을 근거 거래로 연결. 계좌 회차(dueId)가 있으면 그 회차에 붙인다
export function pickEvidence(item, transactionId) {
  const dueId = item.evidenceSuggestion?.dueId
  return apiSend(`/api/budgets/fixed/${item.recurringId}/`, 'PATCH',
    { evidenceTransactionId: transactionId, ...(dueId ? { evidenceDueId: dueId } : {}) })
}

// 결제 수단 후보(금융 연결 계좌 · 카드 목록) 읽기 — 실패를 빈 목록으로 바꾸지 않는다 (N-F2)
// → { items, status, retry, retrying }
//   status: loading(받는 중) · ready(받음, items 는 서버 목록 그대로 — 고를 수 있는지는 부르는 쪽이 거름) ·
//           consent(조회 동의 필요) · login(로그인 풀림) · error(서버 · 네트워크 오류)
// 다시 받기: useApi 는 주소가 바뀔 때만 다시 받아서 서버가 무시하는 ?v= 를 바꾼다 (공통 api.js 는 그대로)
export function usePayOptions(path) {
  const [version, setVersion] = useState(0)
  const [retriedOn, setRetriedOn] = useState(null)  // 다시 받기를 누를 때 보이던 오류 — 새 응답이 오면 달라진다
  const { data, error } = useApi(`${path}?v=${version}`)
  const retrying = retriedOn !== null && retriedOn === error
  const status = payStatus(data, error, retrying)
  const retry = () => { setRetriedOn(error); setVersion((v) => v + 1) }
  return { items: status === 'ready' ? data.items : [], status, retry, retrying }
}

// 새로고침 후에도 불명확한 승인은 같은 요청으로 확인한다. 금액·예산 내용은 저장하지 않는다.
const SAVED = 'mc-budget-approval-pending'
const text = (value, max) => typeof value === 'string' && value.length > 0 && value.length <= max && value.trim() === value
const identifier = (value) => text(value, 128) && /^[A-Za-z0-9_-]+$/.test(value)

export function approvalPath(proposal) {
  if (!identifier(proposal?.proposalId)) return null
  const id = encodeURIComponent(proposal.proposalId)
  if (proposal.mode === 'INITIAL_ADJUSTMENT') return `/api/budgets/${id}/approve/`
  if (proposal.mode === 'NORMAL_ADJUSTMENT') return `/api/budgets/proposals/${id}/approve/`
  return null
}

function valid(record) {
  return record && typeof record === 'object' && Object.keys(record).sort().join(',') === 'body,conversationId,key,path,proposalId'
    && identifier(record.conversationId) && identifier(record.proposalId) && text(record.key, 64)
    && record.body && Object.keys(record.body).join(',') === 'keepForNextCycle' && typeof record.body.keepForNextCycle === 'boolean'
    && ['INITIAL_ADJUSTMENT', 'NORMAL_ADJUSTMENT'].some((mode) => record.path === approvalPath({ proposalId: record.proposalId, mode }))
}

export function readPendingApproval() {
  try {
    const record = JSON.parse(sessionStorage.getItem(SAVED))
    if (valid(record)) return record
    sessionStorage.removeItem(SAVED)
  } catch {
    try { sessionStorage.removeItem(SAVED) } catch { /* 저장소를 못 읽은 기록은 승인 근거로 사용하지 않는다. */ }
  }
  return null
}

export function rememberPendingApproval(conversationId, request) {
  const record = { conversationId, proposalId: request.proposal.proposalId, path: request.path, key: request.key,
    body: { keepForNextCycle: request.body.keepForNextCycle } }
  try {
    if (!valid(record)) throw new Error('invalid approval')
    sessionStorage.setItem(SAVED, JSON.stringify(record))
  } catch {
    throw Object.assign(new Error('확정 정보를 보관하지 못했어요. 브라우저 저장 공간을 허용한 뒤 다시 시도해 주세요.'), { status: 400, code: 'approval_storage_unavailable' })
  }
}

export function forgetPendingApproval(conversationId, proposalId) {
  const record = readPendingApproval()
  if (record?.conversationId !== conversationId || record?.proposalId !== proposalId) return
  try { sessionStorage.removeItem(SAVED) } catch { /* 지우지 못한 기록도 다음 조회에서 서버 상태로 판정한다. */ }
}

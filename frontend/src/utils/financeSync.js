// 금융 정보 다시 불러오기 — POST /api/finance/sync/ → 202 { taskId } → GET /api/tasks/<taskId>/ (docs/api/finance-connect.md)
// 11/09: 서버가 작업을 뒤에서 돌리고 바로 답한다 (카드 업종 조회로 1분 넘게 걸림). 진행 중이면 1초씩 다시 본다 (최대 5분)
// 작업이 succeeded 여도 기관별로 실패할 수 있다 → result.items[].status · error 를 화면에 보여 준다 (실패를 0건으로 보이지 않기)
import { apiSend } from './api.js'

const WAIT_MS = 1000
const MAX_TRIES = 300

// 작업 끝날 때까지 GET /api/tasks/<id>/ → 성공이면 result. 실패 · 시간 초과면 오류. signal 로 화면을 떠나면 멈춘다
export async function waitTask(taskId, { signal } = {}) {
  for (let i = 0; i < MAX_TRIES; i++) {
    if (signal?.aborted) throw new DOMException('aborted', 'AbortError')
    const res = await fetch(`/api/tasks/${taskId}/`, { headers: { Accept: 'application/json' }, signal })
    const task = await res.json().catch(() => null)
    if (!res.ok) throw new Error(task?.error?.message ?? `서버 응답 ${res.status}`)
    if (task.status === 'succeeded') return task.result
    if (task.status === 'failed') throw new Error(task.error?.message ?? '불러오지 못했어요. 잠시 후 다시 시도해 주세요.')
    await new Promise((r) => setTimeout(r, WAIT_MS))
  }
  throw new Error('불러오기가 아직 끝나지 않았어요. 잠시 후 다시 확인해 주세요.')
}

export async function runFinanceSync(body = {}) {
  const started = await apiSend('/api/finance/sync/', 'POST', body).catch((err) => {
    // 400 은 보여 줄 문장이 칸 오류에 있고("연결된 기관이 없어요…"), 서버에 못 닿으면 브라우저 영어 문장이 온다
    err.message = Object.values(err.fields ?? {}).flat()[0] ?? (err.status ? err.message : '서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.')
    throw err
  })
  return waitTask(started.taskId)
}

// 기관별 결과 한 줄 — { name, ok, text }
export function syncLines(result) {
  return (result?.items ?? []).map((it) => {
    if (it.status === 'failed') return { name: it.organizationName, ok: false, text: it.error?.message ?? '불러오지 못했어요.' }
    if (it.status === 'skipped') return { name: it.organizationName, ok: true, text: '불러올 항목이 없어요 (동의하지 않았거나 준비 중)' }
    if (it.cardReviewCount > 0) return { name: it.organizationName, ok: true, text: `내역을 불러왔어요. 결제 카드를 확인할 거래 ${it.cardReviewCount}건이 있어요.` }
    if (it.approvalsComplete === false) return { name: it.organizationName, ok: false, text: '일부 카드 사용 내역을 확인하지 못했어요. 조회 기간과 금액·취소 정보를 확인해 주세요.' }
    const n = it.newTransactionCount
    // 11/09: 원화 금액이 없는 해외 결제는 청구서 · 체크카드 계좌 출금으로 금액을 채우고, 못 채운 건만 빠졌다고 알린다
    const parts = [n == null ? '불러왔어요' : `새 거래 ${n}건을 불러왔어요`]
    if (it.foreignFilledCount > 0) parts.push(`해외 결제 ${it.foreignFilledCount}건은 청구서·계좌 출금으로 원화 금액을 확인했어요`)
    if (it.foreignPendingCount > 0) parts.push(`해외 결제 ${it.foreignPendingCount}건은 원화 금액을 찾지 못해 빠졌어요`)
    return { name: it.organizationName, ok: true, text: parts.length > 1 ? `${parts.join('. ')}.` : parts[0] }
  })
}

// 카드번호 · 카드 비밀번호가 있어야 불러올 수 있는 결과 (현대카드 등) → 홈에서 입력 팝업 (CardCredentialPopup)
export const needsCardValues = (item) => item?.status === 'failed' && Boolean(item.error?.fields?.cardNo || item.error?.fields?.cardPassword)

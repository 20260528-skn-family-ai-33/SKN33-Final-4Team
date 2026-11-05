// 금융 정보 다시 불러오기 — POST /api/finance/sync/ → 202 { taskId } → GET /api/tasks/<taskId>/ (docs/api/finance-connect.md)
// 1차 서버는 요청 안에서 끝까지 처리해서 보통 바로 succeeded 다. 진행 중이면 1초씩 다시 본다
// 작업이 succeeded 여도 기관별로 실패할 수 있다 → result.items[].status · error 를 화면에 보여 준다 (실패를 0건으로 보이지 않기)
import { apiSend } from './api.js'

const WAIT_MS = 1000
const MAX_TRIES = 30

export async function runFinanceSync(body = {}) {
  const started = await apiSend('/api/finance/sync/', 'POST', body).catch((err) => {
    // 400 은 보여 줄 문장이 칸 오류에 있고("연결된 기관이 없어요…"), 서버에 못 닿으면 브라우저 영어 문장이 온다
    err.message = Object.values(err.fields ?? {}).flat()[0] ?? (err.status ? err.message : '서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.')
    throw err
  })
  for (let i = 0; i < MAX_TRIES; i++) {
    const res = await fetch(`/api/tasks/${started.taskId}/`, { headers: { Accept: 'application/json' } })
    const task = await res.json().catch(() => null)
    if (!res.ok) throw new Error(task?.error?.message ?? `서버 응답 ${res.status}`)
    if (task.status === 'succeeded') return task.result
    if (task.status === 'failed') throw new Error(task.error?.message ?? '불러오지 못했어요. 잠시 후 다시 시도해 주세요.')
    await new Promise((r) => setTimeout(r, WAIT_MS))
  }
  throw new Error('불러오기가 아직 끝나지 않았어요. 잠시 후 다시 확인해 주세요.')
}

// 기관별 결과 한 줄 — { name, ok, text }
export function syncLines(result) {
  return (result?.items ?? []).map((it) => {
    if (it.status === 'failed') return { name: it.organizationName, ok: false, text: it.error?.message ?? '불러오지 못했어요.' }
    if (it.status === 'skipped') return { name: it.organizationName, ok: true, text: '불러올 항목이 없어요 (동의하지 않았거나 준비 중)' }
    if (it.cardReviewCount > 0) return { name: it.organizationName, ok: true, text: `내역을 불러왔어요. 결제 카드를 확인할 거래 ${it.cardReviewCount}건이 있어요.` }
    if (it.approvalsComplete === false) return { name: it.organizationName, ok: false, text: '일부 카드 사용 내역을 확인하지 못했어요. 조회 기간과 금액·취소 정보를 확인해 주세요.' }
    const n = it.newTransactionCount
    return { name: it.organizationName, ok: true, text: n == null ? '불러왔어요' : `새 거래 ${n}건을 불러왔어요` }
  })
}

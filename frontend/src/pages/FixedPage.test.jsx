import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import FixedPage from './FixedPage.jsx'
import { apiSend } from '../utils/api.js'

vi.mock('../utils/api.js', async (original) => ({ ...await original(), apiSend: vi.fn(), newRequestKey: () => 'fixed-key' }))
vi.mock('react-router', () => ({ useLocation: () => ({}), useNavigate: () => vi.fn() }))
vi.mock('../popups/context.js', () => ({ usePopup: () => ({ openPopup: vi.fn() }) }))
vi.mock('../components/Layout.jsx', () => ({ default: ({ children, foot }) => <main>{children}{foot}</main> }))

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root, serverData
const response = () => ({ ok: true, json: async () => structuredClone(serverData) })
const button = (text) => [...host.querySelectorAll('button')].find((b) => b.textContent === text)
const amountRow = (text) => [...host.querySelectorAll('.mc-keyvalue')].find((row) => row.textContent.includes(text))?.textContent

beforeEach(() => {
  vi.clearAllMocks()
  serverData = {
    items: [{ recurringId: 'fixed-1', name: '구독료', amount: 12000, status: 'suggested', sourceType: 'detected' }],
    summary: { confirmedMonthlyTotal: 0, suggestedCount: 1 },
    cardBills: [{ issuerCode: '0301', issuerName: '테스트카드', totalAmount: 100000,
      confirmedFixedAmount: 0, amountAfterFixed: 100000, isEstimated: false, reason: null }],
  }
  vi.stubGlobal('fetch', vi.fn(async () => response()))
  apiSend.mockImplementation(async () => {
    serverData.items[0].status = 'confirmed'
    serverData.summary = { confirmedMonthlyTotal: 12000, suggestedCount: 0 }
    serverData.cardBills[0].confirmedFixedAmount = 12000
    serverData.cardBills[0].amountAfterFixed = 88000
    return { items: serverData.items }
  })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(() => root.unmount())
  host.remove()
  vi.unstubAllGlobals()
})

it('개별 확정 후 서버의 확정 합계와 고정비 제외 청구금액을 함께 갱신한다', async () => {
  await act(() => root.render(<FixedPage />))
  expect(amountRow('확정한 고정지출 합계')).toContain('0원')
  await act(() => button('고정지출 맞아요').click())
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/fixed/confirm/', 'POST', { recurringIds: ['fixed-1'] }, { requestKey: 'fixed-key' })
  expect(amountRow('확정한 고정지출 합계')).toContain('12,000원')
  expect(amountRow('고정비 제외 청구금액')).toContain('88,000원')
  expect(button('고정지출 맞아요')).toBeUndefined()
  expect(button('계속하기')).toBeTruthy()
})

it('확정 실패 시 합계를 올리지 않고 오류와 재시도 버튼을 유지한다', async () => {
  apiSend.mockRejectedValue(new Error('저장하지 못했어요.'))
  await act(() => root.render(<FixedPage />))
  await act(() => button('고정지출 맞아요').click())
  expect(host.querySelector('[role=alert]').textContent).toContain('저장하지 못했어요.')
  expect(amountRow('확정한 고정지출 합계')).toContain('0원')
  expect(amountRow('고정비 제외 청구금액')).toContain('100,000원')
  expect(button('고정지출 맞아요').disabled).toBe(false)
})

it('재조회가 끝나기 전에는 재확정과 다음 이동을 막는다', async () => {
  await act(() => root.render(<FixedPage />))
  let resolveRead
  fetch.mockImplementationOnce(() => new Promise((resolve) => { resolveRead = resolve }))
  await act(() => button('고정지출 맞아요').click())
  expect(button('고정지출 맞아요').disabled).toBe(true)
  expect(button('저장 중…').disabled).toBe(true)
  await act(() => button('고정지출 맞아요').click())
  expect(apiSend).toHaveBeenCalledOnce()
  await act(() => resolveRead(response()))
  expect(button('계속하기').disabled).toBe(false)
})

it('확인하지 못한 청구서와 확인된 0원 청구서를 구분한다', async () => {
  serverData.cardBills[0].amountAfterFixed = null
  serverData.cardBills[0].totalAmount = null
  serverData.cardBills[0].reason = 'bill_unavailable'
  serverData.cardBills.push({ issuerCode: '0302', issuerName: '다른카드', totalAmount: 0,
    confirmedFixedAmount: 0, amountAfterFixed: 0, isEstimated: true, reason: null })
  await act(() => root.render(<FixedPage />))
  expect(host.textContent).toContain('청구서 확인 필요')
  expect(amountRow('고정비 제외 청구금액')).toContain('0원')
  expect(host.textContent).toContain('예상·미확정 포함')
})

it.each([0, 10000])('청구금액 %i원보다 월 고정비가 커도 실제 제외 표시는 청구금액을 넘지 않는다', async (total) => {
  Object.assign(serverData.cardBills[0], { totalAmount: total, confirmedFixedAmount: 33309, amountAfterFixed: 0 })
  await act(() => root.render(<FixedPage />))
  expect(host.textContent).not.toContain('확정한 카드 고정비 (월)')
  expect(amountRow('이번 청구금액에서 제외')).toContain(`−${total.toLocaleString('ko-KR')}원`)
  expect(amountRow('고정비 제외 청구금액')).toContain('0원')
  if (total === 0) expect(host.textContent).toContain('추가로 빼는 금액은 없어요')
})

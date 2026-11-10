// 직접 등록 대출 정보 고치기 · 전체 완납 확인 팝업 (R19) — 합성 데이터
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import LoanEditPopup from './LoanEditPopup.jsx'
import LoanPayoffPopup from './LoanPayoffPopup.jsx'
import { apiSend } from '../utils/api.js'

const { closePopup } = vi.hoisted(() => ({ closePopup: vi.fn() }))
vi.mock('../utils/api.js', () => ({ apiSend: vi.fn(), newRequestKey: () => 'payoff-key' }))
vi.mock('./context.js', () => ({ usePopup: () => ({ closePopup }) }))
vi.mock('react-router', () => ({ useNavigate: () => vi.fn() }))
vi.mock('../routes.js', () => ({ PATHS: { login: '/login' } }))

const due = (id, dueDate, paymentState, paidAmount = 0) => ({ dueId: id, dueDate, expectedAmount: 300_000, paidAmount, paymentState })
const loan = {
  loanId: 'loan-1', lenderName: '합성재단', productName: '합성 학자금', origin: 'manual', manualBalance: 5_000_000,
  manualRate: 1.7, maturityDate: null,
  recentDues: [due('past', '2026-11-10', 'needs_review'), due('future', '2026-11-20', 'unpaid'),
    due('ahead', '2026-11-22', 'partial', 100_000), due('done', '2026-10-10', 'paid', 300_000)],
}

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-11-12T03:00:00Z'))
  apiSend.mockResolvedValue({ ...loan, status: 'repaid' })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(() => root.unmount())
  host.remove()
  vi.useRealTimers()
})

const button = (text) => [...host.querySelectorAll('button')].find((b) => b.textContent.includes(text))

it('완납 확인은 이번 회차 납부와 구분해 안내하고, 취소될 회차와 남는 회차를 나눠 보여 준다', async () => {
  const onDone = vi.fn()
  const onCheckDue = vi.fn()
  await act(() => root.render(<LoanPayoffPopup loan={loan} onDone={onDone} onCheckDue={onCheckDue} />))
  const text = host.textContent
  expect(text).toContain('상환을 끝내는 처리')
  expect(text).toContain('[납부 거래 확인]')
  expect(text.split('그대로 남는 납부')[0]).toContain('2026-11-20')     // 미래 · 안 낸 회차만 취소
  const kept = text.split('그대로 남는 납부')[1]
  expect(kept).toContain('2026-11-10')   // 지난 미납
  expect(kept).toContain('2026-11-22')   // 미리 일부 낸 회차
  expect(kept).not.toContain('2026-10-10')
  await act(async () => button('납부 거래 확인').click())
  expect(onCheckDue).toHaveBeenCalledWith('past')
  expect(apiSend).not.toHaveBeenCalled()
  await act(async () => button('전부 갚았어요').click())
  expect(apiSend).toHaveBeenCalledWith('/api/finance/loans/loan-1/payoff/', 'POST', {}, { requestKey: 'payoff-key' })
  expect(onDone).toHaveBeenCalled()
})

it('정보 고치기는 바꾼 칸만 보내고 잔액 0원도 보낸다', async () => {
  const onSaved = vi.fn()
  await act(() => root.render(<LoanEditPopup loan={loan} onSaved={onSaved} />))
  const [lender] = host.querySelectorAll('input')
  const setValue = (input, value) => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  }
  await act(async () => setValue(lender, '새 재단'))
  const balance = host.querySelectorAll('input')[2]
  await act(async () => setValue(balance, '0'))
  await act(async () => host.querySelector('form').requestSubmit())
  expect(apiSend).toHaveBeenCalledWith('/api/finance/loans/loan-1/', 'PATCH', { lenderName: '새 재단', manualBalance: 0 })
  expect(onSaved).toHaveBeenCalled()
})

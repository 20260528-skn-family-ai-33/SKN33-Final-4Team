// 카드 청구서 '앞으로 얼마를 더 내야 하나요?' 확인 — 합성 데이터. 거래 선택 없이 저장 · 일부 입력 · 저장 실패 유지 · 확인 뒤 결과와 수정만
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import CardBillCheck from './CardBillCheck.jsx'
import { apiSend } from '../utils/api.js'

const { openPopup } = vi.hoisted(() => ({ openPopup: vi.fn() }))
vi.mock('../utils/api.js', () => ({ apiSend: vi.fn(), useApi: vi.fn(), newRequestKey: () => 'k' }))
vi.mock('../popups/context.js', () => ({ usePopup: () => ({ openPopup, closePopup: vi.fn() }) }))

const bill = (extra = {}) => ({ billId: 'bill-1', issuerCode: '0302', billingMonth: '2026-11', dueDate: '2026-11-05', totalAmount: 511_515,
  status: 'unchecked', remainingAmount: null, transactionPaidAmount: 0, recheckReason: null, userCheck: null, needsCheck: true, ...extra })

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
const onSaved = vi.fn()
beforeEach(() => {
  vi.clearAllMocks()
  apiSend.mockResolvedValue({})
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(() => root.unmount())
  host.remove()
})
const button = (text) => [...host.querySelectorAll('button')].find((b) => b.textContent === text)
const click = (el) => act(async () => el.click())
const render = (b) => act(() => root.render(<CardBillCheck key={b.status + (b.userCheck?.answer ?? "")} bill={b} issuerName="현대카드" onSaved={onSaved} />))
const URL = '/api/finance/card-bills/bill-1/payment-check/'

it('청구액을 보여 주고 거래를 고르지 않고 답을 저장한다', async () => {
  await render(bill())
  expect(host.querySelector('.mc-bill-head').textContent).toBe('현대카드11월 청구 · 결제일 11.05511,515원')
  expect(host.textContent).toContain('결제일 11.05')
  for (const [text, answer] of [['아직 안 냈어요', 'unpaid'], ['전부 냈어요', 'paid'], ['나중에 확인', 'later']]) {
    await click(button(text))
    expect(apiSend).toHaveBeenLastCalledWith(URL, 'PUT', { answer })
  }
  expect(onSaved).toHaveBeenCalledTimes(3)
})

it('일부 냈어요는 앞으로 더 낼 금액을 받아 저장하고, 실패하면 입력을 유지한다', async () => {
  apiSend.mockRejectedValueOnce(Object.assign(new Error('x'), { status: 400, fields: { remainingAmount: ['1원 이상 511,514원 이하로 입력해 주세요.'] } }))
  await render(bill())
  await click(button('일부 냈어요'))
  const input = host.querySelector('input[aria-label="앞으로 더 낼 금액"]')
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '200,000')
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await click(button('저장'))
  expect(apiSend).toHaveBeenLastCalledWith(URL, 'PUT', { answer: 'partial', remainingAmount: 200000 })
  expect(host.querySelector('[role=alert]').textContent).toContain('511,514원 이하')
  expect(host.querySelector('input[aria-label="앞으로 더 낼 금액"]').value).toBe('200,000')
  expect(onSaved).not.toHaveBeenCalled()
  await click(button('저장'))
  expect(onSaved).toHaveBeenCalledOnce()
})

it('확인한 청구서는 결과와 수정만 보이고 사용자 확인 · 계좌 출금 확인을 구분한다', async () => {
  await render(bill({ status: 'user_confirmed', remainingAmount: 0, needsCheck: false, userCheck: { answer: 'paid' } }))
  expect(host.querySelector('.mc-bill-result').textContent).toBe('앞으로 낼 금액 0원사용자 확인수정')
  expect(button('전부 냈어요')).toBeUndefined()
  await click(button('수정'))
  expect(button('전부 냈어요')).toBeTruthy()
  await render(bill({ status: 'paid_by_transactions', remainingAmount: 0, needsCheck: false, transactionPaidAmount: 511_515 }))
  expect(host.textContent).toContain('계좌 출금으로 확인')
  await click(button('수정'))
  expect(openPopup).toHaveBeenCalledOnce()   // 출금 근거 정정은 거래로 확인하기에서
})

it('재확인 · 나중에 확인을 안내하고 거래로 확인하기는 보조 경로로 남긴다', async () => {
  await render(bill({ status: 'recheck', recheckReason: 'bill_changed', userCheck: { answer: 'paid' } }))
  expect(host.textContent).toContain('확인한 뒤 청구액이 바뀌었어요')
  await render(bill({ userCheck: { answer: 'later' } }))
  expect(host.textContent).toContain('나중에 확인하기로 했어요')
  await click(button('거래로 확인하기'))
  expect(openPopup).toHaveBeenCalledOnce()
})

it('11/06: 청구 총액과 상세 합계가 다르면 낼 금액 없음이라 하지 않고 질문도 하지 않는다', async () => {
  await render(bill({ totalAmount: 0, status: 'total_mismatch', recheckReason: 'bill_total_mismatch', needsCheck: false }))
  expect(host.textContent).toContain('원래 청구액을 확인하고 있어요')
  expect(host.textContent).not.toContain('낼 금액이 없어요')
  expect(button('전부 냈어요')).toBeUndefined()
})


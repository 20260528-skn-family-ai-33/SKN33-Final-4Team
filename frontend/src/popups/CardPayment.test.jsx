// 거래로 확인하기 (카드값 납부 상세) — 합성 데이터로 분할 납부 여러 건 선택 · 분류 변경 확인 · 저장 실패 · 연결 취소
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import CardPaymentPopup from './CardPaymentPopup.jsx'
import { apiSend, useApi } from '../utils/api.js'

const { closePopup, openPopup, reload } = vi.hoisted(() => ({ closePopup: vi.fn(), openPopup: vi.fn(), reload: vi.fn() }))
vi.mock('../utils/api.js', () => ({ apiSend: vi.fn(), useApi: vi.fn(), newRequestKey: () => 'card-key' }))
vi.mock('./context.js', () => ({ usePopup: () => ({ closePopup, openPopup }) }))

const account = { accountId: 'acc', displayName: '합성은행 월급통장 · ****1001', status: 'connected' }
const hints = { issuerInDescription: true, otherIssuer: false, payingAccount: true, nearDueDate: true }
const tx = (id, amount, extra = {}) => ({ transactionId: id, date: '2026-11-05', amount, account, description: `합성카드 ${id}`,
  hints, reclassify: { fromType: 'transfer', fromCategory: null, needsConfirm: false }, ...extra })
const bill = (extra = {}) => ({ billId: 'bill-1', issuerCode: '0306', issuerName: '합성카드', dueDate: '2026-11-05',
  totalAmount: 500_000, paidAmount: 0, remainingAmount: 500_000, paymentState: 'needs_review', canLink: true, ...extra })
const body = (extra = {}) => ({ bill: bill(), linked: [], items: [tx('a', 300_000), tx('b', 200_000),
  tx('food', 50_000, { reclassify: { fromType: 'spending', fromCategory: 'FOOD', needsConfirm: true } })],
  searchFrom: '2026-10-01', searchTo: '2026-11-12', ...extra })

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
const onSaved = vi.fn()
beforeEach(() => {
  vi.clearAllMocks()
  useApi.mockReturnValue({ data: body(), reload })
  apiSend.mockResolvedValue({})
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(() => root.unmount())
  host.remove()
})

const button = (text) => [...host.querySelectorAll('button')].find((b) => b.textContent.includes(text))
const click = (el) => act(async () => el.click())
const render = (el = <CardPaymentPopup billId="bill-1" onSaved={onSaved} />) => act(() => root.render(el))

it('나눠 낸 출금 두 건을 골라 한 번에 연결하고, 연결하면 남는 금액을 보여 준다', async () => {
  await render()
  await click(button('합성카드 a'))
  await click(button('합성카드 b'))
  expect(host.textContent).toContain('고른 출금 2건500,000원')
  expect(host.textContent).toContain('연결하면 남은 납부액0원')
  await click(button('이 청구서에 연결'))
  expect(apiSend).toHaveBeenCalledWith('/api/finance/card-bills/bill-1/payments/', 'POST',
    { transactionIds: ['a', 'b'], confirmReclassify: false }, { requestKey: 'card-key' })
  expect(reload).toHaveBeenCalled()
  expect(onSaved).toHaveBeenCalled()
})

it('소비로 확정한 거래는 바뀌는 분류를 보여 주고 확인해야 연결한다', async () => {
  await render()
  await click(button('합성카드 food'))
  expect(host.textContent).toContain('‘소비 · 식비’ → ‘카드값’')
  expect(button('이 청구서에 연결').disabled).toBe(true)
  await click(host.querySelector('input[type=checkbox]'))
  await click(button('이 청구서에 연결'))
  expect(apiSend.mock.calls[0][2]).toEqual({ transactionIds: ['food'], confirmReclassify: true })
})

it('저장에 실패하면 오류를 보여 주고 고른 출금을 유지한다', async () => {
  apiSend.mockRejectedValueOnce(Object.assign(new Error('이미 다른 납부에 연결된 거래예요.'), { status: 409 }))
  await render()
  await click(button('합성카드 a'))
  await click(button('이 청구서에 연결'))
  expect(host.querySelector('[role=alert]').textContent).toBe('이미 다른 납부에 연결된 거래예요.')
  expect(button('합성카드 a').getAttribute('aria-pressed')).toBe('true')
  expect(onSaved).not.toHaveBeenCalled()
})

it('납부 완료 뒤에도 연결 내역과 연결 취소가 보인다', async () => {
  useApi.mockReturnValue({ data: body({ bill: bill({ paidAmount: 500_000, remainingAmount: 0, paymentState: 'paid' }),
    linked: [{ ...tx('a', 500_000), source: 'rule', matchedAmount: 500_000, cancelEffect: { kind: 'restore', toType: 'transfer', toCategory: null } }],
    items: [] }), reload })
  apiSend.mockResolvedValueOnce({ released: { classification: 'restored', transactionType: 'transfer', category: null } })
  await render()
  expect(host.textContent).toContain('납부 완료')
  expect(host.textContent).toContain('자동 연결')
  await click(button('연결 취소'))
  expect(apiSend).toHaveBeenCalledWith('/api/finance/card-bills/bill-1/payments/a/', 'DELETE')
  expect(host.textContent).toContain('납부 연결을 취소하고 분류를 ‘이체’로 되돌렸어요.')
})

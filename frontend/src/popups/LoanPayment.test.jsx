// 대출 납부 거래 확인 팝업 — 합성 데이터로 후보 선택 · 다 냄 · 일부 · 분류 변경 확인 · 연결 취소 · 저장 실패
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import LoanPaymentPopup from './LoanPaymentPopup.jsx'
import MustPayPopup from './MustPayPopup.jsx'
import { apiSend, useApi } from '../utils/api.js'

const { closePopup, reload } = vi.hoisted(() => ({ closePopup: vi.fn(), reload: vi.fn() }))
vi.mock('../utils/api.js', () => ({ apiSend: vi.fn(), useApi: vi.fn(), newRequestKey: () => 'pay-key' }))
vi.mock('./context.js', () => ({ usePopup: () => ({ closePopup }) }))

const account = { accountId: 'acc', displayName: '합성은행 월급통장 · ****1001', status: 'connected' }
const hints = { linkedAccount: true, nearDueDate: true, lenderInDescription: false, sameAsBefore: false }
const candidate = (id, extra = {}) => ({ transactionId: id, date: '2026-11-10', amount: 298_000, account, description: '자동이체 출금',
  amountDifference: -2_000, hints, reclassify: { fromType: 'transfer', fromCategory: null, needsConfirm: false }, settlement: null, source: null, ...extra })
const due = { dueId: 'due-1', loanId: 'loan-1', name: '합성은행 학자금', dueDate: '2026-11-10', expectedAmount: 300_000, paidAmount: 0, paymentState: 'needs_review', linkedAccount: account }
const body = (extra = {}) => ({ due, linked: [], items: [candidate('tx-1'), candidate('tx-food', {
  amount: 300_000, amountDifference: 0, description: '합성 식당', reclassify: { fromType: 'spending', fromCategory: 'FOOD', needsConfirm: true } })],
  searchFrom: '2026-11-03', searchTo: '2026-11-12', ...extra })

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
const onSaved = vi.fn()
beforeEach(async () => {
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
const render = () => act(() => root.render(<LoanPaymentPopup dueId="due-1" onSaved={onSaved} />))

it('금액이 다르고 적요에 기관 이름이 없어도 후보를 보여 주고, 고른 뒤 다 냈어요로 저장한다', async () => {
  await render()
  expect(host.textContent).toContain('자동이체 출금')
  expect(button('이번 납부를 전부 냈어요')).toBeUndefined()   // 고르기 전에는 확인 버튼 없음
  await click(button('자동이체 출금'))
  expect(host.textContent).toContain('예정액보다 2,000원 적어요')
  expect(host.textContent).toContain('분류가 ‘이체’에서 ‘대출 상환’으로 바뀌어요')
  await click(button('이번 납부를 전부 냈어요'))
  expect(apiSend).toHaveBeenCalledWith('/api/finance/loan-dues/due-1/payments/', 'POST',
    { transactionId: 'tx-1', settlement: 'full', confirmReclassify: false }, { requestKey: 'pay-key' })
  expect(reload).toHaveBeenCalled()
  expect(onSaved).toHaveBeenCalled()
  expect(host.textContent).toContain('이번 납부를 완료로 저장했어요.')
})

it('일부만 냈어요는 partial 로 보낸다', async () => {
  await render()
  await click(button('자동이체 출금'))
  await click(button('일부만 냈어요'))
  expect(apiSend.mock.calls[0][2].settlement).toBe('partial')
})

it('소비 분류를 바꾸는 거래는 변경 내용을 보여 주고 확인해야 저장할 수 있다', async () => {
  await render()
  await click(button('합성 식당'))
  expect(host.textContent).toContain('‘소비 · 식비’에서 ‘대출 상환’으로 바뀌어요')
  expect(button('이번 납부를 전부 냈어요').disabled).toBe(true)
  await click(host.querySelector('input[type=checkbox]'))
  await click(button('이번 납부를 전부 냈어요'))
  expect(apiSend.mock.calls[0][2]).toEqual({ transactionId: 'tx-food', settlement: 'full', confirmReclassify: true })
})

it('저장 실패하면 이유를 보여 주고 고른 거래를 유지한다', async () => {
  apiSend.mockRejectedValueOnce(Object.assign(new Error('이미 다른 납부에 연결된 거래예요.'), { status: 409, code: 'not_ready' }))
  await render()
  await click(button('자동이체 출금'))
  await click(button('이번 납부를 전부 냈어요'))
  expect(host.querySelector('[role=alert]').textContent).toBe('이미 다른 납부에 연결된 거래예요.')
  expect(button('이번 납부를 전부 냈어요')).toBeDefined()
  expect(onSaved).not.toHaveBeenCalled()
  apiSend.mockRejectedValueOnce(new TypeError('Failed to fetch'))
  await click(button('이번 납부를 전부 냈어요'))
  expect(host.querySelector('[role=alert]').textContent).toContain('서버에 연결하지 못했어요')
})

const linkedBody = (effect) => body({ due: { ...due, paidAmount: 298_000, paymentState: 'paid' },
  linked: [candidate('tx-1', { settlement: 'full', source: 'user', reclassify: null, cancelEffect: effect })], items: [] })

it('연결한 출금은 연결 취소 · 정정할 수 있다 (안내는 실제 복원 결과대로)', async () => {
  useApi.mockReturnValue({ reload, data: linkedBody({ kind: 'restore', toType: 'spending', toCategory: 'FOOD' }) })
  apiSend.mockResolvedValueOnce({ released: { transactionId: 'tx-1', classification: 'restored', transactionType: 'spending', category: 'FOOD' } })
  await render()
  expect(host.textContent).toContain('이번 납부 완료')
  expect(host.textContent).toContain('다 냈어요')
  expect(host.textContent).toContain('취소하면 분류가 ‘소비 · 식비’로 돌아가요.')
  await click(button('연결 취소'))
  expect(apiSend).toHaveBeenCalledWith('/api/finance/loan-dues/due-1/payments/tx-1/', 'DELETE', undefined)
  expect(host.textContent).toContain('납부 연결을 취소하고 분류를 ‘소비 · 식비’로 되돌렸어요.')
  await click(button('정정'))
  await click(button('일부만 냈어요'))
  expect(apiSend.mock.calls[1]).toEqual(['/api/finance/loan-dues/due-1/payments/', 'POST',
    { transactionId: 'tx-1', settlement: 'partial', confirmReclassify: false }, { requestKey: 'pay-key' }])
})

it('기간을 넓혀 다시 찾는다', async () => {
  await render()
  const [from] = host.querySelectorAll('input[type=date]')
  await act(async () => {
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
    set.call(from, '2026-10-01')
    from.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await click(button('이 기간으로 찾기'))
  expect(useApi).toHaveBeenLastCalledWith('/api/finance/loan-dues/due-1/payments/?from=2026-10-01&to=2026-11-12')
})

it('꼭 나갈 돈 대출 항목에서 납부 거래 확인을 연다', async () => {
  const onCheckPayment = vi.fn()
  const item = { type: 'loan', name: '합성은행 학자금', amount: 300_000, paymentState: 'needs_review', dueDate: '2026-11-10' }
  await act(() => root.render(<MustPayPopup item={item} date="11.10" badges={[]} onManage={vi.fn()} onCheckPayment={onCheckPayment} />))
  await click(button('납부 거래 확인'))
  expect(closePopup).toHaveBeenCalled()
  expect(onCheckPayment).toHaveBeenCalled()
})

it.each([
  [{ transactionId: 'tx-1', classification: 'kept', transactionType: 'spending', category: 'HOUSEHOLD' }, '납부 연결을 취소했어요. 분류는 지금 값 그대로예요.'],
  [null, '납부 연결을 취소했어요.'],
])('복원하지 않은 취소는 되돌렸다고 안내하지 않는다 %#', async (released, text) => {
  useApi.mockReturnValue({ reload, data: linkedBody({ kind: 'keep', toType: 'spending', toCategory: 'HOUSEHOLD' }) })
  apiSend.mockResolvedValueOnce({ released })
  await render()
  expect(host.textContent).toContain('취소해도 지금 분류 ‘소비 · 생활용품’는 그대로예요.')
  await click(button('연결 취소'))
  expect(host.querySelector('[role=status]').textContent).toBe(text)
  expect(host.textContent).not.toContain('되돌렸어요')
})

it('원래 분류를 모르는 예전 자동 연결은 분류를 고른 뒤 연결 해제와 함께 보낸다', async () => {
  useApi.mockReturnValue({ reload, data: linkedBody({ kind: 'choose', toType: null, toCategory: null }) })
  await render()
  expect(host.textContent).toContain('원래 분류 기록이 없어요')
  await click(button('연결 취소'))
  expect(apiSend).not.toHaveBeenCalled()             // 고르기 전에는 풀지 않는다
  await click(button('연결 취소하고 이 분류로 두기'))
  expect(apiSend).not.toHaveBeenCalled()
  expect(host.querySelector('[role=alert]').textContent).toBe('이 거래를 무엇으로 둘지 골라 주세요.')
  const [, spending] = host.querySelectorAll('input[type=radio]')
  await click(spending)
  const select = host.querySelector('select')
  await act(async () => { select.value = 'FOOD'; select.dispatchEvent(new Event('change', { bubbles: true })) })
  // 저장 실패하면 고른 분류를 유지하고 안내
  apiSend.mockRejectedValueOnce(Object.assign(new Error('입력값을 확인해 주세요.'), { status: 400, fields: { restoreType: ['원래 분류를 알 수 없어요.'] } }))
  await click(button('연결 취소하고 이 분류로 두기'))
  expect(host.querySelector('[role=alert]').textContent).toBe('원래 분류를 알 수 없어요.')
  expect(host.querySelector('select').value).toBe('FOOD')
  expect(onSaved).not.toHaveBeenCalled()
  apiSend.mockResolvedValueOnce({ released: { transactionId: 'tx-1', classification: 'chosen', transactionType: 'spending', category: 'FOOD' } })
  await click(button('연결 취소하고 이 분류로 두기'))
  expect(apiSend).toHaveBeenLastCalledWith('/api/finance/loan-dues/due-1/payments/tx-1/', 'DELETE', { restoreType: 'spending', restoreCategory: 'FOOD' })
  expect(host.textContent).toContain('납부 연결을 취소하고 분류를 ‘소비 · 식비’로 바꿨어요.')
  expect(onSaved).toHaveBeenCalled()
})

it('예전 자동 연결을 이체로 두고 취소한다', async () => {
  useApi.mockReturnValue({ reload, data: linkedBody({ kind: 'choose', toType: null, toCategory: null }) })
  apiSend.mockResolvedValueOnce({ released: { transactionId: 'tx-1', classification: 'chosen', transactionType: 'transfer', category: null } })
  await render()
  await click(button('연결 취소'))
  await click(host.querySelectorAll('input[type=radio]')[0])
  await click(button('연결 취소하고 이 분류로 두기'))
  expect(apiSend).toHaveBeenCalledWith('/api/finance/loan-dues/due-1/payments/tx-1/', 'DELETE', { restoreType: 'transfer' })
  expect(host.textContent).toContain('분류를 ‘이체’로 바꿨어요.')
})
